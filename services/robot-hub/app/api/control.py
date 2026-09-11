"""
控制操作 API — 设备控制、专场切换、资源播放、组策略

所有操作都是"选名称 → 系统找ID → 执行指令"
"""

from fastapi import APIRouter, Body
from app.database import get_db
from app.tcp.central_control import send_tcp_command, send_commands_sequence

router = APIRouter()


# ============================================================
# 专场切换
# ============================================================
@router.post("/control/switch_special")
async def switch_special(special_id: int = Body(..., embed=True)):
    """切换专场"""
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT id, name FROM specials WHERE id=?", (special_id,)
        )
        if not row:
            return {"error": "专场不存在", "code": 404}

        special = dict(row[0])

        # 发 TCP 指令切换专场
        tcp_result = await send_tcp_command(f"2_update_{special_id}")

        # 更新数据库标记
        await db.execute("UPDATE specials SET is_current=0")
        await db.execute("UPDATE specials SET is_current=1 WHERE id=?", (special_id,))
        await db.execute(
            "UPDATE system_config SET value=? WHERE key='current_special_id'",
            (str(special_id),),
        )
        await db.execute(
            "UPDATE system_config SET value=? WHERE key='current_special_name'",
            (special["name"],),
        )
        await db.commit()

        return {"status": "ok", "message": f"专场已切换到: {special['name']}"}
    finally:
        await db.close()


# ============================================================
# 设备控制
# ============================================================
@router.post("/control/equipment")
async def control_equipment(
    equipment_id: int = Body(...),
    action: str = Body(..., description="on 或 off"),
):
    """控制设备开关"""
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT id, name, cmd_on, cmd_off FROM equipments WHERE id=?",
            (equipment_id,),
        )
        if not row:
            return {"error": "设备不存在", "code": 404}

        equip = dict(row[0])
        cmd = equip["cmd_on"] if action == "on" else equip["cmd_off"]

        if not cmd:
            return {"error": f"设备 {equip['name']} 无 {action} 指令", "code": 400}

        tcp_result = await send_tcp_command(cmd)
        return {
            "status": tcp_result["status"],
            "message": f"设备 {equip['name']} 已{('开启' if action == 'on' else '关闭')}",
            "command": cmd,
            "tcp": tcp_result,
        }
    finally:
        await db.close()


# ============================================================
# 组策略执行
# ============================================================
@router.post("/control/group_command")
async def exec_group_command(
    group_id: int = Body(...),
    action: str = Body(None, description="兼容旧版，可不传"),
):
    """执行组策略"""
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT id, name, cmd_on, cmd_off FROM group_commands WHERE id=?",
            (group_id,),
        )
        if not row:
            return {"error": "组策略不存在", "code": 404}

        gc = dict(row[0])
        # 每条组策略是独立指令，直接用 cmd_on
        cmd = gc["cmd_on"]
        if action == "off" and gc["cmd_off"]:
            cmd = gc["cmd_off"]

        if not cmd:
            return {"error": f"组策略 {gc['name']} 无可执行指令", "code": 400}

        tcp_result = await send_tcp_command(cmd)
        return {
            "status": tcp_result["status"],
            "message": f"组策略 {gc['name']} 已执行",
            "command": cmd,
            "tcp": tcp_result,
        }
    finally:
        await db.close()


# ============================================================
# 资源播放
# ============================================================
@router.post("/control/play_resource")
async def play_resource(
    special_id: int = Body(...),
    exhibit_id: int = Body(...),
    resource_id: int = Body(...),
):
    """播放资源到展项"""
    db = await get_db()
    try:
        # 获取展项的中控ID
        exhibit = await db.execute_fetchall(
            "SELECT id, name, exhibition_area_terminal_id FROM exhibits WHERE id=?",
            (exhibit_id,),
        )
        if not exhibit:
            return {"error": "展项不存在", "code": 404}

        exhibit = dict(exhibit[0])
        terminal_ctrl_id = exhibit["exhibition_area_terminal_id"]

        if not terminal_ctrl_id:
            return {"error": f"展项 {exhibit['name']} 无中控ID", "code": 400}

        # 检查当前专场
        current = await db.execute_fetchall(
            "SELECT value FROM system_config WHERE key='current_special_id'"
        )
        current_special_id = int(current[0]["value"]) if current else 0

        commands = []

        # 如果当前专场不一致，先切换
        if current_special_id != special_id:
            commands.append(f"2_update_{special_id}")
            # 需要等2秒再发资源播放

        # 资源播放指令
        commands.append(f"2_{special_id}_{terminal_ctrl_id}_{resource_id}")

        # 按顺序发 TCP 指令（含2秒间隔）
        tcp_results = await send_commands_sequence(commands)

        resource = await db.execute_fetchall(
            "SELECT name FROM resources WHERE id=?", (resource_id,)
        )
        resource_name = dict(resource[0])["name"] if resource else str(resource_id)

        return {
            "status": "ok",
            "message": f"正在播放: {resource_name} → {exhibit['name']}",
            "commands": commands,
        }
    finally:
        await db.close()


