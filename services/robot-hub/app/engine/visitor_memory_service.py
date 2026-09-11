"""
访客记忆服务 — 问答写入、历史检索、画像更新

记忆写入门槛:
- 不进记忆: 嗯、好的、谢谢、再见、继续、下一个 等简单回复
- 短期记忆: 一次性操作指令
- 长期记忆: 技术问题、展项追问、方案咨询、偏好类问题

记忆使用原则:
- 主要用于提升对话连续性
- 不要默认生成监视感表达
- 优先自然延续
"""

import json
import logging
import re

from app.database import get_db

logger = logging.getLogger("hub.engine.visitor_memory")

# ============================================================
# 写入门槛判断
# ============================================================
# 不进记忆的短语
_SKIP_PATTERNS = {
    "嗯", "好的", "好", "谢谢", "再见", "继续", "下一个", "不用了",
    "可以了", "知道了", "明白了", "哦", "嗯嗯", "行", "ok", "OK",
    "是的", "对", "没了", "够了", "不需要", "结束",
}

# 短期记忆关键词（操作类指令）
_SHORT_TERM_PATTERNS = [
    r"^(开灯|关灯|全开|全关|去充电|切换)",
    r"^(第[一二三四五六七八九十\d]+个)",
    r"^(播放|跳过|暂停|停止)",
]

# 长期记忆关键词
_LONG_TERM_KEYWORDS = [
    "怎么", "如何", "为什么", "什么原理", "什么技术", "什么区别",
    "对比", "方案", "建议", "推荐", "感兴趣", "喜欢", "想了解",
    "能不能", "可以吗", "有没有", "多少钱", "价格", "合作",
    "原理", "参数", "规格", "性能", "特点", "优势",
]


def classify_memory_level(question: str, intent: str) -> str:
    """判断记忆级别: skip / short / long"""
    q = question.strip()
    if not q or q in _SKIP_PATTERNS or len(q) <= 2:
        return "skip"

    # 设备控制、简单选择 → 短期
    for pat in _SHORT_TERM_PATTERNS:
        if re.match(pat, q):
            return "short"

    # 控制类意图 → 短期
    if intent in ("lights_on", "lights_off", "all_on", "all_off",
                   "play_resource", "play_next", "skip_exhibit",
                   "switch_special", "go_charging", "ad_screen_on", "ad_screen_off"):
        return "short"

    # 闲聊类 → 短期
    if intent in ("chitchat", "weather_query"):
        return "short"

    # 包含长期关键词 → 长期
    for kw in _LONG_TERM_KEYWORDS:
        if kw in q:
            return "long"

    # 提问类（ask_explain, ask_current, navigate_to带问题）→ 长期
    if intent in ("ask_explain", "ask_current", "interactive_discuss",
                   "query_help", "query_status"):
        return "long"

    # 默认短期
    return "short"


# ============================================================
# 记忆写入
# ============================================================
async def save_memory(visitor_id: int, robot_id: str, question: str, answer: str,
                       intent: str = "", topic: str = "",
                       special_id: int = None, exhibit_id: int = None,
                       raw_context: dict = None):
    """保存一条问答记忆"""
    level = classify_memory_level(question, intent)
    if level == "skip":
        return None

    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO visitor_memories
               (visitor_id, robot_id, question, answer, intent, topic,
                special_id, exhibit_id, memory_level, raw_context_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (visitor_id, robot_id, question, answer, intent, topic,
             special_id, exhibit_id, level,
             json.dumps(raw_context or {}, ensure_ascii=False)),
        )
        await db.commit()
        logger.debug("[Memory] 保存记忆: visitor=%d level=%s intent=%s q=%s",
                     visitor_id, level, intent, question[:30])
        return {"level": level, "visitor_id": visitor_id}
    finally:
        await db.close()


# ============================================================
# 记忆检索
# ============================================================
async def get_relevant_memories(visitor_id: int, current_question: str = "",
                                  exhibit_id: int = None, special_id: int = None,
                                  limit: int = 5) -> list[dict]:
    """检索与当前上下文相关的访客历史记忆

    策略（第一版，不用向量库）:
    1. 同一展项的历史问答（最相关）
    2. 同一专场的历史问答
    3. 关键词匹配的历史问答
    4. 最近的长期记忆
    """
    db = await get_db()
    try:
        results = []
        seen_ids = set()

        # 1. 同展项记忆
        if exhibit_id:
            rows = await db.execute_fetchall(
                """SELECT * FROM visitor_memories
                   WHERE visitor_id=? AND exhibit_id=? AND memory_level='long'
                   ORDER BY created_at DESC LIMIT ?""",
                (visitor_id, exhibit_id, limit),
            )
            for r in rows:
                if r["id"] not in seen_ids:
                    results.append(dict(r))
                    seen_ids.add(r["id"])

        # 2. 同专场记忆
        if special_id and len(results) < limit:
            rows = await db.execute_fetchall(
                """SELECT * FROM visitor_memories
                   WHERE visitor_id=? AND special_id=? AND memory_level='long'
                   ORDER BY created_at DESC LIMIT ?""",
                (visitor_id, special_id, limit - len(results)),
            )
            for r in rows:
                if r["id"] not in seen_ids:
                    results.append(dict(r))
                    seen_ids.add(r["id"])

        # 3. 关键词匹配
        if current_question and len(results) < limit:
            # 提取关键词（简单分词：去掉停用词，取2字以上的片段）
            keywords = _extract_keywords(current_question)
            for kw in keywords[:3]:  # 最多用3个关键词
                if len(results) >= limit:
                    break
                rows = await db.execute_fetchall(
                    """SELECT * FROM visitor_memories
                       WHERE visitor_id=? AND memory_level='long'
                             AND (question LIKE ? OR answer LIKE ?)
                       ORDER BY created_at DESC LIMIT ?""",
                    (visitor_id, f"%{kw}%", f"%{kw}%", limit - len(results)),
                )
                for r in rows:
                    if r["id"] not in seen_ids:
                        results.append(dict(r))
                        seen_ids.add(r["id"])

        # 4. 最近长期记忆（兜底）
        if len(results) < limit:
            rows = await db.execute_fetchall(
                """SELECT * FROM visitor_memories
                   WHERE visitor_id=? AND memory_level='long'
                   ORDER BY created_at DESC LIMIT ?""",
                (visitor_id, limit - len(results)),
            )
            for r in rows:
                if r["id"] not in seen_ids:
                    results.append(dict(r))
                    seen_ids.add(r["id"])

        return results[:limit]
    finally:
        await db.close()


