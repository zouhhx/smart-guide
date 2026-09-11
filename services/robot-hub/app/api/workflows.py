"""
流程管理 API — 参观脚本 CRUD、定时任务管理
"""

import json
import asyncio
from fastapi import APIRouter, Body, Query
from app.database import get_db
from app.engine.flow_executor import start_flow, stop_flow, get_all_active_flows
from app.tcp.server import get_robot

router = APIRouter()


# ============================================================
# 参观流程脚本
# ============================================================
@router.get("/workflows")
async def list_workflows():
    """获取所有参观流程"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT ts.id, ts.name, ts.trigger_word, ts.description,
                      ts.special_id, s.name as special_name,
                      ts.enabled, ts.created_at, ts.updated_at,
                      (SELECT COUNT(*) FROM tour_script_steps WHERE script_id=ts.id) as step_count
               FROM tour_scripts ts
               LEFT JOIN specials s ON s.id = ts.special_id
               ORDER BY ts.id"""
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: int):
    """获取单个流程详情（含步骤）"""
    db = await get_db()
    try:
        wf = await db.execute_fetchall(
            """SELECT ts.id, ts.name, ts.trigger_word, ts.description,
                      ts.special_id, s.name as special_name,
                      ts.enabled
               FROM tour_scripts ts
               LEFT JOIN specials s ON s.id = ts.special_id
               WHERE ts.id=?""",
            (workflow_id,),
        )
        if not wf:
            return {"error": "流程不存在", "code": 404}

        steps = await db.execute_fetchall(
            """SELECT id, step_order, step_type, config_json,
                      wait_type, wait_timeout, description
               FROM tour_script_steps
               WHERE script_id=?
               ORDER BY step_order""",
            (workflow_id,),
        )

        result = dict(wf[0])
        result["steps"] = [dict(s) for s in steps]
        return {"data": result}
    finally:
        await db.close()


@router.post("/workflows")
async def create_workflow(
    name: str = Body(...),
    trigger_word: str = Body(...),
    description: str = Body(""),
    special_id: int = Body(None),
):
    """创建参观流程"""
    db = await get_db()
    try:
        # 检查触发词唯一性
        existing = await db.execute_fetchall(
            "SELECT id FROM tour_scripts WHERE trigger_word=?", (trigger_word,)
        )
        if existing:
            return {"error": f"触发词 '{trigger_word}' 已被使用", "code": 409}

        cursor = await db.execute(
            """INSERT INTO tour_scripts (name, trigger_word, description, special_id)
               VALUES (?, ?, ?, ?)""",
            (name, trigger_word, description, special_id),
        )
        await db.commit()
        return {"status": "ok", "data": {"id": cursor.lastrowid}}
    finally:
        await db.close()


