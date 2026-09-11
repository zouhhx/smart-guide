"""
DecisionBridge — 把 V5 的部分语音输入交给 OpenClaw 做决策

职责：
  1. 判断当前输入是否应该走 OpenClaw（handoff 门控，只接管复杂问答类）。
  2. 组装 prompt（系统约束 + 上下文 + 能力清单 + 用户文本）。
  3. 调 openclaw_client.chat，解析 JSON 决策。
  4. 任何失败（禁用/超时/JSON 坏/字段缺失/置信度低/decision 非法）→ 返回 None，
     调用方据此回落到原 V5 逻辑。

对 V5 主框架、机器人协议、中控协议 零侵入：
  - 不发送任何 TCP 指令
  - 不改 flow / TTS / navigate 执行器
  - 只返回"决策结果"供 answer_engine 转成现有 speak 调用

决策输出协议（assistant 必须输出一个 JSON）：
  {
    "decision": "answer_only|navigate_to|list_exhibits|execute_flow|ask_clarify|fallback",
    "reply": "给用户播报的话",
    "should_interrupt": true|false,
    "resume_policy": "none|ask_next|resume_tour",
    "target": "",
    "flow_name": "",
    "confidence": 0.0-1.0
  }
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from app.engine import openclaw_client
from app.engine.query_router import (
    HALL_CONTEXT,
    HALL_KNOWLEDGE,
    REALTIME_INFO,
    GENERAL_KNOWLEDGE,
    SOCIAL_FALLBACK,
    TOUR_CONTROL,  # noqa: F401  — imported for white-list reference
)

logger = logging.getLogger("hub.engine.decision_bridge")


# ============================================================
# 决策协议
# ============================================================
_ALLOWED_DECISIONS = {
    "answer_only",
    "navigate_to",
    "list_exhibits",
    "execute_flow",
    "ask_clarify",
    "fallback",
}

_ALLOWED_RESUME = {"none", "ask_next"}   # resume_tour 历史上没接执行器, 删掉避免误导

_MIN_CONFIDENCE = 0.4

# 决策缓存: 高频问句 (例如"我想看机器狗") 重复出现时直接返回上次决策, 不打 OpenClaw。
# 只缓存稳定决策 (navigate_to/list_exhibits/answer_only, confidence ≥ _CACHE_MIN_CONF);
# fallback/ask_clarify/execute_flow 一律不缓存 — 它们依赖即时上下文或有副作用。
_CACHE_TTL_SEC = 600.0   # 10min — answer_only 场景延长以提高命中率
_CACHE_MAX = 1024        # 256 → 1024, 高频问句 30 个 + 现场可能 50-100 句, 留余量
_CACHE_MIN_CONF = 0.6    # 0.7 → 0.6, 把 LLM 0.65-0.69 的合理答案也纳入缓存
_CACHEABLE_DECISIONS = {"navigate_to", "list_exhibits", "answer_only"}
# OrderedDict 当 LRU 用: key → (decision_dict, expires_at_monotonic)
_decision_cache: "OrderedDict[tuple, tuple[dict, float]]" = OrderedDict()


def _cache_key(user_text: str, qtype: str, ctx: dict) -> tuple:
    """缓存 key 只取真正影响决策结果的字段; 名字/时间这种动态字段不进 key。"""
    return (
        (user_text or "").strip().lower(),
        qtype or "",
        ctx.get("current_exhibit") or "",
        ctx.get("browse_mode") or "",
        ctx.get("engine_state") or "",
    )


def _cache_get(key: tuple) -> Optional[dict]:
    item = _decision_cache.get(key)
    if not item:
        return None
    raw, expires = item
    if time.monotonic() > expires:
        _decision_cache.pop(key, None)
        return None
    _decision_cache.move_to_end(key)
    return raw


def _cache_put(key: tuple, raw: dict) -> None:
    _decision_cache[key] = (raw, time.monotonic() + _CACHE_TTL_SEC)
    _decision_cache.move_to_end(key)
    while len(_decision_cache) > _CACHE_MAX:
        _decision_cache.popitem(last=False)

# handoff 白名单分两层 — 解释见 should_handoff():
#  - _HANDOFF_QTYPES_ALWAYS:  无论 OpenClaw 开关都 handoff (有 ctx-rich 需求 / 没 V5 retriever 兜底)
#  - _HANDOFF_QTYPES_WHEN_OC: 仅 OpenClaw 开关 ON 时 handoff; 关时退回 V5 retriever 自己的 LLM 路径
# 设计意图: 让管理后台 "OpenClaw 决策桥" 开关变成真正的 "OpenClaw vs V5 LLM" 切换。
# 开 → 知识/通用问答都让 OpenClaw 接 (统一口径 + 命令链)
# 关 → 走 V5 retriever 内部 doubao 直连 (省 WS 一跳, 但失去命令链 + 统一人格)
_HANDOFF_QTYPES_ALWAYS = {
    HALL_CONTEXT,
}
_HANDOFF_QTYPES_WHEN_OC = {
    HALL_KNOWLEDGE,
    GENERAL_KNOWLEDGE,
    # SOCIAL_FALLBACK 暂不进 — "你好/再见" 走 social_retriever 更快
    # REALTIME_INFO 不进 — 天气/时间有高德/wttr 结构化直答, 不需要 LLM
}

# 复合指令探测: TOUR_CONTROL 默认走 V5 单意图链, 但用户说"先X再Y"/"去找X打个招呼"
# 这种含连接词或多动作的句子, 单一 navigate_to/play_resource 表达不了, 需要让
# OpenClaw 拆成 commands 数组顺序执行 (依托 KNOWLEDGE_PRIMITIVES.md + KNOWLEDGE_PROTOCOL.md)。
# 命中以下任一即视为复合指令, 强制 handoff 给 OpenClaw, 哪怕 qtype=TOUR_CONTROL。
_COMPOUND_CONNECTIVES = re.compile(
    r"然后|再.{0,4}[一来]?去|接着|顺便|之后|最后|"
    r"先(?:.{0,8})再|"
    # "打招呼/问好/告别" 只在指向"具体某个人"时才算复合 (= 需要 nav→tts 串行).
    # 排除 "给大家/各位/现场/观众/全场/你们/我们" — 这种是面向所有人的自我介绍,
    # 走 self_intro 单 TTS, 不需要 OpenClaw 拆解。
    r"(?:跟|和|向|找|给)(?!\s*(?:大家|各位|现场|全场|观众|你们|我们))[^,，。!?]{1,8}打(?:个|声)?招呼|"
    r"(?:跟|和|向|找|给)(?!\s*(?:大家|各位|现场|全场|观众|你们|我们))[^,，。!?]{1,8}问个?好|"
    r"(?:跟|和|向|找|给)(?!\s*(?:大家|各位|现场|全场|观众|你们|我们))[^,，。!?]{1,8}告(?:个)?别|"
    r"去找(?:[^我].|$)|去跟(?:[^我].|$)"
)
# 含动作动词关键词 (≥2 个) 也视为复合: "去 + 看/讲解/打开 + 关掉" 等
_ACTION_VERBS = (
    "去", "带", "回", "走",
    "讲解", "讲讲", "介绍", "说",
    "打开", "关掉", "开", "关", "切换", "切到", "换",
    "充电", "回充", "归位", "停靠",
    "播放", "播", "暂停", "继续",
    "打招呼", "问好", "告别",
    "跳", "唱", "舞蹈", "跳舞",
)


# "问/告诉/帮说/跟X说" 后面的内容 — 到达后要问/说的话
_FOLLOWUP_RE = re.compile(
    r"(?:问问?(?:他|她|您|你|她他|大家|各位)?|"
    r"告诉(?:他|她|您|你|大家)?|"
    r"帮\s*(?:我|他|她|你)?\s*(?:说|问)|"
    r"跟(?:他|她|您|你|.{1,6})\s*说)\s*"
    r"([^,，。.!?！？][^,，。.!?！？]{1,40})"
)

# 段间分隔词 (按这些切分多步)
# - 普通连词: 然后/接着/再来/再去/再回/之后/最后/顺便
# - 等待型分隔词: "过(了) N 秒/分/小时/会儿(后)" / "等 N 秒"  → 切段 + 提取秒数
_SEG_SPLIT_RE = re.compile(
    r"过(?:了)?\s*[一二三四五六七八九十两半几0-9]*\s*"
    r"(?:秒钟|秒|分钟|分(?!钟)|个?小时|钟头|会儿?)(?:之?后)?(?:\s*再)?"
    r"|"
    r"等(?:一?会儿?|了)?\s*[一二三四五六七八九十两半几0-9]*\s*"
    r"(?:秒钟|秒|分钟|分(?!钟)|个?小时|钟头)?(?:之?后)?(?:\s*再)?"
    r"|"
    r"然后|接着|再来|再去|再回|之后|最后|顺便"
)

# 末尾的"回/去/到 + 地点" 当导航段 (也认 "来 X")
_TRAILING_NAV_RE = re.compile(
    r"(?:再?\s*回\s*(?:到|来|去)?|再?\s*去\s*(?:到)?|到|前往|返回|来(?:到)?)\s*"
    r"([^,，。.!?！？\s]{1,12})"
)

# 中文小数转 int (含 几/半 的特殊量词)
_CN_NUM_TINY: dict[str, float] = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十五": 15, "二十": 20, "三十": 30,
    "几": 5,    # "过了几秒" 默认按 5 秒
    "半": 0.5,
}


def _parse_wait_seconds(sep_text: str) -> int:
    """从分隔词文本里抽出"等待秒数"。命中 0 表示不是等待类。

    例: "过了几秒钟后" → 5
    例: "等 10 秒" → 10
    例: "等一会儿" → 5
    例: "过半分钟之后" → 30
    上限钳到 60 秒, 防 LLM/用户随手写"等 1 小时"卡死链路。
    """
    if not sep_text:
        return 0
    if not re.search(r"过(?:了)?|等", sep_text):
        return 0
    m = re.search(
        r"([一二三四五六七八九十两半几0-9]+)?\s*(秒钟|秒|分钟|分(?!钟)|会儿?|小时|钟头)",
        sep_text,
    )
    if not m:
        # "等一下" / "等会儿" 不带显式单位 — 默认 3 秒
        if re.search(r"等(?:一?(?:会儿?|下))?", sep_text):
            return 3
        return 0
    amount_str = (m.group(1) or "").strip()
    unit = m.group(2)
    if amount_str.isdigit():
        amount: float = float(amount_str)
    elif amount_str == "":
        amount = 1.0
    elif amount_str in _CN_NUM_TINY:
        amount = float(_CN_NUM_TINY[amount_str])
    else:
        amount = 3.0
    if unit in ("秒", "秒钟"):
        secs = amount
    elif unit in ("分", "分钟"):
        secs = amount * 60
    elif unit in ("小时", "钟头"):
        secs = amount * 3600
    elif unit in ("会", "会儿"):
        secs = 5.0
    else:
        secs = 3.0
    return max(1, min(60, int(round(secs))))


def _extract_followup_question(text: str) -> str:
    """从复合指令原话里提取"到达后要问/说的话"。

    例: "去跟江总打个招呼问他晚上想吃什么" → "晚上想吃什么"
    例: "跟王总告诉他明天会议改到下午" → "明天会议改到下午"
    会用 _SEG_SPLIT_RE 把"问他X然后回工位"中的"然后回工位"切掉, 只保留 X。
    没匹配到返回空串, 调用方会退化成纯打招呼。
    """
    if not text:
        return ""
    m = _FOLLOWUP_RE.search(text)
    if not m:
        return ""
    raw = (m.group(1) or "").strip()
    # 只保留到下一个段落分隔词为止 ("X 然后 Y" → "X")
    m2 = _SEG_SPLIT_RE.search(raw)
    if m2:
        raw = raw[:m2.start()].strip()
    return raw.strip("，。,.!?！？吧呢啊哦了的")


def _parse_trailing_segments(text: str, primary_target: str) -> list:
    """把 "X 然后 Y 过 N 秒后 Z" 切成段, 解析后续段为 commands。

    primary_target: OpenClaw 给的第一站, 用于排除被 _build_arrival_tts 处理过的首段。

    支持的分隔词:
    - 普通: 然后 / 接着 / 再来 / 再去 / 再回 / 之后 / 最后 / 顺便
    - 等待型: 过(了) N 秒 / 过 N 分钟后 / 等一会儿 / 等 5 秒
              这种会先插一条 {"type":"wait","seconds":N}, 再处理后段动作

    例: "去跟姜总打招呼问他晚上想吃什么过几秒后再回工位"
    → 首段"去跟姜总...问他晚上想吃什么" 由 _build_arrival_tts 处理 (nav+tts)
    → 分隔词"过几秒后再" 命中等待型 → +wait(5)
    → 后段"回工位" → +nav(工位)
    """
    if not text:
        return []
    splitters = list(_SEG_SPLIT_RE.finditer(text))
    if not splitters:
        return []
    out: list = []
    for i, m in enumerate(splitters):
        sep_text = m.group(0)
        # 等待型分隔词 → 插 wait 步骤
        wait_secs = _parse_wait_seconds(sep_text)
        if wait_secs > 0:
            out.append({"type": "wait", "seconds": wait_secs})
        # 段范围
        seg_start = m.end()
        seg_end = splitters[i + 1].start() if i + 1 < len(splitters) else len(text)
        seg = text[seg_start:seg_end].strip()
        if not seg:
            continue
        # 1. 导航段 "回工位" / "去 X" / "到 X" / "回到 X" / "来 X"
        m_nav = _TRAILING_NAV_RE.match(seg)
        if m_nav:
            tgt = (m_nav.group(1) or "").strip(" 的了，,。.！!？?吧呢啊哦")
            if tgt and tgt != primary_target:
                out.append({"type": "navigate_to", "target": tgt})
                rest = seg[m_nav.end():].strip()
                if rest:
                    sub_q = _extract_followup_question(rest)
                    if sub_q:
                        out.append({"type": "tts", "text": sub_q})
                continue
        # 2. 告诉/问/说 类
        sub_q = _extract_followup_question(seg)
        if sub_q:
            out.append({"type": "tts", "text": sub_q})
            continue
        # 3. 单纯地点段(不带"回/去")兜底: "工位" / "充电桩"
        if 1 <= len(seg) <= 8 and seg != primary_target:
            out.append({"type": "navigate_to", "target": seg})
    return out


def _build_arrival_tts(text: str, target: str) -> str:
    """根据用户原话 + 目标人, 合成"到达后该说的话"。"""
    payload = _extract_followup_question(text)
    if not payload:
        # 仅打招呼场景
        return f"您好{target}！"
    # 把陈述当问题: 含问询词时补问号
    is_question = any(kw in payload for kw in (
        "什么", "哪", "怎么", "几", "为啥", "为什么", "吗", "呢", "怎样", "如何",
    ))
    if is_question and not payload.endswith(("?", "？")):
        payload = payload.rstrip("。.") + "?"
    return f"{target}您好，{payload}"


def _has_greet_or_ask_intent(text: str) -> bool:
    """用户原话是否明显含"打招呼/问候/告诉/问问"等"到达后还要说话"语义。"""
    return bool(re.search(
        r"打(?:个|声)?招呼|问个?好|告别|"
        r"问问?(?:他|她|您|你|大家|各位)|"
        r"告诉(?:他|她|您|你|大家)|"
        r"帮\s*(?:我|他|她)?\s*说|"
        r"跟(?:他|她|您|你|.{1,6})\s*说",
        text or "",
    ))


def _inject_post_question_wait(cmds: list,
                               max_secs: int = 60,
                               idle_threshold: int = 5,
                               initial_wait: int = 8) -> list:
    """在"问句 TTS → 非 TTS 下一步"之间自动插一段 wait_until_idle:
    阻塞 dispatch 直到用户答完(连续 idle_threshold 秒静默)或者超 max_secs 兜底。

    跟之前简单 wait(N) 的区别: 用户在答的时候不会被打断, 答完才走下一步。
    """
    if not cmds:
        return cmds
    out: list = []
    for i, c in enumerate(cmds):
        out.append(c)
        if not isinstance(c, dict):
            continue
        ctype = (c.get("type") or "").strip().lower()
        if ctype not in ("tts", "speak"):
            continue
        text = (c.get("text") or "").rstrip(" 吧呢啊哦了，。.!！")
        if not text.endswith(("?", "？")):
            continue
        # 下一步是否需要等
        nxt = cmds[i + 1] if i + 1 < len(cmds) else None
        if not isinstance(nxt, dict):
            continue
        nxt_type = (nxt.get("type") or "").strip().lower()
        if nxt_type in ("tts", "speak", "wait", "wait_until_idle", "listen"):
            continue  # 后面已经有等待/收音类, 不重插
        out.append({
            "type": "wait_until_idle",
            "max_seconds": int(max_secs),
            "idle_threshold": int(idle_threshold),
            "initial_wait": int(initial_wait),
        })
    return out


def augment_compound_decision(decision, text: str):
    """复合指令兜底, 两种缺陷场景都处理:

    场景 A: OpenClaw 只输出 single decision (e.g. navigate_to) + 长 reply, commands 为空。
            → V5 用原话合成 [nav, arrival_tts, ...trailing nav] 链。
    场景 B: OpenClaw 出了 commands 链, 但全是 nav 没 tts (用户明明说要"打招呼/问 X")。
            → V5 在第一段 nav 后强行插一条 tts 把问候/提问补上。

    返回 (可能修改过的) decision 对象。
    """
    if not text or not decision:
        return decision
    if not _looks_compound(text):
        return decision

    existing_cmds = getattr(decision, "commands", None) or []

    # 场景 B: commands 已有 — 不论缺啥都做 3 件事:
    #   1. 用 user 原话解析 trailing 段 (然后/接着/再 X / 过 N 秒 / 回 X) → 补到末尾
    #      OpenClaw 经常拆成"先问完, 用户答了再决定第二段"两次调用, V5 必须自己合成完整链
    #   2. 缺 tts 且用户原话明显要说话 → 强行插 tts
    #   3. 问句 tts 后插 wait_until_idle 等用户答完
    if existing_cmds:
        has_tts = any(
            isinstance(c, dict) and (c.get("type") or "").lower() in ("tts", "speak")
            for c in existing_cmds
        )
        # 找 commands 已有的 nav target 当 primary, 给 _parse_trailing_segments 跳过首段用
        primary_target_in_cmds = ""
        for c in existing_cmds:
            if isinstance(c, dict) and (c.get("type") or "").lower() in ("navigate_to", "nav"):
                primary_target_in_cmds = (c.get("target") or "").strip()
                break
        # 1. 解析原话 trailing, 补到末尾 (同 target 已经在的 nav 跳过避免重复)
        trailing = _parse_trailing_segments(text, primary_target=primary_target_in_cmds)
        full_cmds = list(existing_cmds)
        existing_nav_targets = {
            (c.get("target") or "").strip()
            for c in full_cmds
            if isinstance(c, dict) and (c.get("type") or "").lower() in ("navigate_to", "nav")
        }
        appended = 0
        for t in trailing:
            if not isinstance(t, dict):
                continue
            ttype = (t.get("type") or "").strip().lower()
            if ttype in ("navigate_to", "nav"):
                tgt = (t.get("target") or "").strip()
                if not tgt or tgt in existing_nav_targets:
                    continue
                existing_nav_targets.add(tgt)
            full_cmds.append(t)
            appended += 1
        # 2. 缺 tts + 明确要说话 → 在 primary nav 后插 tts
        if (not has_tts) and _has_greet_or_ask_intent(text) and primary_target_in_cmds:
            arrival_tts = _build_arrival_tts(text, primary_target_in_cmds)
            if arrival_tts:
                # 找第一个 nav 的位置, 之后插 tts
                for idx, c in enumerate(full_cmds):
                    if isinstance(c, dict) and (c.get("type") or "").lower() in ("navigate_to", "nav"):
                        full_cmds.insert(idx + 1, {"type": "tts", "text": arrival_tts})
                        appended += 1
                        break
        # 3. 问句 TTS 后自动插 wait_until_idle
        full_cmds = _inject_post_question_wait(full_cmds)
        if full_cmds != existing_cmds:
            logger.info(
                "[DecisionBridge] 已有 commands 链, augment: 原%d步 → 现%d步 (补 trailing/wait/tts)",
                len(existing_cmds), len(full_cmds),
            )
            decision.commands = full_cmds
            # 缩短 reply 避免与新插的 arrival_tts 重复
            if appended and primary_target_in_cmds:
                decision.reply = f"好的，这就去找{primary_target_in_cmds}。"
        return decision
        # 找第一个 nav 的 target 当问候对象
        first_nav_target = ""
        for c in existing_cmds:
            if isinstance(c, dict) and (c.get("type") or "").lower() in ("navigate_to", "nav"):
                first_nav_target = (c.get("target") or "").strip()
                break
        if not first_nav_target:
            return decision
        arrival_tts = _build_arrival_tts(text, first_nav_target)
        if not arrival_tts:
            return decision
        # 在第一个 nav 之后插入 tts
        for i, c in enumerate(existing_cmds):
            if isinstance(c, dict) and (c.get("type") or "").lower() in ("navigate_to", "nav"):
                existing_cmds.insert(i + 1, {"type": "tts", "text": arrival_tts})
                break
        # 问句 TTS 后自动插 wait 给用户答复时间
        existing_cmds = _inject_post_question_wait(existing_cmds, default_secs=10)
        decision.commands = existing_cmds
        # 缩短 reply 避免双重 TTS
        decision.reply = f"好的，这就去找{first_nav_target}。"
        logger.info(
            "[DecisionBridge] commands 缺 tts, 在 nav(%s) 后补一条: %r",
            first_nav_target, arrival_tts,
        )
        return decision

    # 场景 A: 没 commands, single decision
    if getattr(decision, "decision", "") != "navigate_to":
        return decision
    target = (getattr(decision, "target", "") or "").strip()
    # OpenClaw 偶尔把多个目标用逗号拼成 "姜总,工位" 这种, 取第一个
    if "," in target or "，" in target:
        target = re.split(r"[,，]", target)[0].strip()
    if not target:
        return decision
    arrival_tts = _build_arrival_tts(text, target)
    if not arrival_tts:
        return decision
    cmds = [
        {"type": "navigate_to", "target": target},
        {"type": "tts", "text": arrival_tts},
    ]
    # 解析"然后/接着/再/过 N 秒"之后的次级段, 接成多步
    # (e.g. "...过几秒钟然后回工位" → +wait(5) +nav(工位))
    trailing = _parse_trailing_segments(text, primary_target=target)
    cmds.extend(trailing)
    # 问句 TTS 后自动插 wait 给用户答复时间
    cmds = _inject_post_question_wait(cmds, default_secs=10)
    logger.info(
        "[DecisionBridge] 复合指令 V5 兜底合成 commands: target=%s arrival=%r 后续段=%d 总步=%d",
        target, arrival_tts, len(trailing), len(cmds),
    )
    decision.commands = cmds
    # 缩短 reply 避免双重 TTS — 导航前只播一句短预告, 内容由 arrival_tts 在到达后说
    decision.reply = f"好的，这就去找{target}。"
    return decision


def _looks_compound(text: str) -> bool:
    """检测复合指令: 含连接词 或 动作动词≥2 个。

    误伤控制: 只在文本 ≥6 字才判定 (避免 "去开灯" 这类被切成 2 动词复合);
    含问号 → 不算 (是问句不是命令)。
    """
    t = (text or "").strip()
    if len(t) < 6 or "?" in t or "？" in t:
        return False
    if _COMPOUND_CONNECTIVES.search(t):
        return True
    hits = sum(1 for v in _ACTION_VERBS if v in t)
    return hits >= 2

# 精简客户端 system prompt：完整 schema 已在 OpenClaw 服务端的
# agents.list[exhibition-baoxiaomi].systemPromptOverride 里 (含 capabilities
# 枚举), 这里只做一个简短提醒, 服务端配置被误清空时也能保底。
_SYSTEM_PROMPT = """你是展厅导览机器人的决策引擎, 只输出一个纯 JSON 对象, 不要 markdown 也不要思考标签。