def _extract_keywords(text: str) -> list[str]:
    """简单关键词提取"""
    _STOPWORDS = {"的", "了", "吗", "呢", "吧", "啊", "哦", "是", "在", "有", "和", "与",
                  "这", "那", "个", "们", "我", "你", "他", "她", "它", "什么", "怎么",
                  "一下", "一个", "可以", "能不能", "请问", "想", "看", "说"}
    # Split by common delimiters and filter
    words = re.split(r'[\s,，。.！!？?、;；：:]+', text)
    keywords = []
    for w in words:
        w = w.strip()
        if len(w) >= 2 and w not in _STOPWORDS:
            keywords.append(w)
    return keywords


def build_memory_context(memories: list[dict], visitor_name: str = "") -> str:
    """将历史记忆构建为可注入的上下文字符串

    原则：自然延续，不暴露监视感
    """
    if not memories:
        return ""

    parts = []
    if visitor_name:
        parts.append(f"当前访客: {visitor_name}")

    parts.append("访客历史关注点:")
    for m in memories[:5]:
        q = (m.get("question") or "")[:60]
        topic = m.get("topic") or ""
        if topic:
            parts.append(f"- 曾关注{topic}: {q}")
        elif q:
            parts.append(f"- 曾问: {q}")

    return "\n".join(parts)


# ============================================================
# 画像更新
# ============================================================
async def update_profile_summary(visitor_id: int):
    """基于历史记忆更新访客画像摘要"""
    db = await get_db()
    try:
        # 统计信息
        count_row = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM visitor_memories WHERE visitor_id=?",
            (visitor_id,),
        )
        total_memories = count_row[0]["cnt"]

        # 常看展项
        exhibit_rows = await db.execute_fetchall(
            """SELECT exhibit_id, COUNT(*) as cnt FROM visitor_memories
               WHERE visitor_id=? AND exhibit_id IS NOT NULL
               GROUP BY exhibit_id ORDER BY cnt DESC LIMIT 5""",
            (visitor_id,),
        )

        # 常见意图/话题
        topic_rows = await db.execute_fetchall(
            """SELECT topic, COUNT(*) as cnt FROM visitor_memories
               WHERE visitor_id=? AND topic != '' AND topic IS NOT NULL
               GROUP BY topic ORDER BY cnt DESC LIMIT 5""",
            (visitor_id,),
        )

        intent_rows = await db.execute_fetchall(
            """SELECT intent, COUNT(*) as cnt FROM visitor_memories
               WHERE visitor_id=? AND intent != '' AND intent IS NOT NULL
               GROUP BY intent ORDER BY cnt DESC LIMIT 5""",
            (visitor_id,),
        )

        # 构建摘要
        summary_parts = [f"累计问答 {total_memories} 次"]

        if exhibit_rows:
            # look up exhibit names
            exhibit_ids = [str(r["exhibit_id"]) for r in exhibit_rows if r["exhibit_id"]]
            if exhibit_ids:
                placeholders = ",".join(["?"] * len(exhibit_ids))
                name_rows = await db.execute_fetchall(
                    f"SELECT id, name FROM exhibits WHERE id IN ({placeholders})",
                    [int(eid) for eid in exhibit_ids],
                )
                name_map = {r["id"]: r["name"] for r in name_rows}
                exhibit_names = [name_map.get(r["exhibit_id"], f"#{r['exhibit_id']}")
                                for r in exhibit_rows if r["exhibit_id"]]
                if exhibit_names:
                    summary_parts.append(f"常看展项: {', '.join(exhibit_names[:3])}")

        if topic_rows:
            topics = [r["topic"] for r in topic_rows]
            summary_parts.append(f"关注话题: {', '.join(topics[:3])}")

        if intent_rows:
            intent_labels = {
                "ask_explain": "讲解咨询",
                "navigate_to": "展项导航",
                "interactive_discuss": "话题讨论",
                "ask_current": "展项询问",
                "query_help": "功能帮助",
            }
            intents = [intent_labels.get(r["intent"], r["intent"]) for r in intent_rows
                       if r["intent"] not in ("chitchat", "play_resource", "play_next")]
            if intents:
                summary_parts.append(f"偏好行为: {', '.join(intents[:3])}")

        summary = "；".join(summary_parts)

        await db.execute(
            "UPDATE visitor_profiles SET profile_summary=?, updated_at=datetime('now','localtime') WHERE id=?",
            (summary, visitor_id),
        )
        await db.commit()
        logger.info("[Memory] 画像更新: visitor=%d summary=%s", visitor_id, summary[:60])
        return summary
    finally:
        await db.close()
