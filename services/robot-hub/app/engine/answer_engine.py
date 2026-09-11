"""
AnswerEngine — 统一问答中枢

输入: query_router 的分类结果 (qtype, slots) + 文本 + WorldState + robot_id
输出: 统一结构
  {
    "answer": str,            # 播报文案
    "source": str,            # hall_context|workflow|weather_api|search|llm|...
    "confidence": float,      # 0.0-1.0
    "resume_policy": str,     # none|ask_next|resume_tour
    "action": str|None,       # 控制类的具体动作(navigate/list_exhibits/...)
    "qtype": str,             # 透传分类
    "extra": dict,            # 附带 exhibit_id/exhibit_name/city 等
  }

设计:
- 分发到 retrievers 目录下对应模块
- TOUR_CONTROL 不在此处理(由 SmartEngine 走原意图链), 这里只为完整性返回 action 描述
- resume_policy 由 dialogue_policy 决定, 这里仅设默认值供其覆盖
"""

from __future__ import annotations

import logging

from app.engine.query_router import (
    HALL_CONTEXT, TOUR_CONTROL, HALL_KNOWLEDGE,
    REALTIME_INFO, GENERAL_KNOWLEDGE, SOCIAL_FALLBACK,
    POI_NEARBY,
)
from app.engine.retrievers import (
    hall_context_retriever,
    hall_knowledge_retriever,
    realtime_provider,
    general_search_retriever,
    social_retriever,
    poi_retriever,
)
from app.engine.dialogue_policy import default_resume_policy
from app.engine import decision_bridge
from app.engine.gateway_qa import answer_via_gateway
from app.engine.legacy_qa import answer_via_legacy

logger = logging.getLogger("hub.engine.answer_engine")


async def answer(
    qtype: str,
    text: str,
    slots: dict,
    world_state,
    robot_id: str,
    *,
    gateway_text: str | None = None,
    legacy_text: str | None = None,
) -> dict:
    """主入口；外部适配器始终使用未经指代消解的原始 ASR 最终文本。"""
    slots = slots or {}
    extra: dict = {}

    # 新系统优先。active 时无论成功失败都会返回结果，禁止单轮回退到
    # 老系统、OpenClaw、旧 retriever 或本地自由 LLM。
    if qtype != TOUR_CONTROL:
        external_text = (
            gateway_text
            if gateway_text is not None
            else legacy_text if legacy_text is not None else text
        )
        gateway_result = await answer_via_gateway(external_text, world_state, robot_id)
        if gateway_result is not None:
            gateway_result["qtype"] = qtype
            return gateway_result

        # Gateway off/shadow 时保留当前老系统链路作为整体回滚路径。
        # legacy active 同样在成功或失败后终止，不逐请求回退旧问答。
        legacy_result = await answer_via_legacy(
            external_text,
            world_state,
            robot_id,
        )
        if legacy_result is not None:
            legacy_result["qtype"] = qtype
            return legacy_result

    # ── 0. OpenClaw 决策桥（第一阶段：复杂问答/模糊输入）──
    #   命中 handoff 白名单的 qtype 先问 OpenClaw，拿到决策后当作本轮回答。
    #   任何失败都会返回 None，继续走下面的原 V5 retriever 逻辑。
    if await decision_bridge.should_handoff(qtype, text):
        decision = await decision_bridge.decide(text, qtype, slots, world_state, robot_id)
        if decision is not None:
            logger.info(
                "[AnswerEngine] OpenClaw 接管 qtype=%s decision=%s reply=%r",
                qtype, decision.decision, (decision.reply or "")[:60],
            )
            return decision.as_qa_result(qtype)
        else:
            logger.info("[AnswerEngine] OpenClaw 未接管 → 回退 V5 retriever qtype=%s", qtype)

    if qtype == HALL_CONTEXT:
        result = await hall_context_retriever.retrieve(text, slots, world_state, robot_id)
        extra = {k: result.get(k) for k in ("exhibit_id", "exhibit_name") if result.get(k) is not None}
        return {
            "answer": result["answer"],
            "source": result.get("source", "hall_context"),
            "confidence": result.get("confidence", 0.5),
            "resume_policy": default_resume_policy(qtype),
            "action": None,
            "qtype": qtype,
            "extra": extra,
        }

    if qtype == HALL_KNOWLEDGE:
        result = await hall_knowledge_retriever.retrieve(text, slots, world_state, robot_id)
        return {
            "answer": result["answer"],
            "source": result.get("source", "hall_knowledge"),
            "confidence": result.get("confidence", 0.5),
            "resume_policy": default_resume_policy(qtype),
            "action": None,
            "qtype": qtype,
            "extra": {"exhibit_name": result.get("exhibit_name", "")},
        }

    if qtype == POI_NEARBY:
        result = await poi_retriever.retrieve(text, slots, world_state, robot_id)
        return {
            "answer": result["answer"],
            "source": result.get("source", "poi"),
            "confidence": result.get("confidence", 0.5),
            "resume_policy": default_resume_policy(qtype),
            "action": None,
            "qtype": qtype,
            "extra": {k: result.get(k) for k in ("category", "count") if result.get(k) is not None},
        }

    if qtype == REALTIME_INFO:
        result = await realtime_provider.retrieve(text, slots, world_state, robot_id)
        return {
            "answer": result["answer"],
            "source": result.get("source", "realtime"),
            "confidence": result.get("confidence", 0.5),
            "resume_policy": default_resume_policy(qtype),
            "action": None,
            "qtype": qtype,
            "extra": {"city": result.get("city", "")} if result.get("city") else {},
        }

    if qtype == GENERAL_KNOWLEDGE:
        result = await general_search_retriever.retrieve(text, slots, world_state, robot_id)
        return {
            "answer": result["answer"],
            "source": result.get("source", "general"),
            "confidence": result.get("confidence", 0.5),
            "resume_policy": default_resume_policy(qtype),
            "action": None,
            "qtype": qtype,
            "extra": {},
        }

    if qtype == SOCIAL_FALLBACK:
        result = await social_retriever.retrieve(text, slots, world_state, robot_id)
        return {
            "answer": result["answer"],
            "source": result.get("source", "social"),
            "confidence": result.get("confidence", 0.5),
            "resume_policy": default_resume_policy(qtype),
            "action": None,
            "qtype": qtype,
            "extra": {},
        }

    # TOUR_CONTROL: 这里不处理, 由 SmartEngine 走原意图链
    return {
        "answer": "",
        "source": "tour_control_passthrough",
        "confidence": 1.0,
        "resume_policy": "none",
        "action": "passthrough",
        "qtype": TOUR_CONTROL,
        "extra": {},
    }
