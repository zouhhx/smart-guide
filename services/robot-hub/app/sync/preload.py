"""
数据预设模块

首次启动时自动导入预设数据，让系统"开箱即用"。
包含：默认意图映射、默认流程模板、默认系统配置。

业务数据（展馆/展区/展项/专场/资源/设备/组策略）从水滴云同步获取。
导航点位从 OrionStar 同步获取。
"""

import json
import logging

from app.database import get_db

logger = logging.getLogger("hub.sync.preload")


async def preload_defaults():
    """
    预设默认数据

    只在表为空时才插入，避免重复预设覆盖用户配置。
    """
    db = await get_db()
    try:
        await _preload_intent_mappings(db)
        await _preload_tour_scripts(db)
        await db.commit()
        logger.info("默认数据预设完成")
    finally:
        await db.close()


async def _preload_intent_mappings(db):
    """预设常用意图命令映射"""
    existing = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM intent_command_mappings")
    if existing[0]["cnt"] > 0:
        logger.info("意图映射已有数据，跳过预设")
        return

    defaults = [
        {
            "intent_name": "lights_on",
            "display_name": "开灯",
            "command_type": "group_command",
            "command_target": "light",
            "command_action": "on",
            "reply_text": "好的，灯光已打开",
        },
        {
            "intent_name": "lights_off",
            "display_name": "关灯",
            "command_type": "group_command",
            "command_target": "light",
            "command_action": "off",
            "reply_text": "好的，灯光已关闭",
        },
        {
            "intent_name": "all_on",
            "display_name": "全部打开",
            "command_type": "group_command",
            "command_target": "com",
            "command_action": "on",
            "reply_text": "好的，所有设备已打开",
        },
        {
            "intent_name": "all_off",
            "display_name": "全部关闭",
            "command_type": "group_command",
            "command_target": "com",
            "command_action": "off",
            "reply_text": "好的，所有设备已关闭",
        },
        {
            "intent_name": "ad_screen_on",
            "display_name": "开广告机",
            "command_type": "group_command",
            "command_target": "ggj",
            "command_action": "on",
            "reply_text": "好的，广告机已打开",
        },
        {
            "intent_name": "ad_screen_off",
            "display_name": "关广告机",
            "command_type": "group_command",
            "command_target": "ggj",
            "command_action": "off",
            "reply_text": "好的，广告机已关闭",
        },
    ]

    for d in defaults:
        await db.execute(
            """INSERT OR IGNORE INTO intent_command_mappings
               (intent_name, display_name, command_type, command_target,
                command_action, reply_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (d["intent_name"], d["display_name"], d["command_type"],
             d["command_target"], d["command_action"], d["reply_text"]),
        )
    logger.info("预设 %d 条意图映射", len(defaults))


async def _preload_tour_scripts(db):
    """预设参观流程模板"""
    existing = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM tour_scripts")
    if existing[0]["cnt"] > 0:
        logger.info("流程模板已有数据，跳过预设")
        return

    templates = [
        {
            "name": "日常参观讲解",
            "trigger_word": "开始日常参观讲解",
            "description": "日常开馆的标准参观路线，机器人带领访客依次参观各展区",
            "steps": [
                {"step_type": "tts", "config": {"text": "欢迎来到展馆，现在为您开始日常参观讲解"}, "wait_type": "callback", "wait_timeout": 30, "description": "欢迎词"},
                {"step_type": "navigate", "config": {"position": ""}, "wait_type": "callback", "wait_timeout": 60, "description": "导航到序厅入口"},
                {"step_type": "tts", "config": {"text": "这里是序厅，请看屏幕了解展馆概览"}, "wait_type": "callback", "wait_timeout": 30, "description": "序厅介绍"},
                {"step_type": "delay", "config": {"seconds": "30"}, "wait_type": "delay", "wait_timeout": 30, "description": "等待观众观看"},
                {"step_type": "tts", "config": {"text": "感谢参观，祝您愉快"}, "wait_type": "callback", "wait_timeout": 30, "description": "结束语"},
                {"step_type": "end", "config": {}, "wait_type": "none", "wait_timeout": 0, "description": "流程结束"},
            ],
        },
        {
            "name": "VIP贵宾接待",
            "trigger_word": "开始贵宾接待流程",
            "description": "VIP贵宾专属接待流程，包含专场切换和重点展项讲解",
            "steps": [
                {"step_type": "tts", "config": {"text": "尊贵的来宾您好，欢迎来到展馆，我将为您提供专属讲解服务"}, "wait_type": "callback", "wait_timeout": 30, "description": "VIP欢迎词"},
                {"step_type": "end", "config": {}, "wait_type": "none", "wait_timeout": 0, "description": "流程结束"},
            ],
        },
    ]

    for t in templates:
        cursor = await db.execute(
            """INSERT INTO tour_scripts (name, trigger_word, description)
               VALUES (?, ?, ?)""",
            (t["name"], t["trigger_word"], t["description"]),
        )
        script_id = cursor.lastrowid
        for i, step in enumerate(t["steps"]):
            await db.execute(
                """INSERT INTO tour_script_steps
                   (script_id, step_order, step_type, config_json,
                    wait_type, wait_timeout, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    script_id, i + 1, step["step_type"],
                    json.dumps(step["config"], ensure_ascii=False),
                    step["wait_type"], step["wait_timeout"], step["description"],
                ),
            )
    logger.info("预设 %d 个流程模板", len(templates))
