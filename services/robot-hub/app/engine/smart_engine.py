"""
SmartEngine — 意图驱动的智能接待引擎

替代旧 flow_executor 的线性执行，采用状态机+意图循环：
IDLE → WELCOME → EXPLANATION → ASK_NEXT(循环) → END
DISCUSSING → DISCUSS_DONE (话题讨论)

核心设计：
1. 每次收到语音(evt_speech_result)，经过意图识别 → 状态机决策 → 执行动作
2. ASK_NEXT 状态不退出，持续循环直到用户说"结束"
3. 设备控制意图(lights_on等)随时可执行，不干扰对话状态
4. 自干扰过滤：机器人说话时丢弃ASR结果
5. 无响应超时：连续3次无响应回IDLE
6. DISCUSSING 状态：机器人+数字人交替播报讨论话题，结束后询问继续
"""

import asyncio
import json
import logging
import re
import time

from app.database import get_db
from app.engine.world_state import WorldState, get_world_state
from app.engine.intent_resolver import resolve_intent, _parse_resource_index, _to_pinyin
from app.engine.action_executor import ActionExecutor
from app.engine.dialogue_arbiter import arbitrate, ActionDecision, resolve_anaphora


def _parse_resource_index_safe(text: str) -> int:
    """安全解析资源序号，失败返回0"""
    try:
        return _parse_resource_index(text)
    except Exception:
        return 0


def _fuzzy_find_by_name(query: str, rows: list, name_field: str = "name") -> dict | None:
    """
    通用模糊匹配：从数据库行列表中找最接近 query 的记录。
    匹配策略（按优先级）：
    1. 精确包含：query在name中 或 name在query中
    2. 拼音包含：query拼音在name拼音中 或反过来
    3. 拼音最长公共子串 >= 4
    返回匹配到的行(dict)或None。
    """
    if not query or not rows:
        return None

    query_clean = query.strip()
    query_py = _to_pinyin(query_clean)

    # 1. 精确包含匹配
    for r in rows:
        name = r[name_field] if isinstance(r, dict) else r[name_field]
        if query_clean in name or name in query_clean:
            return r

    # 2. 拼音包含匹配
    best = None
    best_score = 0
    for r in rows:
        name = r[name_field] if isinstance(r, dict) else r[name_field]
        name_py = _to_pinyin(name)
        if query_py and name_py:
            if query_py in name_py or name_py in query_py:
                score = len(query_py) + len(name_py)
                if score > best_score:
                    best_score = score
                    best = r
    if best:
        return best

    # 3. 拼音最长公共子串匹配
    best = None
    best_score = 0
    for r in rows:
        name = r[name_field] if isinstance(r, dict) else r[name_field]
        name_py = _to_pinyin(name)
        if query_py and name_py:
            common = 0
            for i in range(len(query_py)):
                for j in range(i + 2, len(query_py) + 1):
                    if query_py[i:j] in name_py:
                        common = max(common, j - i)
            if common >= 4 and common > best_score:
                best_score = common
                best = r
    return best


logger = logging.getLogger("hub.engine.smart_engine")

# 已知设备状态回声短语 — 这些是机器人/上位机自身播报状态时被本机 ASR 抓回来的,
# 不是真实用户输入, 一律丢弃, 避免触发完整意图链。
_DEVICE_ECHO_PHRASES = (
    "启动持续拾音模式成功",
    "停止持续拾音模式成功",
    "持续拾音模式",
    "启动持续拾音",
    "停止持续拾音",
    "唤醒成功",
    "拾音已开启",
    "拾音已关闭",
)

# ── 触发词字面缓存 ──
# 用户诉求: 触发词必须**无条件**优先执行, 不能被自干扰/过短/去重过滤掉。
# 现场场景: 机器人正说欢迎语时用户喊"开始讲解" → 自干扰过滤会丢; 短时重复
# 喊"开始讲解" → 去重过滤会丢. 都不该发生。
_trigger_phrases_cache: set[str] = set()
_trigger_phrases_cache_at: float = 0.0
_TRIGGER_CACHE_TTL = 60.0  # 60s 刷新一次, 后台改 trigger_word 一分钟内会生效


async def _load_trigger_phrases() -> set[str]:
    global _trigger_phrases_cache, _trigger_phrases_cache_at
    if time.time() - _trigger_phrases_cache_at < _TRIGGER_CACHE_TTL and _trigger_phrases_cache:
        return _trigger_phrases_cache
    try:
        from app.database import get_db
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT trigger_word FROM tour_scripts WHERE enabled=1 AND trigger_word!=''"
            )
            phrases: set[str] = set()
            for r in rows:
                for p in (r["trigger_word"] or "").split("|"):
                    p = p.strip()
                    if p:
                        phrases.add(p)
            _trigger_phrases_cache = phrases
            _trigger_phrases_cache_at = time.time()
        finally:
            await db.close()
    except Exception as e:
        logger.warning("[SmartEngine] 触发词缓存加载失败: %s", e)
    return _trigger_phrases_cache


def _is_trigger_phrase(text: str, phrases: set[str]) -> bool:
    """字面/子串比对: text 完全等于触发词或包含触发词. 不走拼音(在 resolve_intent 里有)."""
    if not text or not phrases:
        return False
    t = text.strip()
    for p in phrases:
        if p == t or p in t:
            return True
    # 不在这里把“第一个/第二个/第三个”当强触发。
    # 三种参观方式的短回复只应在 _awaiting_tour_mode_choice 上下文里处理；
    # 否则资源列表里的“第N个”会被当触发词，清空 browse.mode，导致无法播放当前展区资源。
    return False


# 硬打断短语 — 用户说这些时, 立刻 stop_flow + cancel TTS, 不走完整 intent/arbiter 链。
# 适用场景: 流程在 TTS / 导航 / 等待回调中, 用户想立即停止当前活动. 不会 transition 到 END,
# 流程能停下让用户继续问别的, 不强制收尾"感谢您的参观"。
# 严格用 "==" 字面量 + 加少量带语气词变体, 避免误打断"取消订阅咨询"这种偶然包含的句子。
_HARD_CANCEL_PHRASES = frozenset({
    "取消", "取消吧", "取消流程", "取消导航", "取消讲解", "取消播放",
    "停下", "停一下", "停止", "停下来", "先停", "先停下",
    "别讲了", "别说了", "别播了", "不要讲了", "不要说了",
    "打断", "打断一下",
})


def _strip_terminal_speech_punctuation(text: str) -> str:
    """Normalize ASR final punctuation for exact command matching only."""
    return (text or "").strip().rstrip("。.！!？?")

# ============================================================
# SmartEngine 实例
# ============================================================
_engines: dict = {}  # robot_id -> SmartEngine


def get_smart_engine(robot_id: str) -> "SmartEngine | None":
    return _engines.get(robot_id)


def create_smart_engine(robot_id: str, conn=None) -> "SmartEngine":
    if robot_id not in _engines:
        engine = SmartEngine(robot_id, conn)
        _engines[robot_id] = engine
    else:
        previous = _engines[robot_id]
        if previous.conn is not conn:
            runtime = getattr(previous, "_teaching_runtime", None)
            if runtime:
                runtime.clear("connection_changed")
            previous._teaching_runtime = None
            previous._invalidate_legacy_qa_turn("connection_changed")
        previous.conn = conn
    return _engines[robot_id]


def destroy_smart_engine(robot_id: str):
    _engines.pop(robot_id, None)


def _normalize_tour_mode_choice(text: str) -> str:
    """识别三种参观方式选择，兼容 ASR 把“种”听成“组/个/项/号”。返回 preset/smart/free/空。"""
    t = (text or "").strip().replace(" ", "")
    if not t:
        return ""
    if re.fullmatch(r"第?[一1](?:种|组|个|项|号)?|方式[一1]|预设路线|固定路线|后台路线|预置路线", t):
        return "preset"
    if re.fullmatch(r"第?[二2两](?:种|组|个|项|号)?|方式[二2两]|智能规划|智能路线", t):
        return "smart"
    if re.fullmatch(r"第?[三3](?:种|组|个|项|号)?|方式[三3]|自由选择|自由参观|自由看|自己选|我自己选|自主选择", t):
        return "free"
    return ""