【字段】
decision: answer_only | navigate_to | list_exhibits | execute_flow | ask_clarify | fallback
reply: 给用户的简短话术 (≤30字)
should_interrupt: bool
resume_policy: none | ask_next
target / flow_name: 单步动作的目标
confidence: 0~1
commands: 数组, 复合指令必填 (见下)

【复合指令必须用 commands 数组完整列出每一步】

凡用户原话含连接词(然后/接着/再/之后/最后/顺便)、多动作动词(去 X 然后做 Y)、
或人际交流意图(打招呼/问好/告诉/告别/帮我说/问问)，**禁止只回单步 decision**。
必须把每一步都铺到 commands 里, 一步对应一条 primitive (见 KNOWLEDGE_PRIMITIVES.md)。
target 字段不要拼多个目标 (像 "姜总,工位" 这种是错的)。

【正例】
用户: "去跟姜总打个招呼问他晚上想吃什么然后回来工位"
正确输出:
{
  "decision":"execute_flow","reply":"好的, 去找姜总。",
  "commands":[
    {"type":"navigate_to","target":"姜总"},
    {"type":"tts","text":"姜总您好, 您晚上想吃什么呀?"},
    {"type":"navigate_to","target":"工位"}
  ],
  "confidence":0.9
}

【反例 — 禁止】
错: {"decision":"navigate_to","target":"姜总,工位","reply":"我先去找姜总然后回工位"}  ← target 拼两个
错: {"decision":"navigate_to","target":"姜总","reply":"我先去打招呼再问他然后回工位"}  ← 没列 commands
错: commands:[{"type":"navigate_to"...},{"type":"navigate_to"...}]  ← 漏了 tts 步骤

