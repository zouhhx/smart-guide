"""
POI Retriever — 周边美食/景点/酒店等查询, 接高德 Web Service API。

入口: retrieve(text, slots, world_state, robot_id) → {"answer": str, "source": ..., "confidence": ...}

数据来源:
  1. system_config.amap_api_key   (高德 Key, 必填)
  2. system_config.hall_location  (lng,lat 字符串; 空时 fallback geocode)
  3. system_config.hall_address   (中文地址, 用于 geocode)
  4. system_config.poi_search_radius (默认 1500m)

查询流程:
  - 文本里抽分类关键词 (美食/餐厅/景点/酒店/便利店/地铁/公交) 决定 amap 类型
  - 没拿到 hall_location → 调 /v3/geocode/geo 把 hall_address → lng,lat 缓存回 system_config
  - /v3/place/around 拿前 5 个结果, 拼成 TTS 友好的口语化短文
  - 任一阶段失败返回降级文案, 不抛
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from app.database import get_db

logger = logging.getLogger("hub.engine.retrievers.poi")

_AMAP_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
_AMAP_AROUND_URL = "https://restapi.amap.com/v3/place/around"

# 用户问句关键词 → (TTS 朗读用的口语化类目名, 高德 POI types 编码)
# 编码表: https://lbs.amap.com/api/webservice/download
# 用类目编码而不是 keyword 命中更准, 不会被店名干扰; display 名单独配防止"5个饭"这种生硬表达
_CATEGORY_TYPES: dict[str, tuple[str, str]] = {
    "美食":   ("美食",   "050000"),
    "餐厅":   ("美食",   "050000"),
    "饭店":   ("美食",   "050000"),
    "饭":     ("美食",   "050000"),
    "餐":     ("美食",   "050000"),
    "吃饭":   ("美食",   "050000"),
    "吃的":   ("美食",   "050000"),
    "吃":     ("美食",   "050000"),
    "外卖":   ("美食",   "050000"),
    "好吃":   ("美食",   "050000"),
    "咖啡":   ("咖啡店", "050500"),
    "奶茶":   ("奶茶店", "050501"),
    "酒吧":   ("酒吧",   "050700"),
    "景点":   ("景点",   "110000"),
    "玩":     ("好玩的", "110000"),
    "好玩":   ("好玩的", "110000"),
    "公园":   ("公园",   "110101"),
    "博物馆": ("博物馆", "140100"),
    "酒店":   ("酒店",   "100000"),
    "宾馆":   ("酒店",   "100000"),
    "住宿":   ("酒店",   "100000"),
    "住的":   ("酒店",   "100000"),
    "住":     ("酒店",   "100000"),
    "便利店": ("便利店", "060200"),
    "超市":   ("超市",   "060100"),
    "购物":   ("购物",   "060000"),
    "商场":   ("商场",   "060101"),
    "商店":   ("商店",   "060000"),
    "地铁":   ("地铁站", "150500"),
    "公交":   ("公交站", "150700"),
    "车站":   ("车站",   "150700"),
    "停车":   ("停车场", "150900"),
    "银行":   ("银行",   "160100"),
    "atm":    ("ATM",    "160300"),
    "医院":   ("医院",   "090100"),
    "药店":   ("药店",   "090601"),
    "加油站": ("加油站", "010100"),
    "充电桩": ("充电桩", "011100"),
    "喝的":   ("饮品店", "050500"),
}

_TIMEOUT = aiohttp.ClientTimeout(total=4.0)  # 8s→4s, 高德响应通常 <1s, 4s 足够

# 内存缓存 — 同 (location, types, radius) 30min 内复用 POI 结果. 商圈周边变化慢,
# 高频问"附近美食/酒店"可以瞬间命中, 不再走 amap API。
import time as _time_module
_POI_CACHE: dict[tuple, tuple[float, list]] = {}
_POI_TTL_SEC = 1800.0  # 30 min

# 全局 aiohttp session — amap 域名 TLS 复用, 省每次握手 200-500ms。
import asyncio as _asyncio
_amap_session: aiohttp.ClientSession | None = None
_amap_session_lock = _asyncio.Lock()


async def _get_amap_session() -> aiohttp.ClientSession:
    global _amap_session
    if _amap_session is None or _amap_session.closed:
        async with _amap_session_lock:
            if _amap_session is None or _amap_session.closed:
                _amap_session = aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(
                        limit=10, keepalive_timeout=120.0, enable_cleanup_closed=True,
                    ),
                )
    return _amap_session


# ============================================================
# 配置读取
# ============================================================
async def _read_cfg() -> dict:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT key, value FROM system_config WHERE key IN ("
            "'amap_api_key','hall_address','hall_location','poi_search_radius')"
        )
        return {r["key"]: (r["value"] or "").strip() for r in rows}
    finally:
        await db.close()


async def _save_hall_location(lng_lat: str) -> None:
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO system_config(key,value) VALUES('hall_location', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now','localtime')",
            (lng_lat, lng_lat),
        )
        await db.commit()
    finally:
        await db.close()


# ============================================================
# 高德调用
# ============================================================
async def _geocode(address: str, key: str) -> str:
    """中文地址 → lng,lat。失败返回空串。"""
    if not address:
        return ""
    try:
        session = await _get_amap_session()
        params = {"key": key, "address": address, "output": "JSON"}
        async with session.get(_AMAP_GEOCODE_URL, params=params, timeout=_TIMEOUT) as resp:
            data = await resp.json(content_type=None)
        if str(data.get("status")) != "1":
            logger.warning("[POI] geocode 失败 status=%s info=%s addr=%r",
                           data.get("status"), data.get("info"), address)
            return ""
        geos = data.get("geocodes") or []
        if not geos:
            return ""
        loc = geos[0].get("location") or ""
        logger.info("[POI] geocode: %r → %s", address, loc)
        return loc
    except Exception as e:
        logger.warning("[POI] geocode 异常: %s", e)
        return ""


async def _place_around(location: str, types: str, key: str, radius: int = 1500) -> list[dict]:
    """周边搜索, 返回 list[{name,address,distance,type,tel}]。

    30min 内同 (location, types, radius) 命中内存缓存直接返回, 跳过 HTTP。
    """
    if not location:
        return []
    cache_key = (location, types, int(radius))
    cached = _POI_CACHE.get(cache_key)
    if cached:
        ts, data = cached
        if _time_module.time() - ts < _POI_TTL_SEC:
            logger.info("[POI] cache HIT key=%s age=%.0fs n=%d",
                        cache_key, _time_module.time() - ts, len(data))
            return data
        else:
            _POI_CACHE.pop(cache_key, None)

    try:
        session = await _get_amap_session()
        params = {
            "key": key,
            "location": location,
            "types": types,
            "radius": str(int(radius)),
            "sortrule": "distance",
            "offset": "10",
            "page": "1",
            "extensions": "base",
            "output": "JSON",
        }
        async with session.get(_AMAP_AROUND_URL, params=params, timeout=_TIMEOUT) as resp:
            data = await resp.json(content_type=None)
        if str(data.get("status")) != "1":
            logger.warning("[POI] place/around 失败 status=%s info=%s",
                           data.get("status"), data.get("info"))
            return []
        pois = data.get("pois") or []
        out = []
        for p in pois[:10]:
            out.append({
                "name": p.get("name") or "",
                "address": p.get("address") or "",
                "distance": int(p.get("distance") or 0),
                "type": (p.get("type") or "").split(";")[0],
                "tel": p.get("tel") or "",
            })
        _POI_CACHE[cache_key] = (_time_module.time(), out)
        return out
    except Exception as e:
        logger.warning("[POI] place/around 异常: %s", e)
        return []


# ============================================================
# 文本理解
# ============================================================
def _detect_category(text: str) -> tuple[str, str]:
    """从用户文本检测类目, 返回 (display_name, amap_types_code)。

    没命中任何关键词时返回 ('', '') — 由调用方决定要不要降级到通用搜索。
    """
    t = (text or "").lower()
    # 长 keyword 优先: "便利店" 排在 "店"/"利" 之前, "好吃" 排在 "吃" 之前
    keys = sorted(_CATEGORY_TYPES.keys(), key=lambda k: -len(k))
    for k in keys:
        if k in t:
            return _CATEGORY_TYPES[k]  # (display, code)
    return "", ""


def _format_pois(pois: list[dict], category: str) -> str:
    """把 POI list 拼成 TTS 友好的中文短句。"""
    if not pois:
        return ""
    # 取前 3-5 条
    n = min(5, len(pois))
    items = []
    for p in pois[:n]:
        d = p["distance"]
        if d < 1000:
            d_text = f"{d}米"
        else:
            d_text = f"{d/1000:.1f}公里"
        items.append(f"{p['name']}({d_text})")
    label = category or "地点"
    return f"附近{n}个{label}: " + "、".join(items) + "。需要更多信息您可以再问我。"


# ============================================================
# 对外入口
# ============================================================
async def retrieve(text: str, slots: dict, world_state, robot_id: str) -> dict:
    """answer_engine 调用入口, 跟其它 retriever 同款返回结构。"""
    cfg = await _read_cfg()
    key = cfg.get("amap_api_key") or ""
    if not key:
        return {
            "answer": "我这边还没配高德 API Key，暂时查不到周边信息。",
            "source": "poi:no_key",
            "confidence": 0.5,
        }

    location = cfg.get("hall_location") or ""
    if not location:
        addr = cfg.get("hall_address") or ""
        if not addr:
            return {
                "answer": "展厅地址还没配, 我没法确定附近的范围。请管理员在系统配置里填一下展厅地址。",
                "source": "poi:no_address",
                "confidence": 0.4,
            }
        location = await _geocode(addr, key)
        if not location:
            return {
                "answer": "展厅地址解析失败了, 暂时查不到附近信息。",
                "source": "poi:geocode_fail",
                "confidence": 0.4,
            }
        await _save_hall_location(location)

    radius = 1500
    try:
        radius = int(cfg.get("poi_search_radius") or 1500)
    except ValueError:
        pass

    cat_name, cat_code = _detect_category(text)
    if not cat_code:
        # 没命中具体类目, 默认走"美食"
        cat_name, cat_code = _CATEGORY_TYPES["美食"]

    pois = await _place_around(location, cat_code, key, radius=radius)
    if not pois:
        return {
            "answer": f"附近{radius}米内暂时没找到{cat_name}，要不我换个范围再帮您查查？",
            "source": "poi:empty",
            "confidence": 0.5,
        }
    answer = _format_pois(pois, cat_name)
    return {
        "answer": answer,
        "source": "poi:amap",
        "confidence": 0.9,
        "category": cat_name,
        "count": len(pois),
    }
