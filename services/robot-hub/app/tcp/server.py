"""
TCP + WebSocket 服务端

三种连接方式共存：
1. TCP 19888 — Pad：纯字符串 update_{专场ID}
2. TCP 19888 — 机器人(兼容)：JSON + \\n 分隔
3. WebSocket /ws/robot — 机器人(推荐)：JSON

TCP 通过消息内容自动区分 Pad 和 机器人。
消息处理逻辑共用 _process_message。
"""

import asyncio
import json
import time
import logging

from app.config import get_config
from app.database import get_db

logger = logging.getLogger("hub.tcp.server")
logger.setLevel(logging.DEBUG)  # 临时调试


def _extract_json(text: str):
    """从文本中提取第一个完整JSON对象，返回 (None, 0) 如果找不到
    使用括号计数器处理嵌套结构，正确处理字符串内的花括号。
    """
    start = text.find('{')
    if start < 0:
        return None, 0

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                # 完整JSON对象
                json_str = text[start:i + 1]
                try:
                    json.loads(json_str)
                    return json_str, i + 1
                except json.JSONDecodeError:
                    return None, 0
    return None, 0


# 全局机器人连接池
_robot_connections: dict = {}  # robot_id -> {"writer": ..., "reader": ..., "info": ...}
# 陌生人识别连续计数 (P3-⑥): 高频时告警, 提示运维查 face DB / 摄像头
_stranger_streak: dict = {}  # robot_id -> int
# 最近一次已识别人脸缓存: robot_id -> {user_id, name, visitor_id, ts}。
# OrionStar SDK 会在已识别人脸和空人脸事件之间抖动；短时间空事件沿用最近姓名，避免一会儿叫名字一会儿“您好”。
_recent_known_face: dict = {}
RECENT_KNOWN_FACE_TTL = 300
_server = None


_RECEPTION_LOCATION_KEYWORDS = ("接待", "前台", "迎宾")
_RECEPTION_FALLBACK_PREVIOUS_LOCATIONS = ("充电位", "充电", "待机", "待机位")
_RECEPTION_ACTIVE_STATES = ("WELCOME", "ASK_NEXT", "EXPLANATION", "DISCUSSING", "DISCUSS_DONE")
_RECEPTION_ACTIVE_TASKS = ("speak", "listen", "navigate", "show", "photo", "music", "video")
# 接待点主动问候状态机参数：
# - 接待问候后的应答窗口：机器人问“有什么需要帮助”后，用户的“没有/不用/需要/带我参观”
#   必须先在这里消费，不能落到普通状态机误触发下一步流程。
_RECEPTION_FOLLOWUP_TTL_SEC = 20.0
# - 用户同意进入参观模式选择后，短暂停止主动问候，避免刚进入下一步就被 10 秒问候打断；
# - 真正讲解/浏览/规划中由 engine_state/browse/currentTask 继续暂停；
# - 不能用 3600s 长暂停，否则现场中途状态残留会导致接待点永远不主动打招呼。
_RECEPTION_ACTIVE_PAUSE_SEC = 20.0
# - 接待点人脸帧持续上报时，按产品要求每 10 秒问候一次；
# - 同一秒内多条已识别/空人脸抖动只播一次；
# - 姓名优先用于文案。
_RECEPTION_ENCOUNTER_IDLE_RESET_SEC = 8.0
_RECEPTION_GLOBAL_GREETING_GAP_SEC = 10.0
_RECEPTION_SAME_KEY_COOLDOWN_SEC = 10.0
_DEFAULT_FOLLOWUP_LISTEN_TIMEOUT_SEC = 30
_MIN_FOLLOWUP_LISTEN_TIMEOUT_SEC = 5
_MAX_FOLLOWUP_LISTEN_TIMEOUT_SEC = 120


def _resolve_followup_listen_timeout(config_data: dict | None) -> int:
    """读取回答后的免唤醒窗口，并限制在 APK 支持的安全范围内。"""
    raw = (config_data or {}).get("listenTimeout", _DEFAULT_FOLLOWUP_LISTEN_TIMEOUT_SEC)
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        timeout = _DEFAULT_FOLLOWUP_LISTEN_TIMEOUT_SEC
    return max(_MIN_FOLLOWUP_LISTEN_TIMEOUT_SEC, min(_MAX_FOLLOWUP_LISTEN_TIMEOUT_SEC, timeout))


def _is_reception_location(location: str) -> bool:
    """只有接待/前台/迎宾类点位允许人脸主动问候。空位置保持兼容旧 APK。"""
    loc = (location or "").strip()
    if not loc:
        return True
    return any(k in loc for k in _RECEPTION_LOCATION_KEYWORDS)


def _resolve_face_location_for_reception(conn, location: str, data: dict | None = None) -> tuple[str, str]:
    """修正 APK 重连/导航后的点位误报。

    现场发现：重连时 evt_register 可能上报“充电位”，但随后人脸事件里的 location 才是真实位置；
    或者刚到达接待点后个别人脸帧仍沿用旧点位。这里使用“人脸事件位置优先，DB/连接上次位置兜底”的策略。
    """
    loc = (location or "").strip()
    if loc:
        return loc, "face_event"
    try:
        last_loc = (getattr(conn, "last_location", "") or "").strip()
        if last_loc:
            return last_loc, "conn_last_location"
    except Exception:
        pass
    return loc, "raw"


def _reception_pause_reason(ps: dict, engine=None, data: dict | None = None, now: float | None = None) -> str:
    """是否暂停主动问候。

    只在用户刚同意后的模式选择窗口、浏览/讲解/设备执行中暂停。
    不能因为历史 ASK_NEXT/WELCOME 残留就长期压住接待问候。
    """
    now = now or time.time()
    pause_until = float(ps.get("reception_pause_until") or 0.0)
    if pause_until > now:
        return f"mode_select_pause {int(pause_until - now)}s remaining"
    # 过期立即清理，避免日志一直显示历史暂停。
    if pause_until:
        ps["reception_pause_until"] = 0.0
    try:
        browse_mode = getattr(getattr(engine, "ws", None).browse, "mode", "") if engine else ""
        if browse_mode:
            return f"browse_mode={browse_mode}"
    except Exception:
        pass
    try:
        state = getattr(getattr(engine, "ws", None).dialog, "engine_state", "") if engine else ""
        if state in ("EXPLANATION", "DISCUSSING", "DISCUSS_DONE"):
            return f"engine_state={state}"
        # ASK_NEXT 只有在最近刚说完模式选择/追问时才暂停；旧 ASK_NEXT 残留不暂停接待。
        if state == "ASK_NEXT":
            last_bot = getattr(getattr(engine, "ws", None).dialog, "last_bot_reply", "") or ""
            last_speak_done = float(getattr(getattr(engine, "ws", None).dialog, "last_speaking_done_at", 0.0) or 0.0)
            if last_speak_done and now - last_speak_done < 60 and any(k in last_bot for k in ("预设路线", "智能规划", "自由选择", "下一站", "哪个展区")):
                return f"engine_state=ASK_NEXT_recent"
    except Exception:
        pass
    status = ((data or {}).get("status") or "").strip().lower()
    task = ((data or {}).get("currentTask") or "").strip().lower()
    # listening 是欢迎后等待用户答复的一部分，不暂停；speaking/navigating/showing 才避免插话。
    if task in _RECEPTION_ACTIVE_TASKS and task != "listen":
        return f"currentTask={task}"
    if status in ("speaking", "navigating", "showing"):
        return f"status={status}"
    return ""


def _reception_face_key(visitor: dict | None, user_id: str = "", name: str = "", unknown: bool = False) -> str:
    """生成接待会话身份键。

    现场日志显示同一个姓名可能被云端返回多个 userId/visitor_id；
    主动问候去重以姓名优先，避免同一人被不同 ID 连续问候。
    """
    if unknown:
        return "unknown"
    n = (name or "").strip() or ((visitor or {}).get("display_name") or (visitor or {}).get("name") or "").strip()
    if n:
        return f"name:{n.lower()}"
    vid = (visitor or {}).get("id")
    if vid:
        return f"visitor:{vid}"
    uid = (user_id or "").strip()
    return f"face:{uid}" if uid else "unknown"


def _should_reception_greet(ps: dict, face_key: str, now: float) -> tuple[bool, str]:
    """接待点主动问候判定。

    产品当前要求：只要在接待点检测到人脸，不管是否识别出姓名，都按 10 秒节奏主动询问。
    这里不再用“同一在场会话只问候一次”永久压制，只保留 10 秒全局防抖，防止 1 秒多帧刷屏。
    """
    last_greet_at = float(ps.get("last_face_welcome_at") or 0.0)

    ps["reception_last_seen_at"] = now

    if last_greet_at and now - last_greet_at < _RECEPTION_GLOBAL_GREETING_GAP_SEC:
        return False, f"ten_second_gap {int(_RECEPTION_GLOBAL_GREETING_GAP_SEC - (now - last_greet_at))}s remaining"

    ps["reception_session_key"] = face_key
    ps["reception_session_greeted"] = True
    ps["last_welcome_at"] = now
    ps["last_face_welcome_at"] = now
    ps["last_face_welcome_key"] = face_key
    ps["reception_followup_until"] = now + _RECEPTION_FOLLOWUP_TTL_SEC
    ps["reception_followup_key"] = face_key
    ps["reception_pause_until"] = 0.0
    return True, "ten_second_reception_greeting"


