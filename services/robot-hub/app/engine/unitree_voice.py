"""
宇树机器人语音处理器

入口: handle_speech(text)
职责:
1. 宇树"自身控制"意图识别 (规则匹配, 覆盖打招呼/动作/舞蹈/音量/再见).
2. 不命中 → 走 V5 通用问答引擎 (classify_query + answer_engine) 拿答案.
3. 通过宇树 HTTP 网关 TTS 回播.

与豹小秘语音链路完全独立: 不触碰 SmartEngine/ActionExecutor; 不写 world_state
的状态, 只读. robot_id 固定为 "unitree_g1", 方便日志/访客系统过滤.

输入清洗:
- 去除首尾空白
- 过滤唤醒词残留 (宇树 ASR 通常已过滤, 保险起见兜一层)
- 空文本直接丢弃 (ASR 误触发)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

from app.engine.unitree_client import UnitreeClient, UnitreeGatewayError

logger = logging.getLogger("hub.engine.unitree_voice")

UNITREE_ROBOT_ID = "unitree_g1"

# 宇树常用动作速查 (接口指南 §3.6 + §B 节选)
_MOTION_HI = 2003         # 挥手
_MOTION_BOW = 2016        # 敬礼
_MOTION_HEART = 2014      # 比心
_MOTION_HANDSHAKE = 2005  # 握手
_MOTION_HUG = 2015        # 抱抱
_MOTION_POSE = 3056       # 战斗姿态
_MOTION_RESET = 2000      # 复位
_MOTION_POINT_CAM = 3057  # 手指摄像头
_MOTION_TURN = 2018       # 转身

_DANCE_DEFAULT = "F1X"    # 胜利之舞
_DANCE_APTS = "F1B"       # apts
_DANCE_TAIJI = "F1A"      # 太极舞


class _CommandHit:
    """规则命中结果"""
    __slots__ = ("label", "reply_text", "coro")

    def __init__(self, label: str, reply_text: str, coro):
        self.label = label
        self.reply_text = reply_text
        self.coro = coro  # 可选: 需要调网关的协程工厂 lambda(client): ...


# ========== 规则匹配 ==========
def _match_command(text: str) -> Optional[_CommandHit]:
    """按关键词命中宇树自身控制; 返回 _CommandHit 或 None."""
    t = (text or "").strip().lower()
    if not t:
        return None

    # 清掉常见标点, 方便匹配
    clean = re.sub(r"[，。！？、,.?!;；\s]+", "", t)

    # -------- 打招呼 / 再见 --------
    if any(k in clean for k in ("你好", "hi", "hello", "嗨", "在吗")):
        return _CommandHit("greet", "您好，我是宇树机器人，有什么可以帮您？",
                           lambda c: c.motion_play(_MOTION_HI))
    if any(k in clean for k in ("再见", "拜拜", "bye", "回见", "晚安")):
        return _CommandHit("farewell", "再见，欢迎下次再来。",
                           lambda c: c.motion_play(_MOTION_HI))

    # -------- 动作 --------
    if any(k in clean for k in ("挥个手", "挥挥手", "挥手")):
        return _CommandHit("wave", "好的", lambda c: c.motion_play(_MOTION_HI))
    if any(k in clean for k in ("敬个礼", "敬礼")):
        return _CommandHit("salute", "敬礼！", lambda c: c.motion_play(_MOTION_BOW))
    if any(k in clean for k in ("比个心", "比心", "给我个爱心")):
        return _CommandHit("heart", "给您比个心", lambda c: c.motion_play(_MOTION_HEART))
    if any(k in clean for k in ("握个手", "握手")):
        return _CommandHit("shake", "握个手", lambda c: c.motion_play(_MOTION_HANDSHAKE))
    if any(k in clean for k in ("抱一下", "抱抱", "来个拥抱")):
        return _CommandHit("hug", "给您个抱抱", lambda c: c.motion_play(_MOTION_HUG))
    if any(k in clean for k in ("酷一点", "摆个造型", "战斗姿态", "帅一个")):
        return _CommandHit("pose", "摆个造型", lambda c: c.motion_play(_MOTION_POSE))
    if any(k in clean for k in ("复位", "站好", "恢复姿势", "归位")):
        return _CommandHit("reset", "好的", lambda c: c.motion_play(_MOTION_RESET))
    if "转身" in clean or "转个圈" in clean or "转一圈" in clean:
        return _CommandHit("turn", "好的", lambda c: c.motion_play(_MOTION_TURN))

    # -------- 舞蹈 --------
    if "太极" in clean:
        return _CommandHit("dance_taiji", "给您来段太极舞",
                           lambda c: c.dance_select(_DANCE_TAIJI))
    if "apts" in clean or "爱辟它" in clean:
        return _CommandHit("dance_apts", "来段 apts",
                           lambda c: c.dance_select(_DANCE_APTS))
    if any(k in clean for k in ("跳个舞", "跳支舞", "跳舞吧", "跳舞", "来段舞")):
        return _CommandHit("dance", "好的，给您跳段胜利之舞",
                           lambda c: c.dance_select(_DANCE_DEFAULT))
    if any(k in clean for k in ("别跳了", "停止跳舞", "不要跳了", "停下", "stop")):
        return _CommandHit("dance_stop", "好的",
                           lambda c: c.dance_stop())

    # -------- TTS 打断 --------
    if any(k in clean for k in ("闭嘴", "别说了", "不要说了", "停止播报", "shutup")):
        return _CommandHit("tts_stop", "",
                           lambda c: c.tts_stop())

    # -------- 音量 --------
    if any(k in clean for k in ("声音大一点", "大声点", "大点声", "音量大一点", "声音调大")):
        return _CommandHit("volume_up", "好的, 音量调大",
                           lambda c: c.tts_volume_set(85))
    if any(k in clean for k in ("声音小一点", "小声点", "小点声", "音量小一点", "声音调小")):
        return _CommandHit("volume_down", "好的, 音量调小",
                           lambda c: c.tts_volume_set(30))
    m = re.search(r"音量(?:调|设|改)?(?:到|为|成)?(\d{1,3})", clean)
    if m:
        try:
            v = max(0, min(100, int(m.group(1))))
            return _CommandHit(f"volume_set_{v}", f"音量已调为 {v}",
                               lambda c, vv=v: c.tts_volume_set(vv))
        except ValueError:
            pass

    return None


# ========== 去重 / 防抖 ==========
_last_input: dict = {"text": "", "at": 0.0}


def _is_duplicate(text: str, window: float = 2.5) -> bool:
    now = time.time()
    same = (_last_input["text"] == text) and (now - _last_input["at"] < window)
    _last_input["text"] = text
    _last_input["at"] = now
    return same


# 宇树唤醒词及 ASR 常见同音误识 (xiaoyong) — 命中前缀就剥掉, 留下真实指令文本
# 例: "小勇你好" → "你好"; "小用 介绍一下" → "介绍一下"
_UNITREE_WAKE_PREFIXES = (
    "小勇", "小庸", "小用", "小涌", "小拥",
    "晓勇", "晓庸", "校勇", "萧勇", "肖勇",
    "小y", "小Y",
)


def _strip_unitree_wake(text: str) -> str:
    """剥离宇树唤醒词前缀 + 紧随的标点/语气助词。多次匹配 (有时 ASR 重复)。"""
    t = (text or "").strip()
    for _ in range(3):  # 最多剥 3 次, 防止 "小勇小勇 你好"
        before = t
        for wake in _UNITREE_WAKE_PREFIXES:
            if t.startswith(wake):
                t = t[len(wake):].lstrip(" ,.!?，。！？、:；;\t的")
                break
        if t == before:
            break
    return t.strip()


# ========== 主入口 ==========
async def handle_speech(text: str):
    """宇树 ASR 文本入口 (异步, 由 webhook create_task 调用)."""
    text = (text or "").strip()
    if not text:
        return
    # 剥唤醒词 — 现场宇树 ASR 经常把唤醒词跟话一起送上来 ("小勇 附近有美食")
    # 不剥的话整条意图链上 "小勇" 会被当成普通字符串干扰分类/匹配。
    stripped = _strip_unitree_wake(text)
    if stripped != text:
        logger.info("[宇树语音] 剥唤醒词: %r → %r", text, stripped)
        text = stripped
    if not text:
        # 用户只喊了一声 "小勇" 没说后续 — 给一句应答, 不当无效输入扔掉
        try:
            client = await UnitreeClient.from_config(timeout=15)
            if client:
                await client.tts_play("我在的，您说。")
        except Exception:
            pass
        return
    if _is_handle_speech_suspended():
        logger.info("[宇树语音] listen_once 进行中, handle_speech 挂起: %s", text[:30])
        return
    if _is_duplicate(text):
        logger.info("[宇树语音] 重复输入忽略: %s", text[:30])
        return

    logger.info("[宇树语音] 识别到: %r", text)
    await _log_voice("asr_input", text, "")
    # 同步写到 world_state.last_input_time, 让 wait_until_idle 等"用户答完"
    # 能感知宇树侧的用户输入 (ws 是单例, 跨两类机器人共享)
    try:
        from app.engine.world_state import get_world_state
        get_world_state().record_input(text)
    except Exception:
        pass

    client = await UnitreeClient.from_config(timeout=15)
    if not client:
        logger.warning("[宇树语音] 网关未启用, 无法回播")
        return

    # 1) 规则匹配宇树自身控制
    hit = _match_command(text)
    if hit:
        logger.info("[宇树语音] 规则命中: %s", hit.label)
        # 先把回复发去 TTS, 再并行执行动作 (避免先做动作没说话体验差)
        try:
            if hit.reply_text:
                await client.tts_play(hit.reply_text)
            if hit.coro:
                await hit.coro(client)
        except UnitreeGatewayError as e:
            logger.warning("[宇树语音] 规则执行失败: %s", e)
        except Exception as e:
            logger.warning("[宇树语音] 规则执行异常: %s", e, exc_info=True)
        await _log_voice("asr_rule", text, f"{hit.label}:{hit.reply_text[:40]}")
        return

    # 1.5) 设备控制 (开灯/关灯/全开/全关/广告机/充电) — 复用豹小秘 resolve_intent + ActionExecutor
    if _might_be_device_control(text):
        if await _try_device_control(client, text):
            return

    # 1.55) 流程触发词 — 用户配的触发词命中即启动 tour_scripts.script_id 对应的流程。
    # 跟豹小秘 SmartEngine 同款语义, 但宇树侧之前漏了这段, 导致触发词命中却没真正启动流程。
    if await _try_run_script(client, text):
        return

    # 1.6) 展厅查询/中控类意图 (有哪些展项 / X 有什么内容 / 切换 X 专场 / 播第 N 个) —
    # 跟豹小秘共用数据(同一份 DB + 同一个中控 8989), 结果用宇树 TTS 播报。
    # 不涉及机器人导航, 跟 unitree_nav_enabled 无关, 测试期照常工作。
    if await _try_tour_query(client, text):
        return

    # 2) 走 V5 通用问答引擎
    #    立即抛一句 ack 让用户听到"机器人在想"; 和 LLM 并行, 不等 ack 播完就开始算答案.
    ack_task = asyncio.create_task(_quick_ack(client, text))
    answer = ""
    cmd_chain: list = []
    try:
        answer, cmd_chain = await _route_to_answer_engine_full(text)
    except Exception as e:
        logger.warning("[宇树语音] 问答引擎异常: %s", e, exc_info=True)
    # 复合指令: OpenClaw 拆出的 commands 数组在宇树侧执行 (严格串行 + 等回调)
    # 中控类共享 ActionExecutor, 机器人类走 unitree_client, 屏幕类转给豹小秘
    # 测试期 unitree_nav_enabled=0 自动跳过宇树导航
    if cmd_chain:
        from app.engine.unitree_dispatch import dispatch_commands
        try:
            r = await dispatch_commands(cmd_chain, client, robot_kind="unitree")
            logger.info("[宇树语音] commands 执行: %s", r)
            # 只要 dispatch 已经发出过 TTS (含原 tts/speak + 屏命令转出的 TTS),
            # 就不再单独播 OpenClaw 的 reply — 那条 reply 经常带"看屏幕选"这种
            # 不适合宇树的话术, 重复播会拖体验。
            if (r or {}).get("tts_count", 0) > 0:
                answer = ""
            else:
                # commands 全是非 TTS 类(如纯 motion/dance), reply 还是要念让用户知道在做什么
                pass
        except Exception as e:
            logger.warning("[宇树语音] commands 执行异常: %s", e, exc_info=True)
    # 答案算完了, 不再需要等 ack 任务; 但让它自己跑完别取消 (取消会留 warning)
    if not ack_task.done():
        ack_task.add_done_callback(lambda _t: None)

    if not answer:
        logger.info("[宇树语音] answer_engine 空结果, 走 LLM 在线兜底")
        answer = await _llm_fallback(text)
    if not answer:
        # LLM 也失败了 (网络/配额) 才播这句, 正常情况不会走到
        answer = "好的，我再查一下。"
    # 宇树没屏 — 把 OpenClaw/LLM 给出的"看屏幕"类措辞洗掉, 避免给用户错误指引
    answer = _strip_screen_hints_for_unitree(answer)

    logger.info("[宇树语音] 回答 (%d字): %s", len(answer), answer[:60])
    try:
        # 单段最大 200 字; 超长按标点切片依次播报
        for chunk in _split_for_tts(answer):
            await client.tts_play(chunk)
            # 粗略串行: 等上一段 ttsFinished 或最多等 chunk 长度 * 0.25s
            await asyncio.sleep(max(1.0, min(8.0, len(chunk) * 0.25)))
    except UnitreeGatewayError as e:
        logger.warning("[宇树语音] TTS 回播失败: %s", e)

    await _log_voice("asr_answer", text, answer)


async def _route_to_answer_engine_full(text: str) -> tuple[str, list]:
    """复用 V5 通用问答: classify_query + answer_engine.answer.

    返回 (reply_text, commands_chain). commands_chain 是 OpenClaw 拆出的多步动作链
    (复合指令模式), 调用方决定是否走 unitree_dispatch 执行。
    """
    from app.engine.world_state import get_world_state
    from app.engine.query_router import classify_query, TOUR_CONTROL
    from app.engine.answer_engine import answer

    ws = get_world_state()  # 单例; 宇树只读, 不主动写状态
    qr = classify_query(text, ws)
    qtype = qr.get("qtype", TOUR_CONTROL)
    slots = qr.get("slots", {}) or {}

    # TOUR_CONTROL 走老意图链 (导航/播放资源), 宇树不处理这些, 直接 fallback.
    if qtype == TOUR_CONTROL:
        # 仍给一次机会: 用 resolve_intent 看是不是确定性意图 (社交词等)
        return await _fallback_social(text), []

    try:
        result = await asyncio.wait_for(
            answer(qtype, text, slots, ws, UNITREE_ROBOT_ID),
            timeout=20.0,
        )
        result = result or {}
        cmds = ((result.get("extra") or {}).get("commands")) or []
        return result.get("answer", "") or "", cmds if isinstance(cmds, list) else []
    except asyncio.TimeoutError:
        logger.warning("[宇树语音] answer_engine 超时 qtype=%s text=%r", qtype, text)
        return "", []


# 兼容老调用入口: 仅返回字符串 reply
async def _route_to_answer_engine(text: str) -> str:
    reply, _ = await _route_to_answer_engine_full(text)
    return reply


_ACK_PHRASES = (
    "好的，稍等。",
    "嗯，让我想想。",
    "好的，我查一下。",
    "让我想一下。",
)
_ack_idx = {"i": 0}


async def _quick_ack(client, user_text: str):
    """在 LLM 思考期间先抛一句简短 ack, 压低体感延迟.
    规则命中不经过这里; 只有 _route_to_answer_engine 路径用."""
    i = _ack_idx["i"] % len(_ACK_PHRASES)
    _ack_idx["i"] += 1
    phrase = _ACK_PHRASES[i]
    try:
        await client.tts_play(phrase)
        logger.info("[宇树语音] ack: %s", phrase)
    except UnitreeGatewayError as e:
        logger.debug("[宇树语音] ack 发送失败(忽略): %s", e)
    except Exception:
        pass


# ========== 设备控制继承 (豹小秘同款 resolve_intent + ActionExecutor) ==========
# intent → (ActionExecutor 方法名, 成功回复, 失败回复)
_DEVICE_INTENT_MAP = {
    "lights_on":     ("lights_on",     "好，灯开了。",       "灯没开成功，设备可能离线，稍后再试。"),
    "lights_off":    ("lights_off",    "好，灯关了。",       "灯没关成功，设备可能离线，稍后再试。"),
    "all_on":        ("all_on",        "好，都打开了。",     "没能全部打开，部分设备可能离线。"),
    "all_off":       ("all_off",       "好，都关了。",       "没能全部关闭，部分设备可能离线。"),
    "ad_screen_on":  ("ad_screen_on",  "好，广告机开了。",   "广告机没开成功，稍后再试。"),
    "ad_screen_off": ("ad_screen_off", "好，广告机关了。",   "广告机没关成功，稍后再试。"),
    "go_charging":   ("go_charging",   "好的，它去充电了。", "没能安排去充电，请稍后再试。"),
}


def _might_be_device_control(text: str) -> bool:
    """粗过滤: 含这些关键字才进 resolve_intent, 省得对"今天天气"也跑整条意图链."""
    return any(k in text for k in ("灯", "广告机", "充电", "全开", "全关", "全部打开", "全部关"))


async def _try_device_control(client, text: str) -> bool:
    """复用 resolve_intent 识别设备控制意图, 命中则调 ActionExecutor 并通过宇树 TTS 播回执.
    返回 True = 已处理(调用方直接 return); False = 未命中设备类意图, 继续走通用问答."""
    try:
        from app.engine.intent_resolver import resolve_intent
        from app.engine.action_executor import ActionExecutor
        from app.engine.world_state import get_world_state
    except Exception as e:
        logger.warning("[宇树语音] 设备控制: 导入失败, 跳过: %s", e)
        return False

    ws = get_world_state()
    # 豹小秘的 resolve_intent 规则路径 <100ms, LLM 路径可能几秒; 设备词基本都是规则命中.
    # 3s 足够规则命中; 超时说明走到 LLM 分支了, 此时让位给下面的通用问答链, 避免双倍等待.
    try:
        ir = await asyncio.wait_for(resolve_intent(text, ws), timeout=3.0)
    except asyncio.TimeoutError:
        logger.info("[宇树语音] resolve_intent 3s 超时, 放弃设备控制路径 text=%r", text)
        return False
    except Exception as e:
        logger.warning("[宇树语音] resolve_intent 异常, 跳过设备控制: %s", e)
        return False

    intent = (ir or {}).get("intent", "")
    spec = _DEVICE_INTENT_MAP.get(intent)
    if not spec:
        logger.debug("[宇树语音] 非设备控制意图 intent=%s text=%r", intent, text)
        return False

    method_name, ok_reply, fail_reply = spec
    executor = ActionExecutor()  # 设备控制走 TCP 8989, 不依赖 robot_id
    fn = getattr(executor, method_name, None)
    if not fn:
        logger.warning("[宇树语音] ActionExecutor 无 %s 方法", method_name)
        return False

    logger.info("[宇树语音] 设备控制命中 intent=%s source=%s text=%r",
                intent, ir.get("source", "?"), text)
    ok = False
    try:
        ok = await asyncio.wait_for(fn(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("[宇树语音] 设备控制执行超时 intent=%s", intent)
    except Exception as e:
        logger.warning("[宇树语音] 设备控制执行异常 intent=%s: %s", intent, e, exc_info=True)

    reply = ok_reply if ok else fail_reply
    try:
        await client.tts_play(reply)
    except UnitreeGatewayError as e:
        logger.warning("[宇树语音] 设备控制播报失败: %s", e)
    except Exception:
        pass

    await _log_voice(f"asr_device:{intent}", text, f"{reply} [{'ok' if ok else 'fail'}]")
    return True


# ========== 流程触发词 → 跑 tour_scripts ==========
async def _try_run_script(client, text: str) -> bool:
    """命中 trigger_word 配置的流程脚本就启动 flow_executor。

    返回 True 表示已处理(调用方直接 return); False = 未命中, 走后面的 _try_tour_query / 问答。

    设计: 跟豹小秘的 SmartEngine 触发词路径同语义, 但宇树侧 robot_id=unitree_g1.
    flow_executor.start_flow 接收 robot_id 参数, 流程里 unitree_* 步骤会走 HTTP 网关,
    豹小秘 navigate/tts 步骤如果出现在宇树触发的流程里, 取决于豹小秘是否在线。
    """
    try:
        from app.engine.intent_resolver import resolve_intent
        from app.engine.flow_executor import start_flow
    except Exception as e:
        logger.warning("[宇树语音] 触发词检测: 导入失败, 跳过: %s", e)
        return False

    try:
        ir = await asyncio.wait_for(
            resolve_intent(text, None, skip_llm_fallback=True), timeout=3.0,
        )
    except asyncio.TimeoutError:
        return False
    except Exception:
        return False

    if not ir or ir.get("source") != "trigger":
        return False
    if ir.get("intent") != "start_tour":
        return False
    entities = ir.get("entities", {}) or {}
    script_id = entities.get("script_id")
    if not script_id:
        return False
    script_name = entities.get("script_name") or f"流程#{script_id}"
    logger.info("[宇树语音] 触发词命中, 启动流程: id=%s name=%s text=%r",
                script_id, script_name, text)
    try:
        result = await start_flow(int(script_id), UNITREE_ROBOT_ID)
    except Exception as e:
        logger.warning("[宇树语音] 启动流程异常: %s", e, exc_info=True)
        await _safe_tts(client, f"启动「{script_name}」时出错了, 您稍后再试。")
        return True
    status = (result or {}).get("status", "")
    if status not in ("started", "ok", "running"):
        msg = (result or {}).get("message") or "未知错误"
        logger.warning("[宇树语音] 启动流程失败: %s", msg)
        await _safe_tts(client, f"启动「{script_name}」没成功: {msg}")
        return True
    # 启动成功 — 流程内的步骤会自带 TTS / 动作, 这里不重复播报
    await _log_voice("asr_run_script", text, f"{script_name}:started")
    return True


# ========== 展厅查询 / 中控类意图 (跟豹小秘共用数据) ==========
# 这一组意图都是"查 DB + 调中控", 跟机器人本身无关, 宇树和豹小秘应当给出一致结果。
# 跟 _try_device_control 的区别: 这里不是开关灯泡, 而是浏览展项/资源/专场。
_TOUR_QUERY_INTENTS = {
    "list_exhibits", "list_exhibit_resources",
    "play_resource", "switch_special",
}


async def _try_tour_query(client, text: str) -> bool:
    """命中即处理: 列展项 / 列展项资源 / 切专场 / 播放资源。返回 True 表示已处理。

    实现刻意复用豹小秘的 _list_exhibits 使用同款 SQL 过滤(黑名单 + res_cnt>0),
    不另起一份, 保证两侧一致。
    """
    try:
        from app.engine.intent_resolver import resolve_intent
        from app.engine.world_state import get_world_state
    except Exception as e:
        logger.warning("[宇树语音] 展厅查询: 导入失败, 跳过: %s", e)
        return False

    ws = get_world_state()
    try:
        ir = await asyncio.wait_for(resolve_intent(text, ws, skip_llm_fallback=True), timeout=3.0)
    except asyncio.TimeoutError:
        return False
    except Exception:
        return False

    intent = (ir or {}).get("intent", "")
    if intent not in _TOUR_QUERY_INTENTS:
        return False

    entities = ir.get("entities", {}) or {}
    logger.info("[宇树语音] 展厅查询命中 intent=%s entities=%s text=%r",
                intent, entities, text)

    if intent == "list_exhibits":
        await _unitree_list_exhibits(client)
        return True

    if intent == "list_exhibit_resources":
        target = (entities.get("target") or "").strip()
        if not target:
            return False
        await _unitree_list_exhibit_resources(client, target)
        return True

    if intent == "switch_special":
        name = (entities.get("special_name") or "").strip()
        if not name:
            return False
        await _unitree_switch_special(client, name)
        return True

    if intent == "play_resource":
        # 用户必须给出明确"哪个展项+第几个", 否则没法发中控指令
        target = (entities.get("target") or "").strip()
        idx = int(entities.get("resource_index") or 1)
        if not target:
            # fallback: 看 world_state.exhibit.current_exhibit_name (豹小秘可能刚选过)
            try:
                target = (ws.exhibit.current_exhibit_name or "").strip()
            except Exception:
                target = ""
        if not target:
            # 没目标就不能发中控, fallback 让用户带展项名再说
            await _safe_tts(client, "您想看哪个展项的第几个内容? 比如说「播大岛台正面第一个」。")
            return True
        await _unitree_play_resource(client, target, idx)
        return True

    return False


async def _unitree_play_resource(client, exhibit_name: str, idx: int):
    """通过中控播放某展项的第 N 个资源 — 必发 TCP 8989 指令, 跟豹小秘走同款通道。"""
    from app.engine.unitree_dispatch import _play_resource_by_name
    try:
        await _play_resource_by_name(exhibit_name, idx)
        await _safe_tts(client, f"好的, 给您播{exhibit_name}的第{idx}个内容。")
        await _log_voice("asr_play_resource", exhibit_name, f"idx={idx}")
    except Exception as e:
        logger.warning("[宇树语音] play_resource 失败: %s", e)
        await _safe_tts(client, f"播放{exhibit_name}没成功, 稍后再试。")


async def _unitree_list_exhibits(client):
    """跟豹小秘 _list_exhibits 同款过滤, 用宇树 TTS 念展项列表。"""
    from app.database import get_db
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT e.id, e.name,
                      (SELECT COUNT(*) FROM special_exhibit_resources ser
                        WHERE ser.exhibit_id=e.id) AS res_cnt
               FROM exhibits e
               WHERE e.name NOT IN (
                   'cave全黑使用', '1123', 'GPP',
                   '信创测试机', '云猴智能机器人'
               )
               ORDER BY e.id"""
        )
    finally:
        await db.close()
    rows = [r for r in rows if int(r["res_cnt"] or 0) > 0]
    if not rows:
        await _safe_tts(client, "暂时没有可参观的展项。")
        return
    names = [r["name"] for r in rows]
    # 念全部 (一般展厅 ≤25 个, 单段 TTS 上限 200 字, 大多数情况一句够)
    text = f"展厅一共有{len(names)}个展项: " + "、".join(names) + "。您可以告诉我展项名称, 我帮您介绍。"
    await _safe_tts(client, text)
    await _log_voice("asr_list_exhibits", "", text)


