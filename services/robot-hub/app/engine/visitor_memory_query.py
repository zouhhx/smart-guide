"""
访客记忆查询 — 识别用户对"自己/历史"的问询，并用已绑定的访客档案 + 记忆回答。

覆盖类型:
- identity_check: 你认识我吗 / 记得我吗 / 我是谁
- ask_name: 我叫什么 / 我的名字
- visit_count: 我来过几次 / 我第几次来
- recall_history: 我昨天说了什么 / 我上次问了什么 / 我们聊过什么
- interest_query: 我常看什么 / 我关注什么
- recent_exhibit: 上次我看的什么展项

设计原则:
- 自然回答，不要过度暴露监视感
- 没有绑定访客时给友好引导（不直接说"你没注册"）
- 历史回顾要简洁，不背诵逐条对话
"""

import re
import logging
from app.database import get_db

logger = logging.getLogger("hub.engine.visitor_memory_query")


# ============================================================
# 查询分类
# ============================================================
# 顺序优先级: 越具体的放前面 (recall_history 要在 identity_check 前, 否则 "认识" 关键词会被误吞)
_QUERY_PATTERNS = [
    # recall_history: 问上次/历史对话内容
    ("recall_history", [
        r"(昨天|上次|上回|之前|刚才|刚刚|前.*天).*(说|问|聊|讲|谈)",
        r"(我们|咱们).*(聊过|讲过|说过|谈过|讨论过)",
        r"我.*(说过|问过|讲过|提过)什么",
        r"(历史|记录).*(问|对话|聊天)",
        r"上(一)?次.*(问|聊|讨论)",
    ]),
    # ask_name: 问自己的名字
    ("ask_name", [
        r"我叫(什么|啥|谁)",
        r"(知道|记得)我(的)?名字(吗|么)?$",
        r"我(的)?名字(是什么|叫什么|是啥)?$",
    ]),
    # visit_count: 问来访次数
    ("visit_count", [
        r"我.*(第几次|几次)来",
        r"我来过(几次|多少次)",
        r"(这是|是)我(第几次|第\d+次)",
        r"我来过这里吗",
    ]),
    # interest_query: 问自己的兴趣/偏好
    ("interest_query", [
        r"我(最|经常|常常|一般)?(喜欢|关注|感兴趣).*(什么|啥|哪)",
        r"我(的)?(兴趣|偏好|爱好)",
        r"我(常|经常|一般).*看(什么|啥)",
    ]),
    # recent_exhibit: 问最近看了什么展项
    ("recent_exhibit", [
        r"上次.*看.*(什么|啥|哪个展)",
        r"我.*之前看的(什么|啥|哪)",
        r"我最近.*看.*展",
    ]),
    # identity_check: 通用的"认识我吗" — 放最后兜底
    ("identity_check", [
        r"(你|机器人)?(还)?(认识|记得|认得|见过)我",
        r"我是谁",
        r"你.*(认得|记得).*我",
        r"认识(我|这个人)",
    ]),
]


def classify_memory_query(text: str) -> str | None:
    """识别访客记忆查询类型，返回类型或 None"""
    if not text:
        return None
    t = text.strip()
    for qtype, patterns in _QUERY_PATTERNS:
        for pat in patterns:
            if re.search(pat, t):
                return qtype
    return None


# ============================================================
# yes / no 分类（用于 followup 响应）
# ============================================================
_YES_PATTERNS = [
    r"^(需要|要|想要|要的|答应|同意|好|好的|好呀|好啊|好嘞|行|可以|可|没问题|没毛病|当然|行啊|可以的|嗯|嗯嗯|是的|对|对的|需要的|继续|ok|OK|好的呀)$",
    r"(带我参观|带我们参观|开始参观|开始讲解|自动讲解|带我看看|带我看一下|讲解一下|参观一下|帮我介绍)",
    r"(继续看|就看这个|就这个|我要看|想看这个|需要.*帮助|帮我.*看|帮我.*介绍)",
    r"^(要|好|需要|可以).{0,6}(看|继续|参观|讲解|帮助)",
]
_NO_PATTERNS = [
    r"^(不|否|不要|不用|不用了|不需要|不必|没必要|没有|没|没了|没事|没事了|不用帮忙|不需要帮忙|暂时不用|暂时不需要|算了|不用啦|不要了|不看|不想看|不感兴趣|不了|不啦|谢谢不用|谢谢不需要)$",
    r"(不用了|不需要|没有需要|没什么需要|没事了|暂时不用|暂时不需要|不用帮|不需要帮|不麻烦|不看了|不想看|不感兴趣)",
    r"(换.{0,2}(个|展项|别的)|看别的|看其他)",
    r"^(不|否)",
]


def classify_followup_response(text: str) -> str:
    """判断用户对 followup 问题的回答: 'yes' / 'no' / 'other'"""
    if not text:
        return "other"
    t = text.strip()
    for pat in _NO_PATTERNS:
        if re.search(pat, t):
            return "no"
    for pat in _YES_PATTERNS:
        if re.search(pat, t):
            return "yes"
    return "other"