class RobotConnection:
    """单个机器人连接（支持 TCP 和 WebSocket 两种传输）"""

    def __init__(self, reader=None, writer=None, websocket=None):
        # TCP 模式
        self.reader = reader
        self.writer = writer
        # WebSocket 模式
        self.websocket = websocket
        # 公共属性
        self.transport = "ws" if websocket else "tcp"
        self.robot_id = None
        self.robot_name = ""
        self.connected_at = time.time()
        self.last_heartbeat = time.time()
        self.seq_counter = 0
        self.pending_callbacks: dict = {}  # seq -> asyncio.Future
        # TTS 串行: 同一机器人多句 TTS 必须等上一句 evt_tts_done 再发,
        # 否则 APK 会按"后到 cmd_tts 立即接管"打断前一句长播报。
        self.tts_lock = asyncio.Lock()
        self.tts_done_event = asyncio.Event()
        self.tts_done_event.set()  # 初始空闲态
        # evt_video_closed 冷却 — APK 观测到会连续重复发 (~5s/次), 防止反复念告别语
        self.last_video_closed_at = 0.0
        if writer:
            self.addr = writer.get_extra_info("peername")
        elif websocket:
            self.addr = (websocket.client.host, websocket.client.port) if websocket.client else ("ws", 0)
        else:
            self.addr = ("unknown", 0)

    def next_seq(self) -> int:
        self.seq_counter += 1
        return self.seq_counter

    async def send(self, msg: dict) -> bool:
        """发送 JSON 消息到机器人"""
        payload = None
        try:
            payload = json.dumps(msg, ensure_ascii=False)
            if self.websocket:
                await self.websocket.send_text(payload)
            else:
                self.writer.write((payload + "\n").encode("utf-8"))
                await self.writer.drain()
            logger.debug("[豹小秘 %s] 发送(%s): %s data=%s", self.robot_id or self.addr, self.transport, msg.get("type"), msg.get("data", {}))
            _fire_log_robot_command(self.robot_id, msg, payload, "ok")
            return True
        except Exception as e:
            logger.error("[豹小秘 %s] 发送失败(%s): %s", self.robot_id or self.addr, self.transport, e)
            _fire_log_robot_command(self.robot_id, msg, payload, "error", str(e))
            return False

    # 按 cmd 类型抬高超时下限 — 机器人物理移动不是网络级 ack, 30s 远远不够,
    # 之前 cmd_navigate 30s 超时但 31s 才 evt_arrived, future 已释放, 迟到回调
    # 落不到 caller 上, flow 被错误判失败. 这里给出现场实测下限.
    _SLOW_CMD_MIN_TIMEOUT = {
        "cmd_navigate": 180.0,
        # 拍照包含导航、站位准备、倒计时、照片停留/重拍确认，240s 容易误判超时。
        "cmd_take_photo": 300.0,
    }

    async def send_cmd(self, cmd_type: str, data: dict, timeout: float = 30) -> dict:
        """发送指令并等待回调"""
        seq = self.next_seq()
        msg = {"type": cmd_type, "seq": seq, "data": data}

        # 慢指令兜底: 即使 caller 传了短 timeout, 也至少给到设备最低耗时
        floor = self._SLOW_CMD_MIN_TIMEOUT.get(cmd_type)
        if floor and timeout < floor:
            logger.debug("[豹小秘 %s] %s timeout %.0fs 抬高到下限 %.0fs",
                         self.robot_id, cmd_type, timeout, floor)
            timeout = floor

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self.pending_callbacks[seq] = future

        if not await self.send(msg):
            self.pending_callbacks.pop(seq, None)
            return None

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning("[豹小秘 %s] 指令 %s seq=%d 超时(%.0fs)",
                           self.robot_id, cmd_type, seq, timeout)
            self.pending_callbacks.pop(seq, None)
            return None

    def resolve_callback(self, seq: int, data: dict):
        """解决等待中的回调.

        若 seq 已不在 pending_callbacks (caller 端 await 已超时被释放), 这里仍可
        把事件落地到日志, 帮助现场排查"明明到达了 hub 却报失败"这种迟到回调.
        """
        future = self.pending_callbacks.pop(seq, None)
        if future and not future.done():
            future.set_result(data)
            return
        # 迟到回调: 对应 cmd 已经被 caller 端超时丢弃, 但 robot 仍在最后一刻送回事件
        evt = (data or {}).get("type", "?")
        if evt and not evt.startswith("evt_heart"):
            logger.warning(
                "[豹小秘 %s] 迟到回调 seq=%d type=%s (对应 cmd 超时已释放, 上层可能误判失败)",
                self.robot_id, seq, evt,
            )

    async def speak_serialized(self, text: str, priority: str = "normal",
                               extra_data: dict = None) -> bool:
        """发 cmd_tts 并等到 evt_tts_done 才返回, 同一机器人内串行.

        全局唯一 TTS 入口 — action_executor / flow_executor / SmartEngine
        都应走这里, 避免后到 cmd_tts 打断前一句长播报。

        - 锁: tts_lock 保证调用方排队
        - 等待: tts_done_event 由 server 端 evt_tts_done 处理段 set
        - APK 协议上 cmd_tts 没有独立 ack, 唯一的回包就是 evt_tts_done。
          以前 send_cmd 5s ack timeout 经常假阳性 (evt_tts_done 在 100ms 后到也判失败),
          现在直接发裸命令 + 单一 wait, 按文本长度估时长 (0.35s/字 + 8s, 15-90s)。
        """
        if not text:
            return True
        async with self.tts_lock:
            self.tts_done_event.clear()
            seq = self.next_seq()
            payload = {"text": text, "priority": priority}
            if extra_data:
                payload.update(extra_data)
            msg = {"type": "cmd_tts", "seq": seq, "data": payload}
            try:
                if not await self.send(msg):
                    logger.warning("[豹小秘 %s] cmd_tts 发送失败 seq=%d", self.robot_id, seq)
                    return False
                wait_timeout = min(90.0, max(15.0, len(text) * 0.35 + 8.0))
                try:
                    await asyncio.wait_for(self.tts_done_event.wait(), timeout=wait_timeout)
                    return True
                except asyncio.TimeoutError:
                    logger.warning("[豹小秘 %s] 等 evt_tts_done 超时 chars=%d wait=%.0fs (强制释放)",
                                   self.robot_id, len(text), wait_timeout)
                    return False
            finally:
                # 兜底: 不论早退/异常都释放, 防止下一句永远等
                self.tts_done_event.set()

    async def close(self):
        try:
            if self.websocket:
                await self.websocket.close()
            elif self.writer:
                self.writer.close()
                await self.writer.wait_closed()
        except Exception:
            pass


def get_robot(robot_id: str) -> RobotConnection:
    """获取机器人连接"""
    return _robot_connections.get(robot_id)


def get_all_robots() -> dict:
    """获取所有在线机器人"""
    return dict(_robot_connections)


async def push_config_to_all_robots():
    """广播下发配置到所有在线机器人（后台修改配置后调用）"""
    config_data = await _get_robot_config_data()
    count = 0
    for robot_id, conn in list(_robot_connections.items()):
        try:
            await conn.send({"type": "push_config", "data": config_data})
            count += 1
        except Exception as e:
            logger.warning("[豹小秘] %s 下发配置失败: %s", robot_id, e)
    return count


# ============================================================
# 连接处理
# ============================================================
def _fire_log_robot_command(robot_id: str, msg: dict, payload: str, status: str, error: str = ""):
    """中枢平台发出的机器人指令 -> execution_logs (log_type=robot_command).
    跳过 ack_*: 那是协议应答, 不是中枢主动指令. 写库 fire-and-forget, 不阻塞 send."""
    try:
        msg_type = (msg or {}).get("type") or ""
        if not msg_type or msg_type.startswith("ack_"):
            return
        seq = (msg or {}).get("seq")
        action = msg_type if seq is None else f"{msg_type}#{seq}"
        detail = payload if payload is not None else json.dumps(msg, ensure_ascii=False)
        if error:
            detail = f"{detail}  ERROR={error}"
        asyncio.create_task(_log_robot_command(robot_id or "", action, detail, status))
    except Exception as e:
        logger.debug("[TCP] 指令日志调度失败: %s", e)


async def _log_robot_command(robot_id: str, action: str, detail: str, status: str):
    try:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO execution_logs (log_type, source, target, action, detail, status, created_at)
                   VALUES ('robot_command', 'hub', ?, ?, ?, ?, datetime('now','localtime'))""",
                (robot_id, action, detail, status),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        logger.debug("[TCP] 指令日志写入失败: %s", e)


async def _log_robot_conn(action: str, robot_id: str, detail: str, status: str = "ok"):
    """写 robot_conn 类型日志到 execution_logs, 给管理后台日志 tab 查看."""
    try:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO execution_logs (log_type, source, target, action, detail, status, created_at)
                   VALUES ('robot_conn', ?, ?, ?, ?, ?, datetime('now','localtime'))""",
                ("tcp_server", robot_id or "", action, detail[:500], status),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        logger.debug("[TCP] 连接日志写入失败: %s", e)


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """处理新的 TCP 连接"""
    addr = writer.get_extra_info("peername")
    logger.info("[TCP] 新连接: %s", addr)
    # 连接事件日志 fire-and-forget, 不阻塞 reader.read 循环
    asyncio.create_task(_log_robot_conn("connect", "", f"TCP 新连接 {addr}"))

    conn = RobotConnection(reader, writer)
    buffer = b""

    try:
        while True:
            data = await asyncio.wait_for(reader.read(4096), timeout=120)
            if not data:
                break

            logger.debug("[TCP] 原始数据 %s: %s (hex: %s)",
                         addr, data[:200], data[:50].hex())

            buffer += data

            # 尝试按行分割处理
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line_str = line.decode("utf-8", errors="ignore").strip()
                if not line_str:
                    continue
                await _process_message(conn, line_str)

            # 处理没有换行的消息（兼容不发\n的客户端）
            if buffer and b"\n" not in buffer:
                line_str = buffer.decode("utf-8", errors="ignore").strip()

                # 处理粘包: 多个JSON粘在一起（如 }{  }
                # 逐个提取完整JSON对象
                parsed_any = False
                remaining = line_str
                while remaining:
                    remaining = remaining.strip()
                    if not remaining:
                        break
                    # 尝试解析第一个JSON
                    try:
                        obj, end_idx = _extract_json(remaining)
                        if obj is not None:
                            await _process_message(conn, remaining[:end_idx])
                            remaining = remaining[end_idx:]
                            parsed_any = True
                        else:
                            break
                    except Exception:
                        break

                if parsed_any:
                    buffer = remaining.encode("utf-8") if remaining else b""
                else:
                    # 单个JSON尝试
                    try:
                        json.loads(line_str)
                        await _process_message(conn, line_str)
                        buffer = b""
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        # 容错: 截取到最后一个 } 再试（工具追加垃圾字符）
                        brace_idx = line_str.rfind("}")
                        if brace_idx > 0:
                            trimmed = line_str[: brace_idx + 1]
                            try:
                                json.loads(trimmed)
                                await _process_message(conn, trimmed)
                                buffer = b""
                                continue
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                pass
                        # Pad 纯字符串
                        if line_str.startswith("update_"):
                            await _process_message(conn, line_str)
                            buffer = b""
                        # 否则继续等待更多数据

    except asyncio.TimeoutError:
        logger.info("[TCP] 连接超时: %s", addr)
    except ConnectionResetError:
        logger.info("[TCP] 连接重置: %s", addr)
    except Exception as e:
        logger.error("[TCP] 连接异常 %s: %s", addr, e)
    finally:
        # 清理: 必须 identity 检查 — 否则老 conn 的 cleanup 会删掉已被新 conn 覆盖的 slot
        # (APK 反复重连时, 老 socket 的 finally 跑到时, 新 conn 已经注册到同一 robot_id;
        #  无脑 del 会让 _robot_connections[robot_id] 消失, action_executor 找不到机器人)
        if conn.robot_id and _robot_connections.get(conn.robot_id) is conn:
            try:
                from app.engine.smart_engine import get_smart_engine
                engine = get_smart_engine(conn.robot_id)
                if engine:
                    await engine.cancel_legacy_teaching_on_disconnect()
            except Exception as exc:
                logger.warning("[豹小秘 %s] 断线清理教学状态失败: %s", conn.robot_id, exc)
            del _robot_connections[conn.robot_id]
            await _update_robot_status(conn.robot_id, "offline")
            logger.info("[豹小秘] %s 已离线", conn.robot_id)
            await _log_robot_conn("disconnect", conn.robot_id, f"机器人离线 {addr}")
        else:
            await _log_robot_conn("disconnect", conn.robot_id or "", f"已被替换或未注册连接关闭 {addr}")
        await conn.close()
        logger.info("[TCP] 连接关闭: %s", addr)


