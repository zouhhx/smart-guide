"""
TourPlanner — 运行时按访客/资源/意图自动编排一次参观(MVP).

不落 tour_scripts 表, 纯 runtime 生成, 用完即弃.
Pipeline: snapshot → LLM plan → validate → 交 SmartEngine._plan 执行.

外部入口(SmartEngine 调用):
    from app.engine.tour_planner import auto_plan
    plan = await auto_plan(robot_id, text, world_state)
    if plan: ... else: 回落静态脚本
"""

from __future__ import annotations

import json
import logging
import re
import time as _time
from dataclasses import dataclass, field
from typing import Any

from app.database import get_db

logger = logging.getLogger("hub.engine.tour_planner")


async def _alert(kind: str, message: str, severity: str = "warning", **ctx):
    """tour_planner 统一告警入口, 失败静默."""
    try:
        from app.engine.alerts import record_alert
        await record_alert("tour_planner", kind, message, severity=severity, **ctx)
    except Exception:
        pass


# ============================================================
# 数据契约
# ============================================================
@dataclass
class VisitorContext:
    id: int | None = None
    name: str = ""
    display_name: str = ""
    title: str = ""                 # "张总"/"李老师" (从 name 推断或人工配)
    tags: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    past_visits: int = 0
    last_exhibits: list[str] = field(default_factory=list)
    memory_summary: str = ""        # visitor_profiles.profile_summary

    @property
    def is_known(self) -> bool:
        return bool(self.id)


@dataclass
class ExhibitInfo:
    id: int
    name: str
    has_position: bool
    resource_count: int
    tags: list[str] = field(default_factory=list)
    is_core: bool = False
    talk_time: int = 180            # 秒, 来自 exhibit_knowledge
    narration_brief: str = ""       # 前 100 字
    resources: list[dict] = field(default_factory=list)  # 当前专场下绑定资源: index/name/desc/narration


@dataclass
class CapabilitySnapshot:
    exhibits: list[ExhibitInfo] = field(default_factory=list)
    specials: list[dict] = field(default_factory=list)
    current_special_id: int = 0
    current_special_name: str = ""
    robot_online: bool = False
    robot_battery: int = 100
    robot_position: str = ""
    time_budget_sec: int = 1200     # 默认 20 分钟
    topic_hints: list[str] = field(default_factory=list)
    custom_request: bool = False    # 是否显式"定制"触发


@dataclass
class TourStep:
    type: str                       # speak / navigate_exhibit / play_resource / prompt_next / show_list
    payload: dict
    est_seconds: int = 0


@dataclass
class TourPlan:
    title: str
    rationale: str
    steps: list[TourStep]
    est_total_seconds: int = 0
    # 自动驾驶模式: True 时 SmartEngine 不在 prompt_next 处暂停, 而是按 est_seconds
    # 等资源播完直接推下一站. 完整跑完整套讲解, 像配置好的流程.
    # "我有X分钟" / "为我定制" / "随便" 这类场景默认 True.
    autonomous: bool = False


# ============================================================
# 1. Capability Snapshot
# ============================================================
_TOPIC_KEYWORDS = [
    "智慧城市", "智慧园区", "智慧社区", "智慧交通", "智慧建造", "智慧教育", "智慧农田",
    "AI赋能", "人工智能", "数字人", "数字化", "大模型",
    "中国移动", "九天", "5G",
    "制造业", "政务", "金融", "医疗",
]

_TRIGGER_CUSTOM = re.compile(r"(为我|给我).*(定制|设计|规划|安排)|"
                              r"(智能|个性化|定制).*(参观|导览|讲解)")

# 放羊式触发词: "随便/随意/都行/看着办/你定/挑几个" 这种用户没明说要看啥
# 但希望系统自主规划的表达。命中即视为应该 auto_plan。
_TRIGGER_FREEFORM = re.compile(
    r"(随便|随意|都行|都可以|看着办|你定|你看着|挑几个|"
    r"几个亮点|主要的|有什么好的|精华|最好看的|有特色)"
)

# 兜底参观表达 (无具体目标也无定制/随便, 但意图就是"开始参观")
# 命中后只要存在时长预算 / known visitor 任一条件就 auto_plan
_TRIGGER_GENERIC_TOUR = re.compile(
    r"带我参观|开始参观|我想参观|带我看看|参观一下|"
    r"讲解一下|开始讲解|带我转一圈|转一圈|逛一逛|逛一下|"
    r"导览一下|开始导览"
)


def _extract_budget(text: str, default_sec: int = 1200) -> int:
    """从 text 提取时长预算(秒). '20分钟' → 1200. 失败返回默认."""
    m = re.search(r"(\d+)\s*分钟", text)
    if m:
        return max(60, min(3600, int(m.group(1)) * 60))
    # 中文数字
    cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
              "九": 9, "十": 10, "十五": 15, "二十": 20, "三十": 30, "四十": 40, "一小时": 60}
    for cn, n in cn_map.items():
        if f"{cn}分钟" in text:
            return n * 60
        if f"{cn}小时" in text:
            return n * 3600
    return default_sec