async def _unitree_list_exhibit_resources(client, exhibit_name: str):
    """念某展项在当前专场下的资源列表。"""
    from app.database import get_db
    db = await get_db()
    try:
        ex_rows = await db.execute_fetchall(
            "SELECT id, name FROM exhibits WHERE name=? LIMIT 1", (exhibit_name,),
        )
        if not ex_rows:
            # 名字不准 — 走拼音/关键词模糊
            from app.engine.action_executor import ActionExecutor
            ex = await ActionExecutor().find_exhibit_by_name(exhibit_name)
            if not ex:
                await _safe_tts(client, f"我没找到「{exhibit_name}」这个展项, 您说一下完整名称?")
                return
            ex_id, real_name = ex["id"], ex["name"]
        else:
            ex_id, real_name = int(ex_rows[0]["id"]), ex_rows[0]["name"]
        sp_rows = await db.execute_fetchall(
            "SELECT value FROM system_config WHERE key='current_special_id'"
        )
        sp_id = int(sp_rows[0]["value"]) if sp_rows and sp_rows[0]["value"] else 0
        if not sp_id:
            await _safe_tts(client, f"当前没有激活的专场, {real_name}没法列出资源。")
            return
        res_rows = await db.execute_fetchall(
            """SELECT r.id, r.name FROM special_exhibit_resources ser
               JOIN resources r ON r.id = ser.resource_id
               WHERE ser.special_id=? AND ser.exhibit_id=?
               ORDER BY ser.sort_order, r.id""",
            (sp_id, ex_id),
        )
    finally:
        await db.close()

    if not res_rows:
        await _safe_tts(client, f"{real_name}当前专场下没有可播放的内容。")
        return
    names = [(r["name"] or f"内容{i+1}") for i, r in enumerate(res_rows)]
    text = f"{real_name}有{len(names)}个内容: " + "、".join(names[:8])
    if len(names) > 8:
        text += "等"
    text += "。您说想看第几个或者直接说内容名称。"
    await _safe_tts(client, text)
    await _log_voice("asr_list_resources", real_name, text)


