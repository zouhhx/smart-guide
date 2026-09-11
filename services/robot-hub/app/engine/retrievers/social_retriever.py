"""
SocialRetriever — 社交寒暄

简短自然回复, 必要时带一句轻量引导, 不续讲旧展项。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("hub.engine.retriever.social")

_GREETING_RE = re.compile(r"(你好|您好|哈喽|hello|hi|嗨|早上好|下午好|晚上好)")
_THANKS_RE = re.compile(r"(谢谢|多谢|感谢)")
_BYE_RE = re.compile(r"(拜拜|再见)")
_WHO_RE = re.compile(r"(你叫什么|你是谁)")
_CAN_RE = re.compile(r"(你能(做|干)什么|你会什么|有什么(功能|本事)|帮助|功能)")


async def retrieve(text: str, slots: dict, world_state=None, robot_id: str = "") -> dict:
    from app.engine.names import get_robot_name
    robot_name = await get_robot_name()
    raw = text.strip()
    if _GREETING_RE.search(raw):
        return {"answer": f"您好，我是{robot_name}，很高兴为您服务。", "source": "social", "confidence": 0.95}
    if _THANKS_RE.search(raw):
        return {"answer": "不客气，随时为您服务。", "source": "social", "confidence": 0.95}
    if _BYE_RE.search(raw):
        return {"answer": "再见，欢迎再来。", "source": "social", "confidence": 0.95}
    if _WHO_RE.search(raw):
        return {"answer": f"我是展厅导览机器人{robot_name}，可以带您参观讲解。", "source": "social", "confidence": 0.95}
    if _CAN_RE.search(raw):
        return {
            "answer": "我可以带您参观讲解展项、回答展厅相关问题、控制展厅设备。"
                      "您可以说「开始参观」、「有哪些展项」或直接说展项名称。",
            "source": "social",
            "confidence": 0.95,
        }
    # 默认兜底: 走轻量 LLM 寒暄
    try:
        from app.engine.llm import chat_completion
        ans = await chat_completion(
            messages=[{"role": "user", "content": raw}],
            system_prompt=f"你是展厅导览机器人{robot_name}。访客在闲聊,用一句话(15-30字)自然回应,"
                          "可以轻量引导到参观话题,但不要主动续讲任何展项内容。",
            max_tokens=80,  # 30 字够闲聊, 缩短一半生成时间
            temperature=0.3,  # 闲聊用低温度更确定+更短, 0.7 容易发散
        )
        if ans and ans.strip():
            return {"answer": ans.strip(), "source": "llm_social", "confidence": 0.75}
    except Exception:
        pass
    return {"answer": "您好，有什么可以帮您的？", "source": "fallback", "confidence": 0.5}