@router.put("/workflows/{workflow_id}")
async def update_workflow(
    workflow_id: int,
    name: str = Body(None),
    trigger_word: str = Body(None),
    description: str = Body(None),
    special_id: int = Body(None),
    enabled: int = Body(None),
):
    """更新流程基本信息"""
    db = await get_db()
    try:
        updates = []
        params = []
        if name is not None:
            updates.append("name=?")
            params.append(name)
        if trigger_word is not None:
            # 检查唯一性
            existing = await db.execute_fetchall(
                "SELECT id FROM tour_scripts WHERE trigger_word=? AND id!=?",
                (trigger_word, workflow_id),
            )
            if existing:
                return {"error": f"触发词 '{trigger_word}' 已被使用", "code": 409}
            updates.append("trigger_word=?")
            params.append(trigger_word)
        if description is not None:
            updates.append("description=?")
            params.append(description)
        if special_id is not None:
            updates.append("special_id=?")
            params.append(special_id)
        if enabled is not None:
            updates.append("enabled=?")
            params.append(enabled)

        if not updates:
            return {"error": "无更新内容", "code": 400}

        updates.append("updated_at=datetime('now','localtime')")
        params.append(workflow_id)

        await db.execute(
            f"UPDATE tour_scripts SET {', '.join(updates)} WHERE id=?", params
        )
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@router.put("/workflows/{workflow_id}/steps")
async def update_workflow_steps(
    workflow_id: int,
    steps: list = Body(...),
):
    """整体更新流程步骤（替换全部步骤）"""
    db = await get_db()
    try:
        # 删除旧步骤
        await db.execute(
            "DELETE FROM tour_script_steps WHERE script_id=?", (workflow_id,)
        )

        # 插入新步骤
        for i, step in enumerate(steps):
            config = step.get("config", step.get("config_json", {}))
            if isinstance(config, dict):
                config = json.dumps(config, ensure_ascii=False)

            await db.execute(
                """INSERT INTO tour_script_steps
                   (script_id, step_order, step_type, config_json,
                    wait_type, wait_timeout, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    workflow_id,
                    i + 1,
                    step.get("step_type", ""),
                    config,
                    step.get("wait_type", "callback"),
                    step.get("wait_timeout", 30),
                    step.get("description", ""),
                ),
            )

        await db.commit()
        return {"status": "ok", "message": f"已保存 {len(steps)} 个步骤"}
    finally:
        await db.close()


@router.post("/workflows/{workflow_id}/run")
async def run_workflow(workflow_id: int, robot_id: str = Body("", embed=True)):
    """手动执行流程 — 不传 robot_id 时自动按步骤类型识别目标机器人:
    - 任一步骤以 unitree_ 开头 → robot_id=unitree_g1
    - 否则 → 取在线的豹小秘 (没有就报错)
    跟语音触发的路径保持一致, 用户不用在 UI 上挑机器人。
    """
    # 自动推断 robot_id (调用方没传或传空时)
    if not robot_id:
        robot_id = await _auto_pick_robot(workflow_id)
        if not robot_id:
            return {"status": "error", "message": "流程没绑定机器人, 也没在线豹小秘可用"}
    is_unitree = bool(robot_id and (robot_id.startswith("unitree") or "g1" in robot_id.lower()
                                    or "h1" in robot_id.lower()))
    if not is_unitree:
        # 豹小秘: 内存中没找到连接时，等待几秒（重启后机器人重连有延迟）
        if not get_robot(robot_id):
            for _ in range(5):
                await asyncio.sleep(1)
                if get_robot(robot_id):
                    break
    result = await start_flow(workflow_id, robot_id)
    if isinstance(result, dict):
        result["robot_id"] = robot_id  # 让前端 toast 知道实际跑的是哪台
    return result


async def _auto_pick_robot(workflow_id: int) -> str:
    """根据流程步骤类型自动选择目标机器人 ID。

    规则:
    - 任意步骤 step_type 以 'unitree_' 开头 → 'unitree_g1' (假设宇树启用)
    - 否则取在线的豹小秘 (get_all_robots 第一个), 没有则 ''
    """
    from app.database import get_db
    from app.tcp.server import get_all_robots
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT step_type FROM tour_script_steps WHERE script_id=?",
            (workflow_id,),
        )
    finally:
        await db.close()
    has_unitree = any((r["step_type"] or "").startswith("unitree_") for r in rows)
    if has_unitree:
        # 检查宇树启用
        db2 = await get_db()
        try:
            cfg = await db2.execute_fetchall(
                "SELECT value FROM system_config WHERE key='unitree_enabled'"
            )
            enabled = (cfg[0]["value"] if cfg else "1").strip() not in ("0", "false", "False", "")
        finally:
            await db2.close()
        if enabled:
            return "unitree_g1"
    # 默认走豹小秘 — 取第一个在线连接
    robots = get_all_robots() or {}
    for rid, conn in robots.items():
        if conn:
            return rid
    return ""


@router.get("/active-workflows")
async def list_active_flows():
    """正在执行中的流程列表 — 供前端显示"停止"按钮"""
    items = []
    for robot_id, ex in get_all_active_flows().items():
        if ex.status not in ("running", "paused"):
            continue
        items.append({
            "script_id": ex.script_id,
            "script_name": ex.script_name,
            "robot_id": robot_id,
            "status": ex.status,
            "current_step": ex.current_step_index + 1,
            "total_steps": len(ex.steps),
            "started_at": ex.started_at,
        })
    return {"data": items}


@router.post("/active-workflows/stop")
async def stop_active_flow(robot_id: str = Body(..., embed=True)):
    """停止指定机器人正在执行的流程"""
    return await stop_flow(robot_id)


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: int):
    """删除流程"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM tour_scripts WHERE id=?", (workflow_id,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


# ============================================================
# 定时任务
# ============================================================
@router.get("/schedules")
async def list_schedules():
    """获取定时任务列表"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT id, name, schedule_type, schedule_expr,
                      action_type, action_config, enabled,
                      last_run, next_run, description
               FROM schedules ORDER BY id"""
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.post("/schedules")
async def create_schedule(
    name: str = Body(...),
    schedule_type: str = Body(...),
    schedule_expr: str = Body(...),
    action_type: str = Body(...),
    action_config: dict = Body({}),
    description: str = Body(""),
):
    """创建定时任务"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO schedules
               (name, schedule_type, schedule_expr, action_type, action_config, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                name,
                schedule_type,
                schedule_expr,
                action_type,
                json.dumps(action_config, ensure_ascii=False),
                description,
            ),
        )
        await db.commit()
        return {"status": "ok", "data": {"id": cursor.lastrowid}}
    finally:
        await db.close()


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: int, enabled: int = Body(None, embed=True)):
    """更新定时任务（启用/禁用）"""
    db = await get_db()
    try:
        if enabled is not None:
            await db.execute(
                "UPDATE schedules SET enabled=? WHERE id=?", (enabled, schedule_id)
            )
            await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int):
    """删除定时任务"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


# ============================================================
# 流程模板
# ============================================================
_PRESET_TEMPLATES = [
    {
        "id": "tpl_welcome",
        "name": "👋 迎宾流程",
        "description": "机器人在入口迎接访客，播报欢迎词，引导进入展厅",
        "steps": [
            {"order": 1, "type": "navigate", "config": {"pointName": "入口"}, "delay_sec": 0, "has_callback": True},
            {"order": 2, "type": "tts", "config": {"text": "欢迎菅临参观，我是您的智能讲解员，很高兴见到您！"}, "delay_sec": 0, "has_callback": True},
            {"order": 3, "type": "tts", "config": {"text": "请跟我来，我将带您开始今天的参观之旅。"}, "delay_sec": 2, "has_callback": True},
        ]
    },
    {
        "id": "tpl_full_tour",
        "name": "🗺️ 全馆导览",
        "description": "按顺序导航全部展区，每站投屏+讲解+等待",
        "steps": [
            {"order": 1, "type": "tts", "config": {"text": "现在开始全馆导览，请跟随我的脚步。"}, "delay_sec": 0, "has_callback": True},
            {"order": 2, "type": "navigate", "config": {"pointName": "展区1"}, "delay_sec": 0, "has_callback": True},
            {"order": 3, "type": "tts", "config": {"text": "我们到了第一个展区，请看屏幕了解详情。"}, "delay_sec": 2, "has_callback": True},
            {"order": 4, "type": "tts", "config": {"text": "接下来我们前往下一个展区。"}, "delay_sec": 10, "has_callback": True},
        ]
    },
    {
        "id": "tpl_vip",
        "name": "🌟 VIP 接待",
        "description": "重要客户接待流程，开灯+切专场+迎宾+导览",
        "steps": [
            {"order": 1, "type": "group_command", "config": {"commandName": "每日全开"}, "delay_sec": 0, "has_callback": False},
            {"order": 2, "type": "tts", "config": {"text": "尊敬的贵宾您好，欢迎菅临参观，我们已为您准备好专属展示。"}, "delay_sec": 3, "has_callback": True},
            {"order": 3, "type": "navigate", "config": {"pointName": "展区1"}, "delay_sec": 0, "has_callback": True},
        ]
    },
    {
        "id": "tpl_closing",
        "name": "🌃 闭馆流程",
        "description": "播报闭馆提醒，关闭屏幕，关灯",
        "steps": [
            {"order": 1, "type": "tts", "config": {"text": "尊敬的访客，展厅即将闭馆，感谢您的参观，欢迎下次光临！"}, "delay_sec": 0, "has_callback": True},
            {"order": 2, "type": "navigate", "config": {"pointName": "入口"}, "delay_sec": 5, "has_callback": True},
            {"order": 3, "type": "group_command", "config": {"commandName": "每日全关"}, "delay_sec": 5, "has_callback": False},
        ]
    },
    {
        "id": "tpl_demo",
        "name": "🎬 AI巡演演示",
        "description": "三端协作演示: 机器人+中控屏+数字人 联动讲解",
        "steps": [
            {"order": 1, "type": "tts", "config": {"text": "各位领导大家好，欢迎来到展厅，今天的参观由我和我的同事们共同为您讲解。"}, "delay_sec": 0, "has_callback": True},
            {"order": 2, "type": "resource_play", "config": {"resourceName": "封面PPT"}, "delay_sec": 2, "has_callback": False},
            {"order": 3, "type": "tts", "config": {"text": "请看屏幕，我来为大家介绍。"}, "delay_sec": 2, "has_callback": True},
        ]
    },
]


@router.get("/templates")
async def list_templates():
    """获取流程模板列表"""
    return {"data": _PRESET_TEMPLATES}


@router.post("/templates/{template_id}/apply")
async def apply_template(template_id: str, name: str = Body(default="", embed=True)):
    """从模板创建流程"""
    tpl = next((t for t in _PRESET_TEMPLATES if t["id"] == template_id), None)
    if not tpl:
        return {"error": "模板不存在", "code": 404}

    flow_name = name or tpl["name"]
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO tour_scripts (name, description, trigger_word, is_active)
               VALUES (?, ?, '', 1)""",
            (flow_name, tpl["description"]),
        )
        script_id = cursor.lastrowid

        for step in tpl["steps"]:
            await db.execute(
                """INSERT INTO tour_script_steps
                   (script_id, step_order, step_type, step_config,
                    delay_seconds, has_callback)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    script_id,
                    step["order"],
                    step["type"],
                    json.dumps(step["config"], ensure_ascii=False),
                    step.get("delay_sec", 0),
                    1 if step.get("has_callback") else 0,
                ),
            )

        await db.commit()
        return {"status": "ok", "script_id": script_id, "name": flow_name}
    finally:
        await db.close()


@router.post("/workflows/{script_id}/export")
async def export_workflow(script_id: int):
    """导出流程为 JSON 模板"""
    db = await get_db()
    try:
        flow = await db.execute_fetchall(
            "SELECT name, description FROM tour_scripts WHERE id=?",
            (script_id,),
        )
        if not flow:
            return {"error": "流程不存在", "code": 404}

        steps = await db.execute_fetchall(
            """SELECT step_order, step_type, step_config, delay_seconds, has_callback
               FROM tour_script_steps WHERE script_id=? ORDER BY step_order""",
            (script_id,),
        )

        return {
            "data": {
                "name": flow[0]["name"],
                "description": flow[0]["description"],
                "steps": [
                    {
                        "order": s["step_order"],
                        "type": s["step_type"],
                        "config": json.loads(s["step_config"] or "{}"),
                        "delay_sec": s["delay_seconds"],
                        "has_callback": bool(s["has_callback"]),
                    }
                    for s in steps
                ],
            }
        }
    finally:
        await db.close()