async def _process_message(conn: RobotConnection, raw: str):
    """处理单条消息"""

    # 1. Pad 纯字符串协议：update_{专场ID}
    if raw.startswith("update_"):
        await _handle_pad_update(raw)
        return

    # 2. 机器人 JSON 协议
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        # 容错: 截取到最后一个 } 再尝试
        brace_idx = raw.rfind("}")
        if brace_idx > 0:
            try:
                msg = json.loads(raw[: brace_idx + 1])
                logger.debug("[TCP] 截断尾部垃圾字符后解析成功: %s", raw[brace_idx + 1:])
            except json.JSONDecodeError:
                logger.warning("[TCP] 无法解析消息: %s", raw[:200])
                return
        else:
            logger.warning("[TCP] 无法解析消息: %s", raw[:200])
            return

    msg_type = msg.get("type", "")
    seq = msg.get("seq", 0)
    data = msg.get("data", {})

    # 兜底: 任何带 robotId 的事件到达时, 若 conn.robot_id 还未绑定, 自动登记.
    # 某些机器人 APK 版本不发 evt_register 只发 evt_get_config+evt_battery+evt_person_count,
    # 会导致 conn.robot_id=None 一直持续, 后续事件全被业务层 `if conn.robot_id` 守卫丢弃.
    if not conn.robot_id and isinstance(data, dict):
        rid = (data.get("robotId") or "").strip()
        if rid:
            conn.robot_id = rid
            _robot_connections[rid] = conn
            logger.info("[TCP] 从事件 %s 自动绑定 robot_id=%s (未收到 evt_register)",
                        msg_type, rid)
            # 重要: 绑定本身(修内存 dict)已完成, DB 写入 fire-and-forget,
            # 不能阻塞 _handle_get_config 里 ack_config 的发送.
            # 之前这里串行跑 2 次 DB 写入 (~20-50ms), 机器人 APK 对 ack_config 有 timeout
            # 超时断连重试, 表现为短连不断重连.
            _name = data.get("robotName", "") or ""
            _ver = data.get("version", "")
            _bat = int(data.get("battery", 0) or 0)
            _loc = data.get("location", "") or ""
            _msg_type_for_log = msg_type

            async def _auto_bind_db_write():
                try:
                    await _log_robot_conn(
                        "auto_bind", rid,
                        f"从 {_msg_type_for_log} 自动绑定(未收 evt_register)",
                    )
                    await _update_robot_status(
                        rid, "idle",
                        name=_name, version=_ver, battery=_bat, location=_loc,
                    )
                except Exception as e:
                    logger.debug("[TCP] 自动绑定后台 DB 写入失败: %s", e)
            asyncio.create_task(_auto_bind_db_write())

    # 事件路由
    if msg_type == "evt_get_config":
        await _handle_get_config(conn, seq, data)
    elif msg_type == "evt_register":
        conn.teaching_protocol_version = data.get("teachingProtocolVersion", 0)
        await _handle_register(conn, seq, data)
    elif msg_type == "evt_heartbeat":
        await _handle_heartbeat(conn, seq, data)
    elif msg_type == "evt_offline":
        await _handle_offline(conn, seq, data)
    elif msg_type.startswith("evt_"):
        # 通用事件回调处理
        await _handle_event(conn, msg_type, seq, data)
    else:
        logger.warning("[TCP] 未知消息类型: %s", msg_type)


# ============================================================
# Pad 协议处理
# ============================================================
async def _handle_pad_update(raw: str):
    """处理 Pad 专场切换通知：仅更新状态，不触发联动"""
    try:
        special_id = int(raw.split("_", 1)[1])
    except (IndexError, ValueError):
        logger.warning("[Pad] 无法解析专场ID: %s", raw)
        return

    db = await get_db()
    try:
        # 获取旧专场
        old = await db.execute_fetchall(
            "SELECT value FROM system_config WHERE key='current_special_id'"
        )
        old_id = old[0]["value"] if old else "0"

        # 更新状态
        await db.execute("UPDATE specials SET is_current=0")
        await db.execute("UPDATE specials SET is_current=1 WHERE id=?", (special_id,))
        await db.execute(
            "UPDATE system_config SET value=? WHERE key='current_special_id'",
            (str(special_id),),
        )

        # 获取新专场名
        name_row = await db.execute_fetchall(
            "SELECT name FROM specials WHERE id=?", (special_id,)
        )
        name = name_row[0]["name"] if name_row else str(special_id)
        await db.execute(
            "UPDATE system_config SET value=? WHERE key='current_special_name'",
            (name,),
        )

        await db.commit()
        logger.info("[Pad] 专场状态已更新: %s → %s (%s)", old_id, special_id, name)

        # 记录日志
        await db.execute(
            """INSERT INTO execution_logs (log_type, source, action, detail, status, created_at)
               VALUES ('sync', 'pad', 'update_special', ?, 'ok', datetime('now','localtime'))""",
            (f"{old_id} → {special_id} ({name})",),
        )
        await db.commit()
    finally:
        await db.close()


# ============================================================
# 机器人协议处理
# ============================================================
_config_cache: dict = {"data": None, "expire_at": 0.0}


def _invalidate_robot_config_cache() -> None:
    """配置变更后外部调用, 使缓存立即失效. (目前未主动调, TTL 到期自动刷)"""
    _config_cache["expire_at"] = 0.0


async def _get_robot_config_data() -> dict:
    """
    组装下发到机器人的配置.
    - wakeWord 已固定为"小豹小豹", 不读 system_config / config.yaml
    - 其它参数支持 DB 覆盖
    - 20s 模块级 TTL 缓存: 机器人连接高频, 避免每次都打 DB (原耗时 >200ms)
    """
    now = time.time()
    if _config_cache["data"] is not None and now < _config_cache["expire_at"]:
        return dict(_config_cache["data"])  # 返回副本避免 caller 改到缓存

    from app.engine.intent_resolver import WAKE_WORD
    defaults = get_config().get("robot", {})
    result = {
        "wakeWord": WAKE_WORD,
        "heartbeatInterval": defaults.get("heartbeat_interval", 30),
        "listenTimeout": defaults.get("listen_timeout", 30),
        "listenLanguage": defaults.get("listen_language", "zh"),
    }
    # 数据库 system_config 覆盖 (不再包含 wake_word)
    try:
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT key, value FROM system_config WHERE key IN ('heartbeat_interval','listen_timeout','listen_language')"
        )
        await db.close()
        for r in rows:
            k, v = r["key"], r["value"]
            if k == "heartbeat_interval" and v:
                result["heartbeatInterval"] = int(v)
            elif k == "listen_timeout" and v:
                result["listenTimeout"] = int(v)
            elif k == "listen_language" and v:
                result["listenLanguage"] = v
    except Exception as e:
        logger.warning("读取数据库配置失败，用默认值: %s", e)
    # 写回缓存 (20s TTL)
    _config_cache["data"] = dict(result)
    _config_cache["expire_at"] = time.time() + 20
    return result


async def _push_config_to_robot(conn: RobotConnection):
    """主动下发配置到机器人（注册成功后调用）"""
    config_data = await _get_robot_config_data()
    await conn.send({
        "type": "push_config",
        "data": config_data,
    })
    logger.info("[豹小秘] %s 下发配置: wakeWord=%s", conn.robot_id or conn.addr, config_data["wakeWord"])
    # DB 日志 fire-and-forget, 不阻塞协议流
    _target = conn.robot_id or str(conn.addr)
    _detail = 'wakeWord=%s, hb=%s, listen=%s' % (
        config_data['wakeWord'],
        config_data['heartbeatInterval'],
        config_data['listenTimeout'],
    )

    async def _push_log_bg():
        try:
            db = await get_db()
            try:
                await db.execute(
                    """INSERT INTO execution_logs (log_type, source, target, action, detail, status, created_at)
                       VALUES ('config_push', 'hub', ?, 'push_config', ?, 'ok', datetime('now','localtime'))""",
                    (_target, _detail),
                )
                await db.commit()
            finally:
                await db.close()
        except Exception as e:
            logger.warning("配置下发日志写入失败: %s", e)
    asyncio.create_task(_push_log_bg())