def _extract_topics(text: str) -> list[str]:
    """从 text 抽取主题关键词列表."""
    found = []
    for kw in _TOPIC_KEYWORDS:
        if kw.lower() in text.lower() and kw not in found:
            found.append(kw)
    return found


async def build_visitor_context(robot_id: str) -> VisitorContext:
    """基于 robot 当前绑定的访客 + 历史记忆, 装 VisitorContext."""
    vc = VisitorContext()
    try:
        from app.engine.visitor_service import get_active_visitor_for_robot
        from app.engine.visitor_memory_service import get_relevant_memories
        active = await get_active_visitor_for_robot(robot_id)
        if not active:
            return vc
        vc.id = active.get("id")
        vc.name = active.get("name", "") or ""
        vc.display_name = active.get("display_name", "") or vc.name
        vc.past_visits = int(active.get("visit_count", 0) or 0)
        vc.memory_summary = active.get("profile_summary", "") or ""
        # 拉最近记忆里提过的展项名
        try:
            memories = await get_relevant_memories(vc.id, "", exhibit_id=None)
        except Exception:
            memories = []
        seen = set()
        for m in memories[:20]:
            topic = (m.get("topic") or "").strip()
            if topic and topic not in seen:
                vc.last_exhibits.append(topic)
                seen.add(topic)
            if len(vc.last_exhibits) >= 10:
                break
    except Exception as e:
        logger.debug("[TourPlanner] 访客上下文加载失败, 用空档案: %s", e)
    return vc


async def snapshot_capabilities(robot_id: str, text: str = "") -> CapabilitySnapshot:
    """采集当前 hub 能调用的所有资源 + 从 text 推断意图参数."""
    snap = CapabilitySnapshot()
    snap.time_budget_sec = _extract_budget(text)
    snap.topic_hints = _extract_topics(text)
    snap.custom_request = bool(_TRIGGER_CUSTOM.search(text))

    db = await get_db()
    try:
        # 当前专场
        cur_rows = await db.execute_fetchall(
            "SELECT id, name FROM specials WHERE is_current=1 LIMIT 1"
        )
        if cur_rows:
            snap.current_special_id = int(cur_rows[0]["id"])
            snap.current_special_name = cur_rows[0]["name"] or ""
        # 专场清单
        sp_rows = await db.execute_fetchall(
            "SELECT id, name FROM specials ORDER BY id"
        )
        snap.specials = [dict(r) for r in sp_rows]
        # 展项 + position 绑定 + 资源数 + knowledge
        ex_rows = await db.execute_fetchall(
            """SELECT e.id, e.name,
                      (SELECT COUNT(*) FROM position_bindings pb
                        WHERE pb.bind_target_id=e.id AND pb.bind_type='exhibit') AS pos_cnt,
                      (SELECT COUNT(*) FROM special_exhibit_resources ser
                        WHERE ser.exhibit_id=e.id AND ser.special_id=?) AS res_cnt,
                      (SELECT narration_text FROM exhibit_knowledge WHERE exhibit_id=CAST(e.id AS TEXT)) AS narration,
                      (SELECT tags FROM exhibit_knowledge WHERE exhibit_id=CAST(e.id AS TEXT)) AS tags_json,
                      (SELECT is_core FROM exhibit_knowledge WHERE exhibit_id=CAST(e.id AS TEXT)) AS is_core,
                      (SELECT talk_time FROM exhibit_knowledge WHERE exhibit_id=CAST(e.id AS TEXT)) AS talk_time
               FROM exhibits e ORDER BY e.id""",
            (snap.current_special_id,),
        )
        for r in ex_rows:
            tags = []
            try:
                tags = json.loads(r["tags_json"] or "[]") or []
            except Exception:
                tags = []
            narration = (r["narration"] or "")[:100]
            snap.exhibits.append(ExhibitInfo(
                id=int(r["id"]),
                name=r["name"],
                has_position=bool(r["pos_cnt"] and r["pos_cnt"] > 0),
                resource_count=int(r["res_cnt"] or 0),
                tags=tags,
                is_core=bool(r["is_core"] or 0),
                talk_time=int(r["talk_time"] or 180),
                narration_brief=narration,
            ))
        # ?????????????????/??/??????? LLM ???????????
        try:
            res_rows = await db.execute_fetchall(
                """SELECT ser.exhibit_id, ser.sort_order,
                          r.id, r.name, r.category, r.description, r.narration_text
                   FROM special_exhibit_resources ser
                   JOIN resources r ON r.id = ser.resource_id
                   WHERE ser.special_id=?
                   ORDER BY ser.exhibit_id, ser.sort_order, r.id""",
                (snap.current_special_id,),
            )
            by_ex: dict[int, list[dict]] = {}
            for rr in res_rows:
                eid = int(rr["exhibit_id"])
                arr = by_ex.setdefault(eid, [])
                if len(arr) >= 5:
                    continue
                arr.append({
                    "index": len(arr) + 1,
                    "id": int(rr["id"]),
                    "name": rr["name"] or "",
                    "category": rr["category"] or "",
                    "description": (rr["description"] or "")[:100],
                    "narration": (rr["narration_text"] or "")[:160],
                })
            for ex in snap.exhibits:
                ex.resources = by_ex.get(ex.id, [])
        except Exception as e:
            logger.debug("[TourPlanner] ????????: %s", e)
    finally:
        await db.close()

    # 机器人状态 (从 world_state 拿当前绑定机器人, 非 DB)
    try:
        from app.engine.world_state import get_world_state
        ws = get_world_state()
        robot = ws.get_robot(robot_id) if hasattr(ws, "get_robot") else None
        if robot:
            snap.robot_online = True
            snap.robot_battery = int(getattr(robot, "battery", 100) or 100)
            snap.robot_position = getattr(robot, "position", "") or ""
    except Exception as e:
        logger.debug("[TourPlanner] 机器人状态读取失败, 假设在线: %s", e)
        snap.robot_online = True

    logger.info(
        "[TourPlanner] Snapshot: exhibits=%d(可用=%d) special=%s budget=%ds topics=%s custom=%s",
        len(snap.exhibits),
        sum(1 for e in snap.exhibits if e.has_position and e.resource_count > 0),
        snap.current_special_name, snap.time_budget_sec,
        snap.topic_hints, snap.custom_request,
    )
    return snap


