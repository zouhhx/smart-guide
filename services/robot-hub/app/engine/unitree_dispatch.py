"""
宇树命令链调度器 — OpenClaw decompose 出来的 commands 数组在宇树侧的执行入口。

关键约束 (现场要求, 不要随便改):
1. **串行执行**: 宇树网关同时发两个机器人动作会冲突, 必须前一个的 webhook 回调
   (ttsFinished / motionPlaybackFinished / danceFinished / navTaskFinished) 落地
   再发下一条。dispatch 内全局 _serial_lock + 每条 cmd 后 wait_for_event。
2. **机器人 vs 中控分通道**:
   - 机器人专属 (tts/motion/dance/navigate_to/wait) → UnitreeClient (HTTP 网关)
   - 中控类 (switch_special / group_command / equipment / lights_*/all_*/ad_screen_*/
     play_resource / video_control / ppt_control) → ActionExecutor → TCP 8989
     这些跟机器人无关, 豹小秘和宇树共享。
   - 屏幕类 (show_list / show_card / show_text / clear_screen) → 豹小秘屏幕
     宇树本身没屏幕, 这类命令转发给豹小秘 (若在线), 否则忽略。
3. **测试期不发宇树导航**: system_config.unitree_nav_enabled='0' 时跳过所有
   navigate_to / go_charging, 仅日志记录意图。上线后改 '1' 即可解锁。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.database import get_db

logger = logging.getLogger("hub.engine.unitree_dispatch")

# 全局串行锁 — 同一时刻只允许一个 commands 链在跑, 也阻断单链内并发
_serial_lock = asyncio.Lock()

# 各回调超时 (上限, 留充裕余量; 网关正常时早就返回)
_TIMEOUT_TTS = 120.0
_TIMEOUT_MOTION = 60.0
_TIMEOUT_DANCE = 240.0
_TIMEOUT_NAV = 300.0


# ============================================================
# 命令路由表
# ============================================================
# 中控类 — 跟豹小秘共享, 调 ActionExecutor (TCP 8989), 不走宇树网关
_CENTRAL_TYPES = {
    "switch_special",
    "lights_on", "lights_off",
    "all_on", "all_off",
    "ad_screen_on", "ad_screen_off",
    "equipment", "group_command", "group",
    "play_resource",
    "video_control", "ppt_control",
    "go_charging",  # 充电桩走 ActionExecutor; 仅当用户给豹小秘充电时
}

# 屏幕展示 — 给豹小秘屏幕用, 宇树没屏幕
_SCREEN_TYPES = {
    "show_list", "list", "show",
    "show_card",
    "show_text",
    "clear_screen", "show_clear",
}

# 宇树机器人专属
_UNITREE_TYPES = {
    "tts", "speak",
    "navigate_to", "nav",
    "motion", "unitree_motion",
    "dance", "unitree_dance",
    "wait",
    "listen",  # 主动开收音 (asr_start)
}


# ============================================================
# 配置开关
# ============================================================
async def _is_unitree_nav_enabled() -> bool:
    """system_config.unitree_nav_enabled — 测试期默认 '0' 关掉宇树导航,
    避免开发联调时机器人乱跑。上线前改 '1'。"""
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='unitree_nav_enabled'"
            )
            if rows:
                return str(rows[0]["value"]).strip() in ("1", "true", "yes", "on")
        finally:
            await db.close()
    except Exception:
        pass
    return False  # 默认关


# ============================================================
# 主入口
# ============================================================
async def dispatch_commands(commands: list, robot_client: Any,
                            robot_kind: str = "unitree") -> dict:
    """串行执行宇树侧 commands 数组。

    robot_kind: "unitree" (默认, 无屏) / "baoxiaomi" (有屏). 决定 show_* 类命令是
    走屏幕还是转成 TTS 念出来 — 即使 OpenClaw 没遵守 robot_kind 提示词输出了
    show_list, V5 这里也最后一道兜底, 保证用户体验。

    返回 {"executed": int, "skipped": int, "errors": [..], "tts_count": int};
    tts_count 给上层判断"是否还需要补播 reply"。
    """
    if not commands:
        return {"executed": 0, "skipped": 0, "errors": [], "tts_count": 0}

    nav_enabled = await _is_unitree_nav_enabled()

    result = {"executed": 0, "skipped": 0, "errors": [], "tts_count": 0}

    async with _serial_lock:
        for i, cmd in enumerate(commands):
            if not isinstance(cmd, dict):
                logger.warning("[宇树dispatch] cmd[%d] 不是 dict, 跳过: %r", i, cmd)
                result["skipped"] += 1
                continue
            ctype = (cmd.get("type") or "").strip().lower()
            try:
                if ctype in _CENTRAL_TYPES:
                    await _exec_central(cmd, ctype)
                    result["executed"] += 1
                elif ctype in _SCREEN_TYPES:
                    if robot_kind == "unitree":
                        # 宇树没屏 — 把屏幕命令转成 TTS, 用宇树网关念出来
                        # 防止 OpenClaw 没遵守 robot_kind 提示词时仍能给出正确体验
                        tts_text = _screen_cmd_to_tts(cmd, ctype)
                        if tts_text:
                            await _exec_unitree({"type": "tts", "text": tts_text}, "tts", robot_client)
                            result["tts_count"] += 1
                            result["executed"] += 1
                        else:
                            result["skipped"] += 1
                    else:
                        await _exec_screen(cmd, ctype)
                        result["executed"] += 1
                elif ctype in _UNITREE_TYPES:
                    if ctype in ("navigate_to", "nav") and not nav_enabled:
                        logger.info(
                            "[宇树dispatch] 导航功能关闭(unitree_nav_enabled=0), 跳过 target=%r",
                            cmd.get("target") or cmd.get("point_id"),
                        )
                        result["skipped"] += 1
                        continue
                    await _exec_unitree(cmd, ctype, robot_client)
                    if ctype in ("tts", "speak"):
                        result["tts_count"] += 1
                    result["executed"] += 1
                else:
                    logger.warning("[宇树dispatch] 未知 type, 跳过: %r", cmd)
                    result["skipped"] += 1
            except Exception as e:
                logger.error("[宇树dispatch] cmd[%d] type=%s 失败 err=%s cmd=%s",
                             i, ctype, e, cmd, exc_info=True)
                result["errors"].append({"type": ctype, "error": str(e)[:200]})
                # 单步失败不打断后续, 跟豹小秘行为对齐
                continue

    logger.info("[宇树dispatch] 完成: %d 执行 / %d 跳过 / %d 错误",
                result["executed"], result["skipped"], len(result["errors"]))
    return result


# ============================================================
# 中控类执行 — 共享 ActionExecutor
# ============================================================
async def _exec_central(cmd: dict, ctype: str):
    """中控 TCP 8989 命令, 跟豹小秘共用; 走 ActionExecutor 现成方法。"""
    from app.engine.action_executor import ActionExecutor
    executor = ActionExecutor()  # 中控指令不依赖 robot_id

    if ctype == "switch_special":
        name = (cmd.get("special") or cmd.get("name") or "").strip()
        if not name:
            return
        # 用模糊解析器, 处理 ASR 谐音 + 后缀噪声 ("思的转场"=思德专场)
        sp = await executor.find_special_by_name(name)
        if sp:
            await _switch_special_by_id(int(sp["id"]))
            logger.info("[宇树dispatch] switch_special: %s → %s", name, sp["name"])
        else:
            logger.warning("[宇树dispatch] switch_special 找不到: %r", name)
        return

    if ctype in ("lights_on", "lights_off",
                 "all_on", "all_off",
                 "ad_screen_on", "ad_screen_off"):
        fn = getattr(executor, ctype, None)
        if fn:
            ok = await fn()
            logger.info("[宇树dispatch] %s ok=%s", ctype, ok)
        return

    if ctype in ("group_command", "group"):
        name = (cmd.get("group") or cmd.get("name") or "").strip()
        action = (cmd.get("action") or "on").strip().lower()
        if name:
            ok = await executor.group_command(name, on=(action == "on"))
            logger.info("[宇树dispatch] group_command: %s %s ok=%s", name, action, ok)
        return

    if ctype == "equipment":
        name = (cmd.get("equipment") or cmd.get("name") or "").strip()
        action = (cmd.get("action") or "on").strip().lower()
        if name:
            await _equipment_by_name(name, on=(action == "on"))
            logger.info("[宇树dispatch] equipment: %s %s", name, action)
        return

    if ctype == "play_resource":
        ex_name = (cmd.get("exhibit") or "").strip()
        idx = int(cmd.get("resource_index", 1) or 1)
        if ex_name:
            await _play_resource_by_name(ex_name, idx)
        return

    if ctype == "video_control":
        ex_name = (cmd.get("exhibit") or "").strip()
        action = (cmd.get("action") or "play").strip().lower()
        if ex_name:
            ctrl_id = await _exhibit_terminal_id(ex_name)
            if ctrl_id:
                await executor.video_control(ctrl_id, action)
        return

    if ctype == "ppt_control":
        ex_name = (cmd.get("exhibit") or "").strip()
        action = (cmd.get("action") or "next").strip().lower()
        if ex_name:
            ctrl_id = await _exhibit_terminal_id(ex_name)
            if ctrl_id:
                await executor.ppt_control(ctrl_id, action)
        return

    if ctype == "go_charging":
        # 充电桩在豹小秘那边; 宇树侧暂不实现. 如要让豹小秘回充, 走 executor.
        ok = await executor.go_charging()
        logger.info("[宇树dispatch] go_charging (豹小秘) ok=%s", ok)
        return


async def _switch_special_by_id(special_id: int) -> None:
    """切换专场 — 同 smart_engine._dispatch_switch_special 简化版。"""
    from app.tcp.central_control import send_tcp_command
    await send_tcp_command(f"2_update_{special_id}")
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO system_config(key,value) VALUES ('current_special_id', ?)",
            (str(special_id),),
        )
        await db.execute(
            "UPDATE specials SET is_current=CASE WHEN id=? THEN 1 ELSE 0 END",
            (special_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def _equipment_by_name(name: str, on: bool) -> None:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT cmd_on, cmd_off FROM equipments WHERE name=? LIMIT 1", (name,),
        )
        if not rows:
            logger.warning("[宇树dispatch] equipment 找不到名字=%r", name)
            return
        cmd = rows[0]["cmd_on"] if on else rows[0]["cmd_off"]
        if cmd:
            from app.tcp.central_control import send_tcp_command
            await send_tcp_command(cmd)
    finally:
        await db.close()


async def _play_resource_by_name(exhibit_name: str, idx: int) -> None:
    db = await get_db()
    try:
        ex_rows = await db.execute_fetchall(
            "SELECT id, exhibition_area_terminal_id FROM exhibits WHERE name=? LIMIT 1",
            (exhibit_name,),
        )
        if not ex_rows:
            logger.warning("[宇树dispatch] play_resource 展项不存在: %r", exhibit_name)
            return
        ex_id = int(ex_rows[0]["id"])
        ctrl_id = ex_rows[0]["exhibition_area_terminal_id"]
        sp_rows = await db.execute_fetchall(
            "SELECT value FROM system_config WHERE key='current_special_id'"
        )
        sp_id = int(sp_rows[0]["value"]) if sp_rows and sp_rows[0]["value"] else 0
        if not sp_id:
            return
        res_rows = await db.execute_fetchall(
            """SELECT resource_id FROM special_exhibit_resources
               WHERE special_id=? AND exhibit_id=?
               ORDER BY sort_order LIMIT ? OFFSET ?""",
            (sp_id, ex_id, 1, max(0, idx - 1)),
        )
        if not res_rows:
            return
        from app.engine.action_executor import ActionExecutor
        ok = await ActionExecutor().play_resource(sp_id, ctrl_id, int(res_rows[0]["resource_id"]))
        logger.info("[宇树dispatch] play_resource %s[%d] ok=%s", exhibit_name, idx, ok)
    finally:
        await db.close()


async def _exhibit_terminal_id(exhibit_name: str) -> int:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT exhibition_area_terminal_id FROM exhibits WHERE name=? LIMIT 1",
            (exhibit_name,),
        )
        return int(rows[0]["exhibition_area_terminal_id"]) if rows else 0
    finally:
        await db.close()


# ============================================================
# 屏幕命令转 TTS — 宇树没屏时把 show_* 直接念出来
# ============================================================
def _screen_cmd_to_tts(cmd: dict, ctype: str) -> str:
    """把屏幕展示命令的内容拼成可朗读的中文短句。"""
    if ctype in ("clear_screen", "show_clear"):
        return ""  # 宇树没屏, 清屏对宇树无意义
    title = (cmd.get("title") or "").strip()
    items = cmd.get("items") or []
    text = (cmd.get("text") or "").strip()

    if ctype == "show_text":
        return text  # 直接念

    # 把 items 规范化成字符串数组 (item 可能是 str 或 dict{name,detail,...})
    norm: list[str] = []
    for it in items:
        if isinstance(it, str):
            norm.append(it.strip())
        elif isinstance(it, dict):
            n = (it.get("name") or it.get("title") or "").strip()
            if n:
                norm.append(n)
    norm = [x for x in norm if x]
    if not norm:
        return text or title

    if ctype in ("show_list", "list", "show"):
        head = title or "展厅展示项"
        # 截断防 TTS 太长 (单段 ≤200 字)
        if len(norm) > 8:
            body = "、".join(norm[:8]) + f"等{len(norm)}项"
        else:
            body = "、".join(norm)
        return f"{head}有: {body}。您直接告诉我想看哪个。"

    if ctype == "show_card":
        # 卡片通常是单条详细信息; items 拼成一句
        if title:
            return f"{title}: " + "; ".join(norm) + "。"
        return "; ".join(norm) + "。"

    return ""


# ============================================================
# 屏幕类执行 — 转发到豹小秘 (宇树本身无屏)
# ============================================================
async def _exec_screen(cmd: dict, ctype: str):
    """屏幕命令转发给豹小秘机器人; 豹小秘不在线则忽略并 warn。"""
    try:
        from app.tcp.server import get_all_robots
        robots = get_all_robots() or {}
        if not robots:
            logger.warning("[宇树dispatch] %s 跳过: 没有豹小秘机器人在线", ctype)
            return
        # 取第一个在线的豹小秘 (现场就一台 robot_001)
        robot = next(iter(robots.values()))
        if ctype in ("show_list", "list", "show"):
            await robot.send_cmd("cmd_show", {
                "action": "list",
                "listType": cmd.get("listType") or "terminal",
                "title": cmd.get("title") or "",
                "items": cmd.get("items") or [],
            }, timeout=5)
        elif ctype == "show_card":
            await robot.send_cmd("cmd_show", {
                "action": "card",
                "title": cmd.get("title") or "",
                "items": cmd.get("items") or [],
            }, timeout=5)
        elif ctype == "show_text":
            await robot.send_cmd("cmd_show", {
                "action": "text",
                "text": (cmd.get("text") or "")[:500],
            }, timeout=5)
        elif ctype in ("clear_screen", "show_clear"):
            await robot.send_cmd("cmd_show", {"action": "clear"}, timeout=5)
    except Exception as e:
        logger.warning("[宇树dispatch] 屏幕命令失败 %s: %s", ctype, e)


# ============================================================
# 宇树机器人专属执行 — 严格串行 + 等回调
# ============================================================
async def _exec_unitree(cmd: dict, ctype: str, client: Any):
    """每条命令必须等到对应的 webhook 回调才返回, 阻塞 _serial_lock 内的下一条。"""
    from app.engine.unitree_events import wait_for_event

    if ctype in ("tts", "speak"):
        text = (cmd.get("text") or "").strip()
        if not text:
            return
        trace_id = await client.tts_play(text)
        if not trace_id:
            return
        try:
            await asyncio.wait_for(
                wait_for_event(f"tts:{trace_id}"), timeout=_TIMEOUT_TTS,
            )
            logger.info("[宇树dispatch] tts done trace=%s text=%r", trace_id, text[:30])
        except asyncio.TimeoutError:
            logger.warning("[宇树dispatch] tts 等 ttsFinished 超时 trace=%s", trace_id)
        return

    if ctype in ("motion", "unitree_motion"):
        # 接受 motion_id (数字) 或 name (查表)
        motion_id = cmd.get("motion_id") or cmd.get("id")
        if not motion_id and (name := cmd.get("name") or cmd.get("motion")):
            motion_id = await _lookup_motion_id(str(name))
        try:
            motion_id = int(motion_id)
        except (TypeError, ValueError):
            logger.warning("[宇树dispatch] motion 无 motion_id, 跳过: %s", cmd)
            return
        await client.motion_play(motion_id)
        try:
            await asyncio.wait_for(
                wait_for_event(f"motion:{motion_id}"), timeout=_TIMEOUT_MOTION,
            )
            logger.info("[宇树dispatch] motion done id=%s", motion_id)
        except asyncio.TimeoutError:
            logger.warning("[宇树dispatch] motion 等 motionPlaybackFinished 超时 id=%s", motion_id)
        return

    if ctype in ("dance", "unitree_dance"):
        dance_id = cmd.get("dance_id") or cmd.get("id") or cmd.get("name")
        if not dance_id:
            return
        dance_id = str(dance_id)
        await client.dance_select(dance_id)
        try:
            await asyncio.wait_for(
                wait_for_event(f"dance:{dance_id}"), timeout=_TIMEOUT_DANCE,
            )
            logger.info("[宇树dispatch] dance done id=%s", dance_id)
        except asyncio.TimeoutError:
            logger.warning("[宇树dispatch] dance 等 danceFinished 超时 id=%s", dance_id)
        return

    if ctype in ("navigate_to", "nav"):
        # 上层已通过 _is_unitree_nav_enabled 守护; 走到这里说明已开启
        target = (cmd.get("target") or "").strip()
        if not target:
            return
        info = await _lookup_unitree_point(target)
        if not info:
            logger.warning("[宇树dispatch] nav 找不到点位: %r", target)
            return
        from app.engine.unitree_events import register_nav_target
        task_id = await client.nav_goto(info["map_id"], int(info["point_id"]))
        register_nav_target(task_id, info["map_id"], int(info["point_id"]))
        try:
            await asyncio.wait_for(
                wait_for_event(f"nav:{task_id}"), timeout=_TIMEOUT_NAV,
            )
            logger.info("[宇树dispatch] nav arrived task=%s target=%s", task_id, target)
        except asyncio.TimeoutError:
            logger.warning("[宇树dispatch] nav 等 navTaskFinished 超时 task=%s", task_id)
        return

    if ctype == "wait":
        seconds = float(cmd.get("seconds") or cmd.get("duration") or 2)
        seconds = max(0.5, min(60.0, seconds))
        await asyncio.sleep(seconds)
        return

    if ctype == "wait_until_idle":
        # 等用户答完才推下一步 — 跟豹小秘 _dispatch_command_list 同款语义。
        # 关键: 临时释放 _serial_lock 让用户答能完整经过 unitree_voice.handle_speech
        # (含 bot 应答). 否则用户回答会被 _serial_lock 排队卡住, 等同于"答了但没人应"。
        # 退出时重新 acquire 继续后续 dispatch (e.g. 回工位)。
        import time as _time
        from app.engine.world_state import get_world_state
        ws = get_world_state()
        max_secs = float(cmd.get("max_seconds") or cmd.get("max") or 60)
        idle_th = float(cmd.get("idle_threshold") or cmd.get("idle") or 5)
        initial = float(cmd.get("initial_wait") or cmd.get("initial") or 8)
        max_secs = max(2.0, min(120.0, max_secs))
        idle_th = max(2.0, min(30.0, idle_th))
        initial = max(2.0, min(30.0, initial))
        start_t = _time.time()
        deadline = start_t + max_secs
        logger.info("[宇树dispatch] wait_until_idle max=%.0fs idle=%.0fs initial=%.0fs (释放 _serial_lock)",
                    max_secs, idle_th, initial)
        _serial_lock.release()
        try:
            while _time.time() < deadline:
                last_t = ws.recent.last_input_time or 0
                user_started = last_t > start_t
                now = _time.time()
                # bot 在说话 / 锁被持有 / bot 刚说完不到 idle_th 秒 → 继续等
                if ws.dialog.is_speaking:
                    await asyncio.sleep(0.5); continue
                if _serial_lock.locked():
                    await asyncio.sleep(0.5); continue
                bot_done_at = ws.dialog.last_speaking_done_at or 0
                bot_speak_gap = (now - bot_done_at) if bot_done_at else 99999
                if bot_speak_gap < idle_th:
                    await asyncio.sleep(0.5); continue
                if user_started:
                    if (now - last_t) >= idle_th:
                        logger.info("[宇树dispatch] wait_until_idle: 用户停%.0fs+bot停%.0fs, 继续",
                                    now - last_t, bot_speak_gap)
                        break
                else:
                    if (now - start_t) >= initial:
                        logger.info("[宇树dispatch] wait_until_idle: 用户%.0fs没开口, 继续", initial)
                        break
                await asyncio.sleep(0.5)
            else:
                logger.info("[宇树dispatch] wait_until_idle: 兜底超时")
        finally:
            await _serial_lock.acquire()
        return

    if ctype == "listen":
        # 主动收音 — 单触发, 不等回调 (asrWakeup 不一定每次都来)
        try:
            await client.asr_start()
        except Exception as e:
            logger.warning("[宇树dispatch] listen 启动失败: %s", e)
        return


async def _lookup_motion_id(name: str) -> int | None:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT motion_id FROM unitree_motions WHERE name=? LIMIT 1", (name,),
        )
        return int(rows[0]["motion_id"]) if rows else None
    finally:
        await db.close()


async def _lookup_unitree_point(name: str) -> dict | None:
    """按名字查宇树点位 — exact 优先, 子串再次, 拼音模糊兜底。返回 {map_id, point_id}。

    数据来源 unitree_map_points (map_id, point_id, point_name)。
    """
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT map_id, point_id, point_name FROM unitree_map_points",
        )
        if not rows:
            return None
        # 1. exact
        for r in rows:
            if (r["point_name"] or "") == name:
                return {"map_id": r["map_id"], "point_id": r["point_id"]}
        # 2. 子串双向
        for r in rows:
            pn = r["point_name"] or ""
            if pn and (name in pn or pn in name):
                return {"map_id": r["map_id"], "point_id": r["point_id"]}
        # 3. 拼音模糊
        try:
            from app.engine.intent_resolver import _to_pinyin
            from app.engine.intent_synonyms import _edit_distance
            n_py = _to_pinyin(name)
            best = None
            best_ed = 999
            for r in rows:
                rpy = _to_pinyin(r["point_name"] or "")
                if not rpy:
                    continue
                if n_py == rpy or n_py in rpy or rpy in n_py:
                    return {"map_id": r["map_id"], "point_id": r["point_id"]}
                if min(len(n_py), len(rpy)) >= 4 and abs(len(n_py) - len(rpy)) <= 2:
                    ed = _edit_distance(n_py, rpy)
                    if ed <= 2 and ed < best_ed:
                        best_ed, best = ed, r
            if best:
                return {"map_id": best["map_id"], "point_id": best["point_id"]}
        except Exception:
            pass
    finally:
        await db.close()
    return None