async def _handle_get_config(conn: RobotConnection, seq: int, data: dict):
    """
    机器人主动请求配置（连接后第一步）

    流程: 机器人连接 → evt_get_config → ack_config → evt_register
    """
    robot_id = data.get("robotId", "")
    config_data = await _get_robot_config_data()
    await conn.send({
        "type": "ack_config",
        "seq": seq,
        "data": config_data,
    })
    conn._config_sent = True
    logger.info("[豹小秘] %s 请求配置 → 已返回 (wakeWord=%s)",
                robot_id or conn.addr, config_data.get("wakeWord", ""))


async def _handle_register(conn: RobotConnection, seq: int, data: dict):
    """机器人注册"""
    robot_id = data.get("robotId", "")
    if not robot_id:
        await conn.send({"type": "ack_register", "seq": seq,
                         "data": {"success": False, "message": "robotId 不能为空"}})
        return

    conn.robot_id = robot_id
    conn.robot_name = data.get("robotName", "")
    _reg_loc = (data.get("location", "") or "").strip()
    # 重连时 APK 可能上报“充电位”，但如果旧连接/DB 最近位置是接待点，不要立刻用充电位覆盖，
    # 否则重连后站在接待点也会被误判非接待点。
    try:
        prev_loc = (getattr(conn, "last_location", "") or "").strip()
        if _reg_loc and not (any(k in _reg_loc for k in _RECEPTION_FALLBACK_PREVIOUS_LOCATIONS) and _is_reception_location(prev_loc)):
            conn.last_location = _reg_loc
    except Exception:
        pass
    _robot_connections[robot_id] = conn

    # 立刻 ack_register, 别让 DB 写入阻塞响应 (APK 可能对 ack_register 有短 timeout)
    await conn.send({
        "type": "ack_register",
        "seq": seq,
        "data": {
            "success": True,
            "message": "注册成功",
        },
    })
    logger.info("[豹小秘] %s (%s) 注册成功 (%s)", robot_id, conn.robot_name, conn.transport)

    # DB 写入 + 日志 fire-and-forget
    _name = conn.robot_name
    _ver = data.get("version", "")
    _bat = data.get("battery", 0)
    _loc = (data.get("location", "") or "").strip()
    try:
        _prev_loc = (getattr(conn, "last_location", "") or "").strip()
        if _loc and any(k in _loc for k in _RECEPTION_FALLBACK_PREVIOUS_LOCATIONS) and _is_reception_location(_prev_loc):
            logger.info("[豹小秘] %s 注册位置疑似重连误报: raw=%s prev=%s → 保留 prev", robot_id, _loc, _prev_loc)
            _loc = _prev_loc
    except Exception:
        pass

    async def _register_bg():
        try:
            await _update_robot_status(
                robot_id, "idle",
                name=_name, version=_ver, battery=_bat, location=_loc,
            )
            await _log_robot_conn(
                "register", robot_id,
                f"注册成功 name={_name} ver={_ver} battery={_bat}%",
            )
        except Exception as e:
            logger.debug("[TCP] 注册后台 DB 写入失败: %s", e)
    asyncio.create_task(_register_bg())

    # 正常流程下机器人已在注册前通过 evt_get_config 获取配置
    # 仅当机器人跳过 get_config 直接注册时，补推配置
    if not getattr(conn, '_config_sent', False):
        await _push_config_to_robot(conn)


async def _handle_heartbeat(conn: RobotConnection, seq: int, data: dict):
    """机器人心跳"""
    conn.last_heartbeat = time.time()
    battery = data.get("battery")

    if conn.robot_id:
        db = await get_db()
        try:
            updates = ["last_heartbeat=datetime('now','localtime')"]
            params = []
            if battery is not None:
                updates.append("battery=?")
                params.append(battery)
            params.append(conn.robot_id)
            await db.execute(
                f"UPDATE robots SET {', '.join(updates)} WHERE id=?", params
            )
            await db.commit()
        finally:
            await db.close()


async def _handle_offline(conn: RobotConnection, seq: int, data: dict):
    """机器人主动下线"""
    if conn.robot_id:
        # identity 检查 — 防止老 conn 的 offline 把新 conn 覆盖后的 slot 删除掉
        slot_is_self = _robot_connections.get(conn.robot_id) is conn
        if slot_is_self:
            await _update_robot_status(conn.robot_id, "offline")
            del _robot_connections[conn.robot_id]
            # 销毁 SmartEngine 实例, 避免残留状态影响下次上线
            try:
                from app.engine.smart_engine import destroy_smart_engine
                destroy_smart_engine(conn.robot_id)
            except Exception:
                pass
        logger.info("[豹小秘] %s 主动下线: %s%s",
                    conn.robot_id, data.get("reason", ""),
                    "" if slot_is_self else " (已被新连接替换, 仅记录)")


