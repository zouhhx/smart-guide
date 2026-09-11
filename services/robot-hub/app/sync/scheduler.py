"""
定时同步调度器

启动后在后台定期拉取：
- 水滴云数据（设备/终端/专场/资源）
- OrionStar 导航点位
- 宇树机器人 动作库/舞蹈库/地图 (间隔由 system_config.unitree_sync_interval_min 控制)
"""

import asyncio
import logging

from app.config import get_config

logger = logging.getLogger("hub.sync.scheduler")

_tasks: list = []


async def _waterdrop_sync_loop():
    """水滴云定时同步"""
    config = get_config().get("waterdrop", {})
    interval = config.get("sync_interval_minutes", 5) * 60

    while True:
        await asyncio.sleep(interval)
        try:
            from app.sync.waterdrop import WaterdropSync
            sync = WaterdropSync()
            stats = await sync.full_sync()
            logger.info("[定时] 水滴云同步完成: %s", stats)
        except Exception as e:
            logger.warning("[定时] 水滴云同步失败: %s", e)


async def _orionstar_sync_loop():
    """OrionStar 点位定时同步"""
    config = get_config().get("orionstar", {})
    interval = config.get("sync_interval_minutes", 30) * 60

    while True:
        await asyncio.sleep(interval)
        try:
            from app.sync.orionstar import OrionStarSync
            sync = OrionStarSync()
            stats = await sync.sync_positions()
            logger.info("[定时] OrionStar 点位同步完成: %s", stats)
        except Exception as e:
            logger.warning("[定时] OrionStar 点位同步失败: %s", e)


async def _unitree_sync_loop():
    """宇树机器人 动作库/舞蹈库/地图 定时同步 (间隔从 system_config 取)。"""
    from app.database import get_db
    from app.sync.unitree import UnitreeSync

    async def _read_interval_min() -> int:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='unitree_sync_interval_min'"
            )
            if not rows:
                return 60
            try:
                return max(5, int(rows[0]["value"]))
            except (TypeError, ValueError):
                return 60
        finally:
            await db.close()

    # 启动时先做一次, 避免新装机后第一个周期内下拉框全空
    try:
        stats = await UnitreeSync().full_sync()
        logger.info("[定时] 宇树首次同步完成: %s", stats)
    except Exception as e:
        logger.warning("[定时] 宇树首次同步失败: %s", e)

    # 连续失败计数 → 指数退避 (避免网关下线时刷屏)
    fail_count = 0
    BACKOFF_CAP_SEC = 600  # 10 分钟封顶
    while True:
        interval = await _read_interval_min() * 60
        if fail_count > 0:
            backoff = min(BACKOFF_CAP_SEC, interval * (2 ** fail_count))
            sleep_sec = backoff
            if fail_count % 5 == 1:   # 每 5 次失败提示一次, 减少 log spam
                logger.info("[定时] 宇树同步连续失败 %d 次, 退避 %ds 后重试",
                            fail_count, sleep_sec)
        else:
            sleep_sec = interval
        await asyncio.sleep(sleep_sec)
        try:
            stats = await UnitreeSync().full_sync()
            logger.info("[定时] 宇树同步完成: %s", stats)
            fail_count = 0
        except Exception as e:
            fail_count += 1
            # 只在第 1 次和每 10 次失败时 warning, 中间走 debug 减噪
            if fail_count == 1 or fail_count % 10 == 0:
                logger.warning("[定时] 宇树同步失败 (累计 %d 次): %s", fail_count, e)
            else:
                logger.debug("[定时] 宇树同步失败 (累计 %d 次): %s", fail_count, e)


async def start_sync_scheduler():
    """启动所有定时同步任务"""
    config = get_config()

    wd_interval = config.get("waterdrop", {}).get("sync_interval_minutes", 5)
    os_interval = config.get("orionstar", {}).get("sync_interval_minutes", 30)

    t1 = asyncio.create_task(_waterdrop_sync_loop())
    t2 = asyncio.create_task(_orionstar_sync_loop())
    t3 = asyncio.create_task(_unitree_sync_loop())
    _tasks.extend([t1, t2, t3])

    logger.info(
        "定时同步已启动: 水滴云=%d分钟, OrionStar=%d分钟, 宇树=读自DB",
        wd_interval,
        os_interval,
    )


async def stop_sync_scheduler():
    """停止所有定时同步任务"""
    for t in _tasks:
        t.cancel()
    _tasks.clear()
    logger.info("定时同步已停止")