async def _unitree_switch_special(client, special_name: str):
    """通过中控切换专场, 跟豹小秘共用 ActionExecutor 通道。

    用 find_special_by_name 模糊解析处理 ASR 谐音 + 后缀噪声:
    "思的转场" → "思德AI专场" 这种现场常见错配。
    """
    from app.engine.action_executor import ActionExecutor
    sp = await ActionExecutor().find_special_by_name(special_name)
    if not sp:
        await _safe_tts(client, f"我没找到「{special_name}」这个专场, 您再说一下完整名字?")
        return
    sp_id = int(sp["id"])
    sp_name = sp["name"]
    try:
        from app.engine.unitree_dispatch import _switch_special_by_id
        await _switch_special_by_id(sp_id)
        await _safe_tts(client, f"好的, 已为您切换到{sp_name}。")
        await _log_voice("asr_switch_special", special_name, sp_name)
    except Exception as e:
        logger.warning("[宇树语音] 切换专场失败: %s", e)
        await _safe_tts(client, f"切换{sp_name}没成功, 稍后再试。")


_SCREEN_HINT_RE = re.compile(
    r"(?:您|你)?可以?看(?:屏幕|大屏|这个屏|屏)上?(?:选|挑)?[，。!?！？]?|"
    r"屏幕(?:上)?(?:有|显示|可以选|展示|列出)[^，。!?！？]*[，。!?！？]?|"
    r"看(?:这个)?屏(?:幕)?选[^，。!?！？]*[，。!?！？]?"
)