# ============================================================
# 2. LLM Planner
# ============================================================
_PLANNER_SYSTEM_PROMPT = """你是展厅导览机器人路线规划师。基于用户消息里的访客档案/展项清单/意图, 输出路线 JSON.

【运行模式】
- 输出会直接由系统连续执行, 不会在中途问用户"想看下一个吗".
- 每个 play_resource 步骤系统会按 est_seconds 等待资源播完再走下一步, 你需要给一个真实预估.
- 用户在过程中只会用"暂停/继续/换一个/结束"等关键词打断, 不需要你留 prompt_next.

【硬约束】
- exhibit_id 必须来自 exhibits 清单, 不能自造
- step.type 取: speak / navigate_exhibit / play_resource
- 每展项节奏: navigate_exhibit → speak(到位讲解, 60-160字, 必须点明即将播放/屏幕展示的资源名称和观看重点) → play_resource(est_seconds 给资源时长) → speak(20-50字 转场过渡到下一个)
- 每个 exhibit 会给 resources 列表；play_resource.resource_index 必须选择列表里的资源序号，前后的 speak 内容必须和该资源名称/description/narration 对应，不能只泛泛讲展项。
- 第一站省去过渡 speak; 最后一站不写过渡, 改写 30-80 字结尾 speak
- 开场 speak (20-80字, 包含本次时长 + 选了哪几个) + 2-5 个展项 + 结尾 speak, 总步数 ≤30
- speak.text 全部口语化, 无 markdown, 不列点, 不出现"接下来", 多用"我们/咱们"
- 整体时长贴近 budget, 不超过 budget 的 110%

【est_seconds 估算】
- speak: 字数 / 3 (约每秒 3 字)
- navigate_exhibit: 30
- play_resource: 优先根据所选 resource 的 narration/description 推断资源时长 60-180 之间, 取整 30 倍

【个性化】
- 有 visitor.name → 开场自然称呼一次, 不要每轮都叫
- past_visits>0 → 跳过 last_exhibits 里已去过的
- topic_hints 非空 → 优先选 tags 匹配主题的展项
- is_core=true 权重高
- 不编造数据

【rationale 要求】30-50字, 写具体"选了哪几个+为什么". 禁止用套话:
  - 禁: "首次访客" "按核心展项顺序" "依次体验" "递进式路线" "全流程覆盖"
  - 好: "选CAVE+大岛台, 两者tags含智慧城市"  /  "跳过上次看过的小岛台, 补充AI赋能"

【输出示例】
{"title":"10分钟智慧城市精华路线","rationale":"选CAVE+大岛台正面, 都覆盖智慧城市主题","steps":[{"type":"speak","payload":{"text":"您好, 接下来 10 分钟我陪您看两个精华展项, 一起感受智慧城市."},"est_seconds":8},{"type":"navigate_exhibit","payload":{"exhibit_id":141,"exhibit_name":"CAVE空间投影"},"est_seconds":30},{"type":"speak","payload":{"text":"这里是 CAVE 沉浸式投影, 我们用三面环幕做城市数字孪生, 一会儿您可以看到园区实时数据汇聚."},"est_seconds":15},{"type":"play_resource","payload":{"exhibit_id":141,"resource_index":1},"est_seconds":120},{"type":"speak","payload":{"text":"看完投影咱们去大岛台, 那边讲我们怎么把数据变成决策."},"est_seconds":8},{"type":"navigate_exhibit","payload":{"exhibit_id":145,"exhibit_name":"大岛台正面"},"est_seconds":30},{"type":"speak","payload":{"text":"大岛台是中枢, 城市运行的关键指标在这里实时聚合."},"est_seconds":12},{"type":"play_resource","payload":{"exhibit_id":145,"resource_index":1},"est_seconds":150},{"type":"speak","payload":{"text":"今天的 10 分钟体验就到这里, 后面您要是想再看具体展项随时叫我."},"est_seconds":12}]}

只返回单个纯 JSON 对象, 无围栏无思考标签."""


