"""
ActionExecutor — 统一执行层

所有硬件操作统一经过此模块，SmartEngine只需调用高级语义接口。
好处：1) 日志追踪 2) 错误处理统一 3) SmartEngine不关心底层协议细节

执行通道:
- 设备控制: 中控TCP 8989
- 资源播放: 中控TCP 8989
- 机器人导航/TTS: 中枢TCP 19888
- 水滴云: HTTP GET（只读，不控制）
"""

import asyncio
import json
import logging
import time

from app.database import get_db
from app.tcp.server import get_robot, RobotConnection
from app.tcp.central_control import send_tcp_command

logger = logging.getLogger("hub.engine.action_executor")


def _fuzzy_match_pinyin(a: str, b: str) -> bool:
    """拼音模糊匹配"""
    from app.engine.intent_resolver import _to_pinyin
    a_py, b_py = _to_pinyin(a), _to_pinyin(b)
    return a_py in b_py or b_py in a_py or a_py == b_py


class ActionExecutor:
    """统一动作执行器"""

    def __init__(self, robot_id: str = ""):
        self.robot_id = robot_id

    def _get_robot(self) -> RobotConnection | None:
        return get_robot(self.robot_id)

    # ============================================================
    # 机器人控制
    # ============================================================
    async def navigate(self, position_name: str, timeout: float = 120.0) -> bool:
        """导航到指定点位 — 等到 evt_arrived (成功) 或 evt_nav_failed (失败) 才返回.

        关键修正 (2026-04-24):
        - 必须发 pointId (32 字符 hex), APK 协议要的是 ID 不是 name;
          老版本只发字符串名时, APK 不识别 → 不 ack → send_cmd 超时 → 误判失败。
        - 默认 timeout 120s (机器人物理移动需要 30-120s), 之前 10s 远不够。
        - 超时 (result=None) 不再当成失败 — 机器人可能还在路上, 保守返回 True;
          只有明确收到 evt_nav_failed 才返回 False。
        """
        robot = self._get_robot()
        if not robot:
            logger.warning("[Action] 机器人不在线，无法导航")
            return False
        # 反查 pointId — 跟 flow_executor._step_navigate 同款逻辑
        point_id = ""
        real_point_name = position_name
        try:
            from app.database import get_db
            db = await get_db()
            try:
                rows = await db.execute_fetchall(
                    "SELECT position_id, name FROM nav_positions WHERE name=? LIMIT 1",
                    (position_name,),
                )
                if rows:
                    point_id = rows[0]["position_id"]
                    real_point_name = rows[0]["name"]
            finally:
                await db.close()
        except Exception as e:
            logger.warning("[Action] 查询 pointId 失败 name=%s: %s", position_name, e)
        # 精确查不到, 回落到模糊匹配 — ASR 谐音 (相总→姜总) / 同音错字常发生
        if not point_id:
            try:
                fuzzy = await self.find_position_by_name(position_name)
                if fuzzy:
                    point_id = fuzzy["position_id"]
                    real_point_name = fuzzy["name"]
                    logger.info("[Action] 点位模糊回落: '%s' → '%s'", position_name, real_point_name)
            except Exception as e:
                logger.debug("[Action] 模糊匹配异常 name=%s: %s", position_name, e)
        if not point_id:
            logger.error("[Action] 找不到点位 '%s' 的 pointId, 跳过导航", position_name)
            return False
        try:
            result = await robot.send_cmd("cmd_navigate", {
                "pointId": point_id,
                "pointName": real_point_name,
            }, timeout=timeout)
            if result is None:
                # send_cmd 超时 — 机器人可能仍在路上, 不武断判失败
                logger.warning("[Action] 导航 %s → %s 等回调超时 (%.0fs), 假定仍在路上",
                               self.robot_id, real_point_name, timeout)
                return True
            evt_type = result.get("type", "")
            if evt_type == "evt_nav_failed":
                reason = result.get("data", {}).get("reason", "未知")
                logger.error("[Action] 导航失败: %s → %s reason=%s",
                             self.robot_id, real_point_name, reason)
                return False
            if evt_type == "evt_arrived":
                logger.info("[Action] 导航到达: %s → %s", self.robot_id, real_point_name)
                return True
            # 其它事件类型 (理论上不应发生): 保守视为成功, 留 warn 便于排查
            logger.warning("[Action] 导航回调非 evt_arrived/evt_nav_failed: type=%s, 视为成功",
                           evt_type)
            return True
        except Exception as e:
            logger.error("[Action] 导航异常: %s → %s, 错误=%s",
                         self.robot_id, real_point_name, e)
            return False

    async def speak(self, text: str, extra_data: dict | None = None) -> bool:
        """机器人TTS播报 — 走 speak_serialized: 等到 evt_tts_done 才返回,
        防止后到 cmd_tts 立刻打断前一句长播报。"""
        robot = self._get_robot()
        if not robot:
            logger.warning("[Action] 机器人不在线，无法播报")
            return False
        try:
            ok = await robot.speak_serialized(text, extra_data=extra_data)
            logger.info("[Action] TTS: %s → %s... (ok=%s)", self.robot_id, text[:30], ok)
            return ok
        except Exception as e:
            logger.error("[Action] TTS异常: %s, 错误=%s", self.robot_id, e)
            return False

    async def take_photo(self, photo_point_name: str, qr_content: str = "",
                         upload_url: str = "", skip_nav: bool = False,
                         timeout: float = 300.0) -> dict | None:
        """下发拍照指令。

        期望 APK 执行完整拍照体验：
        1. 导航到 photoPointName (skip_nav=True 时跳过导航, 用于重拍)；
        2. 到达后语音/屏幕提示用户站位；
        3. 预览页保持，3/2/1 倒计时并播放快门音；
        4. 拍照后把二维码合成到照片右下角，页面保持展示（不自动关闭）；
        5. Android 端上传原图并回调 evt_photo_done / evt_photo_failed；
        6. 中枢端收到 evt_photo_done 后设置 followup, 等用户说"重拍"/"不用了"。
        """
        robot = self._get_robot()
        if not robot:
            logger.warning("[Action] 机器人不在线，无法拍照")
            return None
        try:
            payload = {
                "photoPointName": photo_point_name,
                "pointName": photo_point_name,
                "qrContent": qr_content or "",
                "photoUrl": qr_content or "",
                "uploadUrl": upload_url or "",
                "prepareSeconds": 6,
                "countdownSeconds": 3,
                "allowRetake": True,
                "screenMode": "photo_countdown_qrcode",
                "qrPosition": "bottom_right",
                "playShutterSound": True,
            }
            if skip_nav:
                payload["skipNav"] = True
            result = await robot.send_cmd("cmd_take_photo", payload, timeout=timeout)

            if result is None:
                logger.warning("[Action] 拍照指令超时: point=%s", photo_point_name)
                return None
            evt_type = result.get("type", "")
            data = result.get("data", {}) or {}
            if evt_type == "evt_photo_done" or data.get("success") is True:
                logger.info("[Action] 拍照完成: point=%s skip_nav=%s", photo_point_name, skip_nav)
                return data
            logger.warning("[Action] 拍照失败: type=%s data=%s", evt_type, data)
            return data
        except Exception as e:
            logger.error("[Action] 拍照异常: point=%s err=%s", photo_point_name, e)
            return None

    async def close_photo(self) -> bool:
        """关闭照片结果页面 (用户说"不用了"时调用)。

        Android CustomPhotoCaptureActivity 收到 cmd_close_photo 后:
          1. 关闭 Activity & 释放相机
          2. 恢复人脸跟随
          3. 回复 evt_photo_closed
        """
        robot = self._get_robot()
        if not robot:
            logger.warning("[Action] 机器人不在线，无法关闭拍照页")
            return False
        try:
            result = await robot.send_cmd("cmd_close_photo", {}, timeout=10.0)
            if result:
                logger.info("[Action] 拍照页已关闭")
                return True
            logger.warning("[Action] 关闭拍照页超时或无响应")
            return False
        except Exception as e:
            logger.error("[Action] 关闭拍照页异常: %s", e)
            return False

    async def listen(self, timeout: int = 3) -> bool:
        """让机器人开始收音.

        协议: timeout 作为"收音时长"下发给 APK, APK 收音 N 秒后自动回唤醒词模式.
        默认 3s (现场反复测试得出的最舒适值, 既够用户接话, 又不会拖慢流程).
        若 APK 版本不支持该字段则忽略, 等同旧行为 (固定 5s).
        """
        robot = self._get_robot()
        if not robot:
            return False
        result = await robot.send_cmd("cmd_listen", {"timeout": int(timeout)})
        return result is not None

    async def cancel(self) -> bool:
        """取消机器人当前任务"""
        robot = self._get_robot()
        if not robot:
            return False
        try:
            result = await robot.send_cmd("cmd_cancel", {})
            return result is not None
        except Exception as e:
            logger.error("[Action] 取消任务异常: %s", e)
            return False

    async def go_charging(self) -> bool:
        """让机器人去充电"""
        # 优先用猎豹云端API
        try:
            from app.sync.orionstar import OrionStarSync
            osync = OrionStarSync()
            await osync.stop_charging()  # 先停止充电状态
        except Exception:
            pass
        # 走 navigate 统一通道 (会查 pointId、用 evt_arrived 判定到达)
        ok = await self.navigate("充电桩", timeout=180)
        logger.info("[Action] 去充电: %s, ok=%s", self.robot_id, ok)
        return ok

    # ============================================================
    # 设备控制（中控TCP 8989）
    # ============================================================
    async def control_equipment(self, equipment_id: int, on: bool = True) -> bool:
        """控制单个设备"""
        cmd = f"0_r_{equipment_id}_{'on' if on else 'off'}"
        result = await send_tcp_command(cmd)
        return result.get("status") == "ok"

    async def group_command(self, group_name: str, on: bool = True) -> bool:
        """执行组策略"""
        cmd = f"0_all_{group_name}_{'on' if on else 'off'}"
        result = await send_tcp_command(cmd)
        return result.get("status") == "ok"

    async def lights_on(self) -> bool:
        return await self.group_command("light", on=True)

    async def lights_off(self) -> bool:
        return await self.group_command("light", on=False)

    async def all_on(self) -> bool:
        return await self.group_command("com", on=True)

    async def all_off(self) -> bool:
        return await self.group_command("com", on=False)

    async def ad_screen_on(self) -> bool:
        return await self.group_command("ggj", on=True)

    async def ad_screen_off(self) -> bool:
        return await self.group_command("ggj", on=False)

    # ============================================================
    # 资源播放（中控TCP 8989）
    # ============================================================
    async def play_resource(self, special_id: int, terminal_ctrl_id: int, resource_id: int) -> bool:
        """播放指定资源"""
        cmd = f"2_{special_id}_{terminal_ctrl_id}_{resource_id}"
        result = await send_tcp_command(cmd)
        return result.get("status") == "ok"

    async def video_control(self, terminal_ctrl_id: int, action: str) -> bool:
        """视频控制: play/pause/replay"""
        cmd = f"2_{terminal_ctrl_id}_video_{action}"
        result = await send_tcp_command(cmd)
        return result.get("status") == "ok"

    async def ppt_control(self, terminal_ctrl_id: int, action: str) -> bool:
        """PPT控制: play/pause/replay/last/next"""
        cmd = f"2_{terminal_ctrl_id}_ppt_{action}"
        result = await send_tcp_command(cmd)
        return result.get("status") == "ok"

    # ============================================================
    # 展项讲解（组合动作）
    # ============================================================
    async def explain_exhibit(self, exhibit_name: str, narration: str, position_name: str = "") -> bool:
        """
        讲解展项的完整流程:
        1. 导航到展项位置（如有）— 走 navigate 统一通道
        2. 到达后TTS讲解
        """
        if position_name:
            await self.navigate(position_name, timeout=120)
        # TTS讲解
        if narration:
            return await self.speak(narration)
        return True

    # ============================================================
    # 数据查询（辅助）
    # ============================================================
    async def get_exhibit_resources(self, exhibit_id: int) -> list:
        """获取展项在当前专场的资源列表"""
        db = await get_db()
        try:
            # 获取当前专场
            sp = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='current_special_id'"
            )
            special_id = int(sp[0]["value"]) if sp and sp[0]["value"] else 0

            # 获取展项的中控终端ID
            exhibit = await db.execute_fetchall(
                "SELECT id, exhibition_area_terminal_id FROM exhibits WHERE id=?", (exhibit_id,)
            )
            if not exhibit:
                return []

            terminal_ctrl_id = exhibit[0]["exhibition_area_terminal_id"]

            # 获取资源列表
            resources = await db.execute_fetchall(
                """SELECT r.id, r.name, r.category, ser.sort_order
                   FROM special_exhibit_resources ser
                   JOIN resources r ON r.id = ser.resource_id
                   WHERE ser.special_id=? AND ser.exhibit_id=?
                   ORDER BY ser.sort_order""",
                (special_id, exhibit_id),
            )
            result = []
            for r in resources:
                result.append({
                    "id": r["id"],
                    "name": r["name"],
                    "type": r["category"],
                    "sort_order": r["sort_order"],
                    "special_id": special_id,
                    "terminal_ctrl_id": terminal_ctrl_id,
                })
            return result
        finally:
            await db.close()

    async def get_current_special(self) -> dict:
        """获取当前专场信息"""
        db = await get_db()
        try:
            sp = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='current_special_id'"
            )
            sp_name = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='current_special_name'"
            )
            return {
                "id": int(sp[0]["value"]) if sp and sp[0]["value"] else 0,
                "name": sp_name[0]["value"] if sp_name and sp_name[0]["value"] else "",
            }
        finally:
            await db.close()

    # 展项名关键词提取映射: 语音识别中常见的变体 → 标准关键词
    # key=展项名关键词, value=语音识别可能出现的变体
    _EXHIBIT_KEYWORDS = {
        "大岛台": ["大岛台", "大脑台", "大楼台", "大盗台", "dar岛台", "大导台", "大到台", "大脑"],
        "小岛台": ["小岛台", "小脑台", "小楼台", "小盗台", "小导台", "小脑"],
        "三联屏": ["三联屏", "三连屏", "三脸屏", "入口屏", "入口三联", "机械臂"],
        "CAVE": ["投影", "cave", "CAVE", "凯夫", "凯福", "空间投影", "凯文"],
        "拼接屏": ["拼接屏", "拼接", "拼节屏", "门口屏"],
        "广告机": ["广告机", "广告", "广高机"],
        "触摸屏": ["触摸屏", "触膜屏", "触摸", "触屏"],
        "云猴": ["云猴", "允猴", "云厚", "导览"],
    }

    # 辅助/内部展项(匹配时跳过，优先匹配主展项)
    _SKIP_EXHIBIT_NAMES = {"cave全黑使用", "1123", "GPP"}

    async def find_exhibit_by_name(self, name: str) -> dict | None:
        """模糊查找展项 — 支持关键词变体、点位名映射、拼音匹配"""
        from app.engine.intent_resolver import _to_pinyin
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT id, name, exhibition_area_terminal_id FROM exhibits"
            )
            if not rows:
                return None

            def _pick(exhibit_row) -> dict:
                r = dict(exhibit_row)
                return {"id": r["id"], "name": r["name"], "terminal_ctrl_id": r["exhibition_area_terminal_id"]}

            def _is_main_exhibit(r) -> bool:
                """是否是主展项(非辅助项)"""
                return r["name"] not in self._SKIP_EXHIBIT_NAMES

            # 0. 点位名映射: 语音识别说点位名时，通过position_bindings找展项
            pos_rows = await db.execute_fetchall(
                """SELECT np.name as pos_name, pb.bind_target_id, e.name as exhibit_name
                   FROM position_bindings pb
                   JOIN nav_positions np ON np.position_id = pb.position_id
                   JOIN exhibits e ON e.id = pb.bind_target_id
                   WHERE pb.bind_type='exhibit'"""
            )
            pos_to_exhibit = {r["pos_name"]: {"id": r["bind_target_id"], "name": r["exhibit_name"]} for r in pos_rows}
            # 点位名变体: 机械臂→三联屏, 投影区→CAVE空间投影
            pos_name_variants = {"机械臂": "三联屏", "机械比": "三联屏", "投影区": "CAVE"}

            # 1. 精确包含匹配
            for r in rows:
                if not _is_main_exhibit(r):
                    continue
                if name in r["name"] or r["name"] in name:
                    return _pick(r)

            # 2. 点位名映射
            if name in pos_to_exhibit:
                info = pos_to_exhibit[name]
                for r in rows:
                    if r["id"] == info["id"]:
                        logger.info("[Action] 点位名匹配: '%s' → '%s'", name, r["name"])
                        return _pick(r)
            for variant, target_kw in pos_name_variants.items():
                if variant in name or name in variant:
                    for r in rows:
                        if _is_main_exhibit(r) and target_kw in r["name"]:
                            logger.info("[Action] 点位变体匹配: '%s' → '%s'", name, r["name"])
                            return _pick(r)

            # 3. 关键词变体匹配
            for standard_kw, variants in self._EXHIBIT_KEYWORDS.items():
                for v in variants:
                    if v.lower() == name.lower() or v in name or name in v:
                        # 用标准关键词匹配主展项
                        candidates = [r for r in rows if _is_main_exhibit(r) and standard_kw in r["name"]]
                        if candidates:
                            r = candidates[0]
                            logger.info("[Action] 展项变体匹配: '%s' → '%s' (via '%s')", name, r["name"], v)
                            return _pick(r)

            # 4. 拼音模糊匹配
            name_py = _to_pinyin(name)
            best_match = None
            best_score = 0
            for r in rows:
                if not _is_main_exhibit(r):
                    continue
                rname = r["name"]
                rname_py = _to_pinyin(rname)
                if name_py and rname_py:
                    if name_py in rname_py or rname_py in name_py:
                        score = len(name_py)
                        if score > best_score:
                            best_score = score
                            best_match = r
                    else:
                        common = 0
                        for i in range(len(name_py)):
                            for j in range(i + 2, len(name_py) + 1):
                                if name_py[i:j] in rname_py:
                                    common = max(common, j - i)
                        # 过去仅 common>=4 就认, 导致 "玄武湖"(xuanwuhu) 被
                        # "岛台下屏"(daotaixiaping) 通过 "xia"+1 抢占。
                        # 现在要求绝对长度 >=4 且至少覆盖输入拼音的 60%,
                        # 这样泛知识/景点名不会误中展项。
                        coverage_ok = common >= max(4, int(len(name_py) * 0.6))
                        if coverage_ok and common > best_score:
                            best_score = common
                            best_match = r

            if best_match:
                logger.info("[Action] 拼音匹配: '%s' → '%s' (score=%d)", name, best_match["name"], best_score)
                return _pick(best_match)

            # 5. 兜底: 在点位名中搜索
            for pos_name, info in pos_to_exhibit.items():
                if _fuzzy_match_pinyin(pos_name, name):
                    for r in rows:
                        if r["id"] == info["id"]:
                            logger.info("[Action] 点位拼音匹配: '%s' → pos='%s' → '%s'", name, pos_name, r["name"])
                            return _pick(r)

            return None
        finally:
            await db.close()

    async def find_position_for_exhibit(self, exhibit_id: int) -> str | None:
        """查找展项绑定的点位名"""
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                """SELECT np.name FROM position_bindings pb
                   JOIN nav_positions np ON np.position_id = pb.position_id
                   WHERE pb.bind_target_id=? AND pb.bind_type='exhibit'""",
                (exhibit_id,),
            )
            return rows[0]["name"] if rows else None
        finally:
            await db.close()

    async def find_special_by_name(self, name: str) -> dict | None:
        """模糊查找专场 — 处理 ASR 谐音/同音错字 + 后缀噪声。

        匹配层级 (短路返回首个命中):
        1. 后缀清洗: "思德专场" / "思德转场" / "思德场" → "思德" 后再比
        2. exact (大小写无关)
        3. 子串双向 ("思德" in "思德AI专场" / 反向)
        4. 拼音相等 (思的=sidi, 思德=side, 不等; 但若用户说"思得"则相等)
        5. 拼音子串双向
        6. 拼音编辑距离 ≤2 (要求最短拼音 ≥4 字符防短词误命中)

        返回 {"id", "name"} 或 None.
        """
        from app.engine.intent_resolver import _to_pinyin
        from app.engine.intent_synonyms import _edit_distance

        n = (name or "").strip()
        # 后缀清洗 — 用户多读/少读
        for suffix in ("专场", "转场", "战场", "场次", "场"):
            if n.endswith(suffix) and len(n) > len(suffix):
                n = n[:-len(suffix)].strip()
                break
        if not n:
            return None
        n_l = n.lower()
        db = await get_db()
        try:
            rows = await db.execute_fetchall("SELECT id, name FROM specials")
            if not rows:
                return None
            # 1. exact (cleaned vs full name + cleaned vs cleaned)
            for r in rows:
                rn = (r["name"] or "")
                if rn.lower() == n_l:
                    return {"id": r["id"], "name": rn}
            # 2. 子串 (用户原话 cleaned 字串 vs 真名)
            for r in rows:
                rn = (r["name"] or "")
                if rn and (n_l in rn.lower() or rn.lower() in n_l):
                    return {"id": r["id"], "name": rn}
            # 3-5. 拼音
            n_py = _to_pinyin(n_l)
            if not n_py or len(n_py) < 2:
                return None
            best_row = None
            best_tier = (9, 999)
            for r in rows:
                rname = r["name"] or ""
                # 真名也剥后缀比拼音 (e.g. "思德AI专场" → "思德AI")
                rstem = rname
                for suffix in ("专场", "转场", "战场", "场次", "场"):
                    if rstem.endswith(suffix) and len(rstem) > len(suffix):
                        rstem = rstem[:-len(suffix)]
                        break
                rpy = _to_pinyin(rstem.lower())
                if not rpy:
                    continue
                tier: tuple = (9, 999)
                if n_py == rpy:
                    tier = (1, 0)
                elif n_py in rpy or rpy in n_py:
                    tier = (2, abs(len(n_py) - len(rpy)))
                else:
                    if abs(len(n_py) - len(rpy)) <= 2 and min(len(n_py), len(rpy)) >= 4:
                        ed = _edit_distance(n_py, rpy)
                        if ed <= 2:
                            tier = (3, ed)
                if tier < best_tier:
                    best_tier = tier
                    best_row = r
            if best_row and best_tier[0] <= 3:
                logger.info(
                    "[Action] 专场模糊匹配: '%s' → '%s' (tier=%d ed=%d)",
                    name, best_row["name"], best_tier[0], best_tier[1],
                )
                return {"id": best_row["id"], "name": best_row["name"]}
            return None
        finally:
            await db.close()

    async def find_position_by_name(self, name: str) -> dict | None:
        """模糊查找点位 — 处理 ASR 谐音/同音错字: "相总" → "姜总" 这种.

        匹配层级 (短路返回首个命中):
        1. exact (大小写无关)
        2. 子串双向 ("姜总" in "姜总工位" / 反向)
        3. 拼音相等
        4. 拼音子串双向
        5. 拼音编辑距离 ≤2 (要求最短拼音 ≥4 字符防短词误命中)

        返回 {"position_id", "name"} 或 None.
        """
        from app.engine.intent_resolver import _to_pinyin
        from app.engine.intent_synonyms import _edit_distance
        n = (name or "").strip()
        if not n:
            return None
        n_l = n.lower()
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT position_id, name FROM nav_positions"
            )
            if not rows:
                return None
            # 1. exact
            for r in rows:
                if (r["name"] or "").lower() == n_l:
                    return {"position_id": r["position_id"], "name": r["name"]}
            # 2. literal containment
            for r in rows:
                rn = (r["name"] or "")
                if rn and (n_l in rn.lower() or rn.lower() in n_l):
                    return {"position_id": r["position_id"], "name": rn}
            # 3-5. 拼音匹配
            n_py = _to_pinyin(n_l)
            if not n_py or len(n_py) < 2:
                return None
            best_row = None
            best_tier = (9, 999)  # (tier, edit_distance) 越小越好
            for r in rows:
                rname = r["name"] or ""
                rpy = _to_pinyin(rname.lower())
                if not rpy:
                    continue
                tier: tuple = (9, 999)
                if n_py == rpy:
                    tier = (1, 0)
                elif n_py in rpy or rpy in n_py:
                    tier = (2, abs(len(n_py) - len(rpy)))
                else:
                    # 编辑距离 — 长度差太大不比, 防止短词被远词命中
                    if abs(len(n_py) - len(rpy)) <= 2 and min(len(n_py), len(rpy)) >= 4:
                        ed = _edit_distance(n_py, rpy)
                        if ed <= 2:
                            tier = (3, ed)
                if tier < best_tier:
                    best_tier = tier
                    best_row = r
            if best_row and best_tier[0] <= 3:
                logger.info(
                    "[Action] 点位模糊匹配: '%s' → '%s' (tier=%d ed=%d)",
                    name, best_row["name"], best_tier[0], best_tier[1],
                )
                return {"position_id": best_row["position_id"], "name": best_row["name"]}
            return None
        finally:
            await db.close()

    # ============================================================
    # 日志
    # ============================================================
    async def _log(self, action: str, detail: str, status: str = "ok"):
        try:
            db = await get_db()
            try:
                await db.execute(
                    """INSERT INTO execution_logs (log_type, source, target, action, detail, status, created_at)
                       VALUES ('action', ?, ?, ?, ?, ?, datetime('now','localtime'))""",
                    (self.robot_id, "action_executor", action, detail, status),
                )
                await db.commit()
            finally:
                await db.close()
        except Exception:
            pass