# ============================================================
# 场景查询 API (无需机器人在线)
# ============================================================
@router.get("/control/specials")
async def list_specials():
    """场景D: 查询所有专场"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name, description, is_current FROM specials ORDER BY id"
        )
        current = await db.execute_fetchall(
            "SELECT value FROM system_config WHERE key='current_special_name'"
        )
        return {
            "data": [dict(r) for r in rows],
            "current": current[0]["value"] if current else "",
        }
    finally:
        await db.close()


@router.get("/control/exhibits")
async def list_exhibits():
    """查询所有展项"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name, exhibition_area_terminal_id FROM exhibits ORDER BY id"
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/control/exhibit/{exhibit_id}/resources")
async def list_exhibit_resources(exhibit_id: int):
    """场景A: 查询展项当前专场下的资源"""
    db = await get_db()
    try:
        curr_sp = await db.execute_fetchall(
            "SELECT value FROM system_config WHERE key='current_special_id'"
        )
        sp_id = int(curr_sp[0]["value"]) if curr_sp and curr_sp[0]["value"] else 0

        exhibit = await db.execute_fetchall(
            "SELECT id, name FROM exhibits WHERE id=?", (exhibit_id,)
        )
        if not exhibit:
            return {"error": "展项不存在", "code": 404}

        rows = await db.execute_fetchall(
            """SELECT r.id, r.name, r.category, r.narration_text,
                      r.thumbnail_url, r.file_url
               FROM special_exhibit_resources ser
               JOIN resources r ON r.id = ser.resource_id
               WHERE ser.special_id=? AND ser.exhibit_id=?
               ORDER BY ser.sort_order, r.id""",
            (sp_id, exhibit_id),
        )
        return {
            "exhibit": dict(exhibit[0]),
            "special_id": sp_id,
            "data": [dict(r) for r in rows],
        }
    finally:
        await db.close()


@router.post("/control/simulate_speech")
async def simulate_speech(
    text: str = Body(..., embed=True),
    robot_id: str = Body(default="test-001", embed=True),
):
    """模拟语音输入 — 走现场同款链路 (query_router + intent_resolver)
    仅返回分类/意图, 不执行任何动作; 无需机器人在线"""
    from app.engine.query_router import classify_query, TOUR_CONTROL
    from app.engine.intent_resolver import resolve_intent
    from app.engine.world_state import get_world_state

    ws = get_world_state()
    qr = classify_query(text, ws)
    qtype = qr.get("qtype", "")
    slots = qr.get("slots", {})

    if qtype == TOUR_CONTROL:
        ir = await resolve_intent(text, ws)
        return {
            "text": text,
            "pipeline": "intent_resolver",
            "qtype": qtype,
            "intent": ir.get("intent"),
            "confidence": ir.get("confidence"),
            "entities": ir.get("entities", {}),
            "source": ir.get("source"),
            "slots": slots,
        }

    return {
        "text": text,
        "pipeline": "query_router",
        "qtype": qtype,
        "slots": slots,
        "confidence": qr.get("confidence", 0.0),
    }


# ============================================================
# 机器人控制
# ============================================================
@router.post("/control/robot")
async def control_robot(
    robot_id: str = Body(...),
    command: str = Body(...),
    params: dict = Body(default={}),
):
    """发送指令给在线机器人

    command: navigate / tts / show / listen / cancel / query_status
    params: 命令参数
    """
    from app.tcp.server import get_robot

    conn = get_robot(robot_id)
    if not conn:
        return {"error": f"机器人 {robot_id} 不在线", "code": 404}

    cmd_type = f"cmd_{command}"

    # 发送指令并等待回调（超时 30 秒）
    result = await conn.send_cmd(cmd_type, params, timeout=30)

    if result is None:
        return {"status": "timeout", "message": f"指令 {cmd_type} 超时"}

    return {
        "status": "ok",
        "message": f"指令已执行: {cmd_type}",
        "result": result,
    }
