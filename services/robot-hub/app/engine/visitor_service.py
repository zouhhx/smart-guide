"""
访客服务 — 访客身份管理、人脸注册、绑定管理、欢迎策略

中枢负责所有业务判断，机器人侧只上报事实。
"""

import asyncio
import json
import logging
import time
import uuid

from app.database import get_db

logger = logging.getLogger("hub.engine.visitor_service")


# ============================================================
# 审计日志
# ============================================================
async def log_action(log_type: str, source: str, action: str, detail: str = "",
                      target: str = "", status: str = "ok", duration_ms: int = 0):
    """写入 execution_logs 审计日志, 失败不抛异常"""
    try:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO execution_logs
                   (log_type, source, target, action, detail, status, duration_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))""",
                (log_type, source, target, action, detail, status, duration_ms),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        logger.warning("[Visitor] 审计日志写入失败: %s", e)


# ============================================================
# 人脸注册
# ============================================================
async def create_register_task(robot_id: str, name: str, created_by: str = "admin") -> dict:
    """创建人脸注册任务，返回 task dict with request_id.

    协议要求 requestId 为 int, 与机器人侧对齐; 机器人解析 int32, 所以对
    ms 时间戳取模缩到 9 位 (<=10^9), 保证落在 int32 范围内, 且 ~11.5 天
    周期远大于一次注册往返. DB 列仍是 TEXT 存字符串形式.
    """
    import time as _t
    request_id_int = int(_t.time() * 1000) % 1_000_000_000
    request_id = str(request_id_int)
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO face_register_tasks (request_id, robot_id, visitor_name, status, created_by)
               VALUES (?, ?, ?, 'pending', ?)""",
            (request_id, robot_id, name, created_by),
        )
        await db.commit()
        row = await db.execute_fetchall(
            "SELECT * FROM face_register_tasks WHERE request_id=?", (request_id,)
        )
        task = dict(row[0]) if row else {"request_id": request_id}
    finally:
        await db.close()
    # 对外返回 int 类型, 方便调用方直接塞进 JSON 协议字段
    task["request_id"] = request_id_int
    await log_action(
        "face_register", created_by, "create_task",
        detail=f"name={name} requestId={request_id_int}",
        target=robot_id, status="ok",
    )
    logger.info("[Visitor] 创建注册任务: robot=%s name=%s requestId=%d", robot_id, name, request_id_int)
    return task


async def update_register_task(request_id, status: str, **kwargs) -> dict:
    """更新注册任务状态。kwargs: robot_user_id, visitor_id, error_code, error_message.

    request_id 可以是 int (新协议) 或 str (历史记录兼容), 统一 str 化后匹配 DB."""
    request_id_str = str(request_id)
    db = await get_db()
    try:
        updates = ["status=?", "updated_at=datetime('now','localtime')"]
        params = [status]
        if status in ("success", "failed", "timeout"):
            updates.append("finished_at=datetime('now','localtime')")
        for k in ("robot_user_id", "visitor_id", "error_code", "error_message"):
            if k in kwargs and kwargs[k] is not None:
                updates.append(f"{k}=?")
                params.append(kwargs[k])
        params.append(request_id_str)
        await db.execute(
            f"UPDATE face_register_tasks SET {', '.join(updates)} WHERE request_id=?",
            params,
        )
        await db.commit()
        row = await db.execute_fetchall(
            "SELECT * FROM face_register_tasks WHERE request_id=?", (request_id_str,)
        )
        task = dict(row[0]) if row else {}
    finally:
        await db.close()
    # 状态变更审计日志
    robot_id = task.get("robot_id", "")
    detail_parts = [f"requestId={request_id}", f"status={status}"]
    if kwargs.get("robot_user_id"):
        detail_parts.append(f"userId={kwargs['robot_user_id']}")
    if kwargs.get("error_code"):
        detail_parts.append(f"error={kwargs['error_code']}")
    if kwargs.get("error_message"):
        detail_parts.append(f"msg={kwargs['error_message']}")
    log_status = "ok" if status in ("success", "sent", "started") else ("error" if status in ("failed", "timeout") else "ok")
    await log_action(
        "face_register", "hub", f"update_{status}",
        detail=" ".join(detail_parts), target=robot_id, status=log_status,
    )
    return task


