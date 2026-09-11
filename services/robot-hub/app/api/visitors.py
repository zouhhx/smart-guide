"""
访客管理 API — 人脸注册、访客档案、标签分类、记忆、欢迎语配置
"""

import time
from fastapi import APIRouter, Query, Body
from app.database import get_db

router = APIRouter()


# ============================================================
# 人脸注册
# ============================================================
@router.post("/visitors/register-face")
async def register_face(body: dict = Body(...)):
    """后台发起人脸注册。兼容 robotId (驼峰) / robot_id (下划线)。
    约束: 姓名不允许重复（与 visitor_profiles.name / display_name 去空白后精确匹配）。
    """
    robot_id = body.get("robotId") or body.get("robot_id") or ""
    name = (body.get("name") or "").strip()
    if not robot_id or not name:
        from app.engine.visitor_service import log_action
        await log_action("face_register", "admin", "api_rejected",
                         detail=f"缺少必填参数 robotId={robot_id!r} name={name!r}",
                         status="error")
        return {"status": "error", "message": "robotId 和 name 必填", "code": 422}

    # 姓名去重检查: 已存在同名访客则拒绝
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT id, name, display_name, robot_user_id FROM visitor_profiles
               WHERE TRIM(name)=? OR TRIM(display_name)=?""",
            (name, name),
        )
    finally:
        await db.close()
    if rows:
        existing = dict(rows[0])
        from app.engine.visitor_service import log_action
        await log_action("face_register", "admin", "duplicate_name",
                         detail=f"name={name} existingId={existing['id']} userId={existing.get('robot_user_id','')}",
                         status="error", target=robot_id)
        return {
            "status": "error",
            "code": 409,
            "message": f"姓名「{name}」已存在，请换一个名字或先到访客列表修改已有访客。",
            "existing_visitor": {
                "id": existing["id"],
                "name": existing["name"],
                "display_name": existing.get("display_name", ""),
            },
        }

    # 同名的历史注册任务（未完成的）也拒绝，防止连点
    db = await get_db()
    try:
        pending_rows = await db.execute_fetchall(
            """SELECT request_id, status, robot_id, created_at FROM face_register_tasks
               WHERE TRIM(visitor_name)=? AND status IN ('pending','sent','started')
               ORDER BY created_at DESC LIMIT 1""",
            (name,),
        )
    finally:
        await db.close()
    if pending_rows:
        pending = dict(pending_rows[0])
        from app.engine.visitor_service import log_action
        await log_action("face_register", "admin", "duplicate_pending",
                         detail=f"name={name} existingRequestId={pending['request_id']} status={pending['status']}",
                         status="error", target=robot_id)
        return {
            "status": "error",
            "code": 409,
            "message": f"姓名「{name}」已有一个{pending['status']}状态的注册任务，请等待其完成或在任务列表查看。",
            "pending_task": pending,
        }

    from app.engine.visitor_service import create_register_task
    task = await create_register_task(robot_id, name)

    # Send command to robot via existing TCP connection
    from app.tcp.server import get_robot
    robot = get_robot(robot_id)
    if not robot:
        from app.engine.visitor_service import update_register_task
        await update_register_task(task["request_id"], "failed",
                                   error_code="robot_offline",
                                   error_message="机器人不在线")
        return {"status": "error", "message": "机器人不在线", "task": task}

    ok = await robot.send({
        "type": "cmd_face_register_start",
        "data": {
            "robotId": robot_id,
            "requestId": int(task["request_id"]),
            "name": name,
            "ts": int(time.time() * 1000),
        }
    })

    if ok:
        from app.engine.visitor_service import update_register_task
        await update_register_task(task["request_id"], "sent")
        return {"status": "ok", "task": task}
    else:
        from app.engine.visitor_service import update_register_task
        await update_register_task(task["request_id"], "failed",
                                   error_code="send_failed",
                                   error_message="命令发送失败")
        return {"status": "error", "message": "命令发送失败", "task": task}


# ============================================================
# 注册任务
# ============================================================
@router.get("/visitors/register-tasks")
async def list_register_tasks(status: str = Query(None), limit: int = Query(20)):
    """列出注册任务。读取前先做一次超时扫描，避免列表卡在 pending/sent。"""
    try:
        from app.engine.visitor_service import sweep_register_timeouts
        await sweep_register_timeouts(timeout_seconds=120)
    except Exception:
        pass
    db = await get_db()
    try:
        where = "WHERE 1=1"
        params = []
        if status:
            where += " AND status=?"
            params.append(status)
        params.append(limit)
        rows = await db.execute_fetchall(
            f"SELECT * FROM face_register_tasks {where} ORDER BY created_at DESC LIMIT ?",
            params,
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/visitors/register-tasks/{request_id}")
async def get_register_task_api(request_id: str):
    from app.engine.visitor_service import get_register_task
    task = await get_register_task(request_id)
    if not task:
        return {"error": "任务不存在", "code": 404}
    return {"data": task}


# ============================================================
# 访客列表与详情
# ============================================================
@router.get("/visitors")
async def list_visitors_api(
    search: str = Query(""),
    category_id: int = Query(None),
    tag_id: int = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
):
    from app.engine.visitor_service import list_visitors
    result = await list_visitors(category_id=category_id, tag_id=tag_id,
                                 search=search, limit=limit, offset=offset)
    return result


@router.get("/visitors/active-bindings")
async def list_active_bindings():
    """列出所有机器人的当前活跃访客绑定 (必须在 /visitors/{visitor_id} 之前声明, 避免路由碰撞)"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT vrb.*, vp.name as visitor_name, vp.display_name, vp.avatar_url
               FROM visitor_robot_bindings vrb
               JOIN visitor_profiles vp ON vp.id = vrb.visitor_id
               WHERE vrb.status = 'active'
                 AND vrb.expires_at > datetime('now','localtime')
               ORDER BY vrb.recognized_at DESC"""
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/visitors/{visitor_id}")
async def get_visitor_api(visitor_id: int):
    from app.engine.visitor_service import get_visitor_detail
    visitor = await get_visitor_detail(visitor_id)
    if not visitor:
        return {"error": "访客不存在", "code": 404}
    return {"data": visitor}


@router.put("/visitors/{visitor_id}")
async def update_visitor_api(visitor_id: int, body: dict = Body(...)):
    from app.engine.visitor_service import update_visitor
    result = await update_visitor(visitor_id, **body)
    return {"status": "ok", "data": result}


# ============================================================
# 标签与分类
# ============================================================
@router.get("/visitors/meta/categories")
async def list_categories_api():
    from app.engine.visitor_service import list_categories
    return {"data": await list_categories()}


@router.post("/visitors/meta/categories")
async def create_category(name: str = Body(...), color: str = Body("#64748b"),
                          sort_order: int = Body(0), description: str = Body("")):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO visitor_categories (name, color, sort_order, description) VALUES (?, ?, ?, ?)",
            (name, color, sort_order, description),
        )
        await db.commit()
    finally:
        await db.close()
    from app.engine.visitor_service import log_action
    await log_action("visitor", "admin", "create_category",
                     detail=f"name={name} color={color}")
    return {"status": "ok"}


@router.get("/visitors/meta/tags")
async def list_tags_api(tag_type: str = Query(None)):
    from app.engine.visitor_service import list_tags
    return {"data": await list_tags(tag_type)}


@router.post("/visitors/meta/tags")
async def create_tag(name: str = Body(...), color: str = Body("#94a3b8"),
                     type: str = Body("custom"), sort_order: int = Body(0)):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO visitor_tags (name, color, type, sort_order) VALUES (?, ?, ?, ?)",
            (name, color, type, sort_order),
        )
        await db.commit()
    finally:
        await db.close()
    from app.engine.visitor_service import log_action
    await log_action("visitor", "admin", "create_tag",
                     detail=f"name={name} color={color} type={type}")
    return {"status": "ok"}


@router.put("/visitors/{visitor_id}/tags")
async def set_visitor_tags_api(visitor_id: int, body=Body(...)):
    """接受裸数组 [1,2,3] 或对象 {"tag_ids":[1,2,3]}"""
    if isinstance(body, list):
        tag_ids = body
    elif isinstance(body, dict):
        tag_ids = body.get("tag_ids") or []
    else:
        tag_ids = []
    tag_ids = [int(t) for t in tag_ids if t is not None]
    from app.engine.visitor_service import set_visitor_tags
    await set_visitor_tags(visitor_id, tag_ids)
    return {"status": "ok"}


# ============================================================
# 访客记忆
# ============================================================
@router.get("/visitors/{visitor_id}/memories")
async def list_visitor_memories(visitor_id: int, limit: int = Query(50), offset: int = Query(0)):
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM visitor_memories WHERE visitor_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (visitor_id, limit, offset),
        )
        count = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM visitor_memories WHERE visitor_id=?", (visitor_id,)
        )
        return {"data": [dict(r) for r in rows], "total": count[0]["cnt"]}
    finally:
        await db.close()


# ============================================================
# 欢迎语配置
# ============================================================
@router.get("/visitors/config/welcome")
async def get_welcome_config():
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT key, value FROM system_config WHERE key LIKE 'visitor_%'"
        )
        return {"data": {r["key"]: r["value"] for r in rows}}
    finally:
        await db.close()


@router.put("/visitors/config/welcome")
async def update_welcome_config(configs: dict = Body(...)):
    import json as _json
    db = await get_db()
    try:
        for key, value in configs.items():
            if key.startswith("visitor_"):
                await db.execute(
                    "INSERT INTO system_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
                    (key, str(value), str(value)),
                )
        await db.commit()
    finally:
        await db.close()
    from app.engine.visitor_service import log_action
    await log_action("visitor", "admin", "update_welcome_config",
                     detail=_json.dumps({k: v for k, v in configs.items() if k.startswith("visitor_")}, ensure_ascii=False))
    return {"status": "ok"}


# 活跃绑定状态接口已上移到 /visitors/{visitor_id} 之前, 避免路由碰撞
