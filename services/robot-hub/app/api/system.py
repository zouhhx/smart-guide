"""
系统管理 API — 配置、日志、同步、状态
"""

import json
import logging
from fastapi import APIRouter, Query, Body
from app.database import get_db

router = APIRouter()
logger = logging.getLogger("hub.api.system")


@router.get("/robots")
async def list_robots():
    """获取机器人列表 — 含豹小秘 (robots 表) + 宇树 (虚拟条目, 网关启用时)。
    前端"手动启动流程"用此列表选目标机器人。宇树通过 HTTP 网关连接, 不在 robots 表里,
    需要单独合成一条让前端能选到。"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT id, name, status, battery, location,
                      version, last_heartbeat, connected_at, updated_at,
                      COALESCE(person_count, 0) AS person_count
               FROM robots ORDER BY last_heartbeat DESC"""
        )
        result = [dict(r) for r in rows]
        # 宇树: 读 system_config.unitree_enabled, 启用就加一条虚拟机器人
        try:
            cfg = await db.execute_fetchall(
                "SELECT key, value FROM system_config WHERE key IN "
                "('unitree_enabled','unitree_robot_name','unitree_base_url')"
            )
            cfg_map = {r["key"]: (r["value"] or "").strip() for r in cfg}
            if cfg_map.get("unitree_enabled", "1") not in ("0", "false", "False", ""):
                # 健康检查 (3s 超时), 网关响应即视为在线
                base_url = cfg_map.get("unitree_base_url") or "http://192.168.23.253:8000"
                online = False
                try:
                    import aiohttp
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=3.0)
                    ) as session:
                        async with session.get(f"{base_url.rstrip('/')}/health") as resp:
                            data = await resp.json(content_type=None)
                            online = bool(data and data.get("status") == "ok")
                except Exception:
                    online = False
                result.append({
                    "id": "unitree_g1",
                    "name": cfg_map.get("unitree_robot_name") or "小勇 (宇树)",
                    "status": "idle" if online else "offline",
                    "battery": 0,
                    "location": "",
                    "version": "",
                    "last_heartbeat": None,
                    "connected_at": None,
                    "updated_at": None,
                    "person_count": 0,
                    "kind": "unitree",
                })
        except Exception as e:
            import logging
            logging.getLogger("hub.api.system").debug("[/robots] 宇树虚拟条目失败: %s", e)
        return {"data": result}
    finally:
        await db.close()


@router.get("/system/config")
async def get_system_config():
    """获取系统配置"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT key, value, description FROM system_config ORDER BY key"
        )
        return {"data": {r["key"]: r["value"] for r in rows}}
    finally:
        await db.close()


@router.put("/system/config")
async def update_system_config(configs: dict = Body(...)):
    """批量更新系统配置"""
    db = await get_db()
    try:
        for key, value in configs.items():
            await db.execute(
                """INSERT INTO system_config (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now','localtime')""",
                (key, str(value), str(value)),
            )
        await db.commit()
    finally:
        await db.close()

    # 名字类 key 变更后让缓存立即失效（否则最多等 20s TTL）
    if {"robot_display_name", "digital_human_display_name"} & set(configs.keys()):
        try:
            from app.engine.names import invalidate_cache
            invalidate_cache()
        except Exception:
            pass

    # 如果修改了机器人相关配置，广播下发到所有在线机器人
    # (wake_word 已固定为"小豹小豹", 不再支持后台配置, 所以不在该集合里)
    robot_keys = {"heartbeat_interval", "listen_timeout", "listen_language"}
    if robot_keys & set(configs.keys()):
        from app.tcp.server import push_config_to_all_robots
        pushed = await push_config_to_all_robots()
        return {"status": "ok", "pushed_to_robots": pushed}
    return {"status": "ok"}