async def get_register_task(request_id) -> dict | None:
    """request_id 可 int 或 str (API 路径参数是 str, 内部生成是 int), 统一 str 化匹配."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM face_register_tasks WHERE request_id=?", (str(request_id),)
        )
        return dict(rows[0]) if rows else None
    finally:
        await db.close()


_sweep_task = None


async def start_register_sweeper(interval_seconds: int = 60, task_timeout_seconds: int = 120):
    """启动后台定时清扫超时注册任务。app 启动时调用一次。"""
    global _sweep_task
    if _sweep_task and not _sweep_task.done():
        return
    async def _loop():
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await sweep_register_timeouts(timeout_seconds=task_timeout_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[Visitor] 超时扫描异常: %s", e)
    _sweep_task = asyncio.create_task(_loop())
    logger.info("[Visitor] 注册任务超时扫描已启动(间隔%ds, 超时阈值%ds)", interval_seconds, task_timeout_seconds)


async def stop_register_sweeper():
    global _sweep_task
    if _sweep_task:
        _sweep_task.cancel()
        try:
            await _sweep_task
        except (asyncio.CancelledError, Exception):
            pass
        _sweep_task = None


async def sweep_register_timeouts(timeout_seconds: int = 120) -> int:
    """扫描超时的注册任务(未完成 + 超过 timeout_seconds 未更新), 标记为 timeout。

    策略:
    - pending (还没发出): 超过 30 秒 → timeout
    - sent / started (已发送, 机器人没回传): 超过 timeout_seconds (默认 120) → timeout

    返回被标记为 timeout 的任务数量。
    """
    pending_timeout = min(30, timeout_seconds)
    db = await get_db()
    stale_rows = []
    try:
        stale_rows = await db.execute_fetchall(
            """SELECT request_id, robot_id, visitor_name, status,
                      (strftime('%s','now','localtime') - strftime('%s', updated_at)) as age_sec
               FROM face_register_tasks
               WHERE status IN ('pending','sent','started')
                 AND (
                   (status='pending' AND (strftime('%s','now','localtime') - strftime('%s', updated_at)) > ?)
                   OR
                   (status IN ('sent','started') AND (strftime('%s','now','localtime') - strftime('%s', updated_at)) > ?)
                 )""",
            (pending_timeout, timeout_seconds),
        )
    finally:
        await db.close()

    if not stale_rows:
        return 0
    # 使用 update_register_task 走正常更新路径(带日志)
    n = 0
    for r in stale_rows:
        r = dict(r)
        await update_register_task(
            r["request_id"], "timeout",
            error_code="timeout",
            error_message=f"超时 {int(r['age_sec'])}s 未完成(原状态={r['status']})",
        )
        n += 1
    logger.info("[Visitor] 清扫超时注册任务: %d 个", n)
    return n


# ============================================================
# 访客档案
# ============================================================
async def upsert_visitor(robot_user_id: str, name: str, source: str = "face_register", robot_id: str = "") -> dict:
    """创建或更新访客档案，返回 visitor dict"""
    # 防御空 userId: 机器人 SDK 在"未识别 / 陌生人"分支可能返回 ""，
    # 直接 upsert 会污染 robot_user_id='' 的行 (UNIQUE)，导致后续所有人都被
    # get_visitor_by_user_id('') 命中同一条记录。
    if not robot_user_id:
        return {}
    is_new = False
    db = await get_db()
    try:
        existing = await db.execute_fetchall(
            "SELECT * FROM visitor_profiles WHERE robot_user_id=?", (robot_user_id,)
        )
        if existing:
            visitor = dict(existing[0])
            # 30 分钟去重: 同一会话内重复识别不灌水 visit_count
            await db.execute(
                """UPDATE visitor_profiles SET
                   visit_count = visit_count + CASE
                       WHEN last_seen_at IS NULL OR
                            (strftime('%s','now','localtime') - strftime('%s', last_seen_at)) > 1800
                       THEN 1 ELSE 0 END,
                   last_seen_at = datetime('now','localtime'),
                   last_robot_id = ?,
                   updated_at = datetime('now','localtime')
                   WHERE id=?""",
                (robot_id, visitor["id"]),
            )
            await db.commit()
            # 重新读取以拿到准确的 visit_count
            rows = await db.execute_fetchall(
                "SELECT * FROM visitor_profiles WHERE id=?", (visitor["id"],)
            )
            if rows:
                visitor = dict(rows[0])
        else:
            # INSERT OR IGNORE: 防止两条 evt_face_recognized 几乎同时落到 select-then-insert
            # 之间, 触发 UNIQUE(robot_user_id) 冲突。冲突时直接重读拿到先到的那行。
            await db.execute(
                """INSERT OR IGNORE INTO visitor_profiles
                   (robot_user_id, name, display_name, source, visit_count, last_robot_id)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (robot_user_id, name, name, source, robot_id),
            )
            await db.commit()
            rows = await db.execute_fetchall(
                "SELECT * FROM visitor_profiles WHERE robot_user_id=?", (robot_user_id,)
            )
            visitor = dict(rows[0]) if rows else {}
            # is_new 仅在拿到的 first_seen_at 跟 now 极近时才算真新建; 这里宽松地用插入路径
            # 且 visit_count==1 标记新建, 反正 log_action 仅写一行差别不大。
            is_new = bool(visitor) and visitor.get("visit_count", 0) == 1 and visitor.get("source") == source
    finally:
        await db.close()
    action = "create_visitor" if is_new else "update_visit_count"
    await log_action(
        "visitor", "hub", action,
        detail=f"visitorId={visitor.get('id')} name={name} userId={robot_user_id} source={source} visitCount={visitor.get('visit_count')}",
        target=robot_id,
    )
    return visitor


