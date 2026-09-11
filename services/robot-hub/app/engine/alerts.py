"""
Alerts — 系统异常监控聚合

设计:
- 一张表 system_alerts, 每条告警带 source+kind+severity+message+context
- 合并策略: 同 source+kind 在 5min 窗口内只新增 occurrences, 不灌新行
- 可选 webhook: system_config.alerts_webhook_url 配置后, 新告警异步 POST 过去
- 不抛异常: 记录失败不影响主流程

调用:
    from app.engine.alerts import record_alert
    await record_alert("tour_planner", "llm_timeout", "LLM 规划超时25s",
                        severity="warning", robot_id=rid, text=user_text)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.database import get_db

logger = logging.getLogger("hub.engine.alerts")

# 告警合并窗口(秒): 同 (source, kind) 在窗口内只累加 occurrences
_COALESCE_WINDOW = 300

# 内存去重: (source, kind) → (last_ts, count)
_last_alert: dict[tuple[str, str], tuple[float, int]] = {}


async def _ensure_table() -> None:
    db = await get_db()
    try:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS system_alerts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at   TIMESTAMP DEFAULT (datetime('now','localtime')),
                source       TEXT DEFAULT '',
                severity     TEXT DEFAULT 'warning',
                kind         TEXT DEFAULT '',
                message      TEXT DEFAULT '',
                robot_id     TEXT DEFAULT '',
                context_json TEXT DEFAULT '',
                occurrences  INTEGER DEFAULT 1,
                last_seen_at TIMESTAMP DEFAULT (datetime('now','localtime')),
                resolved     INTEGER DEFAULT 0,
                resolved_at  TIMESTAMP
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sa_created ON system_alerts(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sa_source_kind ON system_alerts(source, kind)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sa_resolved ON system_alerts(resolved)")
        await db.commit()
    except Exception as e:
        logger.debug("[Alerts] 建表失败: %s", e)
    finally:
        await db.close()


async def record_alert(
    source: str,
    kind: str,
    message: str,
    severity: str = "warning",
    robot_id: str = "",
    **context: Any,
) -> None:
    """记录一条系统告警. 失败静默, 不影响主流程."""
    try:
        await _ensure_table()
        now = time.time()
        key = (source, kind)
        last_ts, _count = _last_alert.get(key, (0.0, 0))
        db = await get_db()
        try:
            if now - last_ts < _COALESCE_WINDOW:
                # 合并到最近一条未解决告警
                rows = await db.execute_fetchall(
                    """SELECT id FROM system_alerts
                       WHERE source=? AND kind=? AND resolved=0
                       ORDER BY id DESC LIMIT 1""",
                    (source, kind),
                )
                if rows:
                    await db.execute(
                        "UPDATE system_alerts SET occurrences=occurrences+1, "
                        "last_seen_at=datetime('now','localtime') WHERE id=?",
                        (rows[0]["id"],),
                    )
                    await db.commit()
                    _last_alert[key] = (now, _count + 1)
                    logger.info("[Alerts] 合并 %s/%s occ+1", source, kind)
                    return
            # 新建一条
            await db.execute(
                """INSERT INTO system_alerts (source, severity, kind, message,
                                              robot_id, context_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    source, severity, kind, (message or "")[:500], robot_id,
                    json.dumps(context, ensure_ascii=False, default=str)[:2000],
                ),
            )
            await db.commit()
        finally:
            await db.close()
        _last_alert[key] = (now, 1)
        logger.warning("[Alerts] %s severity=%s source=%s kind=%s robot=%s msg=%s",
                       "NEW", severity, source, kind, robot_id, message[:100])
        # 后台异步发 webhook, 不阻塞 caller
        asyncio.create_task(_maybe_webhook(source, severity, kind, message, robot_id, context))
    except Exception as e:
        logger.debug("[Alerts] record_alert 内部异常: %s", e)


async def _maybe_webhook(
    source: str, severity: str, kind: str, message: str,
    robot_id: str, context: dict,
) -> None:
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='alerts_webhook_url'"
            )
        finally:
            await db.close()
        if not rows or not rows[0]["value"]:
            return
        url = rows[0]["value"].strip()
        if not url or not url.startswith(("http://", "https://")):
            return
        import aiohttp
        payload = {
            "source": source, "severity": severity, "kind": kind,
            "message": message, "robot_id": robot_id,
            "context": context, "ts": time.time(),
        }
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5),
        ) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status >= 400:
                    logger.debug("[Alerts] webhook 返回 %d", resp.status)
    except Exception as e:
        logger.debug("[Alerts] webhook 发送失败: %s", e)


async def list_recent_alerts(
    limit: int = 50, only_unresolved: bool = True,
) -> list[dict]:
    """查询最近告警(供 admin 接口/面板使用)."""
    try:
        await _ensure_table()
        db = await get_db()
        try:
            where = "WHERE resolved=0" if only_unresolved else ""
            rows = await db.execute_fetchall(
                f"SELECT * FROM system_alerts {where} ORDER BY id DESC LIMIT ?",
                (int(limit),),
            )
            return [dict(r) for r in rows]
        finally:
            await db.close()
    except Exception as e:
        logger.debug("[Alerts] list 失败: %s", e)
        return []


async def resolve_alert(alert_id: int) -> bool:
    """标记告警已解决."""
    try:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE system_alerts SET resolved=1, resolved_at=datetime('now','localtime') WHERE id=?",
                (int(alert_id),),
            )
            await db.commit()
            return True
        finally:
            await db.close()
    except Exception as e:
        logger.debug("[Alerts] resolve 失败: %s", e)
        return False