@router.get("/system/logs")
async def get_logs(
    log_type: str = Query(None, description="单一 log_type 精确"),
    types: str = Query(None, description="多 log_type, 逗号分隔, 与 log_type 二选一"),
    status: str = Query(None, description="ok|error|warning|timeout, 多个用逗号"),
    source: str = Query(None, description="source 模糊匹配"),
    q: str = Query(None, description="跨字段关键词模糊 (action+source+target+detail)"),
    since_minutes: int = Query(None, ge=1, le=10080, description="过去 N 分钟内"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """获取执行日志 — 增强版: 支持多类型/状态/来源/全文/时间窗 联合过滤"""
    db = await get_db()
    try:
        wheres: list[str] = []
        params: list = []
        # 单 type / 多 type
        if types:
            type_list = [t.strip() for t in types.split(",") if t.strip()]
            if type_list:
                wheres.append(f"log_type IN ({','.join(['?']*len(type_list))})")
                params.extend(type_list)
        elif log_type:
            wheres.append("log_type=?")
            params.append(log_type)
        # status (可多)
        if status:
            st_list = [s.strip() for s in status.split(",") if s.strip()]
            if st_list:
                wheres.append(f"status IN ({','.join(['?']*len(st_list))})")
                params.extend(st_list)
        # source 模糊
        if source:
            wheres.append("source LIKE ?")
            params.append(f"%{source}%")
        # 全文 q
        if q:
            wheres.append(
                "(action LIKE ? OR source LIKE ? OR target LIKE ? OR detail LIKE ?)"
            )
            kw = f"%{q}%"
            params.extend([kw, kw, kw, kw])
        # 时间窗
        if since_minutes:
            wheres.append("created_at >= datetime('now', ?, 'localtime')")
            params.append(f"-{int(since_minutes)} minutes")
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""

        count_row = await db.execute_fetchall(
            f"SELECT COUNT(*) as cnt FROM execution_logs {where_sql}", params
        )
        total = count_row[0]["cnt"]

        rows = await db.execute_fetchall(
            f"""SELECT id, log_type, source, target, action, detail,
                       status, duration_ms, created_at
                FROM execution_logs {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )

        return {"data": [dict(r) for r in rows], "total": total}
    finally:
        await db.close()


@router.get("/system/logs/summary")
async def get_logs_summary(since_minutes: int = Query(60, ge=1, le=10080)):
    """日志摘要: 按 log_type / status 给计数, 用于日志页顶部展示。"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT log_type, status, COUNT(*) AS cnt "
            "FROM execution_logs "
            "WHERE created_at >= datetime('now', ?, 'localtime') "
            "GROUP BY log_type, status",
            (f"-{int(since_minutes)} minutes",),
        )
        by_type: dict = {}
        by_status: dict = {"ok": 0, "error": 0, "warning": 0, "timeout": 0, "other": 0}
        total = 0
        for r in rows:
            t = r["log_type"] or "other"
            s = r["status"] or "ok"
            by_type[t] = by_type.get(t, 0) + r["cnt"]
            bucket = s if s in by_status else "other"
            by_status[bucket] += r["cnt"]
            total += r["cnt"]
        return {
            "since_minutes": since_minutes,
            "total": total,
            "by_type": by_type,
            "by_status": by_status,
        }
    finally:
        await db.close()


@router.get("/system/stats")
async def get_stats():
    """获取系统统计"""
    db = await get_db()
    try:
        stats = {}
        for table, key in [
            ("halls", "halls"),
            ("areas", "areas"),
            ("exhibits", "exhibits"),
            ("specials", "specials"),
            ("resources", "resources"),
            ("equipments", "equipments"),
            ("group_commands", "group_commands"),
            ("nav_positions", "positions"),
            ("tour_scripts", "workflows"),
            ("robots", "robots"),
        ]:
            row = await db.execute_fetchall(f"SELECT COUNT(*) as cnt FROM {table}")
            stats[key] = row[0]["cnt"]

        # 当前专场
        current = await db.execute_fetchall(
            "SELECT name FROM specials WHERE is_current=1 LIMIT 1"
        )
        stats["current_special"] = current[0]["name"] if current else "未设置"

        # 在线机器人: 必须 status != 'offline' 且最近 90s 内有心跳
        # (防呆 — 避免历史遗留的 idle 行没心跳还被算作在线)
        online = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM robots "
            "WHERE status != 'offline' "
            "  AND last_heartbeat IS NOT NULL "
            "  AND (strftime('%s','now','localtime') - strftime('%s', last_heartbeat)) < 90"
        )
        stats["online_robots"] = online[0]["cnt"]

        return {"data": stats}
    finally:
        await db.close()


@router.post("/system/sync")
async def trigger_sync(source: str = Query("waterdrop")):
    """手动触发数据同步
    source: waterdrop | orionstar | all
    """
    import asyncio
    results = {}

    if source in ("waterdrop", "all"):
        from app.sync.waterdrop import WaterdropSync
        sync = WaterdropSync()
        try:
            stats = await sync.full_sync()
            results["waterdrop"] = stats
        except Exception as e:
            results["waterdrop"] = {"error": str(e)}

    if source in ("orionstar", "all"):
        from app.sync.orionstar import OrionStarSync
        sync = OrionStarSync()
        try:
            stats = await sync.sync_positions()
            results["orionstar"] = stats
        except Exception as e:
            results["orionstar"] = {"error": str(e)}

    return {"status": "ok", "message": "同步完成", "data": results}


@router.post("/admin/openclaw/resync")
async def resync_openclaw_knowledge():
    """手动触发 OpenClaw 知识同步 (展项/流程/设备 等 → agent workspace MD)。
    适用场景: 改了展项目录或流程模板后, 不重启服务也能让 LLM 立刻看到新数据。"""
    from app.sync.openclaw_knowledge import sync_to_openclaw
    try:
        r = await sync_to_openclaw()
        return {"status": "ok", "data": r}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ============================================================
# 点位绑定管理
# ============================================================
@router.post("/system/position_bindings")
async def create_position_binding(
    position_id: str = Body(...),
    bind_type: str = Body(..., description="exhibit 或 area"),
    bind_target_id: int = Body(...),
    description: str = Body(""),
):
    """创建点位绑定"""
    if bind_type not in ("exhibit", "area"):
        return {"error": "bind_type 必须是 exhibit 或 area", "code": 400}

    db = await get_db()
    try:
        await db.execute(
            """INSERT OR REPLACE INTO position_bindings
               (position_id, bind_type, bind_target_id, description)
               VALUES (?, ?, ?, ?)""",
            (position_id, bind_type, bind_target_id, description),
        )
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@router.delete("/system/position_bindings/{binding_id}")
async def delete_position_binding(binding_id: int):
    """删除点位绑定"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM position_bindings WHERE id=?", (binding_id,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


# ============================================================
# 意图命令映射管理
# ============================================================
@router.post("/system/intent_mappings")
async def create_intent_mapping(
    intent_name: str = Body(...),
    display_name: str = Body(...),
    command_type: str = Body(...),
    command_target: str = Body(...),
    command_action: str = Body(...),
    reply_text: str = Body(""),
):
    """创建意图命令映射"""
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR REPLACE INTO intent_command_mappings
               (intent_name, display_name, command_type, command_target,
                command_action, reply_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (intent_name, display_name, command_type, command_target,
             command_action, reply_text),
        )
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@router.delete("/system/intent_mappings/{mapping_id}")
async def delete_intent_mapping(mapping_id: int):
    """删除意图命令映射"""
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM intent_command_mappings WHERE id=?", (mapping_id,)
        )
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


# ============================================================
# 讲解词管理
# ============================================================
@router.get("/resources/narrations")
async def list_narrations(
    special_id: int = Query(default=0),
    exhibit_id: int = Query(default=0),
    resource_id: int = Query(default=0),
):
    """获取资源列表（含讲解词）"""
    db = await get_db()
    try:
        sql = """SELECT r.id, r.name, r.category, r.narration_text, r.description,
                        r.file_url, r.thumbnail_url
                 FROM resources r"""
        params = []
        if resource_id:
            sql += " WHERE r.id=?"
            params = [resource_id]
        elif special_id and exhibit_id:
            sql += """ JOIN special_exhibit_resources ser
                      ON ser.resource_id = r.id
                      WHERE ser.special_id=? AND ser.exhibit_id=?"""
            params = [special_id, exhibit_id]
        sql += " ORDER BY r.name"
        rows = await db.execute_fetchall(sql, params)
        return {"data": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.put("/resources/{resource_id}/narration")
async def update_narration(
    resource_id: int,
    narration_text: str = Body(..., embed=True),
):
    """手动编辑讲解词"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE resources SET narration_text=?, narration_edited=1 WHERE id=?",
            (narration_text, resource_id),
        )
        await db.commit()
        return {"status": "ok", "message": "讲解词已更新"}
    finally:
        await db.close()


