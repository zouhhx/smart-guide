"""
配置加载器
"""
import yaml
import os
import logging

logger = logging.getLogger("hub.config")

_config = None


def load_config(path: str = None) -> dict:
    """加载配置文件"""
    global _config
    if _config and not path:
        return _config

    if not path:
        # 按优先级查找配置文件
        candidates = [
            "config.yaml",
            "/opt/exhibition-hub/config.yaml",
            os.path.join(os.path.dirname(__file__), "..", "config.yaml"),
        ]
        for c in candidates:
            if os.path.exists(c):
                path = c
                break

    if not path or not os.path.exists(path):
        logger.warning("配置文件未找到，使用默认配置")
        _config = _default_config()
        return _config

    with open(path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)

    logger.info("配置已加载: %s", path)
    return _config


def get_config() -> dict:
    """获取当前配置"""
    if not _config:
        return load_config()
    return _config


def _default_config() -> dict:
    return {
        "server": {"host": "0.0.0.0", "http_port": 8000, "tcp_port": 19888},
        "database": {"path": "data/exhibition_hub.db"},
        "waterdrop": {
            "base_url": "http://192.168.18.65:8866",
            "account": "ZhanGuan",
            "password": os.getenv("V5_WATERDROP_PASSWORD", ""),
            "hall_id": 5,
            "sync_interval_minutes": 5,
        },
        "central_control": {
            "host": "192.168.16.18",
            "port": 8989,
            "command_interval_seconds": 2,
            "retry_count": 3,
        },
        "orionstar": {
            "base_url": "https://openapi.orionstar.com",
            "appid": "ori-6893a1766d015e4b6236abc15a24",
            "secret": os.getenv("V5_ORIONSTAR_SECRET", ""),
            "grant_type": "client_credential",
            "robot_sn": "MC1BCN2K100262058CA0",
            "sync_interval_minutes": 30,
        },
        "llm": {
            "base_url": "https://zhenze-huhehaote.cmecloud.cn/v1/chat/completions",
            "api_key": os.getenv("V5_LLM_API_KEY", ""),
            "model": "minimax-m2.5",
            "timeout_seconds": 15,
        },
        "gateway_qa": {
            "mode": "off",
            "base_url": "http://192.168.22.231:8780",
            "bearer_token_env": "V5_GATEWAY_CLIENT_TOKEN",
            "tenant_id": "",
            "site_id": "",
            "knowledge_base_id": "",
            "locale": "zh-CN",
            "web_enabled": False,
            "request_timeout_seconds": 18,
            "max_attempts": 2,
            "target_screen_id": "",
            "default_current_unit_id": "",
            "target_screen_by_robot": {},
            "default_current_unit_by_robot": {},
        },
        "legacy_qa": {
            "mode": "off",
            "endpoint": "http://192.168.16.61:3001/api/asr-text",
            "target": "2",
            "source": "microphone",
            "timeout_seconds": 18,
        },
        "legacy_teaching": {
            "enabled": False,
            "draft_endpoint": "http://192.168.16.61:3001/api/knowledge/qa-drafts",
            "bearer_token_env": "V5_LEGACY_QA_DRAFT_TOKEN",
            "session_timeout_seconds": 120,
            "request_timeout_seconds": 10,
            "max_attempts": 2,
            "max_fragments": 50,
            "max_answer_chars": 4000,
        },
        "robot": {
            # wake_word 已固定为"小豹小豹"，不再通过配置下发
            "heartbeat_interval": 30,
            "listen_timeout": 30,
            "listen_language": "zh",
            "heartbeat_miss_limit": 3,
        },
        "logging": {
            "level": "INFO",
            "file": "logs/hub.log",
            "max_size_mb": 50,
            "backup_count": 5,
        },
    }
