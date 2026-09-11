"""
DialogueArbiter -- 对话仲裁层

在 IntentResolver(意图识别) 和 SmartEngine(状态机执行) 之间做决策：
  - 该不该执行？
  - 是否需要先确认？
  - 是否需要澄清？
  - 能不能打断当前活动？
  - 回答后要不要恢复之前流程？

意图分为四级：
  DIRECT_EXECUTE  — 明确指令，直接执行 (设备控制、显式流程触发、导航到具体目标、结束)
  CONFIRM_FIRST   — 半明确指令，先确认再执行 (泛化"开始参观"、无名专场切换)
  ANSWER_ONLY     — 问答类，只回答不执行 (天气、状态、闲聊、跟进提问)
  SOCIAL          — 社交寒暄，轻量回应 (你好、谢谢、再见)

v2: 新增指代消解、模糊表达澄清、收缩模式、execution_hint
"""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("hub.engine.arbiter")

# ============================================================
# 意图分级定义
# ============================================================

# 直接执行：明确设备控制、明确流程触发、明确目标导航、结束、充电
DIRECT_EXECUTE_INTENTS = frozenset({
    "lights_on", "lights_off", "all_on", "all_off",
    "ad_screen_on", "ad_screen_off", "go_charging",
    "end_tour",
    "play_music", "stop_music", "next_song",
    "volume_up", "volume_down",
})

# 仅回答：问答/闲聊/状态查询/天气
ANSWER_ONLY_INTENTS = frozenset({
    "chitchat", "weather_query", "query_help", "query_status",
    "ask_current",
})

# 社交词（chitchat 子集，通过原文匹配）
_SOCIAL_TEXTS = frozenset({
    "你好", "您好", "哈喽", "hello", "hi", "嗨",
    "早上好", "下午好", "晚上好",
    "谢谢", "感谢", "谢谢你", "多谢",
    "再见", "拜拜", "bye",
})


@dataclass
class ActionDecision:
    """仲裁结果 — 告诉 SmartEngine 该怎么做"""
    action: str = "execute"       # execute | confirm | clarify | answer_only | social
    intent: str = ""              # 原始或调整后的意图
    entities: dict = field(default_factory=dict)
    reply_hint: str = ""          # 原始 LLM 回复建议
    confirm_prompt: str = ""      # action=="confirm" 时的确认话术
    clarify_prompt: str = ""      # action=="clarify" 时的澄清话术
    reason: str = ""              # 决策原因（诊断用）
    priority: int = 2             # 1=social 2=answer 3=confirm 4=execute
    can_interrupt: bool = False   # 是否可打断当前活动
    should_resume: bool = False   # 处理完后是否恢复之前活动
    execution_hint: str = ""      # 执行策略提示: "acknowledge_first" / ""


# ============================================================
# 指代消解
# ============================================================
# 代词模式
_THIS_PATTERNS = re.compile(r'^(这个|这是什么|这个是什么|这个展项)$')
_THAT_PATTERNS = re.compile(r'(那个|刚才那个|上一个|之前那个)')
_IT_PATTERNS = re.compile(r'^(就它|就这个|好的?就这个|选这个)$')
_FIRST_PATTERN = re.compile(r'^第?[一1]个$')