def _build_planner_user_msg(visitor: VisitorContext, snap: CapabilitySnapshot) -> str:
    """把快照 + 访客组装成 user message. 精简字段降 prompt token, 压低 LLM 延迟.

    - brief 截到 60 字: LLM 知道展项讲什么就够, 不需要长段原文
    - talk_time 改名 talk_sec, is_core 空省略
    - visitor.summary 截到 120 字
    """
    valid = [e for e in snap.exhibits if e.has_position and e.resource_count > 0]
    ex_dump = []
    for e in valid:
        item = {
            "id": e.id, "name": e.name,
            "resource_count": e.resource_count,
            "brief": e.narration_brief[:80] if e.narration_brief else "",
        }
        if e.resources:
            item["resources"] = [
                {
                    "index": r.get("index"),
                    "name": (r.get("name") or "")[:40],
                    "desc": ((r.get("narration") or r.get("description") or "")[:90]),
                }
                for r in e.resources[:4]
            ]
        if e.tags:
            item["tags"] = e.tags
        if e.is_core:
            item["core"] = True
        ex_dump.append(item)
    vis = {
        "past_visits": visitor.past_visits,
    }
    if visitor.name:
        vis["name"] = visitor.name
    if visitor.display_name and visitor.display_name != visitor.name:
        vis["display"] = visitor.display_name
    if visitor.last_exhibits:
        vis["last_exhibits"] = visitor.last_exhibits[:6]
    if visitor.interests:
        vis["interests"] = visitor.interests
    if visitor.memory_summary:
        vis["summary"] = visitor.memory_summary[:120]
    payload = {
        "visitor": vis,
        "special": snap.current_special_name,
        "budget_sec": snap.time_budget_sec,
        "exhibits": ex_dump,
    }
    if snap.topic_hints:
        payload["topics"] = snap.topic_hints
    if snap.custom_request:
        payload["custom"] = True
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def plan_tour(visitor: VisitorContext, snap: CapabilitySnapshot) -> TourPlan | None:
    """调用 LLM 生成 TourPlan. 失败返回 None 让 caller 回落.

    直接调 MiniMax (app.engine.llm.chat_completion) 而不走 OpenClaw:
    OpenClaw 服务端有固定 systemPromptOverride, 会强制所有输出走 V5 决策 schema,
    不适合 planner 需要的自定义 JSON schema 输出.
    """
    if not snap.exhibits:
        logger.warning("[TourPlanner] Snapshot 无可用展项, 无法规划")
        return None
    try:
        from app.engine.llm import chat_completion
        import asyncio as _asyncio
        user_msg = _build_planner_user_msg(visitor, snap)
        t0 = _time.monotonic()
        content = await _asyncio.wait_for(
            chat_completion(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=_PLANNER_SYSTEM_PROMPT,
                max_tokens=2048,
                temperature=0.4,
            ),
            timeout=25.0,
        )
        elapsed = _time.monotonic() - t0
        logger.info("[TourPlanner] LLM 规划耗时 %.2fs, 返回 %d 字符", elapsed, len(content or ""))
        if content:
            logger.debug("[TourPlanner] LLM raw: %s", content[:500])
    except _asyncio.TimeoutError:
        logger.warning("[TourPlanner] LLM 规划超时(25s)")
        return None
    except Exception as e:
        logger.warning("[TourPlanner] LLM 规划调用失败: %s", e)
        return None

    if not content:
        return None
    return _parse_plan(content)


def _json_unescape_text(v: str) -> str:
    try:
        return json.loads('"' + v + '"')
    except Exception:
        return v.replace('\\"', '"').replace('\\n', ' ')