def _strip_screen_hints_for_unitree(text: str) -> str:
    """剔除"请看屏幕选/屏幕上有..."这类不适合宇树的措辞 (宇树没屏)。
    保留主体内容, 仅删掉指向屏幕的子句; 都删完空了就退化成一句兜底。"""
    if not text:
        return text
    cleaned = _SCREEN_HINT_RE.sub("", text).strip(" ，。!?！？\t")
    if not cleaned:
        return "您可以直接告诉我您想看哪个。"
    return cleaned


async def _safe_tts(client, text: str):
    """宇树 TTS 安全包装, 异常不外抛, 防一句失败影响整条意图链。"""
    try:
        await client.tts_play(text)
    except UnitreeGatewayError as e:
        logger.warning("[宇树语音] tts 失败: %s", e)
    except Exception as e:
        logger.warning("[宇树语音] tts 异常: %s", e)


# listen_once 进行时, 挂起常规 handle_speech 自动应答, 否则用户问的"你是谁"
# 会同时被当作文本返回 + 机器人又自动答一句, 体验混乱.
_handle_speech_suspend: int = 0


def _is_handle_speech_suspended() -> bool:
    return _handle_speech_suspend > 0


# 正在跑的"收音时长自动停"任务, 手动再点"停" 或 再启动一次会先取消旧的避免叠加
_listen_duration_task: Optional[asyncio.Task] = None


