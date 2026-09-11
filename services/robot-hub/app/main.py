"""
展厅流程中枢系统 v5.0 — 主入口

启动顺序：
1. 加载配置
2. 初始化数据库
3. 启动 HTTP 服务 (FastAPI)
4. 启动 TCP 服务端 (19888, 接收Pad通知 + 机器人连接)
5. 启动数据同步定时任务
"""

import asyncio
import logging
import logging.handlers
import os
import sys
import signal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from app.config import load_config, get_config
from app.database import init_database

# ============================================================
# 日志初始化
# ============================================================
def setup_logging(config: dict):
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "logs/hub.log")

    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)

    fmt = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件handler (带轮转)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=log_cfg.get("max_size_mb", 50) * 1024 * 1024,
        backupCount=log_cfg.get("backup_count", 5),
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    # 控制台handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


# ============================================================
# FastAPI 应用
# ============================================================
app = FastAPI(
    title="展厅流程中枢系统 v5.0",
    version="5.0.0",
    description="展厅智能流程管理中枢",
)

logger = logging.getLogger("hub.main")


# ============================================================
# API 路由注册
# ============================================================
def register_routes(app: FastAPI):
    """注册所有 API 路由"""
    from app.api.reference import router as ref_router
    from app.api.control import router as ctrl_router
    from app.api.workflows import router as wf_router
    from app.api.system import router as sys_router
    from app.api.visitors import router as visitor_router
    from app.api.admin import router as admin_router
    from app.api.music import router as music_router
    from app.api.photo import router as photo_router

    app.include_router(ref_router, prefix="/api", tags=["参考数据"])
    app.include_router(ctrl_router, prefix="/api", tags=["控制操作"])
    app.include_router(wf_router, prefix="/api", tags=["流程管理"])
    app.include_router(sys_router, prefix="/api", tags=["系统管理"])
    app.include_router(visitor_router, prefix="/api", tags=["访客管理"])
    app.include_router(admin_router, prefix="/api", tags=["运维面板"])
    app.include_router(music_router, prefix="/api", tags=["音乐曲库"])
    app.include_router(photo_router, prefix="/api", tags=["照片管理"])


# 注册路由（在应用启动前完成）
register_routes(app)


# ============================================================
# WebSocket 路由（机器人连接）
# ============================================================
from fastapi import WebSocket


@app.websocket("/ws/robot")
async def websocket_robot(websocket: WebSocket):
    await websocket.accept()
    from app.tcp.server import handle_robot_websocket
    await handle_robot_websocket(websocket)


# 静态文件
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# 音乐文件目录 — APK 通过 /media/music/<filename> 直拉 mp3.
# 启动时确保目录存在, 没有也不报错 (上传时再 mkdir)
_music_dir = os.path.join("data", "music")
os.makedirs(_music_dir, exist_ok=True)
app.mount("/media/music", StaticFiles(directory=_music_dir), name="music_media")

# 照片文件目录 — Android 二维码扫码或浏览器直接访问.
# 上传路径是 /api/photo/upload/{photo_id}, 存储到 data/photos/<photo_id>.jpg.
_photo_dir = os.path.join("data", "photos")
os.makedirs(_photo_dir, exist_ok=True)
app.mount("/media/photos", StaticFiles(directory=_photo_dir), name="photo_media")


@app.get("/")
async def index():
    """管理后台首页"""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "ok", "version": "5.0.0", "message": "展厅流程中枢系统 v5.0"}


@app.get("/admin.html")
async def admin_page():
    """运维面板 (告警 + Auto Plan 日志)"""
    p = os.path.join(static_dir, "admin.html")
    if os.path.exists(p):
        return FileResponse(p)
    return {"error": "admin.html not deployed"}


@app.get("/robot-events")
@app.get("/robot_events.html")
@app.get("/robot-events.html")
async def robot_events_page():
    """机器人回调日志实时面板 (unitree_event)"""
    p = os.path.join(static_dir, "robot_events.html")
    if os.path.exists(p):
        return FileResponse(p)
    return {"error": "robot_events.html not deployed"}


@app.get("/api/health")
async def health():
    """健康检查"""
    return {"status": "ok", "version": "5.0.0"}


