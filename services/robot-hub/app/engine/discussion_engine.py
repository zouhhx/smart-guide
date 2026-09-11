"""
DiscussionEngine — 联动讨论引擎

功能：用户提出话题后，LLM生成机器人+数字人3轮对话，交替播报模拟讨论。

使用方式：
    from app.engine.discussion_engine import DiscussionSession, digital_human_speak
    session = DiscussionSession(topic="人工智能")
    ok = await session.generate_dialogue()
    if ok:
        for round_data in session.dialogue_rounds:
            await robot_speak(round_data["robot"])
            await digital_human_speak(round_data["digital_human"])
"""

import asyncio
import json
import logging
import urllib.parse
import urllib.request

from app.database import get_db

logger = logging.getLogger("hub.engine.discussion")


# ============================================================
# 角色名称配置
# ============================================================

async def _get_character_names() -> tuple[str, str]:
    """从 system_config 读取机器人/数字人显示名称（统一走 names 缓存）"""
    from app.engine.names import get_names
    return await get_names()


# ============================================================
# 数字人播报
# ============================================================

_DH_BASE_URL = "http://192.168.16.18:8899"


async def digital_human_speak(text: str) -> bool:
    """让数字人播报一段文字（HTTP API）"""
    if not text:
        return False
    encoded = urllib.parse.urlencode({"text": text}, safe="")
    url = f"{_DH_BASE_URL}/api/voiceInput?{encoded}"

    def _req(u):
        return urllib.request.urlopen(urllib.request.Request(u), timeout=10)

    try:
        await asyncio.get_event_loop().run_in_executor(None, _req, url)
        logger.info("[讨论] 数字人播报: %s", text[:30])
        return True
    except Exception as e:
        logger.error("[讨论] 数字人播报失败: %s", e)
        return False


async def digital_human_show(exhibition_id: str = "") -> bool:
    """显示数字人"""
    params = {"command": "Show"}
    if exhibition_id:
        params["exhibitionID"] = exhibition_id
    q = urllib.parse.urlencode(params)
    url = f"{_DH_BASE_URL}/digitalHuman?{q}"

    def _req(u):
        return urllib.request.urlopen(urllib.request.Request(u), timeout=10)

    try:
        await asyncio.get_event_loop().run_in_executor(None, _req, url)
        logger.info("[讨论] 数字人显示")
        return True
    except Exception as e:
        logger.error("[讨论] 数字人显示失败: %s", e)
        return False


# ============================================================
# 讨论会话
# ============================================================

class DiscussionSession:
    """一次话题讨论会话"""

    def __init__(self, topic: str, robot_id: str = ""):
        self.topic = topic
        self.robot_id = robot_id
        self.dialogue_rounds: list[dict] = []
        self._cancelled = False

    def cancel(self):
        """取消讨论"""
        self._cancelled = True
        logger.info("[讨论] 取消讨论: %s", self.topic)

    def is_cancelled(self) -> bool:
        return self._cancelled

    async def generate_dialogue(self, world_context: str = "") -> bool:
        """调用LLM生成3轮对话脚本

        Returns:
            True 生成成功, self.dialogue_rounds 已填充
            False 生成失败
        """
        from app.engine.llm import chat_completion

        robot_name, dh_name = await _get_character_names()

        system_prompt = (
            f"你是展厅对话设计师。围绕话题设计3轮对话。\n"
            f"角色：\n"
            f"- {robot_name}（机器人）：热情专业，善于提问引导\n"
            f"- {dh_name}（数字人）：活泼生动，善于补充举例\n"
            f"要求：\n"
            f"- 每句话30-80字，口语化，适合语音播报\n"
            f"- 对话有观点碰撞或互补，不是空话套话\n"
            f"- 返回纯JSON数组，格式：\n"
            f'  [{{"round":1,"robot":"...","digital_human":"..."}},...]\n'
            f"只返回JSON，不要其他内容。"
        )

        user_content = f"话题：{self.topic}"
        if world_context:
            user_content += f"\n当前展厅上下文：{world_context}"

        messages = [{"role": "user", "content": user_content}]

        try:
            result = await asyncio.wait_for(
                chat_completion(
                    messages=messages,
                    system_prompt=system_prompt,
                    max_tokens=1024,
                    temperature=0.8,
                ),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            logger.error("[讨论] LLM生成超时")
            return False

        if not result:
            logger.error("[讨论] LLM返回空")
            return False

        # 解析JSON
        rounds = self._parse_dialogue_json(result)
        if not rounds:
            logger.error("[讨论] 解析对话JSON失败: %s", result[:200])
            return False

        self.dialogue_rounds = rounds
        logger.info("[讨论] 生成%d轮对话: %s", len(rounds), self.topic)
        return True

    def _parse_dialogue_json(self, content: str) -> list[dict]:
        """解析LLM返回的对话JSON"""
        content = content.strip()
        # 去掉markdown代码块
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines).strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取JSON部分
            start = content.find("[")
            end = content.rfind("]")
            if start >= 0 and end > start:
                try:
                    data = json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    return []
            else:
                return []

        if not isinstance(data, list):
            return []

        rounds = []
        for item in data:
            if not isinstance(item, dict):
                continue
            robot_text = item.get("robot", "").strip()
            dh_text = item.get("digital_human", "").strip()
            if robot_text or dh_text:
                rounds.append({
                    "round": item.get("round", len(rounds) + 1),
                    "robot": robot_text,
                    "digital_human": dh_text,
                })

        return rounds