async def _auto_stop_after(seconds: float):
    """计时到点后发 asr_stop, 让网关/APK 回到唤醒词模式."""
    try:
        await asyncio.sleep(seconds)
        client = await UnitreeClient.from_config(timeout=15)
        if not client:
            logger.info("[宇树语音] 自动停 ASR: 网关未启用, 跳过")
            return
        try:
            await client.asr_stop()
            logger.info("[宇树语音] 自动停 ASR: %.1fs 到点, 已回到唤醒词模式", seconds)
        except UnitreeGatewayError as e:
            logger.warning("[宇树语音] 自动停 ASR 失败: %s", e)
    except asyncio.CancelledError:
        logger.info("[宇树语音] 自动停 ASR 任务被取消")


async def asr_start_timed(listen_duration: float = 0.0) -> dict:
    """启动 ASR; 若 listen_duration>0, N 秒后自动停 (回唤醒词模式).

    listen_duration=0 → 普通启动, 等手动停 (原 asr.start 行为).
    listen_duration>0 → V5 侧计时到点自动停; 同时 payload 里带 listen_duration 给网关,
                        若未来网关原生支持该字段, 前端/流程无需再改.
    """
    global _listen_duration_task
    client = await UnitreeClient.from_config(timeout=30)
    if not client:
        return {"ok": False, "error": "网关未启用"}

    listen_duration = max(0.0, float(listen_duration or 0))
    # 参数带 listen_duration, 对当前网关是冗余字段 (会被忽略), 对未来支持的网关即时生效
    try:
        body = {"listen_duration": listen_duration} if listen_duration > 0 else None
        await client._call("POST", "/api/asr", json_body=body)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "ASR 启动超时 (>30s, 网关 funASR 加载慢)"}
    except UnitreeGatewayError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.warning("[宇树语音] asr_start_timed 异常: %s", e, exc_info=True)
        return {"ok": False, "error": str(e)}

    # 先取消旧的自动停任务, 再按需起新的
    if _listen_duration_task and not _listen_duration_task.done():
        _listen_duration_task.cancel()
        _listen_duration_task = None
    if listen_duration > 0:
        _listen_duration_task = asyncio.create_task(_auto_stop_after(listen_duration))
        logger.info("[宇树语音] ASR 已启动, %.1fs 后自动停", listen_duration)
    else:
        logger.info("[宇树语音] ASR 已启动 (无计时, 手动停)")

    return {"ok": True, "listen_duration": listen_duration}


