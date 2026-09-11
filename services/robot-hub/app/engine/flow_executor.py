"""
流程执行引擎

负责执行参观流程脚本（tour_scripts），按步骤串行推进。
支持：
- 回调等待（evt_arrived/evt_tts_done/evt_show_done/evt_speech_result）
- 固定延时
- 唤醒词打断 → 暂停/恢复/放弃
- 执行日志记录
"""

import asyncio
import json
import time
import logging

from app.database import get_db
from app.tcp.server import get_robot, get_all_robots, RobotConnection
from app.tcp.central_control import send_tcp_command, send_commands_sequence

logger = logging.getLogger("hub.engine.flow_executor")


class FlowExecution:
    """单次流程执行实例"""

    def __init__(self, script_id: int, robot_id: str):
        self.script_id = script_id
        self.robot_id = robot_id
        self.script_name = ""
        self.steps = []
        self.current_step_index = 0
        self.status = "pending"  # pending/running/paused/completed/cancelled/error
        self.started_at = time.time()
        self.paused_at = None
        self._cancel_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # 默认不暂停

    @property
    def current_step(self):
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def pause(self):
        self.status = "paused"
        self.paused_at = time.time()
        self._pause_event.clear()
        logger.info("[流程] %s 已暂停 (步骤 %d)", self.script_name, self.current_step_index + 1)

    def resume(self):
        self.status = "running"
        self.paused_at = None
        self._pause_event.set()
        logger.info("[流程] %s 已恢复", self.script_name)

    def cancel(self):
        self.status = "cancelled"
        self._cancel_event.set()
        self._pause_event.set()  # 解除暂停等待
        logger.info("[流程] %s 已取消", self.script_name)


# 全局活跃流程表：robot_id -> FlowExecution
_active_flows: dict = {}


def get_active_flow(robot_id: str) -> FlowExecution:
    return _active_flows.get(robot_id)


def get_all_active_flows() -> dict:
    return dict(_active_flows)


async def start_flow(script_id: int, robot_id: str) -> dict:
    """
    启动流程执行

    Returns: {"status": "ok/error", "message": "..."}
    """
    # 检查是否有正在执行的流程
    existing = _active_flows.get(robot_id)
    if existing and existing.status == "running":
        return {
            "status": "error",
            "message": "机器人正在执行流程: " + existing.script_name + "，请先停止",
        }

    # 加载脚本和步骤
    db = await get_db()
    try:
        script = await db.execute_fetchall(
            "SELECT id, name, trigger_word FROM tour_scripts WHERE id=?",
            (script_id,),
        )
        if not script:
            return {"status": "error", "message": "流程不存在"}

        steps = await db.execute_fetchall(
            """SELECT id, step_order, step_type, config_json,
                      wait_type, wait_timeout, description
               FROM tour_script_steps WHERE script_id=?
               ORDER BY step_order""",
            (script_id,),
        )
        if not steps:
            return {"status": "error", "message": "流程没有步骤"}
    finally:
        await db.close()

    # 检查机器人在线 — 仅当流程含豹小秘类步骤时强制要求豹小秘 TCP 连接;
    # 宇树触发的流程通常只用 unitree_* + 中控类步骤(delay/equipment/group_command/
    # switch_special/end/custom_request), 这些不需要豹小秘 TCP 连接。
    robot = get_robot(robot_id)
    is_unitree = bool(robot_id and (robot_id.startswith("unitree") or "g1" in robot_id.lower()
                                    or "h1" in robot_id.lower()))
    if not robot and not is_unitree:
        # 豹小秘未上线 + 不是宇树场景 — 真不能跑
        return {"status": "error", "message": "机器人不在线"}
    # 宇树场景: 检查每步类型, 含豹小秘专属步骤(navigate/tts/show/listen/digital_human/pad_active)
    # 又没豹小秘在线时, 提前拒绝并提示
    if is_unitree and not robot:
        _BAOXIAOMI_REQUIRED = {"navigate", "tts", "show", "listen", "digital_human", "pad_active"}
        bad_steps = []
        for s in steps:
            # sqlite3.Row 没有 .get(), 用方括号或 dict() 转换
            stype = (s["step_type"] if "step_type" in s.keys() else "") or ""
            if stype.strip() in _BAOXIAOMI_REQUIRED:
                bad_steps.append(stype.strip())
        if bad_steps:
            return {
                "status": "error",
                "message": f"流程含豹小秘专属步骤 {sorted(set(bad_steps))} 但豹小秘未在线",
            }

    # 创建执行实例
    execution = FlowExecution(script_id, robot_id)
    execution.script_name = script[0]["name"]
    execution.steps = [dict(s) for s in steps]
    execution.status = "running"
    _active_flows[robot_id] = execution

    logger.info("[流程] 启动: %s (%d个步骤) → 机器人 %s",
                execution.script_name, len(execution.steps), robot_id)

    # 记录日志
    await _log_flow("flow_start", script_id, robot_id, execution.script_name)

    # 异步执行（不阻塞调用方）
    asyncio.create_task(_execute_flow(execution))

    return {"status": "ok", "message": "流程已启动: " + execution.script_name}


async def stop_flow(robot_id: str) -> dict:
    """停止流程"""
    execution = _active_flows.get(robot_id)
    if not execution:
        return {"status": "error", "message": "没有正在执行的流程"}

    execution.cancel()
    return {"status": "ok", "message": "流程已停止"}


async def pause_flow(robot_id: str) -> dict:
    """暂停流程"""
    execution = _active_flows.get(robot_id)
    if not execution or execution.status != "running":
        return {"status": "error", "message": "没有正在执行的流程"}

    execution.pause()
    return {"status": "ok", "message": "流程已暂停"}