@router.post("/resources/{resource_id}/narration/generate")
async def generate_narration_api(resource_id: int):
    """用 AI 生成讲解词"""
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT name, category FROM resources WHERE id=?", (resource_id,)
        )
        if not row:
            return {"error": "资源不存在", "code": 404}

        from app.engine.llm import generate_narration
        narration = await generate_narration(row[0]["name"], row[0]["category"])
        if not narration:
            return {"error": "AI 生成失败，请检查 LLM 配置是否开启", "code": 500}

        await db.execute(
            "UPDATE resources SET narration_text=?, narration_edited=1 WHERE id=?",
            (narration, resource_id),
        )
        await db.commit()
        return {"status": "ok", "narration": narration}
    finally:
        await db.close()


# ============================================================
# Webhook 测试
# ============================================================
@router.post("/webhook/test")
async def test_webhook():
    """发送测试 webhook 消息"""
    from app.engine.webhook import send_alert
    try:
        await send_alert(
            "test",
            "🔔 这是一条测试告警",
            "展厅流程中枢告警服务连接测试成功",
        )
        return {"status": "ok", "message": "测试消息已发送"}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 宇树机器人 — 手动同步 / 健康检查 / Webhook 接收
# ============================================================
@router.post("/unitree/command")
async def unitree_manual_command(payload: dict = Body(...)):
    """后台手动控制入口 — 透传到宇树网关 /api/common。
    body: {"action": "tts.play", "params": {"text": "..."}}
    """
    from app.engine.unitree_client import UnitreeClient, UnitreeGatewayError
    action = (payload or {}).get("action") or ""
    params = (payload or {}).get("params") or {}
    if not action:
        return {"status": "error", "message": "缺少 action"}
    client = await UnitreeClient.from_config(timeout=10)
    if not client:
        return {"status": "error", "message": "宇树网关未启用"}
    params = params if isinstance(params, dict) else {}
    try:
        # 部分 action 在网关有专用 REST 端点, 走 client 对应方法 (而不是透传 /api/common),
        # 这样也避开 params 里含 "action" 键和 common() 签名撞名的问题 (取消导航报错的根因).
        if action == "nav.task_control":
            data = await client.nav_task_control(
                str(params.get("task_id") or ""),
                str(params.get("action") or ""),
            )
        elif action == "nav.planning_to_goal":
            mid = str(params.get("current_working_map_id") or params.get("map_id") or "")
            pid = int(params.get("point_id") or 0)
            tid = await client.nav_goto(mid, pid, task_id=params.get("task_id") or None)
            # 登记 task_id → (map_id, point_id), 到达时用来查点位绑定自动播报
            if tid and mid and pid:
                try:
                    from app.engine.unitree_events import register_nav_target
                    register_nav_target(tid, mid, pid)
                except Exception:
                    logger.exception("[unitree_manual_command] register_nav_target 失败")
            data = {"task_id": tid, "map_id": mid, "point_id": pid}
        else:
            # 其它 action 走 /api/common; 用 params= 字典形式避免 kwargs 同名冲突.
            data = await client.common(action, params=params)
        return {"status": "ok", "data": data}
    except UnitreeGatewayError as e:
        return {"status": "error", "code": e.code, "message": e.msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.put("/system/unitree_maps/{map_id}/display_name")
async def set_unitree_map_display_name(map_id: str, payload: dict = Body(...)):
    """给宇树地图起本地中文别名; 网关 map_name 通常只是一串 ID."""
    display_name = str((payload or {}).get("display_name", "")).strip()
    db = await get_db()
    try:
        cur = await db.execute_fetchall(
            "SELECT map_id FROM unitree_maps WHERE map_id=?", (str(map_id),)
        )
        if not cur:
            return {"ok": False, "error": "地图不存在"}
        await db.execute(
            "UPDATE unitree_maps SET display_name=?, updated_at=datetime('now','localtime') "
            "WHERE map_id=?",
            (display_name, str(map_id)),
        )
        await db.commit()
        return {"ok": True, "map_id": map_id, "display_name": display_name}
    finally:
        await db.close()


@router.post("/system/unitree_point_bindings")
async def create_unitree_point_binding(payload: dict = Body(...)):
    """创建宇树点位 → 展项/展区 绑定."""
    map_id = str((payload or {}).get("map_id", "")).strip()
    point_id = (payload or {}).get("point_id")
    bind_type = str((payload or {}).get("bind_type", "exhibit")).strip()
    bind_target_id = (payload or {}).get("bind_target_id")
    description = str((payload or {}).get("description", ""))

    if not map_id or point_id in (None, "") or not bind_target_id:
        return {"ok": False, "error": "缺少 map_id / point_id / bind_target_id"}
    if bind_type not in ("exhibit", "area"):
        return {"ok": False, "error": "bind_type 必须为 exhibit 或 area"}
    try:
        point_id = int(point_id)
        bind_target_id = int(bind_target_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "point_id / bind_target_id 必须是整数"}

    db = await get_db()
    try:
        existing = await db.execute_fetchall(
            "SELECT id FROM unitree_point_bindings "
            "WHERE map_id=? AND point_id=? AND bind_type=? AND bind_target_id=?",
            (map_id, point_id, bind_type, bind_target_id),
        )
        if existing:
            return {"ok": False, "error": "该绑定已存在"}
        await db.execute(
            "INSERT INTO unitree_point_bindings "
            "(map_id, point_id, bind_type, bind_target_id, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (map_id, point_id, bind_type, bind_target_id, description),
        )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.delete("/system/unitree_point_bindings/{binding_id}")
async def delete_unitree_point_binding(binding_id: int):
    """删除宇树点位绑定."""
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM unitree_point_bindings WHERE id=?", (binding_id,)
        )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.post("/unitree/asr_start")