async def asr_stop_cancel_timer() -> dict:
    """手动停 ASR 时顺手取消自动停任务."""
    global _listen_duration_task
    if _listen_duration_task and not _listen_duration_task.done():
        _listen_duration_task.cancel()
        _listen_duration_task = None

    client = await UnitreeClient.from_config(timeout=15)
    if not client:
        return {"ok": False, "error": "网关未启用"}
    try:
        await client.asr_stop()
    except UnitreeGatewayError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


async def asr_listen_once(duration: float = 10.0) -> dict:
    """等待下一条 asr 识别事件 (最长 duration 秒), 期间挂起 handle_speech 避免重复应答.

    前提: ASR 已通过 🎧 启动 ASR 按钮 或流程的 asr.start 步骤启动 (网关 funASR 模型已加载)。
    本函数只订阅"下一条文本", 不自行 start/stop, 避免触碰网关 ASR 状态.

    返回: {"text": str, "timed_out": bool, "elapsed_ms": int}
    """
    import time as _t
    from app.engine.unitree_events import wait_for_event

    duration = max(1.0, float(duration or 10.0))
    start = _t.time()

    global _handle_speech_suspend
    _handle_speech_suspend += 1
    # 先注册 waiter 再开始等, 保证不丢 race (event 先到)
    fut = wait_for_event("asr:next", timeout=duration)
    text = ""
    timed_out = False
    try:
        payload = await asyncio.wait_for(fut, timeout=duration)
        text = str((payload or {}).get("text") or "").strip()
        logger.info("[宇树语音] asr_listen_once 收到 text=%r", text)
    except asyncio.TimeoutError:
        timed_out = True
        logger.info("[宇树语音] asr_listen_once 超时 %.1fs 无识别结果", duration)
        if not fut.done():
            fut.cancel()
    finally:
        _handle_speech_suspend = max(0, _handle_speech_suspend - 1)

    elapsed_ms = int((_t.time() - start) * 1000)
    await _log_voice("asr_listen_once",
                     f"duration={duration}s",
                     (text or "(超时)") + f" | {elapsed_ms}ms")
    return {"text": text, "timed_out": timed_out, "elapsed_ms": elapsed_ms}


