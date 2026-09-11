"""
IntentSynonyms — 设备/资源/机器人控制指令的同义/谐音/错字词典

为什么单独一个模块:
- INTENT_RULES 的 regex pattern 只能覆盖书面写法, ASR 识别出来的口语变体太多;
- 用字面包含 + 拼音包含 + 编辑距离≤1 覆盖: 同义词/谐音/同音/错字四类.
- 保持数据与逻辑分离, 非开发也能维护 SYNONYM_INTENT_MAP.

调用点: intent_resolver.resolve_intent 在 rule/trigger/pinyin 未命中但 LLM 前调用 _synonym_match(text).
"""

from __future__ import annotations

import logging

from app.engine.intent_resolver import _to_pinyin

logger = logging.getLogger("hub.engine.intent_synonyms")


# 每个意图对应一组"人话同义词". canonical 只是展示; synonyms 是完整集合.
# 每条 term 会被用于 (a)字面包含 (b)拼音包含 (c)编辑距离≤1 三路匹配.
SYNONYM_INTENT_MAP: dict[str, dict] = {
    # ============== 设备控制 ==============
    "lights_on": {
        "canonical": "开灯",
        "synonyms": [
            "开灯", "打开灯", "把灯打开", "把灯开", "灯开开", "灯打开", "灯亮",
            "灯亮起", "灯亮亮", "亮一下灯", "点亮", "照亮", "照明打开",
            "开下灯", "开一下灯", "开个灯", "给我开灯", "帮我开灯",
            "灯开起来", "灯启动", "灯全开", "所有灯都开",
        ],
    },
    "lights_off": {
        "canonical": "关灯",
        "synonyms": [
            "关灯", "关掉灯", "灯关掉", "灭灯", "熄灯", "把灯关", "把灯灭",
            "灯关上", "关上灯", "关个灯", "关一下灯", "关下灯",
            "灯暗一些", "灯关闭", "灯灭了", "灯全关", "所有灯关",
        ],
    },
    "all_on": {
        "canonical": "全开",
        "synonyms": [
            "全开", "全部打开", "都打开", "都开", "全部开启", "全部启动",
            "都开了", "全开启", "一键开机", "一键全开", "都开起来", "全部开",
            "每日全开", "每日都开",
        ],
    },
    "all_off": {
        "canonical": "全关",
        "synonyms": [
            "全关", "全部关闭", "都关", "都关了", "全部关掉", "全部关停",
            "都关掉", "全关闭", "一键关机", "一键全关", "全关掉",
            "每日全关", "每日都关",
        ],
    },
    "ad_screen_on": {
        "canonical": "开广告机",
        "synonyms": [
            "开广告机", "打开广告机", "广告机打开", "广告屏打开", "广告屏开",
            "开一下广告机", "广告机开起来", "开广告屏",
        ],
    },
    "ad_screen_off": {
        "canonical": "关广告机",
        "synonyms": [
            "关广告机", "关掉广告机", "广告机关", "广告机关掉",
            "广告屏关", "广告屏关掉", "关广告屏", "关掉广告屏",
        ],
    },

    # ============== 机器人控制 ==============
    "go_charging": {
        "canonical": "去充电",
        "synonyms": [
            "去充电", "回去充电", "充电去", "回家充电", "充电桩",
            "回充电桩", "回待机位", "回去歇着", "歇会儿", "休息吧",
            "停靠", "待机",
            # 注意: "回去/归位/回到起点/回原位" 不再归 go_charging,
            # 改归 navigate_to(target=接待点) — 现场常态是回接待点继续接待, 不是去充电桩.
            # 真要充电请明说"去充电/回充电桩".
        ],
    },

    # ============== 资源/播放控制 ==============
    "play_next": {
        "canonical": "下一个",
        "synonyms": [
            "下一个", "下一个资源", "换一个", "再看看", "再看一个", "别的",
            "还有什么", "往下", "后面", "后面的", "继续下一个", "跳下一个",
        ],
    },
    "wait_please": {
        "canonical": "暂停",
        "synonyms": [
            "暂停", "停一下", "先停", "先停一下", "先别说",
            "等一下", "稍等", "等等", "先等", "别说了", "先别",
            "暂停一下",
        ],
    },
    "resume_please": {
        "canonical": "继续",
        "synonyms": [
            "继续", "接着", "接着讲", "接着说", "继续讲", "继续说",
            "好了继续", "可以了继续", "开始吧", "你讲",
            "重播", "再播一遍", "再讲一遍", "再说一次", "重新讲",
        ],
    },

    # ============== 查询 ==============
    "query_status": {
        "canonical": "状态",
        "synonyms": [
            "状态", "电量", "电池", "剩余电量", "你在哪", "当前位置",
            "你还好吗", "系统状态", "运行状态",
        ],
    },
    "query_help": {
        "canonical": "帮助",
        "synonyms": [
            "帮助", "你能做什么", "你会什么", "你有什么功能", "功能",
            "怎么用", "能帮我什么", "有什么功能", "操作指南", "使用说明",
            "使用帮助",
        ],
    },

    # ============== 结束 ==============
    "end_tour": {
        "canonical": "结束参观",
        "synonyms": [
            "结束", "结束参观", "参观完", "看完了", "不用了", "可以了",
            "再见", "拜拜", "回去吧", "差不多", "差不多了", "就到这",
            "今天就到", "到这里吧", "下次再来",
        ],
    },
}


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein 编辑距离 (小文本, 纯 Python 足够)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(
                prev[j] + 1,        # 删除
                cur[j - 1] + 1,     # 插入
                prev[j - 1] + (0 if ca == cb else 1),  # 替换
            )
        prev = cur
    return prev[-1]


