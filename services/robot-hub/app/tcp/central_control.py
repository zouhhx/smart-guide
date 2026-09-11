"""
中控服务器 TCP 客户端

短连接模式：建立连接 → 发送指令 → 立即断开
全局2秒指令间隔
重试3次
"""

import asyncio
import time
import logging

from app.config import get_config
from app.database import get_db

logger = logging.getLogger("hub.tcp.central_control")

# 全局指令间隔控制
_last_send_time = 0.0
_send_lock = asyncio.Lock()


async def send_tcp_command(command: str) -> dict:
    """
    发送 TCP 指令到中控服务器

    短连接：连接 → 发送 → 断开
    全局锁保证 2 秒指令间隔
    发送后服务器不回复（单向）

    Returns:
        {"status": "ok", "command": "...", "duration_ms": N}
        或
        {"status": "error", "command": "...", "error": "..."}
    """
    global _last_send_time

    config = get_config().get("central_control", {})
    host = config.get("host", "192.168.16.18")
    port = config.get("port", 8989)
    interval = config.get("command_interval_seconds", 2)
    retry_count = config.get("retry_count", 3)

    async with _send_lock:
        # 全局指令间隔
        now = time.time()
        wait = interval - (now - _last_send_time)
        if wait > 0:
            logger.debug("指令间隔等待 %.1fs", wait)
            await asyncio.sleep(wait)

        start = time.time()
        last_error = None

        for attempt in range(1, retry_count + 1):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=5,
                )

                writer.write(command.encode("utf-8"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()

                duration_ms = int((time.time() - start) * 1000)
                _last_send_time = time.time()

                logger.info("[中控] 发送成功: %s (耗时 %dms)", command, duration_ms)

                # 记录日志
                await _log_command(command, "ok", duration_ms)

                return {
                    "status": "ok",
                    "command": command,
                    "duration_ms": duration_ms,
                }

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "[中控] 发送失败 (第%d次): %s → %s",
                    attempt, command, e,
                )
                if attempt < retry_count:
                    await asyncio.sleep(2)

        # 全部重试失败
        duration_ms = int((time.time() - start) * 1000)
        _last_send_time = time.time()
        logger.error("[中控] 发送最终失败: %s → %s", command, last_error)
        await _log_command(command, "error", duration_ms, last_error)

        return {
            "status": "error",
            "command": command,
            "error": last_error,
            "duration_ms": duration_ms,
        }


async def send_commands_sequence(commands: list) -> list:
    """
    按顺序发送多条指令（自动间隔2秒）

    用于场景如：先切专场再播放资源
    """
    results = []
    for cmd in commands:
        result = await send_tcp_command(cmd)
        results.append(result)
        if result["status"] == "error":
            logger.warning("序列发送中断: %s 失败", cmd)
            break
    return results


async def _log_command(command: str, status: str, duration_ms: int, error: str = ""):
    """记录执行日志到数据库"""
    try:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO execution_logs
                   (log_type, source, target, action, detail, status, duration_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))""",
                (
                    "tcp_command",
                    "central_control",
                    "192.168.16.18:8989",
                    command,
                    error or "",
                    status,
                    duration_ms,
                ),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        logger.warning("日志记录失败: %s", e)