async def handle_nav_arrived(task_id: str, params: dict) -> None:
    """navTaskFinished (SUCCESS) 回调: 查 (map_id, point_id) → unitree_point_bindings,
    命中展项/展区就用宇树 TTS 播报到达+展项名. 流程内下发的导航没登记, 这里查不到自然跳过."""
    from app.engine.unitree_events import pop_nav_target
    target = pop_nav_target(task_id)
    if not target:
        logger.info("[宇树导航] task_id=%s 未登记 (可能是流程内导航或已过期), 跳过绑定查询", task_id)
        return
    map_id = target["map_id"]
    point_id = target["point_id"]

    try:
        from app.database import get_db
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                """SELECT pb.bind_type, pb.bind_target_id,
                          CASE pb.bind_type
                              WHEN 'exhibit' THEN (SELECT name FROM exhibits WHERE id=pb.bind_target_id)
                              WHEN 'area'    THEN (SELECT name FROM areas    WHERE id=pb.bind_target_id)
                          END AS target_name,
                          mp.point_name
                   FROM unitree_point_bindings pb
                   LEFT JOIN unitree_map_points mp ON mp.map_id = pb.map_id AND mp.point_id = pb.point_id
                   WHERE pb.map_id=? AND pb.point_id=?
                   LIMIT 1""",
                (str(map_id), int(point_id)),
            )
        finally:
            await db.close()
    except Exception as e:
        logger.warning("[宇树导航] 绑定查询失败: %s", e)
        return

    if not rows:
        logger.info("[宇树导航] 点位 %s:%s 无绑定, 到达不播报", map_id, point_id)
        return
    row = dict(rows[0])
    target_name = (row.get("target_name") or "").strip()
    if not target_name:
        return

    reply = f"到达{target_name}。"
    logger.info("[宇树导航] 到达播报 map=%s point=%s → %s", map_id, point_id, reply)

    client = await UnitreeClient.from_config(timeout=10)
    if not client:
        return
    try:
        await client.tts_play(reply)
    except UnitreeGatewayError as e:
        logger.warning("[宇树导航] 到达播报失败: %s", e)
    except Exception:
        pass
    await _log_voice("nav_arrived", f"{map_id}:{point_id}", reply)