# ============================================================
# 启动 & 关闭
# ============================================================
@app.on_event("startup")
async def on_startup():
    config = get_config()
    # 确保 logging 已配置（uvicorn 启动时不走 __main__）
    setup_logging(config)
    logger.info("=" * 60)
    logger.info("展厅流程中枢系统 v5.0 启动中...")
    logger.info("=" * 60)

    from app.engine.gateway_qa import validate_gateway_qa_config
    from app.engine.legacy_qa import validate_legacy_qa_config
    from app.engine.legacy_teaching import validate_legacy_teaching_config
    gateway_settings = validate_gateway_qa_config(config)
    legacy_qa_settings = validate_legacy_qa_config(config)
    legacy_teaching_settings = validate_legacy_teaching_config(config)
    if legacy_teaching_settings.enabled and legacy_qa_settings.mode != "active":
        raise ValueError("legacy_teaching.enabled=true 时 legacy_qa.mode 必须为 active")
    external_qa_active = (
        gateway_settings.mode == "active" or legacy_qa_settings.mode == "active"
    )
    logger.info("Gateway QA 模式: %s", gateway_settings.mode)
    logger.info("老系统问答模式: %s", legacy_qa_settings.mode)
    logger.info("老系统人工教学: %s", "enabled" if legacy_teaching_settings.enabled else "disabled")

    # 初始化数据库
    db_path = config.get("database", {}).get("path", "data/exhibition_hub.db")
    await init_database(db_path)
    logger.info("数据库初始化完成")

    # 数据库迁移 v3 — 访客记忆中心
    try:
        from app.database_migration_v3 import migrate_v3
        v3_result = await migrate_v3(db_path)
        logger.info("数据库迁移 v3: %s", v3_result.get("status", "unknown"))
    except Exception as e:
        logger.warning("数据库迁移 v3 失败: %s", e)

    logger.info("API 路由已加载")

    # 启动 TCP 服务端
    from app.tcp.server import start_tcp_server
    await start_tcp_server()
    logger.info("TCP 服务端启动完成")

    # 预设数据（意图映射、流程模板）
    from app.sync.preload import preload_defaults
    await preload_defaults()
    logger.info("预设数据加载完成")

    # 首次启动自动同步：检查数据库是否为空，若为空则从水滴云拉全量数据
    from app.database import get_db
    db_check = await get_db()
    try:
        exhibit_count = await db_check.execute_fetchall("SELECT COUNT(*) as cnt FROM exhibits")
        is_empty = exhibit_count[0]["cnt"] == 0
    finally:
        await db_check.close()

    if is_empty:
        logger.info("检测到数据库为空，触发首次启动自动同步...")
        try:
            from app.sync.waterdrop import WaterdropSync
            sync = WaterdropSync()
            stats = await sync.full_sync()
            logger.info("水滴云同步完成: %s", stats)
        except Exception as e:
            logger.warning("水滴云同步失败: %s", e)
    else:
        logger.info("数据库已有数据，跳过水滴云同步")

    # OrionStar 点位同步（每次启动都拉一次，然后定时拉取）
    try:
        from app.sync.orionstar import OrionStarSync
        osync = OrionStarSync()
        os_stats = await osync.sync_positions()
        logger.info("OrionStar 点位同步完成: %s", os_stats)
    except Exception as e:
        logger.warning("OrionStar 点位同步失败 (可稍后手动触发): %s", e)

    # 启动定时同步任务
    from app.sync.scheduler import start_sync_scheduler
    await start_sync_scheduler()

    # 启动定时任务执行器
    from app.engine.schedule_executor import start_schedule_executor
    await start_schedule_executor()

    # 启动访客注册任务超时清扫器
    try:
        from app.engine.visitor_service import start_register_sweeper
        await start_register_sweeper(interval_seconds=60, task_timeout_seconds=120)
    except Exception as e:
        logger.warning("访客注册超时扫描启动失败: %s", e)

    # 老系统 active 后，普通问答不再经过 OpenClaw。
    if not external_qa_active:
        try:
            from app.engine import openclaw_client
            openclaw_client.start_background_tasks()
            logger.info("OpenClaw 后台 compact 任务已启动")
        except Exception as e:
            logger.warning("OpenClaw 后台任务启动失败 (可忽略, 主决策路径不受影响): %s", e)
    else:
        logger.info("外部问答 active：跳过 OpenClaw 后台任务")

    # 启动宇树事件转发失败队列的后台 drain 任务 (防数据丢失)
    try:
        from app.api.system import drain_unitree_event_queue
        asyncio.create_task(drain_unitree_event_queue())
        logger.info("[unitree-forward.queue] 后台 drain 任务已启动")
    except Exception as e:
        logger.warning("unitree event queue 后台任务启动失败: %s", e)

    # 同步 V5 资源 (展项/流程/设备/技能 ...) 到 OpenClaw agent workspace,
    # 让 LLM 看到真实的世界模型。失败不影响主路径 (V5 retriever 仍可工作)。
    # 用 background task 跑, 不阻塞启动 — knowledge 同步要打 7 次 WS, 5-10s。
    async def _sync_oc_knowledge():
        try:
            from app.sync.openclaw_knowledge import sync_to_openclaw
            r = await sync_to_openclaw()
            logger.info("OpenClaw 知识同步: %s", r)
        except Exception as e:
            logger.warning("OpenClaw 知识同步失败 (V5 主路径不受影响): %s", e)
    if not external_qa_active:
        asyncio.create_task(_sync_oc_knowledge())
    else:
        logger.info("外部问答 active：跳过 OpenClaw 知识同步")

    # 预热常用问句 cache: 启动时跑一次 + 每小时增量再跑一次, 写入 decision_cache。
    # 现场再次说同样问句直接命中, ≤30ms 出答案。后台异步, 不阻塞启动。
    async def _warmup_qa_cache_loop():
        from app.engine.qa_warmup import warmup_top_queries
        # 启动后第一次 5s 后跑 (等其它同步完成), top 100 / 7 天
        try:
            await asyncio.sleep(5)
            n = await warmup_top_queries(top_k=100, since_days=7)
            logger.info("QA 缓存预热完成: %d 条问句已写入 cache", n)
        except Exception as e:
            logger.warning("QA 缓存预热失败 (主路径不受影响): %s", e)
        # 每小时增量预热近 1 天 top 30 — 让今天新出现的高频问句也能进 cache
        while True:
            try:
                await asyncio.sleep(3600)
                n = await warmup_top_queries(top_k=30, since_days=1)
                logger.info("QA 缓存增量预热: %d 条问句", n)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug("QA 缓存增量预热异常: %s", e)
    if not external_qa_active:
        asyncio.create_task(_warmup_qa_cache_loop())
    else:
        logger.info("外部问答 active：跳过旧问答缓存预热")

    logger.info("系统启动完成 — HTTP: %s:%s | TCP: %s",
                config["server"]["host"],
                config["server"]["http_port"],
                config["server"]["tcp_port"])


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("系统正在关闭...")
    from app.tcp.server import stop_tcp_server
    await stop_tcp_server()
    from app.sync.scheduler import stop_sync_scheduler
    await stop_sync_scheduler()
    from app.engine.schedule_executor import stop_schedule_executor
    await stop_schedule_executor()
    try:
        from app.engine.visitor_service import stop_register_sweeper
        await stop_register_sweeper()
    except Exception:
        pass
    try:
        from app.engine import openclaw_client
        await openclaw_client.shutdown()
    except Exception as e:
        logger.debug("OpenClaw shutdown 忽略: %s", e)
    try:
        from app.engine.gateway_qa import close_gateway_qa_adapter
        await close_gateway_qa_adapter()
    except Exception as e:
        logger.debug("Gateway QA shutdown 忽略: %s", e)
    try:
        from app.engine.legacy_qa import close_legacy_qa_adapter
        await close_legacy_qa_adapter()
    except Exception as e:
        logger.debug("老系统问答适配器 shutdown 忽略: %s", e)
    try:
        from app.engine.legacy_teaching import close_legacy_draft_adapter
        from app.engine.teaching_runtime import close_teaching_runtimes
        await close_teaching_runtimes()
        await close_legacy_draft_adapter()
    except Exception as e:
        logger.debug("老系统教学适配器 shutdown 忽略: %s", e)
    logger.info("系统已关闭")


# ============================================================
# 命令行入口
# ============================================================
def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)
    setup_logging(config)

    uvicorn.run(
        "app.main:app",
        host=config["server"]["host"],
        port=config["server"]["http_port"],
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