【单步指令照旧用 single decision】
"开灯" → {"decision":"answer_only","commands":[{"type":"lights_on"}], "reply":"好,灯开了。"}
"附近有什么美食" → V5 已直接走 POI 检索器, 这种 OpenClaw 不会接到。

【宁可多步, 不可漏步】
用户每一句"打招呼/问候/告别/告诉/问问 X" 都要对应 commands 里**真实**的 tts 命令,
不能只在 reply 里描述意图。reply 是预告, commands 才是真正会执行的动作。"""


@dataclass
class Decision:
    decision: str
    reply: str
    should_interrupt: bool
    resume_policy: str
    target: str
    flow_name: str
    confidence: float
    raw: dict
    # 命令链 (新): OpenClaw 可以输出一串 primitive 命令让 V5 顺序执行,
    # 比单一 decision 表达力更强 (一条话同时导航 + 调资源 + 控灯)。
    # 每项至少含 {"type": "..."}, 其它字段按 type 决定 (见 KNOWLEDGE_*.md)。
    commands: list = None  # type: ignore

    def __post_init__(self):
        if self.commands is None:
            self.commands = []

    def as_qa_result(self, qtype: str) -> dict:
        """翻译成 answer_engine.answer() 的返回格式"""
        return {
            "answer": self.reply,
            "source": "openclaw",
            "confidence": self.confidence,
            "resume_policy": self.resume_policy if self.resume_policy in _ALLOWED_RESUME else "none",
            "action": self.decision,
            "qtype": qtype,
            "extra": {
                "target": self.target,
                "flow_name": self.flow_name,
                "should_interrupt": self.should_interrupt,
                "commands": self.commands or [],
            },
        }


# ============================================================
# 门控
# ============================================================
async def should_handoff(qtype: str, text: str) -> bool:
    """决定该条输入是否走 decide() (即统一决策桥)。

    路由表 (跟管理后台 "OpenClaw 决策桥" 开关联动):

        qtype                      OC=ON      OC=OFF
        ---------------------------------------------
        HALL_CONTEXT               handoff    handoff (decide 内部转 V5 LLM 拆 commands)
        HALL_KNOWLEDGE             handoff    V5 retriever (内部 doubao)
        GENERAL_KNOWLEDGE          handoff    V5 retriever (内部 doubao)
        复合指令 (任何 qtype)      handoff    handoff (decide 内部转 V5 LLM 拆 commands)
        TOUR_CONTROL / SOCIAL / 其它  原 V5 链  原 V5 链

    设计要点: OC 开关 ON 时, OpenClaw 真正"替换"V5 LLM 做知识/通用问答;
              OC 开关 OFF 时, 这些请求回到 V5 retriever 自带的 doubao 路径,
              不再绕 decide() → _llm_decide → 同款 doubao 那一圈, 省一次冗余 LLM。
    """
    if not text or not text.strip():
        return False
    # 1. 复合指令永远 handoff — V5 单意图链表达不了"先X再Y"串行
    if _looks_compound(text):
        logger.info("[DecisionBridge] 复合指令 → handoff (qtype=%s): %r", qtype, text[:60])
        return True
    # 2. 无条件 handoff 类
    if qtype in _HANDOFF_QTYPES_ALWAYS:
        return True
    # 3. 仅当 OpenClaw 开 时 handoff (开 = 用 OC 替换 V5 LLM, 关 = 走 V5 retriever 自带 LLM)
    if qtype in _HANDOFF_QTYPES_WHEN_OC:
        try:
            return await openclaw_client.is_enabled()
        except Exception:
            return False
    return False


# ============================================================
# 决策主入口
# ============================================================
async def _llm_decide(text: str, qtype: str, ctx: dict) -> str:
    """OpenClaw 关闭模式 — 直接调 LLM, 让它输出 OpenClaw 同款 JSON 决策 (含 commands)。

    返回原始 content 字符串, 由调用方走相同的 _parse() 解析。
    LLM 失败/空返回返回空串, 让 caller 走 fallback。
    """
    try:
        from app.engine.llm import chat_completion
    except Exception:
        return ""
    robot_name = ctx.get("robot_name") or "导览机器人"
    robot_kind = ctx.get("robot_kind") or "baoxiaomi"
    has_screen = ctx.get("has_screen", True)
    sys_prompt = _SYSTEM_PROMPT  # 复用同款决策协议
    # 简短 ctx 列出 — 不像 OpenClaw agent 知识库, LLM 短上下文需要直接拼
    ctx_lines = []
    for k in ("robot_name", "robot_kind", "has_screen", "engine_state",
              "current_exhibit", "current_special", "browse_mode",
              "current_datetime", "current_weekday", "time_of_day"):
        if k in ctx and ctx[k] not in (None, ""):
            ctx_lines.append(f"  {k}: {ctx[k]}")
    user_msg = (
        f"用户说: {text!r}\nqtype: {qtype}\n上下文:\n" + "\n".join(ctx_lines) +
        "\n\n请按上面 system prompt 的格式输出决策 JSON, 复合指令必须给 commands 数组。"
    )
    if robot_kind == "unitree" and not has_screen:
        user_msg += "\n注意: 当前是宇树机器人(无屏幕), 不要输出 show_list/show_card/show_text/clear_screen。"
    try:
        content = await asyncio.wait_for(
            chat_completion(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=sys_prompt,
                max_tokens=400,  # 决策 JSON 通常 100-300 tokens
                temperature=0.2,  # 决策类需要确定性
            ),
            timeout=7.0,
        )
        return content or ""
    except asyncio.TimeoutError:
        logger.warning("[DecisionBridge] LLM decide 超时 7s")
        return ""
    except Exception as e:
        logger.warning("[DecisionBridge] LLM decide 异常: %s", e)
        return ""


async def decide(
    text: str,
    qtype: str,
    slots: dict,
    world_state,
    robot_id: str,
) -> Optional[Decision]:
    """
    返回 Decision，或者 None（表示降级到 V5 原逻辑）。
    """
    try:
        ctx = _build_context(world_state, slots, robot_id=robot_id)
        # 把当前机器人/数字人名字注入 context, 让 OpenClaw 生成的 reply
        # 里自称对应机器人的名字。
        # - baoxiaomi: 用 system_config.robot_name (默认"小豹")
        # - unitree:   用 system_config.unitree_robot_name (默认"小勇", 跟唤醒词同名)
        try:
            from app.engine.names import get_names
            rname, dname = await get_names()
            ctx["digital_human_name"] = dname
            if ctx.get("robot_kind") == "unitree":
                # 优先读宇树自己的配置, 没配就回落 "小勇"(唤醒词)
                from app.database import get_db
                db = await get_db()
                try:
                    rows = await db.execute_fetchall(
                        "SELECT value FROM system_config WHERE key='unitree_robot_name'"
                    )
                    if rows and rows[0]["value"]:
                        ctx["robot_name"] = rows[0]["value"]
                    else:
                        ctx["robot_name"] = "小勇"
                finally:
                    await db.close()
            else:
                ctx["robot_name"] = rname
        except Exception:
            pass

        # 缓存命中即跳过下游调用; 缓存只存稳定的决策 (见 _cache_put 处)。
        ck = _cache_key(text, qtype, ctx)
        cached = _cache_get(ck)
        if cached is not None:
            logger.info("[DecisionBridge] cache HIT key=%s", ck)
            return _decision_from_raw(cached)

        # OpenClaw 启用 → 走原 OpenClaw 路径;
        # 关闭 → 直接调 LLM 拆 commands JSON, 节省 WS 握手 + agent 处理 ~1-3s
        oc_enabled = await openclaw_client.is_enabled()
        if oc_enabled:
            user_payload = json.dumps(
                {
                    "session_id": robot_id or "unknown",
                    "user_text": text,
                    "qtype_hint": qtype,
                    "context": ctx,
                },
                ensure_ascii=False,
            )
            logger.info(
                "[DecisionBridge] handoff → OpenClaw qtype=%s text=%r ctx_keys=%s",
                qtype, text[:40], list(ctx.keys()),
            )
            content = await asyncio.wait_for(
                openclaw_client.chat(_SYSTEM_PROMPT, user_payload, session_key=None),
                # 2026-05-22 放宽到 18s — 新 gateway gemma4 warm ~5-6s, knowledge 类长答 ~11s.
                # 旧 8s 阈值会把 knowledge 类问答全部踢到 fallback. 18s 覆盖到 p99 又给冷启动留余地.
                timeout=18.0,
            )
        else:
            # OpenClaw 关闭模式 — 直接调 LLM, 由 LLM 输出同款 JSON 决策
            logger.info(
                "[DecisionBridge] handoff → LLM (OpenClaw disabled) qtype=%s text=%r",
                qtype, text[:40],
            )
            content = await _llm_decide(text, qtype, ctx)
    except asyncio.TimeoutError:
        logger.warning("[DecisionBridge] 决策外层超时(18s) → fallback")
        return None
    except openclaw_client.OpenClawDisabled:
        logger.debug("[DecisionBridge] OpenClaw disabled (race) → fallback")
        return None
    except openclaw_client.OpenClawError as e:
        logger.warning("[DecisionBridge] OpenClaw error → fallback: %s", e)
        return None
    except Exception as e:
        logger.error("[DecisionBridge] unexpected error → fallback: %s", e, exc_info=True)
        return None

    decision = _parse(content)
    if decision is None:
        logger.warning("[DecisionBridge] parse fail → fallback raw=%r", content[:200])
        return None

    if decision.confidence < _MIN_CONFIDENCE:
        logger.info(
            "[DecisionBridge] low confidence %.2f < %.2f → fallback (decision=%s)",
            decision.confidence,
            _MIN_CONFIDENCE,
            decision.decision,
        )
        return None

    if decision.decision == "fallback":
        logger.info(
            "[DecisionBridge] model asked fallback conf=%.2f reply=%r raw=%r",
            decision.confidence,
            (decision.reply or "")[:120],
            json.dumps(decision.raw, ensure_ascii=False)[:300],
        )
        return None

    logger.info(
        "[DecisionBridge] decision=%s confidence=%.2f interrupt=%s resume=%s target=%r flow=%r reply=%r",
        decision.decision,
        decision.confidence,
        decision.should_interrupt,
        decision.resume_policy,
        decision.target,
        decision.flow_name,
        (decision.reply or "")[:60],
    )

    # 写入决策缓存; 只缓存稳定 + 高置信度结果, 避免把 ask_clarify/execute_flow
    # 这种依赖即时状态或有副作用的决策错误地命中。
    # 注意: 在 augment_compound_decision 之前缓存原始 raw, 兜底合成的 commands
    # 不进缓存(每次原话不同, 重新合成更准)。
    if (
        decision.decision in _CACHEABLE_DECISIONS
        and decision.confidence >= _CACHE_MIN_CONF
    ):
        _cache_put(ck, decision.raw)

    # 复合指令兜底: 用户说"去找X打招呼问他Y"但 OpenClaw 只回单步 navigate_to 时,
    # V5 自动合成 commands=[nav, arrival_tts] 让到达后第二动作能真发出去。
    decision = augment_compound_decision(decision, text)

    return decision


def _decision_from_raw(data: dict) -> Optional["Decision"]:
    """缓存命中时把存好的 raw dict 还原成 Decision; 失败返回 None 让上层走 OpenClaw。"""
    try:
        cmds = data.get("commands") if isinstance(data.get("commands"), list) else []
        return Decision(
            decision=str(data.get("decision", "")).strip(),
            reply=str(data.get("reply", "")).strip(),
            should_interrupt=bool(data.get("should_interrupt", False)),
            resume_policy=str(data.get("resume_policy", "none")).strip(),
            target=str(data.get("target") or "").strip(),
            flow_name=str(data.get("flow_name") or "").strip(),
            confidence=float(data.get("confidence", 0.0)),
            raw=data,
            commands=cmds,
        )
    except Exception:
        return None


# ============================================================
# 内部：上下文组装
# ============================================================
def _build_context(world_state, slots: dict | None, robot_id: str = "") -> dict:
    """
    给 OpenClaw 的"现场快照"。字段尽量精简但覆盖足以判断意图的关键状态：
    展项/专场/位置 + 对话引擎状态 + 浏览模式 + flow 运行态 + 当前屏幕资源 + 机器人型号。
    只挑非空字段，减少 token。
    """
    ctx: dict = {}
    # 标识当前是哪一代机器人在问 OpenClaw - 决定输出 commands 时能用哪些 primitive:
    # 宇树没屏幕, 不能用 show_list/show_card/show_text/clear_screen;
    # 宇树测试期 navigate_to 也被 V5 守住 (unitree_nav_enabled=0), 复合指令应优先 motion/dance/tts。
    rid = (robot_id or "").lower()
    if rid.startswith("unitree") or "g1" in rid or "h1" in rid:
        ctx["robot_kind"] = "unitree"
        ctx["has_screen"] = False
    elif rid:
        ctx["robot_kind"] = "baoxiaomi"
        ctx["has_screen"] = True
    if world_state is not None:
        try:
            # 访客身份 + 历史记忆 (SmartEngine 在 _handle_speech_impl 顶部存入 ws._visitor_memory_context)
            # 人脸识别/visitor_memory_service 组装好的字符串, 直接作为 LLM 上下文的一段注入.
            visitor_ctx = getattr(world_state, "_visitor_memory_context", "") or ""
            if visitor_ctx:
                ctx["visitor_memory"] = visitor_ctx[:1000]  # 防 token 爆炸

            # 结构化身份: 由人脸识别 + visitor_robot_bindings 推出, V5 世界模型的真实状态。
            # systemPromptOverride 里规定: 这两个字段是权威身份, 优先级高于用户自报。
            av = getattr(world_state, "_active_visitor", None) or {}
            if isinstance(av, dict):
                vn = (av.get("display_name") or av.get("name") or "").strip()
                if vn:
                    ctx["verified_visitor_name"] = vn
                if av.get("id"):
                    ctx["verified_visitor_id"] = av["id"]

            # 上一轮命令分发失败时, 把错误 + 候选名注入 ctx 让 LLM 自纠;
            # smart_engine._dispatch_command_list 在每次新命令前会清空, 所以
            # 这里只在它非空时注入。
            cmd_errors = getattr(world_state, "_last_command_errors", None) or []
            if cmd_errors:
                ctx["last_command_errors"] = cmd_errors[-5:]  # 截断防 token 爆

            ex = world_state.exhibit
            if ex.current_exhibit_name:
                ctx["current_exhibit"] = ex.current_exhibit_name
            # current_exhibit_id 与 name 冗余, 决策只看名字, 不发 id 省 token。
            if ex.current_resource_name:
                ctx["current_screen_resource"] = ex.current_resource_name
            if ex.current_special_name:
                ctx["current_special"] = ex.current_special_name

            robot = world_state.get_active_robot() if hasattr(world_state, "get_active_robot") else None
            if robot:
                if robot.position:
                    ctx["current_position"] = robot.position
                # 电量: 让 LLM 看到, 低电量 (<20%) 时主动建议 go_charging。
                bat = getattr(robot, "battery", None)
                if bat is not None:
                    try:
                        ctx["battery_pct"] = int(bat)
                    except Exception:
                        pass

            dialog = getattr(world_state, "dialog", None)
            if dialog is not None:
                if getattr(dialog, "engine_state", ""):
                    ctx["engine_state"] = dialog.engine_state
                if getattr(dialog, "mode", ""):
                    ctx["dialog_mode"] = dialog.mode
                if getattr(dialog, "last_bot_reply", ""):
                    ctx["last_bot_reply"] = dialog.last_bot_reply[:60]

            browse = getattr(world_state, "browse", None)
            if browse is not None and getattr(browse, "mode", ""):
                ctx["browse_mode"] = browse.mode
                if browse.selected_exhibit_name:
                    ctx["browse_selected_exhibit"] = browse.selected_exhibit_name
                # browse_mode 下用户可能用数字 "1"/"2"/"3" 选展项 — 把当前候选列表给 LLM
                cand = getattr(browse, "candidate_names", None)
                if isinstance(cand, list) and cand:
                    ctx["browse_candidates"] = list(cand)[:10]

            # execution.pending_command 是 V5 内部命令队列状态, 决策无需感知, 不发。

            recent = getattr(world_state, "recent", None)
            if recent is not None and getattr(recent, "last_mentioned_exhibit", ""):
                ctx["last_mentioned_exhibit"] = recent.last_mentioned_exhibit

            # flow 运行态（script/flow_executor 两种可能都兜一下）
            try:
                from app.engine.flow_executor import get_active_flow
                active = get_active_flow(robot.robot_id) if robot else None
                if active:
                    ctx["flow_running"] = getattr(active, "status", "") == "running"
                    fn = getattr(active, "flow_name", "") or getattr(active, "script_name", "")
                    if fn:
                        ctx["flow_name"] = fn
                    step = getattr(active, "current_step", None) or getattr(active, "step_index", None)
                    if step is not None:
                        ctx["flow_step"] = step
            except Exception:
                pass
        except Exception as e:
            logger.debug("[DecisionBridge] ctx build partial fail: %s", e)

    if slots:
        for k in ("exhibit_name", "topic", "city", "sub"):
            v = slots.get(k)
            if v:
                ctx[k] = v

    # 注入当前日期/时间, 让 LLM 回答 "现在几点"/"今天星期几" 时用真实时间,
    # 不再吐训练数据里的旧日期。OpenClaw 是 stateless 调用, 每次都要带。
    from datetime import datetime
    _now = datetime.now()
    _weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    ctx["current_datetime"] = _now.strftime("%Y-%m-%d %H:%M")
    ctx["current_weekday"] = f"星期{_weekdays[_now.weekday()]}"

    # 时段感知, 让 LLM 寒暄/欢迎语自然贴合 (早安/午安/晚安)。
    h = _now.hour
    if 5 <= h < 11:
        ctx["time_of_day"] = "morning"
    elif 11 <= h < 14:
        ctx["time_of_day"] = "noon"
    elif 14 <= h < 18:
        ctx["time_of_day"] = "afternoon"
    elif 18 <= h < 22:
        ctx["time_of_day"] = "evening"
    else:
        ctx["time_of_day"] = "late_night"

    return ctx


# ============================================================
# 内部：解析
# ============================================================
def _parse(content: str) -> Optional[Decision]:
    if not content:
        return None
    s = content.strip()
    # 去掉 markdown fence
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    # 取第一个 { ... } 块（防多余前后缀）
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        s = m.group(0)

    try:
        data = json.loads(s)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    dec = str(data.get("decision", "")).strip()
    if dec not in _ALLOWED_DECISIONS:
        return None

    reply = str(data.get("reply", "")).strip()
    # answer_only / ask_clarify 必须有 reply
    if dec in ("answer_only", "ask_clarify") and not reply:
        return None

    try:
        confidence = float(data.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    resume = str(data.get("resume_policy", "none")).strip()
    if resume not in _ALLOWED_RESUME:
        resume = "none"

    raw_cmds = data.get("commands")
    commands: list = []
    if isinstance(raw_cmds, list):
        for item in raw_cmds:
            if isinstance(item, dict) and isinstance(item.get("type"), str) and item["type"].strip():
                commands.append(item)

    return Decision(
        decision=dec,
        reply=reply,
        should_interrupt=bool(data.get("should_interrupt", False)),
        resume_policy=resume,
        target=str(data.get("target") or "").strip(),
        flow_name=str(data.get("flow_name") or "").strip(),
        confidence=confidence,
        raw=data,
        commands=commands,
    )
