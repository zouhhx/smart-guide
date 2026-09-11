"""
宇树机器人 webhook 事件总线

职责:
1. 接收 /api/unitree/event 分发过来的事件, 落日志。
2. 提供 wait_for_event(key) 给等待方 (流程执行器/语音环) 用。
3. 对 asr 事件直接派发给 UnitreeVoiceHandler 走语音控制/问答。

事件 key 约定:
  - tts:<trace_id>        ttsFinished
  - nav:<task_id>         navTaskFinished / navTaskPaused
  - motion:<motion_id>    motionPlaybackFinished
  - dance:<dance_name>    danceFinished
  - asr:wakeup            asrWakeup (最近一次, 不按 trace 分)
  - face:latest           faceRecognition (最近一次)

设计取舍:
- 等待方拿 asyncio.Future, 收到事件后 set_result; 超时由调用方处理.
- 不持久化事件 (已在 execution_logs 里有一份).
- 多个等待方同时等同一 key → 全部得到同一 payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger("hub.engine.unitree_events")

_waiters: dict[str, list[asyncio.Future]] = defaultdict(list)

# task_id → {map_id, point_id, at}; 仅非流程触发的导航 (手动面板 / 语音) 写入,
# 给 navTaskFinished 查绑定用; 流程步骤故意不写, 避免打断流程内已编排的 TTS.
import time as _time
_nav_targets: dict[str, dict] = {}
_NAV_TARGETS_TTL = 600.0  # 10 分钟内到达就有效


def register_nav_target(task_id: str, map_id: str, point_id: int) -> None:
    """下发导航时登记 (task_id, map_id, point_id), 供到达回调查点位绑定."""
    if not task_id or not map_id:
        return
    _nav_targets[str(task_id)] = {
        "map_id": str(map_id),
        "point_id": int(point_id),
        "at": _time.time(),
    }
    # 顺手清过期
    cutoff = _time.time() - _NAV_TARGETS_TTL
    stale = [k for k, v in _nav_targets.items() if v.get("at", 0) < cutoff]
    for k in stale:
        _nav_targets.pop(k, None)


def pop_nav_target(task_id: str) -> dict | None:
    """取出并移除对应 task_id 的目标 (一次性消费)."""
    if not task_id:
        return None
    v = _nav_targets.pop(str(task_id), None)
    if v and (_time.time() - v.get("at", 0)) > _NAV_TARGETS_TTL:
        return None
    return v


def wait_for_event(key: str, timeout: float = 60.0) -> asyncio.Future:
    """注册一个等待器, 返回 Future; 等待方可 await asyncio.wait_for(fut, timeout)."""
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _waiters[key].append(fut)
    logger.debug("[unitree-event] wait_for_event key=%s waiters=%d", key, len(_waiters[key]))
    return fut


def fire_event(key: str, payload: dict) -> int:
    """触发事件 → 唤醒所有注册的 Future; 返回命中的 waiter 数。"""
    waiters = _waiters.pop(key, [])
    hit = 0
    for fut in waiters:
        if not fut.done():
            fut.set_result(payload)
            hit += 1
    if hit:
        logger.info("[unitree-event] fire key=%s waiters_hit=%d", key, hit)
    return hit


def dispatch_event(action: str, params: dict) -> list[tuple[str, int]]:
    """把网关事件按 action 映射到具体 key, 触发等待方。返回 [(key, hit), ...]"""
    params = params or {}
    results: list[tuple[str, int]] = []

    def _fire(key: str):
        results.append((key, fire_event(key, params)))

    if action == "ttsFinished":
        tid = params.get("trace_id")
        if tid:
            _fire(f"tts:{tid}")
    elif action in ("navTaskFinished", "navTaskPaused"):
        task_id = params.get("task_id")
        if task_id:
            _fire(f"nav:{task_id}")
    elif action == "motionPlaybackFinished":
        mid = params.get("motions_id") or params.get("motion_id")
        if mid is not None:
            _fire(f"motion:{mid}")
    elif action == "danceFinished":
        dn = params.get("dance_name") or params.get("dance_id")
        if dn:
            _fire(f"dance:{dn}")
    elif action == "asrWakeup":
        _fire("asr:wakeup")
    elif action == "asr":
        # 新增: 供 asr_listen_once() 拿到首条文本; 正常 ASR 处理链(unitree_voice.handle_speech)
        # 由 /api/unitree/event 另外调用, 两条路互不打架.
        _fire("asr:next")
    elif action == "faceRecognition":
        _fire("face:latest")

    # asrError / motionPlaybackStart / danceStart 等广播型事件不走 wait_for_event
    return results


def debug_snapshot() -> dict:
    """管理面板可用; 返回当前等待器数量快照。"""
    return {k: len(v) for k, v in _waiters.items() if v}