async def get_visitor_by_user_id(robot_user_id: str) -> dict | None:
    if not robot_user_id:
        return None
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM visitor_profiles WHERE robot_user_id=?", (robot_user_id,)
        )
        return dict(rows[0]) if rows else None
    finally:
        await db.close()


async def get_visitor(visitor_id: int) -> dict | None:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM visitor_profiles WHERE id=?", (visitor_id,)
        )
        return dict(rows[0]) if rows else None
    finally:
        await db.close()


async def update_visitor(visitor_id: int, **fields) -> dict:
    """更新访客字段"""
    allowed = {"display_name", "category_id", "profile_summary", "notes", "status", "avatar_url"}
    updates = ["updated_at=datetime('now','localtime')"]
    params = []
    changed_fields = {}
    for k, v in fields.items():
        if k in allowed:
            updates.append(f"{k}=?")
            params.append(v)
            changed_fields[k] = v
    if len(params) == 0:
        return {}
    params.append(visitor_id)
    db = await get_db()
    try:
        await db.execute(
            f"UPDATE visitor_profiles SET {', '.join(updates)} WHERE id=?", params
        )
        await db.commit()
    finally:
        await db.close()
    visitor = await get_visitor(visitor_id)
    await log_action(
        "visitor", "admin", "update_visitor",
        detail=f"visitorId={visitor_id} fields={json.dumps(changed_fields, ensure_ascii=False)}",
    )
    return visitor