async def unitree_asr_start(payload: dict = Body(default={})):
    """启动 ASR; 可选 listen_duration (秒), >0 则 V5 侧计时到点自动停, 让 APK 回唤醒词模式."""
    try:
        listen_duration = float((payload or {}).get("listen_duration", 0) or 0)
    except (TypeError, ValueError):
        listen_duration = 0.0
    listen_duration = max(0.0, min(600.0, listen_duration))
    from app.engine.unitree_voice import asr_start_timed
    return {"status": "ok", "data": await asr_start_timed(listen_duration=listen_duration)}


@router.post("/unitree/asr_stop")
async def unitree_asr_stop():
    """手动停 ASR, 顺手取消 V5 侧自动停计时器."""
    from app.engine.unitree_voice import asr_stop_cancel_timer
    return {"status": "ok", "data": await asr_stop_cancel_timer()}


@router.post("/unitree/listen_once")
async def unitree_listen_once(payload: dict = Body(default={})):
    """启动 ASR 监听一次: 拿到首条识别文本或超时停. duration 秒 (默认 10, 范围 3-60)."""
    try:
        duration = float((payload or {}).get("duration", 10))
    except (TypeError, ValueError):
        duration = 10.0
    duration = max(3.0, min(60.0, duration))
    from app.engine.unitree_voice import asr_listen_once
    try:
        result = await asr_listen_once(duration=duration)
    except Exception as e:
        logger.exception("[unitree_listen_once] 异常: %s", e)
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "data": result}