def resolve_anaphora(text: str, engine_state: str,
                     recent=None, exhibit=None) -> tuple:
    """
    指代消解：将"这个"/"那个"/"就它"等替换为具体名称。

    Args:
        text: 用户原文
        engine_state: 当前对话状态
        recent: RecentContext 实例
        exhibit: ExhibitState 实例

    Returns:
        (resolved_text, extra_entities) — resolved_text 可能与原文相同
    """
    if not text or not text.strip():
        return text, {}

    t = text.strip()
    extra = {}

    try:
        # "这个"/"这是什么" → 当前展项
        # 注意: 保留原句 "这是什么 / 这个是什么 / 这个展项", intent_resolver 的 ask_current
        # 规则需要原句匹配, 否则会被改写成 "X是什么" 落入 ask_explain。只对代词 "这个"
        # (出现在句中而非整句)做替换, 并通过 extra 把当前展项名作为 target 传出。
        if _THIS_PATTERNS.match(t):
            name = (exhibit.current_exhibit_name if exhibit else "") or ""
            if name:
                extra["target"] = name
                # 不重写文本, 让 ask_current 的 pattern 直接命中
                logger.debug("[Arbiter] 指代消解: '%s' 保留原句, 注入 target=%s", t, name)
                return t, extra

        # "那个"/"刚才那个" → 上次目标
        if _THAT_PATTERNS.search(t):
            name = ""
            if recent:
                name = recent.last_target or recent.last_mentioned_exhibit or ""
            if name:
                resolved = _THAT_PATTERNS.sub(name, t)
                extra["target"] = name
                logger.debug("[Arbiter] 指代消解: '%s' → '%s' (上次目标)", t, resolved)
                return resolved, extra

        # "就它"/"就这个" → 最近提及的展项
        if _IT_PATTERNS.match(t):
            name = ""
            if recent:
                name = recent.last_mentioned_exhibit or recent.last_target or ""
            if name:
                extra["target"] = name
                logger.debug("[Arbiter] 指代消解: '%s' → target=%s (最近展项)", t, name)
                return name, extra

        # "第一个" → 候选列表第一项
        if _FIRST_PATTERN.match(t):
            if recent and recent.last_candidates:
                first = recent.last_candidates[0]
                name = first.get("name", "") if isinstance(first, dict) else str(first)
                if name:
                    extra["target"] = name
                    logger.debug("[Arbiter] 指代消解: '%s' → '%s' (候选第一项)", t, name)
                    return name, extra

    except Exception as e:
        logger.warning("[Arbiter] 指代消解异常: %s", e)

    return text, {}


# ============================================================
# 模糊表达检测
# ============================================================
_AMBIGUOUS_OPEN = re.compile(r'^(开一下|打开一下|开个)$')