# ============================================================
# 回答生成
# ============================================================
async def answer_memory_query(qtype: str, visitor: dict | None, robot_id: str = "") -> str:
    """兼容旧接口：只返回回答文本。需要 followup 的调用方用 answer_memory_query_ex。"""
    ans, _ = await answer_memory_query_ex(qtype, visitor, robot_id)
    return ans


async def answer_memory_query_ex(qtype: str, visitor: dict | None, robot_id: str = ""):
    """返回 (answer_text, followup_dict_or_None)

    followup 结构（下一轮用户说"继续"时要执行的动作）:
        {"action": "nav_exhibit", "exhibit_id": int, "exhibit_name": str}
    """
    # 未绑定访客的友好回答
    if visitor is None:
        return _answer_unknown_visitor(qtype), None

    name = visitor.get("display_name") or visitor.get("name") or ""
    visit_count = int(visitor.get("visit_count") or 0)

    if qtype == "identity_check":
        if visit_count > 1:
            return f"当然认识，{name}，欢迎回来！", None
        if name:
            return f"认识呀，{name}，很高兴见到您。", None
        return "认识的，欢迎！", None

    if qtype == "ask_name":
        if name:
            return f"您是{name}，我记着呢。", None
        return "我还没记住您的名字，您可以告诉我。", None

    if qtype == "visit_count":
        if visit_count <= 1:
            return f"{name}，这是您第一次来。", None
        return f"{name}，这是您第 {visit_count} 次来，欢迎回来！", None

    if qtype == "recall_history":
        return await _recall_history(visitor, robot_id)

    if qtype == "interest_query":
        return await _describe_interests(visitor)

    if qtype == "recent_exhibit":
        return await _recent_exhibit(visitor)

    # 未知类型兜底
    return _answer_unknown_visitor(qtype), None


def _answer_unknown_visitor(qtype: str) -> str:
    """未绑定访客时的回答（友好引导）"""
    if qtype == "identity_check":
        return "我们好像还没有正式认识呢，您可以请工作人员帮您录一下脸，下次见面我就能认出您了。"
    if qtype == "ask_name":
        return "我还不知道您的名字，您方便告诉我吗？"
    if qtype == "visit_count":
        return "这次我们好像是第一次见面。"
    if qtype == "recall_history":
        return "这应该是我们第一次交流，还没有什么过往记录。您想聊什么？"
    if qtype == "interest_query":
        return "我还不太了解您的偏好，您可以告诉我想看什么。"
    if qtype == "recent_exhibit":
        return "之前我们没聊过具体的展项，您想看哪个？我带您去。"
    return "您好，有什么可以帮您的？"


# ============================================================
# 历史回顾
# ============================================================
async def _recall_history(visitor: dict, robot_id: str = ""):
    """回顾访客历史问答，返回 (answer, followup)"""
    visitor_id = visitor.get("id")
    if not visitor_id:
        return "我这边还没找到您的聊天记录。", None

    db = await get_db()
    try:
        # 取最近 7 天内最多 5 条长期记忆（优先带 exhibit / topic 的）
        rows = await db.execute_fetchall(
            """SELECT question, answer, topic, exhibit_id, intent, created_at
               FROM visitor_memories
               WHERE visitor_id = ?
                 AND memory_level = 'long'
                 AND created_at > datetime('now','localtime','-7 days')
               ORDER BY created_at DESC
               LIMIT 5""",
            (visitor_id,),
        )
        memories = [dict(r) for r in rows]

        # 若无长期记忆, 兜底查最近任何记忆
        if not memories:
            rows2 = await db.execute_fetchall(
                """SELECT question, answer, topic, exhibit_id, intent, created_at
                   FROM visitor_memories
                   WHERE visitor_id = ?
                   ORDER BY created_at DESC
                   LIMIT 3""",
                (visitor_id,),
            )
            memories = [dict(r) for r in rows2]

        # 展项名缓存
        exhibit_ids = [m["exhibit_id"] for m in memories if m.get("exhibit_id")]
        name_map = {}
        if exhibit_ids:
            placeholders = ",".join(["?"] * len(exhibit_ids))
            ex_rows = await db.execute_fetchall(
                f"SELECT id, name FROM exhibits WHERE id IN ({placeholders})",
                exhibit_ids,
            )
            name_map = {r["id"]: r["name"] for r in ex_rows}
    finally:
        await db.close()

    name = visitor.get("display_name") or visitor.get("name") or ""

    if not memories:
        return f"{name}，我们之前好像没有深入聊过。您今天想看什么？", None

    # 构建自然化摘要 — 带 exhibit_id 的话题优先（可以真正导航）
    topics = []           # 仅用于 TTS 文案
    first_nav_target = None   # 第一个有 exhibit_id 的话题 → followup
    for m in memories:
        q = (m.get("question") or "").strip()
        ex_id = m.get("exhibit_id")
        ex_name = name_map.get(ex_id) if ex_id else ""
        topic = m.get("topic") or ex_name or ""
        if ex_name and ex_id and first_nav_target is None:
            first_nav_target = {"id": int(ex_id), "name": ex_name}
        if topic and topic not in topics:
            topics.append(topic)
        elif q and len(q) < 30 and q not in topics:
            topics.append(q)
    topics = topics[:3]

    if len(topics) == 1:
        body = f"上次您对「{topics[0]}」挺感兴趣的"
    elif len(topics) == 2:
        body = f"上次您聊过「{topics[0]}」和「{topics[1]}」"
    else:
        body = f"上次您聊过「{topics[0]}」「{topics[1]}」「{topics[2]}」等话题"

    prefix = f"{name}，" if name else ""
    if first_nav_target:
        tail = f"。想继续看「{first_nav_target['name']}」吗？"
        followup = {
            "action": "nav_exhibit",
            "exhibit_id": first_nav_target["id"],
            "exhibit_name": first_nav_target["name"],
        }
    else:
        tail = "。今天想继续聊这些，还是看点别的？"
        followup = None
    return f"{prefix}{body}{tail}", followup


