"""
本机 LAN IP / 自动外发 URL 工具.

用途: 音乐播放等场景需要把 hub 自己的 HTTP 地址塞进 evt_music_request 推给 APK,
APK 跟 hub 同一 LAN, 直接拉 http://{lan_ip}:{port}/media/... 即可.

策略:
  - 用经典 socket trick "假连一个外网地址, 看 OS 给我选了哪个本地网卡 IP",
    不发包, 仅触发 OS 路由表查询.
  - 结果缓存; 网卡 IP 一般启动期就固定, 没必要每次都查.
  - 拿不到就回 "127.0.0.1" (异常情况, 至少不让上层崩).
"""

import logging
import socket
from typing import Optional

from app.config import get_config

logger = logging.getLogger("hub.engine.host")

_cached_ip: Optional[str] = None
_cached_port: Optional[int] = None


def _detect_lan_ip() -> str:
    """探测本机出网网卡的 IPv4."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 8.8.8.8 / Cloudflare 都行 — 不实际发包, 只让 OS 选源地址
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception as e:
        logger.warning("[host] 探测 LAN IP 失败, 回退 127.0.0.1: %s", e)
        return "127.0.0.1"
    finally:
        try:
            s.close()
        except Exception:
            pass


def get_lan_ip() -> str:
    global _cached_ip
    if _cached_ip is None:
        _cached_ip = _detect_lan_ip()
        logger.info("[host] LAN IP 探测结果: %s", _cached_ip)
    return _cached_ip


def get_http_port() -> int:
    global _cached_port
    if _cached_port is None:
        try:
            _cached_port = int(get_config().get("server", {}).get("http_port", 8000))
        except Exception:
            _cached_port = 8000
    return _cached_port


def get_music_url_base() -> str:
    """默认音乐 URL 前缀: http://{lan_ip}:{http_port} (无尾部斜杠)."""
    return f"http://{get_lan_ip()}:{get_http_port()}"
