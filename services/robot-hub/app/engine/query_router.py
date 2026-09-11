"""
QueryRouter — 统一问答中枢的入口分类器

把所有用户输入分成 6 类:
  hall_context     当前展项/屏幕/位置相关
  tour_control     导览控制(下一个/去X/结束/列表/设备)
  hall_knowledge   展厅知识(展项之间对比/方案场景)
  realtime_info    实时信息(天气/时间/日期)
  general_knowledge 泛知识(美食/景点/常识)
  social_fallback  社交寒暄

设计原则:
- 纯规则, 不调 LLM, 不查 DB
- WorldState 作为可选 hint (当前展项/browse 模式)
- 单输入只返回一个最佳类型 (优先级: realtime > hall_context > tour_control > hall_knowledge > social > general)
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger("hub.engine.query_router")


# ============================================================
# 分类常量
# ============================================================
HALL_CONTEXT = "hall_context"
TOUR_CONTROL = "tour_control"
HALL_KNOWLEDGE = "hall_knowledge"
REALTIME_INFO = "realtime_info"
GENERAL_KNOWLEDGE = "general_knowledge"
SOCIAL_FALLBACK = "social_fallback"
POI_NEARBY = "poi_nearby"  # 周边美食/景点/酒店/便利店等 — 走高德 API


# ============================================================
# 模式库
# ============================================================
# 实时信息 — 天气/时间/日期
_RE_WEATHER = re.compile(
    r"(天气|气温|温度|多少度|下雨|下雪|冷不冷|热不热|穿什么|天气预报)"
)
_RE_TIME = re.compile(
    r"(现在(几点|什么时候)|几点了|什么时间|当前时间|几点钟)"
)
_RE_DATE = re.compile(
    r"(今天(几号|多少号|星期几|周几|是?几月)|"
    r"明天(几号|多少号|星期几|周几|什么日子)|"
    r"昨天(几号|多少号|星期几|周几)|"
    r"今天.{0,4}日(子|期)|今天什么日子|今天是.{0,4}日|"
    r"什么时候.{0,2}过年|今年.{0,4}多少天)"
)
# 城市 → 拼音 (供 weather provider 用); 这里仅用于辅助提取
_CITY_HINT_RE = re.compile(r"([\u4e00-\u9fa5]{2,6})(?:的)?天气")
# "天气" 前面常被误抓的非城市 token (问候/时间)
_NON_CITY_TOKENS = (
    "您好", "你好", "哈喽", "嗨",
    "今天", "明天", "昨天", "后天", "前天",
    "早上", "中午", "下午", "晚上", "上午", "傍晚", "凌晨",
    "现在", "当前", "这里", "那里", "这边",
    "请问", "麻烦", "我想",
)


def _extract_city(raw: str) -> str | None:
    """从 '某地天气' 类输入抽出城市, 剥掉问候/时间噪声; 抽不到时返回 None
    让 provider 回落默认城市, 而不是把 '您好今天' 当城市去 wttr 查 500。
    """
    m = _CITY_HINT_RE.search(raw)
    if not m:
        return None
    cand = m.group(1)
    changed = True
    while changed and cand:
        changed = False
        for tok in _NON_CITY_TOKENS:
            if cand.startswith(tok):
                cand = cand[len(tok):]
                changed = True
                break
            if cand.endswith(tok):
                cand = cand[:-len(tok)]
                changed = True
                break
    cand = cand.strip()
    if 2 <= len(cand) <= 6:
        return cand
    return None



# 导览控制 — 强动作意图
_TOUR_CTRL_PATTERNS = [
    r"^下一个$|下一个", r"换一个", r"换个", r"再看看", r"别的看看",
    r"跳过", r"不看了", r"下一个展[区项]", r"继续走",
    r"结束(参观)?", r"参观完了?", r"看完了", r"今天就到", r"差不多了?",
    r"回去吧", r"再见", r"不用了", r"可以了$",
    r"暂停", r"等一下", r"等等", r"先别",
    r"^继续$|好了继续|可以了继续",
    r"我想参观", r"开始参观", r"参观一下", r"^参观展厅", r"开始讲解",
    r"开始介绍", r"开始展厅讲解", r"展厅讲解", r"导览一下", r"开始导览",
    r"带我(参观|看看|看一下|逛逛|逛一下|转转|转一下|去|看)",
    r"有哪些展项", r"展项列表", r"都有什么", r"展品有哪些", r"全部展项",
    r"看看有什么", r"有什么(可以看|展品|内容)", r"可以看什么",
    # "有哪些内容/资源/东西/展品" — list_exhibit_resources / list_exhibits 入口
    r"有哪些(内容|资源|东西|展品)", r"都有哪些(内容|资源|展项|展品)",
    # 中文数字 + 分钟 — "我有十分钟介绍展厅" / "你帮我规划五分钟" 等
    r"[一二三四五六七八九十两半百0-9]+\s*分钟",
    r"半.{0,2}(?:小时|钟头)", r"一?个?(?:小时|钟头)",
    # "我有/帮我规划...时间" — 给定时长就视为想开始参观
    r"(我|咱|咱们)\s*(还|只|就)?\s*有.*分钟",
    r"(帮|给|为)?\s*(我|咱|咱们)?\s*(规划|安排|打算|计划).*分钟",
    # "介绍/讲解/带我看 这个/整个/咱们 展厅" — 整体导览
    r"(介绍|讲解|讲讲|带.{0,4}看|带.{0,4}逛).*(?:这个|整个|当前|咱们|我们|你们)?\s*展厅",
    # 设备控制口语必须在进入外部问答前识别。
    r"开(?:一下|下|个)?灯", r"关(?:一下|下)?灯",
    r"(?:把|将)灯(?:打开|开|关掉|关上|关了|关)",
    r"灯(?:打开|开一下|关掉|关上|关了)", r"亮灯", r"灭灯",
    r"全部(打开|关闭)", r"全开", r"全关", r"都打开", r"都关",
    r"开广告机", r"关广告机", r"广告机.{0,3}(开|关)",
    r"去充电", r"回去充电", r"充电去",
    r"切换.{0,4}专场", r"切到.{0,4}专场", r"换成.{0,4}专场",
    # 去某地 (导览类)
    r"去.+?(岛台|广告机|空间|展区|展位|区|位|那里|那边)",
    r"带我(去|到|看)",
    r"^前往.+", r"^到.+(岛台|广告机|空间|展区)",
]
_TOUR_CTRL_RE = re.compile("|".join(_TOUR_CTRL_PATTERNS))

# 周边 POI — "附近/周边/旁边/最近" + 类目词
_RE_POI_NEARBY = re.compile(
    r"(?:附近|周边|周围|旁边|最近|这附近|这周边|这边).{0,12}"
    r"(?:美食|餐厅|饭店|饭|吃饭|吃的|喝的|外卖|咖啡|奶茶|酒吧|"
    r"景点|玩|公园|博物馆|"
    r"酒店|宾馆|住宿|住的|"
    r"便利店|超市|购物|商场|商店|"
    r"地铁|公交|车站|停车|"
    r"银行|atm|医院|药店|加油站|充电桩)"
    r"|"
    # 反向语序: "美食...附近", "哪里能吃饭"
    r"(?:美食|餐厅|饭店|景点|酒店|便利店|超市|地铁|公交|银行|加油站).{0,4}"
    r"(?:在哪|在哪儿|哪里|哪儿|附近|周边)"
    r"|"
    # "哪里能 + 吃/喝/玩/住"
    r"哪(?:里|儿)?(?:能|可以|有).{0,3}(?:吃|喝|玩|住)"
    r"|"
    # 简短问法: "这有啥吃的", "这边有什么好吃的"
    r"(?:这|这边|这里).{0,4}有(?:啥|什么).{0,6}(?:吃的|喝的|玩的|好吃|好玩)"
)

# 整厅导览意图 — 必须在 demonstrative 判定之前抢先识别, 防止
# "介绍一下这个展厅" 被 _RE_DEMONSTRATIVE 拦到 hall_context 出闲聊回复。
_RE_HALL_TOUR = re.compile(
    r"(介绍|讲解|讲讲|参观|带.{0,6}看|带.{0,6}逛).{0,4}(?:这个|整个|当前|咱们|我们|你们)?\s*展厅|"
    r"展厅.{0,2}(?:有哪些|有什么|都有什么|都有哪些).{0,4}(?:展项|内容|资源|展品)|"
    r"(?:我|咱|咱们)\s*(?:还|只|就)?\s*有\s*[一二三四五六七八九十两半百0-9]+\s*分钟|"
    r"(?:帮|给|为)?\s*(?:我|咱|咱们)?\s*(?:规划|安排|打算|计划)\s*(?:一?下)?\s*[一二三四五六七八九十两半百0-9]+\s*分钟"
)

# 展厅上下文指示词 — 触发 hall_context
_RE_DEMONSTRATIVE = re.compile(
    r"(这是(什么|哪|啥)|这个(展项|是什么|讲(的|什么)|说什么|介绍)|"
    r"这里(是什么|有什么|讲什么|讲的什么)|这边(是什么|讲什么)|"
    r"当前(展项|屏幕|是什么|讲(的|什么)|位置)|"
    r"现在(讲(的|什么)|是什么|看的)|"
    r"屏幕上(有|是)什么|屏幕显示(的)?是什么|"
    r"我们在哪|你在(讲|介绍)什么|"
    r"介绍(一下)?(这个|这里|这边|当前)|讲讲(这个|这里|这边))"
)

# 社交寒暄
_RE_SOCIAL = re.compile(
    r"^(你好|您好|哈喽|hello|hi|嗨|早上好|下午好|晚上好|"
    r"谢谢|多谢|感谢|拜拜|再见)[\s\W]*$|"
    r"你叫什么|你是谁|你能(做|干)什么|你会什么|你有什么(功能|本事)|"
    r"^帮助$|^功能$"
)

# 展厅知识关键词 (无明显当前指示, 但提到展厅业务概念)
_HALL_KNOWLEDGE_TOKENS = (
    "中国移动", "云猴", "数字人", "机器人和数字人", "AI赋能",
    "智慧城市", "智慧园区", "智慧社区", "智慧教育",
    "大岛台", "小岛台", "CAVE", "三联屏", "广告机", "工位",
    "城市共享客厅", "思德", "解决方案", "应用场景",
    "区别", "对比", "怎么用", "做什么的", "什么意思",
    "讲解词", "知识库",
)

# 已知展项名 (用于 hall_knowledge 命中 — 真实名称由 retriever 再次校验)
_KNOWN_EXHIBITS = (
    "大岛台", "大岛台正面", "大岛台背面", "大岛台标题屏",
    "小岛台", "小岛台正面", "小岛台背面", "小岛台标题屏",
    "CAVE", "cave", "CAVE空间", "CAVE空间投影",
    "入口三联屏", "三联屏",
    "交付旁广告机", "设计中心中广告机",
    "云猴", "思德AI", "中国移动",
)

# 泛知识/常识
_RE_GENERAL_KNOWLEDGE = re.compile(
    r"(美食|小吃|特色菜|好玩(的|吗)|景点|景区|哪里玩|逛逛|"
    r"哪家好吃|什么(地方|公司|意思)|是干什么的|是做什么的|"
    r"来历|历史|起源|发明|怎么(做|来|形成)|"
    r"什么时候(成立|建立|发现|出现)|"
    r"位于哪里|在哪里|属于哪)"
)


# ============================================================
# 主入口
# ============================================================
def classify_query(text: str, world_state=None) -> dict:
    """
    返回 {"qtype": str, "slots": {...}, "reason": str}
    内部异常不冒泡, 降级为 TOUR_CONTROL (交给意图链处理)。
    slots 可能字段:
      - city           (realtime weather)
      - exhibit_name   (hall_knowledge / hall_context, 文本中显式提到的展项名)
      - is_compare     (hall_knowledge 对比类问题)
    """
    if not text:
        return {"qtype": SOCIAL_FALLBACK, "slots": {}, "reason": "empty"}

    try:
        return _classify_query_impl(text, world_state)
    except Exception as e:
        import logging
        logging.getLogger("hub.engine.query_router").error(
            "[QueryRouter] classify_query 异常, 降级 TOUR_CONTROL: %s", e)
        return {"qtype": TOUR_CONTROL, "slots": {}, "reason": "error_fallback"}


def _classify_query_impl(text: str, world_state=None) -> dict:
    """classify_query 实际逻辑, 异常由外层捕获。"""
    raw = text.strip()
    t = raw.lower()

    slots: dict = {}

    # 提取展项名 (供后续路由参考)
    mentioned_exhibit = None
    for name in _KNOWN_EXHIBITS:
        if name.lower() in t:
            mentioned_exhibit = name
            break
    if mentioned_exhibit:
        slots["exhibit_name"] = mentioned_exhibit

    # 输入主体就是展项名 (前后只夹标点/语气词) → 直接当成 navigate_to 走 tour_control
    # 避免 "大岛台" 这种纯展项名落到 social_fallback 兜底闲聊。
    if mentioned_exhibit:
        residue = re.sub(r"[\s,，。.!！?？吧呢啊哦的了吗么嘛是]", "", t)
        if residue == mentioned_exhibit.lower() or len(residue) <= len(mentioned_exhibit):
            slots["target"] = mentioned_exhibit
            return {"qtype": TOUR_CONTROL, "slots": slots, "reason": "bare_exhibit_name"}

    # 0. 整厅导览意图 — "介绍/讲解 这个/整个/咱们 展厅", "我有X分钟介绍展厅", "规划X分钟" 等
    # 必须在 demonstrative 之前判定, 否则 "介绍一下这个" 会被当成展项指示词
    # 路由到 hall_context, 出 LLM 解释而不是真的开 tour。
    if _RE_HALL_TOUR.search(raw):
        return {"qtype": TOUR_CONTROL, "slots": slots, "reason": "hall_tour"}

    # 0.5 周边 POI 查询 — 含 "附近/周边/旁边/最近" + 类目词 (美食/景点/酒店/便利店/...)
    # 走 poi_retriever 调高德 API, 拿到结构化结果后口语化回放, 不走 OpenClaw 闲聊
    if _RE_POI_NEARBY.search(raw):
        return {"qtype": POI_NEARBY, "slots": slots, "reason": "poi nearby"}

    # 1. 实时信息 — 强模式优先
    if _RE_WEATHER.search(raw):
        city = _extract_city(raw)
        if city:
            slots["city"] = city
        slots["sub"] = "weather"
        return {"qtype": REALTIME_INFO, "slots": slots, "reason": "weather pattern"}
    if _RE_TIME.search(raw):
        slots["sub"] = "time"
        return {"qtype": REALTIME_INFO, "slots": slots, "reason": "time pattern"}
    if _RE_DATE.search(raw):
        slots["sub"] = "date"
        return {"qtype": REALTIME_INFO, "slots": slots, "reason": "date pattern"}

    # 2. 展厅上下文 — 指示代词/当前/屏幕
    if _RE_DEMONSTRATIVE.search(raw):
        return {"qtype": HALL_CONTEXT, "slots": slots, "reason": "demonstrative"}

    # 当前 browse/playing 模式下的纯数字/单字 选择 → 走 tour_control
    if world_state is not None:
        try:
            mode = getattr(world_state.browse, "mode", "")
            if mode in ("list_exhibits", "list_resources", "playing"):
                if re.match(r"^[一二三四五六七八九十\d]+$", raw):
                    return {"qtype": TOUR_CONTROL, "slots": slots, "reason": "browse-index"}
        except Exception:
            pass

    # 2.8 拍照/合影/打卡 — 明确功能指令，必须走 tour_control，不能落入闲聊/QA。
    if re.search(r"(拍照|拍张照|拍一张|合照|合影|打卡|留念|照相)", raw):
        return {"qtype": TOUR_CONTROL, "slots": slots, "reason": "photo command"}

    # 3. 导览控制 — 控制类强模式
    if _TOUR_CTRL_RE.search(raw):
        # "去X" / "带我去X" 类:抽 target
        m = re.search(r"(?:去|到|带我去|带我到|前往|带我看)(.+?)(?:[展区台位吧呢啊哦。.，,!！?？]|$)", raw)
        if m:
            tgt = m.group(1).strip()
            if tgt and len(tgt) <= 12:
                slots["target"] = tgt
        return {"qtype": TOUR_CONTROL, "slots": slots, "reason": "tour pattern"}

    # 4. 展厅知识 — 提到展项 / 展厅业务概念 + 含问号或问词
    has_question = ("?" in raw or "？" in raw or
                    re.search(r"(什么|怎么|如何|为什么|为啥|哪些|哪个|哪里|区别|对比|讲(的|什么))", raw))
    if has_question:
        if mentioned_exhibit:
            slots["is_compare"] = bool(re.search(r"(区别|对比|不同|差别)", raw))
            return {"qtype": HALL_KNOWLEDGE, "slots": slots, "reason": "exhibit+question"}
        for tok in _HALL_KNOWLEDGE_TOKENS:
            if tok.lower() in t:
                slots["topic"] = tok
                slots["is_compare"] = bool(re.search(r"(区别|对比|不同|差别)", raw))
                return {"qtype": HALL_KNOWLEDGE, "slots": slots, "reason": f"hall token={tok}"}

    # 5. 社交寒暄
    if _RE_SOCIAL.search(raw):
        return {"qtype": SOCIAL_FALLBACK, "slots": slots, "reason": "social pattern"}

    # 6. 泛知识 — 含问词或常识词
    if has_question or _RE_GENERAL_KNOWLEDGE.search(raw):
        return {"qtype": GENERAL_KNOWLEDGE, "slots": slots, "reason": "general question"}

    # 默认走社交兜底
    return {"qtype": SOCIAL_FALLBACK, "slots": slots, "reason": "default"}
