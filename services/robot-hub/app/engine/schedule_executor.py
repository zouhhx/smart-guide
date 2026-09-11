"""
定时任务执行器

从 schedules 表读取任务，按时执行。
支持: daily(cron), interval, once
支持工作日过滤: all / workday / weekend / holiday
"""

import asyncio
import json
import logging
import time
from datetime import datetime

from app.database import get_db
from app.config import get_config
from app.tcp.central_control import send_tcp_command

logger = logging.getLogger("hub.engine.schedule_executor")

_task = None


async def _execute_action(action_type: str, action_config: dict) -> bool:
    """执行定时任务的动作"""
    try:
        if action_type == "group_command":
            target = action_config.get("target", "")
            db = await get_db()
            try:
                row = await db.execute_fetchall(
                    "SELECT cmd_on FROM group_commands WHERE id=? OR name=?",
                    (target, target),
                )
                if not row or not row[0]["cmd_on"]:
                    logger.warning("[定时] 组策略 %s 无指令", target)
                    return False
                result = await send_tcp_command(row[0]["cmd_on"])
                return result.get("status") == "ok"
            finally:
                await db.close()

        elif action_type == "equipment":
            target = action_config.get("target", "")
            action = action_config.get("action", "on")
            db = await get_db()
            try:
                field = "cmd_on" if action == "on" else "cmd_off"
                row = await db.execute_fetchall(
                    f"SELECT {field} as cmd FROM equipments WHERE id=?",
                    (int(target),),
                )
                if not row or not row[0]["cmd"]:
                    return False
                result = await send_tcp_command(row[0]["cmd"])
                return result.get("status") == "ok"
            finally:
                await db.close()

        elif action_type == "special_switch":
            target = action_config.get("target", "")
            if not target:
                return False
            result = await send_tcp_command(f"2_update_{target}")
            # 更新数据库
            db = await get_db()
            try:
                await db.execute("UPDATE specials SET is_current=0")
                await db.execute(
                    "UPDATE specials SET is_current=1 WHERE id=?", (int(target),)
                )
                await db.commit()
            finally:
                await db.close()
            return result.get("status") == "ok"

        elif action_type == "custom_request":
            from app.engine.flow_executor import _step_custom_request
            return await _step_custom_request(action_config)

        else:
            logger.warning("[定时] 未知动作类型: %s", action_type)
            return False

    except Exception as e:
        logger.error("[定时] 执行异常: %s", e, exc_info=True)
        return False


def _parse_cron_next(expr: str) -> int:
    """简单 cron 解析: 返回距下次执行的秒数 (支持 '分 时 * * *' 格式)"""
    try:
        parts = expr.strip().split()
        if len(parts) < 5:
            return 60

        minute, hour = int(parts[0]), int(parts[1])
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if target <= now:
            # 已过今天时间，算明天
            from datetime import timedelta
            target += timedelta(days=1)

        return int((target - now).total_seconds())
    except (ValueError, IndexError):
        return 60


async def _schedule_loop():
    """主循环: 每分钟检查一次需要执行的任务"""
    await asyncio.sleep(10)  # 启动后等 10 秒

    while True:
        try:
            now = datetime.now()
            db = await get_db()
            try:
                rows = await db.execute_fetchall(
                    "SELECT * FROM schedules WHERE enabled=1"
                )
            finally:
                await db.close()

            for row in rows:
                schedule = dict(row)
                should_run = False
                schedule_type = schedule["schedule_type"]
                schedule_expr = schedule["schedule_expr"]

                if schedule_type == "daily":
                    # schedule_expr 是 cron 表达式 "分 时 * * *"
                    try:
                        parts = schedule_expr.strip().split()
                        minute, hour = int(parts[0]), int(parts[1])
                        if now.hour == hour and now.minute == minute:
                            should_run = True
                    except (ValueError, IndexError):
                        pass

                elif schedule_type == "interval":
                    # schedule_expr 是间隔分钟数
                    try:
                        interval_min = int(schedule_expr)
                        last_run = schedule.get("last_run")
                        if not last_run:
                            should_run = True
                        else:
                            last_dt = datetime.fromisoformat(str(last_run))
                            elapsed = (now - last_dt).total_seconds() / 60
                            if elapsed >= interval_min:
                                should_run = True
                    except (ValueError, TypeError):
                        pass

                elif schedule_type == "cron":
                    # 完整 cron 表达式 (简化: 只匹配分+时)
                    try:
                        parts = schedule_expr.strip().split()
                        minute, hour = int(parts[0]), int(parts[1])
                        if now.hour == hour and now.minute == minute:
                            should_run = True
                    except (ValueError, IndexError):
                        pass

                elif schedule_type == "once":
                    if not schedule.get("last_run"):
                        should_run = True

                if not should_run:
                    continue

                # 工作日过滤
                action_config = json.loads(schedule.get("action_config", "{}"))
                weekday_filter = action_config.get("weekday_filter", "all")

                if weekday_filter != "all":
                    from app.utils.workday import check_weekday_filter
                    if not await check_weekday_filter(weekday_filter):
                        logger.info(
                            "[定时] %s 跳过 (weekday_filter=%s, 今天不符合)",
                            schedule["name"], weekday_filter,
                        )
                        continue

                # 执行
                logger.info("[定时] 执行: %s (type=%s)", schedule["name"], schedule["action_type"])
                success = await _execute_action(schedule["action_type"], action_config)

                # 更新 last_run
                db = await get_db()
                try:
                    await db.execute(
                        "UPDATE schedules SET last_run=datetime('now','localtime') WHERE id=?",
                        (schedule["id"],),
                    )
                    if schedule_type == "once":
                        await db.execute(
                            "UPDATE schedules SET enabled=0 WHERE id=?",
                            (schedule["id"],),
                        )
                    await db.commit()
                finally:
                    await db.close()

                # 记录日志
                db = await get_db()
                try:
                    await db.execute(
                        """INSERT INTO execution_logs
                           (log_type, source, action, detail, status, created_at)
                           VALUES ('schedule', 'scheduler', ?, ?, ?, datetime('now','localtime'))""",
                        (
                            schedule["name"],
                            json.dumps(action_config, ensure_ascii=False),
                            "ok" if success else "error",
                        ),
                    )
                    await db.commit()
                finally:
                    await db.close()

        except Exception as e:
            logger.error("[定时] 主循环异常: %s", e, exc_info=True)

        # 每 60 秒检查一次
        await asyncio.sleep(60)


async def start_schedule_executor():
    """启动定时任务执行器"""
    global _task
    _task = asyncio.create_task(_schedule_loop())
    logger.info("定时任务执行器已启动")


async def stop_schedule_executor():
    """停止定时任务执行器"""
    global _task
    if _task:
        _task.cancel()
        _task = None
    logger.info("定时任务执行器已停止")
