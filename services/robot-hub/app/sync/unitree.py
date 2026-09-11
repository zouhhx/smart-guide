"""
宇树机器人数据同步

从宇树 HTTP 网关拉取动作库 / 舞蹈库 / 地图 / 点位到本地数据库，
给流程编辑器的下拉框选择用。

调用的 gateway action:
- motions.list         → unitree_motions
- dance.list           → unitree_dances
- map.list             → unitree_maps
- map.detail(map_id)   → unitree_map_points  (detail 带副作用: 置当前工作地图)

注意: map.detail 会把被查询的地图置为"当前工作地图"并开避障。同步逻辑只对
      已标记 is_current 的地图拉取点位, 避免干扰机器人工作状态。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.database import get_db
from app.engine.unitree_client import UnitreeClient, UnitreeGatewayError

logger = logging.getLogger("hub.sync.unitree")


class UnitreeSync:
    """宇树机器人数据同步器。"""

    def __init__(self, client: UnitreeClient | None = None):
        self.client = client

    async def _ensure_client(self) -> UnitreeClient | None:
        if self.client:
            return self.client
        self.client = await UnitreeClient.from_config()
        return self.client

    # ============================================================
    # 动作库
    # ============================================================
    async def sync_motions(self) -> int:
        client = await self._ensure_client()
        if not client:
            return 0
        try:
            data = await client.motion_list()
        except UnitreeGatewayError as e:
            logger.warning("[宇树] 拉取动作库失败: %s", e)
            return 0

        items = _extract_items(data, candidate_keys=("motions", "list", "items", "data"))
        if not items:
            logger.info("[宇树] 动作库空")
            return 0

        db = await get_db()
        count = 0
        try:
            for idx, it in enumerate(items):
                mid = _coalesce(it, ("motion_id", "id", "motions_id"))
                name = _coalesce(it, ("name", "motion_name", "label", "title"))
                if mid is None or name in (None, ""):
                    continue
                try:
                    mid_int = int(mid)
                except (TypeError, ValueError):
                    continue
                await db.execute(
                    """INSERT INTO unitree_motions
                         (motion_id, name, category, sort_order, raw_json, updated_at)
                       VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
                       ON CONFLICT(motion_id) DO UPDATE SET
                         name=excluded.name,
                         category=excluded.category,
                         sort_order=excluded.sort_order,
                         raw_json=excluded.raw_json,
                         updated_at=excluded.updated_at""",
                    (
                        mid_int,
                        str(name),
                        str(_coalesce(it, ("category", "group"), default="") or ""),
                        idx,
                        json.dumps(it, ensure_ascii=False),
                    ),
                )
                count += 1
            await db.commit()
        finally:
            await db.close()
        logger.info("[宇树] 动作库同步完成: %d 项", count)
        return count

    # ============================================================
    # 舞蹈库
    # ============================================================
    async def sync_dances(self) -> int:
        client = await self._ensure_client()
        if not client:
            return 0
        try:
            data = await client.dance_list()
        except UnitreeGatewayError as e:
            logger.warning("[宇树] 拉取舞蹈库失败: %s", e)
            return 0

        items = _extract_items(data, candidate_keys=("dances", "list", "items", "data"))
        if not items:
            logger.info("[宇树] 舞蹈库空")
            return 0

        db = await get_db()
        count = 0
        try:
            for it in items:
                did = _coalesce(it, ("dance_id", "id", "code"))
                name = _coalesce(it, ("name", "dance_name", "label", "title"))
                if did in (None, "") or name in (None, ""):
                    continue
                duration = _coalesce(
                    it,
                    ("duration_seconds", "duration", "durationSeconds"),
                    default=0,
                )
                try:
                    duration_int = int(duration) if duration is not None else 0
                except (TypeError, ValueError):
                    duration_int = 0
                await db.execute(
                    """INSERT INTO unitree_dances
                         (dance_id, name, duration_seconds, raw_json, updated_at)
                       VALUES (?, ?, ?, ?, datetime('now','localtime'))
                       ON CONFLICT(dance_id) DO UPDATE SET
                         name=excluded.name,
                         duration_seconds=excluded.duration_seconds,
                         raw_json=excluded.raw_json,
                         updated_at=excluded.updated_at""",
                    (
                        str(did),
                        str(name),
                        duration_int,
                        json.dumps(it, ensure_ascii=False),
                    ),
                )
                count += 1
            await db.commit()
        finally:
            await db.close()
        logger.info("[宇树] 舞蹈库同步完成: %d 项", count)
        return count

    # ============================================================
    # 地图列表 (不自动拉点位, 避免 detail 带副作用切换当前工作地图)
    # ============================================================
    async def sync_maps(self) -> int:
        client = await self._ensure_client()
        if not client:
            return 0
        try:
            data = await client.map_list()
        except UnitreeGatewayError as e:
            logger.warning("[宇树] 拉取地图列表失败: %s", e)
            return 0

        current_id = None
        if isinstance(data, dict):
            current_id = data.get("current_working_map_id")
        maps = _extract_items(
            data if isinstance(data, dict) else {"maps": data},
            candidate_keys=("map_lists", "maps", "list", "items"),
        )
        if not maps:
            return 0

        db = await get_db()
        count = 0
        try:
            for m in maps:
                mid = _coalesce(m, ("map_id", "id"))
                mname = _coalesce(m, ("map_name", "name", "title"))
                if mid in (None, "") or mname in (None, ""):
                    continue
                # 注意: display_name 是 V5 本地别名, sync 不能覆盖用户手改的值;
                # 这里既不在 INSERT 列中提供(使用默认空串), 也不在 ON CONFLICT 更新集中.
                await db.execute(
                    """INSERT INTO unitree_maps
                         (map_id, map_code, map_name, map_index, is_current, raw_json, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))
                       ON CONFLICT(map_id) DO UPDATE SET
                         map_code=excluded.map_code,
                         map_name=excluded.map_name,
                         map_index=excluded.map_index,
                         is_current=excluded.is_current,
                         raw_json=excluded.raw_json,
                         updated_at=excluded.updated_at""",
                    (
                        str(mid),
                        str(_coalesce(m, ("map_code",), default="") or ""),
                        str(mname),
                        int(_coalesce(m, ("map_index",), default=0) or 0),
                        1 if (m.get("is_current") or str(mid) == str(current_id)) else 0,
                        json.dumps(m, ensure_ascii=False),
                    ),
                )
                count += 1
            await db.commit()
        finally:
            await db.close()
        logger.info("[宇树] 地图同步完成: %d 个", count)
        return count

    # ============================================================
    # 点位 (仅同步当前工作地图, 其它地图按需手动)
    # ============================================================
    async def sync_points_for_current_map(self) -> int:
        """只拉当前工作地图的点位, 避免 detail 副作用切换其它地图。"""
        client = await self._ensure_client()
        if not client:
            return 0

        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT map_id FROM unitree_maps WHERE is_current=1 LIMIT 1"
            )
            if not rows:
                logger.info("[宇树] 无当前工作地图, 跳过点位同步")
                return 0
            map_id = rows[0]["map_id"]
        finally:
            await db.close()

        try:
            data = await client.map_detail(map_id)
        except UnitreeGatewayError as e:
            logger.warning("[宇树] 拉取地图 %s 点位失败: %s", map_id, e)
            return 0

        return await _upsert_points(map_id, data)

    async def sync_points_for_map(self, map_id: str) -> int:
        """按需拉取指定地图的点位 (会触发网关副作用, 管理员操作才调用)。"""
        client = await self._ensure_client()
        if not client:
            return 0
        try:
            data = await client.map_detail(str(map_id))
        except UnitreeGatewayError as e:
            logger.warning("[宇树] 拉取地图 %s 点位失败: %s", map_id, e)
            return 0
        return await _upsert_points(str(map_id), data)

    # ============================================================
    # 完整同步
    # ============================================================
    async def full_sync(self) -> dict:
        stats = {"motions": 0, "dances": 0, "maps": 0, "points": 0}
        stats["motions"] = await self.sync_motions()
        stats["dances"] = await self.sync_dances()
        stats["maps"] = await self.sync_maps()
        stats["points"] = await self.sync_points_for_current_map()
        return stats


async def _upsert_points(map_id: str, map_detail_data: Any) -> int:
    points = []
    if isinstance(map_detail_data, dict):
        points = map_detail_data.get("points") or []
    if not isinstance(points, list) or not points:
        return 0

    db = await get_db()
    count = 0
    try:
        # 先清掉老点位, 再写入最新的, 保证删除也能同步
        await db.execute("DELETE FROM unitree_map_points WHERE map_id=?", (map_id,))
        for p in points:
            pid = _coalesce(p, ("point_id", "id"))
            pname = _coalesce(p, ("point_name", "name"), default="") or ""
            if pid is None:
                continue
            try:
                pid_int = int(pid)
            except (TypeError, ValueError):
                continue
            await db.execute(
                """INSERT INTO unitree_map_points
                     (map_id, point_id, point_name, raw_json, updated_at)
                   VALUES (?, ?, ?, ?, datetime('now','localtime'))
                   ON CONFLICT(map_id, point_id) DO UPDATE SET
                     point_name=excluded.point_name,
                     raw_json=excluded.raw_json,
                     updated_at=excluded.updated_at""",
                (
                    map_id,
                    pid_int,
                    str(pname),
                    json.dumps(p, ensure_ascii=False),
                ),
            )
            count += 1
        await db.commit()
    finally:
        await db.close()
    logger.info("[宇树] 地图 %s 点位同步完成: %d 个", map_id, count)
    return count


def _coalesce(d: dict, keys: tuple, default=None):
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _extract_items(data: Any, candidate_keys: tuple) -> list:
    """网关返回可能直接是列表, 也可能嵌在 {motions: [...]} / {list: [...]}。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in candidate_keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []
