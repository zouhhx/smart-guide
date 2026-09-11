"""
HallKnowledgeRetriever — 展厅知识问答(展项详情/对比/方案场景)

数据源优先级:
  1. 文本中匹配到的展项 → exhibit_knowledge.faq + narration_text
  2. resources.narration_text (按展项关联)
  3. 多展项对比 → 拼两份 narration_text
  4. LLM 摘要(基于检索到的本地内容, 而非裸问)
  5. 实在没有 → 引导句
"""

from __future__ import annotations

import json
import logging
import re

from app.database import get_db

logger = logging.getLogger("hub.engine.retriever.hall_knowledge")


_KEYWORD_TOKENS = (
    "AI赋能", "中国移动", "云猴", "数字人", "机器人",
    "智慧城市", "智慧园区", "智慧社区", "智慧教育",
    "城市共享客厅", "思德", "解决方案", "应用场景",
    "大岛台", "小岛台", "CAVE", "三联屏", "广告机",
)


def _extract_keywords(text: str) -> list[str]:
    """从问句里抽取可能的检索词"""
    found = []
    for tok in _KEYWORD_TOKENS:
        if tok.lower() in text.lower() and tok not in found:
            found.append(tok)
    return found


def _strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"&[a-z]+;", " ", s)
    s = re.sub(r"&&高亮标记\s*\d+", " ", s)
    s = re.sub(r"高亮标记\s*\d+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


async def _find_exhibits_by_name(name: str) -> list[dict]:
    if not name:
        return []
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name FROM exhibits WHERE name LIKE ? OR name LIKE ?",
            (f"%{name}%", f"%{name[:2]}%"),
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def _get_exhibit_knowledge(exhibit_id: int) -> dict:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT name, narration_text, narrations, faq, key_data FROM exhibit_knowledge WHERE exhibit_id=?",
            (str(exhibit_id),),
        )
        if rows:
            return dict(rows[0])
    finally:
        await db.close()
    return {}


async def _get_resource_narrations(exhibit_id: int) -> list[str]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT r.narration_text FROM resources r
               JOIN special_exhibit_resources ser ON ser.resource_id = r.id
               WHERE ser.exhibit_id=? AND r.narration_text IS NOT NULL AND r.narration_text != ''
               LIMIT 5""",
            (exhibit_id,),
        )
        return [_strip_html(r["narration_text"]) for r in rows if r["narration_text"]]
    except Exception:
        return []
    finally:
        await db.close()


def _match_faq(faq_json: str, question: str) -> str | None:
    if not faq_json:
        return None
    try:
        items = json.loads(faq_json)
    except Exception:
        return None
    if not isinstance(items, list):
        return None
    q_lower = question.lower()
    best = None
    best_overlap = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        q = (item.get("q") or item.get("question") or "").strip()
        a = (item.get("a") or item.get("answer") or "").strip()
        if not q or not a:
            continue
        if q in question or question in q:
            return a
        overlap = sum(1 for ch in q if ch in q_lower)
        if overlap > best_overlap and overlap >= 4:
            best_overlap = overlap
            best = a
    return best


async def _llm_summarize(question: str, context: str, visitor_ctx: str = "") -> str | None:
    """基于本地知识检索结果做 LLM 摘要 — 不是裸问 LLM.
    visitor_ctx: 人脸识别识别出的访客身份+历史记忆片段, 已组装好, 直接拼到 user 消息里."""
    if not context:
        return None
    try:
        from app.engine.llm import chat_completion
        system = (
            "你是青田'城市共享客厅'展厅的讲解助手。下面给出本展厅相关资料(可能是展项讲解词或资源讲解词)。"
            "请结合资料尽量回答访客问题: "
            "1) 资料里有相关内容就基于资料组织答案; "
            "2) 资料只提到部分信息时,可以补充少量你已知的常识把答案说完整(尤其问'是什么/做什么的'类); "
            "3) 完全无关时才说「这个问题展厅资料里没有详细说明,您可以问问现场工作人员」; "
            "4) 若访客信息里有姓名/偏好/上次看过的展项, 可以自然地引用(比如「XX 您好, 上次您看过...」), 但不要炫耀"
            "记忆, 不要重复访客自己刚说的话; "
            "5) 答案50-150字, 自然口语化, 适合TTS播报, 不列点, 不使用markdown。"
        )
        user_parts = []
        if visitor_ctx:
            user_parts.append(f"访客信息:\n{visitor_ctx[:500]}")
        user_parts.append(f"展厅资料:\n{context[:1500]}")
        user_parts.append(f"访客问题: {question}")
        user = "\n\n".join(user_parts)
        return await chat_completion(
            messages=[{"role": "user", "content": user}],
            system_prompt=system,
            max_tokens=300,
            temperature=0.4,
        )
    except Exception as e:
        logger.warning("[HallKnowledge] LLM summarize fail: %s", e)
        return None


async def retrieve(text: str, slots: dict, world_state, robot_id: str) -> dict:
    name_hint = (slots or {}).get("exhibit_name", "")
    is_compare = (slots or {}).get("is_compare", False)
    topic = (slots or {}).get("topic", "")

    # 提取所有可能展项名(对比类: 大岛台和小岛台 → 取两个)
    candidates: list[dict] = []
    if name_hint:
        candidates = await _find_exhibits_by_name(name_hint)
    if is_compare:
        # 抓第二个
        for kw in ("大岛台", "小岛台", "CAVE", "三联屏", "广告机"):
            if kw == name_hint:
                continue
            if kw.lower() in text.lower():
                more = await _find_exhibits_by_name(kw)
                candidates.extend(more)
                break

    # 收集本地资料
    pieces: list[str] = []
    primary_name = ""
    primary_narration = ""
    primary_faq_answer = None

    for ex in candidates[:2]:
        ex_id = int(ex["id"])
        ex_name = ex["name"]
        if not primary_name:
            primary_name = ex_name
        knowledge = await _get_exhibit_knowledge(ex_id)
        narration = (knowledge.get("narration_text") or "").strip()
        if narration:
            pieces.append(f"【{ex_name}】{narration}")
            if not primary_narration:
                primary_narration = narration
        # FAQ
        faq_ans = _match_faq(knowledge.get("faq", ""), text)
        if faq_ans and not primary_faq_answer:
            primary_faq_answer = faq_ans
        # resources.narration_text
        res_narrations = await _get_resource_narrations(ex_id)
        for n in res_narrations[:2]:
            if n:
                pieces.append(f"【{ex_name}-资源】{n[:400]}")

    # FAQ 命中: 直接返回, 最高置信度
    if primary_faq_answer:
        return {
            "answer": primary_faq_answer,
            "source": "exhibit_faq",
            "confidence": 0.95,
            "exhibit_name": primary_name,
        }

    # 对比类 + 双方都有 narration: 优先用本地两段摘要
    context = "\n".join(pieces)

    # 没匹到展项: 把 name_hint / topic 当主题词去 narration_text 全文搜
    fallback_terms = []
    for term in (name_hint, topic, *_extract_keywords(text)):
        if term and term not in fallback_terms:
            fallback_terms.append(term)
    if not pieces and fallback_terms:
        db = await get_db()
        try:
            for term in fallback_terms[:3]:
                rows = await db.execute_fetchall(
                    "SELECT name, narration_text FROM exhibit_knowledge "
                    "WHERE narration_text LIKE ? LIMIT 3",
                    (f"%{term}%",),
                )
                for r in rows:
                    if r["narration_text"]:
                        pieces.append(f"【{r['name']}】{r['narration_text']}")
                if pieces:
                    break
            # 同样在 resources.narration_text 兜底
            if not pieces:
                for term in fallback_terms[:3]:
                    rows = await db.execute_fetchall(
                        "SELECT name, narration_text FROM resources "
                        "WHERE narration_text LIKE ? AND narration_text != '' LIMIT 3",
                        (f"%{term}%",),
                    )
                    for r in rows:
                        if r["narration_text"]:
                            pieces.append(f"【资源-{r['name']}】{_strip_html(r['narration_text'])[:500]}")
                    if pieces:
                        break
            context = "\n".join(pieces)
        finally:
            await db.close()

    # 还是没有: 只有当用户问题含展厅关键词(说明确实在问展厅业务)才调 LLM 兜底,
    # 否则直接走固定兜底, 避免 LLM 对任意话题编造答案.
    # 关键词命中说明话题与展厅业务相关, LLM 基于 system prompt 给通用答复较安全;
    # 没命中则用户很可能在闲聊/乱问, 拒答更可靠.
    if not pieces:
        _has_hall_keyword = bool(_extract_keywords(text)) or bool(name_hint) or bool(topic)
        if not _has_hall_keyword:
            logger.info("[HallKnowledge] 无展厅关键词命中且无本地资料, 直接兜底不调 LLM")
            return {
                "answer": "这个问题展厅资料里暂时没有明确说明，您可以看屏幕说「有哪些展项」浏览，或问现场工作人员。",
                "source": "fallback_no_anchor",
                "confidence": 0.3,
                "exhibit_name": primary_name,
            }
        try:
            from app.engine.llm import chat_completion
            llm_ans = await chat_completion(
                messages=[{"role": "user", "content": text}],
                system_prompt=(
                    "你是青田'城市共享客厅'展厅的讲解助手, 这是一个展示中国移动相关数字化解决方案"
                    "(智慧社区/智慧园区/AI赋能等)的展厅。访客的问题与展厅业务相关但本展厅没有具体资料。"
                    "请基于你了解的常识简短(50-150字)、口语化、不列点回答; 实在不确定就说"
                    "「这个问题展厅资料里没有明确说明,您可以问问现场工作人员」, 不要编造。"
                ),
                max_tokens=300,
                temperature=0.5,
            )
            if llm_ans and llm_ans.strip():
                return {
                    "answer": llm_ans.strip(),
                    "source": "llm_general_with_hall_context",
                    "confidence": 0.65,
                    "exhibit_name": primary_name,
                }
        except Exception as e:
            logger.warning("[HallKnowledge] generic LLM fail: %s", e)

    # 资料够了 → LLM 摘要 (带上访客身份上下文)
    visitor_ctx = getattr(world_state, "_visitor_memory_context", "") or "" if world_state else ""
    if context:
        llm_answer = await _llm_summarize(text, context, visitor_ctx=visitor_ctx)
        if llm_answer:
            return {
                "answer": llm_answer.strip(),
                "source": "llm_over_local",
                "confidence": 0.82,
                "exhibit_name": primary_name,
            }
        # LLM 不可用 → 截断本地原文回退
        if primary_narration:
            short = primary_narration[:160]
            return {
                "answer": short + ("…" if len(primary_narration) > 160 else ""),
                "source": "local_narration",
                "confidence": 0.7,
                "exhibit_name": primary_name,
            }

    # 实在没有 → 引导
    return {
        "answer": "这个问题展厅资料里暂时没有明确说明，您可以问问现场工作人员。",
        "source": "fallback",
        "confidence": 0.3,
        "exhibit_name": primary_name,
    }
