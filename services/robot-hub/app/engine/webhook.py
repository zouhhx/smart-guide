"""
Webhook 告警模块

支持事件类型:
- robot_offline: 机器人离线
- robot_low_battery: 低电量
- robot_error: 硬件异常
- device_error: 设备异常
- schedule_failed: 定时任务失败

配置项 (system_config):
- webhook_enabled: "0"/"1"
- webhook_url: webhook 地址
- webhook_events: 逗号分隔的事件类型，如 "robot_offline,robot_error"
"""

import asyncio
import json
import logging
import urllib.request

from app.database import get_db

logger = logging.getLogger("hub.engine.webhook")

_config_cache = None
_cache_time = 0


async def _get_webhook_config() -> dict:
    """从数据库读取 webhook 配置"""
    global _config_cache, _cache_time
    import time
    now = time.time()
    # 缓存 60 秒
    if _config_cache and now - _cache_time < 60:
        return _config_cache

    config = {
        "webhook_enabled": "0",
        "webhook_url": "",
        "webhook_events": "robot_offline,robot_error,robot_low_battery",
    }
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT key, value FROM system_config WHERE key LIKE 'webhook_%'"
            )
            for r in rows:
                if r["value"]:
                    config[r["key"]] = r["value"]
        finally:
            await db.close()
    except Exception as e:
        logger.warning("读取 webhook 配置失败: %s", e)

    _config_cache = config
    _cache_time = now
    return config


async def send_alert(event_type: str, title: str, detail: str = "", extra: dict = None):
    """发送告警到 webhook"""
    config = await _get_webhook_config()

    if config.get("webhook_enabled") != "1":
        return

    url = config.get("webhook_url", "").strip()
    if not url:
        return

    allowed_events = [e.strip() for e in config.get("webhook_events", "").split(",")]
    if event_type != "test" and event_type not in allowed_events:
        return

    payload = {
        "event": event_type,
        "title": title,
        "detail": detail,
        "extra": extra or {},
    }

    # 尝试适配企业微信/钉钉/飞书
    if "qyapi.weixin" in url or "wecom" in url:
        # 企业微信格式
        body = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"**🚨 {title}**\n\n{detail}\n\n> 事件类型: {event_type}"
            }
        }
    elif "dingtalk" in url or "oapi.dingtalk" in url:
        # 钉钉格式
        body = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"🚨 {title}",
                "text": f"## 🚨 {title}\n\n{detail}\n\n事件: {event_type}"
            }
        }
    elif "feishu" in url or "larksuite" in url:
        # 飞书格式
        body = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": f"🚨 {title}"}},
                "elements": [{"tag": "markdown", "content": detail}]
            }
        }
    else:
        # 通用 JSON
        body = payload

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: urllib.request.urlopen(req, timeout=10),
        )
        logger.info("[Webhook] 告警已发送: %s - %s", event_type, title)
    except Exception as e:
        logger.warning("[Webhook] 发送失败: %s", e)
