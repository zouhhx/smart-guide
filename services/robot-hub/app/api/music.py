"""
音乐曲库 API — 后台维护一个上传/列表/启停/删除的简单 CRUD,
意图 play_music 命中时从启用列表里随机挑一首推给 APK。

存储:
  /opt/exhibition-hub/data/music/<uuid>_<safe-original-name>.<ext>
  数据库存 filename, 不存全路径; URL 在下发时由 music_url_base 配置拼接。
"""

import logging
import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.database import get_db

logger = logging.getLogger("hub.api.music")

router = APIRouter()

# 文件存放目录 — 跟 SQLite DB 同 data/ 父级, 容易随服务一起备份
# 真实路径在 deploy 时 mkdir -p 创建
MUSIC_DIR = os.path.join("data", "music")

# 允许的扩展名 — 仅常见音频, 防止把 .exe / .sh 之类传上来
_ALLOWED_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

# 单文件大小硬上限 ~50MB (避免管理后台被刷)
_MAX_BYTES = 50 * 1024 * 1024


def _safe_basename(name: str) -> str:
    """规范化文件名 — 去掉路径分隔符 / 控制字符, 保留中英文/数字/常见标点。"""
    base = os.path.basename(name or "").strip()
    base = re.sub(r"[\\/\x00-\x1f]", "_", base)
    return base[:120] or "track"


async def _ensure_music_dir():
    os.makedirs(MUSIC_DIR, exist_ok=True)


def _build_url(filename: str, music_url_base: str, request: Optional[Request]) -> str:
    """拼出 APK 能直拉的 URL.
    优先级: music_url_base 配置 > 本机 LAN IP 自动探测 > 请求 Host 兜底。
    跟 smart_engine 下发 evt_music_request 用同一份 host 工具, 保证后台试听和
    APK 实际拿到的 URL 一致。"""
    base = (music_url_base or "").rstrip("/")
    if not base:
        try:
            from app.engine.host import get_music_url_base
            base = get_music_url_base()
        except Exception:
            pass
    if not base and request is not None:
        base = f"{request.url.scheme}://{request.url.netloc}"
    return f"{base}/media/music/{filename}"


async def _get_music_url_base() -> str:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT value FROM system_config WHERE key='music_url_base'"
        )
        return (rows[0]["value"] if rows else "") or ""
    finally:
        await db.close()


@router.get("/music")
async def list_music(request: Request):
    """列出所有音乐 (含启停/大小/上传时间, 含拼好的 url 供管理后台试听)。
    顺带返回当前生效的 url_base + 自动探测的默认 base, 给管理后台展示用。"""
    configured = await _get_music_url_base()
    auto_base = ""
    try:
        from app.engine.host import get_music_url_base
        auto_base = get_music_url_base()
    except Exception:
        pass
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name, filename, size_bytes, enabled, uploaded_at "
            "FROM music_tracks ORDER BY uploaded_at DESC, id DESC"
        )
        data = []
        for r in rows:
            d = dict(r)
            d["url"] = _build_url(d["filename"], configured, request)
            data.append(d)
        return {
            "data": data,
            "configured_url_base": configured,
            "auto_url_base": auto_base,
            "effective_url_base": (configured or auto_base).rstrip("/"),
        }
    finally:
        await db.close()


@router.post("/music/upload")
async def upload_music(file: UploadFile = File(...), name: str = Form("")):
    """上传一首歌. name 缺省 = 文件名去扩展名."""
    await _ensure_music_dir()

    original = _safe_basename(file.filename or "")
    if "." not in original:
        raise HTTPException(400, "文件缺少扩展名")
    ext = ("." + original.rsplit(".", 1)[1]).lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(400, f"不支持的格式 {ext}, 允许: {sorted(_ALLOWED_EXT)}")

    # 落盘文件名: <uuid>_<safe-original> — uuid 防重名, 保留原名便于管理后台辨认
    storage_name = f"{uuid.uuid4().hex[:12]}_{original}"
    dst_path = os.path.join(MUSIC_DIR, storage_name)

    size = 0
    try:
        with open(dst_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 256)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_BYTES:
                    f.close()
                    try: os.remove(dst_path)
                    except OSError: pass
                    raise HTTPException(413, f"文件超出上限 {_MAX_BYTES // (1024*1024)}MB")
                f.write(chunk)
    finally:
        await file.close()

    display_name = (name.strip() or original.rsplit(".", 1)[0])[:120]

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO music_tracks (name, filename, size_bytes, enabled) VALUES (?, ?, ?, 1)",
            (display_name, storage_name, size),
        )
        await db.commit()
        rows = await db.execute_fetchall(
            "SELECT id, name, filename, size_bytes, enabled, uploaded_at "
            "FROM music_tracks WHERE filename=?", (storage_name,)
        )
    finally:
        await db.close()

    logger.info("[Music] 上传成功: name=%s file=%s size=%d", display_name, storage_name, size)
    return {"data": dict(rows[0]) if rows else None}


@router.patch("/music/{track_id}")
async def update_music(track_id: int, payload: dict):
    """改名 / 启停. payload: {name?, enabled?}"""
    fields, args = [], []
    if "name" in payload:
        fields.append("name=?"); args.append(str(payload["name"]).strip()[:120])
    if "enabled" in payload:
        fields.append("enabled=?"); args.append(1 if payload["enabled"] else 0)
    if not fields:
        raise HTTPException(400, "无可更新字段")
    args.append(track_id)
    db = await get_db()
    try:
        await db.execute(f"UPDATE music_tracks SET {', '.join(fields)} WHERE id=?", tuple(args))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}


@router.delete("/music/{track_id}")
async def delete_music(track_id: int):
    """删除一首歌 — 物理删文件 + 删行."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT filename FROM music_tracks WHERE id=?", (track_id,)
        )
        if not rows:
            raise HTTPException(404, "曲目不存在")
        fname = rows[0]["filename"]
        await db.execute("DELETE FROM music_tracks WHERE id=?", (track_id,))
        await db.commit()
    finally:
        await db.close()
    fp = os.path.join(MUSIC_DIR, fname)
    try:
        if os.path.exists(fp):
            os.remove(fp)
    except OSError as e:
        logger.warning("[Music] 删文件失败 (db 已清理): %s %s", fp, e)
    return {"ok": True}
