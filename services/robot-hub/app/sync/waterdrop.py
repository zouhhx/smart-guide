"""
水滴云数据同步模块

负责从水滴云平台拉取全部展馆数据并入库。
所有分页接口都自动拉全，不遗漏。

数据层级：展馆 → 展区 → 展项(terminal) → 专场 → 资源
核心关系：资源列表 = f(展项, 专场) — 动态关联
"""

import json
import time
import logging
import aiohttp

from app.config import get_config
from app.database import get_db

logger = logging.getLogger("hub.sync.waterdrop")


class WaterdropSync:
    """水滴云数据同步器"""

    def __init__(self):
        self.config = get_config().get("waterdrop", {})
        self.base_url = self.config.get("base_url", "")
        self.account = self.config.get("account", "")
        self.password = self.config.get("password", "")
        self.hall_id = self.config.get("hall_id", 5)
        self.token = None
        self.session = None

    async def _ensure_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # ============================================================
    # 认证
    # ============================================================
    async def login(self) -> bool:
        """登录获取 token"""
        await self._ensure_session()
        url = f"{self.base_url}/app/authority/api/v1/login"
        data = {"account": self.account, "password": self.password}

        try:
            async with self.session.post(url, data=data) as resp:
                result = await resp.json()
                if result.get("code") == 200 and result.get("data", {}).get("token"):
                    self.token = result["data"]["token"]
                    logger.info("水滴云登录成功")
                    return True
                else:
                    logger.error("水滴云登录失败: %s", result)
                    return False
        except Exception as e:
            logger.error("水滴云登录异常: %s", e)
            return False

    def _headers(self) -> dict:
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def _get(self, path: str, params: dict = None) -> dict:
        """GET 请求"""
        await self._ensure_session()
        url = f"{self.base_url}{path}"
        try:
            async with self.session.get(url, headers=self._headers(), params=params) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("GET %s 失败: %s", path, e)
            return {}

    async def _get_all_pages(self, path: str, params: dict = None,
                             data_key: str = "records", page_size: int = 100) -> list:
        """
        自动分页拉取全部数据
        ⚠️ 关键：必须拉全，不能只取第一页
        """
        if params is None:
            params = {}
        params["size"] = page_size
        params["current"] = 1

        all_data = []
        total_pages = 1

        while params["current"] <= total_pages:
            result = await self._get(path, params)
            data = result.get("data", {})

            if isinstance(data, list):
                # 接口直接返回数组
                all_data.extend(data)
                break
            elif isinstance(data, dict):
                records = data.get(data_key, data.get("list", []))
                if isinstance(records, list):
                    all_data.extend(records)

                # 计算总页数
                total = data.get("total", data.get("totalCount", len(records)))
                if total and page_size:
                    total_pages = (total + page_size - 1) // page_size
                else:
                    break

                if not records:
                    break
            else:
                break

            params["current"] += 1

        logger.info("分页拉取 %s: 共 %d 条", path, len(all_data))
        return all_data

    # ============================================================
    # 核心同步：一键获取配置
    # ============================================================
    async def sync_config(self) -> dict:
        """
        同步核心配置：设备 + 终端 + 组策略
        使用一键接口 configsForControl
        """
        result = await self._get(
            f"/app/exhibition/api/v1/halls/{self.hall_id}/configsForControl"
        )
        return result.get("data", {})

    # ============================================================
    # 展区
    # ============================================================
    async def sync_areas(self) -> list:
        """同步展区列表"""
        result = await self._get(
            f"/app/exhibition/api/v1/halls/{self.hall_id}/areas"
        )
        return result.get("data", [])

    # ============================================================
    # 专场
    # ============================================================
    async def sync_specials(self) -> list:
        """同步专场列表"""
        result = await self._get("/pc/special/api/v1/exhibitions/querySpecialList")
        data = result.get("data", [])
        if isinstance(data, dict):
            data = data.get("records", data.get("list", []))
        return data if isinstance(data, list) else []

    async def select_special(self, special_id: int) -> bool:
        """选择专场 ⚠️ 获取专场数据前必须先调用"""
        await self._ensure_session()
        url = f"{self.base_url}/pc/special/api/v1/exhibitions/selectSpecial"
        try:
            async with self.session.put(
                url, headers=self._headers(), params={"specialId": special_id}
            ) as resp:
                result = await resp.json()
                return result.get("code") == 200
        except Exception as e:
            logger.error("选择专场 %d 失败: %s", special_id, e)
            return False

    async def get_special_terminals(self, special_id: int) -> list:
        """获取专场下的终端列表（含中控ID）"""
        result = await self._get(
            f"/pc/special/api/v1/exhibitions/filter/{special_id}/areas"
        )
        return result.get("data", [])

    # ============================================================
    # 资源 — 核心！必须用 exhibits 接口拿完整数据
    # ============================================================
    async def sync_terminal_resources(self, special_id: int, terminal_id: int) -> list:
        """
        获取终端在特定专场下的完整资源列表
        ⚠️ 必须用 /app/special/.../exhibits 接口，不要用 /exhibitions/exhibition
        后者会漏掉程序类型资源！
        """
        all_resources = await self._get_all_pages(
            f"/app/special/api/v1/exhibitions/{special_id}/terminals/{terminal_id}/exhibits",
            data_key="records",
        )
        return all_resources

    # ============================================================
    # 组策略
    # ============================================================
    async def sync_group_commands(self) -> list:
        """同步组策略列表"""
        result = await self._get("/app/exhibition/api/v1/group-command/")
        return result.get("data", [])

    # ============================================================
    # 完整同步流程
    # ============================================================
    async def full_sync(self) -> dict:
        """
        完整同步所有数据到本地数据库

        流程：
        1. 登录
        2. 拉取核心配置（设备+终端+组策略）
        3. 拉取展区
        4. 拉取专场列表
        5. 遍历每个专场的每个终端，拉取资源（分页拉全）
        6. 建立动态关联关系
        """
        start = time.time()
        stats = {
            "halls": 0, "areas": 0, "exhibits": 0, "specials": 0,
            "resources": 0, "relations": 0, "equipments": 0,
            "group_commands": 0, "errors": [],
        }

        # 1. 登录
        if not await self.login():
            stats["errors"].append("登录失败")
            return stats

        db = await get_db()
        try:
            # 2. 展馆（固定）
            await db.execute(
                "INSERT OR REPLACE INTO halls (id, name) VALUES (?, ?)",
                (self.hall_id, "展馆"),
            )
            stats["halls"] = 1

            # 2a. 创建默认展区（areas API 返回空，但设备/终端需要引用）
            await db.execute(
                "INSERT OR IGNORE INTO areas (id, hall_id, name) VALUES (?, ?, ?)",
                (1, self.hall_id, "默认展区"),
            )
            stats["areas"] += 1

            # 3. 核心配置
            config_data = await self.sync_config()

            # 3a. 设备（从 configsForControl.equipment）
            #     API 字段: equipment[], singleControl[]
            equipments = config_data.get("equipment", [])
            single_controls = config_data.get("singleControl", [])

            # 构建设备ID→开关命令映射 (从 singleControl 提取)
            eq_cmd_map = {}  # equipmentId -> {cmd_on, cmd_off, hex_on, hex_off}
            for sc in single_controls:
                eid = sc.get("equipmentId")
                if not eid:
                    continue
                if eid not in eq_cmd_map:
                    eq_cmd_map[eid] = {"cmd_on": "", "cmd_off": "", "hex_on": "", "hex_off": ""}
                msg = sc.get("commandMsg", "")
                if msg.endswith("_on"):
                    eq_cmd_map[eid]["cmd_on"] = msg
                    eq_cmd_map[eid]["hex_on"] = sc.get("command", "")
                elif msg.endswith("_off"):
                    eq_cmd_map[eid]["cmd_off"] = msg
                    eq_cmd_map[eid]["hex_off"] = sc.get("command", "")

            for eq in equipments:
                eq_id = eq.get("id")
                if not eq_id:
                    continue
                cmds = eq_cmd_map.get(eq_id, {})
                await db.execute(
                    """INSERT OR REPLACE INTO equipments
                       (id, area_id, name, ip, port, device_type, zone_name,
                        cmd_on, cmd_off, hex_on, hex_off, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        eq_id,
                        eq.get("exhibitionAreaId") or 1,  # 默认展区
                        eq.get("equipmentName", ""),
                        eq.get("equipmentIp", ""),
                        eq.get("equipmentPort", ""),
                        eq.get("equipmentType", ""),
                        eq.get("zoneId", eq.get("remarks", "")),
                        cmds.get("cmd_on", ""), cmds.get("cmd_off", ""),
                        cmds.get("hex_on", ""), cmds.get("hex_off", ""),
                        json.dumps(eq, ensure_ascii=False),
                    ),
                )
                stats["equipments"] += 1

            # 3b. 终端/展项（从 configsForControl.terminal）
            terminals = config_data.get("terminal", [])
            for t in terminals:
                t_id = t.get("id")
                if not t_id:
                    continue
                await db.execute(
                    """INSERT OR REPLACE INTO exhibits
                       (id, area_id, name, exhibition_area_terminal_id,
                        ip, port, terminal_type, zone_name, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        t_id,
                        t.get("exhibitionAreaId") or 1,  # 默认展区
                        t.get("hostName", ""),
                        t.get("id"),  # terminal 表的 id 就是中控ID
                        t.get("hostIp", ""),
                        t.get("hostPort", ""),
                        t.get("hostProtocol", ""),
                        t.get("remarks", ""),
                        json.dumps(t, ensure_ascii=False),
                    ),
                )
                stats["exhibits"] += 1

            # 3c. 组策略（从 configsForControl.allControl）
            #     每条 allControl 已是一个完整指令（如 "每日全开" -> "0_all_com_on"）
            #     cmd_on 存 TCP 命令，cmd_off 留空（因为每条本身就是一个动作）
            all_controls = config_data.get("allControl", [])
            for idx, gc in enumerate(all_controls):
                gc_name = gc.get("wholeOrderName", "")
                gc_msg = gc.get("wholeOrderMsg", "")  # 完整 TCP 命令

                await db.execute(
                    """INSERT OR REPLACE INTO group_commands
                       (id, name, cmd_on, cmd_off, raw_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (idx + 1, gc_name, gc_msg, "",
                     json.dumps(gc, ensure_ascii=False)),
                )
                stats["group_commands"] += 1

            # 4. 专场列表
            specials = await self.sync_specials()
            special_ids = []
            for s in (specials if isinstance(specials, list) else []):
                s_id = s.get("id")
                if not s_id:
                    continue
                special_ids.append(s_id)
                await db.execute(
                    """INSERT INTO specials (id, name, description, raw_json)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         name=excluded.name,
                         description=excluded.description,
                         raw_json=excluded.raw_json""",
                    (s_id,
                     s.get("specialExhibitionName", s.get("specialName", "")),
                     s.get("specialExhibitionDescription", s.get("description", "")),
                     json.dumps(s, ensure_ascii=False)),
                )
                stats["specials"] += 1

            # 5. 遍历每个专场，拉取每个终端的资源
            #    这是最重要的步骤：建立 专场-展项-资源 三元关系
            logger.info("开始同步资源关系: %d 个专场 × %d 个展项",
                        len(special_ids), stats["exhibits"])

            # 清空旧关系
            await db.execute("DELETE FROM special_exhibit_resources")

            # 构建终端中控ID到展项ID的映射
            # terminal表的 id 就是中控ID，同时也是展项ID
            # 而 filter 接口的 exhibitionAreaTerminalId 才是中控ID
            exhibit_rows = await db.execute_fetchall(
                "SELECT id, exhibition_area_terminal_id FROM exhibits"
            )
            # 中控ID -> 展项表ID
            ctrl_to_exhibit = {}
            for r in exhibit_rows:
                ctrl_to_exhibit[r["exhibition_area_terminal_id"]] = r["id"]

            for s_id in special_ids:
                # ⚠️ 必须先 select 专场
                await self.select_special(s_id)

                # 获取该专场下的终端
                special_areas = await self.get_special_terminals(s_id)

                # 遍历展区 → 终端
                for area_data in (special_areas if isinstance(special_areas, list) else []):
                    terminal_list = area_data.get("exhibitionAreaTerminalList", [])
                    for t in (terminal_list if isinstance(terminal_list, list) else []):
                        ctrl_id = t.get("exhibitionAreaTerminalId")
                        if not ctrl_id:
                            continue

                        # 查找对应的展项ID
                        exhibit_id = ctrl_to_exhibit.get(ctrl_id, ctrl_id)

                        # 拉取该终端在该专场下的资源（分页拉全）
                        resources = await self.sync_terminal_resources(s_id, ctrl_id)

                        for idx, res in enumerate(resources):
                            res_detail = res.get("resourceExhibitDo", res)
                            # 资源ID: 外层 exhibitId 或 resourceExhibitDo.id
                            res_id = res.get("exhibitId") or res_detail.get("id")
                            if not res_id:
                                continue

                            # 资源入库
                            category = res_detail.get("categoryDic", "")
                            file_url = res_detail.get("fileUrl", "") or ""
                            program_path = res_detail.get("programPath", "") or ""

                            # 讲解词: commentary > allCommentary > exhibitDescription
                            narration = (
                                res_detail.get("commentary")
                                or res_detail.get("allCommentary")
                                or res_detail.get("exhibitDescription")
                                or ""
                            )
                            description = res_detail.get("exhibitDescription", "") or ""

                            # 一旦 narration_edited=1 (用户或 AI 编辑过), 同步不再覆盖 narration_text
                            await db.execute(
                                """INSERT INTO resources
                                   (id, name, category, file_url, thumbnail_url,
                                    program_name, program_path, original_file,
                                    description, narration_text, raw_json)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                   ON CONFLICT(id) DO UPDATE SET
                                     name=excluded.name,
                                     category=excluded.category,
                                     file_url=excluded.file_url,
                                     thumbnail_url=excluded.thumbnail_url,
                                     program_name=excluded.program_name,
                                     program_path=excluded.program_path,
                                     original_file=excluded.original_file,
                                     description=excluded.description,
                                     narration_text=CASE
                                       WHEN COALESCE(resources.narration_edited,0)=1
                                       THEN resources.narration_text
                                       ELSE excluded.narration_text
                                     END,
                                     raw_json=excluded.raw_json""",
                                (
                                    res_id,
                                    res_detail.get("exhibitTitle", res_detail.get("name", "")),
                                    category,
                                    file_url,
                                    res_detail.get("thumbnailUrl", "") or "",
                                    res_detail.get("programName", "") or "",
                                    program_path,
                                    res_detail.get("originalFileName", "") or "",
                                    description,
                                    narration,
                                    json.dumps(res_detail, ensure_ascii=False),
                                ),
                            )
                            stats["resources"] += 1

                            # 建立三元关系
                            await db.execute(
                                """INSERT OR IGNORE INTO special_exhibit_resources
                                   (special_id, exhibit_id, resource_id, sort_order)
                                   VALUES (?, ?, ?, ?)""",
                                (s_id, exhibit_id, res_id, idx),
                            )
                            stats["relations"] += 1

            await db.commit()
            elapsed = time.time() - start
            logger.info(
                "完整同步完成: 耗时 %.1fs | 展区=%d 展项=%d 专场=%d "
                "资源=%d 关系=%d 设备=%d 组策略=%d",
                elapsed, stats["areas"], stats["exhibits"], stats["specials"],
                stats["resources"], stats["relations"],
                stats["equipments"], stats["group_commands"],
            )

            # 设置当前专场（仅首次同步时使用默认值，后续由TCP update_{id}更新）
            # ⚠️ 不覆盖Pad已切换的专场！只在 system_config 无记录时才写入默认值
            existing = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='current_special_id'"
            )
            if not existing or not existing[0]["value"]:
                default_sp = self.config.get("default_special_id")
                if default_sp and default_sp in special_ids:
                    await db.execute(
                        "UPDATE specials SET is_current=0"
                    )
                    await db.execute(
                        "UPDATE specials SET is_current=1 WHERE id=?", (default_sp,)
                    )
                    # 查名称
                    sp_row = await db.execute_fetchall(
                        "SELECT name FROM specials WHERE id=?", (default_sp,)
                    )
                    sp_name = sp_row[0]["name"] if sp_row else str(default_sp)
                    await db.execute(
                        """INSERT INTO system_config (key, value) VALUES ('current_special_id', ?)
                           ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now','localtime')""",
                        (str(default_sp), str(default_sp)),
                    )
                    await db.execute(
                        """INSERT INTO system_config (key, value) VALUES ('current_special_name', ?)
                           ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now','localtime')""",
                        (sp_name, sp_name),
                    )
                    logger.info("当前专场设为: %d - %s", default_sp, sp_name)

            # 记录同步时间
            await db.execute(
                "UPDATE system_config SET value=datetime('now','localtime') WHERE key='last_sync_time'"
            )
            await db.commit()

        except Exception as e:
            logger.error("同步异常: %s", e, exc_info=True)
            stats["errors"].append(str(e))
        finally:
            await db.close()
            await self.close()

        return stats
