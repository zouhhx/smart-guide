"""
QA 缓存预热 — 启动时取近 N 天高频用户文本, 跑一次完整问答管线写入 decision_cache。

目标: 现场再次提同样问题时 <30ms 命中缓存, 不再走 OpenClaw/LLM 5-15s。

设计:
- 不打断主路径: 异步串行跑, 每条问句之间 sleep 50ms 让网络/CPU 喘气
- 仅 warm 真实可缓存的问句: 命中 OpenClaw decision (HALL_CONTEXT) 或 LLM 直答的常问
- 失败的问句不重试 (避免无限 loop)
- 全程 try/except 兜底, 单条失败不影响其它
"""

from __future__ import annotations

import asyncio
import logging

from app.database import get_db

logger = logging.getLogger("hub.engine.qa_warmup")


async def _fetch_top_user_texts(top_k: int, since_days: int) -> list[str]:
    """从 execution_logs 提取近 N 天高频用户文本。

    数据来源:
      log_type='robot_event' action='evt_speech_result' detail=JSON 含 .text  (豹小秘)
      log_type='unitree_event' action='asr' detail=JSON 含 .text  (宇树)
    Python 端解析 JSON, 避免 SQLite json_extract 在非 JSON 行上整查询失败。
    Returns: ['南京天气', '附近有什么美食', ...]
    """
    import json as _json
    from collections import Counter
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            f"""
            SELECT detail FROM execution_logs
            WHERE created_at >= datetime('now','localtime','-{int(since_days)} days')
              AND (
                (log_type='robot_event' AND action='evt_speech_result')
                OR
                (log_type='unitree_event' AND action='asr')
              )
              AND detail LIKE '%text%'
            """
        )
    finally:
        await db.close()
    counter: Counter = Counter()
    for r in rows:
        d = r["detail"] or ""
        if not d:
            continue
        try:
            obj = _json.loads(d)
        except Exception:
            continue
        text = (obj.get("text") if isinstance(obj, dict) else "") or ""
        text = text.strip()
        if not (2 <= len(text) <= 30):
            continue
        # 过滤设备回声 / 无意义短语
        if text in ("启动持续拾音模式成功", "停止持续拾音模式成功"):
            continue
        counter[text] += 1
    return [t for t, _ in counter.most_common(top_k)]


# 预热跳过的 qtype: 这两类答案依赖当前世界状态(展项/专场/播放/绑定关系)
# 或纯闲聊上下文, 缓存命中率极低且烧 token, 不预热。
_WARMUP_SKIP_QTYPES = {"tour_control", "social_fallback"}

# 预热跳过的 intent: 即便 QueryRouter 没归类成 tour_control,
# 一旦 IntentResolver 把它识别为参观控制类意图(导航/列表/播放/暂停/继续/结束/触发词/中控指令),
# 都不该缓存 — 这些行为依赖现场状态。
_WARMUP_SKIP_INTENTS = {
    "navigate_to", "start_tour", "end_tour",
    "list_exhibits", "list_exhibit_resources", "list_specials",
    "switch_special", "play_resource", "play_next", "skip_exhibit",
    "wait_please", "resume_please",
    "set_time", "ask_current",
    "device_control", "central_control",
    "self_intro",
}


async def warmup_top_queries(top_k: int = 30, since_days: int = 7) -> int:
    """对 top_k 条高频问句各跑一遍完整问答管线, 写入 decision_cache。

    跳过 tour_control / social_fallback / 命令类意图 — 这些走世界状态, 缓存无意义。
    Returns: 实际命中的条数。
    """
    try:
        texts = await _fetch_top_user_texts(top_k=top_k, since_days=since_days)
    except Exception as e:
        logger.warning("[QAWarmup] 取 top 问句失败: %s", e)
        return 0
    if not texts:
        logger.info("[QAWarmup] 历史日志中无高频问句, 跳过")
        return 0

    from app.engine.query_router import classify_query
    from app.engine.answer_engine import answer as _qa_answer
    from app.engine.world_state import get_world_state
    try:
        from app.engine.intent_resolver import resolve_intent
    except Exception:
        resolve_intent = None  # 找不到 resolver 时只用 qtype 过滤兜底

    ws = get_world_state()
    ok = 0
    skipped = 0
    for i, text in enumerate(texts):
        try:
            qr = classify_query(text, ws)
            qtype = qr.get("qtype", "")
            slots = qr.get("slots", {}) or {}

            # 第一道过滤: qtype 黑名单
            if qtype in _WARMUP_SKIP_QTYPES:
                skipped += 1
                logger.debug("[QAWarmup] 跳过 qtype=%s text=%r", qtype, text[:30])
                continue

            # 第二道过滤: intent_resolver — 防止 QueryRouter 漏归类的命令式输入。
            # skip_llm_fallback=True 让 resolver 跑完规则/触发词/拼音回退就停, 不要又去拉 LLM
            # 否则预热阶段的"过滤"步骤本身就在烧 token, 跟初衷反了。
            if resolve_intent is not None:
                try:
                    ir = await asyncio.wait_for(
                        resolve_intent(text, ws, skip_llm_fallback=True),
                        timeout=2.0,
                    )
                    ir_intent = (ir or {}).get("intent", "")
                    ir_conf = float((ir or {}).get("confidence", 0.0))
                    ir_source = (ir or {}).get("source", "")
                    # 触发词命中 → 一定是参观流程, 跳。
                    # 命令类意图 conf>=0.6 → 跳 (避免规则模糊匹配的误命中也被预热)
                    if ir_source == "trigger" or (ir_intent in _WARMUP_SKIP_INTENTS and ir_conf >= 0.6):
                        skipped += 1
                        logger.debug("[QAWarmup] 跳过 intent=%s(%.2f src=%s) text=%r",
                                     ir_intent, ir_conf, ir_source, text[:30])
                        continue
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    pass

            # 跑完整管线 — answer_engine 内部会调 should_handoff/decide → 写 _decision_cache
            #              → realtime_provider/poi_retriever → 写各自 _WEATHER_CACHE/_POI_CACHE
            await asyncio.wait_for(
                _qa_answer(qtype, text, slots, ws, robot_id="warmup"),
                timeout=20.0,
            )
            ok += 1
            logger.info("[QAWarmup] %d/%d warmed: qtype=%s text=%r",
                        i + 1, len(texts), qtype, text[:30])
        except asyncio.TimeoutError:
            logger.debug("[QAWarmup] 超时跳过: %r", text[:30])
        except Exception as e:
            logger.debug("[QAWarmup] 跑 %r 失败: %s", text[:30], e)
        # 给主路径让出 CPU + 避免打爆外部 API
        await asyncio.sleep(0.05)
    logger.info("[QAWarmup] 完成: warmed=%d skipped=%d total=%d", ok, skipped, len(texts))
    return ok
