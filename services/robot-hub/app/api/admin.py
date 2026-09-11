"""
Admin API — 运维面板接口

只暴露读 + 少量标记操作, 不开 DB 写权限.
当前:
- GET  /api/admin/alerts               列出告警(可筛 resolved/kind/since)
- POST /api/admin/alerts/{id}/resolve  标记告警已解决
- GET  /api/admin/plan_logs            auto_plan 生成日志, 支持 status/robot_id 筛
- GET  /api/admin/plan_logs/{id}       查单条 plan 详情(含 llm_raw + snapshot)
- GET  /api/admin/plan_stats           状态分布 / 平均 LLM 延迟 (dashboard 用)
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query
from app.database import get_db
from app.engine.alerts import list_recent_alerts, resolve_alert

logger = logging.getLogger("hub.api.admin")

router = APIRouter()


# ============================================================
# Alerts
# ============================================================
@router.get("/admin/alerts")
async def get_alerts(
    limit: int = Query(50, ge=1, le=500),
    only_unresolved: bool = Query(True),
    kind: str = Query(""),
    source: str = Query(""),
):
    """查询系统告警. 默认只拉未解决的近 50 条."""
    alerts = await list_recent_alerts(limit=limit, only_unresolved=only_unresolved)
    if kind:
        alerts = [a for a in alerts if a.get("kind") == kind]
    if source:
        alerts = [a for a in alerts if a.get("source") == source]
    # 反解 context_json 便于前端直接用
    for a in alerts:
        raw = a.get("context_json") or ""
        try:
            a["context"] = json.loads(raw) if raw else {}
        except Exception:
            a["context"] = {}
        a.pop("context_json", None)
    return {"total": len(alerts), "data": alerts}


@router.post("/admin/alerts/{alert_id}/resolve")
async def post_resolve_alert(alert_id: int):
    """标记告警已解决."""
    ok = await resolve_alert(alert_id)
    return {"status": "ok" if ok else "failed", "id": alert_id}


# ============================================================
# Auto Plan Logs
# ============================================================
@router.get("/admin/plan_logs")
async def get_plan_logs(
    limit: int = Query(50, ge=1, le=500),
    status: str = Query(""),
    robot_id: str = Query(""),
    days: int = Query(7, ge=1, le=90),
):
    """列 auto_plan_logs 摘要. 默认近 7 天 50 条. 不返回 llm_raw/snapshot/final 全文(太长)."""
    db = await get_db()
    try:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auto_plan_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
                robot_id TEXT, visitor_id INTEGER, user_text TEXT,
                snapshot_json TEXT, visitor_json TEXT, llm_raw TEXT,
                llm_elapsed_ms INTEGER, parsed_steps INTEGER,
                validator_status TEXT, final_plan_json TEXT,
                status TEXT, error TEXT
            )
        """)
        await db.commit()
        where = ["created_at >= datetime('now','-' || ? || ' days','localtime')"]
        params: list = [int(days)]
        if status:
            where.append("status = ?")
            params.append(status)
        if robot_id:
            where.append("robot_id = ?")
            params.append(robot_id)
        params.append(int(limit))
        sql = (
            "SELECT id, created_at, robot_id, visitor_id, user_text, "
            "       llm_elapsed_ms, parsed_steps, validator_status, status, error "
            "FROM auto_plan_logs WHERE " + " AND ".join(where) +
            " ORDER BY id DESC LIMIT ?"
        )
        rows = await db.execute_fetchall(sql, tuple(params))
        return {"total": len(rows), "data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/admin/plan_logs/{log_id}")
async def get_plan_log_detail(log_id: int):
    """查单条 plan 详情 — 含 llm_raw/snapshot_json/final_plan_json 全文."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM auto_plan_logs WHERE id=?", (int(log_id),),
        )
        if not rows:
            return {"error": "not found", "code": 404}
        r = dict(rows[0])
        # 反解 JSON 字段
        for k in ("snapshot_json", "visitor_json", "final_plan_json"):
            raw = r.get(k) or ""
            try:
                r[k.replace("_json", "")] = json.loads(raw) if raw else None
            except Exception:
                r[k.replace("_json", "")] = None
            r.pop(k, None)
        return {"data": r}
    finally:
        await db.close()


@router.get("/admin/plan_stats")
async def get_plan_stats(days: int = Query(7, ge=1, le=90)):
    """auto_plan 状态分布 + 延迟摘要, 给 dashboard 用."""
    db = await get_db()
    try:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auto_plan_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
                robot_id TEXT, visitor_id INTEGER, user_text TEXT,
                snapshot_json TEXT, visitor_json TEXT, llm_raw TEXT,
                llm_elapsed_ms INTEGER, parsed_steps INTEGER,
                validator_status TEXT, final_plan_json TEXT,
                status TEXT, error TEXT
            )
        """)
        await db.commit()
        base_where = ("created_at >= datetime('now','-' || ? || ' days','localtime')",)
        # 状态分布
        status_rows = await db.execute_fetchall(
            "SELECT status, COUNT(*) AS n FROM auto_plan_logs WHERE "
            + base_where[0] + " GROUP BY status ORDER BY n DESC",
            (int(days),),
        )
        # LLM 延迟
        lat_rows = await db.execute_fetchall(
            "SELECT COUNT(*) AS total, AVG(llm_elapsed_ms) AS avg_ms, "
            "MAX(llm_elapsed_ms) AS max_ms, MIN(llm_elapsed_ms) AS min_ms "
            "FROM auto_plan_logs WHERE " + base_where[0]
            + " AND llm_elapsed_ms > 0",
            (int(days),),
        )
        # validator 分布
        val_rows = await db.execute_fetchall(
            "SELECT validator_status, COUNT(*) AS n FROM auto_plan_logs WHERE "
            + base_where[0] + " GROUP BY validator_status",
            (int(days),),
        )
        lat = dict(lat_rows[0]) if lat_rows else {}
        return {
            "days": int(days),
            "status_distribution": [dict(r) for r in status_rows],
            "validator_distribution": [dict(r) for r in val_rows],
            "llm_latency_ms": {
                "count": int(lat.get("total") or 0),
                "avg": round(float(lat.get("avg_ms") or 0), 1),
                "min": int(lat.get("min_ms") or 0),
                "max": int(lat.get("max_ms") or 0),
            },
        }
    finally:
        await db.close()
