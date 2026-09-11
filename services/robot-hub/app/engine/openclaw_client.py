"""
OpenClawClient — 与 OpenClaw 网关的 WebSocket 直连封装 (持久连接版)

只做一件事: 把 system+user 消息送到 OpenClaw WS 网关上配置好的专属 agent
(默认 model: "exhibition-baoxiaomi", 显示名 "展厅豹小秘"), 拿回 assistant
最终文本。不做业务解释、不做降级 — 业务层 (decision_bridge) 负责 prompt
组装、JSON 解析、降级到旧 LLM/规则路径。

配置 (config.yaml → openclaw:):
  enabled: true|false
  ws_url: "ws://192.168.20.99:18789"
  token: "..."
  model: "exhibition-baoxiaomi"     # OpenClaw 端的 agent ID slug
  timeout_seconds: 18

设计要点:
  · 持久 WS: 模块级单例 ws + reader 协程, 所有 chat/reset/compact/patch
    复用同一连接, 省每次握手 + connect.challenge 的 ~200-300ms。
  · 串行: _chat_lock 保证一次只有一个 chat round 在飞 (避免共享 session
    上 run 之间互相覆盖)。
  · 单 session "v5-main" + 60s 闲置自动 reset + 失败强制 reset, 在"近期
    访客记忆"和"避免上下文累积拖慢"之间取折中。
  · 后台 compact 任务: 闲置 ≥30s 且累计 ≥4 turns 时, 趁机调用
    sessions.compact 把历史压成摘要, 下次访客来时延迟稳定。
  · 异常时关闭 WS, 下次调用自动重连; 不缓存死连接。

is_enabled() 综合 yaml 配置 + system_config DB 开关, 让运维可以一键回落
原有 LLM/规则路径而不需要重新部署。
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform as _platform
import time
from typing import Any, Optional
from urllib.parse import urlencode, urlparse, urlunparse
from uuid import uuid4

import aiohttp

from app.config import get_config
from app.database import get_db

logger = logging.getLogger("hub.engine.openclaw_client")


# ---------- session memory window state ----------
IDLE_RESET_SEC = 60.0           # 空闲超过此值视为新访客, 调用前 sessions.reset
COMPACT_IDLE_SEC = 30.0         # 空闲超过此值且累计够 turns, 后台 compact
COMPACT_MIN_TURNS = 4           # 累计够这么多次决策才值得 compact

# 后台守护重连参数
RECONNECT_CHECK_INTERVAL = 10.0  # 多久检查一次连接健康
RECONNECT_MIN_BACKOFF = 5.0      # 重连失败的最小退避
RECONNECT_MAX_BACKOFF = 60.0     # 退避上限 (服务长时间不可达时不再激烈轰)

_last_send_at: float = 0.0
_force_reset: bool = False
_history_count: int = 0         # 自上次 reset/compact 以来的 chat 次数


# ---------- persistent WS state ----------
_ws_session: Optional[aiohttp.ClientSession] = None
_ws: Optional[aiohttp.ClientWebSocketResponse] = None
_chat_lock: Optional[asyncio.Lock] = None
_pending_res: dict[str, asyncio.Future] = {}   # rid → future for res
_pending_finals: dict[str, asyncio.Future] = {}  # runId → future for state=final
_reader_task: Optional[asyncio.Task] = None
_compact_task: Optional[asyncio.Task] = None
_reconnect_task: Optional[asyncio.Task] = None

# 每次新建立的 WS 都给一个 generation 编号, 进 session_key 后缀 — 避免
# 重连后跟服务端可能残留的旧 session 状态同名冲突 ("session locked" 之类)。
_connect_generation: int = 0

# 已经 sessions.patch label 过的 key 集合 — 每个 key 只打一次, 防止
# 重复调 sessions.patch 触发网关 "label already in use" 报错
_labeled_sessions: set[str] = set()


class OpenClawDisabled(RuntimeError):
    pass


class OpenClawError(RuntimeError):
    pass


# ---------- enable gate ----------

def _yaml_enabled() -> bool:
    cfg = get_config().get("openclaw") or {}
    return bool(cfg.get("enabled")) and bool(cfg.get("ws_url")) and bool(cfg.get("token"))


async def _db_flag_enabled() -> bool:
    """读取 system_config.openclaw_enabled. 未配置或读失败时视为开启 (兼容旧库)."""
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='openclaw_enabled'"
            )
            if rows:
                return (rows[0]["value"] or "").strip() == "1"
            return True
        finally:
            await db.close()
    except Exception as e:
        logger.debug("[OpenClaw] 读取 DB 开关失败, 默认视为开启: %s", e)
        return True


async def is_enabled() -> bool:
    """yaml 配置 + DB 运行时开关. 任一关闭即视为禁用 → 调用方回落到 V5 旧路径."""
    if not _yaml_enabled():
        return False
    return await _db_flag_enabled()


def get_settings() -> dict:
    cfg = get_config().get("openclaw") or {}
    return {
        "ws_url": (cfg.get("ws_url") or "").rstrip("/"),
        "token": cfg.get("token", ""),
        "model": cfg.get("model", "exhibition-baoxiaomi"),
        "timeout_seconds": int(cfg.get("timeout_seconds", 18)),
    }


def _session_key() -> str:
    # generation 后缀确保每次新连接用新 session 名, 不跟服务端可能残留的旧 session 撞名.
    # 初始/未连接时 gen=0 也是合法 key, 不影响首次调用.
    return f"agent:{get_settings()['model']}:v5-main-{_connect_generation}"


# ---------- WS protocol helpers ----------

def _build_ws_url(ws_url: str, token: str) -> str:
    parsed = urlparse(ws_url)
    return urlunparse(parsed._replace(query=urlencode({"token": token})))


def _build_origin(ws_url: str) -> str:
    parsed = urlparse(ws_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{scheme}://{host}"


def _connect_params(token: str) -> dict[str, Any]:
    return {
        "minProtocol": 3,
        "maxProtocol": 3,
        "role": "operator",
        "scopes": [
            "operator.read",
            "operator.admin",
            "operator.approvals",
            "operator.pairing",
        ],
        "client": {
            "id": "openclaw-control-ui",
            "version": "1.0.0",
            "platform": _platform.system().lower(),
            "mode": "ui",
        },
        "auth": {"token": token},
    }


def _extract_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    for blk in (message or {}).get("content") or []:
        if isinstance(blk, dict) and blk.get("type") == "text":
            parts.append(blk.get("text") or "")
    return "".join(parts).strip()


# ---------- persistent connection management ----------

def _get_lock() -> asyncio.Lock:
    """惰性创建 lock (要在 event loop 启动后才能创建)。"""
    global _chat_lock
    if _chat_lock is None:
        _chat_lock = asyncio.Lock()
    return _chat_lock


async def _reader_loop(ws: aiohttp.ClientWebSocketResponse) -> None:
    """持续读取 WS 消息, 按 id/runId 派发到等待的 Future。
    其它事件 (tick/health/agent lifecycle/chat delta) 直接丢弃。"""
    try:
        while True:
            try:
                m = await ws.receive()
            except Exception as e:
                logger.warning("[OpenClaw] reader recv error: %s", e)
                return
            if m.type == aiohttp.WSMsgType.TEXT:
                try:
                    d = json.loads(m.data)
                except Exception:
                    continue
                t = d.get("type")
                if t == "res":
                    fut = _pending_res.pop(d.get("id"), None)
                    if fut and not fut.done():
                        fut.set_result(d)
                elif t == "event" and d.get("event") == "chat":
                    p = d.get("payload") or {}
                    if p.get("state") == "final":
                        fut = _pending_finals.pop(p.get("runId"), None)
                        if fut and not fut.done():
                            fut.set_result(p.get("message") or {})
                # 其它 event 类型 (tick, health, agent, chat delta) 丢弃
            elif m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                logger.warning("[OpenClaw] reader: WS closed type=%s", m.type)
                return
    finally:
        # 唤醒所有挂着的等待者, 避免它们一直挂死
        for fut in list(_pending_res.values()) + list(_pending_finals.values()):
            if not fut.done():
                fut.set_exception(OpenClawError("ws connection closed"))
        _pending_res.clear()
        _pending_finals.clear()


async def _ensure_connected() -> None:
    """惰性建立 WS, 已连则直接返回; 调用方需持有 _chat_lock。"""
    global _ws_session, _ws, _reader_task
    if _ws is not None and not _ws.closed:
        return

    # 清理脏状态
    if _reader_task is not None and not _reader_task.done():
        _reader_task.cancel()
        try:
            await _reader_task
        except (asyncio.CancelledError, Exception):
            pass
    _reader_task = None
    if _ws is not None:
        try:
            await _ws.close()
        except Exception:
            pass
    _ws = None
    if _ws_session is not None:
        try:
            await _ws_session.close()
        except Exception:
            pass
    _ws_session = None

    s = get_settings()
    url = _build_ws_url(s["ws_url"], s["token"])
    origin = _build_origin(s["ws_url"])
    _ws_session = aiohttp.ClientSession()
    try:
        _ws = await _ws_session.ws_connect(
            url,
            origin=origin,
            heartbeat=20,        # aiohttp 自动每 20s 发 ping, 避免被网关 idle 关闭
            max_msg_size=0,
            timeout=10,
        )
    except Exception as e:
        await _ws_session.close()
        _ws_session = None
        raise OpenClawError(f"ws_connect failed: {type(e).__name__}: {e}") from e

    # 1) 先把 connect.challenge 吃掉 (服务端开局必发, 几十 ms 到)
    try:
        first = await asyncio.wait_for(_ws.receive(), timeout=2.0)
        if first.type != aiohttp.WSMsgType.TEXT:
            raise OpenClawError(f"unexpected first frame: {first.type}")
    except asyncio.TimeoutError:
        pass

    # 2) 同步发 connect req, 等 res 回来才能继续 (此时 reader 还没起)
    rid = str(uuid4())
    await _ws.send_str(json.dumps({
        "type": "req", "id": rid, "method": "connect", "params": _connect_params(s["token"]),
    }))
    deadline = time.monotonic() + 10.0
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0:
            raise OpenClawError("connect handshake timeout")
        m = await asyncio.wait_for(_ws.receive(), timeout=rem)
        if m.type != aiohttp.WSMsgType.TEXT:
            raise OpenClawError(f"socket closed during connect: {m.type}")
        d = json.loads(m.data)
        if d.get("type") == "event":
            continue
        if d.get("type") == "res" and d.get("id") == rid:
            if d.get("ok") is False:
                err = d.get("error") or {}
                raise OpenClawError(f"connect: {err.get('message') or err}")
            break

    # 3) 起 reader
    _reader_task = asyncio.create_task(_reader_loop(_ws))

    # 新连接 = 新 generation, 默认 session key 跟着变, 不和旧残留状态撞名;
    # 历史计数也清零, 跟服务端新 session 同步。
    global _connect_generation
    _connect_generation += 1
    globals()["_history_count"] = 0
    logger.info(
        "[OpenClaw] persistent WS connected url=%s gen=%d session=%s",
        _build_origin(s["ws_url"]), _connect_generation, _session_key(),
    )


async def _close_ws() -> None:
    """主动关闭 WS + reader, 下次调用会重连。出错路径用。"""
    global _ws_session, _ws, _reader_task
    if _reader_task is not None and not _reader_task.done():
        _reader_task.cancel()
        try:
            await _reader_task
        except (asyncio.CancelledError, Exception):
            pass
    _reader_task = None
    if _ws is not None:
        try:
            await _ws.close()
        except Exception:
            pass
    _ws = None
    if _ws_session is not None:
        try:
            await _ws_session.close()
        except Exception:
            pass
    _ws_session = None
    # 关连接 = 服务端 session 视为脏, 下次连上要重新 label; 清掉本地记忆
    _labeled_sessions.clear()


async def _request(method: str, params: dict[str, Any] | None, *, timeout: float) -> dict[str, Any]:
    """发一个 req, 等 res 返回 payload; 调用方需先持有 _chat_lock 且已 _ensure_connected。"""
    if _ws is None or _ws.closed:
        raise OpenClawError("ws not connected")
    rid = str(uuid4())
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_res[rid] = fut
    try:
        await _ws.send_str(json.dumps({"type": "req", "id": rid, "method": method, "params": params or {}}))
    except Exception as e:
        _pending_res.pop(rid, None)
        raise OpenClawError(f"send {method} failed: {e}") from e
    try:
        d = await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        _pending_res.pop(rid, None)
        raise OpenClawError(f"{method} timed out")
    if d.get("ok") is False:
        err = d.get("error") or {}
        raise OpenClawError(f"{method}: {err.get('message') or err}")
    return d.get("payload") or {}


async def _wait_chat_final(run_id: str, *, timeout: float) -> dict[str, Any]:
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_finals[run_id] = fut
    try:
        msg = await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        _pending_finals.pop(run_id, None)
        raise OpenClawError(f"chat final timeout runId={run_id}")
    return msg


# ---------- public API (signature kept stable for decision_bridge) ----------

async def chat(
    system_prompt: str,
    user_payload: str,
    *,
    session_key: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> str:
    """发一条决策请求, 返回 assistant 文本。失败一律抛 OpenClawError。"""
    if not await is_enabled():
        raise OpenClawDisabled("openclaw config missing or disabled")

    s = get_settings()
    timeout = float(timeout_seconds or s["timeout_seconds"])
    agent_id = s["model"]

    if session_key and session_key.startswith(f"agent:{agent_id}:"):
        sk = session_key
    elif session_key:
        sk = f"agent:{agent_id}:{session_key}"
    else:
        # 默认 session 跟连接 generation 绑定, 重连后用新名字, 不撞旧的残留状态
        sk = _session_key()

    if system_prompt and system_prompt.strip():
        message = f"[INPUT]\n{user_payload}\n\n[SYSTEM_REMINDER]\n{system_prompt.strip()}"
    else:
        message = user_payload

    t0 = time.monotonic()
    deadline = t0 + timeout
    global _last_send_at, _force_reset, _history_count

    async with _get_lock():
        try:
            await _ensure_connected()

            # 视情况清历史: 距上次调用 >IDLE_RESET_SEC, 或上次失败标脏。
            idle = t0 - _last_send_at if _last_send_at else 0.0
            need_reset = _force_reset or (_last_send_at and idle > IDLE_RESET_SEC)
            if need_reset:
                try:
                    await _request("sessions.reset", {"key": sk}, timeout=5.0)
                    logger.info(
                        "[OpenClaw] sessions.reset (idle=%.1fs forced=%s history=%d)",
                        idle, _force_reset, _history_count,
                    )
                    _force_reset = False
                    _history_count = 0
                except Exception as e:
                    logger.debug("[OpenClaw] sessions.reset skipped: %s", e)

            # chat.send → ack with runId
            ack = await _request("chat.send", {
                "sessionKey": sk,
                "message": message,
                "deliver": True,
                "idempotencyKey": uuid4().hex,
            }, timeout=max(5.0, deadline - time.monotonic()))
            run_id = ack.get("runId")
            if not run_id:
                raise OpenClawError(f"chat.send missing runId: {ack}")

            # wait for state=final
            final = await _wait_chat_final(run_id, timeout=max(1.0, deadline - time.monotonic()))
            content = _extract_text(final)
            if not content:
                raise OpenClawError(
                    f"empty content (elapsed={int((time.monotonic() - t0) * 1000)}ms)"
                )

            _history_count += 1
            _last_send_at = time.monotonic()

            # 每个 session key 只 patch label 一次 — 网关侧 label 全局唯一,
            # 重复 patch 会报 "label already in use"。label 加 gen 后缀防跨重连冲突。
            if sk not in _labeled_sessions:
                try:
                    await _request(
                        "sessions.patch",
                        {"key": sk, "label": f"V5 决策主会话 (gen {_connect_generation})"},
                        timeout=2.0,
                    )
                    _labeled_sessions.add(sk)
                except Exception as e:
                    # 标也加进去, 避免每次 chat 都重试同款失败
                    _labeled_sessions.add(sk)
                    logger.debug("[OpenClaw] sessions.patch label skipped: %s", e)

            logger.info(
                "[OpenClaw] ws ok elapsed=%dms agent=%s history=%d",
                int((time.monotonic() - t0) * 1000), agent_id, _history_count,
            )
            return content
        except OpenClawError:
            _force_reset = True
            await _close_ws()
            raise
        except (asyncio.TimeoutError, TimeoutError) as e:
            _force_reset = True
            await _close_ws()
            raise OpenClawError(f"timeout after {timeout}s") from e
        except (aiohttp.ClientError, OSError) as e:
            _force_reset = True
            await _close_ws()
            raise OpenClawError(f"transport: {type(e).__name__}: {e}") from e


# ---------- background idle compact task ----------

async def _compact_loop() -> None:
    """每 15s 醒一次, 闲置满足条件就 sessions.compact 把历史压成摘要。"""
    logger.info(
        "[OpenClaw] compact loop started (idle>=%.0fs turns>=%d)",
        COMPACT_IDLE_SEC, COMPACT_MIN_TURNS,
    )
    while True:
        try:
            await asyncio.sleep(15)
            # OpenClaw 关闭时跳过 — 不浪费 CPU 周期 + 不无效持有 lock
            if not await is_enabled():
                continue
            if _history_count < COMPACT_MIN_TURNS:
                continue
            if not _last_send_at:
                continue
            idle = time.monotonic() - _last_send_at
            if idle < COMPACT_IDLE_SEC:
                continue
            lock = _get_lock()
            if lock.locked():
                # 有 chat 在飞, 不打扰; 等下一轮
                continue
            async with lock:
                # 重新校验 (拿到锁的时候状态可能变了)
                if _history_count < COMPACT_MIN_TURNS:
                    continue
                idle = time.monotonic() - _last_send_at
                if idle < COMPACT_IDLE_SEC:
                    continue
                try:
                    await _ensure_connected()
                    sk = _session_key()
                    r = await _request("sessions.compact", {"key": sk}, timeout=30.0)
                    logger.info(
                        "[OpenClaw] background compact done idle=%.1fs history=%d → %s",
                        idle, _history_count, r,
                    )
                    # compact 后历史已压缩, 重置计数; 不动 _last_send_at 让 idle reset
                    # 仍能正确触发(下次访客来了如果再过 60s 还会被 reset)。
                    globals()["_history_count"] = 0
                except Exception as e:
                    logger.warning("[OpenClaw] background compact failed: %s", e)
                    await _close_ws()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("[OpenClaw] compact_loop unexpected: %s", e, exc_info=True)


async def _reconnect_loop() -> None:
    """连接守护 — 每 10s 探一下 WS 是否还活着, 断开就用指数退避把它拉回来.

    解决场景: 网关重启 / 网络抖动 / WS 被中间设备 idle reaper 砍掉.
    aiohttp 的 heartbeat=20 只能保 TCP 层活跃; 真断了得靠这里主动重连,
    不能等到下一个 chat 调用才发现 (那时已经吃了一发 timeout).

    冲突预防: 拿锁后才连; 连接成功后 generation+1, 默认 session key 跟着变,
    服务端旧 session 自然作废, 不会"new connect 写到旧 session 上"。
    """
    backoff = RECONNECT_MIN_BACKOFF
    logger.info(
        "[OpenClaw] reconnect guardian started (check=%.0fs backoff=%.0f→%.0f)",
        RECONNECT_CHECK_INTERVAL, RECONNECT_MIN_BACKOFF, RECONNECT_MAX_BACKOFF,
    )
    while True:
        try:
            await asyncio.sleep(RECONNECT_CHECK_INTERVAL)
            # 开关关了就不维护连接, 顺手把现有连接关掉省资源
            if not await is_enabled():
                if _ws is not None and not _ws.closed:
                    async with _get_lock():
                        await _close_ws()
                    logger.info("[OpenClaw] openclaw_enabled=0, 已关闭后台连接")
                backoff = RECONNECT_MIN_BACKOFF
                continue
            # 连着且没标脏 → 没事
            if _ws is not None and not _ws.closed and not _force_reset:
                backoff = RECONNECT_MIN_BACKOFF
                continue
            lock = _get_lock()
            if lock.locked():
                # 有 chat 在飞, 它走完会自带重连; 别打扰
                continue
            async with lock:
                # 再校验一遍 (拿锁过程中状态可能改变)
                if _ws is not None and not _ws.closed and not _force_reset:
                    backoff = RECONNECT_MIN_BACKOFF
                    continue
                try:
                    await _ensure_connected()
                    _force_reset_local_clear()
                    logger.info("[OpenClaw] 后台重连成功 gen=%d", _connect_generation)
                    backoff = RECONNECT_MIN_BACKOFF
                except Exception as e:
                    logger.warning(
                        "[OpenClaw] 后台重连失败, 将在 %.0fs 后再试: %s",
                        backoff, e,
                    )
                    # 让出锁后再睡退避, 不然占着锁影响其它路径
                    pass
            # 重连失败 → 退避
            if _ws is None or _ws.closed:
                await asyncio.sleep(backoff)
                backoff = min(RECONNECT_MAX_BACKOFF, backoff * 2)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("[OpenClaw] reconnect_loop unexpected: %s", e, exc_info=True)


def _force_reset_local_clear() -> None:
    """重连成功后清掉脏标记, 让下一发 chat 不再触发额外 sessions.reset."""
    global _force_reset, _history_count
    _force_reset = False
    _history_count = 0


def start_background_tasks() -> None:
    """app/main.py 启动钩子调用; 起 compact + 重连守护后台任务。已起则跳过。"""
    global _compact_task, _reconnect_task
    if _compact_task is None or _compact_task.done():
        _compact_task = asyncio.create_task(_compact_loop())
    if _reconnect_task is None or _reconnect_task.done():
        _reconnect_task = asyncio.create_task(_reconnect_loop())


async def set_agent_file(name: str, content: str) -> dict:
    """通过持久 WS 把一个文件写到 agent workspace (供 knowledge sync 用)。
    需要 OpenClaw 启用 + 网关可达; 失败抛 OpenClawError, 调用方决定吞还是抛。"""
    if not _yaml_enabled():
        raise OpenClawDisabled("openclaw config missing or disabled")
    s = get_settings()
    agent_id = s["model"]
    async with _get_lock():
        await _ensure_connected()
        try:
            return await _request(
                "agents.files.set",
                {"agentId": agent_id, "name": name, "content": content},
                timeout=15.0,
            )
        except (OpenClawError, asyncio.TimeoutError, TimeoutError, aiohttp.ClientError, OSError) as e:
            await _close_ws()
            raise OpenClawError(f"set_agent_file({name}): {e}") from e


async def shutdown() -> None:
    """app/main.py 关闭钩子调用; 停后台任务 + 关 WS。"""
    global _compact_task, _reconnect_task
    for name in ("_compact_task", "_reconnect_task"):
        t = globals().get(name)
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        globals()[name] = None
    await _close_ws()
