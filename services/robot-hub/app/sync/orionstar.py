"""
OrionStar 云端 API 同步模块

仅用于拉取导航点位和地图信息。
机器人控制走 TCP 长连接，不走云端 API。
"""

import json
import time
import logging
import aiohttp

from app.config import get_config
from app.database import get_db

logger = logging.getLogger("hub.sync.orionstar")


class OrionStarSync:
    """OrionStar 点位同步器"""

    def __init__(self):
        self.config = get_config().get("orionstar", {})
        self.base_url = self.config.get("base_url", "")
        self.appid = self.config.get("appid", "")
        self.secret = self.config.get("secret", "")
        self.grant_type = self.config.get("grant_type", "client_credential")
        self.robot_sn = self.config.get("robot_sn", "")
        self.access_token = None
        self.token_expire_at = 0
        self.session = None

    async def _ensure_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # ============================================================
    # 认证
    # ============================================================
    async def get_token(self) -> bool:
        """获取 access_token"""
        now = time.time()
        if self.access_token and now < self.token_expire_at:
            return True

        await self._ensure_session()
        url = f"{self.base_url}/v1/auth/get_token"
        params = {
            "appid": self.appid,
            "secret": self.secret,
            "grant_type": self.grant_type,
        }

        try:
            async with self.session.get(url, params=params) as resp:
                result = await resp.json()
                if result.get("code") == 0 and result.get("data", {}).get("access_token"):
                    self.access_token = result["data"]["access_token"]
                    expire_in = int(result["data"].get("expire_in", 7200))
                    self.token_expire_at = now + max(expire_in - 60, 60)
                    logger.info("OrionStar 认证成功")
                    return True
                else:
                    logger.error("OrionStar 认证失败: %s", result.get("msg", result))
                    return False
        except Exception as e:
            logger.error("OrionStar 认证异常: %s", e)
            return False

    def _headers(self) -> dict:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }

    async def _get(self, path: str, params: dict = None) -> dict:
        """GET 请求"""
        await self._ensure_session()
        if not await self.get_token():
            return {}

        url = f"{self.base_url}{path}"
        try:
            async with self.session.get(url, headers=self._headers(), params=params) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("OrionStar GET %s 失败: %s", path, e)
            return {}

    # ============================================================
    # 地图
    # ============================================================
    async def get_map_list(self) -> list:
        """获取企业地图列表"""
        result = await self._get("/v1/corp/map_list")
        return result.get("data", []) if result.get("code") == 0 else []

    async def get_map_positions(self, map_id: str) -> list:
        """获取地图点位列表"""
        result = await self._get(
            "/v1/corp/map_position_list", params={"map_id": map_id}
        )
        return result.get("data", []) if result.get("code") == 0 else []

    async def get_robot_positions(self) -> list:
        """获取机器人当前地图点位列表
        返回结构: data.position_list[].position.{pos_id, pos_name, coords_x, ...}
        """
        result = await self._get(
            "/v1/robot/map/position_list", params={"robot_sn": self.robot_sn}
        )
        if result.get("code") != 0:
            return []
        data = result.get("data", {})
        if isinstance(data, dict):
            pos_list = data.get("position_list", [])
            # 每个元素是 {"position": {...}}
            return [item.get("position", item) for item in pos_list if isinstance(item, dict)]
        return data if isinstance(data, list) else []

    # ============================================================
    # 机器人信息
    # ============================================================
    async def get_robot_list(self) -> list:
        """获取机器人列表"""
        result = await self._get("/v1/robot/robot_list")
        return result.get("data", []) if result.get("code") == 0 else []

    async def get_robot_info(self) -> dict:
        """获取机器人详情"""
        result = await self._get(
            "/v1/robot/robot_info", params={"robot_sn": self.robot_sn}
        )
        return result.get("data", {}) if result.get("code") == 0 else {}

    # ============================================================
    # 完整同步
    # ============================================================
    async def sync_positions(self) -> dict:
        """
        同步导航点位到本地数据库

        流程：
        1. 获取地图列表
        2. 遍历每个地图拉取点位
        3. 入库
        4. 若本次 sync 只涉及一张地图 (活地图模式), 自动迁移其他旧地图的
           position_bindings + tour_script_steps.config_json 到同名活点位,
           然后删除旧地图行 — 防止"换地图后历史绑定失效"。
        """
        stats = {"maps": 0, "positions": 0, "migrated_bindings": 0,
                 "migrated_scripts": 0, "purged_obsolete": 0, "errors": []}

        if not await self.get_token():
            stats["errors"].append("认证失败")
            return stats

        db = await get_db()
        try:
            # 先尝试获取机器人地图点位
            positions = await self.get_robot_positions()
            single_map_mode = bool(positions)  # robot 接口只返回活地图 → 此时启用自动清理

            if not positions:
                # fallback: 遍历企业地图 (多地图模式, 不会触发自动清理)
                maps = await self.get_map_list()
                for m in (maps if isinstance(maps, list) else []):
                    map_id = m.get("map_id", m.get("id", ""))
                    map_name = m.get("map_name", m.get("name", ""))
                    if not map_id:
                        continue
                    stats["maps"] += 1

                    map_positions = await self.get_map_positions(str(map_id))
                    for p in (map_positions if isinstance(map_positions, list) else []):
                        p["_map_id"] = str(map_id)
                        p["_map_name"] = map_name
                        positions.append(p)

            # 入库 — 字段映射兼容两种格式:
            #   OrionStar: pos_id, pos_name, coords_x, coords_y, coords_t, map_id, map_name
            #   通用:     position_id, position_name, x, y, theta
            active_map_ids: set[str] = set()
            active_map_names: set[str] = set()
            for p in (positions if isinstance(positions, list) else []):
                pos_id = str(
                    p.get("pos_id") or p.get("position_id") or p.get("id", "")
                )
                pos_name = (
                    p.get("pos_name") or p.get("position_name") or p.get("name", "")
                )
                if not pos_id or not pos_name:
                    continue
                map_id_str = str(p.get("map_id", p.get("_map_id", "")))
                map_name_str = p.get("map_name", p.get("_map_name", ""))

                await db.execute(
                    """INSERT OR REPLACE INTO nav_positions
                       (position_id, name, map_id, map_name, x, y, theta, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pos_id,
                        pos_name,
                        map_id_str,
                        map_name_str,
                        float(p.get("coords_x", p.get("x", 0))),
                        float(p.get("coords_y", p.get("y", 0))),
                        float(p.get("coords_t", p.get("theta", 0))),
                        json.dumps(p, ensure_ascii=False),
                    ),
                )
                stats["positions"] += 1
                if map_id_str:
                    active_map_ids.add(map_id_str)
                if map_name_str:
                    active_map_names.add(map_name_str)

            # 单地图(活地图)模式 → 自动迁移 + 清理旧地图
            if single_map_mode and len(active_map_ids) == 1 and stats["positions"] > 0:
                active_map_id = next(iter(active_map_ids))
                active_map_name = next(iter(active_map_names)) if active_map_names else ""
                migrated_b, migrated_s, purged = await self._migrate_and_purge_obsolete(
                    db, active_map_id,
                )
                stats["migrated_bindings"] = migrated_b
                stats["migrated_scripts"] = migrated_s
                stats["purged_obsolete"] = purged
                # 写入 active_map_id 系统配置, 供 admin UI / 其他模块查询
                await db.execute(
                    """INSERT OR REPLACE INTO system_config (key, value, description)
                       VALUES ('active_map_id', ?, '当前活地图 ID (OrionStar)')""",
                    (active_map_id,),
                )
                if active_map_name:
                    await db.execute(
                        """INSERT OR REPLACE INTO system_config (key, value, description)
                           VALUES ('active_map_name', ?, '当前活地图名 (OrionStar)')""",
                        (active_map_name,),
                    )

            await db.commit()
            logger.info(
                "OrionStar 点位同步完成: %d 个点位, 迁移绑定=%d, 迁移脚本=%d, 清理旧点位=%d",
                stats["positions"], stats.get("migrated_bindings", 0),
                stats.get("migrated_scripts", 0), stats.get("purged_obsolete", 0),
            )

        except Exception as e:
            logger.error("OrionStar 同步异常: %s", e, exc_info=True)
            stats["errors"].append(str(e))
        finally:
            await db.close()
            await self.close()

        return stats

    async def _migrate_and_purge_obsolete(self, db, active_map_id: str) -> tuple[int, int, int]:
        """活地图模式下: 把旧地图同名点位的引用迁到活地图, 然后删除旧点位.

        返回 (迁移的绑定数, 迁移的脚本步骤数, 删除的旧点位数)。

        - position_bindings.position_id: 直接 UPDATE 到活地图同名 position_id
        - tour_script_steps.config_json: REPLACE 旧 pid 字面量为新 pid
        - 找不到同名替代时: 留着旧点位 (避免引用变成死链), 仅日志告警
        """
        # 找出所有不在活地图的旧点位
        obsolete_rows = await db.execute_fetchall(
            "SELECT position_id, name FROM nav_positions WHERE map_id != ?",
            (active_map_id,),
        )
        if not obsolete_rows:
            return (0, 0, 0)

        # 活地图按 name → position_id 索引
        active_rows = await db.execute_fetchall(
            "SELECT position_id, name FROM nav_positions WHERE map_id = ?",
            (active_map_id,),
        )
        name_to_active_pid = {r["name"]: r["position_id"] for r in active_rows}

        migrated_bindings = 0
        migrated_scripts = 0
        purged = 0
        unmatched_kept = 0

        for ob in obsolete_rows:
            old_pid = ob["position_id"]
            name = ob["name"]
            new_pid = name_to_active_pid.get(name)

            if not new_pid:
                logger.warning(
                    "[OrionStar] 旧点位 %s '%s' 在新地图无同名替代, 保留以免引用断链",
                    old_pid, name,
                )
                unmatched_kept += 1
                continue

            # 1. position_bindings 重定向
            cur = await db.execute(
                "UPDATE position_bindings SET position_id=? WHERE position_id=?",
                (new_pid, old_pid),
            )
            migrated_bindings += (cur.rowcount or 0) if hasattr(cur, "rowcount") else 0

            # 2. tour_script_steps.config_json 字面量替换
            #    pos_id 是 32 字符 hex, 唯一性强, REPLACE 不会误伤
            cur = await db.execute(
                "UPDATE tour_script_steps SET config_json = REPLACE(config_json, ?, ?) "
                "WHERE config_json LIKE '%' || ? || '%'",
                (old_pid, new_pid, old_pid),
            )
            migrated_scripts += (cur.rowcount or 0) if hasattr(cur, "rowcount") else 0

            # 3. 删除旧点位行
            await db.execute(
                "DELETE FROM nav_positions WHERE position_id=?",
                (old_pid,),
            )
            purged += 1

        if unmatched_kept:
            logger.warning("[OrionStar] %d 个旧点位无同名替代, 已保留 (避免死引用)", unmatched_kept)

        return (migrated_bindings, migrated_scripts, purged)
