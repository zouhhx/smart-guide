"""
Photo API — 拍照上传/下载/查看

提供三个端点:
  POST   /api/photo/upload/{photo_id}   — Android 拍照后上传原图
  GET    /api/photo/download/{photo_id}  — 二维码扫码下载原图 (返回 .jpg)
  GET    /api/photo/view/{photo_id}      — 查看照片 (同 download, 浏览器友好)

文件存储: data/photos/<photo_id>.jpg
上传时自动创建目录。
"""

import os
import re
import time
import asyncio
import logging
import hashlib

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from starlette.status import HTTP_404_NOT_FOUND

logger = logging.getLogger("hub.api.photo")
router = APIRouter()

# 照片存储根目录 (相对于项目根)
PHOTO_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "photos"
))


def _normalize_photo_id(photo_id: str) -> str:
    """规范化 photo_id，兼容扫码 URL 携带 .jpg、查询参数或异常斜杠。"""
    raw = (photo_id or "").strip()
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    raw = raw.replace("\\", "/").strip("/")
    # path 参数理论上只会进来最后一段；这里防御性取最后一段，避免目录穿越。
    if "/" in raw:
        raw = raw.rsplit("/", 1)[-1]
    raw = re.sub(r"\.(jpg|jpeg)$", "", raw, flags=re.IGNORECASE)
    # photo_id 当前由 uuid4.hex 生成；如后台配置异常，至少限制为安全字符。
    safe = re.sub(r"[^0-9A-Za-z_-]", "", raw)
    return safe


def _photo_path(photo_id: str) -> str:
    """返回 photo_id 对应的磁盘路径, 始终为 .jpg 扩展名。"""
    safe = _normalize_photo_id(photo_id)
    return os.path.join(PHOTO_DIR, f"{safe}.jpg")


@router.post("/photo/upload/{photo_id}")
async def upload_photo(photo_id: str, file: UploadFile = File(...)):
    """Android 拍照后通过此接口上传原图; 后续扫码下载直接用 photo_id 取。

    ---
    流程:
      1. Android 拍照 → 二维码合成 → 照片保存到本地
      2. Android uploadPhotoToHub() → HTTP POST /api/photo/upload/{photo_id}
      3. 后端保存到 data/photos/<photo_id>.jpg
      4. 用户扫描二维码 → GET /api/photo/download/{photo_id}.jpg → 下载原图

    photo_id 由中枢侧生成 (uuid4.hex), 通过 `qrContent` 字段下发给 Android。
    Android 解析 URL path 中的 photo_id 作为上传目标。
    """
    os.makedirs(PHOTO_DIR, exist_ok=True)
    safe_photo_id = _normalize_photo_id(photo_id)
    if not safe_photo_id:
        raise HTTPException(status_code=400, detail="photo_id 无效")
    dest = _photo_path(safe_photo_id)

    try:
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="上传内容为空")

        file_size = len(contents)
        md5 = hashlib.md5(contents).hexdigest()

        with open(dest, "wb") as f:
            f.write(contents)

        logger.info("[Photo] 上传成功: photo_id=%s safe=%s size=%dBytes md5=%s dest=%s",
                    photo_id, safe_photo_id, file_size, md5, dest)
        return {
            "success": True,
            "photo_id": safe_photo_id,
            "size": file_size,
            "md5": md5,
            "download_url": f"/api/photo/download/{safe_photo_id}.jpg",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Photo] 上传失败: photo_id=%s safe=%s err=%s", photo_id, safe_photo_id, e)
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")


@router.get("/photo/download/{photo_id:path}")
@router.get("/photo/view/{photo_id:path}")
async def download_photo(photo_id: str):
    """扫码下载 / 浏览器查看照片。

    photo_id 接受带或不带 .jpg 后缀:
      GET /api/photo/download/abc123.jpg
      GET /api/photo/download/abc123
      都指向 data/photos/abc123.jpg

    机器人拍照后会先保存本地、再 HTTP 上传到中枢。极端情况下用户可能刚拍完
    就扫码，下载请求会早于上传完成；这里短暂等待，避免直接看到 Photo not found。
    """
    safe_photo_id = _normalize_photo_id(photo_id)
    if not safe_photo_id:
        raise HTTPException(status_code=400, detail="photo_id 无效")

    dest = _photo_path(safe_photo_id)
    for _ in range(20):  # 最多等待约 10 秒，让 Android 上传完成
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            break
        await asyncio.sleep(0.5)

    if not os.path.isfile(dest):
        logger.warning("[Photo] 下载失败: 文件不存在 photo_id=%s safe=%s path=%s dir_exists=%s",
                       photo_id, safe_photo_id, dest, os.path.isdir(PHOTO_DIR))
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="照片还未上传完成或上传失败，请稍后重试；如果仍失败，请查看机器人端照片上传日志。",
        )

    filename = f"{safe_photo_id}.jpg"
    return FileResponse(
        path=dest,
        media_type="image/jpeg",
        filename=filename,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def get_photo_download_url(photo_id: str) -> str:
    """供其它模块生成二维码扫码下载链接。

    返回绝对 URL 后半段 (path only), 前端拼接 host:
      /api/photo/download/{photo_id}.jpg

    注: 实际部署时如果 Nginx/Caddy 反代, 此路径自动生效。
    """
    return f"/api/photo/download/{photo_id}.jpg"