async def resume_flow(robot_id: str) -> dict:
    """恢复流程"""
    execution = _active_flows.get(robot_id)
    if not execution or execution.status != "paused":
        return {"status": "error", "message": "没有暂停的流程"}

    execution.resume()
    return {"status": "ok", "message": "流程已恢复"}


# ============================================================
# 核心执行循环
# ============================================================
async def _execute_flow(execution: FlowExecution):
    """执行流程的所有步骤"""
    robot = get_robot(execution.robot_id)

    try:
        while execution.current_step_index < len(execution.steps):
            # 检查取消
            if execution._cancel_event.is_set():
                break

            # 等待暂停恢复
            await execution._pause_event.wait()
            if execution._cancel_event.is_set():
                break

            step = execution.current_step
            if not step:
                break

            step_type = step["step_type"]
            config = _parse_config(step.get("config_json", "{}"))
            # 统一模型: 每步只有一个"步骤时长(秒)", 从步骤开始计时.
            # 兼容旧流程的 config.seconds (delay 步骤旧字段).
            wait_timeout = int(
                step.get("wait_timeout")
                or config.get("seconds")
                or 30
            )

            # 步骤类型前缀决定机器人来源 — unitree_* 是宇树, 其余走豹小秘 (含 navigate/tts/show/listen 等).
            # 现场排查日志时一眼能看出"哪个机器人在执行哪一步".
            _robot_badge = "宇树" if step_type.startswith("unitree_") else "豹小秘"
            logger.info("[流程·%s] %s 执行步骤 %d/%d: %s (%s)",
                        _robot_badge,
                        execution.script_name,
                        execution.current_step_index + 1,
                        len(execution.steps),
                        step_type,
                        step.get("description", ""))

            step_start = time.time()
            success = False

            try:
                robot = get_robot(execution.robot_id)
                # 以下步骤不依赖老机器人 (豹小秘mini WebSocket) 在线:
                # - 通用: delay/end/switch_special/group_command/equipment/custom_request
                # - 宇树机器人: unitree_* 走独立 HTTP 网关, 与豹小秘 WebSocket 无关
                _no_robot_required = (
                    # 中控类 / 配置类 / 宇树专属步骤不依赖豹小秘 TCP 连接
                    step_type in ("delay", "end", "switch_special",
                                   "group_command", "equipment", "custom_request",
                                   "resource_play", "play_resource")
                    or step_type.startswith("unitree_")
                )
                if not robot and not _no_robot_required:
                    logger.error("[流程] 机器人 %s 已离线", execution.robot_id)
                    execution.status = "error"
                    break

                # 执行步骤
                if step_type == "navigate":
                    success = await _step_navigate(robot, config, wait_timeout)
                elif step_type == "tts":
                    # 发送 TTS 并等到 evt_tts_done 才推进, 避免下一句步骤打断这句长播报
                    tts_text = config.get("text", "")
                    if tts_text and robot:
                        try:
                            await robot.speak_serialized(tts_text)
                            logger.info("[流程] TTS已完成: %s", tts_text[:30])
                        except Exception as e:
                            logger.warning("[流程] TTS失败: %s", e)
                    success = True
                elif step_type == "show":
                    success = await _step_show(robot, config, wait_timeout)
                elif step_type in ("play_resource", "resource_play"):
                    success = await _step_play_resource(config, robot)
                elif step_type == "switch_special":
                    success = await _step_switch_special(config)
                elif step_type == "group_command":
                    success = await _step_group_command(config)
                elif step_type == "equipment":
                    success = await _step_equipment(config)
                elif step_type == "delay":
                    # 纯延时步骤: 整个步骤时长由 wait_timeout 控制, 此处不做事,
                    # 由下方统一的"补齐到 wait_timeout"逻辑产生延迟.
                    success = True
                elif step_type == "listen":
                    success = await _step_listen(robot, config, wait_timeout)
                elif step_type == "custom_request":
                    success = await _step_custom_request(config)
                elif step_type == "digital_human":
                    success = await _step_digital_human(config, robot)
                elif step_type == "pad_active":
                    success = await _step_pad_active(config)
                # ====== 宇树机器人 (HTTP 网关) ======
                elif step_type == "unitree_tts":
                    success = await _step_unitree_tts(config, wait_timeout)
                elif step_type == "unitree_tts_stop":
                    success = await _step_unitree_tts_stop(config)
                elif step_type == "unitree_volume":
                    success = await _step_unitree_volume(config)
                elif step_type == "unitree_motion":
                    success = await _step_unitree_motion(config, wait_timeout)
                elif step_type == "unitree_dance":
                    success = await _step_unitree_dance(config, wait_timeout)
                elif step_type == "unitree_dance_stop":
                    success = await _step_unitree_dance_stop(config)
                elif step_type == "unitree_map_apply":
                    success = await _step_unitree_map_apply(config)
                elif step_type == "unitree_nav":
                    success = await _step_unitree_nav(config, execution, wait_timeout)
                elif step_type == "unitree_nav_control":
                    success = await _step_unitree_nav_control(config, execution)
                elif step_type == "unitree_asr":
                    success = await _step_unitree_asr(config, execution)
                elif step_type == "unitree_face":
                    success = await _step_unitree_face(config)
                elif step_type == "end":
                    success = True
                else:
                    logger.warning("[流程] 未知步骤类型: %s", step_type)
                    success = True

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[流程] 步骤执行异常: %s", e, exc_info=True)

            # === 分类延时策略 ===
            # - navigate / listen: 自身已按 wait_timeout 等回调/收音，不再补时
            # - unitree_tts/motion/dance/nav: 自己 await webhook, 同样不补; 其它 unitree_*
            #   是同步 HTTP 结束即可, 补齐到 wait_timeout 保持节拍
            # - 其他类型（show/play_resource/delay/switch_special/...）: 补齐到 wait_timeout，
            #   让 UI 上的"步骤时长(秒)"对所有类型都生效
            _NO_PAD_TYPES = {
                "navigate", "listen",
                "unitree_tts", "unitree_motion", "unitree_dance", "unitree_nav",
            }
            if success and step_type not in _NO_PAD_TYPES:
                elapsed = time.time() - step_start
                remaining = wait_timeout - elapsed
                if remaining > 0:
                    logger.info("[流程] %s 补齐延时 %.1fs (时长=%ds)",
                                step_type, remaining, wait_timeout)
                    await asyncio.sleep(remaining)

            duration_ms = int((time.time() - step_start) * 1000)
            await _log_flow(
                "flow_step",
                execution.script_id,
                execution.robot_id,
                "%s step %d: %s" % (execution.script_name, execution.current_step_index + 1, step_type),
                "ok" if success else "error",
                duration_ms,
            )

            # 关键步骤失败时终止流程，避免无意义推进
            if not success and step_type in ("navigate", "listen"):
                logger.warning("[流程] %s 步骤 %d (%s) 失败，终止流程",
                               execution.script_name, execution.current_step_index + 1, step_type)
                execution.status = "error"
                break

            execution.current_step_index += 1

        # 流程结束
        if execution.status == "running":
            execution.status = "completed"

    except Exception as e:
        logger.error("[流程] 执行异常: %s", e, exc_info=True)
        execution.status = "error"

    finally:
        _active_flows.pop(execution.robot_id, None)
        await _log_flow(
            "flow_end",
            execution.script_id,
            execution.robot_id,
            "%s: %s" % (execution.script_name, execution.status),
        )
        logger.info("[流程] %s 结束: %s", execution.script_name, execution.status)