async def list_visitors(category_id: int = None, tag_id: int = None, search: str = "",
                         limit: int = 50, offset: int = 0) -> dict:
    """列出访客，支持搜索/分类/标签筛选"""
    db = await get_db()
    try:
        where_parts = ["vp.status != 'deleted'"]
        params = []
        if category_id:
            where_parts.append("vp.category_id = ?")
            params.append(category_id)
        if search:
            where_parts.append("(vp.name LIKE ? OR vp.display_name LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if tag_id:
            where_parts.append("vp.id IN (SELECT visitor_id FROM visitor_profile_tags WHERE tag_id=?)")
            params.append(tag_id)
        where = " AND ".join(where_parts)

        count_row = await db.execute_fetchall(
            f"SELECT COUNT(*) as cnt FROM visitor_profiles vp WHERE {where}", params
        )
        total = count_row[0]["cnt"]

        params.extend([limit, offset])
        rows = await db.execute_fetchall(
            f"""SELECT vp.*,
                       vc.name as category_name, vc.color as category_color
                FROM visitor_profiles vp
                LEFT JOIN visitor_categories vc ON vc.id = vp.category_id
                WHERE {where}
                ORDER BY vp.last_seen_at DESC
                LIMIT ? OFFSET ?""",
            params,
        )
        visitors = [dict(r) for r in rows]

        # attach tags for each visitor
        for v in visitors:
            tag_rows = await db.execute_fetchall(
                """SELECT vt.id, vt.name, vt.color, vpt.score
                   FROM visitor_profile_tags vpt
                   JOIN visitor_tags vt ON vt.id = vpt.tag_id
                   WHERE vpt.visitor_id = ?""",
                (v["id"],),
            )
            v["tags"] = [dict(t) for t in tag_rows]

        return {"data": visitors, "total": total}
    finally:
        await db.close()


async def get_visitor_detail(visitor_id: int) -> dict | None:
    """获取访客详情（含标签、最近记忆、分类）"""
    visitor = await get_visitor(visitor_id)
    if not visitor:
        return None

    db = await get_db()
    try:
        # tags
        tag_rows = await db.execute_fetchall(
            """SELECT vt.id, vt.name, vt.color, vt.type, vpt.score, vpt.source
               FROM visitor_profile_tags vpt
               JOIN visitor_tags vt ON vt.id = vpt.tag_id
               WHERE vpt.visitor_id = ?""",
            (visitor_id,),
        )
        visitor["tags"] = [dict(t) for t in tag_rows]

        # category
        if visitor.get("category_id"):
            cat_rows = await db.execute_fetchall(
                "SELECT * FROM visitor_categories WHERE id=?", (visitor["category_id"],)
            )
            visitor["category"] = dict(cat_rows[0]) if cat_rows else None
        else:
            visitor["category"] = None

        # recent memories
        mem_rows = await db.execute_fetchall(
            """SELECT * FROM visitor_memories
               WHERE visitor_id = ?
               ORDER BY created_at DESC LIMIT 20""",
            (visitor_id,),
        )
        visitor["recent_memories"] = [dict(m) for m in mem_rows]

        # register tasks
        if visitor.get("robot_user_id"):
            reg_rows = await db.execute_fetchall(
                """SELECT * FROM face_register_tasks
                   WHERE robot_user_id = ? OR visitor_id = ?
                   ORDER BY created_at DESC LIMIT 5""",
                (visitor["robot_user_id"], visitor_id),
            )
            visitor["register_tasks"] = [dict(r) for r in reg_rows]
        else:
            visitor["register_tasks"] = []

        return visitor
    finally:
        await db.close()


# ============================================================
# 访客绑定
# ============================================================
async def bind_visitor_to_robot(robot_id: str, visitor_id: int,
                                  source: str = "face", confidence: float = 0.0,
                                  ttl_seconds: int = 300) -> dict:
    """绑定访客到机器人（活跃会话）"""
    expires_at_expr = f"datetime('now','localtime','+{ttl_seconds} seconds')"
    db = await get_db()
    try:
        await db.execute(
            f"""INSERT INTO visitor_robot_bindings
                (robot_id, visitor_id, status, recognized_at, last_interaction_at,
                 expires_at, binding_source, binding_confidence)
                VALUES (?, ?, 'active', datetime('now','localtime'), datetime('now','localtime'),
                        {expires_at_expr}, ?, ?)
                ON CONFLICT(robot_id, visitor_id) DO UPDATE SET
                    status='active',
                    recognized_at=datetime('now','localtime'),
                    last_interaction_at=datetime('now','localtime'),
                    expires_at={expires_at_expr},
                    binding_source=?,
                    binding_confidence=?""",
            (robot_id, visitor_id, source, confidence, source, confidence),
        )
        await db.commit()
        rows = await db.execute_fetchall(
            "SELECT * FROM visitor_robot_bindings WHERE robot_id=? AND visitor_id=?",
            (robot_id, visitor_id),
        )
        binding = dict(rows[0]) if rows else {}
    finally:
        await db.close()
    await log_action(
        "visitor", "hub", "bind",
        detail=f"visitorId={visitor_id} source={source} confidence={confidence:.2f} ttl={ttl_seconds}s",
        target=robot_id,
    )
    return binding


async def get_active_visitor_for_robot(robot_id: str) -> dict | None:
    """获取机器人当前活跃访客（未过期的最高置信度绑定）"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT vp.*, vrb.binding_confidence, vrb.recognized_at, vrb.expires_at
               FROM visitor_robot_bindings vrb
               JOIN visitor_profiles vp ON vp.id = vrb.visitor_id
               WHERE vrb.robot_id = ?
                 AND vrb.status = 'active'
                 AND vrb.expires_at > datetime('now','localtime')
               ORDER BY vrb.binding_confidence DESC, vrb.recognized_at DESC
               LIMIT 1""",
            (robot_id,),
        )
        return dict(rows[0]) if rows else None
    finally:
        await db.close()


async def refresh_binding(robot_id: str, visitor_id: int, ttl_seconds: int = 300):
    """刷新绑定有效期（每次互动时调用）"""
    expires_at_expr = f"datetime('now','localtime','+{ttl_seconds} seconds')"
    db = await get_db()
    try:
        await db.execute(
            f"""UPDATE visitor_robot_bindings SET
                last_interaction_at = datetime('now','localtime'),
                expires_at = {expires_at_expr}
                WHERE robot_id=? AND visitor_id=? AND status='active'""",
            (robot_id, visitor_id),
        )
        await db.commit()
    finally:
        await db.close()