async def _handle_event(conn: RobotConnection, msg_type: str, seq: int, data: dict):
    """通用事件处理 — 解决等待中的回调"""
    if conn.robot_id and _robot_connections.get(conn.robot_id) is not conn:
        return
    # 教学事件按TCP接收顺序处理，状态变更不等待HTTP，也不将原文写普通日志。
    teaching_event = (msg_type in {"evt_teaching_started", "evt_teaching_transcribing", "evt_teaching_finished", "evt_teaching_reset"}
                      or (msg_type in {"evt_speech_result", "evt_listen_timeout"}
                          and (data.get("teachingTurnId") or data.get("speechMode") == "TEACHING")))
    if teaching_event:
        from app.engine.smart_engine import get_smart_engine
        engine = get_smart_engine(conn.robot_id)
        runtime = getattr(engine, "_teaching_runtime", None) if engine else None
        if not runtime or engine.conn is not conn:
            return
        turn_id = str(data.get("teachingTurnId") or "")
        if msg_type == "evt_teaching_started":
            runtime.started(turn_id, data.get("success"))
        elif msg_type == "evt_teaching_transcribing":
            runtime.transcribing(turn_id, data.get("completionReason"))
        elif msg_type == "evt_teaching_finished":
            runtime.finish(turn_id, data.get("completionReason"), data.get("lastFragmentSeq"), data.get("replayRequested", False))
        elif msg_type == "evt_speech_result":
            runtime.fragment(turn_id, data.get("fragmentSeq"), data.get("text"))
        elif msg_type == "evt_teaching_reset":
            if not turn_id or any(s and s.turn_id == turn_id for s in (runtime.active, runtime.recent)):
                runtime.clear("reception_reset")
        else:
            runtime.cancel(turn_id, str(data.get("reason") or "listen_error"))
        return
    logger.info("[豹小秘 %s] 事件: %s seq=%d", conn.robot_id, msg_type, seq)

    # 解决回调
    conn.resolve_callback(seq, {"type": msg_type, "data": data})

    # 特定事件附加处理
    if msg_type == "evt_music_request":
        # 兼容旧版 APK: 旧 APK 在本地拦截了"唱首歌/唱歌"后,
        # 发送 evt_music_request 到 Hub 请求音乐.
        # 这里查曲库返回 cmd_play_music, 让旧 APK 也能播放曲库歌曲.
        if conn.robot_id:
            import random
            from app.engine.host import get_music_url_base
            _db = await get_db()
            try:
                _tracks = await _db.execute_fetchall(
                    "SELECT id, name, filename FROM music_tracks WHERE enabled=1"
                )
                if _tracks:
                    _t = random.choice([dict(r) for r in _tracks])
                    _base_rows = await _db.execute_fetchall(
                        "SELECT value FROM system_config WHERE key=\'music_url_base\'"
                    )
                    _url_base = ((_base_rows[0]["value"] if _base_rows else "") or "").rstrip("/") \
                        or get_music_url_base()
                    _url = f"{_url_base}/media/music/{_t['filename']}"
                    await conn.send({
                        "type": "cmd_play_music",
                        "data": {"audioUrl": _url, "songName": _t["name"]},
                    })
                    logger.info("[豹小秘 %s] evt_music_request → 返回曲库歌曲: %s", conn.robot_id, _t["name"])
            except Exception as e:
                logger.warning("[TCP] evt_music_request 处理异常: %s", e)
            finally:
                await _db.close()

    if msg_type == "evt_arrived":
        if conn.robot_id:
            await _update_robot_status(
                conn.robot_id, "idle",
                location=data.get("pointName", ""),
                point_id=data.get("pointId", ""),
            )
            if data.get("pointName"):
                try:
                    conn.last_location = data.get("pointName", "")
                except Exception:
                    pass
            # 展区联动: 到达点位后自动触发绑定的设备/资源
            asyncio.create_task(_trigger_area_bindngs(
                conn.robot_id, data.get("pointId"),
            ))

    elif msg_type == "evt_tts_done":
        if conn.robot_id:
            await _update_robot_status(conn.robot_id, "idle")
            # 唤醒 speak_serialized 等待中的下一句 TTS
            conn.tts_done_event.set()
            _teaching_tts_started = bool(
                data.get("teachingTurnId")
                and str(data.get("teachingAction") or "").upper() == "START"
            )
            # TTS 播报结束后自动打开配置时长的免唤醒窗口 — 自动化流程进行中除外。
            # status 必须**完整覆盖** 流程"还在跑或将要跑"的所有状态:
            #   pending(刚 start_flow 还没进 main loop) / running / paused
            from app.engine.flow_executor import get_active_flow
            _active = get_active_flow(conn.robot_id)
            _flow_active = bool(_active) and getattr(_active, "status", "") not in (
                "completed", "cancelled", "error", ""
            )
            if _teaching_tts_started:
                logger.info(
                    "[豹小秘 %s] 教学提示播报完成，由 APK 开启教学监听 turn=%s",
                    conn.robot_id,
                    data.get("teachingTurnId"),
                )
                if data.get("success") is False:
                    try:
                        from app.engine.smart_engine import get_smart_engine
                        engine = get_smart_engine(conn.robot_id)
                        if engine:
                            asyncio.create_task(engine.handle_legacy_teaching_timeout(
                                str(data.get("teachingTurnId") or ""),
                                "prompt_tts_failed",
                            ))
                    except Exception as exc:
                        logger.warning("[豹小秘 %s] 教学提示失败清理异常: %s", conn.robot_id, exc)
            elif data.get("success") is False:
                # Wake-word interruption already opens the RobotOS ASR window.
                # Restarting it here cancels the user's new utterance.
                logger.info("[豹小秘 %s] TTS中止/失败，仅释放播报等待，不重新打开收音", conn.robot_id)
            elif not _flow_active:
                try:
                    _listen_config = await _get_robot_config_data()
                    _listen_timeout = _resolve_followup_listen_timeout(_listen_config)
                except Exception as exc:
                    _listen_timeout = _DEFAULT_FOLLOWUP_LISTEN_TIMEOUT_SEC
                    logger.warning("[豹小秘 %s] 读取免唤醒时长失败，使用默认%ss: %s",
                                   conn.robot_id, _listen_timeout, exc)
                asyncio.create_task(conn.send({
                    "type": "cmd_listen",
                    "data": {"timeout": _listen_timeout},
                }))
                logger.info("[豹小秘 %s] TTS结束，开启免唤醒收音%ss",
                            conn.robot_id, _listen_timeout)
            else:
                logger.debug("[豹小秘 %s] 流程进行中(%s), 跳过自动收音",
                             conn.robot_id, getattr(_active, "status", "?"))

    elif msg_type in ("evt_photo_done", "evt_photo_failed"):
        if conn.robot_id:
            await _update_robot_status(conn.robot_id, "idle")

    elif msg_type == "evt_show_done":
        # 屏幕展示完成
        if conn.robot_id:
            await _update_robot_status(conn.robot_id, "idle")

    elif msg_type == "evt_nav_failed":
        # 导航失败 → 停止流程 + 通知 SmartEngine 回到 ASK_NEXT
        if conn.robot_id:
            await _update_robot_status(conn.robot_id, "idle")
            # APK 协议有时漏字段 reason; 兜底标记 unknown 不要让排查丢线索
            _reason = (data.get("reason") or "").strip() or "unknown(sdk_missing_field)"
            # APK 反语义: 机器人已经在目的地点位附近时也回 evt_nav_failed,
            # 这其实是"无需移动"的成功态, 流程不该终止。
            _already_arrived = any(m in _reason for m in
                ("已经在目的地", "已在目的地", "目的地范围内", "无需导航"))
            if _already_arrived:
                logger.info("[豹小秘 %s] 导航无需移动: pointId=%s reason=%s (视为成功, 不终止流程)",
                            conn.robot_id, data.get("pointId"), _reason)
                # 注意: _step_navigate 那边的 send_cmd 拿到 evt_nav_failed 也会被同步处理为成功
                # (代码已加 _already_arrived_markers 判断)。这里仅日志提示。
                return
            logger.warning("[豹小秘 %s] 导航失败: pointId=%s reason=%s",
                           conn.robot_id, data.get("pointId"), _reason)
            # 停止正在执行的流程（导航失败后续步骤无意义）
            from app.engine.flow_executor import get_active_flow, stop_flow
            active = get_active_flow(conn.robot_id)
            if active and active.status == "running":
                await stop_flow(conn.robot_id)
                logger.info("[豹小秘 %s] 导航失败 → 流程已停止", conn.robot_id)
            # 通知 SmartEngine 状态回退
            try:
                from app.engine.smart_engine import get_smart_engine
                engine = get_smart_engine(conn.robot_id)
                if engine:
                    engine.ws.transition("ASK_NEXT")
            except Exception:
                pass

    elif msg_type == "evt_listen_timeout":
        # 收音超时 → SmartEngine 记录无响应
        if conn.robot_id:
            await _update_robot_status(conn.robot_id, "idle")
            logger.info("[豹小秘 %s] 收音超时: %s", conn.robot_id, data.get("reason", "timeout"))
            _teaching_turn_id = str(data.get("teachingTurnId") or "").strip()
            if _teaching_turn_id:
                try:
                    from app.engine.smart_engine import get_smart_engine
                    engine = get_smart_engine(conn.robot_id)
                    if engine:
                        asyncio.create_task(engine.handle_legacy_teaching_timeout(
                            _teaching_turn_id,
                            str(data.get("reason") or "timeout"),
                        ))
                except Exception as exc:
                    logger.warning("[豹小秘 %s] 教学超时处理失败: %s", conn.robot_id, exc)
                return
            # 通知 SmartEngine 记录无响应（连续3次触发回IDLE由健康检查处理）
            try:
                from app.engine.smart_engine import get_smart_engine
                engine = get_smart_engine(conn.robot_id)
                if engine:
                    engine.ws.record_low_confidence(True)
            except Exception:
                pass

    elif msg_type == "evt_cancelled":
        # 任务取消确认 → 停止流程 + SmartEngine 回到 IDLE
        if conn.robot_id:
            await _update_robot_status(conn.robot_id, "idle")
            logger.info("[豹小秘 %s] 任务已取消: %s", conn.robot_id, data.get("cancelledTask"))
            # 停止正在执行的流程
            from app.engine.flow_executor import get_active_flow, stop_flow
            active = get_active_flow(conn.robot_id)
            if active and active.status in ("running", "paused"):
                await stop_flow(conn.robot_id)
                logger.info("[豹小秘 %s] 取消事件 → 流程已停止", conn.robot_id)
            # 通知 SmartEngine 回到 IDLE
            try:
                from app.engine.smart_engine import get_smart_engine
                engine = get_smart_engine(conn.robot_id)
                if engine:
                    engine.ws.transition("IDLE")
                    engine.ws.reset_browse()
            except Exception:
                pass

    elif msg_type == "evt_video_closed":
        # 视频/资源被用户主动关闭 → 等同于用户说"不用了": 停止流程 + 重置浏览 + 回 IDLE
        # 并播报专用结束语 (区别于普通"不用了"的静默结束)
        # 注意: APK 实测会连续重复发该事件 (~5s/次), 需要冷却 + 只有真正有事可结束时才播报
        reason = data.get("reason", "")
        if conn.robot_id:
            await _update_robot_status(conn.robot_id, "idle")
            logger.info("[豹小秘 %s] 视频关闭: reason=%s ts=%s", conn.robot_id, reason, data.get("ts"))

            now_ts = time.time()
            in_cooldown = (now_ts - conn.last_video_closed_at) < 10.0
            conn.last_video_closed_at = now_ts

            from app.engine.flow_executor import get_active_flow, stop_flow
            active = get_active_flow(conn.robot_id)
            had_active_flow = bool(active and active.status in ("running", "paused"))
            if had_active_flow:
                await stop_flow(conn.robot_id)
                logger.info("[豹小秘 %s] evt_video_closed → 流程已停止 (视为 不用了)", conn.robot_id)

            in_browse = False
            ws_busy = False
            try:
                from app.engine.smart_engine import get_smart_engine
                engine = get_smart_engine(conn.robot_id)
                if engine:
                    in_browse = bool(engine.ws.browse.mode)
                    # 状态实际在 dialog.engine_state, 之前误写 engine.ws.state 抛 AttributeError
                    # 被外层吞掉, 导致 reset_browse 没执行 → 浏览模式残留
                    ws_busy = engine.ws.dialog.engine_state != "IDLE"
                    if had_active_flow or in_browse or ws_busy:
                        engine.ws.transition("IDLE")
                        engine.ws.reset_browse()
            except Exception as _e:
                logger.warning("[豹小秘 %s] evt_video_closed reset 失败: %s", conn.robot_id, _e)

            should_speak = (had_active_flow or in_browse or ws_busy) and not in_cooldown
            if should_speak:
                asyncio.create_task(conn.speak_serialized(
                    "好的,已为您关闭。想继续了解随时叫我。"
                ))
            else:
                logger.info(
                    "[豹小秘 %s] evt_video_closed 静默忽略 (flow=%s browse=%s ws_busy=%s cooldown=%s)",
                    conn.robot_id, had_active_flow, in_browse, ws_busy, in_cooldown,
                )

    elif msg_type == "evt_location":
        # 位置主动上报
        if conn.robot_id:
            await _update_robot_status(
                conn.robot_id, "idle",
                location=data.get("pointName", ""),
                point_id=data.get("pointId", ""),
            )
            if data.get("pointName"):
                try:
                    conn.last_location = data.get("pointName", "")
                except Exception:
                    pass

    elif msg_type == "evt_status":
        # 状态查询响应
        if conn.robot_id:
            await _update_robot_status(
                conn.robot_id, data.get("status", "idle"),
                battery=data.get("battery", 0),
                location=data.get("location", ""),
            )

    elif msg_type in ("evt_sequence_step", "evt_sequence_done"):
        # 组合动作回调 — 流程引擎通过 seq 匹配
        logger.info("[豹小秘 %s] %s: %s", conn.robot_id, msg_type, data)

    elif msg_type == "evt_speech_result":
        # 语音识别结果 — 需要转给意图引擎处理
        speech_text = data.get("text", "")
        if conn.robot_id:
            logger.info("[豹小秘 %s] 语音识别: \"%s\"", conn.robot_id, speech_text)
            await _update_robot_status(conn.robot_id, "idle")
            
            # 记录到执行日志
            db_log = await get_db()
            try:
                await db_log.execute(
                    """INSERT INTO execution_logs (log_type, source, target, action, detail, status, created_at)
                       VALUES ('robot_event', ?, 'speech', 'evt_speech_result', ?, 'ok', datetime('now','localtime'))""",
                    (conn.robot_id, speech_text),
                )
                await db_log.commit()
            finally:
                await db_log.close()
            
            # 使用 SmartEngine 统一状态机处理 (现场主入口, 不再走 conversation.handle_speech)
            from app.engine.smart_engine import create_smart_engine
            engine = create_smart_engine(conn.robot_id, conn)
            asyncio.create_task(engine.handle_speech(speech_text, data))

    elif msg_type == "evt_battery":
        if conn.robot_id:
            battery_level = data.get("battery", 100)
            charging = data.get("charging", False)
            if charging:
                try:
                    from app.engine.smart_engine import get_smart_engine
                    engine = get_smart_engine(conn.robot_id)
                    if engine:
                        asyncio.create_task(engine.handle_legacy_teaching_timeout(
                            str(data.get("teachingTurnId") or ""),
                            "charging",
                        ))
                except Exception:
                    pass
            status = "charging" if charging else None
            # 低电量告警 (<=15%)
            if battery_level <= 15 and not charging:
                from app.engine.webhook import send_alert
                asyncio.create_task(send_alert(
                    "robot_low_battery",
                    f"机器人 {conn.robot_name or conn.robot_id} 电量过低",
                    f"当前电量: {battery_level}%",
                ))
            db = await get_db()
            try:
                if status:
                    await db.execute(
                        "UPDATE robots SET battery=?, status=? WHERE id=?",
                        (data.get("battery", 0), status, conn.robot_id),
                    )
                else:
                    await db.execute(
                        "UPDATE robots SET battery=? WHERE id=?",
                        (data.get("battery", 0), conn.robot_id),
                    )
                await db.commit()
            finally:
                await db.close()

    elif msg_type == "evt_flow_interrupted":
        if conn.robot_id:
            await _update_robot_status(conn.robot_id, "idle")
            from app.engine.flow_executor import pause_flow
            await pause_flow(conn.robot_id)
            # 通知 SmartEngine 状态回退到 ASK_NEXT（等用户决定是否恢复）
            try:
                from app.engine.smart_engine import get_smart_engine
                engine = get_smart_engine(conn.robot_id)
                if engine:
                    engine.ws.transition("ASK_NEXT")
            except Exception:
                pass

    elif msg_type == "evt_face_register_success":
        if conn.robot_id:
            user_id = data.get("userId", "")
            name = (data.get("name") or "").strip()
            request_id = data.get("requestId", "")
            # APK 不发 userId 时用 name 当身份键, 跟 recognized/photo 对齐.
            if not user_id and name:
                user_id = name
            logger.info("[豹小秘 %s] 人脸注册成功: userId=%s name=%s requestId=%s",
                       conn.robot_id, user_id, name, request_id)
            if not user_id:
                logger.warning("[豹小秘 %s] 人脸注册成功事件 userId/name 都为空, 跳过 (避免脏数据)", conn.robot_id)
                return
            try:
                from app.engine.visitor_service import upsert_visitor, update_register_task, bind_visitor_to_robot
                # upsert visitor profile
                visitor = await upsert_visitor(user_id, name, source="face_register", robot_id=conn.robot_id)
                # update register task
                if request_id:
                    await update_register_task(request_id, "success",
                                                robot_user_id=user_id,
                                                visitor_id=visitor.get("id"))
                # bind visitor to robot
                if visitor.get("id"):
                    await bind_visitor_to_robot(conn.robot_id, visitor["id"],
                                                 source="face_register", confidence=1.0)
                # notify SmartEngine
                from app.engine.smart_engine import get_smart_engine
                engine = get_smart_engine(conn.robot_id)
                if engine:
                    await engine.handle_face_registered({"name": name, "success": True, "userId": user_id})
            except Exception as e:
                logger.error("[豹小秘 %s] 人脸注册成功处理异常: %s", conn.robot_id, e, exc_info=True)

    elif msg_type == "evt_face_register_failed":
        if conn.robot_id:
            request_id = data.get("requestId", "")
            error_code = data.get("errorCode", "unknown")
            error_msg = data.get("errorMessage", "")
            logger.warning("[豹小秘 %s] 人脸注册失败: requestId=%s error=%s %s",
                          conn.robot_id, request_id, error_code, error_msg)
            try:
                from app.engine.visitor_service import update_register_task
                if request_id:
                    await update_register_task(request_id, "failed",
                                                error_code=error_code,
                                                error_message=error_msg)
            except Exception as e:
                logger.error("[豹小秘 %s] 人脸注册失败处理异常: %s", conn.robot_id, e)

    elif msg_type == "evt_face_recognized":
        if conn.robot_id:
            user_id = data.get("userId", "")
            name = (data.get("name") or "").strip()
            confidence = data.get("confidence", 0.0)
            location = (data.get("location") or "").strip()
            location, location_source = _resolve_face_location_for_reception(conn, location, data)
            if location:
                try:
                    conn.last_location = location
                except Exception:
                    pass
            # APK 已改成不发 userId, 仅靠 name 标识. name 当唯一身份键使,
            # 下游 upsert / 绑定 / 欢迎语全自动. name 也空时才走陌生人 streak.
            if not user_id and name:
                user_id = name
            # SDK 会在已识别事件与空事件之间抖动；若短时间内刚识别过姓名，空事件沿用最近姓名。
            if not user_id:
                cached_face = _recent_known_face.get(conn.robot_id) or {}
                if cached_face and time.time() - cached_face.get("ts", 0) <= RECENT_KNOWN_FACE_TTL:
                    cached_name = (cached_face.get("name") or "").strip()
                    cached_user_id = cached_face.get("user_id") or cached_name
                    if cached_name:
                        user_id = cached_user_id
                        name = cached_name
                        logger.info("[豹小秘 %s] 空人脸事件沿用最近已识别身份: userId=%s name=%s", conn.robot_id, user_id, name)

            logger.info("[豹小秘 %s] 人脸识别: userId=%s name=%s conf=%.2f location=%s source=%s",
                       conn.robot_id, user_id, name, confidence, location or "-", location_source)
            # 空 userId 视为未识别/陌生人, 不入库不绑定: 否则会命中 robot_user_id='' 的脏行.
            if not user_id:
                # 陌生人: 不入库不绑定, 但按欢迎策略触发一次通用问候。
                # 旧逻辑直接 return, 导致"检测到未录入人脸"没有任何接待语。
                _stranger_streak[conn.robot_id] = _stranger_streak.get(conn.robot_id, 0) + 1
                streak = _stranger_streak[conn.robot_id]
                if streak >= 10 and streak % 10 == 0:
                    logger.warning(
                        "[豹小秘 %s] 人脸识别 userId 连续为空 %d 次 — 检查 face DB / 摄像头",
                        conn.robot_id, streak,
                    )
                    # 持久化到 execution_logs 让管理面板看到, detail 带原始 APK 上报 JSON
                    # 方便后台直接看到机器人当时发了什么(name/confidence/ts 等)。
                    # 注意: get_db 已在模块顶层 import (line 19), 这里**绝不能**再
                    # `from app.database import get_db`, 否则 Python 把整个外层
                    # 函数里的 get_db 当成 local var → 其它路径先用 get_db 时
                    # UnboundLocalError → TCP 连接被异常关掉, 机器人疯狂重连。
                    try:
                        import json as _json
                        _detail_obj = {
                            "streak": streak,
                            "raw": data,  # 原始 APK 上报 JSON (含 name/confidence/ts 等)
                        }
                        _detail_str = _json.dumps(_detail_obj, ensure_ascii=False)[:1500]
                        db = await get_db()
                        try:
                            await db.execute(
                                "INSERT INTO execution_logs (log_type, source, target, action, detail, status, created_at) "
                                "VALUES ('error', ?, ?, 'face_recognition_unknown_streak', ?, 'warning', datetime('now','localtime'))",
                                (conn.robot_id, "face_db", _detail_str),
                            )
                            await db.commit()
                        finally:
                            await db.close()
                    except Exception:
                        pass
                else:
                    logger.info("[豹小秘 %s] 人脸识别 userId 为空, 视为陌生人, 跳过绑定 (streak=%d)",
                                conn.robot_id, streak)

                try:
                    from app.engine.visitor_service import get_person_state, get_welcome_text
                    import time as _time

                    ps = get_person_state(conn.robot_id)
                    now = _time.time()

                    db_wc = await get_db()
                    try:
                        wc_rows = await db_wc.execute_fetchall(
                            "SELECT key, value FROM system_config WHERE key IN ('visitor_welcome_enabled','visitor_welcome_cooldown','visitor_welcome_mode')"
                        )
                        wc = {r["key"]: r["value"] for r in wc_rows}
                    finally:
                        await db_wc.close()

                    welcome_enabled = wc.get("visitor_welcome_enabled", "1") == "1"
                    welcome_mode = wc.get("visitor_welcome_mode", "recognized")

                    from app.engine.smart_engine import get_smart_engine, create_smart_engine
                    engine = get_smart_engine(conn.robot_id) or create_smart_engine(conn.robot_id, conn)

                    face_key = _reception_face_key(None, unknown=True)

                    # recognized 模式下也欢迎陌生人；any 模式保持兼容。
                    if not welcome_enabled:
                        logger.info("[豹小秘 %s] 陌生人欢迎跳过: welcome_disabled", conn.robot_id)
                    elif not _is_reception_location(location):
                        logger.info("[豹小秘 %s] 陌生人欢迎跳过: 非接待点 location=%s", conn.robot_id, location)
                    elif welcome_mode not in ("recognized", "any"):
                        logger.info("[豹小秘 %s] 陌生人欢迎跳过: mode=%s", conn.robot_id, welcome_mode)
                    else:
                        pause_reason = _reception_pause_reason(ps, engine=engine, data=data, now=now)
                        if pause_reason:
                            logger.info("[豹小秘 %s] 陌生人欢迎跳过: %s", conn.robot_id, pause_reason)
                            return
                        ok, reason = _should_reception_greet(ps, face_key, now)
                        if not ok:
                            logger.info("[豹小秘 %s] 陌生人欢迎跳过: %s", conn.robot_id, reason)
                        else:
                            engine.ws.visitor_detected(name="", face_id="")
                            welcome_text = await get_welcome_text(None)
                            logger.info("[豹小秘 %s] 陌生人欢迎语: reason=%s text=%r", conn.robot_id, reason, welcome_text)
                            asyncio.create_task(engine._speak(welcome_text))
                except Exception as e:
                    logger.error("[豹小秘 %s] 陌生人欢迎处理异常: %s", conn.robot_id, e, exc_info=True)
                return
            else:
                # 识别成功 → 重置 streak 计数
                _stranger_streak[conn.robot_id] = 0
            try:
                from app.engine.visitor_service import (
                    get_visitor_by_user_id, upsert_visitor,
                    bind_visitor_to_robot, get_person_state, get_welcome_text,
                )
                # find or create visitor
                visitor = await get_visitor_by_user_id(user_id)
                if not visitor:
                    visitor = await upsert_visitor(user_id, name, source="auto_recognized", robot_id=conn.robot_id)
                else:
                    # update last seen. 一次"到访"规则: 距上次 last_seen_at 超过 30 分钟才 +1;
                    # 否则仅刷新 last_seen_at, 避免同一会话内多次识别把 visit_count 灌水。
                    db_tmp = await get_db()
                    try:
                        await db_tmp.execute(
                            """UPDATE visitor_profiles SET
                               name = CASE WHEN (name IS NULL OR name='') AND ? != '' THEN ? ELSE name END,
                               display_name = CASE WHEN (display_name IS NULL OR display_name='') AND ? != '' THEN ? ELSE display_name END,
                               visit_count = visit_count + CASE
                                   WHEN last_seen_at IS NULL OR
                                        (strftime('%s','now','localtime') - strftime('%s', last_seen_at)) > 1800
                                   THEN 1 ELSE 0 END,
                               last_seen_at = datetime('now','localtime'),
                               last_robot_id = ?,
                               updated_at = datetime('now','localtime')
                               WHERE id=?""",
                            (name, name, name, name, conn.robot_id, visitor["id"]),
                        )
                        await db_tmp.commit()
                    finally:
                        await db_tmp.close()

                # bind to robot
                if visitor.get("id"):
                    # read TTL from config
                    db_cfg = await get_db()
                    try:
                        cfg_row = await db_cfg.execute_fetchall(
                            "SELECT value FROM system_config WHERE key='visitor_binding_ttl'"
                        )
                        ttl = int(cfg_row[0]["value"]) if cfg_row else 300
                    finally:
                        await db_cfg.close()
                    await bind_visitor_to_robot(conn.robot_id, visitor["id"],
                                                 source="face", confidence=confidence,
                                                 ttl_seconds=ttl)

                    # Check welcome - person_state based
                    import time as _time
                    ps = get_person_state(conn.robot_id)
                    db_wc = await get_db()
                    try:
                        wc_rows = await db_wc.execute_fetchall(
                            "SELECT key, value FROM system_config WHERE key IN ('visitor_welcome_enabled','visitor_welcome_cooldown','visitor_welcome_mode','visitor_lock_duration')"
                        )
                        wc = {r["key"]: r["value"] for r in wc_rows}
                    finally:
                        await db_wc.close()

                    welcome_enabled = wc.get("visitor_welcome_enabled", "1") == "1"
                    cooldown = int(wc.get("visitor_welcome_cooldown", "300"))
                    welcome_mode = wc.get("visitor_welcome_mode", "recognized")
                    lock_dur = int(wc.get("visitor_lock_duration", "60"))
                    now = _time.time()

                    # Lock current visitor
                    ps["locked_visitor_id"] = visitor["id"]
                    ps["lock_until"] = now + lock_dur

                    # 主动 create_smart_engine: 用户可能"先被识别后说话",
                    # 此时 engine 还没在 evt_speech_result 路径里被创建。
                    # get → 没有就 create, 保证欢迎语能播 + 世界模型能更新。
                    from app.engine.smart_engine import get_smart_engine, create_smart_engine
                    engine = get_smart_engine(conn.robot_id) or create_smart_engine(conn.robot_id, conn)

                    # 同步到世界模型: 让后续 LLM/answer_engine/tour_planner 能拿到 visitor.name
                    # 做指代消解和上下文匹配("我刚才...", "他记得我..." 这类). face_id 用 user_id
                    # (= APK 上报的 name 兜底), 保持跟数据库一致。
                    engine.ws.visitor_detected(name=name or "", face_id=user_id or "")
                    if name and not (visitor.get("display_name") or visitor.get("name")):
                        visitor = dict(visitor)
                        visitor["name"] = name
                        visitor["display_name"] = name
                    _recent_known_face[conn.robot_id] = {
                        "user_id": user_id or name,
                        "name": name or (visitor.get("display_name") or visitor.get("name") or ""),
                        "visitor_id": visitor.get("id"),
                        "ts": now,
                    }
                    logger.debug("[豹小秘 %s] 世界模型已记录: name=%s face_id=%s",
                                 conn.robot_id, name, user_id)

                    # 主动问候只允许在接待/前台/迎宾类点位触发；
                    # 用接待会话状态机防重复：识别到姓名时应该在 recognized/any 两种模式下都能主动问候；
                    # 同一人在场期间只问候一次，人离开或换人后再开启新会话。
                    face_key = _reception_face_key(visitor, user_id=user_id, name=name)

                    if not welcome_enabled:
                        logger.info("[豹小秘 %s] 欢迎语跳过: welcome_disabled visitor_id=%s", conn.robot_id, visitor.get("id"))
                    elif not _is_reception_location(location):
                        logger.info("[豹小秘 %s] 欢迎语跳过: 非接待点 location=%s visitor_id=%s name=%s",
                                    conn.robot_id, location, visitor.get("id"), visitor.get("name"))
                    elif welcome_mode not in ("recognized", "any"):
                        logger.info("[豹小秘 %s] 欢迎语跳过: mode=%s visitor_id=%s", conn.robot_id, welcome_mode, visitor.get("id"))
                    else:
                        pause_reason = _reception_pause_reason(ps, engine=engine, data=data, now=now)
                        if pause_reason:
                            logger.info("[豹小秘 %s] 欢迎语跳过: %s visitor_id=%s name=%s key=%s",
                                        conn.robot_id, pause_reason, visitor.get("id"), visitor.get("name"), face_key)
                            return
                        ok, reason = _should_reception_greet(ps, face_key, now)
                        if not ok:
                            logger.info("[豹小秘 %s] 欢迎语跳过: %s visitor_id=%s name=%s key=%s",
                                        conn.robot_id, reason, visitor.get("id"), visitor.get("name"), face_key)
                        else:
                            # APK 给了 name → 是它人脸库里已注册的老熟人, 直接走姓名欢迎。
                            force_returning = bool(name)
                            welcome_text = await get_welcome_text(visitor, force_returning=force_returning)
                            logger.info("[豹小秘 %s] 欢迎语: reason=%s visitor_id=%s name=%s force_returning=%s key=%s text=%r",
                                        conn.robot_id, reason, visitor.get("id"), visitor.get("name"),
                                        force_returning, face_key, welcome_text)
                            asyncio.create_task(engine._speak(welcome_text))

            except Exception as e:
                logger.error("[豹小秘 %s] 人脸识别处理异常: %s", conn.robot_id, e, exc_info=True)

    elif msg_type == "evt_face_photo":
        if conn.robot_id:
            user_id = data.get("userId", "")
            name = (data.get("name") or "").strip()
            photo_base64 = data.get("photoBase64", "")
            photo_type = data.get("photoType", "register")
            content_type = data.get("contentType", "image/jpeg")
            # APK 不发 userId 时用 name 当身份, 跟 evt_face_recognized 对齐.
            if not user_id and name:
                user_id = name
            logger.info("[豹小秘 %s] 人脸照片: userId=%s name=%s type=%s contentType=%s b64Len=%d",
                       conn.robot_id, user_id, name, photo_type, content_type, len(photo_base64))
            if not user_id or not photo_base64:
                logger.warning("[豹小秘 %s] 人脸照片字段不全, 跳过 (userId/name=%s b64Len=%d)",
                               conn.robot_id, user_id, len(photo_base64))
            else:
                try:
                    import base64, os, hashlib
                    ext = "jpg" if "jpeg" in content_type else "png"
                    face_dir = os.path.join("data", "faces")
                    os.makedirs(face_dir, exist_ok=True)
                    # 文件名优先用 user_id (含中文也能存), 但 file system 安全考虑做 fallback:
                    # 含路径分隔符 / 控制字符 → 用 md5 hash 避免越权写入。
                    safe_id = user_id
                    if any(c in user_id for c in ("/", "\\", "..", "\x00")) or len(user_id) > 60:
                        safe_id = hashlib.md5(user_id.encode("utf-8")).hexdigest()[:16]
                    filepath = os.path.join(face_dir, f"{safe_id}.{ext}")
                    with open(filepath, "wb") as f:
                        f.write(base64.b64decode(photo_base64))
                    local_url = f"/static/faces/{safe_id}.{ext}"
                    from app.engine.visitor_service import update_visitor_photo
                    await update_visitor_photo(user_id, local_url, name=name,
                                                robot_id=conn.robot_id)
                    logger.info("[豹小秘 %s] 人脸照片已保存: %s", conn.robot_id, filepath)
                except Exception as e:
                    logger.error("[豹小秘 %s] 人脸照片处理异常: %s", conn.robot_id, e, exc_info=True)

    elif msg_type == "evt_person_count":
        if conn.robot_id:
            person_count = data.get("personCount", 0)
            logger.info("[豹小秘 %s] 人数变化: %d", conn.robot_id, person_count)
            try:
                from app.engine.visitor_service import handle_person_count, get_welcome_text
                result = await handle_person_count(conn.robot_id, person_count)
                if result["action"] == "welcome":
                    welcome_text = await get_welcome_text(result.get("visitor"))
                    from app.engine.smart_engine import get_smart_engine
                    engine = get_smart_engine(conn.robot_id)
                    if engine:
                        asyncio.create_task(engine._speak(welcome_text))
                elif result["action"] == "clear":
                    _recent_known_face.pop(conn.robot_id, None)
                    # 人离开 → 清世界模型的访客信息, 让下个人来时不会被当成同一人
                    from app.engine.smart_engine import get_smart_engine
                    engine = get_smart_engine(conn.robot_id)
                    if engine:
                        engine.ws.visitor_left()
                        logger.info("[豹小秘 %s] 世界模型: 访客离开, 清空 name/face_id", conn.robot_id)
                # Update robot person_count in DB
                db_pc = await get_db()
                try:
                    await db_pc.execute(
                        "UPDATE robots SET person_count=? WHERE id=?",
                        (person_count, conn.robot_id),
                    )
                    await db_pc.commit()
                except Exception:
                    pass
                finally:
                    await db_pc.close()
            except Exception as e:
                logger.error("[豹小秘 %s] 人数变化处理异常: %s", conn.robot_id, e, exc_info=True)

    elif msg_type == "evt_error":
        logger.error("[豹小秘 %s] 异常: %s - %s",
                     conn.robot_id, data.get("code"), data.get("message"))
        from app.engine.webhook import send_alert
        asyncio.create_task(send_alert(
            "robot_error",
            f"机器人 {conn.robot_name or conn.robot_id} 异常",
            f"错误码: {data.get('code')} - {data.get('message')}",
        ))

    # 记录日志
    try:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO execution_logs
                   (log_type, source, action, detail, status, created_at)
                   VALUES ('robot_event', ?, ?, ?, 'ok', datetime('now','localtime'))""",
                (conn.robot_id or str(conn.addr), msg_type,
                 json.dumps(data, ensure_ascii=False)),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        logger.warning("事件日志记录失败: %s", e)


# ============================================================
# 辅助函数
# ============================================================
async def _trigger_area_bindngs(robot_id: str, point_id):
    """展区联动：机器人到达点位后自动触发绑定的设备开关和资源播放"""
    if not point_id:
        return
    try:
        from app.tcp.central_control import send_tcp_command
        db = await get_db()
        try:
            # 查绑定：设备 — equipments.cmd_on 直接存"开"指令(2024 重构后合并的列),
            # 老 schema 的 equipment_commands 表已不存在, JOIN 它会 raise no such table.
            equip_rows = await db.execute_fetchall(
                """SELECT e.id, e.cmd_on AS command_msg
                   FROM position_bindings pb
                   JOIN equipments e ON e.id = pb.bind_target_id
                   WHERE pb.position_id=? AND pb.bind_type='equipment'
                     AND e.cmd_on IS NOT NULL AND e.cmd_on != ''""",
                (point_id,),
            )
            for row in equip_rows:
                cmd = row["command_msg"]
                if cmd:
                    await send_tcp_command(cmd)
                    logger.info("[联动] 点位%s → 设备%s 开", point_id, row["id"])

            # 查绑定：资源播放
            res_rows = await db.execute_fetchall(
                """SELECT pb.bind_target_id as exhibit_id,
                          e.exhibition_area_terminal_id as ctrl_id
                   FROM position_bindings pb
                   JOIN exhibits e ON e.id = pb.bind_target_id
                   WHERE pb.position_id=? AND pb.bind_type='exhibit'""",
                (point_id,),
            )
            if res_rows:
                # 获取当前专场
                sp = await db.execute_fetchall(
                    "SELECT value FROM system_config WHERE key='current_special_id'"
                )
                sp_id = int(sp[0]["value"]) if sp and sp[0]["value"] else 0
                if sp_id:
                    for row in res_rows:
                        # 播放该展项在当前专场下排序第一的资源
                        first_res = await db.execute_fetchall(
                            """SELECT resource_id FROM special_exhibit_resources
                               WHERE special_id=? AND exhibit_id=?
                               ORDER BY sort_order LIMIT 1""",
                            (sp_id, row["exhibit_id"]),
                        )
                        if first_res:
                            cmd = f"2_{sp_id}_{row['ctrl_id']}_{first_res[0]['resource_id']}"
                            await send_tcp_command(cmd)
                            logger.info("[联动] 点位%s → 资源播放 %s", point_id, cmd)
        finally:
            await db.close()
    except Exception as e:
        logger.error("[联动] 点位%s 触发失败: %s", point_id, e)


async def _update_robot_status(robot_id: str, status: str, **kwargs):
    """更新机器人状态到数据库"""
    db = await get_db()
    try:
        # upsert
        existing = await db.execute_fetchall(
            "SELECT id FROM robots WHERE id=?", (robot_id,)
        )
        if existing:
            updates = ["status=?", "updated_at=datetime('now','localtime')"]
            params = [status]
            for k, v in kwargs.items():
                updates.append(f"{k}=?")
                params.append(v)
            if status != "offline":
                updates.append("connected_at=COALESCE(connected_at, datetime('now','localtime'))")
            params.append(robot_id)
            await db.execute(
                f"UPDATE robots SET {', '.join(updates)} WHERE id=?", params
            )
        else:
            cols = ["id", "status"]
            vals = [robot_id, status]
            for k, v in kwargs.items():
                cols.append(k)
                vals.append(v)
            placeholders = ", ".join(["?"] * len(cols))
            await db.execute(
                f"INSERT INTO robots ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
        await db.commit()
    finally:
        await db.close()


# ============================================================
# WebSocket 连接处理（机器人专用）
# ============================================================
async def handle_robot_websocket(websocket):
    """处理机器人 WebSocket 连接（由 FastAPI 路由调用）"""
    from starlette.websockets import WebSocketDisconnect

    conn = RobotConnection(websocket=websocket)
    logger.info("[WS] 机器人连接: %s", conn.addr)

    try:
        while True:
            raw = await websocket.receive_text()
            raw = raw.strip()
            if raw:
                await _process_message(conn, raw)
    except WebSocketDisconnect:
        logger.info("[WS] 机器人断开: %s", conn.addr)
    except Exception as e:
        logger.error("[WS] 机器人异常 %s: %s", conn.addr, e)
    finally:
        # identity 检查见 TCP finally 同名注释
        if conn.robot_id and _robot_connections.get(conn.robot_id) is conn:
            del _robot_connections[conn.robot_id]
            await _update_robot_status(conn.robot_id, "offline")
            logger.info("[豹小秘] %s 已离线 (WS)", conn.robot_id)


# ============================================================
# 启动/停止
# ============================================================
async def start_tcp_server():
    """启动 TCP 服务端"""
    global _server
    config = get_config().get("server", {})
    port = config.get("tcp_port", 19888)
    host = config.get("host", "0.0.0.0")

    _server = await asyncio.start_server(_handle_connection, host, port)
    logger.info("[TCP] 服务端启动: %s:%d", host, port)

    # 启动时把所有残留的非 offline 机器人重置为 offline,
    # 避免上次进程退出时遗留的 idle 行被误算作在线 (心跳检测只看活连接, 不会回收孤行)
    try:
        db = await get_db()
        try:
            cur = await db.execute(
                "UPDATE robots SET status='offline', updated_at=datetime('now','localtime') "
                "WHERE status != 'offline'"
            )
            await db.commit()
            reset_n = getattr(cur, "rowcount", 0) or 0
            if reset_n:
                logger.info("[TCP] 启动重置: %d 个残留机器人标记为 offline", reset_n)
        finally:
            await db.close()
    except Exception as e:
        logger.warning("[TCP] 启动重置 robots 失败: %s", e)

    # 启动心跳超时检测
    global _heartbeat_task
    _heartbeat_task = asyncio.create_task(_heartbeat_check_loop())
    logger.info("心跳超时检测已启动 (间隔/超时由 system_config 控制)")

    # 预热 robot_config 缓存, 让机器人首次 evt_get_config 的 ack 不用跑冷 DB 读
    try:
        await _get_robot_config_data()
        logger.info("[TCP] robot_config 缓存已预热")
    except Exception as e:
        logger.debug("[TCP] config 预热跳过: %s", e)


_heartbeat_task = None


async def _read_heartbeat_threshold() -> tuple[int, int]:
    """从 system_config 读心跳间隔 + 检测倍数。
    返回 (interval_seconds, check_multiplier)。默认 30s × 3 = 90s 阈值。
    运维可改 system_config.heartbeat_interval / heartbeat_check_multiplier 调整灵敏度。"""
    try:
        # get_db 已在模块顶层 import (line 19), 不再重复
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT key, value FROM system_config WHERE key IN "
                "('heartbeat_interval', 'heartbeat_check_multiplier')"
            )
            kv = {r["key"]: r["value"] for r in rows}
            interval = max(10, int(kv.get("heartbeat_interval") or 30))
            mult = max(2, int(kv.get("heartbeat_check_multiplier") or 3))
            return interval, mult
        finally:
            await db.close()
    except Exception:
        return 30, 3


async def _heartbeat_check_loop():
    """定时检查机器人心跳，超时 3 次心跳间隔未收到则标记离线"""
    await asyncio.sleep(30)  # 启动后等 30 秒
    while True:
        try:
            now = time.time()
            # 阈值 = heartbeat_interval × heartbeat_check_multiplier (默认 3×)
            interval, multiplier = await _read_heartbeat_threshold()
            timeout_threshold = max(60, interval * multiplier)
            for robot_id, conn in list(_robot_connections.items()):
                elapsed = now - conn.last_heartbeat
                if elapsed > timeout_threshold:
                    logger.warning("[豹小秘] %s 心跳超时 %.0f秒 (阈值 %ds), 标记离线",
                                   robot_id, elapsed, timeout_threshold)
                    del _robot_connections[robot_id]
                    await _update_robot_status(robot_id, "offline")
                    await conn.close()
                    # Webhook 告警
                    from app.engine.webhook import send_alert
                    asyncio.create_task(send_alert(
                        "robot_offline",
                        f"机器人 {conn.robot_name or robot_id} 已离线",
                        f"心跳超时 {elapsed:.0f} 秒",
                    ))
        except Exception as e:
            logger.error("心跳检查异常: %s", e)
        await asyncio.sleep(30)  # 每 30 秒检查一次


async def stop_tcp_server():
    """停止 TCP 服务端"""
    global _server, _heartbeat_task
    if _heartbeat_task:
        _heartbeat_task.cancel()
        _heartbeat_task = None
    if _server:
        _server.close()
        await _server.wait_closed()
        logger.info("[TCP] 服务端已停止")

    # 关闭所有机器人连接
    for robot_id, conn in list(_robot_connections.items()):
        await conn.close()
    _robot_connections.clear()