# ============================================================
# 步骤执行器
# ============================================================
async def _step_navigate(robot: RobotConnection, config: dict, timeout: float) -> bool:
    """导航步骤"""
    point_id = config.get("pointId", "") or config.get("position", "")
    if not point_id:
        logger.error("[流程] 导航失败: pointId为空! config=%s", config)
        return False
    # pointName 必须用 nav_positions 的原始点位名（机器人地图上的名字）
    # 不能用前端拼接的展项名，机器人只认地图里的点位名
    point_name = ""
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall("SELECT name FROM nav_positions WHERE position_id=?", (point_id,))
            if rows:
                point_name = rows[0]["name"]
        finally:
            await db.close()
    except Exception as e:
        logger.warning("[流程] 查询点位名失败: %s", e)
    if not point_name:
        point_name = config.get("pointName", "")
        # 安全检查：如果pointName包含括号拼接(如"大岛台正面 (岛台1)")，取括号内的纯点位名
        import re
        m = re.search(r'\(([^)]+)\)$', point_name)
        if m:
            point_name = m.group(1)
            logger.info("[流程] pointName从拼接名提取纯点位名: %s", point_name)
    logger.info("[流程] 导航: pointId=%s pointName=%s", point_id[:16], point_name)
    result = await robot.send_cmd("cmd_navigate", {
        "pointId": point_id,
        "pointName": point_name,
        "command": config.get("command", ""),
    }, timeout=timeout)
    if result is None:
        return False
    # 检查回调类型：evt_nav_failed = 失败
    if result.get("type") == "evt_nav_failed":
        reason = result.get("data", {}).get("reason", "未知")
        # APK 反语义: 机器人已经在目的地点位附近时也回 evt_nav_failed,
        # 实际上是"无需移动"的成功态, 不应中止流程。
        _already_arrived_markers = ("已经在目的地", "已在目的地", "目的地范围内", "无需导航")
        if any(m in reason for m in _already_arrived_markers):
            logger.info("[流程] 导航无需移动: pointId=%s pointName=%s reason=%s (视为成功)",
                        point_id[:16], point_name, reason)
            return True
        logger.error("[流程] 导航失败: pointId=%s pointName=%s reason=%s", point_id[:16], point_name, reason)
        return False
    return True


async def _step_tts(robot: RobotConnection, config: dict, timeout: float) -> bool:
    """TTS播报步骤 — 走 speak_serialized 等到 evt_tts_done 再返回."""
    text = config.get("text", "")
    if not text:
        return True
    return await robot.speak_serialized(text, priority=config.get("priority", "normal"))


async def _step_show(robot: RobotConnection, config: dict, timeout: float) -> bool:
    """屏幕展示步骤

    支持 action:
      - list: 列表展示 (listType: terminal/special/resource)
      - card: 信息卡片
      - text: 纯文字
      - clear: 清空屏幕
    """
    show_data = {
        "action": config.get("action", "text"),
    }
    action = show_data["action"]

    if action == "list":
        show_data["listType"] = config.get("listType", "terminal")
        show_data["title"] = config.get("title", "")
        show_data["items"] = config.get("items", [])
    elif action == "card":
        show_data["title"] = config.get("title", "")
        show_data["items"] = config.get("items", [])
    elif action == "text":
        show_data["text"] = config.get("text", "")
    # clear 不需要额外字段

    # fire-and-forget: 展示类指令机器人不回调
    ok = await robot.send({"type": "cmd_show", "data": show_data})
    return ok


