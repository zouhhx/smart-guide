"""
参考数据 API — 所有下拉框数据源

零ID化核心：所有接口返回 id + name，前端只展示 name，提交时用 id。
级联关系：展馆 → 展区 → 展项 → (选专场) → 资源
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from app.database import get_db

router = APIRouter()


# ============================================================
# 展馆
# ============================================================
@router.get("/ref/halls")
async def list_halls():
    """获取展馆列表"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT id, name, description FROM halls ORDER BY id")
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


# ============================================================
# 展区
# ============================================================
@router.get("/ref/areas")
async def list_areas(hall_id: int = Query(None, description="按展馆筛选")):
    """获取展区列表"""
    db = await get_db()
    try:
        if hall_id:
            rows = await db.execute_fetchall(
                "SELECT id, hall_id, name, description FROM areas WHERE hall_id=? ORDER BY id",
                (hall_id,),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT id, hall_id, name, description FROM areas ORDER BY id"
            )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


# ============================================================
# 展项 (对用户叫"展项"，底层是 terminal)
# ============================================================
@router.get("/ref/exhibits")
async def list_exhibits(area_id: int = Query(None, description="按展区筛选")):
    """获取展项列表"""
    db = await get_db()
    try:
        if area_id:
            rows = await db.execute_fetchall(
                """SELECT id, area_id, name, exhibition_area_terminal_id,
                          ip, terminal_type, zone_name
                   FROM exhibits WHERE area_id=? ORDER BY id""",
                (area_id,),
            )
        else:
            rows = await db.execute_fetchall(
                """SELECT id, area_id, name, exhibition_area_terminal_id,
                          ip, terminal_type, zone_name
                   FROM exhibits ORDER BY id"""
            )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


# ============================================================
# 专场
# ============================================================
@router.get("/ref/specials")
async def list_specials():
    """获取专场列表"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name, description, is_current FROM specials ORDER BY id"
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/ref/specials/current")
async def get_current_special():
    """获取当前激活专场"""
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT id, name, description FROM specials WHERE is_current=1 LIMIT 1"
        )
        if row:
            return {"data": dict(row[0])}
        return {"data": None}
    finally:
        await db.close()


# ============================================================
# 资源 — 核心：按 展项+专场 动态查询
# ============================================================
@router.get("/ref/resources")
async def list_resources(
    exhibit_id: int = Query(None, description="展项ID"),
    special_id: int = Query(None, description="专场ID"),
):
    """
    获取资源列表
    - 无参数：返回全部资源
    - 有 exhibit_id + special_id：返回该展项在该专场下的动态资源列表
    - 仅 special_id：返回该专场下所有资源
    """
    db = await get_db()
    try:
        if exhibit_id and special_id:
            # 核心查询：展项+专场 → 资源
            rows = await db.execute_fetchall(
                """SELECT r.id, r.name, r.category, r.file_url, r.thumbnail_url,
                          r.program_name, r.program_path, r.original_file,
                          r.description, r.narration_text,
                          ser.sort_order
                   FROM special_exhibit_resources ser
                   JOIN resources r ON r.id = ser.resource_id
                   WHERE ser.special_id=? AND ser.exhibit_id=?
                   ORDER BY ser.sort_order, r.id""",
                (special_id, exhibit_id),
            )
        elif special_id:
            rows = await db.execute_fetchall(
                """SELECT DISTINCT r.id, r.name, r.category, r.file_url,
                          r.thumbnail_url, r.program_name, r.description
                   FROM special_exhibit_resources ser
                   JOIN resources r ON r.id = ser.resource_id
                   WHERE ser.special_id=?
                   ORDER BY r.id""",
                (special_id,),
            )
        else:
            rows = await db.execute_fetchall(
                """SELECT id, name, category, file_url, thumbnail_url,
                          program_name, description
                   FROM resources ORDER BY id"""
            )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


# ============================================================
# 设备
# ============================================================
@router.get("/ref/equipments")
async def list_equipments(area_id: int = Query(None, description="按展区筛选")):
    """获取设备列表"""
    db = await get_db()
    try:
        if area_id:
            rows = await db.execute_fetchall(
                """SELECT id, area_id, name, ip, device_type, zone_name,
                          cmd_on, cmd_off
                   FROM equipments WHERE area_id=? ORDER BY id""",
                (area_id,),
            )
        else:
            rows = await db.execute_fetchall(
                """SELECT id, area_id, name, ip, device_type, zone_name,
                          cmd_on, cmd_off
                   FROM equipments ORDER BY id"""
            )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


# ============================================================
# 组策略
# ============================================================
@router.get("/ref/group_commands")
async def list_group_commands():
    """获取组策略列表"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name, cmd_on, cmd_off, description FROM group_commands ORDER BY id"
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


