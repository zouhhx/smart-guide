"""
宇树机器人 HTTP 网关客户端

对接 robot_api_gateway v2.0 (FastAPI + ROS2 Humble)，通过统一 /api/common 端点调用。

要点：
- HTTP 状态码恒为 200，业务错误由 body.code 表达（0=成功）。
- 配置从 system_config 表读取: unitree_base_url / unitree_api_key / unitree_enabled。
- 同一能力不要并发下发（TTS/Nav 内部是单 trace 模型）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

from app.database import get_db

logger = logging.getLogger("hub.engine.unitree")


class UnitreeGatewayError(Exception):
    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"[{code}] {msg}")


async def _load_gateway_config() -> tuple[str, str, bool]:
    """读取 system_config 里的网关配置 (base_url, api_key, enabled)。"""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT key, value FROM system_config WHERE key IN "
            "('unitree_base_url','unitree_api_key','unitree_enabled')"
        )
        cfg = {r["key"]: r["value"] for r in rows}
    finally:
        await db.close()
    base_url = (cfg.get("unitree_base_url") or "http://192.168.23.253:8000").rstrip("/")
    api_key = cfg.get("unitree_api_key") or ""
    enabled = str(cfg.get("unitree_enabled", "1")).strip() not in ("0", "false", "False", "")
    return base_url, api_key, enabled


class UnitreeClient:
    """宇树网关客户端 — 按需创建，一次请求用完即关。"""

    def __init__(self, base_url: str, api_key: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        }
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    @classmethod
    async def from_config(cls, timeout: float = 20.0) -> Optional["UnitreeClient"]:
        """按系统配置构造客户端；若 unitree_enabled=0 返回 None。"""
        base_url, api_key, enabled = await _load_gateway_config()
        if not enabled:
            logger.info("[宇树] 网关已禁用 (unitree_enabled=0)")
            return None
        return cls(base_url, api_key, timeout=timeout)

    # --- 底层 ---
    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> Any:
        url = self.base_url + path
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.request(
                method, url, headers=self.headers, params=params, json=json_body
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception as e:
                    text = (await resp.text())[:200]
                    raise UnitreeGatewayError(-1, f"非JSON响应: {text} ({e})")
        code = data.get("code", -1)
        if code != 0:
            raise UnitreeGatewayError(code, data.get("msg", "unknown"))
        return data.get("data")

    async def common(self, action: str, *, params: Optional[dict] = None, **kwparams) -> Any:
        """统一入口: POST /api/common { action, params }。

        用法二选一:
          - kwargs 形式 (原有): client.common("dance.select", dance_id="F1X")
          - dict 形式 (推荐给透传入口): client.common("x", params={"action": "cancel", ...})
            — 当 params 里含有和本函数签名同名的 key (如 action) 时, 必须用 dict 形式,
              否则会触发 "multiple values for keyword argument" TypeError.
        """
        body_params = params if params is not None else kwparams
        body = {"action": action, "params": body_params}
        return await self._call("POST", "/api/common", json_body=body)

    # --- TTS ---
    async def tts_play(self, text: str) -> str:
        d = await self._call("POST", "/api/tts", json_body={"text": text})
        return d["trace_id"]

    async def tts_stop(self) -> None:
        await self._call("DELETE", "/api/tts")

    async def tts_volume_set(self, volume: int) -> None:
        await self._call("PUT", "/api/tts/volume", json_body={"audio_volume": int(volume)})

    # --- ASR ---
    async def asr_start(self) -> Any:
        return await self._call("POST", "/api/asr")

    async def asr_stop(self) -> Any:
        return await self._call("DELETE", "/api/asr")

    async def asr_status(self) -> Any:
        return await self._call("GET", "/api/asr")

    # --- 地图 ---
    async def map_list(self) -> Any:
        return await self._call("GET", "/api/map/list")

    async def map_detail(self, map_id: str) -> Any:
        # 注意: detail 带副作用(置当前工作地图+开避障)
        return await self._call("GET", "/api/map/detail", params={"map_id": str(map_id)})

    async def map_refresh(self) -> Any:
        return await self._call("POST", "/api/map/refresh")

    # --- 导航 ---
    async def nav_goto(
        self, map_id: str, point_id: int, task_id: Optional[str] = None
    ) -> str:
        body: dict = {
            "current_working_map_id": str(map_id),
            "point_id": int(point_id),
        }
        if task_id:
            body["task_id"] = task_id
        d = await self._call("POST", "/api/nav/planning-to-goal", json_body=body)
        return d["task_id"]

    async def nav_task_control(self, task_id: str, action: str) -> Any:
        return await self._call(
            "POST",
            "/api/nav/task-control",
            json_body={"task_id": task_id, "action": action},
        )

    async def nav_status(self, task_id: Optional[str] = None) -> Any:
        params = {"task_id": task_id} if task_id else None
        return await self._call("GET", "/api/nav/status", params=params)

    # --- 人脸识别 ---
    async def face_start(self, group_id: Optional[int] = None) -> Any:
        params = {"group_id": int(group_id)} if group_id else None
        return await self._call("POST", "/api/face-recognition", params=params)

    async def face_stop(self) -> Any:
        return await self._call("DELETE", "/api/face-recognition")

    async def face_status(self) -> Any:
        return await self._call("GET", "/api/face-recognition")

    # --- 动作 / 舞蹈（只能走 /api/common） ---
    async def motion_play(self, motion_id: int) -> Any:
        return await self.common("motions.playback.create", motions=[int(motion_id)])

    async def motion_list(self) -> Any:
        return await self.common("motions.list")

    async def dance_select(self, dance_id: str) -> Any:
        return await self.common("dance.select", dance_id=str(dance_id))

    async def dance_stop(self) -> Any:
        return await self.common("dance.stop")

    async def dance_list(self) -> Any:
        return await self.common("dance.list")

    # --- 运维 ---
    async def health(self) -> Any:
        url = self.base_url + "/health"
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(url) as resp:
                return await resp.json(content_type=None)