async def _step_play_resource(config: dict, robot: RobotConnection = None) -> bool:
    """资源播放步骤 — 播放资源并自动TTS播报讲解词"""
    special_id = config.get("special", "")
    exhibit_id = config.get("exhibit", "") or config.get("exhibitId", "")
    resource_id = config.get("resource", "") or config.get("resourceId", "")

    if not special_id or not exhibit_id:
        logger.warning("[流程] 资源播放步骤缺少参数: special=%s exhibit=%s resource=%s",
                       special_id, exhibit_id, resource_id)
        return False

    # 未指定资源时，播放该展项下第一个资源
    if not resource_id:
        db_tmp = await get_db()
        try:
            first = await db_tmp.execute_fetchall(
                """SELECT resource_id FROM special_exhibit_resources
                   WHERE special_id=? AND exhibit_id=?
                   ORDER BY sort_order, resource_id LIMIT 1""",
                (int(special_id), int(exhibit_id)),
            )
            if first:
                resource_id = str(first[0]["resource_id"])
                logger.info("[流程] 资源未指定，自动选择第一个: %s", resource_id)
            else:
                logger.warning("[流程] 展项 %s 下无资源", exhibit_id)
                return False
        finally:
            await db_tmp.close()

    # 获取展项中控ID
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT exhibition_area_terminal_id FROM exhibits WHERE id=?",
            (int(exhibit_id),),
        )
        if not row:
            return False
        ctrl_id = row[0]["exhibition_area_terminal_id"]
    finally:
        await db.close()

    # 检查并切换专场
    db = await get_db()
    try:
        current = await db.execute_fetchall(
            "SELECT value FROM system_config WHERE key='current_special_id'"
        )
        current_id = current[0]["value"] if current else "0"
    finally:
        await db.close()

    switched = False
    commands = []
    if str(current_id) != str(special_id):
        commands.append("2_update_%s" % special_id)
        switched = True
    commands.append("2_%s_%s_%s" % (special_id, ctrl_id, resource_id))

    results = await send_commands_sequence(commands)
    tcp_ok = all(r.get("status") == "ok" for r in results)

    # 切专场后同步数据库 + 机器人播报提醒
    if switched and tcp_ok:
        db_sync = await get_db()
        try:
            await db_sync.execute("UPDATE specials SET is_current=0")
            await db_sync.execute("UPDATE specials SET is_current=1 WHERE id=?", (int(special_id),))
            await db_sync.execute(
                "UPDATE system_config SET value=? WHERE key='current_special_id'",
                (str(special_id),),
            )
            await db_sync.commit()
            logger.info("[流程] 专场已切换并同步数据库: %s", special_id)
        except Exception as e:
            logger.warning("[流程] 专场切换数据库同步失败: %s", e)
        finally:
            await db_sync.close()

        # 机器人播报专场切换提醒
        if robot:
            try:
                db_name = await get_db()
                try:
                    name_row = await db_name.execute_fetchall(
                        "SELECT name FROM specials WHERE id=?",
                        (int(special_id),),
                    )
                    special_name = name_row[0]["name"] if name_row else str(special_id)
                finally:
                    await db_name.close()
                await robot.speak_serialized(
                    "已为您切换到「%s」展览" % special_name,
                    priority="high",
                )
                logger.info("[流程] 专场切换播报: %s", special_name)
            except Exception as e:
                logger.warning("[流程] 专场切换播报失败: %s", e)

    # 播放资源后, 查讲解词. narration_target 决定由谁朗读:
    # - "screen"        / "" / 缺省: 仅中控屏播放, 不发讲解词到机器人
    # - "baoxiaomi": 中控屏 + 豹小秘 TTS (跟旧行为一致)
    # - "digital_human": 中控屏 + 数字人 TTS (cmd_digital_human action=speak)
    # - "unitree": 中控屏 + 宇树 TTS (走 unitree_client.tts_play)
    if tcp_ok:
        narration_target = (config.get("narration_target") or "").strip().lower()
        if not narration_target:
            # 兼容旧流程: 没配 target 时, 有机器人在线就走豹小秘 TTS (旧默认), 否则只屏播
            narration_target = "baoxiaomi" if robot else "screen"

        narration = ""
        if narration_target != "screen":
            db_narr = await get_db()
            try:
                narr_row = await db_narr.execute_fetchall(
                    "SELECT narration_text FROM resources WHERE id=? AND narration_text IS NOT NULL AND narration_text != ''",
                    (int(resource_id),),
                )
                if narr_row and narr_row[0]["narration_text"]:
                    narration = narr_row[0]["narration_text"]
            finally:
                await db_narr.close()

        if narration:
            logger.info("[流程] 资源 %s 讲解词 (%d字) 走 %s", resource_id, len(narration), narration_target)
            try:
                if narration_target == "baoxiaomi":
                    if robot:
                        await robot.speak_serialized(narration)
                    else:
                        logger.warning("[流程] 讲解词目标=baoxiaomi 但机器人不在线, 跳过")
                elif narration_target == "digital_human":
                    if robot:
                        await robot.send_cmd("cmd_digital_human", {
                            "action": "speak", "text": narration,
                        }, timeout=5)
                    else:
                        logger.warning("[流程] 讲解词目标=digital_human 但机器人不在线 (cmd_digital_human 走豹小秘通道), 跳过")
                elif narration_target == "unitree":
                    from app.engine.unitree_client import UnitreeClient
                    from app.engine.unitree_voice import _split_for_tts
                    client = await UnitreeClient.from_config(timeout=15)
                    if client:
                        # 宇树网关单段 TTS 上限 200 字, 长讲解词必须切片串行播
                        chunks = _split_for_tts(narration, limit=180)
                        for i, chunk in enumerate(chunks):
                            try:
                                await client.tts_play(chunk)
                            except Exception as e:
                                logger.warning("[流程] 宇树 TTS 第 %d/%d 段失败: %s",
                                               i + 1, len(chunks), e)
                                break
                            # 等播完再发下一段 — 估时 (字数 * 0.3s + 1s buffer, 上限 8s)
                            await asyncio.sleep(min(8.0, max(1.0, len(chunk) * 0.3)))
                    else:
                        logger.warning("[流程] 讲解词目标=unitree 但宇树网关未启用, 跳过")
                else:
                    logger.warning("[流程] 未知 narration_target=%r, 跳过讲解 TTS", narration_target)
            except Exception as e:
                logger.warning("[流程] 讲解词 TTS (target=%s) 失败: %s", narration_target, e)

    return tcp_ok