@router.post("/unitree/sync")
async def unitree_manual_sync():
    """管理后台手动触发宇树机器人动作库/舞蹈库/地图同步。"""
    from app.sync.unitree import UnitreeSync
    try:
        stats = await UnitreeSync().full_sync()
        return {"status": "ok", "stats": stats}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/unitree/sync-points")
async def unitree_sync_points(map_id: str = Body(..., embed=True)):
    """按需同步指定地图的点位 (会让网关把该地图置为当前工作地图)。"""
    from app.sync.unitree import UnitreeSync
    try:
        count = await UnitreeSync().sync_points_for_map(map_id)
        return {"status": "ok", "points": count}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/unitree/health")
async def unitree_health():
    """直连宇树网关 /health 返回健康状态。"""
    from app.engine.unitree_client import UnitreeClient
    client = await UnitreeClient.from_config(timeout=5)
    if not client:
        return {"status": "disabled"}
    try:
        return {"status": "ok", "gateway": await client.health()}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# 后台面板 3s 轮询, 多客户端叠加会放大到网关; 加 2s TTL 缓存兜底
_unitree_status_cache: dict = {"at": 0.0, "value": None}


@router.get("/unitree/status")
async def unitree_status():
    """宇树机器人完整状态 (给后台面板用): 启用/在线/库存/当前地图。

    2 秒 TTL 内存缓存, 避免多个后台客户端 3s 轮询把网关打爆。
    """
    import time as _t
    from app.engine.unitree_client import UnitreeClient, _load_gateway_config

    now = _t.time()
    if _unitree_status_cache["value"] and (now - _unitree_status_cache["at"]) < 2.0:
        return {"data": _unitree_status_cache["value"]}

    base_url, _api_key, enabled = await _load_gateway_config()

    # 库存 + 当前地图 (本地 DB, 无需打网关)
    db = await get_db()
    try:
        motions = (await db.execute_fetchall(
            "SELECT COUNT(*) AS c FROM unitree_motions"
        ))[0]["c"]
        dances = (await db.execute_fetchall(
            "SELECT COUNT(*) AS c FROM unitree_dances"
        ))[0]["c"]
        maps = (await db.execute_fetchall(
            "SELECT COUNT(*) AS c FROM unitree_maps"
        ))[0]["c"]
        points = (await db.execute_fetchall(
            "SELECT COUNT(*) AS c FROM unitree_map_points"
        ))[0]["c"]
        cur = await db.execute_fetchall(
            "SELECT map_id, map_name, "
            "       COALESCE(NULLIF(display_name,''), map_name) AS display_name "
            "FROM unitree_maps WHERE is_current=1 LIMIT 1"
        )
        current_map = dict(cur[0]) if cur else None
    finally:
        await db.close()

    result = {
        "enabled": enabled,
        "online": False,
        "base_url": base_url,
        "gateway": None,
        "error": None,
        "checked_at": _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(now)),
        "library": {
            "motions": motions,
            "dances": dances,
            "maps": maps,
            "points": points,
        },
        "current_map": current_map,
    }

    if enabled:
        client = await UnitreeClient.from_config(timeout=3)
        if client:
            try:
                gw = await client.health()
                result["gateway"] = gw
                result["online"] = bool(gw and gw.get("status") == "ok")
            except Exception as e:
                result["error"] = str(e)

    _unitree_status_cache["at"] = now
    _unitree_status_cache["value"] = result
    return {"data": result}