async def clear_bindings_for_robot(robot_id: str):
    """清除某机器人的所有活跃绑定（人离开时调用）"""
    db = await get_db()
    cleared = 0
    try:
        # 先查出要清理的数量用于日志
        cnt_rows = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM visitor_robot_bindings WHERE robot_id=? AND status='active'",
            (robot_id,),
        )
        cleared = cnt_rows[0]["cnt"] if cnt_rows else 0
        await db.execute(
            """UPDATE visitor_robot_bindings SET status='cleared'
               WHERE robot_id=? AND status='active'""",
            (robot_id,),
        )
        await db.commit()
    finally:
        await db.close()
    if cleared > 0:
        await log_action(
            "visitor", "hub", "clear_bindings",
            detail=f"cleared={cleared}", target=robot_id,
        )


async def expire_stale_bindings():
    """清理过期绑定（定期调用或事件触发时调用）"""
    db = await get_db()
    try:
        await db.execute(
            """UPDATE visitor_robot_bindings SET status='expired'
               WHERE status='active' AND expires_at < datetime('now','localtime')"""
        )
        await db.commit()
    finally:
        await db.close()


# ============================================================
# 人数状态 & 欢迎逻辑
# ============================================================
# 内存状态：每个机器人的视野人数 + 上次欢迎时间
_robot_person_state: dict = {}  # robot_id -> {"count": int, "last_welcome_at": float, "locked_visitor_id": int, "lock_until": float}


def get_person_state(robot_id: str) -> dict:
    if robot_id not in _robot_person_state:
        _robot_person_state[robot_id] = {
            "count": 0,
            "last_welcome_at": 0.0,
            "locked_visitor_id": None,
            "lock_until": 0.0,
            "last_face_welcome_at": 0.0,
            "last_face_welcome_key": "",
            "reception_last_seen_at": 0.0,
            "reception_session_key": "",
            "reception_session_greeted": False,
            "reception_followup_until": 0.0,
            "reception_followup_key": "",
            "reception_pause_until": 0.0,
        }
    return _robot_person_state[robot_id]


async def handle_person_count(robot_id: str, person_count: int) -> dict:
    """处理人数变化事件，返回 action dict

    Returns: {"action": "welcome"|"clear"|"none", "visitor": dict|None, "reason": str}
    """
    state = get_person_state(robot_id)
    old_count = state["count"]
    state["count"] = person_count

    now = time.time()
    result = {"action": "none", "visitor": None, "reason": ""}

    # 有人 → 无人: 清理绑定
    if old_count > 0 and person_count == 0:
        await clear_bindings_for_robot(robot_id)
        state["locked_visitor_id"] = None
        state["lock_until"] = 0.0
        state["last_face_welcome_at"] = 0.0
        state["last_face_welcome_key"] = ""
        state["reception_last_seen_at"] = 0.0
        state["reception_session_key"] = ""
        state["reception_session_greeted"] = False
        state["reception_followup_until"] = 0.0
        state["reception_followup_key"] = ""
        state["reception_pause_until"] = 0.0
        result = {"action": "clear", "visitor": None, "reason": "no_person"}
        logger.info("[Visitor] robot=%s 无人, 清理绑定", robot_id)
        await log_action(
            "visitor", "hub", "person_leave",
            detail=f"from={old_count} to=0 → clear bindings", target=robot_id,
        )
        return result

    # 无人 → 有人: 欢迎候选
    if old_count == 0 and person_count > 0:
        # 检查冷却时间
        db = await get_db()
        try:
            cfg = await db.execute_fetchall(
                "SELECT key, value FROM system_config WHERE key IN ('visitor_welcome_enabled','visitor_welcome_cooldown','visitor_welcome_mode')"
            )
            config = {r["key"]: r["value"] for r in cfg}
        finally:
            await db.close()

        welcome_enabled = config.get("visitor_welcome_enabled", "1") == "1"
        cooldown = int(config.get("visitor_welcome_cooldown", "300"))
        welcome_mode = config.get("visitor_welcome_mode", "recognized")

        if not welcome_enabled:
            result["reason"] = "welcome_disabled"
        else:
            # 人数事件只作为“有人出现/离开”的状态信号，不直接播通用欢迎。
            # 现场人脸识别通常 0.5~2s 内随后到达；若这里先说“您好”，已识别人脸的姓名欢迎会被短防抖压住，
            # 用户就会感知成“录入了也只会说您好”。统一交给 evt_face_recognized 决定姓名/陌生人欢迎。
            result["reason"] = f"waiting_for_face_recognition(mode={welcome_mode})"

        await log_action(
            "visitor", "hub", "person_appear",
            detail=f"count={person_count} mode={welcome_mode} action={result['action']} reason={result['reason']}",
            target=robot_id,
        )
        return result

    # 人数变化但不涉及 0-有人 边界, 只记录
    if old_count != person_count:
        await log_action(
            "visitor", "hub", "person_count_change",
            detail=f"from={old_count} to={person_count}", target=robot_id,
        )

    return result