async def _step_switch_special(config: dict) -> bool:
    """切换专场步骤"""
    special_id = config.get("special", "")
    if not special_id:
        return False
    result = await send_tcp_command("2_update_%s" % special_id)

    # 更新数据库
    db = await get_db()
    try:
        await db.execute("UPDATE specials SET is_current=0")
        await db.execute("UPDATE specials SET is_current=1 WHERE id=?", (int(special_id),))
        await db.execute(
            "UPDATE system_config SET value=? WHERE key='current_special_id'",
            (str(special_id),),
        )
        await db.commit()
    finally:
        await db.close()

    return result.get("status") == "ok"


async def _step_group_command(config: dict) -> bool:
    """组策略步骤"""
    group_name = config.get("group", "") or config.get("commandName", "")
    action = config.get("action", "on")

    if not group_name:
        logger.warning("[流程] 组策略步骤缺少参数: config=%s", config)
        return False

    db = await get_db()
    try:
        field = "cmd_on" if action == "on" else "cmd_off"
        # group_name 可能是数字ID或名称字符串，都兼容
        row = await db.execute_fetchall(
            "SELECT %s as cmd, name FROM group_commands WHERE id=? OR name=?" % field,
            (group_name, group_name),
        )
        if not row or not row[0]["cmd"]:
            logger.warning("[流程] 组策略未找到: %s", group_name)
            return False
        cmd = row[0]["cmd"]
        logger.info("[流程] 组策略执行: %s → %s", row[0]["name"], cmd)
    finally:
        await db.close()

    result = await send_tcp_command(cmd)
    return result.get("status") == "ok"


async def _step_equipment(config: dict) -> bool:
    """设备控制步骤"""
    equip_id = config.get("equipment", "")
    action = config.get("action", "on")

    db = await get_db()
    try:
        field = "cmd_on" if action == "on" else "cmd_off"
        row = await db.execute_fetchall(
            "SELECT %s as cmd FROM equipments WHERE id=?" % field,
            (int(equip_id),),
        )
        if not row or not row[0]["cmd"]:
            return False
        cmd = row[0]["cmd"]
    finally:
        await db.close()

    result = await send_tcp_command(cmd)
    return result.get("status") == "ok"


async def _step_custom_request(config: dict) -> bool:
    """自定义请求步骤 — 支持 TCP/UDP/HTTP"""
    import socket
    protocol = config.get("protocol", "tcp")
    host = config.get("host", "")
    port = config.get("port", "")
    body = config.get("body", "")
    url_path = config.get("url", "")
    timeout_sec = int(config.get("timeout", 10))

    if not host:
        logger.warning("[流程] 自定义请求缺少目标地址")
        return False

    try:
        if protocol == "tcp":
            # TCP 短连接发送
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port)),
                timeout=timeout_sec,
            )
            writer.write(body.encode("utf-8"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            logger.info("[流程] TCP → %s:%s | %s", host, port, body)
            return True

        elif protocol == "udp":
            # UDP 发送
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout_sec)
            sock.sendto(body.encode("utf-8"), (host, int(port)))
            sock.close()
            logger.info("[流程] UDP → %s:%s | %s", host, port, body)
            return True

        elif protocol in ("http_get", "http_post"):
            # HTTP 请求
            import urllib.request
            import urllib.error
            full_url = host.rstrip("/") + (url_path if url_path.startswith("/") else "/" + url_path) if url_path else host
            if protocol == "http_get":
                req = urllib.request.Request(full_url, method="GET")
            else:
                data = body.encode("utf-8") if body else None
                req = urllib.request.Request(
                    full_url, data=data, method="POST",
                    headers={"Content-Type": "application/json"} if data else {},
                )
            resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=timeout_sec),
            )
            status = resp.getcode()
            logger.info("[流程] %s → %s | status=%d", protocol.upper(), full_url, status)
            return 200 <= status < 400

        else:
            logger.warning("[流程] 未知协议: %s", protocol)
            return False

    except Exception as e:
        logger.error("[流程] 自定义请求失败: %s %s:%s → %s", protocol, host, port, e)
        return False