_forward_cache: dict = {"at": 0.0, "url": None}
_forward_stats: dict = {
    "url": "",
    "count_ok": 0,
    "count_fail": 0,
    "last_status": None,
    "last_at": "",
    "last_error": "",
}


async def _get_event_forward_url() -> str:
    """读取转发地址 (带 30s 缓存, 避免每条 webhook 都查库)。"""
    import time as _t
    now = _t.time()
    if _forward_cache["url"] is not None and (now - _forward_cache["at"]) < 30.0:
        return _forward_cache["url"]
    url = ""
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='unitree_event_forward_url'"
            )
            if rows:
                url = (rows[0]["value"] or "").strip()
        finally:
            await db.close()
    except Exception:
        pass
    _forward_cache["url"] = url
    _forward_cache["at"] = now
    return url


async def _forward_raw_event(payload: dict):
    """原封不动转发 webhook payload 到下游中控。失败入队 unitree_event_queue,
    防数据丢失; 后台任务 (drain_unitree_event_queue) 定时重试。"""
    import logging as _lg
    import time as _t
    import json as _json
    import aiohttp as _aiohttp
    _logger = _lg.getLogger("hub.api.system.unitree_forward")
    url = await _get_event_forward_url()
    _forward_stats["url"] = url
    if not url:
        return
    now_str = _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime())
    err: str = ""
    try:
        timeout = _aiohttp.ClientTimeout(total=3)
        async with _aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                _forward_stats["last_status"] = resp.status
                _forward_stats["last_at"] = now_str
                if resp.status >= 400:
                    _forward_stats["count_fail"] += 1
                    _forward_stats["last_error"] = f"HTTP {resp.status}"
                    err = f"HTTP {resp.status}"
                    _logger.warning("[unitree-forward] %s → HTTP %s", url, resp.status)
                else:
                    _forward_stats["count_ok"] += 1
                    _forward_stats["last_error"] = ""
                    return  # ← 成功直接返回, 不入队
    except Exception as e:
        _forward_stats["count_fail"] += 1
        _forward_stats["last_status"] = None
        _forward_stats["last_at"] = now_str
        _forward_stats["last_error"] = str(e)[:200]
        err = str(e)[:200]
        _logger.warning("[unitree-forward] %s 转发失败: %s (已入队重试)", url, e)
    # 失败 (HTTP 4xx/5xx 或网络异常) → 入队
    try:
        from app.database import get_db
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO unitree_event_queue (payload, target_url, fail_count, last_error) "
                "VALUES (?, ?, 1, ?)",
                (_json.dumps(payload, ensure_ascii=False), url, err[:200]),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        _logger.error("[unitree-forward] 入队失败 (数据丢失): %s", e)