def synonym_match(text: str) -> dict | None:
    """在同义词字典中查找, 返回最佳匹配 or None.

    匹配层级(按置信度排序):
    1. 字面完全相等 conf=0.95
    2. 字面互为子串 conf=0.75 * len_ratio
    3. 拼音相等 / 拼音互为子串 conf=0.70 * len_ratio
    4. 编辑距离 ≤1 且短串 ≥3 conf=0.65

    最终 conf < 0.55 丢弃, 让 LLM 兜底.
    """
    t = (text or "").lower().strip()
    if not t or len(t) < 2:
        return None
    t_py = _to_pinyin(t)

    best_intent = None
    best_conf = 0.0
    best_term = ""
    best_layer = ""

    for intent, spec in SYNONYM_INTENT_MAP.items():
        for term in spec["synonyms"]:
            tl = term.lower()

            # L1: 字面完全相等 → 立即返回(最强信号)
            if t == tl:
                return {
                    "intent": intent,
                    "confidence": 0.95,
                    "entities": {},
                    "source": "synonym_exact",
                    "matched_term": term,
                }

            # L2: 字面互为子串
            if tl in t or t in tl:
                ratio = min(len(tl), len(t)) / max(len(tl), len(t))
                conf = 0.75 * ratio
                if conf > best_conf:
                    best_conf, best_intent, best_term, best_layer = conf, intent, term, "substr"
                continue

            # L3: 拼音相等 / 子串
            tl_py = _to_pinyin(tl)
            if tl_py and (tl_py == t_py or tl_py in t_py or t_py in tl_py):
                ratio = min(len(tl_py), len(t_py)) / max(len(tl_py), len(t_py))
                conf = 0.70 * ratio
                if conf > best_conf:
                    best_conf, best_intent, best_term, best_layer = conf, intent, term, "pinyin"
                continue

            # L4: 编辑距离 ≤1 (短串至少 3 字, 否则误伤)
            if min(len(tl), len(t)) >= 3 and abs(len(tl) - len(t)) <= 1:
                if _edit_distance(t, tl) <= 1:
                    if 0.65 > best_conf:
                        best_conf, best_intent, best_term, best_layer = 0.65, intent, term, "edit"

    if best_intent and best_conf >= 0.55:
        return {
            "intent": best_intent,
            "confidence": round(best_conf, 3),
            "entities": {},
            "source": f"synonym_{best_layer}",
            "matched_term": best_term,
        }
    return None