async def _step_listen(robot: RobotConnection, config: dict, timeout: float) -> bool:
    """收音步骤 — 区分真正收到语音 vs 超时无人应答.

    协议: timeout 作为收音时长下发给 APK (APK 收音 N 秒后自动回唤醒词模式);
    APK 老版本不支持该字段时会忽略, 等同旧行为 (固定 5s).
    send_cmd 的网络级 timeout 比业务 timeout 多 5s, 避免赶不上 APK 的收音回调.
    """
    result = await robot.send_cmd("cmd_listen", {
        "timeout": int(timeout),
        "language": config.get("language", "zh"),
    }, timeout=timeout + 5)
    if result is None:
        # send_cmd 超时（网络层），视为失败
        logger.warning("[流程] listen 指令超时无回调")
        return False
    # 区分回调类型：evt_listen_timeout = 无人应答 = 失败
    result_type = result.get("type", "")
    if result_type == "evt_listen_timeout":
        logger.info("[流程] listen 收音超时（无人应答），视为失败")
        return False
    # evt_speech_result = 真正收到语音 = 成功
    return True


# ============================================================
# 辅助
# ============================================================
def _parse_config(config_json) -> dict:
    if isinstance(config_json, dict):
        return config_json
    try:
        return json.loads(config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


async def _log_flow(log_type: str, script_id: int, robot_id: str,
                    detail: str, status: str = "ok", duration_ms: int = 0):
    try:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO execution_logs
                   (log_type, source, target, action, detail, status, duration_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))""",
                (log_type, "flow_engine", robot_id,
                 "script_%d" % script_id, detail, status, duration_ms),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        logger.warning("流程日志记录失败: %s", e)


async def _step_digital_human(config: dict, robot: RobotConnection = None) -> bool:
    """数字人控制步骤"""
    import asyncio, urllib.request, urllib.parse
    action = config.get("action", "Show")
    text = config.get("text", "")
    BASE_URL = "http://192.168.16.18:8899"
    def _req(url):
        return urllib.request.urlopen(urllib.request.Request(url), timeout=10)
    try:
        if action == "Speak":
            if not text:
                return False
            p = urllib.parse.urlencode({"text": text}, safe="")
            await asyncio.get_event_loop().run_in_executor(None, _req, f"{BASE_URL}/api/voiceInput?{p}")
            return True
        eid = config.get("exhibitionID", "")
        if not eid and action not in ("AllShow", "AllHide"):
            return False
        p = {"command": action}
        if eid:
            p["exhibitionID"] = eid
        q = urllib.parse.urlencode(p)
        await asyncio.get_event_loop().run_in_executor(None, _req, f"{BASE_URL}/digitalHuman?{q}")
        return True
    except Exception as e:
        logger.error("[流程] 数字人控制失败: %s", e)
        return False


async def _step_pad_active(config: dict) -> bool:
    """激活展项数字人 — 通知pad系统进入某展项"""
    import asyncio, urllib.request
    eid = config.get("exhibitionID", "")
    if not eid:
        logger.warning("[流程] pad_active 缺少 exhibitionID")
        return False
    base_url = "http://192.168.16.18:8899"
    url = f"{base_url}/api/pad/active?exhibitionID={eid}"
    def _req(u):
        return urllib.request.urlopen(urllib.request.Request(u), timeout=10)
    try:
        await asyncio.get_event_loop().run_in_executor(None, _req, url)
        logger.info("[流程] pad_active 展项 %s", eid)
        return True
    except Exception as e:
        logger.error("[流程] pad_active 失败: %s", e)
        return False


# ============================================================
# 宇树机器人步骤 (HTTP 网关, 独立于豹小秘 mini WebSocket)
# ============================================================
async def _get_unitree_client():
    """按需加载 UnitreeClient; 网关禁用或未配置返回 None。"""
    from app.engine.unitree_client import UnitreeClient
    return await UnitreeClient.from_config()


async def _unitree_call(action_label: str, coro_factory) -> bool:
    """包一层统一的错误日志, coro_factory 是一个返回 coroutine 的 lambda。"""
    from app.engine.unitree_client import UnitreeGatewayError
    client = await _get_unitree_client()
    if not client:
        logger.warning("[流程][宇树] %s 跳过: 网关未启用", action_label)
        return False
    try:
        await coro_factory(client)
        return True
    except UnitreeGatewayError as e:
        logger.error("[流程][宇树] %s 失败: %s", action_label, e)
        return False
    except Exception as e:
        logger.error("[流程][宇树] %s 异常: %s", action_label, e, exc_info=True)
        return False


async def _unitree_wait(event_key: str, timeout: float, *, label: str = "") -> bool:
    """等待宇树网关 webhook 事件; 超时则打 warning 并按 True 返回 (视作步骤已下发成功,
    避免回调丢失时整个流程卡死)。

    关键前提: 网关 .env 里已配置 CENTRAL_CALLBACK_URL=http://<hub>:8000/api/unitree/event;
    未配置时所有 wait 都会走 timeout 路径, 退化为 fire-and-forget。
    """
    from app.engine.unitree_events import wait_for_event
    try:
        timeout = float(timeout) if timeout else 30.0
    except (TypeError, ValueError):
        timeout = 30.0
    fut = wait_for_event(event_key)
    try:
        payload = await asyncio.wait_for(fut, timeout=timeout)
        logger.info("[流程][宇树] 回调到达 %s (%s): %s", label or event_key, event_key,
                    str(payload)[:120])
        return True
    except asyncio.TimeoutError:
        logger.warning(
            "[流程][宇树] 等待 %s 超时 (%ss, key=%s) — 按已完成推进下一步",
            label or event_key, timeout, event_key,
        )
        return True


async def _step_unitree_tts(config: dict, wait_timeout: float = 30.0) -> bool:
    """宇树 TTS 播报 — 发起后 await ttsFinished webhook, 最长等 wait_timeout。"""
    text = (config.get("text") or "").strip()
    if not text:
        logger.warning("[流程][宇树] tts 缺少 text")
        return False
    # 单次最大 200 字 (Pydantic 强校验), 超长前端已限制; 这里保险截断
    text = text[:200]
    from app.engine.unitree_client import UnitreeGatewayError
    client = await _get_unitree_client()
    if not client:
        return False
    try:
        trace_id = await client.tts_play(text)
        logger.info("[流程][宇树] TTS 已下发 trace=%s (%d字), 等回调", trace_id, len(text))
    except UnitreeGatewayError as e:
        logger.error("[流程][宇树] tts.play 失败: %s", e)
        return False
    except Exception as e:
        logger.error("[流程][宇树] tts.play 异常: %s", e, exc_info=True)
        return False
    return await _unitree_wait(f"tts:{trace_id}", wait_timeout, label="ttsFinished")




async def _step_unitree_tts_stop(config: dict) -> bool:
    async def _run(c):
        await c.tts_stop()
        logger.info("[流程][宇树] TTS 已打断")
    return await _unitree_call("tts.stop", _run)


async def _step_unitree_volume(config: dict) -> bool:
    try:
        vol = int(config.get("audio_volume", 50))
    except (TypeError, ValueError):
        logger.warning("[流程][宇树] volume 参数非数字: %s", config.get("audio_volume"))
        return False
    vol = max(0, min(100, vol))
    async def _run(c):
        await c.tts_volume_set(vol)
        logger.info("[流程][宇树] 音量已设为 %d", vol)
    return await _unitree_call("tts.volume.set", _run)


async def _step_unitree_motion(config: dict, wait_timeout: float = 30.0) -> bool:
    """宇树动作 — 发起后 await motionPlaybackFinished webhook。"""
    mid_raw = config.get("motion_id") or config.get("motionId")
    try:
        mid = int(mid_raw)
    except (TypeError, ValueError):
        logger.warning("[流程][宇树] motion 缺少 motion_id: %s", config)
        return False
    client = await _get_unitree_client()
    if not client:
        return False
    from app.engine.unitree_client import UnitreeGatewayError
    try:
        await client.motion_play(mid)
        logger.info("[流程][宇树] 动作 %d 已下发, 等回调", mid)
    except UnitreeGatewayError as e:
        logger.error("[流程][宇树] motions.playback.create 失败: %s", e)
        return False
    except Exception as e:
        logger.error("[流程][宇树] motion 异常: %s", e, exc_info=True)
        return False
    # 回调 params: {motions_id} → key=motion:<id>
    return await _unitree_wait(f"motion:{mid}", wait_timeout, label="motionPlaybackFinished")


async def _step_unitree_dance(config: dict, wait_timeout: float = 120.0) -> bool:
    """宇树舞蹈 — 发起后 await danceFinished webhook; 超时默认给 120s (最长 F1A 125s)。"""
    did = (config.get("dance_id") or config.get("danceId") or "").strip()
    if not did:
        logger.warning("[流程][宇树] dance 缺少 dance_id")
        return False
    client = await _get_unitree_client()
    if not client:
        return False
    from app.engine.unitree_client import UnitreeGatewayError
    try:
        await client.dance_select(did)
        logger.info("[流程][宇树] 舞蹈 %s 已下发, 等回调", did)
    except UnitreeGatewayError as e:
        logger.error("[流程][宇树] dance.select 失败: %s", e)
        return False
    except Exception as e:
        logger.error("[流程][宇树] dance 异常: %s", e, exc_info=True)
        return False
    # 回调 params: {dance_name} — 可能是 id 或 name, 两个都等一下
    from app.engine.unitree_events import wait_for_event
    key_id = f"dance:{did}"
    fut_id = wait_for_event(key_id)
    # 从本地 dances 库拿中文名也等一下
    alt_keys = []
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT name FROM unitree_dances WHERE dance_id=? LIMIT 1", (did,)
            )
            if rows and rows[0]["name"]:
                alt_keys.append(f"dance:{rows[0]['name']}")
        finally:
            await db.close()
    except Exception:
        pass
    alt_futs = [wait_for_event(k) for k in alt_keys]
    try:
        timeout = float(wait_timeout) if wait_timeout else 120.0
    except (TypeError, ValueError):
        timeout = 120.0
    try:
        done, _pending = await asyncio.wait(
            [fut_id, *alt_futs],
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if done:
            logger.info("[流程][宇树] danceFinished 已到 (dance_id=%s)", did)
        else:
            logger.warning(
                "[流程][宇树] 等待 danceFinished 超时 (%ss, dance_id=%s) — 推进下一步",
                timeout, did,
            )
    except Exception as e:
        logger.warning("[流程][宇树] dance wait 异常: %s", e)
    return True


async def _step_unitree_dance_stop(config: dict) -> bool:
    async def _run(c):
        await c.dance_stop()
        logger.info("[流程][宇树] 舞蹈已停止")
    return await _unitree_call("dance.stop", _run)


async def _step_unitree_map_apply(config: dict) -> bool:
    map_id = str(config.get("map_id") or "").strip()
    if not map_id:
        logger.warning("[流程][宇树] map_apply 缺少 map_id")
        return False
    async def _run(c):
        await c.map_detail(map_id)
        logger.info("[流程][宇树] 切换工作地图 → %s", map_id)
    ok = await _unitree_call("map.detail", _run)
    # 同步更新本地 is_current 标记, 供后续导航步骤校验
    if ok:
        try:
            db = await get_db()
            try:
                await db.execute("UPDATE unitree_maps SET is_current=0")
                await db.execute(
                    "UPDATE unitree_maps SET is_current=1 WHERE map_id=?", (map_id,)
                )
                await db.commit()
            finally:
                await db.close()
        except Exception as e:
            logger.warning("[流程][宇树] 同步当前地图标记失败: %s", e)
    return ok


async def _step_unitree_nav(config: dict, execution: FlowExecution,
                             wait_timeout: float = 180.0) -> bool:
    """宇树导航到点位 — 发起后 await navTaskFinished webhook; 超时按已到达推进。"""
    map_id = str(config.get("map_id") or "").strip()
    point_id_raw = config.get("point_id") or config.get("pointId")
    try:
        point_id = int(point_id_raw)
    except (TypeError, ValueError):
        logger.warning("[流程][宇树] nav 缺少 point_id: %s", config)
        return False
    if not map_id:
        # 兜底: 用本地当前工作地图
        try:
            db = await get_db()
            try:
                rows = await db.execute_fetchall(
                    "SELECT map_id FROM unitree_maps WHERE is_current=1 LIMIT 1"
                )
                if rows:
                    map_id = str(rows[0]["map_id"])
            finally:
                await db.close()
        except Exception:
            pass
    if not map_id:
        logger.warning("[流程][宇树] nav 无当前工作地图, 请先执行 map_apply")
        return False

    from app.engine.unitree_client import UnitreeGatewayError
    client = await _get_unitree_client()
    if not client:
        return False
    try:
        task_id = await client.nav_goto(map_id, point_id)
        execution._unitree_last_nav_task = task_id
        logger.info("[流程][宇树] 导航已下发 map=%s point=%d task=%s, 等回调",
                    map_id, point_id, task_id)
    except UnitreeGatewayError as e:
        logger.error("[流程][宇树] 导航下发失败: %s", e)
        return False
    except Exception as e:
        logger.error("[流程][宇树] 导航异常: %s", e, exc_info=True)
        return False
    return await _unitree_wait(f"nav:{task_id}", wait_timeout, label="navTaskFinished")


async def _step_unitree_nav_control(config: dict, execution: FlowExecution) -> bool:
    action = (config.get("action") or "cancel").strip().lower()
    if action not in ("pause", "resume", "cancel"):
        logger.warning("[流程][宇树] nav_control action 非法: %s", action)
        return False
    task_id = str(config.get("task_id") or "").strip()
    if not task_id:
        task_id = getattr(execution, "_unitree_last_nav_task", None) or ""
    if not task_id:
        logger.warning("[流程][宇树] nav_control 缺少 task_id 且无最近导航任务")
        return False
    async def _run(c):
        await c.nav_task_control(task_id, action)
        logger.info("[流程][宇树] 导航控制 %s → %s", action, task_id)
    return await _unitree_call("nav.task_control", _run)


async def _step_unitree_asr(config: dict, execution: "FlowExecution" = None) -> bool:
    action = (config.get("action") or "start").strip().lower()
    if action not in ("start", "stop", "listen_once"):
        logger.warning("[流程][宇树] asr action 非法: %s", action)
        return False
    # listen_once: 启动 → 等首条文本 (或超时) → 自动停, 结果写进 execution 供后续步骤读
    if action == "listen_once":
        try:
            duration = float(config.get("duration") or 10)
        except (TypeError, ValueError):
            duration = 10.0
        duration = max(3.0, min(60.0, duration))
        from app.engine.unitree_voice import asr_listen_once
        try:
            result = await asr_listen_once(duration=duration)
        except Exception as e:
            logger.error("[流程][宇树] listen_once 异常: %s", e, exc_info=True)
            return False
        text = result.get("text", "")
        if execution is not None:
            execution._unitree_last_asr_text = text
            execution._unitree_last_asr_result = result
        logger.info("[流程][宇树] listen_once %.1fs → text=%r timed_out=%s",
                    duration, text, result.get("timed_out"))
        # 有文本视为成功; 超时无文本视为失败 (方便流程分支)
        return bool(text)

    if action == "start":
        try:
            listen_duration = float(config.get("listen_duration") or 0)
        except (TypeError, ValueError):
            listen_duration = 0.0
        listen_duration = max(0.0, min(600.0, listen_duration))
        from app.engine.unitree_voice import asr_start_timed
        try:
            r = await asr_start_timed(listen_duration=listen_duration)
        except Exception as e:
            logger.error("[流程][宇树] asr.start 异常: %s", e, exc_info=True)
            return False
        if not r.get("ok"):
            logger.warning("[流程][宇树] asr.start 失败: %s", r.get("error"))
            return False
        logger.info("[流程][宇树] ASR start 成功, listen_duration=%.1fs", listen_duration)
        return True

    async def _run(c):
        await c.asr_stop()
        logger.info("[流程][宇树] ASR stop")
    return await _unitree_call("asr.stop", _run)


async def _step_unitree_face(config: dict) -> bool:
    action = (config.get("action") or "start").strip().lower()
    if action not in ("start", "stop"):
        logger.warning("[流程][宇树] face action 非法: %s", action)
        return False
    group_id_raw = config.get("group_id")
    group_id = None
    if group_id_raw not in (None, ""):
        try:
            group_id = int(group_id_raw)
        except (TypeError, ValueError):
            logger.warning("[流程][宇树] face group_id 非数字: %s", group_id_raw)
    async def _run(c):
        if action == "start":
            await c.face_start(group_id)
        else:
            await c.face_stop()
        logger.info("[流程][宇树] 人脸识别 %s group_id=%s", action, group_id)
    return await _unitree_call(f"face_recognition.{action}", _run)