async def drain_unitree_event_queue():
    """后台任务: 定时把 unitree_event_queue 里堆积的 payload 重发出去。
    成功 → 删行; 失败 → fail_count+1, last_error 更新, 留在队列里。
    fail_count >50 的太老的行 7 天后自动清理 (避免无限堆积)。"""
    import logging as _lg
    import json as _json
    import asyncio as _asyncio
    import aiohttp as _aiohttp
    _logger = _lg.getLogger("hub.api.system.unitree_forward")
    while True:
        try:
            await _asyncio.sleep(30)   # 每 30s 尝试一次
            url = await _get_event_forward_url()
            if not url:
                continue
            from app.database import get_db
            db = await get_db()
            try:
                # 7 天清理过老 + 失败超 50 次的死信
                await db.execute(
                    "DELETE FROM unitree_event_queue "
                    "WHERE fail_count >= 50 OR created_at < datetime('now', '-7 days', 'localtime')"
                )
                await db.commit()
                rows = await db.execute_fetchall(
                    "SELECT id, payload FROM unitree_event_queue "
                    "ORDER BY id ASC LIMIT 50"
                )
            finally:
                await db.close()
            if not rows:
                continue
            timeout = _aiohttp.ClientTimeout(total=3)
            async with _aiohttp.ClientSession(timeout=timeout) as session:
                for r in rows:
                    try:
                        payload = _json.loads(r["payload"])
                        async with session.post(url, json=payload) as resp:
                            ok = resp.status < 400
                    except Exception as e:
                        ok = False
                        err = str(e)[:200]
                    else:
                        err = "" if ok else f"HTTP {resp.status}"
                    db = await get_db()
                    try:
                        if ok:
                            await db.execute(
                                "DELETE FROM unitree_event_queue WHERE id=?", (r["id"],)
                            )
                        else:
                            await db.execute(
                                "UPDATE unitree_event_queue "
                                "SET fail_count=fail_count+1, last_error=?, "
                                "    last_tried_at=datetime('now','localtime') "
                                "WHERE id=?",
                                (err, r["id"]),
                            )
                        await db.commit()
                    finally:
                        await db.close()
            _logger.info("[unitree-forward.queue] drained %d rows (url=%s)", len(rows), url)
        except _asyncio.CancelledError:
            return
        except Exception as e:
            _logger.error("[unitree-forward.queue] 后台 drain 异常: %s", e)


