"""
GeneralSearchRetriever — 泛知识问答(美食/景点/常识)

设计:
- 不允许 LLM 凭空猜实时数据 (那走 realtime_provider)
- 这里允许 LLM 给一般性常识答案, 但加 system prompt 限定:
    "若不确定/不是你的训练知识,请直接说不清楚"
- 未来可接入真实搜索 API; 当前先用 LLM-with-guard 作为基线
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hub.engine.retriever.general_search")

# 实时类关键词: 这些问题 LLM 的训练数据必然过期, 拦截在入口避免幻觉
# (QueryRouter 通常分到 realtime_info, 但漏分时这里是最后一道防线)
_REALTIME_BLOCKLIST = (
    "天气", "气温", "多少度", "下雨", "晴天", "阴天", "降雨",
    "几点", "现在时间", "今天星期",
    "股价", "股票", "涨跌", "行情", "比分",
    "最新", "新闻", "热点", "头条",
)

_GUARDED_SYSTEM_TEMPLATE = (
    "你是展厅导览机器人{robot_name}的常识问答助手。"
    "对访客的泛知识问题,回答必须满足: "
    "1) 仅基于你确知的常识知识回答; "
    "2) 答案50到150字,自然口语化,适合TTS播报,不要列点,不要markdown; "
    "3) 涉及实时信息(天气/股价/比分/今日新闻)请直接说「这类实时信息我帮不了您,建议查一下手机」; "
    "4) 不确定时直接说「这个我不太确定,您可以查一下」, 不要编造; "
    "5) 不要主动续讲展厅展项内容。"
)


async def retrieve(text: str, slots: dict, world_state=None, robot_id: str = "") -> dict:
    # 实时类关键词直接兜底, 不让 LLM 编造过期数据
    lc = (text or "").lower()
    for kw in _REALTIME_BLOCKLIST:
        if kw in lc:
            logger.info("[GeneralSearch] realtime 关键词命中 '%s', 固定兜底不调 LLM", kw)
            return {
                "answer": "这类实时信息我暂时查不到，您可以看手机或问一下现场工作人员。",
                "source": "realtime_blocked",
                "confidence": 0.5,
            }

    try:
        from app.engine.llm import chat_completion
        from app.engine.names import get_robot_name
        robot_name = await get_robot_name()
        ans = await chat_completion(
            messages=[{"role": "user", "content": text}],
            system_prompt=_GUARDED_SYSTEM_TEMPLATE.format(robot_name=robot_name),
            max_tokens=120,  # 60 字够口语答案, 太长 LLM 生成时间近线性
            temperature=0.3,  # 常识问答低温度更确定 + 减少发散字数
        )
        if ans and ans.strip():
            return {
                "answer": ans.strip(),
                "source": "llm_general",
                "confidence": 0.7,
            }
    except Exception as e:
        logger.warning("[GeneralSearch] LLM fail: %s", e)

    return {
        "answer": "这个问题我不太确定，您可以查一下手机或问问现场工作人员。",
        "source": "fallback",
        "confidence": 0.3,
    }