class SmartEngine:
    """
    智能接待引擎 — 意图驱动状态机

    状态:
    - IDLE: 待机，等待唤醒或人脸触发
    - WELCOME: 迎宾，主动打招呼
    - EXPLANATION: 讲解展项
    - ASK_NEXT: 询问下一步（循环状态）
    - DISCUSSING: 话题讨论中
    - DISCUSS_DONE: 讨论结束，等待用户决定是否继续
    - END: 结束参观
    """

    def __init__(self, robot_id: str, conn=None):
        self.robot_id = robot_id
        self.conn = conn
        self.ws = get_world_state()
        self.executor = ActionExecutor(robot_id)
        self._plan = []          # 参观路线 [{exhibit_id, name, position, narration}, ...]
        self._plan_index = 0     # 当前路线位置
        self._is_speaking = False
        self._tts_lock = asyncio.Lock()       # TTS串行锁，保证同一时刻只有一个播报
        self._tts_cancel = asyncio.Event()    # 取消排队TTS信号
        self._last_listen_time = 0
        self._listen_mode = "single"  # single/continuous
        self._discussion = None         # DiscussionSession 实例
        self._was_flow_running = False  # 讨论前是否有流程在执行
        self._handle_lock = asyncio.Lock()  # 单消费者保护: 同一 robot 同时只处理一条 speech
        self._pending_tasks: list = []      # 本引擎 create_task 的可取消句柄
        self._memory_followup = None        # 访客记忆查询后的待确认动作 {action, exhibit_id, exhibit_name, expires_at}
        # 对话暂停态: dispatch 在 wait_until_idle 内时设 True, 该期间用户输入只走简化 chitchat
        # 应答 (LLM 直答, 不启新 commands chain), 避免和外层 dispatch 冲突。
        self._in_dialog_pause = False
        # bot 最近一次说完话的时间. wait_until_idle 用它做"等用户消化+答"的基线 —
        # 否则 bot 在 dialog_pause 内又抛新问句, 用户答的窗口被旧的 last_input_time 遮挡。
        self._last_bot_speak_at = 0.0
        # 拿不到 _handle_lock 时缓存最新 ASR, 当前 turn 结束后自动重试一次。
        # 防止 "上条语音处理中, 丢弃并发 ASR" 把用户真任务吞掉。
        self._queued_asr: str | None = None
        self._queued_asr_raw: dict | None = None
        # 新系统 Gateway 问答轮次令牌。
        self._gateway_turn_seq = 0
        self._active_gateway_turn_token: str | None = None
        # 老系统问答本地轮次令牌：硬打断或触发词抢占后，晚到回复不播报。
        self._legacy_qa_turn_seq = 0
        self._active_legacy_qa_turn_token: str | None = None
        # 老系统明确拒答后的工作人员教学轮次（每个 SmartEngine/机器人独立）。
        self._legacy_teaching_session = None
        self._last_legacy_teaching_session = None
        self._legacy_teaching_lock = asyncio.Lock()
        self._legacy_teaching_timeout_task: asyncio.Task | None = None
        # ── auto_plan 状态 (Phase F/G) ──
        self._auto_plan = None                      # type: Any | None, 活跃的 TourPlan
        self._auto_plan_idx: int = 0                # 下一步待执行 step 下标
        self._auto_plan_completed_exhibits: set[int] = set()
        self._auto_plan_start_ts: float = 0.0
        self._photo_followup = None  # {exhibit_id, exhibit_name, expires_at}
        self._awaiting_smart_plan_minutes = False  # 第二种智能规划：等待用户回答分钟数
        self._awaiting_tour_mode_choice = False    # 仅接待区欢迎后，等待用户选择三种参观方式
        self._default_smart_plan_minutes = 15      # 用户说“随便/都可以”时，智能规划和兜底都默认 15 分钟

    # ============================================================
    # 主入口：处理语音识别结果
    # ============================================================
    async def handle_speech(self, text: str, raw_data: dict = None):
        """
        处理机器人上传的语音识别结果

        流程: 过滤 → 意图识别 → 仲裁层决策 → 执行/确认/澄清/回答 → 诊断日志
        """
        if not text or not text.strip():
            return
        # 包括教学片段在内，所有有效 ASR 都应刷新交互时间。
        self.ws.recent.last_input_time = time.time()
        raw_data = raw_data or {}
        teaching_turn_id = str(raw_data.get("teachingTurnId") or "").strip()
        active_teaching = getattr(self, "_legacy_teaching_session", None)
        # 教学语音必须先于硬打断和意图解析处理，避免工作人员回答里的
        # “开灯/导航/停止”等字样触发机器人动作。
        if teaching_turn_id:
            if not active_teaching or teaching_turn_id != active_teaching.turn_id:
                logger.warning(
                    "[SmartEngine] 丢弃失效教学语音 robot=%s turn=%s",
                    self.robot_id,
                    teaching_turn_id,
                )
                return
            await self._handle_legacy_teaching_speech(text, teaching_turn_id, raw_data.get("fragmentSeq"))
            return
        self._invalidate_legacy_qa_turn("new_speech")
        if active_teaching is not None:
            # 未带教学轮次的合法新 ASR 视为新一轮抢占，旧教学不得继续提交。
            self._finish_legacy_teaching_state("CANCELLED", "new_takeover")
        # 硬打断: "取消/停下/别讲了" 等 — 必须绕过 _handle_lock 立即生效。
        # 当前 turn 在长 TTS / 导航等回调期间持有锁, 普通 ASR 会被缓存到 _queued_asr,
        # 等当前 turn 结束才处理 — 这样用户根本停不下机器人。所以这里前置到入口,
        # 字面量精确比对 (rstrip 标点), 命中就立即 _interrupt_current_activity 并短确认 TTS,
        # 不走 intent/arbiter 链, 不结束参观状态。
        _hc_clean = _strip_terminal_speech_punctuation(text)
        if _hc_clean in _HARD_CANCEL_PHRASES:
            logger.info("[SmartEngine] 硬打断(绕锁): '%s' lock_held=%s", _hc_clean, self._handle_lock.locked())
            self._invalidate_gateway_turn(reason=f"hard_cancel:{_hc_clean}")
            self._invalidate_legacy_qa_turn(reason=f"hard_cancel:{_hc_clean}")
            # 硬打断位于浏览处理之前并会直接 return。若不在这里清理，
            # “取消/停下”等无法退出浏览，机器人仍会把后续普通问题
            # 当成展项名称或序号。
            if self.ws.browse.mode:
                logger.info("[SmartEngine] 硬打断同时退出浏览模式: %s", self.ws.browse.mode)
                self.ws.reset_browse()
            try:
                interrupted = await self._interrupt_current_activity(reason=f"hard_cancel:{_hc_clean}")
            except Exception as e:
                logger.warning("[SmartEngine] 硬打断 _interrupt 异常: %s", e)
                interrupted = False
            # 清掉 _queued_asr — 用户要停, 之前缓存的"下一句"也作废, 避免打断后又自动跑队列。
            if self._queued_asr:
                logger.info("[SmartEngine] 硬打断顺带丢弃 queued ASR: %r", self._queued_asr[:30])
                self._queued_asr = None
                self._queued_asr_raw = None
            self._tts_cancel.clear()  # 让接下来的确认 TTS 能正常出
            ack = "好的，已停下。您接下来想做什么？" if interrupted else "好的。"
            try:
                await self._speak(ack)
            except Exception as e:
                logger.warning("[SmartEngine] 硬打断 ack TTS 异常: %s", e)
            try:
                from app.engine.dialogue_arbiter import ActionDecision
                self._log_turn(text.strip(), "hard_cancel", 0.99,
                               ActionDecision(action="execute", reason=f"hard_cancel:{_hc_clean}"),
                               self.ws.dialog.engine_state, "hard_cancel",
                               bool(interrupted), False)
            except Exception:
                pass
            return

        # 触发词无条件优先: 锁占用中 → 强制打断当前活动, 让触发词尽快上位。
        # 不发 ack TTS (触发词本身会带流程开场白). 触发词放 queue 等当前 turn 结束自动 retry.
        try:
            _trigger_phrases = await _load_trigger_phrases()
            _mode_choice_hit = bool(getattr(self, "_awaiting_tour_mode_choice", False)
                                    and not self.ws.browse.mode
                                    and _normalize_tour_mode_choice(text.strip()))
            if _is_trigger_phrase(text.strip(), _trigger_phrases) or _mode_choice_hit:
                if self._handle_lock.locked():
                    logger.info("[SmartEngine] 触发词强占: 锁占用中, 中断当前活动: %r",
                                text.strip()[:30])
                    self._invalidate_gateway_turn(reason="trigger_priority")
                    self._invalidate_legacy_qa_turn(reason="trigger_priority")
                    try:
                        await self._interrupt_current_activity(reason="trigger_priority")
                    except Exception as e:
                        logger.warning("[SmartEngine] 触发词强占 _interrupt 异常: %s", e)
                    # 强制把触发词塞到 queue, 等当前 turn 因为 _interrupt 尽快结束就被处理
                    self._queued_asr = text
                    self._queued_asr_raw = raw_data
                    return
                # 锁未占用 → 落到下面正常路径处理
        except Exception as e:
            logger.debug("[SmartEngine] 触发词检查异常(忽略): %s", e)

        # 对话暂停态: 上层 dispatch 正卡在 wait_until_idle 等用户答完。
        # 这种情况下用户输入只走简化 chitchat 回应, 不再触发新的 commands chain
        # (避免 OpenClaw 把"什么都可以"当成原任务延续, 重新规划一遍 nav)。
        if self._in_dialog_pause:
            async with self._handle_lock:
                await self._dialog_pause_reply(text.strip())
            return
        # 单消费者保护: 同一 robot 的上一条 speech 还没处理完, 新 ASR 缓存到 queue,
        # 等当前 turn 结束自动重试一次. 之前是直接丢弃, 会把用户真任务吞掉。
        # 同一 turn 内多条 ASR 进来, 后到的覆盖前面 (取最新意图).
        if self._handle_lock.locked():
            self._queued_asr = text
            self._queued_asr_raw = raw_data
            logger.info("[SmartEngine] 上条语音处理中, 缓存待重试: '%s'", text.strip()[:30])
            return
        async with self._handle_lock:
            await self._handle_speech_impl(text, raw_data)
        # 当前 turn 结束: 看 queue 是否有新输入, 有就异步触发新一轮处理
        # (异步避免栈嵌套, 也让 evt_tts_done 等附带事件先返回)
        if self._queued_asr and self._queued_asr != text:
            qtext = self._queued_asr
            qraw = self._queued_asr_raw
            self._queued_asr = None
            self._queued_asr_raw = None
            logger.info("[SmartEngine] 处理缓存中的 ASR: %r", qtext[:30])
            asyncio.create_task(self.handle_speech(qtext, qraw))
        else:
            self._queued_asr = None
            self._queued_asr_raw = None

    async def _dialog_pause_reply(self, text: str):
        """对话暂停期间(wait_until_idle 内)的简化回应:
        - 拿最近一次 bot 问句当上下文
        - 调 LLM 给一句简短自然的"听到了"风格应答 (≤30 字)
        - 直接 _speak 出来, 不启动 commands dispatch (避免和外层 dispatch 冲突)
        - 失败/超时 兜底一句"好的, 听到啦。"
        """
        if not text:
            return
        clean = text.strip()
        last_q = (self.ws.dialog.last_bot_reply or "").strip()
        # 用户输入也写入 last_input_text 帮去重
        try:
            self.ws.record_input(clean)
        except Exception:
            pass
        # ws.add_to_history 让历史可查
        try:
            self.ws.add_to_history("user", clean)
        except Exception:
            pass
        logger.info("[SmartEngine] dialog_pause 简化回应: user=%r last_q=%r",
                    clean[:40], last_q[:40])
        try:
            from app.engine.llm import chat_completion
            sys_prompt = (
                "你是导览机器人, 正在和用户做简短问答收尾。回应必须 ≤25 字, 自然口语化, "
                "**禁止**: 反问/追问(不要带问号)、规划路线、列举展项、重复用户原任务、提建议让用户选。"
                "**只**承接, 表示听到了/收到了。例如: '好的, 我帮您记下了' / '嗯, 知道了' / "
                "'好嘞, 一会儿告诉他' / '行, 都听您的'。"
            )
            user_prompt = f"刚才我(机器人)问: {last_q!r}\n用户答: {clean!r}\n请生成一句简短自然回应:"
            ans = await chat_completion(
                messages=[{"role": "user", "content": user_prompt}],
                system_prompt=sys_prompt,
                max_tokens=60,  # 简短承接 25 字够; 缩到 60 tokens
                temperature=0.3,  # 低温度避免反问/发散
            )
            ans = (ans or "").strip().split("\n")[0][:60]
            if not ans:
                ans = "好的，听到啦。"
        except Exception as e:
            logger.warning("[SmartEngine] dialog_pause LLM 失败: %s", e)
            ans = "好的，听到啦。"
        await self._speak(ans)

    async def _handle_speech_impl(self, text: str, raw_data: dict = None):
        # 健康检查：防卡死
        await self._check_health()

        clean = text.strip()
        # 本轮浏览模式被打断 → 在 QA 答完后追问"要继续吗"。仅当前 call 内有效, 不跨轮。
        _suspend_resume_prompt = False

        # 设备状态回声过滤: 机器人侧 ASR 把自身/上位机的状态提示当成用户语音上报,
        # (例: "启动持续拾音模式成功" / "停止持续拾音模式成功") 会被走完整套意图链
        # 调 LLM、出闲聊回答, 既慢又混乱。这里在最前面截断。
        if any(p in clean for p in _DEVICE_ECHO_PHRASES):
            logger.info("[SmartEngine] 设备回声过滤: '%s'", clean)
            return

        # Teaching follow-ups must precede general intent/context/LLM routing.
        # The bounded semantic classifier chooses an action; it never invents the replay.
        _teaching_runtime = getattr(self, "_teaching_runtime", None)
        if _teaching_runtime and await _teaching_runtime.handle_followup(clean):
            return
        if not _teaching_runtime:
            from app.engine.teaching_intent import explicit_learning_request
            if explicit_learning_request(clean):
                await self._speak("这轮还没有可复述的现场补充，请先教我一次。")
                return

        # 触发词命中 → 无条件优先, 跳过自干扰/过短/去重三层过滤. 保证用户说"开始讲解"
        # 等触发词时永远能进流程, 哪怕机器人正说话或 1.5s 内重复喊。
        _trigger_phrases = await _load_trigger_phrases()
        _is_trigger_hit = _is_trigger_phrase(clean, _trigger_phrases)
        # 仅在接待区“三种参观方式”问句之后，把“第一种/第二组/第三个”当作模式选择短回复。
        # browse.mode 活跃时绝不能把“第N个”升级成触发词，否则会清空资源列表状态。
        _mode_choice_hit = bool(getattr(self, "_awaiting_tour_mode_choice", False)
                                and not self.ws.browse.mode
                                and _normalize_tour_mode_choice(clean))
        if _mode_choice_hit:
            _is_trigger_hit = True
        if _is_trigger_hit:
            logger.info("[SmartEngine] 触发词字面命中: '%s' → 跳过 自干扰/过短/去重 过滤", clean)

        # 趣味互动/拍照快速识别：这些功能可在任意环节触发，需要绕过自干扰、过短和去重过滤。
        # 拍照会进入独立流程（导航到拍照点 → 留时间摆姿势 → 倒计时 → 等 APK 回 evt_photo_*）。
        _fun_intent_result = None
        _fun_intent = ""
        try:
            _maybe_fun = await resolve_intent(clean, self.ws, skip_llm_fallback=True)
            if (_maybe_fun.get("intent") in ("play_music", "stop_music", "next_song", "volume_up", "volume_down", "take_photo")
                    and float(_maybe_fun.get("confidence", 0)) >= 0.8):
                _fun_intent_result = _maybe_fun
                _fun_intent = _maybe_fun.get("intent", "")
                logger.info("[SmartEngine] 趣味互动快速命中: text=%r intent=%s", clean, _fun_intent)
        except Exception as e:
            logger.debug("[SmartEngine] 趣味互动快速识别跳过: %s", e)
        _is_fun_hit = bool(_fun_intent)

        # followup 激活时放宽过滤: 短词 yes/no 答复(要/好/嗯/不/行)必须能通过, 否则 followup 永远收不到
        _reception_followup_active = False
        try:
            from app.engine.visitor_service import get_person_state
            _ps = get_person_state(self.robot_id)
            _reception_followup_active = float(_ps.get("reception_followup_until") or 0.0) > time.time()
        except Exception:
            _reception_followup_active = False

        _followup_active = bool(
            (self._memory_followup and self._memory_followup.get("expires_at", 0) > time.time())
            or (self._photo_followup and self._photo_followup.get("expires_at", 0) > time.time())
            or _reception_followup_active
        )

        # 自干扰过滤：机器人正在说话时丢弃ASR (followup 激活时缩短窗口, 避免用户简短答复被吞)
        if self._is_speaking and not _is_trigger_hit and not _is_fun_hit:
            speaking_elapsed = time.time() - self.ws.dialog.speaking_started_at if self.ws.dialog.is_speaking else 99
            self_interfer_window = 1.0 if _followup_active else 3.0
            if speaking_elapsed < self_interfer_window:
                logger.info("[SmartEngine] 自干扰过滤: 丢弃ASR (机器人说话中 %.1fs, window=%.1fs)",
                            speaking_elapsed, self_interfer_window)
                return

        # 过短结果过滤 (followup 激活时保留 1 字符答复: 要/好/嗯/不/行;
        # browse 模式下放宽以接受单数字序号 "1"/"2"/"3")
        _browse_active = bool(self.ws.browse.mode)
        min_len = 1 if (_followup_active or _browse_active) else 2
        if (len(clean) < min_len or not clean) and not _is_trigger_hit and not _is_fun_hit:
            logger.info("[SmartEngine] 过短结果过滤: '%s' (min=%d)", clean, min_len)
            return

        # ── 保护1：去重 — 1.5秒内完全相同的输入丢弃 ──
        # (旧 3s 窗口对正常用户重复说一句话太长, 1.5s 已够防 evt_speech_result 双触发)
        # 触发词不去重: 用户连续喊两次"开始讲解" 也要保证第二次能命中, 不能因为 ASR 双发把它过滤掉
        if not _is_trigger_hit and not _is_fun_hit and self.ws.is_duplicate_input(clean, window=1.5):
            logger.info("[SmartEngine] 去重过滤: '%s'", clean)
            return
        self.ws.record_input(clean)

        old_state = self.ws.dialog.engine_state
        logger.info("[SmartEngine] 处理语音: robot=%s state=%s text=\"%s\"",
                    self.robot_id, old_state, clean)

        # 记录用户输入到对话历史
        self.ws.add_to_history("user", clean)

        # ── 趣味互动快速通道 ──
        # 放歌/换歌/停歌/音量调节不应打断正在讲解的 flow/TTS，也不进入 QA/LLM。
        # 这里直接执行音乐相关动作并返回。
        if _is_fun_hit:
            try:
                if _fun_intent == "play_music":
                    await self._handle_play_music()
                elif _fun_intent == "stop_music":
                    await self._handle_stop_music()
                elif _fun_intent == "next_song":
                    await self._handle_next_song()
                elif _fun_intent == "volume_up":
                    await self._handle_volume("up")
                elif _fun_intent == "volume_down":
                    await self._handle_volume("down")
                elif _fun_intent == "take_photo":
                    await self._handle_take_photo()
                from app.engine.dialogue_arbiter import ActionDecision
                self._log_turn(clean, _fun_intent, float(_fun_intent_result.get("confidence", 0.9)),
                               ActionDecision(action="execute", reason="fun_quick_path"),
                               old_state, "fun_quick_path", False, False)
            except Exception as e:
                logger.error("[SmartEngine] 趣味互动快速执行失败 intent=%s err=%s", _fun_intent, e, exc_info=True)
            return

        # ── -1. 访客记忆上下文加载 ──
        # 根据当前机器人的活跃访客绑定，加载历史记忆作为回答增强
        _visitor_ctx = ""
        _active_visitor = None
        try:
            from app.engine.visitor_service import get_active_visitor_for_robot, refresh_binding
            _active_visitor = await get_active_visitor_for_robot(self.robot_id)
            if _active_visitor:
                # 刷新绑定有效期
                await refresh_binding(self.robot_id, _active_visitor["id"])
                # 加载相关记忆
                from app.engine.visitor_memory_service import get_relevant_memories, build_memory_context
                exhibit_id = int(self.ws.exhibit.current_exhibit_id) if self.ws.exhibit.current_exhibit_id else None
                memories = await get_relevant_memories(
                    _active_visitor["id"], clean,
                    exhibit_id=exhibit_id,
                )
                _visitor_ctx = build_memory_context(
                    memories,
                    visitor_name=_active_visitor.get("display_name") or _active_visitor.get("name", ""),
                )
                if _visitor_ctx:
                    logger.debug("[SmartEngine] 访客记忆上下文: visitor=%d len=%d",
                                 _active_visitor["id"], len(_visitor_ctx))
        except Exception as e:
            logger.debug("[SmartEngine] 访客记忆加载跳过: %s", e)

        # 兜底：绑定表偶发未及时读到时，使用世界模型里最近一次人脸识别身份再查一次访客档案。
        # 这能避免“刚被识别并问候了，但问‘你认识我吗’却说没记录”的割裂体验。
        if not _active_visitor:
            try:
                from app.engine.visitor_service import get_visitor_by_user_id, refresh_binding
                face_id = (getattr(self.ws.visitor, "face_id", "") or "").strip()
                face_name = (getattr(self.ws.visitor, "name", "") or "").strip()
                lookup_key = face_id or face_name
                if lookup_key:
                    _active_visitor = await get_visitor_by_user_id(lookup_key)
                    if _active_visitor and _active_visitor.get("id"):
                        await refresh_binding(self.robot_id, _active_visitor["id"])
                        logger.info("[SmartEngine] 使用世界模型人脸身份兜底命中访客: visitor=%s key=%s",
                                    _active_visitor.get("id"), lookup_key)
            except Exception as e:
                logger.debug("[SmartEngine] 访客身份兜底查询跳过: %s", e)

        # 把 active visitor 的结构化身份注入 ws, 给 decision_bridge 当作权威身份源 —
        # 比"用户自报姓名"优先级更高 (人脸识别 + 绑定结果, V5 世界模型的真实状态)。
        # 即使前面加载失败, 也置 None 让下游不读到陈旧值。
        self.ws._active_visitor = _active_visitor

        # ── -0.6. 接待点欢迎问候后的 yes/no 拦截 ──
        # 机器人刚问过“您好/某某，有什么需要帮助的吗？”时，用户说“没有/不用了/不需要”
        # 只表示拒绝帮助，不能落入普通 end_tour/start_tour 状态机继续走流程。
        try:
            from app.engine.visitor_service import get_person_state
            from app.engine.visitor_memory_query import classify_followup_response
            ps = get_person_state(self.robot_id)
            if float(ps.get("reception_followup_until") or 0.0) > time.time():
                resp = classify_followup_response(clean)
                logger.info("[SmartEngine] 接待欢迎 followup 响应: resp=%s text=%r", resp, clean)
                if resp == "no":
                    ps["reception_followup_until"] = 0.0
                    ps["reception_followup_key"] = ""
                    ps["reception_pause_until"] = 0.0
                    await self._speak("好的，有需要您再叫我。")
                    from app.engine.dialogue_arbiter import ActionDecision
                    self._log_turn(clean, "reception_followup.no", 0.98,
                                   ActionDecision(action="answer_only", reason="reception_followup_no"),
                                   old_state, "reception_followup_no", False, False)
                    return
                if resp == "yes":
                    ps["reception_followup_until"] = 0.0
                    ps["reception_followup_key"] = ""
                    ps["reception_pause_until"] = time.time() + 20.0
                    # 用户同意后进入“模式选择”，介绍三种参观方式；短暂停止主动问候，后续讲解/浏览由真实状态继续暂停。
                    await self._guide_before_tour()
                    from app.engine.dialogue_arbiter import ActionDecision
                    self._log_turn(clean, "reception_followup.yes", 0.98,
                                   ActionDecision(action="execute", reason="reception_followup_yes_mode_select"),
                                   old_state, "reception_followup_yes_mode_select", True, False)
                    return
                # 其他内容说明用户提出了具体问题/指令，清掉接待 followup，继续正常处理。
                ps["reception_followup_until"] = 0.0
                ps["reception_followup_key"] = ""
                ps["reception_pause_until"] = time.time() + 20.0
        except Exception as e:
            logger.warning("[SmartEngine] 接待欢迎 followup 处理异常: %s", e, exc_info=True)

        # ── -0.55. 模式选择强拦截 ──
        # 只在接待区欢迎后、刚播完“三种参观方式”时生效。
        # 不能全局拦截“第一个/第二个/第三个”，否则展区资源选择会被误当成第一/第二/第三种参观方式。
        try:
            choice = _normalize_tour_mode_choice(clean)
            if choice and getattr(self, "_awaiting_tour_mode_choice", False):
                from app.engine.visitor_service import get_person_state
                ps = get_person_state(self.robot_id)
                ps["reception_followup_until"] = 0.0
                ps["reception_followup_key"] = ""
                ps["reception_pause_until"] = time.time() + 20.0
                logger.info("[SmartEngine] 模式选择强拦截: choice=%s text=%r", choice, clean)
                self._awaiting_tour_mode_choice = False
                if choice == "preset":
                    self._awaiting_smart_plan_minutes = False
                    await self._begin_preset_tour()
                elif choice == "smart":
                    self._awaiting_smart_plan_minutes = True
                    self.ws.transition("ASK_NEXT")
                    await self._speak("请问您大概有多少分钟？")
                elif choice == "free":
                    self._awaiting_smart_plan_minutes = False
                    await self._list_exhibits()
                from app.engine.dialogue_arbiter import ActionDecision
                self._log_turn(clean, f"tour_mode.{choice}", 0.99,
                               ActionDecision(action="execute", reason="tour_mode_choice"),
                               old_state, f"tour_mode_{choice}", True, False)
                return
        except Exception as e:
            logger.warning("[SmartEngine] 模式选择强拦截异常: %s", e, exc_info=True)

        # ── -0.54. 智能规划分钟数强拦截 ──
        # 原有 _extract_minutes 已支持“十分钟/二十分钟/半小时”，但 set_time 规则只认阿拉伯数字。
        # 在第二种智能规划已询问“请问您大概有多少分钟？”后，必须先消费分钟回答，避免落到 LLM 闲聊。
        try:
            if getattr(self, "_awaiting_smart_plan_minutes", False):
                from app.engine.intent_resolver import _extract_minutes
                minutes = _extract_minutes(clean)
                if minutes is not None:
                    self._awaiting_smart_plan_minutes = False
                    from app.engine.visitor_service import get_person_state
                    ps = get_person_state(self.robot_id)
                    ps["reception_followup_until"] = 0.0
                    ps["reception_followup_key"] = ""
                    ps["reception_pause_until"] = time.time() + 20.0
                    logger.info("[SmartEngine] 智能规划分钟强拦截: minutes=%s text=%r", minutes, clean)
                    await self._speak(f"好的，我来为您规划{minutes}分钟的参观路线。")
                    await self._begin_tour({"minutes": int(minutes)}, f"智能规划{minutes}分钟参观路线", time_budget=int(minutes))
                    from app.engine.dialogue_arbiter import ActionDecision
                    self._log_turn(clean, "set_time", 0.99,
                                   ActionDecision(action="execute", reason="smart_plan_minutes"),
                                   old_state, "smart_plan_minutes", True, False)
                    return
                if clean in ("随便", "都可以", "都行", "你看着安排"):
                    self._awaiting_tour_mode_choice = False
                    minutes = int(getattr(self, "_default_smart_plan_minutes", 15) or 15)
                    self._awaiting_smart_plan_minutes = False
                    await self._speak(f"好的，我按{minutes}分钟为您智能规划参观路线。")
                    await self._begin_tour({"minutes": minutes}, f"智能规划{minutes}分钟参观路线", time_budget=minutes)
                    from app.engine.dialogue_arbiter import ActionDecision
                    self._log_turn(clean, "set_time.default", 0.95,
                                   ActionDecision(action="execute", reason="smart_plan_minutes_default"),
                                   old_state, "smart_plan_minutes_default", True, False)
                    return
        except Exception as e:
            logger.warning("[SmartEngine] 智能规划分钟拦截异常: %s", e, exc_info=True)

        # ── -0.5. 拍照主动建议 / 拍照结果 followup 响应拦截 ──
        try:
            if self._photo_followup and self._photo_followup.get("expires_at", 0) > time.time():
                fu_state = self._photo_followup.get("state", "suggest")
                logger.info("[SmartEngine] 拍照 followup 响应: state=%s text=%r",
                            fu_state, clean)
                if fu_state == "photo_result":
                    # 拍照结果 followup: 用户说"重拍"/"再拍一张" → retake + skipNav
                    _retake_patterns = (r"^重拍", r"^重新拍", r"^重新拍照",
                                        r"^再拍(?:一|1)次", r"^再拍(?:一|1)张",
                                        r"^再来(?:一|1)张", r"^重新来")
                    _is_retake = any(re.search(p, clean) for p in _retake_patterns) if clean else False

                    if _is_retake:
                        # 不清除 followup — 用户可能还想再重拍
                        logger.info("[SmartEngine] 拍照 followup: 用户要求重拍")
                        await self._handle_take_photo(skip_nav=False)
                        from app.engine.dialogue_arbiter import ActionDecision
                        self._log_turn(clean, "take_photo.retake", 0.95,
                                       ActionDecision(action="execute", reason="photo_retake"),
                                       old_state, "take_photo", False, False)
                        return

                    # 【修复】"好了"/"好的"在拍照上下文中表示满意已拍照片, 关闭而非重拍。
                    # classify_followup_response 会把"好了"匹配为"yes"(因为"好"在 YES 模式中),
                    # 但事实上用户说"好了"意味着对当前照片满意, 不需要重拍 → 应该关闭。
                    # 注意: "好的" 不在此关闭, 让 classify_followup_response 匹配为"yes"(重拍)。
                    if clean and re.search(r"^好了\s*$|^可以了\s*$", clean.strip()):
                        self._photo_followup = None
                        logger.info("[SmartEngine] 拍照 followup: 用户满意当前照片, 关闭拍照页")
                        await self.executor.close_photo()
                        await self._speak("好的，我们继续参观。")
                        return

                    # 尝试 yes/no 分类
                    from app.engine.visitor_memory_query import classify_followup_response
                    resp = classify_followup_response(clean)
                    if resp == "yes":
                        self._photo_followup = None
                        await self._handle_take_photo(skip_nav=False)
                        from app.engine.dialogue_arbiter import ActionDecision
                        self._log_turn(clean, "take_photo.confirm", 0.95,
                                       ActionDecision(action="execute", reason="photo_followup_yes"),
                                       old_state, "take_photo", False, False)
                        return
                    elif resp == "no":
                        self._photo_followup = None
                        # 关闭 Android 照片页面 + 恢复人脸跟随
                        await self.executor.close_photo()
                        await self._speak("好的，我们继续参观。")
                        return
                    else:
                        # 未识别到 yes/no/retake → 清除 followup 走正常处理
                        self._photo_followup = None
                else:
                    # 主动建议 followup (旧流程, 兼容保留)
                    from app.engine.visitor_memory_query import classify_followup_response
                    resp = classify_followup_response(clean)
                    logger.info("[SmartEngine] 拍照建议 followup: resp=%s", resp)
                    if resp == "yes":
                        self._photo_followup = None
                        await self._handle_take_photo()
                        from app.engine.dialogue_arbiter import ActionDecision
                        self._log_turn(clean, "take_photo.confirm", 0.95,
                                       ActionDecision(action="execute", reason="photo_suggest_yes"),
                                       old_state, "take_photo", False, False)
                        return
                    elif resp == "no":
                        self._photo_followup = None
                        await self._speak("好的，我们继续参观。")
                        return
                    else:
                        self._photo_followup = None
            elif self._photo_followup:
                self._photo_followup = None
        except Exception as e:
            logger.warning("[SmartEngine] 拍照 followup 处理异常: %s", e, exc_info=True)

        # ── -0.4. 访客记忆 followup 响应拦截 ──
        # 上一轮机器人问过 "要继续看「X」吗?" 之类, 这一轮用户的 yes/no 直接执行
        try:
            import time as _tmod
            # 访客切换检测: followup 是给 A 的, 但现在当前访客是 B, 作废
            _fu_owner = self._memory_followup.get("visitor_id") if self._memory_followup else None
            _cur_visitor = _active_visitor.get("id") if _active_visitor else None
            if self._memory_followup and _fu_owner is not None and _fu_owner != _cur_visitor:
                logger.info("[SmartEngine] 访客切换, 丢弃 followup: owner=%s current=%s",
                            _fu_owner, _cur_visitor)
                self._memory_followup = None
            if self._memory_followup and self._memory_followup.get("expires_at", 0) > _tmod.time():
                from app.engine.visitor_memory_query import classify_followup_response
                fu = self._memory_followup
                resp = classify_followup_response(clean)
                logger.info("[SmartEngine] 记忆 followup 响应: resp=%s action=%s target=%s",
                            resp, fu.get("action"), fu.get("exhibit_name"))
                if resp == "yes":
                    # 清 followup, 打断, 导航到展项并播放
                    self._memory_followup = None
                    await self._interrupt_current_activity(reason="memory_followup_yes")
                    # 清 TTS 取消位, 避免后续 _navigate → _speak 的第一句被吞
                    self._tts_cancel.clear()
                    target_name = fu.get("exhibit_name", "")
                    if fu.get("action") == "nav_exhibit" and target_name:
                        await self._navigate_to_exhibit(target_name)
                    # 写入访客记忆
                    if _active_visitor:
                        try:
                            from app.engine.visitor_memory_service import save_memory
                            await save_memory(
                                visitor_id=_active_visitor["id"], robot_id=self.robot_id,
                                question=clean, answer=f"开始带看「{target_name}」",
                                intent="memory_followup.yes", topic=target_name,
                                exhibit_id=fu.get("exhibit_id"),
                            )
                        except Exception:
                            pass
                    from app.engine.dialogue_arbiter import ActionDecision
                    self._log_turn(clean, "memory_followup.yes", 0.95,
                                   ActionDecision(action="execute", reason=f"nav→{target_name}"),
                                   old_state, "memory_followup_exec", True, False)
                    return
                elif resp == "no":
                    # 清 followup, 友好回应
                    self._memory_followup = None
                    await self._speak("好的，您可以说想看的展项名，或者说「有哪些展项」看看。")
                    if _active_visitor:
                        try:
                            from app.engine.visitor_memory_service import save_memory
                            await save_memory(
                                visitor_id=_active_visitor["id"], robot_id=self.robot_id,
                                question=clean, answer="放弃上次展项",
                                intent="memory_followup.no", topic="",
                            )
                        except Exception:
                            pass
                    from app.engine.dialogue_arbiter import ActionDecision
                    self._log_turn(clean, "memory_followup.no", 0.95,
                                   ActionDecision(action="answer", reason="user_declined"),
                                   old_state, "memory_followup_no", False, False)
                    return
                else:
                    # other: 清 followup, 继续正常流程（用户可能要问别的）
                    logger.info("[SmartEngine] 记忆 followup 不明确, 清除并走正常流程")
                    self._memory_followup = None
            elif self._memory_followup:
                # 过期清理
                self._memory_followup = None
        except Exception as e:
            logger.warning("[SmartEngine] followup 处理异常: %s", e, exc_info=True)

        # ── -0.45. 浏览挂起恢复 followup ──
        # 上一轮 browse 被无关输入打断, bot 已问"要继续吗". 这一轮用户的 yes/no 拦截:
        #   yes → resume_browse 还原现场, 重提"想看第几个"
        #   no  → 清快照, 走正常流程把这次输入也答了
        #   other(说了别的) → 清快照, 当作新输入处理
        # 防御: 上一轮 suspend 后走 tour_control 跳过了 QA 路径(没机会问 awaiting_resume),
        # 这里清掉 ghost 快照, 避免长期残留。
        if self.ws.browse.suspended_mode and not self.ws.browse.awaiting_resume:
            logger.info("[SmartEngine] 丢弃 ghost suspended_browse(mode=%s)",
                        self.ws.browse.suspended_mode)
            self.ws.clear_suspended_browse()
        if self.ws.browse.awaiting_resume:
            try:
                from app.engine.visitor_memory_query import classify_followup_response
                resp = classify_followup_response(clean)
            except Exception:
                resp = "other"
            self.ws.browse.awaiting_resume = False  # 一次性, 无论结果都消费掉
            if resp == "yes":
                if self.ws.resume_browse():
                    logger.info("[SmartEngine] 浏览模式恢复: mode=%s exhibit=%s",
                                self.ws.browse.mode, self.ws.browse.selected_exhibit_name)
                    mode = self.ws.browse.mode
                    name = self.ws.browse.selected_exhibit_name
                    if mode == "list_resources":
                        n = len(self.ws.browse.resources_cache)
                        await self._speak(f"好的，回到「{name}」的{n}个内容，您想看第几个？")
                    elif mode == "list_exhibits":
                        n = len(self.ws.browse.exhibits_cache)
                        await self._speak(f"好的，回到展项列表，共{n}个，您想看第几个？")
                    elif mode == "list_specials":
                        n = len(self.ws.browse.specials_cache)
                        await self._speak(f"好的，回到专场列表，共{n}个，您想选哪个？")
                    elif mode == "playing":
                        await self._speak(f"好的，「{name}」继续。")
                    from app.engine.dialogue_arbiter import ActionDecision
                    self._log_turn(clean, "browse_resume.yes", 0.95,
                                   ActionDecision(action="execute", reason=f"resume:{mode}"),
                                   old_state, "browse_resume", False, True)
                    return
                # 没快照(理论上不会), 当 other 处理
                logger.warning("[SmartEngine] awaiting_resume 但无 suspended_mode, 走正常流程")
            elif resp == "no":
                self.ws.clear_suspended_browse()
                await self._speak("好的，那不继续了。")
                from app.engine.dialogue_arbiter import ActionDecision
                self._log_turn(clean, "browse_resume.no", 0.95,
                               ActionDecision(action="answer", reason="user_declined"),
                               old_state, "browse_resume", False, False)
                return
            else:
                # 用户说了其它话题 → 丢弃挂起的 browse, 当新输入处理
                self.ws.clear_suspended_browse()
                logger.info("[SmartEngine] 浏览挂起后用户切话题, 丢弃 browse 快照")

        # ── -0.5. 访客记忆查询拦截 ──
        # "你认识我吗 / 我叫什么 / 我昨天说了什么 / 我来过几次" 这类问题
        # 直接用访客档案+历史回答, 不走 QueryRouter / LLM
        try:
            from app.engine.visitor_memory_query import classify_memory_query, answer_memory_query_ex
            _mem_qtype = classify_memory_query(clean)
            if _mem_qtype:
                logger.info("[SmartEngine] 访客记忆查询: type=%s visitor=%s",
                            _mem_qtype,
                            _active_visitor.get("id") if _active_visitor else None)
                # 打断旧活动防串话
                await self._interrupt_current_activity(reason=f"memory_query:{_mem_qtype}")
                # 清 TTS 取消位, 保证记忆查询回答不被吞
                self._tts_cancel.clear()
                answer, followup = await answer_memory_query_ex(_mem_qtype, _active_visitor, self.robot_id)
                if answer:
                    await self._speak(answer)
                # 设置 followup(60秒内有效), 供下一轮 yes/no 响应
                if followup:
                    import time as _tmod
                    self._memory_followup = {
                        **followup,
                        "expires_at": _tmod.time() + 60,
                        "visitor_id": _active_visitor.get("id") if _active_visitor else None,
                    }
                    logger.info("[SmartEngine] 设置记忆 followup: %s", self._memory_followup)
                # 写入访客记忆（如果已绑定）
                if _active_visitor:
                    try:
                        from app.engine.visitor_memory_service import save_memory
                        await save_memory(
                            visitor_id=_active_visitor["id"],
                            robot_id=self.robot_id,
                            question=clean,
                            answer=answer[:500] if answer else "",
                            intent=f"memory_query.{_mem_qtype}",
                            topic="",
                        )
                    except Exception as e:
                        logger.debug("[SmartEngine] 记忆查询写入跳过: %s", e)
                # 结构化日志
                from app.engine.dialogue_arbiter import ActionDecision
                self._log_turn(
                    clean, f"memory_query.{_mem_qtype}", 0.95,
                    ActionDecision(action="answer", reason="visitor_memory_query"),
                    old_state, "memory_query", False, False,
                )
                return
        except Exception as e:
            logger.warning("[SmartEngine] 访客记忆查询处理异常: %s", e, exc_info=True)

        # 用户开口说新话，取消旧链路排队中的TTS（防串话）
        self._cancel_pending_tts()

        # ── 0. 指代消解 ──
        resolved_text, extra_entities = resolve_anaphora(
            clean, old_state, self.ws.recent, self.ws.exhibit
        )

        # ── 0.5. 统一问答中枢 (QueryRouter → AnswerEngine → DialoguePolicy) ──
        # 现场实时语音的主链路: 所有非 tour_control 的 Q&A 全部走这里, 不再触发旧意图链。
        # tour_control 才回落到下面的 resolve_intent / dialogue_arbiter 流程。
        from app.engine.query_router import classify_query, TOUR_CONTROL, GENERAL_KNOWLEDGE
        from app.engine.answer_engine import answer as _qa_answer
        from app.engine.dialogue_policy import apply_policy as _qa_apply_policy

        qr = classify_query(resolved_text, self.ws)
        qtype = qr["qtype"]
        qslots = qr.get("slots", {})
        logger.info("[SmartEngine] QueryRouter: qtype=%s slots=%s reason=%s",
                    qtype, qslots, qr.get("reason", ""))

        # 一次性意图识别: resolve_intent 内部已做 rule + trigger_word + LLM 的级联;
        # 这里缓存结果, 下面用来 (a) 决定 qtype 覆盖 (b) 后面状态机 dispatch 复用, 避免二次调用.
        # QA 路径 (qtype != TOUR_CONTROL) 跳掉 IntentResolver 的 LLM 兜底:
        # 它返回 chitchat 不会触发 _tour_intents 覆盖, 5-10s 纯浪费;
        # rule + 同义词 + 拼音 + trigger_word 仍会跑, 控制类高置信意图照常命中。
        _skip_intent_llm = (qtype != TOUR_CONTROL)
        _cached_intent_result: dict | None = None
        if not self.ws.browse.mode:
            try:
                _cached_intent_result = await asyncio.wait_for(
                    resolve_intent(resolved_text, self.ws,
                                   skip_llm_fallback=_skip_intent_llm),
                    timeout=5.0)  # 18s→5s, LLM 已经 8s timeout, 这里 5s 足够
            except asyncio.TimeoutError:
                logger.warning("[SmartEngine] resolve_intent 超时(顶层 5s), 使用 chitchat 兜底")
                _cached_intent_result = {
                    "intent": "chitchat", "confidence": 0.2, "entities": {}, "source": "timeout",
                }
            except Exception as e:
                logger.debug("[SmartEngine] resolve_intent 异常: %s", e)
                _cached_intent_result = None

            # navigate_to 校验: 规则 "介绍/讲解/带我去 + X" 抽出的 X 不一定是真展项
            # ("介绍一下玄武湖" 抽到 "玄武湖" 不是展项, 是自由提问).
            # 在做 qtype 覆盖之前先 DB 确认; 不是展项就把意图降级 + 强制路由到 GENERAL_KNOWLEDGE,
            # 让 LLM 直接回答, 不再绕道 _navigate_to_exhibit 失败兜底。
            if (_cached_intent_result
                    and _cached_intent_result.get("intent") == "navigate_to"
                    and _cached_intent_result.get("entities", {}).get("target")
                    and _cached_intent_result.get("source") != "trigger"):
                _nav_target = _cached_intent_result["entities"]["target"]
                _maybe_exhibit = None
                _maybe_position = None
                try:
                    _maybe_exhibit = await self.executor.find_exhibit_by_name(_nav_target)
                except Exception as e:
                    logger.debug("[SmartEngine] 展项校验异常 target=%r: %s", _nav_target, e)
                # 没找到展项 → 再查点位库. "接待点/前台/充电桩" 这类不是展项但是真实点位,
                # 同样应该保留 navigate_to 走标准导航, 而不是降级到 LLM 闲聊。
                if not _maybe_exhibit:
                    try:
                        _maybe_position = await self.executor.find_position_by_name(_nav_target)
                    except Exception as e:
                        logger.debug("[SmartEngine] 点位校验异常 target=%r: %s", _nav_target, e)
                if not _maybe_exhibit and not _maybe_position:
                    logger.info(
                        "[SmartEngine] navigate_to target=%r 不在展项库/点位库 → 视为自由提问, 路由到 GENERAL_KNOWLEDGE",
                        _nav_target,
                    )
                    qtype = GENERAL_KNOWLEDGE
                    qr = {"qtype": GENERAL_KNOWLEDGE, "slots": {}, "reason": "navigate_target_not_exhibit"}
                    _cached_intent_result = {
                        "intent": "chitchat", "confidence": 0.3,
                        "entities": {}, "source": "free_query",
                    }
                elif _maybe_position and not _maybe_exhibit:
                    logger.info("[SmartEngine] navigate_to target=%r 命中点位 %r (非展项, 走点位导航)",
                                _nav_target, _maybe_position.get("name"))

            if _cached_intent_result and qtype != TOUR_CONTROL:
                _tour_intents = {
                    "navigate_to", "switch_special", "list_exhibits",
                    "list_exhibit_resources",
                    "start_tour", "end_tour", "play_resource", "play_next",
                    "skip_exhibit", "wait_please", "resume_please",
                    "set_time", "ask_current",
                    "play_music", "stop_music", "next_song",
                    "volume_up", "volume_down", "take_photo",
                }
                _ir_intent = _cached_intent_result.get("intent")
                _ir_conf = _cached_intent_result.get("confidence", 0)
                if _ir_intent in _tour_intents and _ir_conf >= 0.8:
                    logger.info("[SmartEngine] 意图覆盖 QueryRouter: '%s' → %s (conf=%.2f)",
                                resolved_text, _ir_intent, _ir_conf)
                    qtype = TOUR_CONTROL
                    qr = {"qtype": TOUR_CONTROL, "slots": qslots, "reason": "intent_override"}
                # 触发词命中也强制 TOUR_CONTROL (resolve_intent 内部已判定, 复用其 source)
                elif _cached_intent_result.get("source") == "trigger":
                    logger.info("[SmartEngine] 流程触发词命中(覆盖 QueryRouter): '%s' → script=%s",
                                resolved_text,
                                _cached_intent_result.get("entities", {}).get("script_name"))
                    qtype = TOUR_CONTROL
                    qr = {"qtype": TOUR_CONTROL, "slots": qslots, "reason": "trigger_word_override"}

        # 浏览模式活跃时(专场/展项/资源列表): 跳过 QueryRouter + IntentResolver,
        # 直接用原始文本走 browse handler, 避免指代消解/LLM 把序号吞掉.
        # synth_intent 覆盖数字序号 / 播放控制 / 结束 三大类高频词, 避免 browse handler
        # 拿到 navigate_to 误触发展项名查找.
        # 触发词命中时不进 browse handler — 触发词是"无条件优先"语义, 应该启动新流程
        # 而不是被当作展项名走 fuzzy 搜索 (e.g. "开始讲解" 拼音命中 "宇树讲解屏").
        # 触发词命中 → 清空 browse 模式 + 跳过这段, 让下面意图链跑到 trigger_word 命中分支.
        if self.ws.browse.mode and _is_trigger_hit:
            logger.info("[SmartEngine] 浏览模式(%s) + 触发词命中 → 退出浏览模式, 让触发词执行: %r",
                        self.ws.browse.mode, clean[:30])
            self.ws.reset_browse()

        # 浏览模式下命中"明确意图" (switch_special/start_tour 等) 也要绕过 browse handler.
        # 否则用户说"切换丝的专场" 会被 browse 当展项名 fuzzy 搜, 不能正确切换专场。
        # play_resource/play_next/wait_please 等仍由 browse handler 内部合成意图处理 (它知道当前 list).
        _BROWSE_BYPASS_INTENTS = {
            "switch_special", "start_tour", "go_charging",
            "list_specials",  # "切换专场"无名称 → 列专场
            "play_music",     # "放一首歌"也应该任何时候都能切到音乐播放
            "take_photo",     # "我要拍照/合照/打卡"也应该任何时候都能切到拍照流程
        }
        if (self.ws.browse.mode and _cached_intent_result
                and _cached_intent_result.get("intent") in _BROWSE_BYPASS_INTENTS
                and _cached_intent_result.get("confidence", 0) >= 0.7):
            logger.info("[SmartEngine] 浏览模式(%s) + 明确意图 %s → 退出浏览模式走标准意图链: %r",
                        self.ws.browse.mode, _cached_intent_result.get("intent"), clean[:30])
            self.ws.reset_browse()

        if self.ws.browse.mode:
            logger.info("[SmartEngine] 浏览模式(%s)活跃, 直接走 browse handler", self.ws.browse.mode)
            _c = _strip_terminal_speech_punctuation(clean)
            # 序号识别放宽: 不止纯数字"3"/纯短句"第一个", 还要识别"我要看第一个"/"播第3个" 这种带前缀.
            # 只要文本里出现 "第X" / "X个" / 纯数字 / 单字中文数字 → 都视为带序号意图.
            _has_idx_marker = bool(re.search(r"第\s*[一二三四五六七八九十百\d]+", _c)) or \
                              bool(re.search(r"[一二三四五六七八九十\d]+\s*个(?:吧)?$", _c)) or \
                              _c.isdigit() or \
                              _c in ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")
            idx = _parse_resource_index(_c) if _has_idx_marker else 0
            # 播放/浏览控制关键词 → 合成对应意图, 让 browse handler 精确命中
            _playback_map = {
                "暂停": "wait_please", "停一下": "wait_please", "先停": "wait_please",
                "先停一下": "wait_please",
                "继续": "resume_please", "接着讲": "resume_please",
                "下一个": "play_next", "下一个资源": "play_next",
                "换一个": "play_next", "再看一个": "play_next",
                "上一个": "play_next",  # 走 browse handler 的 "上一个" 字面量分支
                "上一个资源": "play_next", "前一个": "play_next", "返回上一个": "play_next",
                "重播": "resume_please", "再播一遍": "resume_please",
                "再讲一遍": "resume_please", "再说一次": "resume_please",
                "结束": "end_tour", "不用了": "end_tour", "可以了": "end_tour",
                "再见": "end_tour", "够了": "end_tour",
            }
            # confidence 按合成意图的"确信度"区分:
            # 数字序号匹配 regex 且成功解析 index → 0.95 (高度确信)
            # 播放/结束关键词字面量完全相等 → 0.92 (明确)
            # 退化成 navigate_to 做 fuzzy 展项名 → 0.70 (模糊, 可能误伤)
            if idx:
                synth_intent = "play_resource"
                synth_entities = {"resource_index": idx}
                synth_conf = 0.95
            elif _c in _playback_map:
                synth_intent = _playback_map[_c]
                synth_entities = {}
                synth_conf = 0.92
            else:
                synth_intent = "navigate_to"
                synth_entities = {"target": _c}
                synth_conf = 0.70
            handled = await self._handle_browse_input(synth_intent, synth_entities, clean, "")
            if handled:
                self.ws.record_turn(clean, synth_intent)
                from app.engine.dialogue_arbiter import ActionDecision
                self._log_turn(clean, synth_intent, synth_conf,
                               ActionDecision(action="execute",
                                              reason=f"browse:{self.ws.browse.mode}"),
                               old_state, f"browse_synth:{synth_intent}", False, False)
                return
            # browse 不认识的输入 → 挂起浏览状态走正常 QA, 答完追问"要继续吗".
            # 用户下一轮回"继续/嗯/好" → resume_browse 还原现场; 回"不用/算了" 或别的话题 → 丢快照.
            # 这样不会因一句无关插话(如"导航找不到方向了")把列表选择吃掉。
            _suspended_mode_for_log = self.ws.browse.mode
            _suspended_exhibit_for_log = self.ws.browse.selected_exhibit_name
            self.ws.suspend_browse()
            _suspend_resume_prompt = True
            logger.info("[SmartEngine] browse 未处理输入, 挂起浏览状态(mode=%s exhibit=%s) 待用户确认是否继续",
                        _suspended_mode_for_log, _suspended_exhibit_for_log)

        if qtype != TOUR_CONTROL:
            from app.engine.gateway_qa import (
                SERVICE_UNAVAILABLE_TEXT as GATEWAY_UNAVAILABLE_TEXT,
                get_gateway_qa_mode,
                mark_gateway_result_spoken,
            )
            from app.engine.legacy_qa import (
                SERVICE_UNAVAILABLE_TEXT as LEGACY_UNAVAILABLE_TEXT,
                get_legacy_qa_mode,
            )
            _gateway_active = get_gateway_qa_mode() == "active"
            # Gateway active 时老系统仅作为整体回滚，不允许单轮降级。
            _legacy_qa_active = (
                not _gateway_active and get_legacy_qa_mode() == "active"
            )
            _external_qa_active = _gateway_active or _legacy_qa_active
            _service_unavailable_text = (
                GATEWAY_UNAVAILABLE_TEXT
                if _gateway_active
                else LEGACY_UNAVAILABLE_TEXT if _legacy_qa_active else ""
            )
            # 任何 Q&A 都需先打断在跑的 flow / 排队 TTS, 防止旧任务串话
            interrupted = await self._interrupt_current_activity(reason=f"qa:{qtype}")
            _gateway_turn_token = (
                self._begin_gateway_turn() if _gateway_active else None
            )
            _legacy_qa_turn_token = (
                self._begin_legacy_qa_turn() if _legacy_qa_active else None
            )
            # 注入访客记忆上下文（临时属性，供 AnswerEngine / LLM 使用）
            self.ws._visitor_memory_context = _visitor_ctx
            # 占位前奏 — 用户说完到 bot 出声 ≤300ms, 提升感知响应速度。
            # 仅对预期慢路径(LLM/OpenClaw 类)开启, 结构化路径(realtime/poi)不抢话。
            # speak_serialized 内部 tts_lock 保证占位语和后面的实质答案 TTS 串行不打架。
            _qa_quick_ack_task = None if _external_qa_active else self._maybe_quick_ack(
                qtype, text=resolved_text or clean
            )
            # 外层 20s 硬超时 — 跟 decision_bridge 内层 OpenClaw 18s 对齐 + 2s 余量;
            # 配合占位前奏 (≤300ms 出声"我想想"), 用户感知不到这段等待时间;
            # 仍保留兜底, 服务端卡死时 20s 后机器人不会挂死。
            try:
                qa_result = await asyncio.wait_for(
                    _qa_answer(
                        qtype,
                        resolved_text,
                        qslots,
                        self.ws,
                        self.robot_id,
                        gateway_text=clean,
                        legacy_text=clean,
                    ),
                    timeout=20.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[SmartEngine] QA 路径超时(20s), gateway_active=%s legacy_active=%s",
                    _gateway_active,
                    _legacy_qa_active,
                )
                _error_prefix = "gateway_qa" if _gateway_active else "legacy_qa"
                qa_result = {
                    "answer": _service_unavailable_text if _external_qa_active else "",
                    "source": f"{_error_prefix}_error:outer_timeout" if _external_qa_active else "qa_timeout",
                    "confidence": 1.0 if _external_qa_active else 0.0,
                    "resume_policy": "none",
                    "qtype": qtype,
                    "extra": {
                        "gateway" if _gateway_active else "legacy_qa": {
                            "should_speak": True,
                            "error_code": "outer_timeout",
                        }
                    } if _external_qa_active else {},
                }
            except Exception as e:
                logger.error(
                    "[SmartEngine] AnswerEngine 异常: %s gateway_active=%s legacy_active=%s",
                    e,
                    _gateway_active,
                    _legacy_qa_active,
                    exc_info=True,
                )
                _error_prefix = "gateway_qa" if _gateway_active else "legacy_qa"
                qa_result = {
                    "answer": _service_unavailable_text if _external_qa_active else "",
                    "source": f"{_error_prefix}_error:engine_error" if _external_qa_active else "engine_error",
                    "confidence": 1.0 if _external_qa_active else 0.0,
                    "resume_policy": "none",
                    "qtype": qtype,
                    "extra": {
                        "gateway" if _gateway_active else "legacy_qa": {
                            "should_speak": True,
                            "error_code": "engine_error",
                        }
                    } if _external_qa_active else {},
                }

            if _gateway_active and not self._is_gateway_turn_current(_gateway_turn_token):
                logger.info("[SmartEngine] Gateway QA 结果晚到，turn 已失效，丢弃播报 robot=%s", self.robot_id)
                return
            if _legacy_qa_active and not self._is_legacy_qa_turn_current(_legacy_qa_turn_token):
                logger.info("[SmartEngine] 老系统回复晚到，turn 已失效，丢弃播报 robot=%s", self.robot_id)
                return

            _qa_result_extra = qa_result.get("extra") or {}
            _legacy_meta = _qa_result_extra.get("legacy_qa") or {}
            if _legacy_qa_active and _legacy_meta.get("teaching_required"):
                await self._start_legacy_teaching(clean)
                return

            # 低置信度 / 无答案 / 无结构化支撑 → 不再固定话术拒答, 改用 LLM 兜底
            # (用户要求: 美食/天气/景点这类问题最差也要 LLM 给出回答, 不能丢「没听清」)
            # 仍然保留: 已经是高置信结构化结果 (展项/导航 anchor / hall_*) 不覆盖
            ans = (qa_result.get("answer") or "").strip()
            _src = qa_result.get("source") or ""
            _conf = float(qa_result.get("confidence", 0.0))
            _extra = _qa_result_extra
            _has_anchor = bool(_extra.get("exhibit_id") or _extra.get("flow_name")
                               or _extra.get("target") or qa_result.get("action"))
            _trusted_src = _src in ("tour_control_passthrough", "social",
                                    "hall_context", "hall_knowledge",
                                    "weather_api", "local_clock", "llm_general")
            if (not _external_qa_active and (not ans or _conf < 0.5)
                    and not _has_anchor and not _trusted_src):
                logger.info("[SmartEngine] 低置信度/空答案 (conf=%.2f, source=%s) → LLM 兜底",
                            _conf, _src)
                llm_ans = await self._llm_last_resort(resolved_text)
                if llm_ans:
                    ans = llm_ans
                    qa_result["answer"] = ans
                    qa_result["source"] = "llm_fallback"
                    qa_result["confidence"] = 0.6
                elif not ans:
                    # LLM 也彻底失败, 最后才回到温和提示 (而非「没听清」)
                    ans = "这个问题我一时回答不上来，您可以稍后再问我，或者问问现场工作人员。"
                    qa_result["answer"] = ans
                    qa_result["source"] = "llm_fallback_failed"

            _gateway_meta = (_extra.get("gateway") or {}) if _gateway_active else {}
            _gateway_should_speak = bool(_gateway_meta.get("should_speak", True))
            if ans and _gateway_should_speak:
                await self._speak(ans)
                _gateway_request_id = str(_gateway_meta.get("request_id") or "")
                _gateway_result_id = str(_gateway_meta.get("result_id") or "")
                if _gateway_active and _gateway_request_id and _gateway_result_id:
                    mark_gateway_result_spoken(
                        self.robot_id, _gateway_request_id, _gateway_result_id
                    )
            elif _gateway_active and not _gateway_should_speak:
                logger.warning("[SmartEngine] Gateway 重放/冲突结果不播报 robot=%s", self.robot_id)

            # 浏览被无关输入打断 → 答完正题后追问"要不要继续刚才的浏览"。
            # 设 awaiting_resume=True, 下一轮 _handle_speech 顶上拦截 yes/no.
            if _suspend_resume_prompt:
                _suspend_resume_prompt = False
                _smode = self.ws.browse.suspended_mode
                _sname = self.ws.browse.suspended_selected_exhibit_name
                if _smode == "list_resources" and _sname:
                    _resume_q = f"要不要继续看「{_sname}」的资源？说继续或者不用了。"
                elif _smode == "list_exhibits":
                    _resume_q = "要不要继续看展项列表？说继续或者不用了。"
                elif _smode == "list_specials":
                    _resume_q = "要不要继续选专场？说继续或者不用了。"
                elif _smode == "playing" and _sname:
                    _resume_q = f"要不要继续讲「{_sname}」？说继续或者不用了。"
                else:
                    _resume_q = ""
                if _resume_q:
                    self.ws.browse.awaiting_resume = True
                    await self._speak(_resume_q)
                else:
                    # 没有可恢复内容(theoretically不该走到), 清掉快照
                    self.ws.clear_suspended_browse()

            # 如果 hall_context 检索到了具体展项, 同步到 WorldState (供后续上下文复用)
            extra = qa_result.get("extra") or {}
            if extra.get("exhibit_id") and extra.get("exhibit_name"):
                self.ws.set_current_exhibit(str(extra["exhibit_id"]), extra["exhibit_name"])

            # ── OpenClaw 决策执行 ─────────────────────────────
            # 当 source=openclaw 且有命令链 (commands) 时, 顺序执行。
            # 没命令链但 action 是执行类时, 走旧的单动作分发兼容路径。
            action = qa_result.get("action") or ""
            source = qa_result.get("source") or ""
            if source == "openclaw":
                cmds = list(extra.get("commands") or [])
                # 安全网: ask_clarify 决策但 commands 没带 listen 的, 自动补一发
                # 8s 收音, 让用户能自然回答 — 不然机器人就自言自语没下文。
                if action == "ask_clarify":
                    has_listen = any(
                        isinstance(c, dict) and (c.get("type") or "").lower() == "listen"
                        for c in cmds
                    )
                    if not has_listen:
                        cmds.append({"type": "listen", "seconds": 8})
                if cmds:
                    try:
                        await self._dispatch_command_list(cmds)
                    except Exception as e:
                        logger.error("[SmartEngine] OpenClaw 命令链执行失败 err=%s",
                                     e, exc_info=True)
                elif action in ("navigate_to", "list_exhibits", "execute_flow"):
                    try:
                        await self._dispatch_openclaw_action(action, extra)
                    except Exception as e:
                        logger.error("[SmartEngine] OpenClaw 动作执行失败 action=%s err=%s",
                                     action, e, exc_info=True)

            # 应用恢复策略
            try:
                await _qa_apply_policy(self, qa_result.get("resume_policy", "none"), qtype)
            except Exception as e:
                logger.debug("[SmartEngine] apply_policy fail: %s", e)

            # 结构化日志 (复用 _log_turn, 用伪 decision)
            class _PseudoDecision:
                action = "answer_engine"
                reason = qa_result.get("source", "")
            self._log_turn(
                clean,
                f"qa.{qtype}",
                float(qa_result.get("confidence", 0.0)),
                _PseudoDecision(),
                old_state,
                qa_result.get("source", "answer_engine"),
                interrupted,
                False,
                resolved_text,
            )
            # QA 路径的访客记忆写入
            if _active_visitor and clean:
                try:
                    from app.engine.visitor_memory_service import save_memory
                    exhibit_id = int(self.ws.exhibit.current_exhibit_id) if self.ws.exhibit.current_exhibit_id else None
                    await save_memory(
                        visitor_id=_active_visitor["id"],
                        robot_id=self.robot_id,
                        question=clean,
                        answer=ans[:500] if ans else "",
                        intent=f"qa.{qtype}",
                        topic=self.ws.exhibit.current_exhibit_name or "",
                        exhibit_id=exhibit_id,
                    )
                except Exception as e:
                    logger.debug("[SmartEngine] QA 访客记忆写入跳过: %s", e)
            return

        # ── 1. 意图识别（已在顶部 classify_query 后一次性 resolve_intent, 这里直接复用）──
        if _cached_intent_result is not None:
            intent_result = _cached_intent_result
        else:
            # 仅当顶部 resolve_intent 被跳过(如 browse.mode 激活路径已 return 前的边角) 才补算
            try:
                intent_result = await asyncio.wait_for(
                    resolve_intent(resolved_text, self.ws), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("[SmartEngine] resolve_intent 超时(5s), 降级 chitchat")
                intent_result = {"intent": "chitchat", "confidence": 0.2, "entities": {}, "source": "timeout"}
        # 合并指代消解提取的 entities
        if extra_entities:
            for k, v in extra_entities.items():
                if v and not intent_result.get("entities", {}).get(k):
                    intent_result.setdefault("entities", {})[k] = v

        intent = intent_result.get("intent", "chitchat")
        confidence = intent_result.get("confidence", 0)
        entities = intent_result.get("entities", {})
        reply_hint = intent_result.get("reply", "")

        logger.info("[SmartEngine] 意图: %s (%.2f) entities=%s resolved='%s'",
                    intent, confidence, entities,
                    resolved_text if resolved_text != clean else "")

        # ── 保护5：低置信度计数 ──
        self.ws.record_low_confidence(confidence < 0.5)

        # ── 2. 仲裁层决策 ──
        decision = arbitrate(
            intent_result, old_state, clean,
            has_pending_command=bool(self.ws.execution.pending_command),
            world_state=self.ws,
        )
        logger.info("[SmartEngine] 仲裁: action=%s reason=%s interrupt=%s resume=%s",
                    decision.action, decision.reason, decision.can_interrupt, decision.should_resume)

        # 记录到短时上下文
        self.ws.recent.last_target = entities.get("target", "") or self.ws.recent.last_target

        # ── 2b. 强打断: 明确切换任务的意图, 直接停旧 flow / 取消旧 TTS / 清理 pending ──
        _INTERRUPT_INTENTS = {
            "navigate_to", "end_tour", "list_exhibits", "ask_current",
            "start_tour", "interactive_discuss", "switch_special",
            "play_resource", "go_charging",
        }
        hard_interrupted = False
        _should_interrupt = False
        if intent in _INTERRUPT_INTENTS and not (
            intent == "navigate_to" and not entities.get("target")
        ):
            if decision.can_interrupt:
                _should_interrupt = True
            else:
                # arbiter 对 ANSWER_ONLY_INTENTS 硬编码 can_interrupt=False, 但用户明确要求:
                # flow 中"这是什么"/"有哪些展项" 必须停 flow, 问完再答。
                # 所以只要有 active flow, 对切换类意图一律强打断。
                try:
                    from app.engine.flow_executor import get_active_flow
                    active = get_active_flow(self.robot_id)
                    if active and getattr(active, "status", "") == "running":
                        _should_interrupt = True
                except Exception:
                    pass
        if _should_interrupt:
            hard_interrupted = await self._interrupt_current_activity(
                reason=f"切换到意图={intent}"
            )

        # ── 3. 按仲裁结果分发 ──
        action_taken = decision.action
        interrupted = hard_interrupted
        resumed = False

        # --- 3a. 澄清：低置信度或缺关键信息 ---
        if decision.action == "clarify":
            if decision.should_resume and old_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE"):
                snippet = self._build_context_snippet(old_state)
                self.ws.suspend_activity(self._plan_index, "prompt_next", snippet)
                interrupted = True
            await self._speak(decision.clarify_prompt)

        # --- 3b. 确认：半明确意图，先引导 ---
        elif decision.action == "confirm":
            if decision.should_resume and old_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE"):
                snippet = self._build_context_snippet(old_state)
                self.ws.suspend_activity(self._plan_index, "prompt_next", snippet)
                interrupted = True
            await self._speak(decision.confirm_prompt)
            # 确认后进入 ASK_NEXT 等用户回复
            if old_state not in ("ASK_NEXT", "WELCOME"):
                self.ws.transition("ASK_NEXT")

        # --- 3c. 社交寒暄：轻量回应，不改状态 ---
        elif decision.action == "social":
            if reply_hint:
                await self._speak(reply_hint)
            else:
                await self._smart_speak(
                    "您好！有什么可以帮您的？",
                    "社交回应，简短自然"
                )

        # --- 3d. 仅回答：问答类意图，回答后可恢复 ---
        elif decision.action == "answer_only":
            if decision.should_resume and old_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE"):
                snippet = self._build_context_snippet(old_state)
                self.ws.suspend_activity(self._plan_index, "prompt_next", snippet)
                interrupted = True
            # 按具体意图回答
            if intent in ("chitchat", "weather_query"):
                await self._handle_chitchat(clean, reply_hint)
            elif intent == "query_help":
                await self._handle_utility_intent("query_help", entities, clean)
            elif intent == "query_status":
                await self._handle_utility_intent("query_status", entities, clean)
            elif intent == "ask_current":
                await self._handle_utility_intent("ask_current", entities, clean)
            elif intent == "ask_explain":
                await self._handle_followup_question(clean)
            else:
                await self._handle_chitchat(clean, reply_hint)

        # --- 3e. 执行：明确意图，走状态机 ---
        elif decision.action == "execute":
            # 流程触发词命中：有 script_id 时无视状态机
            script_id = entities.get("script_id")
            if script_id:
                logger.info("[SmartEngine] 触发词命中 script_id=%s，跳过状态机直接启动流程", script_id)
                if self._discussion:
                    self._discussion.cancel()
                    self._discussion = None
                from app.engine.flow_executor import get_active_flow, stop_flow
                active = get_active_flow(self.robot_id)
                if active and active.status == "running":
                    await stop_flow(self.robot_id)
                self.ws.reset_browse()
                self.ws.transition("WELCOME")
                await self._speak("好，马上开始。")
                await self._run_script_tour(script_id)
                self._log_turn(clean, intent, confidence, decision, old_state, "script_trigger", False, False, resolved_text)
                return

            # 设备控制意图：随时执行，不改对话状态
            if intent in ("lights_on", "lights_off", "all_on", "all_off",
                          "ad_screen_on", "ad_screen_off", "switch_special", "go_charging"):
                if decision.should_resume and old_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE"):
                    snippet = self._build_context_snippet(old_state)
                    self.ws.suspend_activity(self._plan_index, "prompt_next", snippet)
                    interrupted = True
                await self._handle_control_intent(intent, entities)
            # 音乐播放: 任何状态直接随机一首推给 APK, 不改对话状态
            elif intent == "play_music":
                await self._handle_play_music()
            # 停止音乐: 任何状态直接停掉当前音乐
            elif intent == "stop_music":
                await self._handle_stop_music()
            # 换歌: 随机挑另一首歌推给 APK
            elif intent == "next_song":
                await self._handle_next_song()
            # 音量控制: 通知 APK 调大/调小音乐音量
            elif intent == "volume_up":
                await self._handle_volume("up")
            elif intent == "volume_down":
                await self._handle_volume("down")
            # 拍照/合影/打卡: 用户主动触发，任何状态都可进入拍照流程。
            elif intent == "take_photo":
                await self._handle_take_photo()
            # 自我介绍/向大家打招呼: 任何状态都直接 TTS 一句, 不进 tour 规划/不走状态机.
            elif intent == "self_intro":
                await self._handle_self_intro()
            # 工具意图
            elif intent in ("query_help", "query_status", "list_exhibits"):
                await self._handle_utility_intent(intent, entities, clean)
            # 交互意图：按状态机路由
            else:
                state = self.ws.dialog.engine_state
                if state == "IDLE":
                    await self._on_idle(intent, entities, clean, reply_hint)
                elif state == "WELCOME":
                    await self._on_welcome(intent, entities, clean, reply_hint)
                elif state == "EXPLANATION":
                    await self._on_explanation(intent, entities, clean, reply_hint)
                elif state == "ASK_NEXT":
                    await self._on_ask_next(intent, entities, clean, reply_hint)
                elif state == "DISCUSSING":
                    await self._on_discussing(intent, entities, clean, reply_hint)
                elif state == "DISCUSS_DONE":
                    await self._on_discuss_done(intent, entities, clean, reply_hint)
                elif state == "END":
                    await self._on_end(intent, entities, clean, reply_hint)
                else:
                    logger.warning("[SmartEngine] 未知状态 %s，重置到IDLE", state)
                    self.ws.transition("IDLE")
                    await self._on_idle(intent, entities, clean, reply_hint)

        # ── 4. 打断恢复：如果之前挂起了活动，现在恢复 ──
        if interrupted or (decision.action in ("answer_only", "social", "clarify") and decision.should_resume):
            resumed = await self._try_resume_suspended()

        # ── 5. 结构化诊断日志 ──
        self._log_turn(clean, intent, confidence, decision, old_state, action_taken, interrupted, resumed, resolved_text)

        # ── 6. 访客记忆写入 ──
        if _active_visitor and clean:
            try:
                from app.engine.visitor_memory_service import save_memory, update_profile_summary
                last_reply = self.ws.dialog.last_bot_reply or ""
                exhibit_id = int(self.ws.exhibit.current_exhibit_id) if self.ws.exhibit.current_exhibit_id else None
                await save_memory(
                    visitor_id=_active_visitor["id"],
                    robot_id=self.robot_id,
                    question=clean,
                    answer=last_reply[:500],
                    intent=intent,
                    topic=self.ws.exhibit.current_exhibit_name or "",
                    exhibit_id=exhibit_id,
                )
                # 每 10 次交互更新一次画像
                if _active_visitor.get("visit_count", 0) % 10 == 0:
                    asyncio.create_task(update_profile_summary(_active_visitor["id"]))
            except Exception as e:
                logger.debug("[SmartEngine] 访客记忆写入跳过: %s", e)

    # ============================================================
    # 人脸感知入口
    # ============================================================
    async def handle_person_detected(self, data: dict):
        """检测到人"""
        name = data.get("name", "")
        distance = data.get("distance", 0)
        face_id = data.get("faceId", "")

        self.ws.visitor_detected(name=name, face_id=face_id, distance=distance)
        logger.info("[SmartEngine] 人员检测: name=%s dist=%.1f state=%s",
                    name or "陌生人", distance, self.ws.dialog.engine_state)

        # IDLE状态有人来了 → 主动迎宾
        if self.ws.dialog.engine_state == "IDLE" and distance < 2.0:
            await self._welcome_visitor(name)

    async def handle_person_left(self, data: dict):
        """人离开"""
        self.ws.visitor_left()
        logger.info("[SmartEngine] 人员离开: state=%s", self.ws.dialog.engine_state)

        # 对话中的人离开了
        if self.ws.dialog.engine_state in ("WELCOME", "ASK_NEXT", "DISCUSSING", "DISCUSS_DONE"):
            await self._speak("好的，有需要随时叫我。")
            self.ws.transition("IDLE")
            self.ws.reset_dialog()

    async def handle_no_person(self):
        """视野无人"""
        self.ws.visitor_no_person()
        if self.ws.dialog.engine_state != "IDLE":
            await self._speak("如果您需要帮助，随时叫我。")
            self.ws.transition("IDLE")
            self.ws.reset_dialog()

    async def handle_face_registered(self, data: dict):
        """人脸注册结果"""
        name = data.get("name", "")
        success = data.get("success", False)
        logger.info("[SmartEngine] 人脸注册: name=%s success=%s", name, success)
        if success and name:
            await self._speak(f"{name}，已经记住您了，下次见面直接打招呼！")

    # ============================================================
    # 状态机处理
    # ============================================================
    async def _on_idle(self, intent, entities, text, reply_hint):
        """IDLE状态：等待唤醒"""
        if intent in ("start_tour", "chitchat"):
            # 开始参观 或 闲聊打招呼
            await self._start_interaction(entities, text)
        elif intent == "interactive_discuss":
            await self._start_discussion(entities.get("topic", ""))
        elif intent == "list_exhibits":
            await self._list_exhibits()
        elif intent == "list_exhibit_resources":
            target = entities.get("target", "")
            if target:
                await self._enumerate_exhibit_resources(target)
            else:
                await self._list_exhibits()
        elif intent in ("ask_explain", "ask_current"):
            # 直接问问题 / 问当前展项
            await self._start_interaction(entities, text)
        elif intent == "navigate_to":
            # 直接要去某处
            target = entities.get("target", "")
            if target:
                await self._navigate_to_exhibit(target)
            else:
                await self._start_interaction(entities, text)
        elif intent == "set_time":
            # IDLE下说"参观X分钟" → 直接启动按时长规划
            minutes = entities.get("minutes", 20)
            await self._speak(f"好的，我来为您规划{minutes}分钟的参观路线。")
            await self._begin_tour(entities, text, time_budget=int(minutes))
        elif intent == "wait_please":
            pass  # IDLE不用等
        else:
            # 其他意图在IDLE时当闲聊处理
            if reply_hint:
                await self._speak(reply_hint)
            else:
                await self._smart_speak(
                    "您好，我可以带您参观展厅。您可以说「开始参观」，或说「有哪些展项」浏览展品。",
                    "用户在待机状态说了什么，自然回应并引导参观"
                )

    async def _on_welcome(self, intent, entities, text, reply_hint):
        """WELCOME状态：刚打过招呼，等用户回应"""
        if intent == "navigate_to":
            await self._begin_tour(entities, text)
        elif intent == "start_tour":
            if entities.get("script_id") or entities.get("target"):
                await self._begin_tour(entities, text)
            else:
                await self._guide_before_tour()
        elif intent == "set_time":
            minutes = entities.get("minutes", 20)
            await self._speak(f"好的，我来为您规划{minutes}分钟的参观路线。")
            await self._begin_tour(entities, text, time_budget=minutes)
        elif intent == "ask_explain":
            # 直接开始讲解当前展项
            await self._explain_current_exhibit()
        elif intent == "interactive_discuss":
            await self._start_discussion(entities.get("topic", ""))
        elif intent == "end_tour":
            await self._end_tour()
        elif intent in ("chitchat", "weather_query"):
            await self._handle_chitchat(text, reply_hint)
        else:
            # 引导用户
            await self._speak("您可以说「开始参观」，或者告诉我您想看什么。")

    async def _on_explanation(self, intent, entities, text, reply_hint):
        """EXPLANATION状态：正在讲解中"""
        if intent == "wait_please":
            await self._speak("好的，您慢慢看。")
            self.ws.transition("ASK_NEXT")
        elif intent == "skip_exhibit":
            await self._next_exhibit()
        elif intent == "end_tour":
            await self._end_tour()
        elif intent == "interactive_discuss":
            await self._start_discussion(entities.get("topic", ""))
        elif intent == "play_resource":
            # 讲解中用户指定播放某个资源
            await self._play_resource_by_index(entities.get("resource_index", 1))
        elif intent in ("ask_explain", "ask_current"):
            # 讲解中再问，可以补充说明
            await self._handle_followup_question(text)
        else:
            # 其他输入当打断, 切到 ASK_NEXT 并交给其 handler 处理.
            # 这是合法的中断模式(用户讲解中途插话), 但要防深度递归: 若已经在
            # _on_ask_next 触发的 _on_explanation 里再回 ASK_NEXT 就死循环.
            if getattr(self, "_dispatch_depth", 0) >= 1:
                logger.warning("[SmartEngine] EXPLANATION 递归深度保护, 直接给引导")
                await self._speak("您可以说「下一个」、「结束参观」，或说展项名称跳转。")
                self.ws.transition("ASK_NEXT")
                return
            self._dispatch_depth = getattr(self, "_dispatch_depth", 0) + 1
            try:
                self.ws.transition("ASK_NEXT")
                await self._on_ask_next(intent, entities, text, reply_hint)
            finally:
                self._dispatch_depth = max(0, self._dispatch_depth - 1)

    async def _on_ask_next(self, intent, entities, text, reply_hint):
        """ASK_NEXT状态：询问下一步（循环核心）"""
        # auto_plan 活跃时的优先拦截: 下一个/换一个/不想看/结束
        if self._auto_plan:
            if await self._handle_auto_plan_interrupt(intent, text):
                self.ws.record_turn(text, intent)
                return
        # 浏览子流程拦截
        if self.ws.browse.mode:
            handled = await self._handle_browse_input(intent, entities, text, reply_hint)
            if handled:
                self.ws.record_turn(text, intent)
                return
            # 未拦截则回退到正常 ASK_NEXT 逻辑

        if intent in ("end_tour", "chitchat") and text.strip() in ("结束", "参观完", "不用了", "可以了", "再见", "谢谢"):
            await self._end_tour()
            return

        # 模式选择态：只在接待区欢迎后、系统明确询问“三种参观方式”时承接。
        # 其他展区/资源浏览里的“第一个/第二个/第三个”必须优先作为当前列表序号处理。
        clean_text = (text or "").strip()
        mode_choice = _normalize_tour_mode_choice(clean_text) if getattr(self, "_awaiting_tour_mode_choice", False) else ""
        if mode_choice == "preset":
            self._awaiting_tour_mode_choice = False
            self._awaiting_smart_plan_minutes = False
            await self._begin_preset_tour()
            return
        if mode_choice == "smart":
            self._awaiting_tour_mode_choice = False
            self._awaiting_smart_plan_minutes = True
            self.ws.transition("ASK_NEXT")
            await self._speak("请问您大概有多少分钟？")
            return
        if mode_choice == "free":
            self._awaiting_tour_mode_choice = False
            self._awaiting_smart_plan_minutes = False
            await self._list_exhibits()
            return
        if getattr(self, "_awaiting_smart_plan_minutes", False) and intent == "set_time":
            self._awaiting_tour_mode_choice = False
            self._awaiting_smart_plan_minutes = False
            minutes = entities.get("minutes", 20)
            await self._speak(f"好的，我来为您规划{minutes}分钟的参观路线。")
            await self._begin_tour(entities, f"智能规划{minutes}分钟参观路线", time_budget=int(minutes))
            return
        if getattr(self, "_awaiting_smart_plan_minutes", False) and clean_text in ("随便", "都可以", "都行", "你看着安排"):
            minutes = int(getattr(self, "_default_smart_plan_minutes", 15) or 15)
            self._awaiting_tour_mode_choice = False
            self._awaiting_smart_plan_minutes = False
            await self._speak(f"好的，我按{minutes}分钟为您智能规划参观路线。")
            await self._begin_tour({"minutes": minutes}, f"智能规划{minutes}分钟参观路线", time_budget=minutes)
            return
        if intent in ("start_tour", "chitchat") and clean_text in ("自动讲解", "开始讲解", "开始参观", "带我参观"):
            # 兼容旧说法：默认按预设路线。
            self._awaiting_smart_plan_minutes = False
            await self._begin_preset_tour()
            return

        if intent == "end_tour":
            await self._end_tour()
        elif intent == "play_resource":
            await self._play_resource_by_index(entities.get("resource_index", 1))
        elif intent == "play_next":
            await self._next_exhibit()
        elif intent == "skip_exhibit":
            await self._next_exhibit()
        elif intent in ("ask_explain", "ask_current"):
            await self._explain_current_exhibit()
        elif intent == "navigate_to":
            target = entities.get("target", "")
            await self._navigate_to_exhibit(target)
        elif intent == "list_exhibit_resources":
            target = entities.get("target", "")
            if target:
                await self._enumerate_exhibit_resources(target)
            else:
                await self._list_exhibits()
        elif intent == "interactive_discuss":
            await self._start_discussion(entities.get("topic", ""))
        elif intent == "start_tour":
            # 重新开始参观
            if entities.get("script_id") or entities.get("target"):
                await self._begin_tour(entities, text)
            else:
                await self._guide_before_tour()
        elif intent == "wait_please":
            await self._speak("好的，不着急，您慢慢看。")
        elif intent == "resume_please":
            await self._prompt_next()
        elif intent == "chitchat":
            await self._handle_chitchat(text, reply_hint)
        elif intent == "weather_query":
            await self._handle_chitchat(text, reply_hint)
        elif intent == "set_time":
            minutes = entities.get("minutes", 20)
            await self._speak(f"好的，我来为您重新规划{minutes}分钟的参观路线。")
            await self._begin_tour(entities, text, time_budget=int(minutes))
        else:
            # fallback：LLM的回答或无法理解
            if reply_hint:
                await self._speak(reply_hint)
            else:
                await self._speak("没听清楚，您可以说「下一个」继续参观，说展项名称前往，或说「结束参观」。")

        # 记录交互
        self.ws.record_turn(text, intent)

    async def _on_end(self, intent, entities, text, reply_hint):
        """END状态"""
        if intent == "start_tour":
            # 重新开始
            self.ws.reset_dialog()
            await self._start_interaction(entities, text)
        elif intent == "interactive_discuss":
            self.ws.reset_dialog()
            await self._start_discussion(entities.get("topic", ""))
        else:
            # END后其他输入回到IDLE
            self.ws.transition("IDLE")

    # ============================================================
    # 话题讨论
    # ============================================================
    async def _start_discussion(self, topic: str):
        """启动话题讨论"""
        if not topic:
            await self._speak("请问您想讨论什么话题？")
            return

        # 1. 暂停正在运行的流程
        from app.engine.flow_executor import get_active_flow, pause_flow
        active_flow = get_active_flow(self.robot_id)
        self._was_flow_running = active_flow is not None and active_flow.status == "running"
        if self._was_flow_running:
            await pause_flow(self.robot_id)
            logger.info("[SmartEngine] 讨论前暂停流程: %s", active_flow.script_name)

        # 2. 创建讨论会话
        from app.engine.discussion_engine import DiscussionSession
        self._discussion = DiscussionSession(topic, self.robot_id)
        self.ws.transition("DISCUSSING")

        # 3. 生成对话
        await self._speak(f"好的，让我们来讨论一下{topic}。")
        world_ctx = self.ws.to_context_string() if hasattr(self.ws, 'to_context_string') else ""
        success = await self._discussion.generate_dialogue(world_context=world_ctx)
        if not success:
            await self._speak("抱歉，话题讨论生成失败，我们继续参观吧。")
            self._discussion = None
            if self._was_flow_running:
                from app.engine.flow_executor import resume_flow
                await resume_flow(self.robot_id)
                self._was_flow_running = False
                self.ws.transition("EXPLANATION")
            else:
                self.ws.transition("ASK_NEXT")
                await self._prompt_next()
            return

        # 4. 执行讨论
        await self._execute_discussion()

    async def _execute_discussion(self):
        """执行讨论对话（3轮交替播报）"""
        if not self._discussion:
            return
        from app.engine.discussion_engine import digital_human_speak, digital_human_show

        # 讨论开播前, 先把 pad 切到当前所在展项对应的数字人形象,
        # 避免沿用上一展项的角色. 失败不阻断讨论, 只打日志.
        current_exhibit_id = (self.ws.exhibit.current_exhibit_id or "").strip()
        try:
            ok = await digital_human_show(exhibition_id=current_exhibit_id)
            logger.info(
                "[SmartEngine] 讨论前切换数字人形象 exhibition_id=%r ok=%s",
                current_exhibit_id or "(default)",
                ok,
            )
        except Exception as e:
            logger.warning("[SmartEngine] 讨论前切换数字人形象异常(继续): %s", e)

        session = self._discussion
        for i, round_data in enumerate(session.dialogue_rounds):
            if session.is_cancelled():
                break

            # 机器人播报
            robot_text = round_data.get("robot", "")
            if robot_text:
                await self._speak(robot_text)
                if session.is_cancelled():
                    break
                await asyncio.sleep(2.0)

            # 数字人播报
            dh_text = round_data.get("digital_human", "")
            if dh_text:
                await digital_human_speak(dh_text)
                if i < len(session.dialogue_rounds) - 1:
                    await asyncio.sleep(1.5)

        # 讨论完成
        self._discussion = None
        self.ws.transition("DISCUSS_DONE")
        if self._was_flow_running:
            await self._speak("讨论就到这里。要继续之前的参观吗？说「继续」就好。")
        else:
            await self._speak("讨论就到这里。您还想做什么？")

    async def _on_discussing(self, intent, entities, text, reply_hint):
        """DISCUSSING状态：正在执行话题讨论，仅响应中断"""
        if intent == "end_tour":
            # 用户要中断讨论
            if self._discussion:
                self._discussion.cancel()
            self._discussion = None
            if self._was_flow_running:
                self.ws.transition("DISCUSS_DONE")
                await self._speak("好的，讨论结束。要继续之前的参观吗？说「继续」就好。")
            else:
                self.ws.transition("ASK_NEXT")
                await self._speak("好的，不讨论了。您还想做什么？")
        elif intent == "interactive_discuss":
            # 讨论中用户提新话题，先结束后再开始新的
            if self._discussion:
                self._discussion.cancel()
            self._discussion = None
            logger.info("[SmartEngine] 讨论中切换新话题: %s", entities.get("topic", ""))
            await self._start_discussion(entities.get("topic", ""))
        # 其他意图在讨论中忽略，等讨论结束后处理

    async def _on_discuss_done(self, intent, entities, text, reply_hint):
        """DISCUSS_DONE状态：讨论结束，等待用户决定是否继续"""
        if intent == "resume_please" or text.strip() in ("继续", "好了继续", "可以继续", "继续参观"):
            # 恢复之前暂停的流程
            if self._was_flow_running:
                from app.engine.flow_executor import resume_flow
                result = await resume_flow(self.robot_id)
                if result.get("status") == "ok":
                    self._was_flow_running = False
                    await self._speak("好的，继续之前的参观流程。")
                    self.ws.transition("EXPLANATION")
                else:
                    # 流程已不存在（可能超时取消了）
                    self._was_flow_running = False
                    await self._speak("之前的流程已结束。您还想做什么？")
                    self.ws.transition("ASK_NEXT")
                    await self._prompt_next()
            else:
                self.ws.transition("ASK_NEXT")
                await self._prompt_next()
        elif intent == "end_tour":
            self._was_flow_running = False
            await self._end_tour()
        elif intent == "interactive_discuss":
            # 用户想讨论新话题，先恢复之前的流程
            if self._was_flow_running:
                from app.engine.flow_executor import resume_flow
                await resume_flow(self.robot_id)
                self._was_flow_running = False
            await self._start_discussion(entities.get("topic", ""))
        elif intent == "start_tour":
            # 重新开始参观（放弃之前的流程）
            self._was_flow_running = False
            if entities.get("script_id") or entities.get("target"):
                await self._begin_tour(entities, text)
            else:
                await self._guide_before_tour()
        else:
            # 其他意图: flow 运行中引导; 否则转 ASK_NEXT 分发 (同样带深度保护)
            if self._was_flow_running:
                await self._speak("您可以说「继续」恢复之前的参观，或者说「结束参观」。")
            else:
                if getattr(self, "_dispatch_depth", 0) >= 1:
                    logger.warning("[SmartEngine] DISCUSS_DONE 递归深度保护, 直接给引导")
                    await self._speak("您可以说「下一个」、展项名称、或「结束参观」。")
                    self.ws.transition("ASK_NEXT")
                    return
                self._dispatch_depth = getattr(self, "_dispatch_depth", 0) + 1
                try:
                    self.ws.transition("ASK_NEXT")
                    await self._on_ask_next(intent, entities, text, reply_hint)
                finally:
                    self._dispatch_depth = max(0, self._dispatch_depth - 1)

    # ============================================================
    # 高级动作
    # ============================================================
    async def _welcome_visitor(self, name: str = ""):
        """主动迎宾"""
        self.ws.transition("WELCOME")
        if name:
            fallback = f"{name}您好！欢迎来到展厅，我可以带您参观，您说「开始参观」就好。"
        else:
            fallback = "您好！欢迎来到展厅，我可以带您参观讲解，您说「开始参观」就好。"
        await self._smart_speak(
            fallback,
            f"热情地欢迎{'访客'+name if name else '这位访客'}来到展厅，提醒可以说开始参观"
        )
        self.ws.record_turn("", "welcome")

    async def _start_interaction(self, entities: dict, text: str):
        """开始交互（用户主动触发）"""
        self._finish_legacy_teaching_state("CANCELLED", "new_reception")
        from app.engine.teaching_runtime import reception_id
        reception_id(self.robot_id, reset=True)
        self.ws.transition("WELCOME")
        script_id = entities.get("script_id")
        minutes = entities.get("minutes")
        if script_id:
            # 匹配到流程触发词，用预设流程
            await self._speak("好的，马上开始！")
            await self._run_script_tour(script_id)
        elif entities.get("target"):
            # 有明确目标展项，直接开始
            await self._begin_tour(entities, text)
        elif minutes:
            # 按时长规划（"我想参观10分钟"）
            await self._speak(f"好的，我来为您规划{minutes}分钟的参观路线。")
            await self._begin_tour(entities, text, time_budget=int(minutes))
        else:
            # 泛化话术（"参观一下"/"开始参观"），不直接启动默认路线，先引导确认
            await self._guide_before_tour()

    async def _guide_before_tour(self):
        """进入模式选择：介绍三种参观方式，等待用户选择。

        这是接待欢迎 yes 后的标准下一步。只进入选择态，不直接 10 秒循环问候。
        """
        self.ws.transition("ASK_NEXT")
        self._awaiting_tour_mode_choice = True
        self._awaiting_smart_plan_minutes = False
        try:
            self.ws.reset_browse()
        except Exception:
            pass
        await self._speak(
            "展厅有三种参观方式。"
            "第一种是预设路线参观，我会按固定顺序带您游览所有展项。"
            "第二种是智能规划参观，告诉我您有多少时间，我为您量身定制路线。"
            "第三种是自由选择参观，您可以指定感兴趣的展项，我重点讲解。"
            "您想选哪一种呢？"
        )

    async def _begin_preset_tour(self):
        """第一种：预设路线参观。

        恢复原流程：走后台 tour_scripts 配置好的导览脚本，而不是 SmartEngine 临时 _load_plan。
        默认优先启动「日常参观讲解」/ 含「开始讲解」触发词的启用脚本；找不到再兜底旧静态路线。
        """
        script_id = await self._find_default_preset_script_id()
        if script_id:
            logger.info("[SmartEngine] 预设路线 → 启动后台导览脚本 script_id=%s", script_id)
            await self._run_script_tour(int(script_id))
            return
        logger.warning("[SmartEngine] 预设路线未找到后台脚本，兜底静态路线")
        await self._load_plan(0)
        self._plan_index = 0
        self.ws.transition("EXPLANATION")
        if self._plan:
            await self._explain_current_exhibit()
        else:
            await self._speak("当前还没有配置预设路线，我先为您列出可参观的展项。")
            await self._list_exhibits()

    async def _find_default_preset_script_id(self) -> int | None:
        """查找后台配置的默认预设路线流程脚本。"""
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                """
                SELECT id, name, trigger_word
                FROM tour_scripts
                WHERE enabled=1
                ORDER BY
                  CASE
                    WHEN name='日常参观讲解' THEN 0
                    WHEN trigger_word LIKE '%开始讲解%' THEN 1
                    WHEN trigger_word LIKE '%开始参观%' THEN 2
                    ELSE 9
                  END,
                  id ASC
                LIMIT 1
                """
            )
            if rows:
                return int(rows[0]["id"])
        except Exception as e:
            logger.warning("[SmartEngine] 查询默认预设路线脚本失败: %s", e)
        finally:
            await db.close()
        return None

    async def _begin_tour(self, entities: dict, text: str, time_budget: int = 0):
        """开始参观讲解"""
        # target 已指定(如 "带我参观大岛台") → 单展项定向访问, 走交互资源选择
        target = (entities or {}).get("target", "")
        if target and time_budget <= 0:
            await self._navigate_to_exhibit(target)
            return

        # 智能规划: feature flag 开且触发条件满足 → 让 LLM 编排自定义路线
        # 任一步失败则回落到静态 _load_plan
        # 把 time_budget 透传, 让 _try_auto_plan 决定是否 autonomous
        used_auto = await self._try_auto_plan(text, time_budget=time_budget)
        if used_auto:
            return

        # 加载展项知识，规划路线
        await self._load_plan(time_budget)
        self._plan_index = 0
        self.ws.transition("EXPLANATION")

        if self._plan:
            # 开始第一个展项
            await self._explain_current_exhibit()
        else:
            await self._speak("我来看看有哪些展项可以为您介绍。")
            # 查询展项列表
            db = await get_db()
            try:
                exhibits = await db.execute_fetchall(
                    "SELECT id, name FROM exhibits ORDER BY id LIMIT 5"
                )
                if exhibits:
                    names = "、".join(e["name"] for e in exhibits)
                    await self._speak(f"目前有{names}等展项，您想看哪个？")
                    self.ws.transition("ASK_NEXT")
                else:
                    await self._speak("暂时没有可参观的展项。")
                    self.ws.transition("IDLE")
                    self.ws.reset_dialog()
            finally:
                await db.close()

    async def _try_auto_plan(self, text: str, time_budget: int = 0) -> bool:
        """尝试用 tour_planner 自动编排路线. 返回是否成功启动 auto plan.

        time_budget>0 (用户给了 X 分钟) → 走 autonomous 模式, 整套讲解连续跑, 不在中途停下问.
        其他场景 (随便/为我定制 等) → 非 autonomous, 每个展项后停下确认.
        """
        try:
            from app.engine.tour_planner import (
                is_auto_plan_enabled, should_auto_plan,
                build_visitor_context, auto_plan,
            )
            if not await is_auto_plan_enabled():
                return False
            # 先拿 visitor (低成本 DB 读), 判断是否触发
            visitor = await build_visitor_context(self.robot_id)
            if not should_auto_plan(text, visitor):
                return False
            # autonomous: ???????? ? ??"???????"; ????.
            autonomous = bool(time_budget and time_budget > 0)
            # ???????????? LLM ????? LLM ??? plan ??????????
            # ??????????????????????/????????????
            if autonomous:
                await self._speak(f"好的，我帮您按{time_budget}分钟规划一条参观路线，马上开始。")
            else:
                await self._speak("好的，我根据您的情况为您规划一下路线，稍等。")
            plan = await auto_plan(self.robot_id, text, autonomous=autonomous)
            if not plan or not plan.steps:
                if autonomous:
                    logger.info("[SmartEngine] auto_plan ????? plan, ????????????")
                    fallback = await self._build_smart_plan_by_minutes(text, time_budget)
                    if not fallback or not fallback.steps:
                        fallback = await self._build_deterministic_auto_plan(text, time_budget)
                    if fallback and fallback.steps:
                        await self._execute_auto_plan(fallback)
                        return True
                logger.info("[SmartEngine] auto_plan ????? plan, ??????")
                return False
            logger.info("[SmartEngine] auto_plan: %s (%d steps, %ds, autonomous=%s) — %s",
                        plan.title, len(plan.steps), plan.est_total_seconds,
                        plan.autonomous, plan.rationale)
            # 把 TourPlan.steps 转成 _plan 的 list 并 execute
            await self._execute_auto_plan(plan)
            return True
        except Exception as e:
            logger.warning("[SmartEngine] _try_auto_plan 异常, 回落: %s", e, exc_info=True)
            return False

    def _smart_plan_minute_band(self, minutes: int) -> dict:
        """第二种智能规划的分钟档策略。"""
        m = int(minutes or getattr(self, "_default_smart_plan_minutes", 15) or 15)
        if m <= 5:
            band = 5
        elif m <= 10:
            band = 10
        elif m <= 15:
            band = 15
        elif m <= 20:
            band = 20
        else:
            band = 30
        return {
            5: {"minutes": 5, "title": "5分钟核心速览路线", "names": ["大岛台正面"], "open": "好的，我按5分钟为您安排核心速览，我们重点看大岛台正面，最后做一个简短总结。", "summary": "5分钟核心速览就到这里。大岛台正面展示的是展厅的核心能力，您可以把它理解为全场方案的总览窗口。", "resource_wait": 120, "target_elapsed": 300},
            10: {"minutes": 10, "title": "10分钟大小岛台精华路线", "names": ["大岛台正面", "小岛台正面"], "open": "好的，我按10分钟为您安排精华路线，重点看大岛台正面和小岛台正面。", "summary": "10分钟精华路线就到这里。大岛台负责呈现整体方案，小岛台补充业务场景和落地细节，两处合起来就是本展区的重点。", "resource_wait": 150, "target_elapsed": 600},
            15: {"minutes": 15, "title": "15分钟标准完整路线", "names": ["入口三联屏", "CAVE空间投影", "大岛台正面", "小岛台正面"], "open": "好的，我按15分钟标准版带您完整参观4个展项。", "summary": "15分钟标准路线参观完毕。我们从入口总览、沉浸式空间，到大岛台和小岛台，完整看完了本展区的主要内容。", "resource_wait": 150, "target_elapsed": 900},
            20: {"minutes": 20, "title": "20分钟完整扩展路线", "names": ["入口三联屏", "CAVE空间投影", "大岛台正面", "小岛台正面"], "open": "好的，我按20分钟为您安排完整路线，并在每个重点展项后补充一点扩展说明。", "summary": "20分钟完整扩展路线就到这里。刚才四个展项已经覆盖总览、沉浸体验、核心能力和场景落地。您如果想深挖某一块，可以直接问我。", "resource_wait": 180, "target_elapsed": 1200, "extended": True},
            30: {"minutes": 30, "title": "30分钟完整互动路线", "names": ["入口三联屏", "CAVE空间投影", "大岛台正面", "小岛台正面"], "open": "好的，我按30分钟为您安排完整讲解路线，后面也预留一点拍照、音乐或互动问答时间。", "summary": "30分钟完整讲解先到这里。四个核心展项已经讲完，后面您可以继续问我细节，也可以说拍照、合照、放首歌，体验一下趣味互动。", "resource_wait": 240, "target_elapsed": 1800, "extended": True, "fun": True},
        }[band]

    async def _build_smart_plan_by_minutes(self, text: str, time_budget: int):
        """按用户提供的分钟档生成第二种接待路线。"""
        try:
            from app.engine.tour_planner import snapshot_capabilities, TourPlan, TourStep
            cfg = self._smart_plan_minute_band(int(time_budget or self._default_smart_plan_minutes))
            snap = await snapshot_capabilities(self.robot_id, text or f"智能规划{cfg['minutes']}分钟参观路线")
            usable = [e for e in snap.exhibits if e.has_position and e.resource_count > 0]
            if not usable:
                logger.warning("[SmartEngine] 第二种分钟档路线失败: 无可用展项")
                return None
            by_name = {e.name: e for e in usable}
            selected = []
            for name in cfg["names"]:
                ex = by_name.get(name)
                if ex:
                    selected.append(ex)
                else:
                    logger.warning("[SmartEngine] 第二种分钟档缺少配置展项: %s", name)
            if not selected:
                usable.sort(key=lambda e: (not e.is_core, int(e.id)))
                selected = usable[:1]
            steps = [TourStep(type="speak", payload={"text": cfg["open"]}, est_seconds=max(6, len(cfg["open"]) // 3))]
            total = len(selected)
            for i, ex in enumerate(selected, start=1):
                brief = (getattr(ex, "narration_brief", "") or "").strip()
                intro = brief[:150] if brief else f"这里是{ex.name}，我带您重点了解它在本展区中的核心作用。"
                prefix = "第一站" if i == 1 else f"第{i}站"
                intro = f"{prefix}，{intro}"
                steps.extend([
                    TourStep(type="navigate_exhibit", payload={"exhibit_id": ex.id, "exhibit_name": ex.name}, est_seconds=30),
                    TourStep(type="speak", payload={"text": intro}, est_seconds=max(8, len(intro) // 3)),
                    TourStep(type="play_resource", payload={"exhibit_id": ex.id, "resource_index": 1}, est_seconds=int(cfg.get("resource_wait", 10) or 10)),
                ])
                if cfg.get("extended"):
                    extra = f"这里可以重点关注{ex.name}背后的业务价值：它不是单点展示，而是和整体展厅方案联动，体现从技术能力到实际场景的落地。"
                    steps.append(TourStep(type="speak", payload={"text": extra}, est_seconds=max(8, len(extra) // 3)))
                if i < total:
                    next_name = selected[i].name
                    steps.append(TourStep(type="speak", payload={"text": f"这一站先看到这里，咱们继续去看{next_name}。"}, est_seconds=8))
            if cfg.get("fun"):
                fun_text = "完整讲解之后，我也可以配合您做趣味互动，比如拍照打卡、播放音乐，或者继续回答您关心的问题。"
                steps.append(TourStep(type="speak", payload={"text": fun_text}, est_seconds=14))
            # ?????????????????/TTS/??????????????
            # ??????????? step??????????????15??3-5??????
            target_elapsed = int(cfg.get("target_elapsed", 0) or 0)
            summary_step = TourStep(type="speak", payload={"text": cfg["summary"]}, est_seconds=max(10, len(cfg["summary"]) // 3))
            current_est = sum(s.est_seconds for s in steps) + summary_step.est_seconds
            if target_elapsed and current_est < target_elapsed:
                dwell = target_elapsed - current_est
                steps.append(TourStep(type="dwell", payload={"reason": "minute_band_fill", "seconds": dwell}, est_seconds=dwell))
            steps.append(summary_step)
            plan = TourPlan(title=cfg["title"], rationale=f"??????{cfg['minutes']}??????" + "?".join(e.name for e in selected), steps=steps, est_total_seconds=sum(s.est_seconds for s in steps), autonomous=True)
            logger.info("[SmartEngine] ????????: minutes=%s exhibits=%s steps=%d est=%ds", cfg["minutes"], [e.name for e in selected], len(steps), plan.est_total_seconds)
            return plan
        except Exception as e:
            logger.warning("[SmartEngine] 第二种分钟档路线异常: %s", e, exc_info=True)
            return None

    async def _build_deterministic_auto_plan(self, text: str, time_budget: int):
        """智能规划 LLM 失败时的确定性兜底路线。

        这是“第二种/智能规划”的兜底，不是自由选择：必须 autonomous=True，自动导航+播放资源，
        不能回落到单展项资源列表让用户再选。
        """
        try:
            from app.engine.tour_planner import snapshot_capabilities, TourPlan, TourStep
            default_minutes = int(getattr(self, "_default_smart_plan_minutes", 15) or 15)
            minutes_for_plan = int(time_budget or default_minutes)
            snap = await snapshot_capabilities(self.robot_id, text or f"智能规划{minutes_for_plan}分钟参观路线")
            budget_sec = max(60, minutes_for_plan * 60)
            candidates = [e for e in snap.exhibits if e.has_position and e.resource_count > 0]
            if not candidates:
                logger.warning("[SmartEngine] 确定性自动路线兜底失败: 无可用展项")
                return None

            # 优先核心展项、资源多的展项；按预算截断，至少一个，最多五个。
            candidates.sort(key=lambda e: (not e.is_core, -int(e.resource_count or 0), int(e.id)))
            per_exhibit_sec = 150  # 导航30 + 简述30 + 播资源约90
            max_count = max(1, min(5, budget_sec // per_exhibit_sec))
            selected = candidates[:max_count]

            steps = [TourStep(
                type="speak",
                payload={"text": f"好的，我按{minutes_for_plan}分钟为您规划一条自动参观路线，接下来我会按顺序带您参观。"},
                est_seconds=12,
            )]
            for ex in selected:
                brief = (getattr(ex, "narration_brief", "") or "").strip()
                intro = brief[:120] if brief else f"这里是{ex.name}，我带您重点了解它的核心内容。"
                steps.extend([
                    TourStep(type="navigate_exhibit", payload={"exhibit_id": ex.id, "exhibit_name": ex.name}, est_seconds=30),
                    TourStep(type="speak", payload={"text": intro}, est_seconds=max(15, len(intro) // 3)),
                    TourStep(type="play_resource", payload={"exhibit_id": ex.id, "resource_index": 1}, est_seconds=min(120, max(45, int(getattr(ex, "talk_time", 90) or 90)))),
                    TourStep(type="prompt_next", payload={}, est_seconds=2),
                ])
            steps.append(TourStep(type="speak", payload={"text": "这条智能规划路线参观完毕，您还想了解哪个展项，可以直接告诉我。"}, est_seconds=12))
            plan = TourPlan(
                title=f"{minutes_for_plan}分钟自动参观兜底路线",
                rationale="LLM 智能规划失败时，基于当前专场可导航且有资源的展项确定性生成自动路线。",
                steps=steps,
                est_total_seconds=sum(s.est_seconds for s in steps),
                autonomous=True,
            )
            logger.info("[SmartEngine] 确定性自动路线兜底: %d exhibits, %d steps, %ds",
                        len(selected), len(steps), plan.est_total_seconds)
            return plan
        except Exception as e:
            logger.warning("[SmartEngine] 确定性自动路线兜底异常: %s", e, exc_info=True)
            return None

    async def _build_auto_plan_commentary_segments(self, ex_id: int, ex_name: str, res_name: str = "", res_id: int = 0) -> list[str]:
        """为第二种智能规划的资源展示等待期生成有声补充。

        优先使用当前播放资源的 narration_text/description，让讲解和屏幕资源更贴近；
        资源资料不足时再回落到展项 exhibit_knowledge.narration_text。
        """
        name = (ex_name or "这个展项").strip()
        resource = (res_name or "当前内容").strip()
        resource_text = ""
        exhibit_text = ""
        try:
            from app.database import get_db
            db = await get_db()
            try:
                if res_id:
                    rows = await db.execute_fetchall(
                        "SELECT narration_text, description, name FROM resources WHERE id=? LIMIT 1",
                        (int(res_id),),
                    )
                    if rows:
                        rr = rows[0]
                        resource = (rr["name"] or resource or "当前内容").strip()
                        resource_text = (rr["narration_text"] or rr["description"] or "").strip()
                rows = await db.execute_fetchall(
                    "SELECT narration_text FROM exhibit_knowledge WHERE exhibit_id=CAST(? AS TEXT) LIMIT 1",
                    (int(ex_id or 0),),
                )
                if rows:
                    exhibit_text = (rows[0]["narration_text"] or "").strip()
            finally:
                await db.close()
        except Exception as e:
            logger.debug("[SmartEngine] 读取资源/展项讲解词失败 ex_id=%s res_id=%s: %s", ex_id, res_id, e)

        def _split_segments(text: str, limit: int) -> list[str]:
            if not text:
                return []
            import re as _re
            sentences = [x.strip() for x in _re.split(r"[\u3002\uff01\uff1f!?\uff1b;\n]+", text) if x.strip()]
            parts: list[str] = []
            buf = ""
            for sent in sentences:
                if len(sent) < 6:
                    continue
                if not buf:
                    buf = sent
                elif len(buf) + len(sent) <= 90:
                    buf += "。" + sent
                else:
                    parts.append(buf + "。")
                    buf = sent
                if len(parts) >= limit:
                    break
            if buf and len(parts) < limit:
                parts.append(buf + "。")
            return parts

        resource_parts = _split_segments(resource_text, 4)
        exhibit_parts = _split_segments(exhibit_text, 3)
        prefix = f"现在屏幕上展示的是{resource}，" if resource and resource != "当前内容" else "现在屏幕上展示的是当前内容，"
        fallback = [
            f"{prefix}大家可以先看屏幕里的主题和画面变化，我会结合{name}说明它对应的实际场景。",
            f"这段内容和{name}关联比较紧，重点看它如何把技术能力转成可以落地的业务效果。",
            "如果屏幕里出现案例或流程画面，可以重点看它解决了什么问题，以及最后呈现出的结果。",
            "我再留一点时间给大家看屏幕内容，稍后带您继续往下参观。",
        ]
        merged: list[str] = []
        for text in [*resource_parts, *exhibit_parts, *fallback]:
            t = (text or "").strip()
            if t and t not in merged:
                merged.append(t[:160])
        return merged[:8]

    async def _execute_auto_plan(self, plan):
        """初次启动 TourPlan: 记录状态 + 执行到第一个 prompt_next 停下等用户.

        后续用户说"下一个/换一个/不想看"时由 _handle_auto_plan_interrupt 继续推进.
        """
        self._auto_plan = plan
        self._auto_plan_idx = 0
        self._auto_plan_completed_exhibits = set()
        self._auto_plan_start_ts = time.time()
        self.ws.transition("EXPLANATION")
        await self._advance_auto_plan()

    async def _advance_auto_plan(self):
        """从 self._auto_plan_idx 开始执行.

        autonomous=True: 跳过 prompt_next, play_resource 后按 est_seconds 等资源自然播完,
                         整套讲解一气呵成, 像跑一个配置好的流程.
        autonomous=False (传统): 遇 prompt_next 停下问用户.
        """
        if not self._auto_plan:
            return
        autonomous = bool(getattr(self._auto_plan, "autonomous", False))
        # 自动模式下用户随时可以喊 "暂停/停" / "继续" — 通过 _auto_plan_paused 标志同步.
        # interrupt 处理在 _handle_auto_plan_interrupt, 这里只检查标志.
        if not hasattr(self, "_auto_plan_paused"):
            self._auto_plan_paused = False

        while self._auto_plan_idx < len(self._auto_plan.steps):
            # 取消(用户说 结束/不用了 等)
            if not self._auto_plan:
                return
            # 暂停态 — 留个观察窗口, 1s 轮询一次, 直到用户说"继续"或者结束
            while autonomous and getattr(self, "_auto_plan_paused", False):
                if not self._auto_plan:
                    return
                await asyncio.sleep(1.0)

            step = self._auto_plan.steps[self._auto_plan_idx]
            t = step.type
            p = step.payload or {}
            est = max(0, int(getattr(step, "est_seconds", 0) or 0))
            self._auto_plan_idx += 1  # 先自增, 失败也不卡
            try:
                if t == "speak":
                    txt = (p.get("text") or "").strip()
                    if txt:
                        await self._speak(txt)
                elif t == "navigate_exhibit":
                    ex_id = int(p.get("exhibit_id", 0) or 0)
                    ex_name = (p.get("exhibit_name") or "").strip()
                    if ex_id:
                        self.ws.set_current_exhibit(str(ex_id), ex_name)
                        self._auto_plan_completed_exhibits.add(ex_id)
                        position = await self.executor.find_position_for_exhibit(ex_id)
                        if position:
                            await self._execute_with_feedback(
                                f"导航到{ex_name}",
                                self.executor.navigate(position),
                                "", f"导航到{ex_name}没走通, 先在这里讲.",
                            )
                elif t == "play_resource":
                    ex_id = int(p.get("exhibit_id", 0) or 0)
                    idx = int(p.get("resource_index", 1) or 1)
                    resources = await self.executor.get_exhibit_resources(ex_id)
                    if resources and 1 <= idx <= len(resources):
                        res = resources[idx - 1]
                        await self.executor.play_resource(
                            res["special_id"], res["terminal_ctrl_id"], res["id"],
                        )
                        self.ws.set_current_resource(str(res["id"]), res.get("name", ""))
                        # 自动模式: 按 est_seconds 等资源播完, 期间允许 _auto_plan_paused 暂停
                        # 没给 est 时默认 60s, 上限 240s 防止 LLM 写错卡死
                        if autonomous:
                            wait_left = min(240, est or 60)
                            ex_name = (p.get("exhibit_name") or self.ws.exhibit.current_exhibit_name or "????").strip()
                            res_name = (res.get("name") or "????").strip()
                            logger.info("[SmartEngine] auto_plan ??????: exhibit=%s resource=%s wait=%ds", ex_name, res_name, wait_left)
                            segments = await self._build_auto_plan_commentary_segments(ex_id, ex_name, res_name, int(res["id"]))
                            next_comment = 0
                            elapsed_wait = 0
                            comment_no = 0
                            while wait_left > 0:
                                if not self._auto_plan:
                                    return  # ??? (??/???)
                                if getattr(self, "_auto_plan_paused", False):
                                    await asyncio.sleep(1.0)
                                    continue
                                if elapsed_wait >= next_comment and wait_left > 15 and comment_no < len(segments):
                                    text = segments[comment_no]
                                    comment_no += 1
                                    logger.info("[SmartEngine] auto_plan ????????: exhibit=%s no=%d", ex_name, comment_no)
                                    tts_started = time.time()
                                    await self._speak(text)
                                    # TTS ????????????????????????????????????
                                    spent = max(0, int(time.time() - tts_started))
                                    wait_left = max(0, wait_left - spent)
                                    elapsed_wait += spent
                                    next_comment += 10
                                    continue
                                chunk = min(2.0, wait_left)
                                await asyncio.sleep(chunk)
                                wait_left -= chunk
                                elapsed_wait += chunk
                            logger.info("[SmartEngine] auto_plan ??????: exhibit=%s resource=%s", ex_name, res_name)
                elif t == "dwell":
                    if autonomous:
                        wait_left = min(1800, est or int(p.get("seconds", 0) or 0))
                        ex_id = int(self.ws.exhibit.current_exhibit_id or 0)
                        ex_name = self.ws.exhibit.current_exhibit_name or "????"
                        logger.info("[SmartEngine] auto_plan dwell ???? %ds reason=%s", wait_left, p.get("reason", ""))
                        segments = await self._build_auto_plan_commentary_segments(ex_id, ex_name, self.ws.exhibit.current_resource_name, int(self.ws.exhibit.current_resource_id or 0))
                        next_comment = 30
                        elapsed_wait = 0
                        comment_no = 0
                        while wait_left > 0:
                            if not self._auto_plan:
                                return
                            if getattr(self, "_auto_plan_paused", False):
                                await asyncio.sleep(1.0)
                                continue
                            if elapsed_wait >= next_comment and wait_left > 20 and comment_no < len(segments):
                                await self._speak(segments[comment_no])
                                comment_no += 1
                                next_comment += 45
                                continue
                            chunk = min(2.0, wait_left)
                            await asyncio.sleep(chunk)
                            wait_left -= chunk
                            elapsed_wait += chunk
                    else:
                        await asyncio.sleep(min(30, est))
                elif t == "prompt_next":
                    if autonomous:
                        # 自动模式: prompt_next 视为节奏停顿, 不停下问用户
                        logger.debug("[SmartEngine] auto_plan(autonomous) 跳过 prompt_next")
                        continue
                    self.ws.transition("ASK_NEXT")
                    await self._speak("您想继续下一站，还是换一个展项看看？")
                    return  # 停下等用户
                elif t == "show_list":
                    pass
            except Exception as e:
                logger.warning("[SmartEngine] auto_plan step 执行失败 type=%s: %s", t, e)
                continue
        # 全部 step 完 → 清状态
        logger.info("[SmartEngine] auto_plan 全部执行完毕 (autonomous=%s)", autonomous)
        self._auto_plan = None
        self._auto_plan_idx = 0
        self._auto_plan_paused = False
        # 自动模式跑完直接回 IDLE, 非自动模式保持 ASK_NEXT 让用户可以说"看下一个"
        self.ws.transition("IDLE" if autonomous else "ASK_NEXT")

    async def _handle_auto_plan_interrupt(self, intent: str, text: str) -> bool:
        """auto_plan 活跃时的用户交互拦截. 返回 True 表示已处理, 不走默认分发."""
        if not self._auto_plan:
            return False
        clean = (text or "").strip()
        autonomous = bool(getattr(self._auto_plan, "autonomous", False))
        # 结束
        if intent == "end_tour" or clean in ("结束", "不用了", "可以了", "再见", "够了"):
            self._auto_plan = None
            self._auto_plan_idx = 0
            self._auto_plan_completed_exhibits = set()
            self._auto_plan_paused = False
            await self._end_tour()
            return True
        # 自动模式: 暂停 / 继续 — 不打断 plan, 只切换 _auto_plan_paused 标志
        if autonomous and (intent == "wait_please" or clean in ("暂停", "停", "停一下", "等一下", "稍等", "先停")):
            self._auto_plan_paused = True
            await self._speak("好的，先停一下。")
            return True
        if autonomous and (intent == "resume_please" or clean in ("继续", "好了继续", "可以了继续")):
            self._auto_plan_paused = False
            await self._speak("好，咱们继续。")
            return True
        # 继续下一步 (非自动模式 / autonomous=False)
        if intent == "play_next" or clean in ("下一个", "下一站", "下一个展项"):
            if autonomous:
                # 自动模式下"下一个" = 跳过当前等待, 立刻推下一步
                self._auto_plan_paused = False
                # 用 task cancel 当前 sleep — 简化: 把 idx 推过当前 play_resource 的等待
                # 实际上 _advance_auto_plan 的 sleep 循环会在下一个 chunk 看到状态变化
                await self._speak("好，跳到下一个。")
                # _auto_plan_paused False 后, sleep 循环会继续；这里没法直接打断 sleep, 但 chunk=2s 可接受
                return True
            await self._advance_auto_plan()
            return True
        # 跳过/换一个 → 重规划剩余
        if intent == "skip_exhibit" or any(kw in clean for kw in
                ("换个展项", "换一个展项", "不想看这个", "跳过这个", "换个", "看别的")):
            await self._replan_auto_plan_remainder(reason=clean or "用户想看别的")
            return True
        return False

    async def _replan_auto_plan_remainder(self, reason: str):
        """根据剩余预算 + 已看/已跳, 重新规划后续路线."""
        from app.engine.tour_planner import replan_remainder
        completed = list(self._auto_plan_completed_exhibits)
        elapsed = int(time.time() - self._auto_plan_start_ts) if self._auto_plan_start_ts else 0
        remaining = max(120, (self._auto_plan.est_total_seconds or 1200) - elapsed)
        logger.info("[SmartEngine] auto_plan 重规划: 已看=%s 剩余=%ds reason=%r",
                    completed, remaining, reason)
        await self._speak("好，我为您重新规划一下后面的路线。")
        # skipped 传当前展项 id
        current_ex = 0
        try:
            current_ex = int(self.ws.exhibit.current_exhibit_id or 0)
        except (ValueError, TypeError):
            pass
        skipped = [current_ex] if current_ex and current_ex in self._auto_plan_completed_exhibits else []
        new_plan = await replan_remainder(
            self.robot_id, reason, completed, skipped, remaining,
        )
        if not new_plan or not new_plan.steps:
            await self._speak("剩下可看的展项不多了，要不我们结束今天的参观？")
            self._auto_plan = None
            self.ws.transition("ASK_NEXT")
            return
        self._auto_plan = new_plan
        self._auto_plan_idx = 0
        # 已看的 exhibit 保留到 completed set, 不重置; 继续从 new_plan 的第一步执行
        self.ws.transition("EXPLANATION")
        await self._advance_auto_plan()

    async def _explain_current_exhibit(self):
        """讲解当前展项.

        单展项定向访问(len(_plan)==1, 如 "带我参观大岛台") → 导航+展项简介后,
        进入资源选择交互循环: 列资源 + 问第几个 + 循环, 直至用户结束.

        多展项游览(len(_plan)>1, 如 "参观 20 分钟") → 自动播第一个资源 + _prompt_next
        询问是否继续下一展项, 保持原有体验.
        """
        # 优先走计划路线
        if self._plan and self._plan_index < len(self._plan):
            item = self._plan[self._plan_index]
            exhibit_name = item.get("name", "")
            position = item.get("position", "")
            narration = item.get("narration", "")
            exhibit_id = item.get("exhibit_id", 0)

            # 导航到点位
            if position:
                await self._speak(f"跟我来，我们去看{exhibit_name}。")
                nav_ok = await self._execute_with_feedback(
                    f"导航到{exhibit_name}", self.executor.navigate(position),
                    "", f"导航到{exhibit_name}没走通，我先在这里给您讲讲吧。"
                )
                if not nav_ok:
                    logger.warning("[SmartEngine] 导航到 %s 失败或超时", position)

            self.ws.set_current_exhibit(str(exhibit_id), exhibit_name)

            # 单展项定向访问 → 纯问答式: 跳过长讲解词, 直接列资源问"第几个", 等用户答.
            # 展项本身的详细介绍等用户选完资源、播放时由资源 narration 承载.
            if len(self._plan) == 1:
                await self._list_resources_for_exhibit(exhibit_id, exhibit_name)
                self.ws.transition("ASK_NEXT")
                self.ws.record_turn("", "interactive_browse")
                return

            # 多展项游览 → 自动播第一个资源 + 引导下一展项
            if not narration:
                narration = await self._get_narration(exhibit_id, exhibit_name)
            await self._speak(narration)
            if await self._maybe_suggest_photo(exhibit_id, exhibit_name):
                self.ws.transition("ASK_NEXT")
                self.ws.record_turn("", "photo_suggestion")
                return

            resources = await self.executor.get_exhibit_resources(exhibit_id)
            if resources:
                first = resources[0]
                await self.executor.play_resource(
                    first["special_id"], first["terminal_ctrl_id"], first["id"]
                )
                self.ws.set_current_resource(str(first["id"]), first.get("name", ""))

            self.ws.transition("ASK_NEXT")
            self.ws.record_turn("", "explanation")
            await self._prompt_next()
        else:
            # 没有计划路线，讲解当前展项
            current_id = self.ws.exhibit.current_exhibit_id
            if current_id:
                narration = await self._get_narration(int(current_id), self.ws.exhibit.current_exhibit_name)
                if narration:
                    await self._speak(narration)
                    if await self._maybe_suggest_photo(int(current_id), self.ws.exhibit.current_exhibit_name or ""):
                        self.ws.transition("ASK_NEXT")
                        return
                # 无 plan 也触发资源选择循环, 给用户交互选择权
                await self._list_resources_for_exhibit(
                    int(current_id), self.ws.exhibit.current_exhibit_name or ""
                )
                self.ws.transition("ASK_NEXT")
            else:
                await self._speak("还没选定展项呢，说展项名称或说「有哪些展项」浏览列表。")
                self.ws.transition("ASK_NEXT")


    async def _get_photo_point_config(self, exhibit_id: int | None = None, exhibit_name: str = "") -> dict | None:
        """读取后台拍照配置。优先当前展项绑定点位，其次全局拍照点。"""
        db = await get_db()
        try:
            row = None
            if exhibit_id:
                rows = await db.execute_fetchall(
                    """SELECT tp.*, np.name AS position_name
                       FROM position_bindings pb
                       JOIN tour_points tp ON tp.position_id = pb.position_id
                       JOIN nav_positions np ON np.position_id = tp.position_id
                       WHERE pb.bind_type='exhibit' AND pb.bind_target_id=?
                         AND (tp.is_photo_point=1 OR tp.photo_suggestion_enabled=1)
                       LIMIT 1""",
                    (int(exhibit_id),),
                )
                if rows:
                    row = dict(rows[0])
            if not row:
                rows = await db.execute_fetchall(
                    """SELECT tp.*, np.name AS position_name
                       FROM tour_points tp
                       JOIN nav_positions np ON np.position_id = tp.position_id
                       WHERE tp.is_photo_point=1
                       ORDER BY tp.id LIMIT 1"""
                )
                if rows:
                    row = dict(rows[0])
            if row:
                return row
            # 兜底: 点位名含“拍照”也可作为后台拍照点，便于旧配置直接生效
            rows = await db.execute_fetchall(
                "SELECT position_id, name AS position_name FROM nav_positions WHERE name LIKE '%拍照%' ORDER BY id LIMIT 1"
            )
            return dict(rows[0]) if rows else None
        finally:
            await db.close()

    async def _maybe_suggest_photo(self, exhibit_id: int, exhibit_name: str) -> bool:
        """仅在后台配置了主动建议，且展项名称为机械臂时询问拍照。"""
        name = exhibit_name or ""
        if "机械臂" not in name:
            return False
        cfg = await self._get_photo_point_config(exhibit_id, exhibit_name)
        if not cfg or int(cfg.get("photo_suggestion_enabled") or 0) != 1:
            return False
        text = (cfg.get("photo_suggestion_text") or "").strip() or "这里是机械臂展示区，要不要和我合张影留念？"
        self._photo_followup = {
            "exhibit_id": exhibit_id,
            "exhibit_name": exhibit_name,
            "expires_at": time.time() + 45,
        }
        await self._speak(text)
        return True

    async def _handle_take_photo(self, skip_nav: bool = False):
        """拍照 / 重拍 流程。

        skip_nav=False: 首次拍照, 中枢下发 cmd_take_photo → APK 导航到拍照点 → 拍照流程。
        skip_nav=True:  重拍, 中枢下发 cmd_take_photo 带 skipNav:true → APK 跳过导航直接拍照。

        拍照成功后:
          - Android 端: 上传原图到 /api/photo/upload/{photo_id}, 回调 evt_photo_done
          - 中枢端:  设置 _photo_followup (state=photo_result), TTS "需要重拍吗?"
          - 用户说 "重拍"       → 再次调用 _handle_take_photo(skip_nav=True)
          - 用户说 "不用了/好了" → 发送 cmd_close_photo 关掉 Android 照片页
        """
        cfg = await self._get_photo_point_config(None, "")
        if not cfg or not cfg.get("position_name"):
            await self._speak("当前还没有配置拍照点，请先在后台配置拍照点。")
            return
        point_name = cfg.get("position_name") or "拍照点"
        base = (cfg.get("photo_download_base_url") or "").strip()

        # 每拍一张（含重拍）都生成独立 photo_id, 保证二维码链接唯一。
        import uuid
        from app.engine.host import get_lan_ip
        from app.config import get_config as _get_cfg
        _svr = _get_cfg().get("server", {})
        _lan_ip = get_lan_ip()
        _port = _svr.get("http_port", 8000)

        photo_id = uuid.uuid4().hex

        if base:
            # 有 photo_download_base_url 配置（通常为公网 IP/域名）→ 用于二维码下载
            qr_content = base if "{" not in base else base.format(photo_id=photo_id, robot_id=self.robot_id)
        else:
            # 兜底: 构造 HTTP 下载链接。前端扫码后向此 URL 发起 GET 即可下载原图。
            # 注意: 不能用 config["server"]["host"] (通常是 "0.0.0.0"),
            # 要使用实际 LAN IP 才能被手机扫码后访问到。
            qr_content = f"http://{_lan_ip}:{_port}/api/photo/download/{photo_id}.jpg"

        # 单独构造 upload URL，使用 LAN IP 供 Android 端上传，
        # 避免公网 IP 有 IP 白名单导致机器人无法上传。
        upload_url = f"http://{_lan_ip}:{_port}/api/photo/upload/{photo_id}"

        result = await self.executor.take_photo(point_name, qr_content, upload_url=upload_url, skip_nav=skip_nav)

        if result and result.get("success") is not False:
            # 拍照成功 → 设置 followup, 等用户说"重拍"/"不用了"
            self._photo_followup = {
                "state": "photo_result",
                "expires_at": time.time() + 60,
            }
            await self._speak("照片已经拍好了，二维码在照片右下方。需要重新拍一张吗？")
        else:
            # 拍照失败
            self._photo_followup = None
            reason = ""
            if isinstance(result, dict):
                reason = str(result.get("reason") or "").strip()
            if reason:
                await self._speak(f"拍照没有成功，原因是{reason}。您可以稍后再试。")
            else:
                await self._speak("拍照没有成功，请稍后再试。")

    async def _next_exhibit(self):
        """下一个展项"""
        if self._plan and self._plan_index < len(self._plan) - 1:
            self._plan_index += 1
            self.ws.transition("EXPLANATION")
            await self._explain_current_exhibit()
        else:
            await self._speak("已经参观完所有展项了，您还有其他需要吗？")
            self.ws.transition("ASK_NEXT")

    async def _play_resource_by_index(self, index: int):
        """播放指定序号的资源"""
        current_exhibit_id = self.ws.exhibit.current_exhibit_id
        if not current_exhibit_id:
            await self._speak("还没选定展项呢。您可以说展项名称，或说「有哪些展项」浏览列表。")
            return

        resources = await self.executor.get_exhibit_resources(int(current_exhibit_id))
        if 1 <= index <= len(resources):
            res = resources[index - 1]
            await self.executor.play_resource(
                res["special_id"], res["terminal_ctrl_id"], res["id"]
            )
            self.ws.set_current_resource(str(res["id"]), res.get("name", ""))
            # 不再说 "好, 播放XXX" — 直接进讲解词更紧凑 (跟 _play_browse_resource 行为一致)
        else:
            if resources:
                await self._speak(f"目前有{len(resources)}个资源，请选择1到{len(resources)}之间的。")
            else:
                await self._speak("当前展项没有可播放的资源。")

    async def _dispatch_openclaw_action(self, action: str, extra: dict):
        """
        把 OpenClaw 返回的执行类 decision 翻译成 V5 现有动作。
        action 已经在调用方验证过白名单, 这里只做分发。
        extra 结构: {"target": str, "flow_name": str, "should_interrupt": bool}
        """
        extra = extra or {}
        if action == "navigate_to":
            target = (extra.get("target") or "").strip()
            if not target:
                logger.warning("[SmartEngine] OpenClaw navigate_to 缺 target, 跳过")
                return
            logger.info("[SmartEngine] OpenClaw → navigate_to target=%r", target)
            await self._navigate_to_exhibit(target)
            return

        if action == "list_exhibits":
            logger.info("[SmartEngine] OpenClaw → list_exhibits")
            await self._list_exhibits()
            return

        if action == "execute_flow":
            flow_name = (extra.get("flow_name") or "").strip()
            if not flow_name:
                logger.warning("[SmartEngine] OpenClaw execute_flow 缺 flow_name, 跳过")
                return
            script_id = await self._find_script_id_by_name(flow_name)
            if script_id is None:
                logger.warning("[SmartEngine] OpenClaw execute_flow 找不到流程 %r", flow_name)
                await self._speak(f"没找到「{flow_name}」这个流程，您可以说「有哪些展项」看看。")
                return
            logger.info("[SmartEngine] OpenClaw → execute_flow %r (id=%d)", flow_name, script_id)
            await self._run_script_tour(script_id)
            return

    # ============================================================
    # OpenClaw 命令链 (decision.commands) 分发
    # ============================================================
    async def _dispatch_command_list(self, commands: list):
        """顺序执行 OpenClaw 输出的 commands 列表。每条 {type, ...}。
        某条失败不打断后续 (单条出错记日志, 继续下一条) — LLM 编排错误时
        前面的 speak 已经播过, 后续的导航/控制不该被堵死。

        失败的命令收集到 self.ws._last_command_errors, decision_bridge 下一轮
        会把它注入 ctx, 让 LLM 看到 'XX 没找到, 候选: A/B/C', 自己纠正命名。"""
        if not commands:
            return
        # 每次新命令链开始前先清掉旧错误
        self.ws._last_command_errors = []
        for i, cmd in enumerate(commands):
            if not isinstance(cmd, dict):
                logger.warning("[SmartEngine] cmd[%d] 不是 dict, 跳过: %r", i, cmd)
                continue
            ctype = (cmd.get("type") or "").strip().lower()
            try:
                if ctype == "speak":
                    text = (cmd.get("text") or "").strip()
                    if text:
                        await self._speak(text)
                elif ctype == "navigate_to":
                    target = (cmd.get("exhibit") or cmd.get("target") or "").strip()
                    if target:
                        await self._dispatch_cmd_navigate(target)
                elif ctype == "play_resource":
                    await self._dispatch_play_resource(cmd)
                elif ctype == "switch_special":
                    name = (cmd.get("special") or cmd.get("name") or "").strip()
                    await self._dispatch_switch_special(name)
                elif ctype in ("group_command", "group"):
                    name = (cmd.get("group") or cmd.get("name") or "").strip()
                    action = (cmd.get("action") or "on").strip().lower()
                    await self._dispatch_group_command(name, action)
                elif ctype == "equipment":
                    name = (cmd.get("equipment") or cmd.get("name") or "").strip()
                    action = (cmd.get("action") or "on").strip().lower()
                    await self._dispatch_equipment(name, action)
                elif ctype == "execute_flow":
                    fn = (cmd.get("flow") or cmd.get("flow_name") or "").strip()
                    if fn:
                        await self._dispatch_cmd_execute_flow(fn)
                elif ctype in ("unitree_dance", "dance"):
                    name = (cmd.get("dance") or cmd.get("name") or "").strip()
                    await self._dispatch_unitree_dance(name)
                elif ctype in ("unitree_motion", "motion"):
                    name = (cmd.get("motion") or cmd.get("name") or "").strip()
                    await self._dispatch_unitree_motion(name)
                elif ctype == "listen":
                    # 主动开收音 (cmd_listen) — 追问后等用户回答. 默认 3s 跟其它入口一致
                    seconds = int(cmd.get("seconds") or cmd.get("timeout") or 3)
                    await self._dispatch_listen(seconds)
                elif ctype in ("show_list", "list", "show"):
                    # 列表上屏: items=[str], title?
                    await self._dispatch_show_list(
                        title=cmd.get("title") or "",
                        items=cmd.get("items") or [],
                        list_type=cmd.get("listType") or "terminal",
                    )
                elif ctype == "show_card":
                    await self._dispatch_show_card(
                        title=cmd.get("title") or "",
                        items=cmd.get("items") or [],
                    )
                elif ctype == "show_text":
                    await self._dispatch_show_text(text=cmd.get("text") or "")
                elif ctype in ("clear_screen", "show_clear"):
                    await self._dispatch_show_clear()
                elif ctype == "cancel":
                    await self._dispatch_cancel()
                elif ctype == "digital_human":
                    await self._dispatch_digital_human(cmd)
                elif ctype == "go_charging":
                    # 让机器人去充电桩
                    logger.info("[SmartEngine] cmd go_charging")
                    try:
                        await self.executor.go_charging()
                    except Exception as e:
                        logger.warning("[SmartEngine] go_charging 失败: %s", e)
                        self._record_cmd_error("go_charging", "充电桩", [], reason=str(e))
                elif ctype == "wait":
                    # 等待 N 秒 — 复合流程里需要"到位停一下再说" / "讲完等一会再走"
                    seconds = float(cmd.get("seconds") or cmd.get("duration") or 2)
                    seconds = max(0.5, min(60.0, seconds))  # 钳到合理区间, 防 LLM 写大数字卡死
                    logger.info("[SmartEngine] cmd wait %.1fs", seconds)
                    await asyncio.sleep(seconds)
                elif ctype == "wait_until_idle":
                    # 等用户答完: 问句后阻塞 dispatch, 直到三个条件都满足或者超 max_secs:
                    #   (1) 用户连续 idle_th 秒没新输入
                    #   (2) bot 不在说话 (_is_speaking=False)
                    #   (3) handle_lock 不被持有 (没有用户回答正在处理中)
                    #
                    # 关键改造:
                    # a) 释放 _handle_lock 让用户答能完整经过 _dialog_pause_reply (LLM chitchat)
                    # b) 设 _in_dialog_pause=True, 让用户答只走简化回复, 不启动新 commands chain
                    # c) 退出条件包含 _handle_lock.locked() 检查, 保证 LLM 应答跑完才回 dispatch
                    max_secs = float(cmd.get("max_seconds") or cmd.get("max") or 60)
                    idle_th = float(cmd.get("idle_threshold") or cmd.get("idle") or 5)
                    initial = float(cmd.get("initial_wait") or cmd.get("initial") or 8)
                    max_secs = max(2.0, min(180.0, max_secs))
                    idle_th = max(2.0, min(30.0, idle_th))
                    initial = max(2.0, min(30.0, initial))
                    start_t = time.time()
                    deadline = start_t + max_secs
                    logger.info("[SmartEngine] cmd wait_until_idle max=%.0fs idle=%.0fs initial=%.0fs"
                                " (释放 _handle_lock + _in_dialog_pause=True)",
                                max_secs, idle_th, initial)
                    self._in_dialog_pause = True
                    self._handle_lock.release()
                    try:
                        while time.time() < deadline:
                            last_t = self.ws.recent.last_input_time or 0
                            user_started = last_t > start_t
                            now = time.time()
                            # 任一条件成立就继续等:
                            # (1) bot 在 TTS 中
                            # (2) 有 handle_speech 在跑 (锁被 _dialog_pause_reply 持有)
                            # (3) bot 刚说完话不到 idle_th 秒, 给用户消化+开口的时间
                            #     这条很关键: 否则 bot 在 dialog_pause 里又问了新问题,
                            #     用户答的窗口会被旧 last_input_time 算成 idle 而 break。
                            if self._is_speaking:
                                await asyncio.sleep(0.5)
                                continue
                            if self._handle_lock.locked():
                                await asyncio.sleep(0.5)
                                continue
                            bot_speak_gap = (now - self._last_bot_speak_at) if self._last_bot_speak_at else 99999
                            if bot_speak_gap < idle_th:
                                # bot 刚说完话, 等用户答 (或者用户在说就 last_t 会很新, 自动等下去)
                                await asyncio.sleep(0.5)
                                continue
                            if user_started:
                                if (now - last_t) >= idle_th:
                                    logger.info("[SmartEngine] wait_until_idle: 用户停%.0fs+bot停%.0fs, "
                                                "处理空闲, 继续", now - last_t, bot_speak_gap)
                                    break
                            else:
                                if (now - start_t) >= initial:
                                    logger.info("[SmartEngine] wait_until_idle: 用户%.0fs没开口, 继续", initial)
                                    break
                            await asyncio.sleep(0.5)
                        else:
                            logger.info("[SmartEngine] wait_until_idle: 兜底超时 %.0fs", max_secs)
                    finally:
                        self._in_dialog_pause = False
                        # 重新拿锁继续后续 dispatch — 此处会等正在跑的 _dialog_pause_reply 释放
                        await self._handle_lock.acquire()
                elif ctype in ("lights_on", "lights_off",
                                "all_on", "all_off",
                                "ad_screen_on", "ad_screen_off"):
                    # 设备总控别名: OpenClaw 直接说 "lights_on" 也认, 不用绕 group_command。
                    # 全部走 ActionExecutor 现成方法。
                    logger.info("[SmartEngine] cmd %s", ctype)
                    try:
                        fn = getattr(self.executor, ctype, None)
                        if fn:
                            await fn()
                        else:
                            self._record_cmd_error(ctype, ctype, [], reason="executor missing method")
                    except Exception as e:
                        logger.warning("[SmartEngine] %s 失败: %s", ctype, e)
                        self._record_cmd_error(ctype, ctype, [], reason=str(e))
                elif ctype == "tts":
                    # speak 的别名 (KNOWLEDGE_PRIMITIVES 用 "tts" 更直观)
                    text = (cmd.get("text") or "").strip()
                    if text:
                        await self._speak(text)
                elif ctype == "video_control":
                    # 控制展项的视频: play|pause|replay
                    await self._dispatch_video_control(
                        exhibit=(cmd.get("exhibit") or "").strip(),
                        action=(cmd.get("action") or "play").strip().lower(),
                    )
                elif ctype == "ppt_control":
                    # 控制展项的 PPT: play|pause|replay|next|last
                    await self._dispatch_ppt_control(
                        exhibit=(cmd.get("exhibit") or "").strip(),
                        action=(cmd.get("action") or "next").strip().lower(),
                    )
                else:
                    logger.warning("[SmartEngine] 未知 cmd type=%r 跳过: %r", ctype, cmd)
                    self._record_cmd_error(ctype, str(cmd), [])
            except Exception as e:
                logger.error("[SmartEngine] cmd[%d] type=%s 执行失败 err=%s cmd=%s",
                             i, ctype, e, cmd, exc_info=True)
                self._record_cmd_error(ctype, str(cmd.get("type")), [], reason=str(e))

    def _record_cmd_error(self, type_: str, name: str, candidates: list, reason: str = "not_found"):
        """记录一条命令分发错误供下一轮 LLM 自纠。"""
        if not hasattr(self.ws, "_last_command_errors") or self.ws._last_command_errors is None:
            self.ws._last_command_errors = []
        self.ws._last_command_errors.append({
            "type": type_,
            "name": name,
            "reason": reason,
            "candidates": list(candidates)[:5],
        })

    async def _dispatch_cmd_navigate(self, target: str):
        """命令链版的导航。target 既可以是 nav_positions 里的点位名 (一等公民),
        也可以是 exhibits 表里的展项名 (作为点位的别名, 通过 position_bindings 映射)。

        分支顺序 (点位优先, 展项作为别名):
          1) target 是 nav_positions 里的点位名 → 直接 executor.navigate(target)
          2) target 是 exhibits 表里的展项, 且绑定了点位 → 走原 _navigate_to_exhibit (含讲解)
          3) target 是展项但无点位绑定 → 报错 + 给可导航候选, 不假装导航
          4) 都不是 → 给候选 (展项 + 点位混合) 让 LLM 自纠"""
        # 1) 点位名直接走 (机器狗都没绑定时也能"去充电桩"/"去工位")
        if await self._is_known_position_name(target):
            logger.info("[SmartEngine] cmd navigate_to: 直走点位 %r", target)
            try:
                ok = await self.executor.navigate(target)
                if not ok:
                    logger.warning("[SmartEngine] navigate(%r) 返回 False", target)
                    self._record_cmd_error("navigate_to", target,
                                           await self._all_position_names(),
                                           reason="navigation_failed")
            except Exception as e:
                logger.warning("[SmartEngine] navigate(%r) 异常: %s", target, e)
                self._record_cmd_error("navigate_to", target, [], reason=str(e))
            return

        # 2) 展项有绑定 → 走原讲解流程 (含 narration / 资源播放)
        exhibit = await self.executor.find_exhibit_by_name(target)
        if exhibit:
            position = await self.executor.find_position_for_exhibit(exhibit["id"])
            if position:
                await self._navigate_to_exhibit(target)
                return
            # 3) 展项无绑定 — 不假装导航
            navigable = await self._navigable_exhibits()
            positions = await self._all_position_names()
            logger.warning(
                "[SmartEngine] navigate_to: 展项 %r 无 position 绑定, 可导航展项=%s 点位=%s",
                target, navigable, positions,
            )
            self._record_cmd_error(
                "navigate_to", target, navigable + positions,
                reason="no_position_binding",
            )
            return

        # 4) 既不是点位也不是展项 — 给混合候选
        cands = await self._candidates_exhibits(target) + await self._all_position_names()
        cands = list(dict.fromkeys(cands))[:10]   # 去重 + 截断
        logger.warning("[SmartEngine] cmd navigate_to: %r 不存在, 候选=%s", target, cands)
        self._record_cmd_error("navigate_to", target, cands)

    async def _all_position_names(self) -> list[str]:
        from app.database import get_db
        db = await get_db()
        try:
            rows = await db.execute_fetchall("SELECT name FROM nav_positions ORDER BY id")
            return [r["name"] for r in rows]
        finally:
            await db.close()

    async def _navigable_exhibits(self) -> list[str]:
        """返回所有有 position 绑定的展项名 (LLM 应该只用这些做 navigate_to)。"""
        from app.database import get_db
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT e.name FROM exhibits e "
                "WHERE e.id IN (SELECT bind_target_id FROM position_bindings WHERE bind_type='exhibit')"
            )
            return [r["name"] for r in rows]
        finally:
            await db.close()

    async def _is_known_position_name(self, name: str) -> bool:
        """点位名是否能解析到一个真实点位 — 精确 + 模糊回退。
        模糊兜 ASR 谐音 / 同音错字 (e.g. "相总"→"姜总")。
        """
        from app.database import get_db
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT 1 FROM nav_positions WHERE LOWER(name)=LOWER(?) LIMIT 1",
                (name,),
            )
            if rows:
                return True
        finally:
            await db.close()
        # 精确没中, 走模糊
        try:
            fuzzy = await self.executor.find_position_by_name(name)
            return fuzzy is not None
        except Exception:
            return False

    async def _dispatch_cmd_execute_flow(self, fn: str):
        sid = await self._find_script_id_by_name(fn)
        if sid:
            await self._run_script_tour(sid)
            return
        cands = await self._candidates_flows(fn)
        logger.warning("[SmartEngine] cmd execute_flow: 流程 %r 不存在, 候选=%s", fn, cands)
        self._record_cmd_error("execute_flow", fn, cands)

    async def _candidates_exhibits(self, q: str) -> list[str]:
        return await self._candidates_table("exhibits", "name", q)

    async def _candidates_flows(self, q: str) -> list[str]:
        return await self._candidates_table("tour_scripts", "name", q, where="enabled=1")

    async def _candidates_table(self, table: str, col: str, q: str, where: str = "1=1") -> list[str]:
        from app.database import get_db
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                f"SELECT {col} FROM {table} WHERE {where} AND {col} LIKE ? LIMIT 5",
                (f"%{q[:6]}%",),
            )
            if rows:
                return [r[col] for r in rows]
            # 没匹配就返回前 5 个让 LLM 看到选项
            rows = await db.execute_fetchall(f"SELECT {col} FROM {table} WHERE {where} LIMIT 5")
            return [r[col] for r in rows]
        except Exception:
            return []
        finally:
            await db.close()

    # ---- 命令实现 (按名字查 ID, 复用现有 TCP/HTTP 通道) ----

    async def _dispatch_group_command(self, name: str, action: str):
        if not name:
            return
        from app.database import get_db
        from app.tcp.central_control import send_tcp_command
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT id, name, cmd_on, cmd_off FROM group_commands "
                "WHERE LOWER(name)=LOWER(?) OR name LIKE ?",
                (name, f"%{name}%"),
            )
            all_names = [r["name"] for r in await db.execute_fetchall(
                "SELECT name FROM group_commands LIMIT 10"
            )]
        finally:
            await db.close()
        if not rows:
            logger.warning("[SmartEngine] 群组 %r 不存在, 候选=%s", name, all_names)
            self._record_cmd_error("group_command", name, all_names)
            return
        gc = dict(rows[0])
        cmd = gc["cmd_off"] if action == "off" and gc["cmd_off"] else gc["cmd_on"]
        if not cmd:
            logger.warning("[SmartEngine] 群组 %r 无 %s 指令", name, action)
            self._record_cmd_error("group_command", name, [], reason=f"no_{action}_command")
            return
        logger.info("[SmartEngine] cmd group=%s action=%s → %s", gc["name"], action, cmd)
        await send_tcp_command(cmd)

    async def _dispatch_equipment(self, name: str, action: str):
        if not name:
            return
        from app.database import get_db
        from app.tcp.central_control import send_tcp_command
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT id, name, cmd_on, cmd_off FROM equipments "
                "WHERE LOWER(name)=LOWER(?) OR name LIKE ?",
                (name, f"%{name}%"),
            )
            cands = [r["name"] for r in await db.execute_fetchall(
                "SELECT name FROM equipments WHERE name LIKE ? LIMIT 5",
                (f"%{name[:4]}%" if name else "%",),
            )] or [r["name"] for r in await db.execute_fetchall(
                "SELECT name FROM equipments LIMIT 5"
            )]
        finally:
            await db.close()
        if not rows:
            logger.warning("[SmartEngine] 设备 %r 不存在, 候选=%s", name, cands)
            self._record_cmd_error("equipment", name, cands)
            return
        eq = dict(rows[0])
        cmd = eq["cmd_off"] if action == "off" else eq["cmd_on"]
        if not cmd:
            logger.warning("[SmartEngine] 设备 %r 无 %s 指令", name, action)
            self._record_cmd_error("equipment", name, [], reason=f"no_{action}_command")
            return
        logger.info("[SmartEngine] cmd equipment=%s action=%s → %s", eq["name"], action, cmd)
        await send_tcp_command(cmd)

    async def _dispatch_switch_special(self, name: str):
        if not name:
            return
        # 用模糊解析器: 处理 "思的"=思德/"思德转场"=思德专场 等 ASR 谐音 + 后缀噪声
        sp = await self.executor.find_special_by_name(name)
        if not sp:
            from app.database import get_db
            db = await get_db()
            try:
                cands = [r["name"] for r in await db.execute_fetchall(
                    "SELECT name FROM specials LIMIT 8"
                )]
            finally:
                await db.close()
            logger.warning("[SmartEngine] 专场 %r 不存在, 候选=%s", name, cands)
            self._record_cmd_error("switch_special", name, cands)
            return
        from app.database import get_db
        from app.tcp.central_control import send_tcp_command
        db = await get_db()
        try:
            await send_tcp_command(f"2_update_{sp['id']}")
            await db.execute("UPDATE specials SET is_current=0")
            await db.execute("UPDATE specials SET is_current=1 WHERE id=?", (sp["id"],))
            await db.execute(
                "UPDATE system_config SET value=? WHERE key='current_special_id'",
                (str(sp["id"]),),
            )
            await db.execute(
                "UPDATE system_config SET value=? WHERE key='current_special_name'",
                (sp["name"],),
            )
            await db.commit()
            logger.info("[SmartEngine] cmd switch_special → %s (id=%d)", sp["name"], sp["id"])
        finally:
            await db.close()

    async def _dispatch_play_resource(self, cmd: dict):
        """play_resource: 在指定展项上播放某个资源 (按序号或名字)。
        cmd: {exhibit, resource_index?, resource_name?, special?}
        位置感知: exhibit 缺省时用当前 current_exhibit / 最近提及 兜底。"""
        exhibit_name = self._resolve_implicit_exhibit(cmd.get("exhibit") or "")
        if not exhibit_name:
            logger.warning("[SmartEngine] play_resource 缺 exhibit 且无当前展项")
            self._record_cmd_error("play_resource", "", [], reason="no_exhibit_and_no_current")
            return
        idx = cmd.get("resource_index")
        rname = (cmd.get("resource_name") or "").strip()
        special_name = (cmd.get("special") or "").strip()
        from app.database import get_db
        from app.tcp.central_control import send_commands_sequence
        db = await get_db()
        try:
            ex_rows = await db.execute_fetchall(
                "SELECT id, name, exhibition_area_terminal_id FROM exhibits "
                "WHERE LOWER(name)=LOWER(?) OR name LIKE ?",
                (exhibit_name, f"%{exhibit_name}%"),
            )
            if not ex_rows:
                cands = [r["name"] for r in await db.execute_fetchall(
                    "SELECT name FROM exhibits WHERE name LIKE ? LIMIT 5",
                    (f"%{exhibit_name[:4]}%" if exhibit_name else "%",),
                )] or [r["name"] for r in await db.execute_fetchall(
                    "SELECT name FROM exhibits LIMIT 5"
                )]
                logger.warning("[SmartEngine] play_resource: 展项 %r 不存在, 候选=%s",
                               exhibit_name, cands)
                self._record_cmd_error("play_resource", exhibit_name, cands)
                return
            ex = dict(ex_rows[0])
            term_ctrl = ex["exhibition_area_terminal_id"]
            if not term_ctrl:
                logger.warning("[SmartEngine] play_resource: 展项 %s 无中控ID", ex["name"])
                return

            # 决定 special_id: 用户指定优先, 否则取当前
            if special_name:
                sp_match = await self.executor.find_special_by_name(special_name)
                special_id = int(sp_match["id"]) if sp_match else 0
            else:
                cur = await db.execute_fetchall(
                    "SELECT value FROM system_config WHERE key='current_special_id'"
                )
                special_id = int(cur[0]["value"]) if cur and cur[0]["value"] else 0
            if not special_id:
                logger.warning("[SmartEngine] play_resource: 没有可用专场")
                return

            # 资源定位: 按 resource_name 优先, 否则按 sort_order 取第 idx 条 (1-based)
            res_rows = await db.execute_fetchall(
                "SELECT r.id, r.name, ser.sort_order "
                "FROM resources r "
                "JOIN special_exhibit_resources ser ON ser.resource_id = r.id "
                "WHERE ser.exhibit_id=? AND ser.special_id=? "
                "ORDER BY ser.sort_order, r.id",
                (ex["id"], special_id),
            )
            if not res_rows:
                logger.warning("[SmartEngine] play_resource: 展项 %s 在专场 %s 下无资源",
                               ex["name"], special_id)
                return
            res = None
            if rname:
                rname_lc = rname.lower()
                for r in res_rows:
                    if (r["name"] or "").lower() == rname_lc:
                        res = dict(r); break
                if res is None:
                    for r in res_rows:
                        if rname_lc in (r["name"] or "").lower():
                            res = dict(r); break
            if res is None:
                # 用 idx, 1-based; 默认第 1 条
                try:
                    n = int(idx) if idx is not None else 1
                except Exception:
                    n = 1
                if n < 1 or n > len(res_rows):
                    n = 1
                res = dict(res_rows[n - 1])

            # 检查当前专场是否一致, 不一致先发切换
            cur = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='current_special_id'"
            )
            cur_special = int(cur[0]["value"]) if cur and cur[0]["value"] else 0
            cmds = []
            if cur_special != special_id:
                cmds.append(f"2_update_{special_id}")
            cmds.append(f"2_{special_id}_{term_ctrl}_{res['id']}")
            logger.info("[SmartEngine] cmd play_resource → exhibit=%s special=%d res=%s tcp=%s",
                        ex["name"], special_id, res["name"], cmds)
            await send_commands_sequence(cmds)
        finally:
            await db.close()

    async def _dispatch_unitree_dance(self, name: str):
        if not name:
            return
        from app.database import get_db
        from app.engine.unitree_client import UnitreeClient
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT dance_id, name FROM unitree_dances "
                "WHERE LOWER(name)=LOWER(?) OR name LIKE ?",
                (name, f"%{name}%"),
            )
            cands = [r["name"] for r in await db.execute_fetchall(
                "SELECT name FROM unitree_dances LIMIT 5"
            )] if not rows else []
        finally:
            await db.close()
        if not rows:
            logger.warning("[SmartEngine] 舞蹈 %r 不存在, 候选=%s", name, cands)
            self._record_cmd_error("unitree_dance", name, cands)
            return
        dance_id = rows[0]["dance_id"]
        client = await UnitreeClient.from_config()
        if client is None:
            logger.warning("[SmartEngine] 宇树网关已禁用")
            return
        logger.info("[SmartEngine] cmd unitree_dance → %s (id=%s)", rows[0]["name"], dance_id)
        await client.dance_select(dance_id)

    # ---- 机器人交互能力 (listen / show / cancel / digital_human) ----

    async def _dispatch_listen(self, seconds: int):
        """主动打开豹小秘收音, N 秒。这是追问/确认场景的关键命令 — 不开就等于
        机器人自言自语, 用户没法接话。失败不抛, 后续命令继续。"""
        seconds = max(1, min(60, int(seconds)))  # 限定 1-60s 防滥用
        logger.info("[SmartEngine] cmd listen %ds", seconds)
        try:
            await self.executor.listen(timeout=seconds)
        except Exception as e:
            logger.warning("[SmartEngine] listen 失败: %s", e)

    async def _dispatch_show_list(self, *, title: str, items: list, list_type: str = "terminal"):
        if not isinstance(items, list):
            items = []
        items = [str(x) for x in items][:20]   # 截 20 条防屏挤
        robot = self._get_active_robot_for_show()
        if not robot:
            logger.warning("[SmartEngine] show_list: 无活跃机器人连接")
            return
        logger.info("[SmartEngine] cmd show_list title=%r items=%d", title, len(items))
        try:
            await robot.send_cmd("cmd_show", {
                "action": "list", "listType": list_type, "title": title, "items": items,
            }, timeout=5)
        except Exception as e:
            logger.warning("[SmartEngine] show_list 失败: %s", e)

    async def _dispatch_show_card(self, *, title: str, items: list):
        robot = self._get_active_robot_for_show()
        if not robot:
            logger.warning("[SmartEngine] show_card: 无活跃机器人连接")
            return
        logger.info("[SmartEngine] cmd show_card title=%r items=%d", title, len(items or []))
        try:
            await robot.send_cmd("cmd_show", {
                "action": "card", "title": title, "items": items or [],
            }, timeout=5)
        except Exception as e:
            logger.warning("[SmartEngine] show_card 失败: %s", e)

    async def _dispatch_show_text(self, *, text: str):
        if not text:
            return
        robot = self._get_active_robot_for_show()
        if not robot:
            logger.warning("[SmartEngine] show_text: 无活跃机器人连接")
            return
        logger.info("[SmartEngine] cmd show_text len=%d", len(text))
        try:
            await robot.send_cmd("cmd_show", {"action": "text", "text": text[:500]}, timeout=5)
        except Exception as e:
            logger.warning("[SmartEngine] show_text 失败: %s", e)

    async def _dispatch_show_clear(self):
        robot = self._get_active_robot_for_show()
        if not robot:
            return
        logger.info("[SmartEngine] cmd show_clear")
        try:
            await robot.send_cmd("cmd_show", {"action": "clear"}, timeout=5)
        except Exception as e:
            logger.warning("[SmartEngine] show_clear 失败: %s", e)

    async def _dispatch_cancel(self):
        logger.info("[SmartEngine] cmd cancel")
        try:
            await self.executor.cancel()
        except Exception as e:
            logger.warning("[SmartEngine] cancel 失败: %s", e)

    async def _dispatch_digital_human(self, cmd: dict):
        """数字人控制 (透传给 flow_executor 的 digital_human step 实现)。
        cmd 可含 action / text / animation 等字段, 直接发 cmd_digital_human。"""
        robot = self._get_active_robot_for_show()
        if not robot:
            logger.warning("[SmartEngine] digital_human: 无活跃机器人连接")
            return
        action = (cmd.get("action") or "").strip()
        params = {k: v for k, v in cmd.items() if k not in ("type", "action")}
        logger.info("[SmartEngine] cmd digital_human action=%r params=%s", action, params)
        try:
            await robot.send_cmd("cmd_digital_human", {"action": action, **params}, timeout=5)
        except Exception as e:
            logger.warning("[SmartEngine] digital_human 失败: %s", e)

    def _get_active_robot_for_show(self):
        """show / digital_human 命令需要 robot connection 直接发底层 cmd。
        优先 ws.get_active_robot(), 否则 _robot_connections 字典里取第一个。"""
        try:
            r = self.ws.get_active_robot() if hasattr(self.ws, "get_active_robot") else None
        except Exception:
            r = None
        if r:
            return r
        try:
            from app.tcp.server import get_robot
            return get_robot(self.robot_id)
        except Exception:
            return None

    # ---- Round 1 新增: 视频/PPT 控制, 含位置感知缺省补全 ----

    def _resolve_implicit_exhibit(self, given: str) -> str:
        """命令缺 exhibit 时, 用 ws.exhibit.current_exhibit_name 兜底; 还没有就用
        最近 navigate_to 过的展项 (ws.recent.last_mentioned_exhibit) 兜底。
        给定值优先, 不空就直接返回。"""
        given = (given or "").strip()
        if given:
            return given
        try:
            cur = getattr(self.ws.exhibit, "current_exhibit_name", "") or ""
            if cur:
                logger.info("[SmartEngine] 命令缺 exhibit, 用当前位置 %r 兜底", cur)
                return cur
        except Exception:
            pass
        try:
            last = getattr(getattr(self.ws, "recent", None), "last_mentioned_exhibit", "") or ""
            if last:
                logger.info("[SmartEngine] 命令缺 exhibit, 用最近提及 %r 兜底", last)
                return last
        except Exception:
            pass
        return ""

    async def _dispatch_video_control(self, *, exhibit: str, action: str):
        # 位置感知: 没指定展项时用当前所在展项兜底
        exhibit = self._resolve_implicit_exhibit(exhibit)
        if not exhibit or action not in ("play", "pause", "replay"):
            self._record_cmd_error("video_control", f"{exhibit}/{action}", [],
                                   reason="invalid_action_or_exhibit_and_no_current")
            return
        ex = await self.executor.find_exhibit_by_name(exhibit)
        if not ex or not ex.get("exhibition_area_terminal_id"):
            cands = await self._candidates_exhibits(exhibit)
            self._record_cmd_error("video_control", exhibit, cands, reason="exhibit_not_found")
            return
        terminal_ctrl_id = ex["exhibition_area_terminal_id"]
        logger.info("[SmartEngine] cmd video_control %s/%s tcid=%s", exhibit, action, terminal_ctrl_id)
        try:
            await self.executor.video_control(int(terminal_ctrl_id), action)
        except Exception as e:
            logger.warning("[SmartEngine] video_control 失败: %s", e)
            self._record_cmd_error("video_control", exhibit, [], reason=str(e))

    async def _dispatch_ppt_control(self, *, exhibit: str, action: str):
        # 位置感知: 没指定展项时用当前所在展项兜底 ("PPT 下一页" 直接生效)
        exhibit = self._resolve_implicit_exhibit(exhibit)
        if not exhibit or action not in ("play", "pause", "replay", "next", "last"):
            self._record_cmd_error("ppt_control", f"{exhibit}/{action}", [],
                                   reason="invalid_action_or_exhibit_and_no_current")
            return
        ex = await self.executor.find_exhibit_by_name(exhibit)
        if not ex or not ex.get("exhibition_area_terminal_id"):
            cands = await self._candidates_exhibits(exhibit)
            self._record_cmd_error("ppt_control", exhibit, cands, reason="exhibit_not_found")
            return
        terminal_ctrl_id = ex["exhibition_area_terminal_id"]
        logger.info("[SmartEngine] cmd ppt_control %s/%s tcid=%s", exhibit, action, terminal_ctrl_id)
        try:
            await self.executor.ppt_control(int(terminal_ctrl_id), action)
        except Exception as e:
            logger.warning("[SmartEngine] ppt_control 失败: %s", e)
            self._record_cmd_error("ppt_control", exhibit, [], reason=str(e))

    async def _dispatch_unitree_motion(self, name: str):
        if not name:
            return
        from app.database import get_db
        from app.engine.unitree_client import UnitreeClient
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT motion_id, name FROM unitree_motions "
                "WHERE LOWER(name)=LOWER(?) OR name LIKE ?",
                (name, f"%{name}%"),
            )
            cands = [r["name"] for r in await db.execute_fetchall(
                "SELECT name FROM unitree_motions WHERE name LIKE ? LIMIT 6",
                (f"%{name[:2]}%" if name else "%",),
            )] if not rows else []
            if not rows and not cands:
                cands = [r["name"] for r in await db.execute_fetchall(
                    "SELECT name FROM unitree_motions LIMIT 8"
                )]
        finally:
            await db.close()
        if not rows:
            logger.warning("[SmartEngine] 动作 %r 不存在, 候选=%s", name, cands)
            self._record_cmd_error("unitree_motion", name, cands)
            return
        motion_id = rows[0]["motion_id"]
        client = await UnitreeClient.from_config()
        if client is None:
            logger.warning("[SmartEngine] 宇树网关已禁用")
            return
        logger.info("[SmartEngine] cmd unitree_motion → %s (id=%s)", rows[0]["name"], motion_id)
        await client.motion_play(int(motion_id))

    async def _find_script_id_by_name(self, name: str) -> "int | None":
        """按 name/trigger_word 子串模糊定位 tour_scripts.id"""
        try:
            from app.database import get_db
            db = await get_db()
            try:
                rows = await db.execute_fetchall(
                    "SELECT id, name, trigger_word FROM tour_scripts WHERE enabled=1"
                )
            finally:
                await db.close()
        except Exception as e:
            logger.warning("[SmartEngine] 查询 tour_scripts 失败: %s", e)
            return None
        name_lc = (name or "").strip().lower()
        if not name_lc:
            return None
        # 精确 > trigger 精确 > 子串
        for r in rows:
            if (r["name"] or "").lower() == name_lc:
                return int(r["id"])
        for r in rows:
            if (r["trigger_word"] or "").lower() == name_lc:
                return int(r["id"])
        for r in rows:
            if name_lc in (r["name"] or "").lower() or (r["name"] or "").lower() in name_lc:
                return int(r["id"])
        return None

    async def _navigate_to_exhibit(self, target: str):
        """导航到指定展项或点位

        优先级:
        1. 展项库命中 → 走标准展项讲解流程 (含 narration / 资源播放)
        2. 点位库命中 → 直接 executor.navigate(point_name) (覆盖 "接待点/前台/充电桩" 等)
        3. 都没命中 → LLM 兜底当泛知识问答
        """
        exhibit = await self.executor.find_exhibit_by_name(target)
        if exhibit:
            self.ws.set_current_exhibit(str(exhibit["id"]), exhibit["name"])
            position = await self.executor.find_position_for_exhibit(exhibit["id"])
            self._plan = [{
                "exhibit_id": exhibit["id"],
                "name": exhibit["name"],
                "position": position or "",
                "narration": "",
            }]
            self._plan_index = 0
            self.ws.transition("EXPLANATION")
            await self._explain_current_exhibit()
            return

        # 不是展项 → 看是不是点位 ("接待点/充电桩/前台"等). 直接发起点位导航,
        # 走 executor.navigate(name) 让 find_position_by_name fuzzy 匹配.
        try:
            position_row = await self.executor.find_position_by_name(target)
        except Exception as e:
            logger.debug("[SmartEngine] 点位查找异常 target=%r: %s", target, e)
            position_row = None
        if position_row:
            point_name = position_row.get("name", target)
            logger.info("[SmartEngine] navigate_to: 命中点位 %r, 走点位导航", point_name)
            await self._speak(f"好的，这就带您去{point_name}。")
            try:
                await self.executor.navigate(point_name)
            except Exception as e:
                logger.warning("[SmartEngine] navigate(%r) 异常: %s", point_name, e)
            self.ws.transition("ASK_NEXT")
            return

        # 找不到展项 — "介绍一下玄武湖"/"介绍一下中山陵" 这类问的是非展项
        # 主题。不要硬拼到最相似展项 (那是讲错), 也不要死板拒答 "没找到XX",
        # 直接走 LLM 当泛知识回答。
        logger.info("[SmartEngine] navigate_to 找不到展项/点位 '%s' → LLM 兜底", target)
        ans = await self._llm_last_resort(f"介绍一下{target}")
        if ans:
            await self._speak(ans)
        else:
            await self._speak(
                f"展厅里没有「{target}」这个展项，您可以说「有哪些展项」查看完整列表。"
            )

    async def _enumerate_exhibit_resources(self, target: str):
        """列出指定展项的资源 - 不导航, 仅屏幕+语音问答式呈现.
        对应 list_exhibit_resources 意图 ("大岛台有什么")."""
        exhibit = await self.executor.find_exhibit_by_name(target)
        if not exhibit:
            await self._speak(f"没有找到「{target}」，您可以说「有哪些展项」查看完整列表。")
            return
        self.ws.set_current_exhibit(str(exhibit["id"]), exhibit["name"])
        await self._list_resources_for_exhibit(exhibit["id"], exhibit["name"])
        self.ws.transition("ASK_NEXT")

    async def _end_tour(self):
        """结束参观"""
        self._finish_legacy_teaching_state("CANCELLED", "tour_end")
        from app.engine.teaching_runtime import reception_id
        reception_id(self.robot_id, reset=True)
        await self._speak("感谢您的参观，祝您愉快！有需要随时叫我。")
        self.ws.transition("END")
        # 短暂延迟后回到IDLE（仅当状态未被新交互改变时才重置）
        await asyncio.sleep(3)
        if self.ws.dialog.engine_state == "END":
            self.ws.reset_dialog()
            self._plan = []
            self._plan_index = 0

    async def _prompt_next(self):
        """询问用户下一步"""
        if self._plan and self._plan_index < len(self._plan) - 1:
            current_name = self.ws.exhibit.current_exhibit_name or ""
            next_name = self._plan[self._plan_index + 1].get("name", "")
            if next_name:
                prefix = f"{current_name}就介绍到这。" if current_name else ""
                await self._smart_speak(
                    f"{prefix}接下来是{next_name}，要看吗？",
                    f"引导访客继续参观，下一站是{next_name}，语气自然亲切"
                )
                return
        await self._smart_speak(
            "您还想看什么？可以说「下一个」，说「有哪些展项」浏览列表，或者「结束参观」。",
            "已参观完计划路线，引导用户可以浏览展项列表或结束参观"
        )

    async def _handle_chitchat(self, text: str, reply_hint: str = ""):
        """处理闲聊/泛知识问答。不续讲旧展项正文, 避免污染导览。"""
        if reply_hint:
            await self._speak(reply_hint)
        else:
            from app.engine.names import get_robot_name
            robot_name = await get_robot_name()
            await self._smart_speak(
                f"我是展厅导览机器人{robot_name}，可以带您参观讲解。您想看什么？",
                "用户在闲聊，自然地回应并引导到展厅参观话题上"
            )
        # 若当前处于讲解/ASK_NEXT, 只加一句轻量引导, 不自动恢复展项讲解
        if self.ws.dialog.engine_state in ("EXPLANATION", "ASK_NEXT"):
            await self._speak("如果您想继续参观，也可以直接说展项名称，或说「有哪些展项」。")
            if self.ws.dialog.engine_state == "EXPLANATION":
                self.ws.transition("ASK_NEXT")

    async def _handle_followup_question(self, text: str):
        """处理跟进问题"""
        await self._smart_speak(
            "这个问题我不太确定，您可以问问工作人员。",
            f"用户针对当前展项「{self.ws.exhibit.current_exhibit_name}」提出了跟进问题，尽量回答"
        )
        self.ws.transition("ASK_NEXT")

    # ============================================================
    # 自我介绍 / 向全场打招呼
    # ============================================================
    async def _handle_self_intro(self):
        """用户说"给大家打个招呼/自我介绍" — 念一句固定话术, 不进 tour 规划。

        话术从 system_config.self_intro_template 读, 缺省回退到内置版本。
        模板支持 {robot_name} / {hall_name} 占位符.
        """
        robot_name = "小豹"
        hall_name = "我们这个展厅"
        try:
            from app.engine.names import get_robot_name
            robot_name = await get_robot_name() or robot_name
        except Exception:
            pass
        try:
            from app.database import get_db
            db = await get_db()
            try:
                rows = await db.execute_fetchall(
                    "SELECT key, value FROM system_config WHERE key IN ('self_intro_template','hall_name')"
                )
                template = ""
                for r in rows:
                    if r["key"] == "self_intro_template" and r["value"]:
                        template = r["value"]
                    elif r["key"] == "hall_name" and r["value"]:
                        hall_name = r["value"]
            finally:
                await db.close()
        except Exception:
            template = ""
        if not template:
            template = (
                "大家好，我是导览机器人{robot_name}，欢迎来到{hall_name}。"
                "我可以带大家参观展厅、讲解展项、控制现场设备，有什么需要随时叫我。"
            )
        try:
            text = template.format(robot_name=robot_name, hall_name=hall_name)
        except Exception:
            text = template
        logger.info("[SmartEngine] self_intro → %s", text[:60])
        await self._speak(text)

    # ============================================================
    # 音乐播放
    # ============================================================
    async def _handle_play_music(self):
        """用户说"放首歌/唱首歌/播放音乐" — 从 music_tracks 启用列表里随机挑一首,
        通过 cmd_play_music 推给 APK.

        URL 拼接策略:
          1. music_url_base 配置非空 → 直接用 (管理员自定义场景)
          2. 否则自动用本机出网网卡 IP + HTTP 端口 — APK 跟 hub 同 LAN 时直接可拉
        """
        import random
        from app.database import get_db
        from app.tcp.server import get_robot
        from app.engine.host import get_music_url_base

        db = await get_db()
        try:
            tracks = await db.execute_fetchall(
                "SELECT id, name, filename FROM music_tracks WHERE enabled=1"
            )
            base_rows = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='music_url_base'"
            )
        finally:
            await db.close()

        if not tracks:
            await self._speak("曲库还没有上传歌曲，您可以让管理员先上传几首。")
            return

        track = random.choice([dict(r) for r in tracks])
        url_base = ((base_rows[0]["value"] if base_rows else "") or "").rstrip("/") \
            or get_music_url_base()
        url = f"{url_base}/media/music/{track['filename']}"

        robot = get_robot(self.robot_id)
        if not robot:
            logger.warning("[SmartEngine] play_music: 机器人不在线 robot=%s", self.robot_id)
            return
        try:
            await robot.send({
                "type": "cmd_play_music",
                "data": {"audioUrl": url, "songName": track["name"]},
            })
            logger.info("[SmartEngine] play_music → robot=%s name=%r url=%s",
                        self.robot_id, track["name"], url)
        except Exception as e:
            logger.error("[SmartEngine] play_music 下发失败: %s", e)
            return
        # 话术匹配交互设计文档: 轻声告知, 不报具体曲名
        await self._speak("好的，为您播放一首轻音乐，营造轻松氛围。")

    async def _handle_stop_music(self):
        """用户说"停/关掉音乐/别唱了" — 通知 Android APK 停止音乐播放."""
        from app.tcp.server import get_robot
        robot = get_robot(self.robot_id)
        if not robot:
            logger.warning("[SmartEngine] stop_music: 机器人不在线 robot=%s", self.robot_id)
            return
        try:
            await robot.send({
                "type": "cmd_stop_music",
                "data": {},
            })
            logger.info("[SmartEngine] stop_music → robot=%s", self.robot_id)
        except Exception as e:
            logger.error("[SmartEngine] stop_music 下发失败: %s", e)
            return
        await self._speak("好的，停止播放音乐。")

    async def _handle_next_song(self):
        """用户说"换一首/换个歌" — 从 music_tracks 里再随机挑一首推给 APK.
        如果当前没在播放音乐, 行为同 play_music.
        """
        import random
        from app.database import get_db
        from app.tcp.server import get_robot
        from app.engine.host import get_music_url_base

        db = await get_db()
        try:
            tracks = await db.execute_fetchall(
                "SELECT id, name, filename FROM music_tracks WHERE enabled=1"
            )
            base_rows = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='music_url_base'"
            )
        finally:
            await db.close()

        if not tracks:
            await self._speak("曲库还没有上传歌曲。")
            return

        track = random.choice([dict(r) for r in tracks])
        url_base = ((base_rows[0]["value"] if base_rows else "") or "").rstrip("/") \
            or get_music_url_base()
        url = f"{url_base}/media/music/{track['filename']}"

        robot = get_robot(self.robot_id)
        if not robot:
            logger.warning("[SmartEngine] next_song: 机器人不在线 robot=%s", self.robot_id)
            return
        try:
            await robot.send({
                "type": "cmd_play_music",
                "data": {"audioUrl": url, "songName": track["name"]},
            })
            logger.info("[SmartEngine] next_song → robot=%s name=%r url=%s",
                        self.robot_id, track["name"], url)
        except Exception as e:
            logger.error("[SmartEngine] next_song 下发失败: %s", e)
            return
        await self._speak("好的，换一首。")

    async def _handle_volume(self, direction: str):
        """用户说"大声点/小声点" — 通知 APK 调整音乐音量.
        direction: "up" 或 "down"
        """
        from app.tcp.server import get_robot
        robot = get_robot(self.robot_id)
        if not robot:
            logger.warning("[SmartEngine] volume: 机器人不在线 robot=%s", self.robot_id)
            return
        try:
            await robot.send({
                "type": "cmd_volume",
                "data": {"direction": direction},
            })
            logger.info("[SmartEngine] volume_{} → robot=%s", self.robot_id)
        except Exception as e:
            logger.error("[SmartEngine] volume 下发失败: %s", e)
            return
        hint = "调大" if direction == "up" else "调小"
        await self._speak(f"音量已{hint}。")

    # ============================================================
    # 工具意图
    # ============================================================
    async def _handle_utility_intent(self, intent: str, entities: dict, text: str):
        """处理帮助/状态/浏览等工具意图"""
        if intent == "query_help":
            state = self.ws.dialog.engine_state
            if state == "IDLE":
                await self._speak(
                    "我可以帮您做以下事情："
                    "说「开始参观」开始讲解，"
                    "说展项名称比如「大岛台」直接前往，"
                    "说「有哪些展项」浏览展项列表，"
                    "说「开灯」「关灯」控制设备，"
                    "说「去充电」让我回去充电。"
                )
            elif state == "ASK_NEXT":
                browse = self.ws.browse
                if browse.mode == "list_exhibits":
                    await self._speak("请说展项名称或序号选择，比如说「大岛台」或「第一个」。说「结束」退出浏览。")
                elif browse.mode == "list_resources":
                    await self._speak(f"请说序号选择要播放的内容，比如「第一个」。说「换个展项」浏览其他展项，说「结束」退出。")
                elif browse.mode == "playing":
                    await self._speak("您可以继续说序号选择其他内容，说「换个展项」看其他展项，或说「结束」退出。")
                else:
                    await self._speak("您可以说「下一个」继续参观，说展项名称跳转，或说「结束参观」。")
            else:
                await self._speak("您还可以说「开始参观」、展项名称、或「结束参观」。有需要随时叫我。")

        elif intent == "query_status":
            robot = self.ws.get_robot(self.robot_id)
            parts = []
            if robot:
                parts.append(f"当前电量{robot.battery}%")
                if robot.position:
                    parts.append(f"我在{robot.position}")
                parts.append(f"状态{robot.status}")
            if self.ws.exhibit.current_exhibit_name:
                parts.append(f"当前展项是{self.ws.exhibit.current_exhibit_name}")
            if self.ws.exhibit.current_special_name:
                parts.append(f"当前专场是{self.ws.exhibit.current_special_name}")
            if not parts:
                parts.append("系统运行正常")
            await self._speak("，".join(parts) + "。")

        elif intent == "list_exhibits":
            await self._list_exhibits()

        elif intent == "list_exhibit_resources":
            target = entities.get("target", "")
            if target:
                await self._enumerate_exhibit_resources(target)
            else:
                await self._list_exhibits()

        elif intent in ("ask_current", "ask_explain"):
            # "当前是哪个专场" → 走 current_special 分支; 其余走展项
            if "专场" in text:
                await self._answer_current_special()
            else:
                await self._answer_current_exhibit()

    async def _answer_current_special(self):
        """回答"当前是哪个专场/现在什么专场"。优先从 DB 取 is_current=1, 次选 system_config."""
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT id, name FROM specials WHERE is_current=1 LIMIT 1"
            )
            if rows and rows[0]["name"]:
                await self._speak(f"当前是「{rows[0]['name']}」专场。")
                return
            cfg = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='current_special_name'"
            )
            if cfg and cfg[0]["value"]:
                await self._speak(f"当前是「{cfg[0]['value']}」专场。")
                return
        except Exception as e:
            logger.warning("[SmartEngine] 查询当前专场失败: %s", e)
        finally:
            await db.close()
        await self._speak("暂时没有查到当前专场信息。")

    async def _answer_current_exhibit(self):
        """回答"这是什么/当前展项是什么/当前屏幕上是什么"。
        优先级: browse(选中资源) > current_exhibit > 机器人位置反查 > 兜底引导。
        """
        browse = self.ws.browse if hasattr(self.ws, "browse") else None
        if browse and browse.mode in ("list_resources", "playing") and getattr(browse, "selected_exhibit_name", ""):
            name = browse.selected_exhibit_name
            try:
                ex_id = int(getattr(browse, "selected_exhibit_id", "") or 0)
            except (TypeError, ValueError):
                ex_id = 0
            narration = await self._get_narration(ex_id, name) if ex_id else ""
            await self._speak(narration or f"当前屏幕上是{name}。")
            return
        ex_id_str = self.ws.exhibit.current_exhibit_id
        ex_name = self.ws.exhibit.current_exhibit_name
        if ex_id_str and ex_name:
            try:
                ex_id = int(ex_id_str)
            except (TypeError, ValueError):
                ex_id = 0
            narration = await self._get_narration(ex_id, ex_name) if ex_id else ""
            await self._speak(narration or f"当前是{ex_name}。")
            return
        exhibit = await self._lookup_exhibit_by_robot_position()
        if exhibit:
            self.ws.set_current_exhibit(str(exhibit["id"]), exhibit["name"])
            narration = await self._get_narration(exhibit["id"], exhibit["name"]) or ""
            await self._speak(narration or f"我们在{exhibit['name']}。")
            return
        await self._speak("您可以直接说展项名称，我带您过去；或者说「有哪些展项」查看完整列表。")

    async def _lookup_exhibit_by_robot_position(self):
        """通过机器人当前位置反查对应展项。找不到返回 None。"""
        robot = self._get_robot_conn()
        position = getattr(robot, "position", "") if robot else ""
        if not position:
            return None
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                """SELECT e.id, e.name FROM exhibits e
                   JOIN position_bindings pb ON pb.bind_target_id = e.id
                   JOIN nav_positions np ON np.id = pb.position_id
                   WHERE pb.bind_target_type='exhibit' AND np.name=?
                   LIMIT 1""",
                (position,),
            )
            if rows:
                return {"id": rows[0]["id"], "name": rows[0]["name"]}
        except Exception as e:
            logger.debug("[SmartEngine] 位置反查展项失败: %s", e)
        finally:
            await db.close()
        return None

    # ============================================================
    # 交互浏览子流程
    # ============================================================
    async def _list_exhibits(self):
        """列出所有可参观的展项

        过滤策略:
        - 名称黑名单: 测试/占位/系统类名 (信创测试机/云猴智能机器人/GPP/1123/cave全黑使用)
          这些不是真展项, 不应出现在面向访客的列表里.
        - 当前专场至少有 1 个资源: 没有资源的条目讲解器无内容可放, 列出来用户也点不进去.
        """
        db = await get_db()
        try:
            exhibits = await db.execute_fetchall(
                """SELECT e.id, e.name, a.name as area_name,
                          (SELECT COUNT(*) FROM special_exhibit_resources ser
                            WHERE ser.exhibit_id=e.id) AS res_cnt
                   FROM exhibits e
                   LEFT JOIN areas a ON a.id = e.area_id
                   WHERE e.name NOT IN (
                       'cave全黑使用', '1123', 'GPP',
                       '信创测试机', '云猴智能机器人'
                   )
                   ORDER BY e.id"""
            )
            # 过滤掉无资源的, 列了用户也看不到内容
            exhibits = [e for e in exhibits if int(e["res_cnt"] or 0) > 0]
            if not exhibits:
                await self._speak("暂时没有可参观的展项。")
                return

            # 缓存到浏览状态
            cache = []
            for ex in exhibits:
                cache.append({
                    "id": ex["id"],
                    "name": ex["name"],
                    "area_name": ex["area_name"] or "",
                })
            self.ws.browse.exhibits_cache = cache
            self.ws.browse.mode = "list_exhibits"
            # 记录候选列表供指代消解用（"第一个" → cache[0]）
            self.ws.recent.last_candidates = cache

            # 发送列表到机器人屏幕显示
            # listType 必须用设备端识别的 terminal/special/resource 之一,
            # 之前用 "exhibit" 设备不渲染所以列表显示不出来.
            items = []
            for i, ex in enumerate(cache):
                items.append({
                    "name": f"{i+1}. {ex['name']}",
                    "status": "",
                    "detail": ex["area_name"],
                })
            await self._show_list("展项列表", items, list_type="terminal")

            # TTS只说数量，引导看屏幕
            await self._speak(f"展厅共有{len(cache)}个展项，您想看第几个？说序号或展项名都可以。")
            self.ws.transition("ASK_NEXT")
        finally:
            await db.close()

    async def _browse_select_exhibit(self, exhibit_id: int, exhibit_name: str):
        """浏览模式下用户选定一个展项时调用:
        1. 查该展项的点位绑定, 有就先导航过去 (并发: TTS 预告 + cmd_navigate, 等到达再列资源)
        2. 没绑点位 → 不动机器人, 直接列资源
        3. 导航失败 (返回 False) 也照常列资源, 用户在哪都能选

        跟之前直接 _list_resources_for_exhibit 的区别: 现场体感更自然 — 选完
        机器人会"走过去再讲解", 而不是停在原地干念列表。
        """
        try:
            position = await self.executor.find_position_for_exhibit(int(exhibit_id))
        except Exception as e:
            logger.warning("[SmartEngine] 查 %s 点位绑定异常: %s", exhibit_name, e)
            position = None
        if position:
            # 预告 + 启动导航; navigate 内部 await evt_arrived (上限 180s), 到达后再列资源
            await self._speak(f"好的，这就带您去{exhibit_name}。")
            try:
                ok = await self.executor.navigate(position)
                if not ok:
                    logger.info("[SmartEngine] 导航 %s 返回 False, 仍继续列资源", exhibit_name)
            except Exception as e:
                logger.warning("[SmartEngine] 导航 %s 异常: %s, 直接列资源", exhibit_name, e)
        else:
            logger.info("[SmartEngine] 展项 %s 无点位绑定, 不导航直接列资源", exhibit_name)
        await self._list_resources_for_exhibit(int(exhibit_id), exhibit_name)

    async def _list_resources_for_exhibit(self, exhibit_id: int, exhibit_name: str):
        """列出指定展项的资源列表"""
        resources = await self.executor.get_exhibit_resources(exhibit_id)
        if not resources:
            await self._speak(f"{exhibit_name}当前没有可播放的内容，换个展项看看？")
            self.ws.browse.mode = "list_exhibits"
            return

        self.ws.browse.resources_cache = resources
        self.ws.browse.selected_exhibit_id = str(exhibit_id)
        self.ws.browse.selected_exhibit_name = exhibit_name
        self.ws.browse.mode = "list_resources"
        self.ws.recent.last_candidates = resources
        self.ws.set_current_exhibit(str(exhibit_id), exhibit_name)

        # 发送资源列表到机器人屏幕显示
        items = []
        for i, r in enumerate(resources):
            items.append({
                "name": f"{i+1}. {r['name']}",
                "status": "",
                "detail": r.get("type", ""),
            })
        await self._show_list(f"{exhibit_name} 资源列表", items, list_type="resource")

        # TTS只说数量，引导看屏幕
        await self._speak(f"{exhibit_name}共有{len(resources)}个内容，您想看第几个？")

    async def _describe_browse_resource(self, index: int):
        """仅讲解指定序号资源的 narration, 不实际播放投屏.
        对应 "第N个讲什么/介绍下第N个" 用户询问, 让用户在选播之前先了解内容."""
        resources = self.ws.browse.resources_cache
        if not resources:
            await self._speak("当前没有可介绍的内容。")
            return
        if index < 1 or index > len(resources):
            await self._speak(f"请选择1到{len(resources)}之间的序号。")
            return
        res = resources[index - 1]
        res_name = res.get("name", f"第{index}个")
        narration = ""
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT narration_text FROM resources WHERE id=?", (res["id"],)
            )
            if rows and rows[0]["narration_text"]:
                narration = rows[0]["narration_text"]
        finally:
            await db.close()
        if narration:
            await self._speak(f"第{index}个是「{res_name}」：{narration}")
        else:
            await self._speak(f"第{index}个是「{res_name}」，暂时没有详细介绍。想看可以说「播第{index}个」。")

    async def _play_browse_resource(self, index: int):
        """在浏览模式中播放指定序号的资源"""
        resources = self.ws.browse.resources_cache
        if not resources:
            await self._speak("没有可播放的内容。")
            return

        if index < 1 or index > len(resources):
            await self._speak(f"请选择1到{len(resources)}之间的序号。")
            return

        res = resources[index - 1]
        res_name = res.get('name', f'第{index}个')
        # 播放资源
        play_ok = await self._execute_with_feedback(
            f"播放{res_name}",
            self.executor.play_resource(res["special_id"], res["terminal_ctrl_id"], res["id"]),
            "", f"{res_name}播放失败，换一个试试？"
        )
        if not play_ok:
            return
        self.ws.set_current_resource(str(res["id"]), res.get("name", ""))

        # 获取讲解词: 优先级 资源自身.narration_text > 展项级讲解词 > LLM 生成 > 空
        narration = ""
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT narration_text FROM resources WHERE id=?", (res["id"],)
            )
            if rows and rows[0]["narration_text"]:
                narration = rows[0]["narration_text"]
        finally:
            await db.close()

        if not narration:
            # 资源没自带讲解词 → 用 展项名+资源名 调 LLM 现场生成 (避免裸放视频/图片没解说).
            try:
                from app.engine.llm import generate_narration
                exhibit_name = self.ws.browse.selected_exhibit_name or ""
                gen = await generate_narration(res_name, exhibit_name=exhibit_name)
                if gen:
                    narration = gen
                    logger.info("[SmartEngine] 资源 %s 无 narration, LLM 生成 (%d字, exhibit=%s)",
                                res["id"], len(gen), exhibit_name)
            except Exception as e:
                logger.warning("[SmartEngine] LLM 生成讲解词失败: %s", e)
            # 兜底: LLM 也失败 → 用展项级讲解词
            if not narration:
                try:
                    narration = await self._get_narration(
                        int(self.ws.browse.selected_exhibit_id),
                        self.ws.browse.selected_exhibit_name
                    )
                except Exception:
                    narration = ""

        self.ws.browse.mode = "playing"
        # 不再说"好，播放XXX" — 直接进讲解词更紧凑
        if narration:
            await self._speak(narration)
        # 重新把资源列表上屏 — 用户讲解听完后能直接看到列表选下一个,
        # 不用追问"刚才有几个内容"。
        try:
            items = []
            for i, r in enumerate(resources):
                items.append({
                    "name": f"{i+1}. {r['name']}",
                    "status": "▶" if (i + 1) == index else "",  # 当前在播的那条标个 ▶
                    "detail": r.get("type", ""),
                })
            await self._show_list(
                f"{self.ws.browse.selected_exhibit_name} 资源列表",
                items, list_type="resource",
            )
        except Exception as e:
            logger.debug("[SmartEngine] 重发资源列表失败(忽略): %s", e)
        await self._speak("接下来想看第几个？不想看了可以说「换个展项」或「不用了」。")

    async def _handle_browse_input(self, intent: str, entities: dict, text: str, reply_hint: str) -> bool:
        """
        处理浏览模式下的用户输入。
        返回 True 表示已处理，False 表示回退到正常 ASK_NEXT 逻辑。
        """
        browse = self.ws.browse
        _clean_text = _strip_terminal_speech_punctuation(text)

        # 结束浏览
        if intent == "end_tour" or _clean_text in ("结束", "不用了", "可以了", "再见", "够了"):
            self.ws.reset_browse()
            await self._end_tour()
            return True

        # 换展项
        if any(kw in text for kw in ("换个展项", "其他展项", "换一个展项", "换个", "返回列表")):
            await self._list_exhibits()
            return True

        # "第N个讲什么 / 介绍下第N个 / 第三个是啥" 询问式描述 (非播放) —— 资源模式通用
        # 两种语序都支持: 数字在前(第X个讲啥) 和 动词在前(介绍下第X个)
        if browse.mode in ("list_resources", "playing"):
            _desc_verbs = ("讲的是", "讲什么", "讲啥", "介绍", "说什么", "说啥",
                           "是啥", "是什么", "什么内容")
            _has_desc_verb = any(v in _clean_text for v in _desc_verbs)
            if _has_desc_verb and browse.resources_cache:
                _num_m = re.search(r"第?\s*([一二三四五六七八九十\d]+)\s*(个|号)?", _clean_text)
                if _num_m:
                    idx = _parse_resource_index_safe(_num_m.group(1))
                    if 1 <= idx <= len(browse.resources_cache):
                        await self._describe_browse_resource(idx)
                        return True

        # list_specials 模式：选择专场
        if browse.mode == "list_specials":
            cache = browse.specials_cache
            if intent == "play_resource" or re.match(r'^[一二三四五六七八九十\d]+$', text.strip()):
                idx = entities.get("resource_index") or _parse_resource_index_safe(text)
                if idx and 1 <= idx <= len(cache):
                    await self._switch_special_by_row(cache[idx - 1])
                    return True
                else:
                    await self._speak(f"请选择1到{len(cache)}之间的序号。")
                    return True
            # 名称选择（含拼音容错）
            sp = _fuzzy_find_by_name(text.strip(), cache)
            if sp:
                await self._switch_special_by_row(sp)
                return True
            # switch_special 带了名称（arbiter 可能重新识别）
            if intent == "switch_special" and entities.get("special_name"):
                sp = _fuzzy_find_by_name(entities["special_name"], cache)
                if sp:
                    await self._switch_special_by_row(sp)
                    return True
            await self._speak("没听清专场名称，请说名称或序号。")
            return True

        # list_exhibits 模式：选择展项
        if browse.mode == "list_exhibits":
            # 数字选择 — 选完先导航到展项对应点位 (如有), 再列资源; 没绑点位就原地列。
            if intent == "play_resource" or re.match(r'^[一二三四五六七八九十\d]+$', text.strip()):
                idx = entities.get("resource_index") or _parse_resource_index_safe(text)
                if idx and 1 <= idx <= len(browse.exhibits_cache):
                    ex = browse.exhibits_cache[idx - 1]
                    await self._browse_select_exhibit(ex["id"], ex["name"])
                    return True
                else:
                    await self._speak(f"请选择1到{len(browse.exhibits_cache)}之间的序号。")
                    return True

            # 名称选择 — 同款逻辑, 先导航再列资源
            if intent in ("navigate_to", "ask_explain", "start_tour", "chitchat"):
                target = entities.get("target", "") or text.strip()
                exhibit = await self.executor.find_exhibit_by_name(target)
                if exhibit:
                    await self._browse_select_exhibit(exhibit["id"], exhibit["name"])
                    return True
                # 没找到，从cache里模糊匹配（含拼音容错）
                cache_match = _fuzzy_find_by_name(target, browse.exhibits_cache)
                if cache_match:
                    await self._browse_select_exhibit(cache_match["id"], cache_match["name"])
                    return True
                # 不是序号、也不是已知展项：不要吞掉普通问答。
                # 返回 False 让调用方挂起浏览现场，并继续进入统一 QA 链路。
                logger.info("[SmartEngine] 浏览列表未匹配展项，交回普通问答: %r", target)
                return False

            return False  # 不认识的意图，回退

        # list_resources 模式：选择资源
        if browse.mode == "list_resources":
            if intent == "play_resource" or re.match(r'^[一二三四五六七八九十\d]+$', text.strip()):
                idx = entities.get("resource_index") or _parse_resource_index_safe(text)
                if idx:
                    await self._play_browse_resource(idx)
                    return True
                await self._speak("请说序号选择要播放的内容。")
                return True

            # 说了资源名 → 模糊匹配资源列表
            if browse.resources_cache and text.strip():
                res_match = _fuzzy_find_by_name(text.strip(), browse.resources_cache)
                if res_match:
                    idx = browse.resources_cache.index(res_match) + 1
                    await self._play_browse_resource(idx)
                    return True

            # 说了展项名 → 切换展项
            if intent in ("navigate_to", "ask_explain"):
                target = entities.get("target", "") or text.strip()
                exhibit = await self.executor.find_exhibit_by_name(target)
                if exhibit:
                    await self._list_resources_for_exhibit(exhibit["id"], exhibit["name"])
                    return True

            return False

        # playing 模式：播放完成等待选择
        if browse.mode == "playing":
            _clean = text.strip()

            # 暂停: 仅取消排队 TTS, 保持 browse.playing + current_resource_id,
            # 让下一轮 "继续"/"重播"/"上一个" 仍能找到当前资源. 不调 _interrupt_current_activity
            # 以免 reset_browse 吞掉上下文.
            if intent == "wait_please" or _clean in ("暂停", "停一下", "先停", "先停一下"):
                self._cancel_pending_tts()
                await self._speak("好的，您看完告诉我「继续」就接着讲。")
                return True

            # 继续/重播当前: 重新播放当前资源 (re-speak narration)
            if (intent == "resume_please"
                    or _clean in ("继续", "接着讲", "重播", "再播一遍", "再讲一遍", "重新讲", "再说一次")):
                current_res_id = self.ws.exhibit.current_resource_id
                current_idx = next(
                    (i for i, r in enumerate(browse.resources_cache)
                     if str(r["id"]) == current_res_id),
                    -1,
                )
                if current_idx >= 0:
                    await self._play_browse_resource(current_idx + 1)
                    return True
                await self._speak("我还没开始讲呢，您想看第几个？")
                return True

            # 上一个资源
            if _clean in ("上一个", "上一个资源", "前一个", "返回上一个"):
                current_res_id = self.ws.exhibit.current_resource_id
                current_idx = next(
                    (i for i, r in enumerate(browse.resources_cache)
                     if str(r["id"]) == current_res_id),
                    -1,
                )
                if current_idx > 0:
                    await self._play_browse_resource(current_idx)  # 1-based of prev
                    return True
                await self._speak("这是第一个，前面没有了。")
                return True

            # 继续选资源 (纯数字/第X个)
            if intent == "play_resource" or re.match(r'^[一二三四五六七八九十\d]+$', _clean):
                idx = entities.get("resource_index") or _parse_resource_index_safe(text)
                if idx:
                    await self._play_browse_resource(idx)
                    return True

            # 下一个
            if intent in ("play_next", "skip_exhibit"):
                current_idx = 0
                current_res_id = self.ws.exhibit.current_resource_id
                for i, r in enumerate(browse.resources_cache):
                    if str(r["id"]) == current_res_id:
                        current_idx = i
                        break
                next_idx = current_idx + 2  # 1-based
                if next_idx <= len(browse.resources_cache):
                    await self._play_browse_resource(next_idx)
                else:
                    await self._speak("这个展项的内容都看完了。要换个展项看看吗？")
                    self.ws.browse.mode = "list_exhibits"
                return True

            # 说了资源名 → 模糊匹配资源列表
            if browse.resources_cache and _clean:
                res_match = _fuzzy_find_by_name(_clean, browse.resources_cache)
                if res_match:
                    idx = browse.resources_cache.index(res_match) + 1
                    await self._play_browse_resource(idx)
                    return True

            # 说了展项名 → 切换展项
            if intent in ("navigate_to", "ask_explain"):
                target = entities.get("target", "") or _clean
                exhibit = await self.executor.find_exhibit_by_name(target)
                if exhibit:
                    await self._list_resources_for_exhibit(exhibit["id"], exhibit["name"])
                    return True

            return False

        return False

    # ============================================================
    # 设备控制
    # ============================================================
    async def _switch_special_by_row(self, sp):
        """执行专场切换（sp 是 {id, name} dict）"""
        sp = dict(sp)
        from app.database import get_db
        from app.tcp.central_control import send_tcp_command
        db = await get_db()
        try:
            await send_tcp_command(f"2_update_{sp['id']}")
            await db.execute("UPDATE specials SET is_current=0")
            await db.execute("UPDATE specials SET is_current=1 WHERE id=?", (sp['id'],))
            await db.execute(
                "UPDATE system_config SET value=? WHERE key='current_special_id'",
                (str(sp['id']),),
            )
            await db.commit()
            self.ws.reset_browse()
            self.ws.transition("IDLE")
            await self._speak(f"好，切到{sp['name']}了。")
        except Exception as e:
            logger.error("[SmartEngine] 切换专场失败: %s", e)
            await self._speak("切换专场时出现错误，请稍后再试。")
        finally:
            await db.close()

    async def _handle_control_intent(self, intent: str, entities: dict):
        """处理设备控制意图（不改变对话状态）"""
        # 控制意图执行前清除 browse 状态, 防止泄漏
        if self.ws.browse.mode:
            logger.info("[SmartEngine] 控制意图 %s 执行前清除 browse 状态", intent)
            self.ws.reset_browse()
        # 先查数据库映射
        mapped = await self._try_mapped_command(intent, entities)
        if mapped:
            return

        # 回退到硬编码逻辑
        reply = ""
        if intent == "lights_on":
            await self._execute_with_feedback(
                "开灯", self.executor.lights_on(),
                "好，灯开了。", "灯没开成功，设备可能离线，稍后再试。")
            return
        elif intent == "lights_off":
            await self._execute_with_feedback(
                "关灯", self.executor.lights_off(),
                "好，灯关了。", "灯没关成功，设备可能离线，稍后再试。")
            return
        elif intent == "all_on":
            await self._execute_with_feedback(
                "全开", self.executor.all_on(),
                "好，都打开了。", "没能全部打开，部分设备可能离线。")
            return
        elif intent == "all_off":
            await self._execute_with_feedback(
                "全关", self.executor.all_off(),
                "好，都关了。", "没能全部关闭，部分设备可能离线。")
            return
        elif intent == "ad_screen_on":
            await self._execute_with_feedback(
                "开广告机", self.executor.ad_screen_on(),
                "好，广告机开了。", "广告机没开成功，稍后再试。")
            return
        elif intent == "ad_screen_off":
            await self._execute_with_feedback(
                "关广告机", self.executor.ad_screen_off(),
                "好，广告机关了。", "广告机没关成功，稍后再试。")
            return
        elif intent == "switch_special":
            special_name = entities.get("special_name", "")
            if special_name:
                from app.database import get_db
                db = await get_db()
                try:
                    all_specials = await db.execute_fetchall(
                        "SELECT id, name FROM specials"
                    )
                    sp = _fuzzy_find_by_name(special_name, all_specials)
                    if sp:
                        await self._switch_special_by_row(sp)
                        return
                    else:
                        names = "、".join(s["name"] for s in all_specials) if all_specials else "无"
                        reply = f"没有找到「{special_name}」相关专场。目前有：{names}。"
                except Exception as e:
                    logger.error("[SmartEngine] 切换专场失败: %s", e)
                    reply = "切换专场时出现错误，请稍后再试。"
                finally:
                    await db.close()
            else:
                # 没说专场名，列出可用专场并发到屏幕 + 进入 list_specials 浏览模式
                from app.database import get_db
                db = await get_db()
                try:
                    all_specials = await db.execute_fetchall("SELECT id, name FROM specials")
                    if all_specials:
                        cache = [dict(s) for s in all_specials]
                        self.ws.browse.specials_cache = cache
                        self.ws.browse.mode = "list_specials"
                        self.ws.recent.last_candidates = cache
                        items = [{"name": f"{i+1}. {s['name']}", "status": "", "detail": ""} for i, s in enumerate(cache)]
                        await self._show_list("可用专场", items, list_type="special")
                        reply = f"展厅共有{len(cache)}个专场，想切到第几个？说序号或名称都可以。"
                        self.ws.transition("ASK_NEXT")
                    else:
                        reply = "目前没有可用的专场。"
                except Exception:
                    reply = "请问切换到哪个专场？"
                finally:
                    await db.close()
                if reply:
                    await self._speak(reply)
                return
        elif intent == "go_charging":
            ok = await self._execute_with_feedback(
                "去充电", self.executor.go_charging(),
                "好，我去充电了。", "充电桩没走成，稍后再试。")
            if ok:
                self.ws.transition("IDLE")
                self.ws.reset_dialog()
            return

        if reply:
            await self._speak(reply)

    # ============================================================
    # 数据库意图映射执行
    # ============================================================
    async def _try_mapped_command(self, intent: str, entities: dict) -> bool:
        """
        尝试从 intent_command_mappings 表执行映射命令。
        返回 True 表示已执行，False 表示无映射。
        """
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT * FROM intent_command_mappings WHERE intent_name=?",
                (intent,),
            )
            if not rows:
                return False

            mapping = dict(rows[0])
            cmd_type = mapping.get("command_type", "")
            cmd_target = mapping.get("command_target", "")
            cmd_action = mapping.get("command_action", "")
            reply_text = mapping.get("reply_text", "")

            if not cmd_type or not cmd_target:
                return False

            from app.tcp.central_control import send_tcp_command

            executed = False

            if cmd_type == "group_command":
                groups = await db.execute_fetchall(
                    "SELECT cmd_on, cmd_off FROM group_commands WHERE id=? OR name=?",
                    (cmd_target, cmd_target),
                )
                if not groups:
                    logger.warning("[SmartEngine] 映射 %s: 组策略 '%s' 不存在，回落硬编码", intent, cmd_target)
                    return False
                cmd = groups[0]["cmd_on"] if cmd_action != "off" else groups[0]["cmd_off"]
                if not cmd:
                    logger.warning("[SmartEngine] 映射 %s: 组 '%s' 缺少 cmd_%s，回落硬编码", intent, cmd_target, cmd_action or "on")
                    return False
                await send_tcp_command(cmd)
                executed = True

            elif cmd_type == "equipment":
                equips = await db.execute_fetchall(
                    "SELECT cmd_on, cmd_off FROM equipments WHERE id=? OR name=?",
                    (cmd_target, cmd_target),
                )
                if not equips:
                    logger.warning("[SmartEngine] 映射 %s: 设备 '%s' 不存在，回落硬编码", intent, cmd_target)
                    return False
                cmd = equips[0]["cmd_on"] if cmd_action != "off" else equips[0]["cmd_off"]
                if not cmd:
                    logger.warning("[SmartEngine] 映射 %s: 设备 '%s' 缺少 cmd_%s，回落硬编码", intent, cmd_target, cmd_action or "on")
                    return False
                await send_tcp_command(cmd)
                executed = True

            elif cmd_type == "special_switch":
                special_id = cmd_target
                await send_tcp_command(f"2_update_{special_id}")
                await db.execute("UPDATE specials SET is_current=0")
                await db.execute("UPDATE specials SET is_current=1 WHERE id=?", (special_id,))
                await db.commit()
                executed = True

            elif cmd_type == "robot_command":
                if cmd_target == "charging":
                    await self.executor.go_charging()
                    executed = True
                elif cmd_target == "cancel":
                    await self.executor.cancel()
                    executed = True
                else:
                    logger.warning("[SmartEngine] 映射 %s: 未知 robot_command '%s'，回落硬编码", intent, cmd_target)
                    return False
            else:
                logger.warning("[SmartEngine] 映射 %s: 未知 command_type '%s'，回落硬编码", intent, cmd_type)
                return False

            if not executed:
                return False

            if reply_text:
                await self._speak(reply_text)
            return True
        except Exception as e:
            logger.error("[SmartEngine] 映射命令执行失败: %s", e)
            return False
        finally:
            await db.close()

    # ============================================================
    # 预设流程执行
    # ============================================================
    async def _run_script_tour(self, script_id: int):
        """使用预设流程脚本执行参观"""
        from app.engine.flow_executor import start_flow
        result = await start_flow(script_id, self.robot_id)
        if result.get("status") == "ok":
            self.ws.transition("EXPLANATION")
            logger.info("[SmartEngine] 流程脚本启动: script_id=%d", script_id)
        else:
            await self._speak("这个流程暂时启动不了。您可以说「有哪些展项」浏览，或说具体展项名称。")
            self.ws.transition("IDLE")
            self.ws.reset_dialog()

    # ============================================================
    # 仲裁辅助：打断恢复 / 带反馈执行 / 诊断日志
    # ============================================================
    async def _try_resume_suspended(self) -> bool:
        """尝试恢复被打断的活动。返回 True 表示恢复了。"""
        info = self.ws.pop_suspended()
        if not info:
            return False
        suspended_state = info.get("state", "")
        plan_idx = info.get("plan_index", -1)
        hint = info.get("resume_hint", "")
        snippet = info.get("context_snippet", "")

        # 恢复到之前的状态
        if suspended_state == "EXPLANATION":
            self.ws.transition("ASK_NEXT")
            if plan_idx >= 0:
                self._plan_index = plan_idx
            if hint == "prompt_next":
                if snippet:
                    await self._speak(snippet)
                await self._prompt_next()
            return True
        elif suspended_state == "ASK_NEXT":
            self.ws.transition("ASK_NEXT")
            if snippet:
                await self._speak(snippet)
            return True
        elif suspended_state == "DISCUSS_DONE":
            self.ws.transition("DISCUSS_DONE")
            if snippet:
                await self._speak(snippet)
            else:
                if self._was_flow_running:
                    await self._speak("说「继续」恢复参观，或说「结束参观」。")
                else:
                    await self._speak("您还想做什么？")
            return True
        return False

    def _build_context_snippet(self, state: str) -> str:
        """生成恢复时的上下文提示语"""
        exhibit = self.ws.exhibit.current_exhibit_name
        if state == "EXPLANATION" and exhibit:
            return f"好，回到{exhibit}。"
        if state == "ASK_NEXT":
            return "好，咱们继续。"
        if state == "DISCUSS_DONE":
            return ""  # DISCUSS_DONE 由 _try_resume 自行处理
        return ""

    async def _execute_with_feedback(self, action_name: str, action_coro,
                                      success_msg: str, failure_msg: str) -> bool:
        """执行动作并给用户有意义的反馈（成功/失败/超时）"""
        try:
            result = await action_coro
            if result:
                self.ws.record_action(action_name, "ok")
                if success_msg:
                    await self._speak(success_msg)
                return True
            else:
                self.ws.record_action(action_name, "failed", failure_msg)
                await self._speak(failure_msg)
                return False
        except asyncio.TimeoutError:
            self.ws.record_action(action_name, "timeout")
            await self._speak(f"{action_name}超时了，您可以稍后再试。")
            return False
        except Exception as e:
            logger.error("[SmartEngine] %s 执行异常: %s", action_name, e)
            self.ws.record_action(action_name, "error", str(e))
            await self._speak(f"{action_name}出了点问题，请稍后再试。")
            return False

    def _log_turn(self, user_text: str, intent: str, confidence: float,
                  decision: ActionDecision, old_state: str,
                  action_taken: str, interrupted: bool, resumed: bool,
                  resolved_text: str = ""):
        """结构化诊断日志：每轮对话完整决策追踪（JSON格式）"""
        new_state = self.ws.dialog.engine_state
        log_data = {
            "user": user_text[:60],
            "resolved": resolved_text[:60] if resolved_text and resolved_text != user_text else "",
            "intent": intent,
            "conf": round(confidence, 2),
            "decision": decision.action,
            "reason": decision.reason,
            "interrupted": interrupted,
            "action": action_taken,
            "state": f"{old_state}->{new_state}",
            "resumed": resumed,
            "exhibit": self.ws.exhibit.current_exhibit_name or "",
            "exec_result": self.ws.recent.last_action_result or "",
            "reply": (self.ws.dialog.last_bot_reply or "")[:40],
            "low_conf_streak": self.ws.recent.low_confidence_streak,
        }
        logger.info("[SmartEngine][Turn] %s", json.dumps(log_data, ensure_ascii=False))

    # ============================================================
    # 辅助方法
    # ============================================================
    async def _llm_last_resort(self, user_text: str) -> str:
        """所有 retriever 都拿不出像样答案时, 直接让 LLM 兜底答用户的问题.

        与 general_search_retriever 的差异:
        - 不再拦截「实时类关键词」: 这条路本就是"已经走过 retriever 都失败了",
          再拦一次只会让用户听到「没听清」, 用户明确要求最差也是 LLM 回答;
        - prompt 更宽松, 允许 LLM 给出常识级别的城市/天气/景点描述,
          但仍要求"不确定就说不确定", 防止编造硬数据.
        - 8s 超时, 失败返回空串由调用方决定下一步.
        """
        if not user_text or not user_text.strip():
            return ""
        try:
            from app.engine.llm import chat_completion
            from app.engine.names import get_robot_name
            robot_name = await get_robot_name()
            system_prompt = (
                f"你是展厅导览机器人{robot_name}的兜底问答助手。"
                "上游 retriever 都没能给出像样的答案, 现在由你直接回答访客问题。"
                "要求: "
                "1) 用 50 到 150 字的自然口语回答, 适合 TTS 播报, 不要列点, 不要 markdown; "
                "2) 涉及实时数据(具体气温/股价/今日比分等)请说「具体数据建议查一下手机」, "
                "但城市/景点/美食/常识类问题请正常给出常识性回答, 不要拒答; "
                "3) 完全不知道时坦白说不太确定, 不要编造; "
                "4) 不要主动续讲展厅展项内容。"
            )
            ans = await asyncio.wait_for(
                chat_completion(
                    messages=[{"role": "user", "content": user_text}],
                    system_prompt=system_prompt,
                    max_tokens=300,
                    temperature=0.6,
                ),
                timeout=8.0,
            )
            return (ans or "").strip()
        except asyncio.TimeoutError:
            logger.warning("[SmartEngine] _llm_last_resort 超时(8s)")
        except Exception as e:
            logger.warning("[SmartEngine] _llm_last_resort 失败: %s", e)
        return ""

    async def _smart_speak(self, fallback: str, instruction: str = ""):
        """LLM增强播报：尝试用LLM生成自然回复，超时或失败用fallback"""
        try:
            from app.engine.llm import conversational_reply
            reply = await asyncio.wait_for(
                conversational_reply(
                    user_text=self.ws.dialog.last_user_text or "",
                    world_context=self.ws.to_context_string(),
                    conversation_history=self.ws.dialog.conversation_history,
                    instruction=instruction,
                ),
                timeout=3.0,
            )
            if reply and reply.strip():
                await self._speak(reply.strip())
                return
        except asyncio.TimeoutError:
            logger.debug("[SmartEngine] LLM回复超时，使用fallback")
        except Exception as e:
            logger.debug("[SmartEngine] LLM回复失败: %s，使用fallback", e)
        await self._speak(fallback)

    async def _check_health(self):
        """健康检查：防止系统卡死 (每次 handle_speech 入口调用一次).

        增加: 长时间无交互(>300s)再开口时视为新会话, 重置残留状态.
        避免 ASK_NEXT / browse 旧上下文污染新用户."""
        # 说话状态卡住超60秒
        if self.ws.dialog.is_speaking and self.ws.dialog.speaking_started_at > 0:
            elapsed = time.time() - self.ws.dialog.speaking_started_at
            if elapsed > 60:
                logger.warning("[SmartEngine] 说话状态卡住 %.0fs，强制清除", elapsed)
                self._is_speaking = False
                self.ws.set_speaking(False)

        # 命令卡死
        if self.ws.is_stuck(timeout=60):
            logger.warning("[SmartEngine] 命令卡死: %s，强制清除", self.ws.execution.pending_command)
            self.ws.command_completed("timeout")
            await self.executor.cancel()

        # 连续失败过多
        if self.ws.execution.consecutive_failures > 3:
            logger.warning("[SmartEngine] 连续失败%d次，重置到IDLE", self.ws.execution.consecutive_failures)
            self.ws.execution.consecutive_failures = 0
            self.ws.transition("IDLE")
            self.ws.reset_browse()
            # 不在此处speak，避免嵌套风险

        # 长时间无交互后的残留状态清理:
        # 用户上次说话距现在 >5 分钟, 若 state 不是 IDLE, 视作旧会话过期, 重置.
        # 避免 ASK_NEXT/browse.mode 被上一位访客留下污染新访客.
        last_input_time = getattr(self.ws.recent, "last_input_time", 0) or 0
        if last_input_time > 0:
            idle_sec = time.time() - last_input_time
            current_state = self.ws.dialog.engine_state
            if idle_sec > 300 and current_state not in ("IDLE", "END"):
                logger.info("[SmartEngine] 无交互 %.0fs (state=%s browse=%s), 重置到 IDLE",
                            idle_sec, current_state, self.ws.browse.mode)
                self.ws.reset_dialog()  # 内部已 reset_browse

    async def _show_list(self, title: str, items: list, list_type: str = "exhibit"):
        """发送列表到机器人屏幕显示"""
        robot = self._get_robot_conn()
        if robot:
            try:
                await robot.send_cmd("cmd_show", {
                    "action": "list",
                    "listType": list_type,
                    "title": title,
                    "items": items,
                })
            except Exception as e:
                logger.warning("[SmartEngine] 发送列表到屏幕失败: %s", e)

    def _get_robot_conn(self):
        """获取机器人连接"""
        from app.tcp.server import get_robot
        return get_robot(self.robot_id)

    def _begin_gateway_turn(self) -> str:
        """创建本地 Gateway 轮次令牌，不写入 InputEvent metadata。"""
        self._gateway_turn_seq += 1
        token = f"{self.robot_id}:{self._gateway_turn_seq}:{time.monotonic_ns()}"
        self._active_gateway_turn_token = token
        return token

    def _invalidate_gateway_turn(self, reason: str = "") -> None:
        if self._active_gateway_turn_token is not None:
            logger.info("[SmartEngine] Gateway turn 作废 robot=%s reason=%s", self.robot_id, reason)
        self._active_gateway_turn_token = None

    def _is_gateway_turn_current(self, token: str | None) -> bool:
        return bool(token and token == self._active_gateway_turn_token)

    def _begin_legacy_qa_turn(self) -> str:
        self._legacy_qa_turn_seq += 1
        token = f"{self.robot_id}:{self._legacy_qa_turn_seq}:{time.monotonic_ns()}"
        self._active_legacy_qa_turn_token = token
        return token

    def _invalidate_legacy_qa_turn(self, reason: str = "") -> None:
        if self._active_legacy_qa_turn_token is not None:
            logger.info("[SmartEngine] 老系统问答 turn 作废 robot=%s reason=%s", self.robot_id, reason)
        self._active_legacy_qa_turn_token = None

    def _is_legacy_qa_turn_current(self, token: str | None) -> bool:
        return bool(token and token == self._active_legacy_qa_turn_token)

    async def _start_legacy_teaching(self, question: str) -> None:
        from app.engine.legacy_teaching import get_legacy_teaching_settings, TEACHING_PROMPT, TEACHING_DISABLED_TEXT
        from app.engine.teaching_runtime import TeachingRuntime
        settings = get_legacy_teaching_settings()
        if (not settings.enabled or self.robot_id not in settings.allowed_robot_ids
                or getattr(self.conn, "teaching_protocol_version", 0) != 2):
            await self._speak(TEACHING_DISABLED_TEXT)
            return
        runtime = getattr(self, "_teaching_runtime", None)
        if runtime:
            runtime.clear("replaced", reset_reception=False)
        runtime = self._teaching_runtime = TeachingRuntime(self, settings)
        session = runtime.start(question)
        ok = await self._speak(TEACHING_PROMPT, extra_data={"teaching": {
            "protocolVersion": 2, "action": "START", "turnId": session.turn_id,
            "question": question, "timeoutSeconds": 120, "silenceTimeoutSeconds": 10}})
        if not ok:
            runtime.cancel(session.turn_id, "prompt_tts_failed")

    async def handle_legacy_teaching_timeout(self, turn_id: str, reason: str) -> None:
        runtime = getattr(self, "_teaching_runtime", None)
        if runtime:
            runtime.cancel(turn_id, reason)

    async def cancel_legacy_teaching_on_disconnect(self) -> None:
        self._invalidate_legacy_qa_turn("disconnect")
        runtime = getattr(self, "_teaching_runtime", None)
        if runtime:
            runtime.clear("disconnect")

    async def _handle_legacy_teaching_speech(self, text: str, turn_id: str, fragment_seq=None) -> None:
        runtime = getattr(self, "_teaching_runtime", None)
        if runtime:
            runtime.fragment(turn_id, fragment_seq, text)

    def _finish_legacy_teaching_state(self, status: str, reason: str) -> None:
        runtime = getattr(self, "_teaching_runtime", None)
        if runtime:
            runtime.clear(reason, reset_reception=False)

    def _cancel_pending_tts(self):
        """取消排队中的TTS（已获锁正在播报的不受影响，但后续排队的会跳过）

        只有当前有 TTS 在播报或有 _speak 协程在等锁时才置取消位,
        否则会把本轮待发的首个 _speak 误杀, 导致每次新 turn 机器人直接静音。
        """
        if self._is_speaking or self._tts_lock.locked():
            self._tts_cancel.set()
            logger.info("[SmartEngine] 取消排队TTS")

    async def _interrupt_current_activity(self, reason: str = "") -> bool:
        """统一中断当前活动: 停 flow + 取消排队 TTS + 取消讨论 + 清 pending_command + 取消挂起任务。
        返回是否真的有东西被打断。"""
        items: list[str] = []
        # 清除浏览状态, 防止 browse.mode 泄漏
        if self.ws.browse.mode:
            items.append("browse")
            self.ws.reset_browse()
        # 1) 停正在跑的流程
        try:
            from app.engine.flow_executor import get_active_flow, stop_flow
            active = get_active_flow(self.robot_id)
            if active and getattr(active, "status", "") == "running":
                await stop_flow(self.robot_id)
                items.append(f"flow={getattr(active, 'script_name', '?')}")
        except Exception as e:
            logger.warning("[SmartEngine] stop_flow 失败: %s", e)
        # 2) 取消讨论
        if self._discussion is not None:
            try:
                self._discussion.cancel()
            except Exception:
                pass
            self._discussion = None
            items.append("discussion")
        # 3) 取消排队 TTS
        if self._is_speaking or self._tts_lock.locked():
            self._tts_cancel.set()
            items.append("tts_queue")
        # 4) 清 pending_command
        if getattr(self.ws.execution, "pending_command", ""):
            self.ws.execution.pending_command = ""
            items.append("pending_command")
        # 5) 取消本引擎挂起的 create_task 句柄
        for t in list(self._pending_tasks):
            if not t.done():
                try:
                    t.cancel()
                    items.append("pending_task")
                except Exception:
                    pass
        self._pending_tasks = []
        if items:
            logger.info("[SmartEngine] 已打断旧活动: %s (reason=%s)", ",".join(items), reason)
            return True
        return False

    def _track_task(self, coro):
        """包装 asyncio.create_task 并记录句柄, 以便 _interrupt_current_activity 可取消。"""
        t = asyncio.create_task(coro)
        self._pending_tasks.append(t)
        t.add_done_callback(lambda _t: self._pending_tasks.remove(_t) if _t in self._pending_tasks else None)
        return t

    # 占位前奏候选 — 全部 ≤8 字, TTS 极快; 随机选一句让交互更自然
    _QA_QUICK_ACK_PHRASES = (
        "嗯，让我看看。",
        "好的稍等。",
        "这个嘛。",
        "我想想。",
        "嗯，我帮您查一下。",
    )

    def _maybe_quick_ack(self, qtype: str, text: str = ""):
        """对预期慢路径的 Q&A 提前播一句短占位语, 让用户感知响应 ≤300ms。

        慢路径:
          - 知识类 qtype: HALL_KNOWLEDGE / GENERAL_KNOWLEDGE / SOCIAL_FALLBACK
          - 复合指令: text 含 然后/接着/再/之后/最后/顺便 等连接词 (handoff 给 OpenClaw)
        快路径不抢话: REALTIME_INFO / POI_NEARBY / HALL_CONTEXT (有结构化答案直出)
        返回 task 句柄供调用方决定要不要 await/cancel; 不返回 None 时调用方可以忽略。
        """
        from app.engine.query_router import (
            HALL_KNOWLEDGE, GENERAL_KNOWLEDGE, SOCIAL_FALLBACK,
        )
        slow_qtype = qtype in (HALL_KNOWLEDGE, GENERAL_KNOWLEDGE, SOCIAL_FALLBACK)
        # 复合指令检测 — 跟 decision_bridge._looks_compound 同款关键词, 避免依赖循环
        is_compound = False
        if text:
            for kw in ("然后", "接着", "再去", "再回", "之后", "最后", "顺便"):
                if kw in text:
                    is_compound = True
                    break
        if not slow_qtype and not is_compound:
            return None
        # bot 已经在说话或队列里有 TTS — 别再加占位, 避免叠音
        if self._is_speaking or self._tts_lock.locked():
            return None
        import random
        phrase = random.choice(self._QA_QUICK_ACK_PHRASES)
        # fire-and-forget: 占位 TTS 跟实质答案 TTS 通过 tts_lock 串行, 不会打架
        return asyncio.create_task(self._speak(phrase))

    async def _speak(self, text: str, extra_data: dict | None = None, teaching_turn_token: str | None = None) -> bool:
        """播报并标记说话状态（串行锁保护，同一时刻只允许一个TTS）.

        关键不变式:
        - _tts_cancel 若已 set, 本次 _speak 吞掉并 clear, 恢复下一次正常;
        - _is_speaking / ws.set_speaking 始终在 finally 复位, 不会因异常卡住说话态;
        - 不论 TTS 正常/超时/异常, 都记录一条 turn, 便于定位.
        """
        async with self._tts_lock:
            if teaching_turn_token and not self._is_legacy_qa_turn_current(teaching_turn_token):
                return False
            # 检查是否被取消（用户已开口说新话，旧链路的排队TTS应跳过）
            if self._tts_cancel.is_set():
                self._tts_cancel.clear()
                logger.info("[SmartEngine] TTS被取消，跳过: %s...", text[:30])
                return False
            self._is_speaking = True
            self.ws.set_speaking(True)
            self.ws.command_started(f"tts:{text[:30]}")
            try:
                # speak() 现在会等 evt_tts_done, 长文本播报需要更长上限
                # (~0.35s/字 + 12s base, 上限 100s) — 比 RobotConnection 内部
                # wait_timeout 略宽, 给网络/锁排队留余量。
                speak_timeout = min(100.0, max(15.0, len(text) * 0.35 + 12.0))
                speak_call = (
                    self.executor.speak(text, extra_data=extra_data)
                    if extra_data
                    else self.executor.speak(text)
                )
                ok = await asyncio.wait_for(speak_call, timeout=speak_timeout)
                self.ws.command_completed("ok" if ok else "failed")
                self.ws.record_turn("", "tts", text)
                self.ws.add_to_history("assistant", text)
                return bool(ok)
            except asyncio.TimeoutError:
                logger.warning("[SmartEngine] TTS超时: %s...", text[:30])
                self.ws.command_completed("timeout")
                self.ws.record_turn("", "tts", text)
                self.ws.add_to_history("assistant", text)
                return False
            except asyncio.CancelledError:
                logger.warning("[SmartEngine] TTS被取消(CancelledError): %s...", text[:30])
                self.ws.command_completed("cancelled")
                return False
            except Exception as e:
                logger.error("[SmartEngine] TTS失败: %s", e)
                self.ws.command_completed("error")
                return False
            finally:
                # 防御: 即使 TTS 过程中 _tts_cancel 被外部再次 set, 这里兜底清一次,
                # 避免残留把下一轮合法 _speak 吞掉
                if self._tts_cancel.is_set():
                    self._tts_cancel.clear()
                await asyncio.sleep(1.5)
                self._is_speaking = False
                self.ws.set_speaking(False)
                # 记 bot 刚说完话的时间, 给 wait_until_idle 用作"等用户消化+答复"的基线
                # 否则 bot 在 dialog_pause 里又问了新问题, idle 计时仍按用户上次时间算, break 太早
                self._last_bot_speak_at = time.time()

    async def _load_plan(self, time_budget: int = 0):
        """加载参观路线"""
        db = await get_db()
        try:
            exhibits = await db.execute_fetchall(
                """SELECT e.id, e.name, e.exhibition_area_terminal_id,
                          ek.narration_text
                   FROM exhibits e
                   LEFT JOIN exhibit_knowledge ek ON ek.exhibit_id = e.id
                   ORDER BY e.id"""
            )
            self._plan = []
            for ex in exhibits:
                ex = dict(ex)  # sqlite3.Row → dict
                position = await self.executor.find_position_for_exhibit(ex["id"])
                narration = ex.get("narration_text") or ""
                self._plan.append({
                    "exhibit_id": ex["id"],
                    "name": ex["name"],
                    "position": position or "",
                    "narration": narration,
                })

            # 如果有时间预算，截取路线
            if time_budget > 0 and self._plan:
                # 粗略估算：每个展项8分钟
                max_items = max(1, time_budget // 8)
                self._plan = self._plan[:max_items]

            logger.info("[SmartEngine] 路线规划: %d个展项", len(self._plan))
        finally:
            await db.close()

    async def _get_narration(self, exhibit_id: int, exhibit_name: str) -> str:
        """获取展项讲解词"""
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT narration_text, name FROM exhibit_knowledge WHERE exhibit_id=?",
                (exhibit_id,),
            )
            if rows and rows[0]["narration_text"]:
                return rows[0]["narration_text"]
            if rows and rows[0]["name"]:
                return f"这是{exhibit_name}。{rows[0]['name']}"
        finally:
            await db.close()

        # 知识库没有，尝试LLM生成
        from app.engine.llm import generate_narration
        narration = await generate_narration(exhibit_name)
        if narration:
            return narration

        return f"这是{exhibit_name}，一个非常精彩的展项。"

    def get_status(self) -> dict:
        """返回引擎状态"""
        return {
            "robot_id": self.robot_id,
            "state": self.ws.dialog.engine_state,
            "plan_length": len(self._plan),
            "plan_index": self._plan_index,
            "turn_count": self.ws.dialog.turn_count,
            "visitor": {
                "present": self.ws.visitor.is_present,
                "name": self.ws.visitor.name,
            },
        }