@router.post("/unitree/event")
async def unitree_event_webhook(payload: dict = Body(...)):
    """宇树网关 → 本系统的回调入口。

    网关侧需在 .env 配置 CENTRAL_CALLBACK_URL=http://<hub>:8000/api/unitree/event。

    分发策略 (按顺序):
      0. **第一时间原封不动转发**一份到 system_config.unitree_event_forward_url
         (fire-and-forget, 不阻塞本响应, 3s 超时, 失败仅 warning)
      1. 通用事件 (ttsFinished/navTaskFinished/motionPlaybackFinished/danceFinished/
        asrWakeup/faceRecognition) → 唤醒 unitree_events 里的 Future 等待方
      2. asr → 派发给 UnitreeVoiceHandler 走语音控制/问答
      3. 全部事件都落 execution_logs (log_type=unitree_event)
    """
    import json as _json
    import asyncio as _asyncio

    # 0) 第一时间 fire-and-forget 转发; create_task 不等待, 后面 dispatch/log 并行进行
    _asyncio.create_task(_forward_raw_event(payload))

    action = str(payload.get("action") or "unknown")
    params = payload.get("params") or {}
    logger.info("[宇树] action=%s params=%s", action, params)

    # 1) 落日志 — created_at 必须显式 datetime('now','localtime'), 跟 robot_event/
    # robot_command 等写入路径保持一致, 否则 schema 默认的 CURRENT_TIMESTAMP 是 UTC,
    # 导致管理后台按 since_minutes(localtime) 过滤时宇树事件全部消失。
    try:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO execution_logs
                     (log_type, source, target, action, detail, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))""",
                (
                    "unitree_event",
                    "unitree_gateway",
                    "hub",
                    action,
                    _json.dumps(params, ensure_ascii=False)[:2000],
                    "ok",
                ),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception:
        pass

    # 2) 等待方分发 (流程执行器 / 语音环后续会注册)
    try:
        from app.engine.unitree_events import dispatch_event
        hits = dispatch_event(action, params)
        if hits:
            logger.info("[宇树] dispatch hits=%s", hits)
    except Exception:
        logger.exception("[宇树] dispatch_event failed action=%s", action)

    # 2.5) navTaskFinished 成功到达 → 查点位绑定自动播报 (不阻塞 webhook 响应)
    if action == "navTaskFinished" and (params or {}).get("state") == "PncServiceState_SUCCESS":
        task_id = str((params or {}).get("task_id") or "")
        if task_id:
            try:
                from app.engine.unitree_voice import handle_nav_arrived
                _asyncio.create_task(handle_nav_arrived(task_id, params or {}))
            except Exception:
                logger.exception("[宇树] handle_nav_arrived dispatch failed")

    # 3) asr 文本 → 语音处理器 (独立环, 不阻塞 webhook 响应)
    if action == "asr":
        text = (params or {}).get("text") or ""
        if text:
            try:
                from app.engine.unitree_voice import handle_speech
                _asyncio.create_task(handle_speech(text))
                logger.info("[宇树] asr → handle_speech dispatched text=%r", text[:120])
            except Exception:
                logger.exception("[宇树] handle_speech dispatch failed text=%r", text[:120])
        else:
            logger.warning("[宇树] asr received but params.text empty params=%s", params)
    elif action == "asrError":
        # 识别失败不用处理, 仅日志已存. (trace_id / error 都在 params)
        pass

    return {"ok": True}


@router.get("/unitree/event/debug")
async def unitree_event_debug():
    """诊断: 当前有哪些 key 有等待方 + webhook 转发统计 (运维/调试)。"""
    from app.engine.unitree_events import debug_snapshot
    return {
        "data": {
            "waiters": debug_snapshot(),
            "forward": dict(_forward_stats),
        }
    }