# ============================================================
# 导航点位
# ============================================================
@router.get("/ref/positions")
async def list_positions():
    """获取导航点位列表"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT id, position_id, name, map_id, map_name
               FROM nav_positions ORDER BY name"""
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


# ============================================================
# 点位绑定
# ============================================================
@router.get("/ref/position_bindings")
async def list_position_bindings():
    """获取点位绑定关系"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT pb.id, pb.position_id, np.name as position_name,
                      pb.bind_type, pb.bind_target_id, pb.description,
                      CASE pb.bind_type
                          WHEN 'exhibit' THEN (SELECT name FROM exhibits WHERE id=pb.bind_target_id)
                          WHEN 'area' THEN (SELECT name FROM areas WHERE id=pb.bind_target_id)
                      END as target_name
               FROM position_bindings pb
               LEFT JOIN nav_positions np ON np.position_id = pb.position_id
               ORDER BY np.name"""
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()



@router.post("/system/position_bindings")
async def create_position_binding(request: Request):
    """创建点位绑定"""
    body = await request.json()
    position_id = body.get("position_id", "")
    bind_type = body.get("bind_type", "exhibit")
    bind_target_id = body.get("bind_target_id", 0)
    description = body.get("description", "")
    if not position_id or not bind_target_id:
        return JSONResponse({"error": "缺少必填字段"}, status_code=400)
    db = await get_db()
    try:
        existing = await db.execute_fetchall(
            "SELECT id FROM position_bindings WHERE position_id=? AND bind_type=? AND bind_target_id=?",
            (position_id, bind_type, int(bind_target_id)),
        )
        if existing:
            return {"ok": False, "error": "该绑定已存在"}
        await db.execute(
            "INSERT INTO position_bindings (position_id, bind_type, bind_target_id, description) VALUES (?, ?, ?, ?)",
            (position_id, bind_type, int(bind_target_id), description),
        )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.delete("/system/position_bindings/{binding_id}")
async def delete_position_binding(binding_id: int):
    """删除点位绑定"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM position_bindings WHERE id=?", (binding_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# ============================================================
# 参观点位配置
# ============================================================
@router.get("/ref/tour_points")
async def list_tour_points():
    """获取参观点位配置"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT tp.id, tp.position_id, np.name as position_name,
                      tp.enabled, tp.end_phrase, tp.return_position_id,
                      tp.is_photo_point, tp.photo_suggestion_enabled,
                      tp.photo_suggestion_text, tp.photo_download_base_url,
                      np2.name as return_position_name
               FROM tour_points tp
               LEFT JOIN nav_positions np ON np.position_id = tp.position_id
               LEFT JOIN nav_positions np2 ON np2.position_id = tp.return_position_id
               ORDER BY tp.id"""
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.post("/system/tour_points")
async def save_tour_point(request: Request):
    """创建或更新参观点位"""
    body = await request.json()
    position_id = body.get("position_id", "")
    enabled = body.get("enabled", True)
    end_phrase = body.get("end_phrase", "感谢参观，欢迎下次再来！")
    return_position_id = body.get("return_position_id", "")
    is_photo_point = body.get("is_photo_point", False)
    photo_suggestion_enabled = body.get("photo_suggestion_enabled", False)
    photo_suggestion_text = body.get("photo_suggestion_text", "")
    photo_download_base_url = body.get("photo_download_base_url", "")
    if not position_id:
        return JSONResponse({"error": "缺少position_id"}, status_code=400)
    db = await get_db()
    try:
        existing = await db.execute_fetchall(
            "SELECT id FROM tour_points WHERE position_id=?", (position_id,)
        )
        if existing:
            await db.execute(
                """UPDATE tour_points
                   SET enabled=?, end_phrase=?, return_position_id=?,
                       is_photo_point=?, photo_suggestion_enabled=?,
                       photo_suggestion_text=?, photo_download_base_url=?,
                       updated_at=datetime('now','localtime')
                   WHERE position_id=?""",
                (1 if enabled else 0, end_phrase, return_position_id,
                 1 if is_photo_point else 0, 1 if photo_suggestion_enabled else 0,
                 photo_suggestion_text, photo_download_base_url, position_id),
            )
        else:
            await db.execute(
                """INSERT INTO tour_points (
                       position_id, enabled, end_phrase, return_position_id,
                       is_photo_point, photo_suggestion_enabled,
                       photo_suggestion_text, photo_download_base_url
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (position_id, 1 if enabled else 0, end_phrase, return_position_id,
                 1 if is_photo_point else 0, 1 if photo_suggestion_enabled else 0,
                 photo_suggestion_text, photo_download_base_url),
            )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.delete("/system/tour_points/{point_id}")