# ============================================================
# 兴趣描述
# ============================================================
async def _describe_interests(visitor: dict):
    """返回 (answer, followup)。第一个高频展项作为 followup 目标。"""
    visitor_id = visitor.get("id")
    if not visitor_id:
        return "您想看什么呢？告诉我就好。", None

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT exhibit_id, COUNT(*) as cnt FROM visitor_memories
               WHERE visitor_id=? AND exhibit_id IS NOT NULL AND memory_level='long'
               GROUP BY exhibit_id ORDER BY cnt DESC LIMIT 3""",
            (visitor_id,),
        )
        exhibit_entries = []  # [(id, name)]
        if rows:
            ex_ids = [r["exhibit_id"] for r in rows]
            placeholders = ",".join(["?"] * len(ex_ids))
            nm = await db.execute_fetchall(
                f"SELECT id, name FROM exhibits WHERE id IN ({placeholders})", ex_ids,
            )
            name_map = {r["id"]: r["name"] for r in nm}
            for r in rows:
                eid = r["exhibit_id"]
                if eid in name_map:
                    exhibit_entries.append((int(eid), name_map[eid]))

        tag_rows = await db.execute_fetchall(
            """SELECT vt.name FROM visitor_profile_tags vpt
               JOIN visitor_tags vt ON vt.id = vpt.tag_id
               WHERE vpt.visitor_id = ?
               ORDER BY vpt.score DESC LIMIT 3""",
            (visitor_id,),
        )
        tag_names = [r["name"] for r in tag_rows]
    finally:
        await db.close()

    name = visitor.get("display_name") or visitor.get("name") or ""
    prefix = f"{name}，" if name else ""

    parts = []
    exhibit_names = [n for _, n in exhibit_entries]
    if exhibit_names:
        parts.append(f"您之前比较关注{' 和 '.join(exhibit_names)}")
    if tag_names:
        parts.append(f"聊过{' / '.join(tag_names)}等话题")

    if not parts:
        return f"{prefix}您可以说展项名，我带您看看。", None

    if exhibit_entries:
        first_id, first_name = exhibit_entries[0]
        answer = f"{prefix}{'，'.join(parts)}。要继续看「{first_name}」吗？"
        followup = {"action": "nav_exhibit", "exhibit_id": first_id, "exhibit_name": first_name}
        return answer, followup
    return f"{prefix}{'，'.join(parts)}。今天想看什么？", None


# ============================================================
# 最近展项
# ============================================================
async def _recent_exhibit(visitor: dict):
    """返回 (answer, followup)"""
    visitor_id = visitor.get("id")
    if not visitor_id:
        return "您想看哪个展项？说个名字我带您去。", None

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT m.exhibit_id, e.name, MAX(m.created_at) as last_at
               FROM visitor_memories m
               JOIN exhibits e ON e.id = m.exhibit_id
               WHERE m.visitor_id=? AND m.exhibit_id IS NOT NULL
               GROUP BY m.exhibit_id ORDER BY last_at DESC LIMIT 1""",
            (visitor_id,),
        )
    finally:
        await db.close()

    name = visitor.get("display_name") or visitor.get("name") or ""
    prefix = f"{name}，" if name else ""
    if not rows:
        return f"{prefix}我们还没有一起看过具体的展项，今天想看哪个？", None
    row = dict(rows[0])
    ex_id = int(row["exhibit_id"])
    ex_name = row["name"]
    answer = f"{prefix}您上次看的是「{ex_name}」。要继续看这个，还是换别的？"
    followup = {"action": "nav_exhibit", "exhibit_id": ex_id, "exhibit_name": ex_name}
    return answer, followup