_LLM_FALLBACK_SYS = (
    "你是一位展厅导览机器人, 正在现场回答参观者的问题。"
    "要求: 1) 直接给口语化答案, 不要寒暄或反问; "
    "2) 控制在 80 字以内; 3) 不要用列表/Markdown; "
    "4) 如果问题与展厅无关, 也按常识简短回答, 不要说'我不知道'或'无法回答'。"
)


async def _llm_fallback(text: str) -> str:
    """answer_engine 超时/为空时, 直接调 LLM 在线兜底一个答案, 避免对用户说'抱歉我没听明白'."""
    try:
        from app.engine.llm import chat_completion
        reply = await asyncio.wait_for(
            chat_completion(
                messages=[{"role": "user", "content": text}],
                system_prompt=_LLM_FALLBACK_SYS,
                max_tokens=256,
                temperature=0.6,
            ),
            timeout=15.0,
        )
        return (reply or "").strip()
    except asyncio.TimeoutError:
        logger.warning("[宇树语音] LLM 兜底超时 text=%r", text)
        return ""
    except Exception as e:
        logger.warning("[宇树语音] LLM 兜底异常: %s", e, exc_info=True)
        return ""


async def _fallback_social(text: str) -> str:
    """TOUR_CONTROL 类 (导航/播放) 宇树不介入; 尝试社交问答, 否则交白."""
    try:
        from app.engine.retrievers import social_retriever
        r = await social_retriever.retrieve(text, {}, None, UNITREE_ROBOT_ID)
        return (r or {}).get("answer", "") or ""
    except Exception:
        return ""


def _split_for_tts(text: str, limit: int = 200) -> list[str]:
    """TTS 单次最大 200 字, 按标点切; 参考 §6.1。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts = re.split(r"(?<=[。！？；!?;])", text)
    out, buf = [], ""
    for p in parts:
        if not p:
            continue
        if len(buf) + len(p) <= limit:
            buf += p
            continue
        if buf:
            out.append(buf)
        if len(p) <= limit:
            buf = p
        else:
            # 超长句按逗号再切
            for s in re.split(r"(?<=[，、,])", p):
                if len(buf) + len(s) <= limit:
                    buf += s
                else:
                    if buf:
                        out.append(buf)
                    buf = s
    if buf:
        out.append(buf)
    return [x for x in out if x]


async def _log_voice(action: str, user_text: str, bot_text: str):
    try:
        from app.database import get_db
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO execution_logs
                     (log_type, source, target, action, detail, status, created_at)
                   VALUES ('unitree_voice', ?, 'hub', ?, ?, 'ok', datetime('now','localtime'))""",
                (UNITREE_ROBOT_ID, action,
                 (user_text[:200] + ((" | " + bot_text[:400]) if bot_text else ""))),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        logger.debug("[宇树语音] 日志写入失败: %s", e)