async def get_welcome_text(visitor: dict | None, force_returning: bool = False) -> str:
    """根据访客信息生成欢迎词

    当前产品规则:
    - 已录入/已识别人脸: "某某，有什么需要帮助的吗？"
    - 未录入/陌生人: "您好，有什么需要帮助的吗？"

    force_returning 保留兼容旧调用；新规则不再区分 VIP/回头客模板，避免欢迎语漂移。
    """
    if visitor is None:
        return "您好，有什么需要帮助的吗？"

    name = (visitor.get("display_name") or visitor.get("name") or "").strip()
    if name:
        return f"{name}，有什么需要帮助的吗？"
    return "您好，有什么需要帮助的吗？"


# ============================================================
# 分类 & 标签管理
# ============================================================
async def list_categories() -> list:
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM visitor_categories ORDER BY sort_order, id")
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def list_tags(tag_type: str = None) -> list:
    db = await get_db()
    try:
        if tag_type:
            rows = await db.execute_fetchall(
                "SELECT * FROM visitor_tags WHERE type=? ORDER BY sort_order, id", (tag_type,)
            )
        else:
            rows = await db.execute_fetchall("SELECT * FROM visitor_tags ORDER BY sort_order, id")
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def set_visitor_tags(visitor_id: int, tag_ids: list[int], source: str = "manual"):
    """设置访客标签（全量替换）"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM visitor_profile_tags WHERE visitor_id=? AND source=?",
                         (visitor_id, source))
        for tid in tag_ids:
            await db.execute(
                """INSERT OR IGNORE INTO visitor_profile_tags (visitor_id, tag_id, source)
                   VALUES (?, ?, ?)""",
                (visitor_id, tid, source),
            )
        await db.commit()
    finally:
        await db.close()
    await log_action(
        "visitor", "admin", "set_tags",
        detail=f"visitorId={visitor_id} tagIds={tag_ids} source={source}",
    )


async def update_visitor_photo(robot_user_id: str, photo_url: str,
                                name: str = "", robot_id: str = ""):
    """更新访客头像。

    name 非空且 visitor 不存在时, 走 upsert_visitor 兜底建档(避免 evt_face_photo
    早于 evt_face_register_success 到达时数据被静默吞掉)。
    name 非空且 visitor 已存在但当前 name 为空时, 顺手补上姓名。
    """
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name FROM visitor_profiles WHERE robot_user_id=?",
            (robot_user_id,),
        )
        if not rows:
            # visitor 不存在 → 走 upsert 兜底 (DB 关掉后再调, 避免锁)
            need_upsert = True
            existing_name = ""
        else:
            need_upsert = False
            existing_name = rows[0]["name"] or ""
            sets = ["avatar_url=?", "last_face_photo_at=datetime('now','localtime')",
                    "updated_at=datetime('now','localtime')"]
            params: list = [photo_url]
            if name and not existing_name:
                sets.append("name=?")
                params.append(name)
            params.append(robot_user_id)
            await db.execute(
                f"UPDATE visitor_profiles SET {', '.join(sets)} WHERE robot_user_id=?",
                params,
            )
            await db.commit()
    finally:
        await db.close()

    if need_upsert and name:
        await upsert_visitor(robot_user_id, name, source="face_photo", robot_id=robot_id)
        # upsert 后再补一次 photo URL (upsert 不带 photo)
        db2 = await get_db()
        try:
            await db2.execute(
                """UPDATE visitor_profiles SET
                   avatar_url=?, last_face_photo_at=datetime('now','localtime'),
                   updated_at=datetime('now','localtime')
                   WHERE robot_user_id=?""",
                (photo_url, robot_user_id),
            )
            await db2.commit()
        finally:
            await db2.close()

    await log_action(
        "visitor", "hub", "update_photo",
        detail=f"userId={robot_user_id} name={name} url={photo_url[:80]}",
    )