async def delete_tour_point(point_id: int):
    """删除参观点位"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM tour_points WHERE id=?", (point_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# ============================================================
# 意图命令映射
# ============================================================
@router.get("/ref/intent_mappings")
async def list_intent_mappings():
    """获取意图命令映射"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT id, intent_name, display_name, command_type,
                      command_target, command_action, reply_text
               FROM intent_command_mappings ORDER BY display_name"""
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


# ============================================================
# 综合：一次性获取所有下拉框数据 (减少前端请求)
# ============================================================
@router.get("/ref/all")
async def get_all_ref_data():
    """一次性获取所有参考数据，用于前端初始化下拉框"""
    db = await get_db()
    try:
        halls = await db.execute_fetchall("SELECT id, name FROM halls ORDER BY id")
        areas = await db.execute_fetchall("SELECT id, hall_id, name FROM areas ORDER BY id")
        exhibits = await db.execute_fetchall(
            "SELECT id, area_id, name, exhibition_area_terminal_id FROM exhibits ORDER BY id"
        )
        specials = await db.execute_fetchall(
            "SELECT id, name, is_current FROM specials ORDER BY id"
        )
        equipments = await db.execute_fetchall(
            "SELECT id, area_id, name, cmd_on, cmd_off FROM equipments ORDER BY id"
        )
        group_cmds = await db.execute_fetchall(
            "SELECT id, name, cmd_on, cmd_off FROM group_commands ORDER BY id"
        )
        positions = await db.execute_fetchall(
            "SELECT id, position_id, name, map_name FROM nav_positions ORDER BY name"
        )

        return {
            "data": {
                "halls": [dict(r) for r in halls],
                "areas": [dict(r) for r in areas],
                "exhibits": [dict(r) for r in exhibits],
                "specials": [dict(r) for r in specials],
                "equipments": [dict(r) for r in equipments],
                "group_commands": [dict(r) for r in group_cmds],
                "positions": [dict(r) for r in positions],
            }
        }
    finally:
        await db.close()


# ============================================================
# 宇树机器人参考数据 (动作库 / 舞蹈库 / 地图 / 点位)
# ============================================================
@router.get("/unitree/motions")
async def list_unitree_motions():
    """宇树动作库 — 给流程编辑器的动作下拉框用。"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT motion_id, name, category, sort_order "
            "FROM unitree_motions ORDER BY sort_order, motion_id"
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/unitree/dances")
async def list_unitree_dances():
    """宇树舞蹈库。"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT dance_id, name, duration_seconds "
            "FROM unitree_dances ORDER BY dance_id"
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/unitree/maps")
async def list_unitree_maps():
    """宇树地图列表 (网关拉取的结果 + V5 本地中文别名)。"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT map_id, map_code, map_name, "
            "       COALESCE(NULLIF(display_name,''), map_name) AS display_name, "
            "       map_index, is_current "
            "FROM unitree_maps ORDER BY map_index, map_id"
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/ref/unitree_point_bindings")
async def list_unitree_point_bindings():
    """宇树点位绑定列表 (join 地图/点位/目标名字)。"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT pb.id, pb.map_id, pb.point_id,
                      pb.bind_type, pb.bind_target_id, pb.description,
                      COALESCE(NULLIF(m.display_name,''), m.map_name, pb.map_id) AS map_name,
                      mp.point_name,
                      CASE pb.bind_type
                          WHEN 'exhibit' THEN (SELECT name FROM exhibits WHERE id=pb.bind_target_id)
                          WHEN 'area'    THEN (SELECT name FROM areas    WHERE id=pb.bind_target_id)
                      END AS target_name
               FROM unitree_point_bindings pb
               LEFT JOIN unitree_maps m        ON m.map_id = pb.map_id
               LEFT JOIN unitree_map_points mp ON mp.map_id = pb.map_id AND mp.point_id = pb.point_id
               ORDER BY pb.map_id, pb.point_id"""
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.get("/unitree/map-points")
async def list_unitree_map_points(map_id: str = Query(None)):
    """宇树地图点位; 不传 map_id 返回当前工作地图的点位。"""
    db = await get_db()
    try:
        if not map_id:
            cur = await db.execute_fetchall(
                "SELECT map_id FROM unitree_maps WHERE is_current=1 LIMIT 1"
            )
            if cur:
                map_id = cur[0]["map_id"]
        if not map_id:
            return {"data": []}
        rows = await db.execute_fetchall(
            "SELECT map_id, point_id, point_name FROM unitree_map_points "
            "WHERE map_id=? ORDER BY point_id",
            (str(map_id),),
        )
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()
