"""
DialoguePolicy — 回答完之后的状态恢复策略

resume_policy 取值:
  none         不动旧状态, 不主动续讲, 也不引导(给极轻量回答用)
  ask_next     维持/进入 ASK_NEXT, 必要时加一句轻量引导
  resume_tour  从挂起的 flow / 计划路线 恢复参观

策略表 (按 qtype):
  hall_context     → ask_next   (展厅相关, 答完保持上下文等用户继续问)
  hall_knowledge   → ask_next   (同上)
  realtime_info    → none       (天气/时间答完不要乱续讲)
  general_knowledge → none      (泛知识答完不要乱续讲)
  social_fallback  → none       (闲聊不改状态)
  tour_control     → none       (由控制逻辑自己决定)
"""

from __future__ import annotations

import logging

from app.engine.query_router import (
    HALL_CONTEXT, HALL_KNOWLEDGE, REALTIME_INFO,
    GENERAL_KNOWLEDGE, SOCIAL_FALLBACK, POI_NEARBY,
)

logger = logging.getLogger("hub.engine.dialogue_policy")


def default_resume_policy(qtype: str) -> str:
    if qtype in (HALL_CONTEXT, HALL_KNOWLEDGE):
        return "ask_next"
    if qtype in (REALTIME_INFO, GENERAL_KNOWLEDGE, SOCIAL_FALLBACK, POI_NEARBY):
        return "none"
    return "none"


async def apply_policy(engine, policy: str, qtype: str):
    """
    在 SmartEngine 上应用 resume_policy。
    engine: SmartEngine 实例 (持有 ws/_speak/_try_resume_suspended)
    """
    ws = engine.ws

    if policy == "none":
        # 严格不动状态; 不引导; 不续讲
        return

    if policy == "ask_next":
        # 仅在已经处于讲解/浏览相关状态时, 加一句轻量引导
        state = ws.dialog.engine_state
        if state in ("EXPLANATION", "ASK_NEXT", "DISCUSS_DONE", "WELCOME"):
            # 已在交互态, 不再加引导(避免啰嗦)
            if state == "EXPLANATION":
                ws.transition("ASK_NEXT")
            return
        # 未在参观态: 加一句极轻量提示, 不展开
        try:
            await engine._speak("如果想继续参观，可以说展项名称，或者说「有哪些展项」。")
            ws.transition("ASK_NEXT")
        except Exception as e:
            logger.debug("[Policy] speak hint fail: %s", e)
        return

    if policy == "resume_tour":
        try:
            await engine._try_resume_suspended()
        except Exception as e:
            logger.debug("[Policy] resume fail: %s", e)
        return