def _parse_plan_lenient(raw: str) -> TourPlan | None:
    """???? LLM ?? JSON?????????????????"""
    try:
        title_m = re.search(r'"title"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
        rationale_m = re.search(r'"rationale"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
        title = _json_unescape_text(title_m.group(1)) if title_m else "??????"
        rationale = _json_unescape_text(rationale_m.group(1)) if rationale_m else "LLM JSON ????"
        type_matches = list(re.finditer(r'"type"\s*:\s*"(speak|navigate_exhibit|play_resource|prompt_next|show_list)"', raw))
        if not type_matches:
            return None
        steps: list[TourStep] = []
        for i, m in enumerate(type_matches):
            stype = m.group(1)
            seg = raw[m.start(): type_matches[i + 1].start() if i + 1 < len(type_matches) else len(raw)]
            est_m = re.search(r'"est_seconds"\s*:\s*(\d+)', seg)
            est = int(est_m.group(1)) if est_m else 0
            payload: dict[str, Any] = {}
            if stype == "speak":
                text_m = re.search(r'"text"\s*[:>]\s*"((?:\\.|[^"\\])*)"', seg)
                if not text_m:
                    continue
                payload["text"] = _json_unescape_text(text_m.group(1))
            elif stype == "navigate_exhibit":
                id_m = re.search(r'"exhibit_id"\s*:\s*(\d+)', seg)
                name_m = re.search(r'"exhibit_name"\s*:\s*"((?:\\.|[^"\\])*)"', seg)
                if not id_m:
                    continue
                payload["exhibit_id"] = int(id_m.group(1))
                if name_m:
                    payload["exhibit_name"] = _json_unescape_text(name_m.group(1))
            elif stype == "play_resource":
                id_m = re.search(r'"exhibit_id"\s*:\s*(\d+)', seg)
                idx_m = re.search(r'"resource_index"\s*:\s*(\d+)', seg)
                if not id_m:
                    continue
                payload["exhibit_id"] = int(id_m.group(1))
                payload["resource_index"] = int(idx_m.group(1)) if idx_m else 1
            else:
                payload = {}
            steps.append(TourStep(type=stype, payload=payload, est_seconds=est))
        if len(steps) < 3:
            return None
        total = sum(x.est_seconds for x in steps)
        logger.info("[TourPlanner] ???? LLM JSON ??: %d steps, %ds", len(steps), total)
        return TourPlan(title=title[:80], rationale=rationale[:200], steps=steps, est_total_seconds=total)
    except Exception as e:
        logger.warning("[TourPlanner] ???? LLM JSON ??: %s", e)
        return None


def _parse_plan(raw: str) -> TourPlan | None:
    """解析 LLM 输出 JSON → TourPlan. 解析失败返回 None."""
    s = raw.strip()
    # 去 markdown 围栏
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    # 抽第一个 {...}
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        s = m.group(0)
    try:
        data = json.loads(s)
    except Exception as e:
        logger.warning("[TourPlanner] JSON ????: %s (raw ?300?: %r)", e, raw[:300])
        return _parse_plan_lenient(raw)
    if not isinstance(data, dict) or not data.get("steps"):
        return None
    steps: list[TourStep] = []
    for raw_step in data.get("steps", []):
        if not isinstance(raw_step, dict):
            continue
        stype = str(raw_step.get("type", "")).strip()
        if stype not in ("speak", "navigate_exhibit", "play_resource", "prompt_next", "show_list"):
            continue
        steps.append(TourStep(
            type=stype,
            payload=raw_step.get("payload", {}) or {},
            est_seconds=int(raw_step.get("est_seconds", 0) or 0),
        ))
    return TourPlan(
        title=str(data.get("title", "智能参观路线"))[:80],
        rationale=str(data.get("rationale", ""))[:200],
        steps=steps,
        est_total_seconds=int(data.get("est_total_seconds", 0) or 0),
        autonomous=bool(data.get("autonomous", False)),
    )


# ============================================================
# 3. Validator — 强校验 + 时长修剪
# ============================================================
def validate_plan(plan: TourPlan, snap: CapabilitySnapshot) -> TourPlan | None:
    """校验+修剪. 任一硬失败则返回 None 让 caller 回落.

    硬规则:
    - 必须至少 1 个 navigate_exhibit 或 speak(纯闲聊空 plan 没意义)
    - navigate_exhibit.exhibit_id 必须在 snap 里且 has_position/resource_count>0
    - play_resource.exhibit_id 必须前有对应 navigate_exhibit
    - speak.text 非空
    软规则(自动修剪, 不 reject):
    - 时长预算 ±30% 范围, 超了砍尾直到 ≤预算×1.1
    """
    if not plan or not plan.steps:
        return None

    valid_ex = {e.id: e for e in snap.exhibits if e.has_position and e.resource_count > 0}

    cleaned: list[TourStep] = []
    current_exhibit: int = 0
    nav_count = 0
    speak_count = 0

    for step in plan.steps:
        if step.type == "speak":
            text = (step.payload.get("text") or "").strip()
            if not text:
                continue
            if len(text) > 400:
                text = text[:400]
            step.payload["text"] = text
            # 估算: 每 3 字 1 秒
            step.est_seconds = max(step.est_seconds, len(text) // 3)
            cleaned.append(step)
            speak_count += 1
        elif step.type == "navigate_exhibit":
            ex_id = int(step.payload.get("exhibit_id", 0) or 0)
            if ex_id not in valid_ex:
                logger.warning("[Validator] 丢弃无效 navigate_exhibit id=%d", ex_id)
                continue
            ex = valid_ex[ex_id]
            step.payload["exhibit_name"] = ex.name
            step.est_seconds = max(step.est_seconds, 30)
            current_exhibit = ex_id
            cleaned.append(step)
            nav_count += 1
        elif step.type == "play_resource":
            ex_id = int(step.payload.get("exhibit_id", 0) or 0)
            if ex_id != current_exhibit or ex_id not in valid_ex:
                logger.warning("[Validator] 丢弃错位 play_resource exhibit_id=%d (current=%d)",
                               ex_id, current_exhibit)
                continue
            step.payload.setdefault("resource_index", 1)
            step.est_seconds = max(step.est_seconds, 60)
            cleaned.append(step)
        elif step.type == "prompt_next":
            step.est_seconds = max(step.est_seconds, 3)
            cleaned.append(step)
        elif step.type == "show_list":
            cleaned.append(step)  # 暂不强验 show_list 内容
        # 其他类型已在 _parse_plan 过滤

    # 硬条件: 至少 1 导航 + 1 speak
    if nav_count < 1 or speak_count < 1:
        logger.warning("[Validator] Plan 无有效导航或开场, 拒绝. nav=%d speak=%d",
                       nav_count, speak_count)
        return None

    # 时长修剪: 超预算 130% 就砍尾, 保留开场+尾
    budget = snap.time_budget_sec or 1200
    total = sum(s.est_seconds for s in cleaned)
    if total > budget * 1.3:
        logger.info("[Validator] 时长 %ds 超预算 %ds, 砍尾", total, budget)
        # 保留前 N 步使 ≤ budget*1.1, 然后保留最后一个 speak (结尾)
        trimmed: list[TourStep] = []
        running = 0
        tail_speak: TourStep | None = None
        for s in cleaned:
            if s.type == "speak" and s is cleaned[-1]:
                tail_speak = s
                continue
            if running + s.est_seconds > budget * 1.1:
                break
            trimmed.append(s)
            running += s.est_seconds
        if tail_speak:
            trimmed.append(tail_speak)
            running += tail_speak.est_seconds
        cleaned = trimmed
        total = running

    # 第二种智能规划优先保留 LLM 路线：
    # 旧逻辑低于预算 80% 直接拒绝，会把“可用但略短”的 LLM plan 打回分钟档兜底，
    # 导致第二种不像智能规划，也更难保持和 LLM 选择的屏幕资源一致。
    # 现在只告警不拒绝；只要结构校验通过，就优先执行 LLM 路线。
    min_total = int(budget * 0.8)
    if budget >= 300 and total < min_total:
        logger.warning("[Validator] Plan 时长不足但接受: %ds < 预算参考下限 %ds (budget=%ds)，优先执行 LLM 路线", total, min_total, budget)

    plan.steps = cleaned
    plan.est_total_seconds = total
    logger.info("[Validator] Plan 校验通过: %d steps (%ds), 预算 %ds", len(cleaned), total, budget)
    return plan


# ============================================================
# 4. 归档 — 每次 auto_plan 调用写一行到 auto_plan_logs
# ============================================================
async def _ensure_plan_log_table():
    db = await get_db()
    try:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auto_plan_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
                robot_id        TEXT DEFAULT '',
                visitor_id      INTEGER,
                user_text       TEXT DEFAULT '',
                snapshot_json   TEXT DEFAULT '',
                visitor_json    TEXT DEFAULT '',
                llm_raw         TEXT DEFAULT '',
                llm_elapsed_ms  INTEGER DEFAULT 0,
                parsed_steps    INTEGER DEFAULT 0,
                validator_status TEXT DEFAULT '',
                final_plan_json TEXT DEFAULT '',
                status          TEXT DEFAULT 'ok',
                error           TEXT DEFAULT ''
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_apl_created ON auto_plan_logs(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_apl_robot ON auto_plan_logs(robot_id)")
        await db.commit()
    except Exception as e:
        logger.debug("[TourPlanner] 建 auto_plan_logs 失败: %s", e)
    finally:
        await db.close()


async def _archive_plan_attempt(
    robot_id: str, text: str, snap: CapabilitySnapshot | None,
    visitor: VisitorContext | None, llm_raw: str, llm_elapsed_ms: int,
    parsed_plan: TourPlan | None, validator_result: TourPlan | None,
    status: str, error: str = "",
) -> None:
    """把一次 auto_plan 调用的所有中间产物落库. 失败不影响主流程."""
    try:
        await _ensure_plan_log_table()
        snap_json = ""
        if snap:
            snap_json = json.dumps({
                "current_special": snap.current_special_name,
                "time_budget_sec": snap.time_budget_sec,
                "topic_hints": snap.topic_hints,
                "custom_request": snap.custom_request,
                "exhibits": [
                    {"id": e.id, "name": e.name, "has_position": e.has_position,
                     "resource_count": e.resource_count, "tags": e.tags, "is_core": e.is_core}
                    for e in snap.exhibits
                ],
                "robot_online": snap.robot_online,
                "robot_battery": snap.robot_battery,
            }, ensure_ascii=False)[:4000]
        visitor_json = ""
        if visitor:
            visitor_json = json.dumps({
                "id": visitor.id, "name": visitor.name,
                "display_name": visitor.display_name,
                "past_visits": visitor.past_visits,
                "last_exhibits": visitor.last_exhibits,
                "interests": visitor.interests,
            }, ensure_ascii=False)[:2000]
        final_json = ""
        if validator_result:
            final_json = json.dumps({
                "title": validator_result.title,
                "rationale": validator_result.rationale,
                "est_total_seconds": validator_result.est_total_seconds,
                "steps": [{"type": s.type, "payload": s.payload, "est_seconds": s.est_seconds}
                          for s in validator_result.steps],
            }, ensure_ascii=False)[:6000]
        parsed_steps = len(parsed_plan.steps) if parsed_plan else 0
        validator_status = "ok" if validator_result else ("parsed_but_rejected" if parsed_plan else "no_parse")
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO auto_plan_logs
                   (robot_id, visitor_id, user_text, snapshot_json, visitor_json,
                    llm_raw, llm_elapsed_ms, parsed_steps, validator_status,
                    final_plan_json, status, error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    robot_id, visitor.id if visitor else None,
                    text[:500], snap_json, visitor_json,
                    (llm_raw or "")[:4000], llm_elapsed_ms,
                    parsed_steps, validator_status,
                    final_json, status, error[:500],
                ),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        logger.debug("[TourPlanner] 归档失败(不影响主流程): %s", e)


# ============================================================
# 5. 外部入口 — 一站式 auto_plan
# ============================================================
async def auto_plan(robot_id: str, text: str, autonomous: bool = False) -> TourPlan | None:
    """SmartEngine 的一站式入口. 任一阶段失败返回 None, 由 caller 回落到静态脚本.

    整个过程写 auto_plan_logs 归档: snapshot/visitor/LLM raw/elapsed/validator 结果.
    用于 prompt 迭代 + 故障排查.

    autonomous=True: caller (典型 "我有X分钟" 路径) 想要全自动连续讲解, 不在每个展项后停下问用户.
    会把 plan.autonomous 置 True, SmartEngine._advance_auto_plan 看到这位会跳过 prompt_next, 走完整路线.
    """
    import time as _t
    t0 = _t.monotonic()
    snap: CapabilitySnapshot | None = None
    visitor: VisitorContext | None = None
    parsed: TourPlan | None = None
    validated: TourPlan | None = None
    llm_raw_capture = ""
    llm_elapsed_ms = 0
    status = "ok"
    error = ""

    try:
        snap = await snapshot_capabilities(robot_id, text)
        if not snap.exhibits:
            status, error = "no_exhibits", "snapshot returned empty exhibits"
            return None
        visitor = await build_visitor_context(robot_id)

        # 内联展开 plan_tour 以便捕获 raw + elapsed 用于归档
        try:
            from app.engine.llm import chat_completion
            import asyncio as _asyncio
            user_msg = _build_planner_user_msg(visitor, snap)
            t_llm = _t.monotonic()
            llm_raw_capture = await _asyncio.wait_for(
                chat_completion(
                    messages=[{"role": "user", "content": user_msg}],
                    system_prompt=_PLANNER_SYSTEM_PROMPT,
                    max_tokens=2048, temperature=0.4,
                ),
                timeout=25.0,
            ) or ""
            llm_elapsed_ms = int((_t.monotonic() - t_llm) * 1000)
            logger.info("[TourPlanner] LLM 规划耗时 %dms, 返回 %d 字符",
                        llm_elapsed_ms, len(llm_raw_capture))
        except _asyncio.TimeoutError:
            status, error = "llm_timeout", "LLM 25s timeout"
            logger.warning("[TourPlanner] LLM 规划超时(25s)")
            await _alert("llm_timeout", "Planner LLM 25s 超时", "warning",
                         robot_id=robot_id, text=text[:120])
            return None
        except Exception as e:
            status, error = "llm_error", str(e)[:200]
            logger.warning("[TourPlanner] LLM 规划调用失败: %s", e)
            await _alert("llm_error", f"Planner LLM 调用异常: {e}", "error",
                         robot_id=robot_id, text=text[:120])
            return None

        parsed = _parse_plan(llm_raw_capture) if llm_raw_capture else None
        if not parsed:
            status, error = "parse_fail", "无法解析 LLM JSON"
            await _alert("parse_fail", "Planner 解析 LLM 输出失败",
                         severity="warning", robot_id=robot_id,
                         raw_preview=(llm_raw_capture or "")[:200])
            return None

        validated = validate_plan(parsed, snap)
        if not validated:
            status, error = "validator_rejected", "校验不通过"
            await _alert("validator_rejected", "Planner 校验拒绝",
                         severity="info", robot_id=robot_id,
                         parsed_steps=len(parsed.steps))
            return None
        if autonomous:
            validated.autonomous = True
        return validated
    finally:
        await _archive_plan_attempt(
            robot_id, text, snap, visitor, llm_raw_capture, llm_elapsed_ms,
            parsed, validated, status, error,
        )
        logger.info("[TourPlanner] auto_plan 完成 status=%s 总耗时=%dms",
                    status, int((_t.monotonic() - t0) * 1000))


async def replan_remainder(
    robot_id: str,
    reason: str,
    completed_exhibits: list[int],
    skipped_exhibits: list[int],
    time_remaining_sec: int,
) -> TourPlan | None:
    """中途重规划: 用户打断后基于已看/已跳 + 剩余时长 重新编排后面路线.

    策略: Snapshot 里剔除 completed + skipped 的展项, budget 设为剩余, 把
    reason (用户原话) 拼进 text 让 LLM 理解"为什么要换".
    """
    snap = await snapshot_capabilities(robot_id, reason or "")
    # 剔除已看/已跳
    exclude = set(int(x) for x in (completed_exhibits or []) + (skipped_exhibits or []))
    snap.exhibits = [e for e in snap.exhibits if e.id not in exclude]
    snap.time_budget_sec = max(120, int(time_remaining_sec))
    if not snap.exhibits:
        logger.info("[TourPlanner] replan 无剩余展项可选")
        return None
    visitor = await build_visitor_context(robot_id)
    # 把"已看过"信息注入 visitor.last_exhibits 让 LLM 知道要跳过
    ex_name_map = {}
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall("SELECT id, name FROM exhibits WHERE id IN ("
                                             + ",".join(str(x) for x in exclude) + ")")
            ex_name_map = {int(r["id"]): r["name"] for r in rows}
        finally:
            await db.close()
    except Exception:
        pass
    visitor.last_exhibits = list({*visitor.last_exhibits, *ex_name_map.values()})
    parsed = await plan_tour(visitor, snap)
    if not parsed:
        return None
    return validate_plan(parsed, snap)


async def is_auto_plan_enabled() -> bool:
    """Feature flag: DB 配置 auto_plan_enabled=1 才启用."""
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='auto_plan_enabled'"
            )
            if rows:
                return str(rows[0]["value"]).strip() in ("1", "true", "yes", "on")
        finally:
            await db.close()
    except Exception:
        pass
    return False


def should_auto_plan(text: str, visitor: VisitorContext | None = None) -> bool:
    """判断当前输入是否应走自动规划。放宽后的命中条件 (任一):
    1. 显式 "为我定制 / 智能参观" 触发词
    2. 放羊式表达 (随便 / 随意 / 都行 / 挑几个 / 精华 ...) — 用户明显不想自己挑
    3. 时长预算 (单独一个就够, 例: "十分钟讲解一下")
    4. 时长 + 主题 (例: "20分钟智慧城市")
    5. 主题 + 任意泛化参观词 (例: "我想看看人工智能展区")
    6. 已识别访客 (past_visits>=1) + 泛化参观 — 老访客就别让他再选, 直接编排
    7. 通用参观词命中 + 没有具体 target — 反正也得规划, 不如让 LLM 编排
    """
    text = text or ""
    if _TRIGGER_CUSTOM.search(text):
        return True
    if _TRIGGER_FREEFORM.search(text):
        return True
    has_budget = bool(_extract_budget(text, 0))
    if has_budget:
        return True
    has_topic = bool(_extract_topics(text))
    if has_topic and _TRIGGER_GENERIC_TOUR.search(text):
        return True
    if (visitor and visitor.is_known and visitor.past_visits >= 1
            and _TRIGGER_GENERIC_TOUR.search(text)):
        return True
    # 兜底: 通用参观词 + 无具体目标 → auto_plan 编排, 比静态脚本更贴合现场状态
    if _TRIGGER_GENERIC_TOUR.search(text):
        # 如果用户其实只说了 "带我去 XX" 这种 navigate_to 的话, 不该走 tour
        if re.search(r"带我去[^,，。!?]*$|带我到[^,，。!?]*$", text):
            return False
        return True
    return False
