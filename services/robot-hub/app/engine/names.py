"""
Names — 机器人/数字人显示名称的集中读取点

存储：`system_config` 表的 `robot_display_name` / `digital_human_display_name`
后台修改：`PUT /system/config` 带对应 key 即可，零迁移。

为什么做缓存：
  - 这两个名字在 prompt 组装、每次 TTS welcome、每次 LLM 意图 system prompt
    等热路径被读到。DB 在同一进程下虽快，但每轮都打一次也没必要。
  - 20s TTL 足够让后台修改"秒级感知"，又避免同一轮对话里反复查 DB。

对外只暴露三个异步函数：
  get_robot_name() / get_digital_human_name() / get_names()

所有上层代码（retrievers、smart_engine welcome、intent prompt 等）都通过
这里拿名字，永远不要再把名字硬编码到字符串里。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("hub.engine.names")

DEFAULT_ROBOT_NAME = "小豹"
DEFAULT_DIGITAL_HUMAN_NAME = "艾琳"

_TTL_SECONDS = 20.0

_cache: dict = {"robot": DEFAULT_ROBOT_NAME, "dh": DEFAULT_DIGITAL_HUMAN_NAME, "at": 0.0}
_lock = asyncio.Lock()


async def _load_from_db() -> tuple[str, str]:
    from app.database import get_db
    robot_name = DEFAULT_ROBOT_NAME
    dh_name = DEFAULT_DIGITAL_HUMAN_NAME
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT key, value FROM system_config "
            "WHERE key IN ('robot_display_name','digital_human_display_name')"
        )
        kv = {r["key"]: (r["value"] or "").strip() for r in rows}
        if kv.get("robot_display_name"):
            robot_name = kv["robot_display_name"]
        if kv.get("digital_human_display_name"):
            dh_name = kv["digital_human_display_name"]
    finally:
        await db.close()
    return robot_name, dh_name


async def get_names(*, force_refresh: bool = False) -> tuple[str, str]:
    """返回 (robot_name, digital_human_name)，带 TTL 缓存"""
    now = time.monotonic()
    if not force_refresh and now - _cache["at"] < _TTL_SECONDS:
        return _cache["robot"], _cache["dh"]
    async with _lock:
        now = time.monotonic()
        if not force_refresh and now - _cache["at"] < _TTL_SECONDS:
            return _cache["robot"], _cache["dh"]
        try:
            robot, dh = await _load_from_db()
        except Exception as e:
            logger.warning("[names] 读取 system_config 失败 → 用默认值: %s", e)
            robot, dh = DEFAULT_ROBOT_NAME, DEFAULT_DIGITAL_HUMAN_NAME
        _cache["robot"] = robot
        _cache["dh"] = dh
        _cache["at"] = time.monotonic()
        return robot, dh


async def get_robot_name() -> str:
    r, _ = await get_names()
    return r


async def get_digital_human_name() -> str:
    _, d = await get_names()
    return d


def invalidate_cache():
    """写入 system_config 之后可以主动让缓存失效（非强制，20s 内也会自然刷新）"""
    _cache["at"] = 0.0