def arbitrate(intent_result: dict, engine_state: str, text: str,
              has_pending_command: bool = False,
              world_state=None) -> ActionDecision:
    """
    对话仲裁：根据意图结果 + 当前状态，决定执行策略。

    Args:
        intent_result: resolve_intent() 的返回值
        engine_state: 当前 SmartEngine 状态 (IDLE/WELCOME/EXPLANATION/ASK_NEXT/...)
        text: 用户原文
        has_pending_command: 是否有正在执行的命令

    Returns:
        ActionDecision 仲裁结果
    """
    intent = intent_result.get("intent", "chitchat")
    confidence = intent_result.get("confidence", 0)
    entities = intent_result.get("entities", {})
    reply_hint = intent_result.get("reply", "")
    source = intent_result.get("source", "")
    script_id = entities.get("script_id")

    base = ActionDecision(
        intent=intent,
        entities=entities,
        reply_hint=reply_hint,
    )

    # ============================================================
    # 规则 -1：收缩模式 — 连续低置信度后只做澄清
    # ============================================================
    if world_state and hasattr(world_state, 'recent'):
        if world_state.recent.low_confidence_streak >= 3 and not script_id:
            base.action = "clarify"
            base.priority = 2
            base.can_interrupt = False
            base.clarify_prompt = "我好像没太听清楚，您能再说一遍吗？也可以说「帮助」看看我能做什么。"
            base.reason = f"收缩模式(连续{world_state.recent.low_confidence_streak}轮低置信度)"
            base.should_resume = engine_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE")
            return base

    # ============================================================
    # 规则 0：显式触发词命中 script_id → 直接执行（最高优先级）
    # ============================================================
    if script_id:
        base.action = "execute"
        base.priority = 4
        base.can_interrupt = True
        base.reason = f"显式触发词命中 script_id={script_id}"
        return base

    # ============================================================
    # 规则 0.5：模糊"开一下" → 澄清（不知道开什么）
    # ============================================================
    if _AMBIGUOUS_OPEN.match(text.strip()):
        base.action = "clarify"
        base.priority = 2
        base.can_interrupt = False
        base.clarify_prompt = "您想开什么？灯光、广告机、还是其他设备？"
        base.reason = "模糊设备控制"
        base.should_resume = engine_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE")
        return base

    # ============================================================
    # 规则 1：低置信度 → 澄清
    # ============================================================
    if confidence < 0.45:
        base.action = "clarify"
        base.priority = 2
        base.can_interrupt = False
        base.clarify_prompt = _build_clarify_prompt(engine_state)
        base.reason = f"置信度过低({confidence:.2f})"
        # 如果在讲解/ASK_NEXT中被打断，之后应恢复
        base.should_resume = engine_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE")
        return base

    # 中等置信度且来源是 LLM（非规则命中）→ 也需澄清
    if confidence < 0.65 and source in ("llm", "llm_parse_fail", "llm_error", "no_llm"):
        base.action = "clarify"
        base.priority = 2
        base.can_interrupt = False
        base.clarify_prompt = _build_clarify_prompt(engine_state)
        base.reason = f"LLM低置信度({confidence:.2f})，非规则命中"
        base.should_resume = engine_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE")
        return base

    # ============================================================
    # 规则 2：社交寒暄（chitchat 中的打招呼/感谢/告别）
    # ============================================================
    clean = text.strip().lower()
    if intent == "chitchat" and clean in _SOCIAL_TEXTS:
        base.action = "social"
        base.priority = 1
        base.can_interrupt = False
        # 社交寒暄不回放上一段展项讲解, 只接一句轻量引导
        base.should_resume = False
        base.reason = "社交寒暄"
        return base

    # ============================================================
    # 规则 3：纯回答类意图 → answer_only
    # ============================================================
    if intent in ANSWER_ONLY_INTENTS:
        base.action = "answer_only"
        base.priority = 2
        base.can_interrupt = False
        # 仅 ask_current (明确针对当前展项) 保留 snippet 回放语义。
        # ask_explain 是模糊分类("什么"泛命中), 可能是"南京美食/今天天气"这种非当前上下文,
        # 不能自动续讲旧展项;_handle_followup_question 自己说一句就收尾。
        # 其他泛问答(chitchat/weather_query/query_help/query_status/fallback_qa)同样不回放。
        if intent == "ask_current":
            base.should_resume = engine_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE")
        else:
            base.should_resume = False
        base.reason = f"问答意图({intent})"
        return base

    # ============================================================
    # 规则 4：设备控制 → 直接执行 + 可打断
    # ============================================================
    if intent in DIRECT_EXECUTE_INTENTS:
        base.action = "execute"
        base.priority = 4
        base.can_interrupt = True
        # 设备控制不改对话状态，执行完恢复
        if intent not in ("end_tour", "go_charging"):
            base.should_resume = engine_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE")
        base.reason = f"直接执行意图({intent})"
        return base

    # ============================================================
    # 规则 5：switch_special 一律 execute。
    # 无名称时由 _handle_control_intent 弹专场列表到屏幕 + 语音提示,
    # 比 confirm 纯 TTS 体验好(机器人 TTS 偶发漏播时用户还能看到列表)。
    # ============================================================
    if intent == "switch_special":
        base.action = "execute"
        base.priority = 4
        base.can_interrupt = True
        base.should_resume = engine_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE")
        name = entities.get("special_name") or ""
        base.reason = f"切换专场({name})" if name else "切换专场(待选)"
        return base

    # ============================================================
    # 规则 6a：start_tour 带时长 → 直接按时长执行(走 _on_idle → _start_interaction)
    # ============================================================
    if intent == "start_tour" and entities.get("minutes") and not entities.get("target"):
        base.action = "execute"
        base.priority = 4
        base.can_interrupt = True
        base.should_resume = engine_state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE")
        base.reason = f"start_tour按时长规划({entities.get('minutes')}分钟)"
        return base

    # ============================================================
    # 规则 6：start_tour 无 script_id 无 target → 确认
    # ============================================================
    if intent == "start_tour" and not entities.get("target"):
        base.action = "confirm"
        base.priority = 3
        base.can_interrupt = False
        base.confirm_prompt = (
            "好的，我可以带您参观。您可以说展项名称直接前往，"
            "说「有哪些展项」浏览列表，或者告诉我您有多少分钟，我来规划路线。"
        )
        base.reason = "泛化start_tour，无明确目标"
        return base

    # ============================================================
    # 规则 7：navigate_to 无 target → 澄清
    # ============================================================
    if intent == "navigate_to" and not entities.get("target"):
        base.action = "clarify"
        base.priority = 2
        base.clarify_prompt = "您想去哪里？请说展项名称或地点。"
        base.reason = "导航但无目标"
        return base

    # ============================================================
    # 规则 8：interactive_discuss 无 topic → 澄清
    # ============================================================
    if intent == "interactive_discuss" and not entities.get("topic"):
        base.action = "clarify"
        base.priority = 2
        base.clarify_prompt = "您想讨论什么话题？"
        base.reason = "讨论但无话题"
        return base

    # ============================================================
    # 规则 9：ask_explain 在讲解/ASK_NEXT 中 → answer_only
    # ask_explain 是弱规则("什么"泛命中), 很多非当前上下文的泛问答(比如"南京美食")
    # 也会落进来; 不自动续讲旧展项 narration, 让 _handle_followup_question 自己收尾。
    # ============================================================
    if intent == "ask_explain" and engine_state in ("EXPLANATION", "ASK_NEXT"):
        base.action = "answer_only"
        base.priority = 2
        base.should_resume = False
        base.reason = "讲解中追问，只回答"
        return base

    # ============================================================
    # 规则 9.5：IDLE下 ask_explain 无 target → 澄清
    # ============================================================
    if intent == "ask_explain" and engine_state == "IDLE" and not entities.get("target"):
        base.action = "clarify"
        base.priority = 2
        base.clarify_prompt = "您想了解哪个展项？可以说展项名称，或说「有哪些展项」查看列表。"
        base.reason = "IDLE下ask_explain无目标"
        return base

    # ============================================================
    # 规则 10：正在执行命令时收到新的动作请求 → 排队提示
    # ============================================================
    if has_pending_command and intent in ("navigate_to", "play_resource"):
        base.action = "clarify"
        base.priority = 2
        base.clarify_prompt = "正在执行上一个操作，请稍等一下。"
        base.reason = "有命令正在执行"
        return base

    # ============================================================
    # 默认：直接执行
    # ============================================================
    base.action = "execute"
    base.priority = 4
    base.can_interrupt = True
    base.reason = f"默认执行({intent})"
    # 导航/播放/切换等耗时操作，先口头确认再执行
    if intent in ("navigate_to", "play_resource", "switch_special", "start_tour"):
        base.execution_hint = "acknowledge_first"
    return base


def _build_clarify_prompt(engine_state: str) -> str:
    """根据当前状态生成上下文相关的澄清提示"""
    if engine_state == "ASK_NEXT":
        return "没听清楚，您可以说「下一个」继续参观，说展项名称前往，或说「结束参观」。"
    elif engine_state == "EXPLANATION":
        return "没听清楚，您可以说「继续」、「下一个」，或问我关于这个展项的问题。"
    elif engine_state == "WELCOME":
        return "没听清楚，您可以说「开始参观」，或告诉我您想看什么展项。"
    elif engine_state == "DISCUSSING":
        return "没听清楚，您可以继续讨论，或说「结束」停止讨论。"
    elif engine_state == "DISCUSS_DONE":
        return "没听清楚，您可以说「继续」恢复参观，或说「结束参观」。"
    else:
        return "没听清楚，请再说一遍。您可以说「开始参观」或展项名称。"
