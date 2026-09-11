"""
IntentResolver — 统一意图识别

合并旧 intent.py 和 intent_engine.py 的能力：
1. 规则引擎：毫秒级确定性匹配（优先）
2. 触发词匹配：流程触发词数据库匹配
3. LLM兜底：MiniMax-M2.5 语义理解

新增：根据 WorldState 上下文调整意图（状态相关意图）
"""

import json
import logging
import re
import aiohttp

from app.config import get_config
from app.database import get_db

logger = logging.getLogger("hub.engine.intent_resolver")

# ============================================================
# 谐音/模糊匹配（从旧intent.py保留，已验证可用）
# ============================================================
_PINYIN_MAP = {
    # 小豹小豹 唤醒词 + 常见误识别
    '小': 'xiao', '豹': 'bao',
    '晓': 'xiao', '霄': 'xiao', '肖': 'xiao',
    '抱': 'bao', '包': 'bao', '宝': 'bao', '报': 'bao', '暴': 'bao', '爆': 'bao', '薄': 'bao',
    # 历史遗留: 老唤醒词云猴云猴 拼音表
    '云': 'yun', '猴': 'hou', '允': 'yun', '候': 'hou',
    '运': 'yun', '韵': 'yun', '晕': 'yun', '纭': 'yun',
    '喉': 'hou', '厚': 'hou', '后': 'hou', '吼': 'hou',
    '开': 'kai', '始': 'shi', '参': 'can', '观': 'guan',
    '卡': 'ka', '士': 'shi', '残': 'can', '官': 'guan', '管': 'guan',
    '巡': 'xun', '航': 'hang', '迅': 'xun', '行': 'hang',
    # 展厅常用字
    '展': 'zhan', '厅': 'ting', '讲': 'jiang', '解': 'jie',
    '日': 'ri', '常': 'chang', '贵': 'gui', '宾': 'bin',
    '接': 'jie', '待': 'dai', '流': 'liu', '程': 'cheng',
    '导': 'dao', '览': 'lan', '专': 'zhuan', '场': 'chang',
    '数': 'shu', '字': 'zi', '人': 'ren', '智': 'zhi',
    '能': 'neng', '互': 'hu', '动': 'dong',
    '灯': 'deng', '屏': 'ping', '幕': 'mu', '电': 'dian',
    '播': 'bo', '放': 'fang', '视': 'shi', '频': 'pin',
    '资': 'zi', '源': 'yuan', '设': 'she', '备': 'bei',
    '组': 'zu', '开': 'kai', '关': 'guan', '闭': 'bi',
    '上': 'shang', '下': 'xia', '前': 'qian', '后': 'hou',
    '进': 'jin', '出': 'chu', '来': 'lai', '去': 'qu',
    '到': 'dao', '过': 'guo', '看': 'kan', '听': 'ting',
    '说': 'shuo', '问': 'wen', '答': 'da', '回': 'hui',
    '机': 'ji', '器': 'qi', '人': 'ren', '器': 'qi',
}

try:
    from pypinyin import lazy_pinyin as _lazy_pinyin
    def _to_pinyin(text: str) -> str:
        # 全库转换, 英文/数字保持原样
        return ''.join(_lazy_pinyin(text))
except ImportError:
    # 兜底: 没装 pypinyin 时用手工小表, 覆盖率低但不崩
    def _to_pinyin(text: str) -> str:
        return ''.join(_PINYIN_MAP.get(c, c) for c in text)

def _fuzzy_match(text: str, keyword: str) -> bool:
    """模糊匹配：用于规则模式中的关键词匹配。
    keyword 在 text 中出现即可，支持拼音容错。
    """
    if keyword in text:
        return True
    if _to_pinyin(keyword) in _to_pinyin(text):
        return True
    return False

# 机器人语音唤醒词: 已固定, 不走后台配置。
# 展厅硬件侧的唤醒模型烧的就是这个词, 这里只做去前缀 + 拼音容错。
WAKE_WORD = "小豹小豹"


def _strip_wake_word(text: str) -> str:
    """去除唤醒词前缀（精确 + 拼音容错）"""
    wake_word = WAKE_WORD
    if text.startswith(wake_word):
        return text[len(wake_word):].strip(' ,.!?，。！？')
    wk_len = len(wake_word)
    if len(text) >= wk_len and _to_pinyin(text[:wk_len]) == _to_pinyin(wake_word):
        return text[wk_len:].strip(' ,.!?，。！？')
    return text.strip()


# ============================================================
# 意图定义（覆盖展厅全场景）
# ============================================================
# 交互意图（SmartEngine状态机驱动）
INTERACTIVE_INTENTS = [
    "start_tour",           # 开始参观
    "end_tour",             # 结束参观
    "play_resource",        # 播放指定资源
    "take_photo",           # 拍照/合影/打卡
    "play_next",            # 下一个
    "skip_exhibit",         # 跳过当前展项
    "ask_explain",          # 讲解/介绍
    "navigate_to",          # 去某处
    "wait_please",          # 等一下
    "resume_please",        # 继续
    "chitchat",             # 闲聊
    "set_time",             # 设置时间
    "ask_current",          # 问当前展项
    "query_help",           # 帮助/功能查询
    "query_status",         # 状态查询
    "list_exhibits",        # 浏览展项列表
    "interactive_discuss",  # 开始讨论
    "weather_query",        # 天气查询
]

# 设备控制意图（直接执行）
CONTROL_INTENTS = [
    "lights_on",
    "lights_off",
    "all_on",
    "all_off",
    "ad_screen_on",
    "ad_screen_off",
    "switch_special",
    "go_charging",
]


# ============================================================
# 规则引擎
# ============================================================
INTENT_RULES = [
    # --- 交互意图 ---
    # ⚠️ self_intro 必须排在 ask_explain/start_tour 之前: 它们的 "介绍一下" 会
    # 抢匹 "给大家介绍一下自己" / "自我介绍" 把这些误送进 tour 规划链路.
    {"intent": "self_intro", "patterns": [
        r"自我介绍",
        r"介绍(?:一)?下?自己",
        r"给(?:大家|各位|现场|你们)\s*(?:做个|做下|来个|来下)?\s*自我介绍",
        r"给(?:大家|各位|现场|你们)\s*介绍(?:一)?下?\s*(?:你)?自己",
        r"(?:给|跟|和|向)(?:大家|各位|现场|全场|观众|你们)\s*打(?:个|声|下|一?下)?招呼",
        r"(?:给|跟|和|向)(?:大家|各位|现场|全场|观众|你们)\s*问(?:个)?好",
        r"(?:跟|和|对)(?:大家|各位|现场|全场|观众|你们)\s*说\s*[句个下]?\s*(?:你好|hello|嗨)",
        r"打个招呼吧$|问个好吧$",  # 用户单说一句, 主语隐含"对大家"
    ], "confidence": 0.93},

    {"intent": "end_tour", "patterns": [
        r"结束", r"结束参观", r"参观完", r"就到这", r"不想看", r"不用了",
        r"看完了", r"今天就到", r"差不多", r"可以了", r"再见",
        # 注意: "回去吧" 不再归 end_tour, 改归 navigate_to(接待点) — 用户希望
        # 说"回去/回去吧"机器人就主动导航回接待点, 不是单纯结束。
    ], "confidence": 0.92},


    {"intent": "take_photo", "patterns": [
        # 拍照是明确功能指令，必须整句优先匹配，避免"我要拍照"只命中子串"拍照"导致置信度被 coverage 拉低。
        r"^(?:我(?:要|想)?|我们|咱们|大家)?\s*(?:要|想|可以)?\s*(?:拍照|照相|合照|合影|打卡|留念)\s*(?:吧|一下|一张|一张照片)?$",
        r"^我要拍照$", r"^我想拍照$", r"^想拍照$", r"^可以拍照$",
        r"拍(?:个|张|一张)?照", r"合(?:个|张)?影", r"合照",
        r"打卡", r"留念", r"拍一下", r"拍吧", r"照相",
        r"拍一张照片",
        # 重拍/重新拍 — 拍完照片后用户想重新拍一张
        r"^重拍$", r"^重新拍$", r"^重新拍照$",
        r"^再拍(?:一|1)次", r"^再拍(?:一|1)张",
        r"^再来(?:一|1)张", r"^重新来",
    ], "confidence": 0.94},

    {"intent": "play_resource", "patterns": [
        r"第[一二三四五六七八九十百\d]+个",
        r"播放?第[一二三四五六七八九十百\d]+",
        r"选第?[一二三四五六七八九十百\d]+个?",
        r"播\d", r"播放.*第",
        r"^[一二三四五六七八九十\d]{1,2}$",
    ], "confidence": 0.93, "extract": "resource_index"},

    # 音乐播放 — 命中后从 music_tracks 启用列表里随机挑一首推给 APK.
    # 仅识别"放歌/听音乐"这种通用诉求, 不抓"放第几个/播放XXX"(那些归 play_resource).
    # confidence 比 play_resource 略低, 让带"第N个"的优先走资源播放分支.
    {"intent": "play_music", "patterns": [
        r"放(?:一)?首歌", r"来(?:一)?首歌", r"放(?:一)?个歌", r"来(?:一)?个歌",
        r"听(?:一)?首歌", r"听(?:点)?音乐", r"想听歌", r"想听音乐",
        r"放(?:点|个|首)?音乐", r"来(?:点|个|首)?音乐", r"放歌",
        r"播放音乐", r"播首歌", r"换首歌",
        r"唱(?:一)?首歌", r"唱个歌", r"唱歌", r"唱一曲",
        r"随便放(?:一)?首", r"随便来(?:一)?首",
    ], "confidence": 0.90},

    # 停止音乐 — 无论是唱歌/放歌/背景音乐, 用户说停就停.
    {"intent": "stop_music", "patterns": [
        r"^停$", r"^停下$", r"^停止$",
        r"停止播放", r"停止音乐", r"关掉音乐", r"关闭音乐",
        r"别唱了", r"别放了", r"不听了", r"听完了",
        r"关了(?:吧|它)?", r"关掉(?:吧|它)?",
        r"不要再唱了", r"不要再放了",
    ], "confidence": 0.92},

    # 换歌 — 换一首歌, 与 play_next(跳过展项) 语义不同.
    # 换歌只影响音乐播放, 不改展区进度.
    {"intent": "next_song", "patterns": [
        r"^换(?:一)?首$", r"换(?:一)?个歌", r"换个歌",
        r"换一首歌", r"切(?:一)?首", r"切换歌曲",
        r"下一首", r"再换", r"换别?的?",
    ], "confidence": 0.92},

    # 音量控制 — 调大/调小
    {"intent": "volume_up", "patterns": [
        r"大声点", r"大点声", r"音量(?:调)?大(?:一)?点",
        r"开大(?:一)?点", r"放大(?:一)?点", r"响(?:一)?点",
    ], "confidence": 0.92},
    {"intent": "volume_down", "patterns": [
        r"小声点", r"小点声", r"音量(?:调)?小(?:一)?点",
        r"开小(?:一)?点", r"关小(?:一)?点", r"轻(?:一)?点",
    ], "confidence": 0.92},

    {"intent": "play_next", "patterns": [
        r"下一个", r"换个", r"换一个", r"再看看", r"别的", r"还有什么",
    ], "confidence": 0.92},

    {"intent": "skip_exhibit", "patterns": [
        r"跳过", r"不看了", r"下一个展区", r"走吧", r"继续走",
    ], "confidence": 0.92},

    {"intent": "ask_explain", "patterns": [
        r"什么", r"介绍一下", r"讲解", r"讲讲", r"什么意思",
        r"干什么的", r"做什么的", r"干嘛的", r"说说",
    ], "confidence": 0.85},

    # 返回接待点/回接待点 — 高频专用语音指令, 必须能随时打断当前活动直接导航。
    # 规则前置 + confidence 高于通用 navigate_to (0.94 vs 0.92), 避免被 "回" / "去" 等
    # 短前缀 navigate_to 抢先抓到错误 target。capture group 抓"接待点/接待处/接待/前台"等,
    # 由 find_position_by_name 模糊匹配真实点位 (子串 + 拼音兜底)。
    {"intent": "navigate_to", "patterns": [
        r"返\s*回\s*(?:到\s*)?(接待点|接待处|接待区|接待台|接待|前台)",
        r"回\s*(?:去|到|回)?\s*(接待点|接待处|接待区|接待台|接待|前台)",
        r"(?:那|好|好的|那行)?\s*(?:就|咱|咱们|我们)?\s*回\s*(接待点|接待处|接待区|接待台|接待|前台)",
        r"去\s*(接待点|接待处|接待区|接待台|接待|前台)",
        r"导航\s*(?:到|回|去)?\s*(接待点|接待处|接待区|接待台|接待|前台)",
    ], "confidence": 0.94, "extract": "target"},

    # 裸 "回去/回去吧/归位/回到起点" 等不带接待字样的短句 — 也视为"返回接待点".
    # 没有 capture group, 用 fixed_target 强制 target="接待点", 由 find_position_by_name 模糊匹配真实点位。
    # confidence 0.93 高于 end_tour(0.92), 防止 "回去吧" 被错抢成结束参观.
    {"intent": "navigate_to", "patterns": [
        r"^(?:那\s*)?(?:就\s*)?(?:咱们|我们)?\s*回\s*去\s*吧?$",
        r"^归位$", r"^归个位$",
        r"^回到?起点$", r"^回原位$", r"^回原点$",
        r"^返\s*回$", r"^返回吧$",
    ], "confidence": 0.93, "fixed_target": "接待点"},

    {"intent": "navigate_to", "patterns": [
        r"去(.+?)[展区台位]", r"看(.+?)[展区台位]", r"到(.+?)[展区台位]",
        r"带我去(.+)", r"带我参观(.+)", r"带我(?:去|到|看)(.+)", r"我想看(.+)",
        r"讲解(.+)", r"介绍(.+)", r"讲讲(.+)",
    ], "confidence": 0.92, "extract": "target"},

    {"intent": "wait_please", "patterns": [
        r"等一下", r"稍等", r"等等", r"先别", r"暂停",
    ], "confidence": 0.93},

    {"intent": "resume_please", "patterns": [
        r"继续", r"好了继续", r"可以了继续", r"走吧",
    ], "confidence": 0.88},

    {"intent": "start_tour", "patterns": [
        # ⚠️ 顺序敏感: _rule_match 同 rule 内首个命中 pattern 即 break, 后面不再比较.
        # 高覆盖整句模式必须排前, 避免被 "讲解一下" / "我想参观" 这类短模式低 coverage 抢先 → 拉低 real_conf 走不到 TOUR_CONTROL.
        # ── 时长 + 讲解/参观/介绍 整句模式 (.* 锚整句, coverage→1.0, real_conf=0.88) ──
        r".*[一二三四五六七八九十两半百0-9]+\s*分钟.*(?:讲解|参观|介绍|看看|逛|游览|导览).*$",
        r".*(?:讲解|参观|介绍|看看|逛|游览|导览).*[一二三四五六七八九十两半百0-9]+\s*分钟.*$",
        r".*半\s*(?:个\s*)?(?:小时|钟头).*(?:讲解|参观|介绍|看看|逛|游览|导览).*$",
        r".*(?:讲解|参观|介绍|看看|逛|游览|导览).*半\s*(?:个\s*)?(?:小时|钟头).*$",
        # "我有/我只有/我还有 X分钟" — 即使没明说讲解, 给定时长就视为想开始
        r".*(?:我|咱|咱们)\s*(?:还|只|就)?\s*有\s*[一二三四五六七八九十两半百0-9]+\s*分钟.*$",
        # "帮我/给我/为我 规划/安排/打算 X分钟" — 时长规划意图
        r".*(?:帮|给|为)?\s*(?:我|咱|咱们)?\s*(?:规划|安排|打算|计划)\s*(?:一?下)?\s*[一二三四五六七八九十两半百0-9]+\s*分钟.*$",
        # 介绍/讲解 整个/这个/当前/咱们这个 展厅 → 整体导览 (避开 navigate_to 误抽 target)
        r".*(?:介绍|讲解|讲讲|带.*看|带.*逛)\s*(?:一?下)?\s*(?:这个|整个|当前|咱们|我们|你们)?\s*展厅.*$",
        # ── 短模式兜底 ──
        r"开始参观", r"我想参观", r"参观一下", r"带我参观$",
        r"开始讲解", r"开始展厅讲解", r"展厅讲解", r"讲解一下",
        r"开始介绍", r"介绍一下", r"带我看看", r"带我看一下",
        r"带我逛逛", r"带我逛一下", r"带我转转",
        r"导览一下", r"开始导览", r"参观展厅",
    ], "confidence": 0.88},

    {"intent": "interactive_discuss", "patterns": [
        r"开始讨论(.+)", r"讨论一下(.+)", r"聊聊(.+)",
        r"谈谈(.+)", r"讨论(.+)", r"辩论(.+)",
        r"你们.*聊(.+)", r"你们.*讨论(.+)", r"来.*讨论(.+)",
    ], "confidence": 0.93, "extract": "topic"},

    {"intent": "set_time", "patterns": [
        r"([0-9一二两三四五六七八九十百]+)\s*分钟",
        r"半\s*(?:个\s*)?(?:小时|钟头)",
        r"([0-9一二两三四五六七八九十]+)\s*个?\s*(?:小时|钟头)",
    ], "confidence": 0.85, "extract": "minutes"},

    {"intent": "ask_current", "patterns": [
        r"这是哪里", r"当前展项", r"屏幕上有什么", r"屏幕上是什么",
        r"当前屏幕", r"这是什么", r"这个是什么", r"这个展项",
        r"这里是什么", r"这讲的是什么", r"这个讲的是什么",
        r"当前(是|在)什么", r"现在讲的什么", r"现在讲的是什么",
        # 当前专场查询 (覆盖 "当前/现在" + "是/在" + "哪个/什么" 组合)
        r"(当前|现在)(是|在)?(哪个|什么).*专场",
        r"(当前|现在)(是|在)?.*(哪|什么)专场",
        r"这(是|在)哪个专场", r"这是什么专场",
        r"(当前|现在)专场(是什么)?",
    ], "confidence": 0.90},

    {"intent": "weather_query", "patterns": [
        r"天气", r"下雨", r"温度", r"气温", r"天气预报",
        r"多少度", r"冷不冷", r"热不热", r"穿什么衣",
    ], "confidence": 0.88},

    {"intent": "chitchat", "patterns": [
        r"你好", r"哈喽", r"hello", r"hi", r"早上好", r"下午好",
        r"谢谢", r"感谢", r"你叫什么", r"你是谁",
    ], "confidence": 0.80},

    {"intent": "query_help", "patterns": [
        r"帮助", r"你能做什么", r"你会什么", r"功能", r"怎么用",
        r"能帮我什么", r"有什么功能", r"操作指南",
    ], "confidence": 0.90},

    {"intent": "query_status", "patterns": [
        r"状态", r"电量", r"你在哪", r"当前位置", r"电池",
        r"你还好吗", r"系统状态", r"剩余电量",
    ], "confidence": 0.90},

    {"intent": "list_exhibits", "patterns": [
        r"有哪些展项", r"展项列表", r"有什么可以看", r"都有什么",
        r"可以看什么", r"展品有哪些", r"全部展项",
        r"看看有什么", r"有什么展品",
        # 新增: "(整|这个|当前)展厅 有哪些 内容/展品"
        # list_exhibit_resources 会先抢, 但展厅相关 target 落到 _NAV_TARGET_STOPWORDS
        # 被判 invalid_target 后, 这里兜住列出全部展项.
        r"展厅\s*有哪些(内容|展品|展项)",
        r"展厅\s*都有哪些(内容|展品|展项)?",
        r"还有哪些(内容|展项|展品)",
        # 无主语形式 — list_exhibit_resources 那条要求前缀 .+?, 不带主语会落空,
        # 兜在这里等价于"列展项让用户选". 顺序很关键: list_exhibit_resources 0.93
        # 优先级更高, 带主语的"X 有哪些资源"还是它先抢, 这里只兜住没主语的。
        r"^有哪些(资源|东西|内容)$",
        r"^有什么(资源|东西|内容|展项)?$",
        r"^什么(资源|内容|展项)$",
        r"^资源有哪些$", r"^内容有哪些$",
        # 复合上下文型: 切到 X 专场后说 "X 有哪些资源"
        r"专场\s*有哪些(资源|内容|展项)",
        r"当前.{0,4}有哪些(资源|内容|展项)",
    ], "confidence": 0.92},

    # list_exhibit_resources: "XX 有什么" → 直接枚举该展项的资源列表, 不导航
    # 置信度 0.93 高于 list_exhibits 0.92, 使带展项名的查询优先命中
    {"intent": "list_exhibit_resources", "patterns": [
        r"(.+)里(面)?有什么",
        r"(.+?)有什么(内容|资源|东西)?$",
        r"(.+?)都有什么(内容|资源)?$",
        r"(.+?)都有哪些(内容|资源)?",
        # ASR 常出 "X 有哪些内容/资源" 而不是 "都有哪些"; 必须接受不带 "都"
        r"(.+?)有哪些(内容|资源|东西)$",
        r"看看(.+?)有什么",
    ], "confidence": 0.93, "extract": "target"},

    # --- 设备控制意图 ---
    {"intent": "lights_on", "patterns": [
        r"开灯", r"打开灯", r"灯打开", r"亮灯",
        r"把灯打开", r"把灯开", r"灯都开", r"灯[全传]开", r"所有灯.*开",
        r"开一下灯", r"开下灯", r"开个灯",
        r"灯开开", r"灯开一下", r"灯开下", r"灯亮",
    ], "confidence": 0.95},

    {"intent": "lights_off", "patterns": [
        r"关灯", r"关掉灯", r"灯关掉", r"灭灯",
        r"把灯关", r"灯关上", r"关上灯", r"灯关了", r"灯都关", r"灯[全传]关", r"所有灯.*关",
        r"关一下灯", r"关下灯",
    ], "confidence": 0.95},

    {"intent": "all_on", "patterns": [
        r"全部打开", r"全开", r"都打开", r"全部开启", r"全部启动", r"都开了", r"全开启",
        r"每日[全传]开", r"每日都开",
    ], "confidence": 0.95},

    {"intent": "all_off", "patterns": [
        r"全部关闭", r"全关", r"都关了", r"全部关掉", r"全部关停", r"都关掉", r"全关闭",
        r"每日[全传]关", r"每日都关",
    ], "confidence": 0.95},

    {"intent": "ad_screen_on", "patterns": [
        r"开广告机", r"打开广告机",
    ], "confidence": 0.95},

    {"intent": "ad_screen_off", "patterns": [
        r"关广告机", r"关掉广告机",
    ], "confidence": 0.95},

    {"intent": "go_charging", "patterns": [
        r"去充电", r"回去充电", r"充电去",
    ], "confidence": 0.90},

    # switch_special: 统一单条规则, extract 成功则进切换, extract 空则进列表模式
    # - "切换到/切到/换成 X 专场" → entities.special_name=X → _switch_special_by_row
    # - "有哪些专场/切换专场" → entities={} → 列 specials_cache 进 list_specials 模式
    {"intent": "switch_special", "patterns": [
        # 带名称(前缀含 "切换/切到/换成/换到/换")
        r"切换(?:到|成)?.+", r"切到.+",
        r"换成.+专场", r"换到.+专场", r"换.+专场",
        # 无名称(直接列表)
        r"有哪些专场", r"专场列表", r"有什么专场", r"都有什么专场",
        r"看看专场", r"全部专场", r"切换专场$", r"换专场$", r"换个专场",
    ], "confidence": 0.92, "extract": "special_name"},
]


def _parse_resource_index(text: str) -> int:
    """从文字提取序号（1-based）.

    支持: "第X个" / "第X" / 纯数字 "3" / 纯中文数字 "三" / "十二" 等.
    旧版只识别 "第X个", 纯数字/纯中文数字都落回 1, 会把 "3"/"5" 都当成 "1".
    """
    cn_map = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
    t = text.strip()
    # "第X个" / "第X"
    for cn, n in cn_map.items():
        if f"第{cn}个" in t or f"第{cn}" == t:
            return n
    m = re.search(r"第\s*(\d+)\s*个?", t)
    if m:
        return int(m.group(1))
    # 纯数字
    if t.isdigit():
        return int(t)
    # 纯中文数字 (含 十一/十二 /二十/二十一 等)
    n = _cn_to_int(t)
    if n is not None:
        return n
    return 1


# 已知模糊/代词模式 — 规则匹配命中这些时标记 ambiguous
_AMBIGUOUS_PATTERNS = frozenset({
    "介绍一下", "讲解一下", "带我看看", "开一下", "切换一下",
    "带我去那里", "看看那个", "讲讲那个", "去那里", "去那边",
    "那个", "这个", "那里", "这里",
})

# 代词模式 — 命中 navigate_to/ask_explain 时 target 应置空
_PRONOUN_TARGETS = re.compile(r'^(那里|那边|这里|这边|那个|这个|那儿|这儿)$')

# navigate_to target 泛化停用词 — 命中则视为无具体目标, 让 start_tour / ask_explain 等更高语义规则赢
_NAV_TARGET_STOPWORDS = frozenset({
    "一下", "一下下", "一会", "一会儿", "一会会",
    "看", "看看", "看一看", "看一下",
    "逛", "逛逛", "逛一下", "转", "转转", "转一下",
    "介绍", "介绍一下", "讲解", "讲解一下", "讲讲",
    "参观", "参观一下", "游览",
    "展厅", "这里", "那里", "这边", "那边", "这儿", "那儿",
    "那个", "这个", "吧", "呢", "啊", "哦",
    "充电",
    # 整厅泛指 — 真实意图是 start_tour, 不应当 navigate_to target
    "这个展厅", "整个展厅", "当前展厅", "咱们展厅", "我们展厅", "你们展厅",
    "这个展厅吗", "整个展厅吗", "我们这个展厅",
    # 自我相关 — 真实意图是 self_intro, 不该当展项 target
    "自己", "你自己", "自我", "大家", "各位", "现场", "观众", "全场", "你们", "我们",
})

# start_tour 命中后回扫时长
_CN_NUM = {"零":0,"一":1,"二":2,"两":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
_DURATION_RE = re.compile(r"([0-9一二两三四五六七八九十百]+)\s*分钟")
_HALF_HOUR_RE = re.compile(r"半\s*(?:个\s*)?(?:小时|钟头)")
_HOUR_RE = re.compile(r"([0-9一二两三四五六七八九十]+)\s*个?\s*(?:小时|钟头)")

def _cn_to_int(s: str):
    if not s: return None
    if s.isdigit(): return int(s)
    if s == "十": return 10
    if len(s) == 1 and s in _CN_NUM: return _CN_NUM[s]
    if s.startswith("十") and len(s) == 2 and s[1] in _CN_NUM: return 10 + _CN_NUM[s[1]]
    if len(s) >= 2 and s[0] in _CN_NUM and s[1] == "十":
        base = _CN_NUM[s[0]] * 10
        if len(s) == 2: return base
        if len(s) == 3 and s[2] in _CN_NUM: return base + _CN_NUM[s[2]]
    return None

def _extract_minutes(text: str):
    if _HALF_HOUR_RE.search(text):
        return 30
    m = _HOUR_RE.search(text)
    if m:
        n = _cn_to_int(m.group(1))
        if n: return n * 60
    m = _DURATION_RE.search(text)
    if m:
        n = _cn_to_int(m.group(1))
        if n: return n
    return None


_CHINESE_LITERAL_RE = re.compile(r'^[\u4e00-\u9fff]{2,}$')

# navigate_to / ask_explain 的"前缀"短语列表, 用拼音匹配处理 ASR 谐音:
# "带我残官大脑台"(daiwocanguandanaotai) → prefix="带我参观"(daiwocanguan) 命中 →
# target="大脑台", 交 find_exhibit_by_name 的变体表/拼音匹配解析为大岛台.
_NAV_PINYIN_PREFIXES = [
    ("带我参观", "navigate_to"), ("带我看看", "navigate_to"), ("带我看", "navigate_to"),
    ("带我去", "navigate_to"), ("带我到", "navigate_to"),
    ("我想看", "navigate_to"), ("我要看", "navigate_to"),
    ("去看看", "navigate_to"), ("去看", "navigate_to"),
    ("讲解一下", "navigate_to"), ("介绍一下", "navigate_to"),
    ("讲讲", "navigate_to"),
]


def _extract_nav_target_by_pinyin(text: str) -> dict | None:
    """拼音前缀扫描: 逐字比对前缀拼音, 命中后返回 navigate_to + 剩余字符作 target.

    汉字数量可变, 不能按字节数截取, 所以必须按 char-by-char 累积拼音匹配.
    """
    t = text.strip()
    if len(t) < 3:
        return None
    try:
        from pypinyin import lazy_pinyin
        chars_py = lazy_pinyin(t)
    except Exception:
        return None
    if not chars_py or len(chars_py) < 3:
        return None
    for prefix, intent in _NAV_PINYIN_PREFIXES:
        prefix_py = "".join(lazy_pinyin(prefix))
        if len(prefix_py) < 5:
            continue  # 前缀太短易误伤
        acc = ""
        match_end = -1
        for i, p in enumerate(chars_py):
            acc += p
            if acc == prefix_py:
                match_end = i
                break
            if len(acc) > len(prefix_py):
                break
        if match_end >= 0 and match_end + 1 < len(t):
            target = t[match_end + 1:].strip(" 的了，,。.！!？?吧呢啊哦")
            if target:
                return {
                    "intent": intent,
                    "confidence": 0.82,
                    "entities": {"target": target},
                    "source": "rule_pinyin_target",
                }
    return None


def _rule_match_pinyin(text: str) -> dict | None:
    """规则匹配的拼音回退: 处理 ASR 谐音识别错误.

    触发条件: 常规 _rule_match 未命中 + pattern 是纯中文字面量(2 字以上).
    做法: 把 text 和 pattern 都转拼音, 若 pattern 拼音是 text 拼音的子串即命中.
    只覆盖无槽位抽取的规则(start_tour/end_tour/chitchat 等); 有槽位的(navigate_to target)
    交给 find_exhibit_by_name 的拼音模糊匹配处理.
    置信度比原规则略降, 保留差异信号.
    """
    t = text.lower().strip()
    t_py = _to_pinyin(t)
    if len(t_py) < 4:
        return None
    best = None
    best_confidence = 0.0
    for rule in INTENT_RULES:
        if rule.get("extract"):
            continue
        for pattern in rule["patterns"]:
            if not _CHINESE_LITERAL_RE.match(pattern):
                continue
            pat_py = _to_pinyin(pattern)
            if len(pat_py) < 6:
                # 太短(1-2汉字)误伤率高: "你好"nihao 会混"你号"/"里好" 之外也混很多词
                continue
            if pat_py in t_py and rule["confidence"] > best_confidence:
                best = {
                    "intent": rule["intent"],
                    "confidence": max(0.6, rule["confidence"] - 0.1),
                    "entities": {},
                    "source": "rule_pinyin",
                }
                best_confidence = rule["confidence"]
                break
    return best


_REGEX_META_RE = re.compile(r'[.*+?()\[\]{}|^$\\]')


def _is_pure_literal(pattern: str) -> bool:
    """判断 regex pattern 是否是纯中文/英文/数字字面量(无正则元字符)."""
    return not bool(_REGEX_META_RE.search(pattern or ""))


def _compute_match_quality(pattern: str, match, text: str, rule: dict, entities: dict) -> float:
    """基于正则匹配质量算 [0.5, 1.0] 的 quality 系数.

    维度:
    - coverage: match.span 占 text 的比例 (部分命中给低分, 整句命中满分)
    - exact_literal: 纯字面量 pattern 且 text 完全等同 → 强制 1.0
    - slot_extract: rule 定义了 extract 但 entities 为空 → 乘 0.85 惩罚
    """
    if not text:
        return 1.0
    span = max(0, match.end() - match.start())
    coverage = min(1.0, span / max(1, len(text)))
    quality = 0.5 + 0.5 * coverage  # regex 命中就至少 0.5, 完全覆盖 1.0
    if _is_pure_literal(pattern) and pattern == text:
        quality = 1.0
    if rule.get("extract") and not entities:
        quality *= 0.85
    return max(0.5, min(1.0, quality))


def _rule_match(text: str) -> dict | None:
    """规则匹配，返回最高优先级规则的结果，confidence 由匹配质量真实算出.

    - rule["confidence"] 视为该规则的"上限/优先级"; 实际返回 confidence = 上限 × 匹配质量 ∈ [0.5*上限, 上限]
    - 新增 ambiguous 标志：命中已知模糊表达时置 True
    - navigate_to 的 target 若落入泛化停用词, 视为未命中, 让低优先级的 start_tour/ask_explain 兜住
    """
    t = text.lower().strip()
    best = None
    best_rule_prio = 0.0

    for rule in INTENT_RULES:
        for pattern in rule["patterns"]:
            m = re.search(pattern, t)
            if not (m and rule["confidence"] > best_rule_prio):
                continue
            entities: dict = {}
            extract = rule.get("extract")
            invalid_target = False
            # 规则配置 fixed_target: 命中后强制 target=该值, 不依赖 capture group.
            # 用于 "回去/归位/回到起点" 这类不带显式目标但语义明确(回接待点)的短语。
            _fixed_target = rule.get("fixed_target")
            if _fixed_target:
                entities["target"] = _fixed_target
            if extract == "resource_index":
                entities["resource_index"] = _parse_resource_index(text)
            elif extract == "target" and m.groups():
                raw = m.group(1).strip(" 的了，,。.！!？?吧呢啊哦吗么嘛")
                # 剥离 "介绍/讲解/讲讲" 后常带的填充词前缀
                # ("介绍一下玄武湖" → 一下玄武湖 → 玄武湖)
                for _filler in ("一下下", "一下", "一会儿", "一会", "我", "你"):
                    if raw.startswith(_filler) and len(raw) > len(_filler):
                        raw = raw[len(_filler):].strip(" 的了，,。.！!？?吧呢啊哦吗么嘛")
                        break
                # 句尾再剥一次问句助词 (覆盖中间 strip 没清的 "...展厅吗")
                while raw and raw[-1] in "吗么嘛吧呢啊哦了":
                    raw = raw[:-1].strip(" 的了，,。.！!？?")
                if not raw or raw in _NAV_TARGET_STOPWORDS:
                    invalid_target = True  # 泛化目标, 不认这条规则命中
                else:
                    entities["target"] = raw
            elif extract == "minutes":
                mins = _extract_minutes(text)
                if mins is not None:
                    entities["minutes"] = int(mins)
            elif extract == "topic" and m.groups():
                topic = m.group(1).strip()
                if len(topic) > 1 and topic[-1] in "吧啊呢吗呀":
                    topic = topic[:-1].strip()
                if topic:
                    entities["topic"] = topic
            elif extract == "special_name":
                # 更具体的分隔符优先, 最后才用裸 "切换" / "换"
                # 不 strip "的" — "丝的"→"思德" 的谐音匹配要保留 de 音
                for sep in ["切换到", "切换成", "切到", "换成", "换到", "切换", "换"]:
                    if sep in t:
                        name = t.split(sep, 1)[1].replace("专场", "").strip(" 了，,。.！!？?吧呢啊哦")
                        if name:
                            entities["special_name"] = name
                        break
            if invalid_target:
                continue  # 尝试同 rule 的下一 pattern
            quality = _compute_match_quality(pattern, m, t, rule, entities)
            real_conf = round(rule["confidence"] * quality, 3)
            best = {
                "intent": rule["intent"],
                "confidence": real_conf,
                "entities": entities,
                "source": "rule",
                "match_quality": round(quality, 3),
                "rule_prio": rule["confidence"],
            }
            best_rule_prio = rule["confidence"]
            break

    if best:
        if t in _AMBIGUOUS_PATTERNS:
            best["ambiguous"] = True
        target = best.get("entities", {}).get("target", "")
        if target and _PRONOUN_TARGETS.match(target):
            best["entities"]["target"] = ""
            best["ambiguous"] = True
            logger.debug("[IntentResolver] 代词target清空: '%s'", target)
        # start_tour 命中后补抽时长(支持 "我想参观10分钟")
        if best["intent"] == "start_tour" and "minutes" not in best["entities"]:
            mins = _extract_minutes(t)
            if mins is not None:
                best["entities"]["minutes"] = mins

    return best


async def _match_trigger_word(text: str) -> dict | None:
    """匹配流程触发词（最优先级）

    匹配策略（按优先级，score越高越优）：
    1. 精确匹配(score=5)：完全一致
    2. 去标点精确匹配(score=4)：去除标点后一致
    3. 子串匹配(score=3)：互为子串
    4. 拼音包含匹配(score=2)：拼音互为子串
    5. 拼音公共子串匹配(score=1)：拼音公共子串足够长（>=60%的短串长度）
    """
    try:
        db = await get_db()
        try:
            scripts = await db.execute_fetchall(
                "SELECT id, name, trigger_word FROM tour_scripts WHERE enabled=1"
            )
            if not scripts:
                logger.debug("[IntentResolver] 触发词匹配: 无已启用的流程脚本")
                return None

            logger.debug("[IntentResolver] 触发词匹配: text='%s', 候选%d条: %s",
                        text, len(scripts),
                        [(s["id"], s["trigger_word"]) for s in scripts])

            # 去除标点/空格用于容错匹配
            import string
            strip_chars = string.punctuation + '，。！？、；：""''《》（）【】…—·　 '
            text_clean = text.strip().strip(strip_chars)
            text_py = _to_pinyin(text_clean)

            best_match = None
            best_score = 0
            best_confidence = 0.0
            for script in scripts:
                raw_trigger = script["trigger_word"]
                if not raw_trigger:
                    continue
                # trigger_word 支持用 "|" 分隔配置多条别名, 任一命中即触发该脚本
                phrases = [p.strip() for p in raw_trigger.split("|") if p.strip()]
                for trigger in phrases:
                    trigger_clean = trigger.strip(strip_chars)
                    trigger_py = _to_pinyin(trigger_clean)

                    score = 0
                    # 连续度量: score + 辅助变量用于合成 confidence (不再是离散五档)
                    match_ratio = 0.0  # 匹配占比, 0-1
                    if trigger_clean == text_clean:
                        score, match_ratio = 5, 1.0
                    elif trigger == text:
                        score, match_ratio = 5, 1.0
                    elif trigger_clean in text_clean or text_clean in trigger_clean:
                        score = 3
                        shorter = min(len(trigger_clean), len(text_clean))
                        longer = max(len(trigger_clean), len(text_clean))
                        match_ratio = shorter / max(1, longer)
                    elif trigger_py and text_py and (trigger_py in text_py or text_py in trigger_py):
                        score = 2
                        shorter = min(len(trigger_py), len(text_py))
                        longer = max(len(trigger_py), len(text_py))
                        match_ratio = shorter / max(1, longer)
                    else:
                        # 拼音公共子串匹配
                        if trigger_py and text_py:
                            shorter_len = min(len(trigger_py), len(text_py))
                            # 短串需至少 8 拼音字符(约 3+ 汉字), 避免 "停止"/"安静" 之类 2 字短命令
                            # 的 4 字共有子串误触长 trigger ("停止"ting → "开始展厅讲解"zhanting)
                            if shorter_len >= 8:
                                common = 0
                                for i in range(len(text_py)):
                                    for j in range(i + 2, len(text_py) + 1):
                                        if text_py[i:j] in trigger_py:
                                            common = max(common, j - i)
                                # 公共子串占短串 60% 且至少 8 字符
                                if common >= max(8, int(shorter_len * 0.6)):
                                    score = 1
                                    match_ratio = common / max(1, shorter_len)

                    # 真实 confidence 合成: 按等级给基线 + match_ratio 微调, 避免五档跳跃
                    if score == 5:
                        conf = 0.98
                    elif score == 3:
                        conf = 0.80 + 0.12 * match_ratio  # 0.80-0.92
                    elif score == 2:
                        conf = 0.72 + 0.13 * match_ratio  # 0.72-0.85
                    elif score == 1:
                        conf = 0.60 + 0.15 * match_ratio  # 0.60-0.75
                    else:
                        conf = 0.0

                    if score > best_score or (score == best_score and conf > best_confidence):
                        best_score = score
                        best_confidence = conf
                        best_match = {
                            "intent": "start_tour",
                            "confidence": round(conf, 3),
                            "match_ratio": round(match_ratio, 3),
                            "score_tier": score,
                            "entities": {
                                "trigger_word": trigger,
                                "script_id": script["id"],
                                "script_name": script["name"],
                            },
                            "source": "trigger",
                        }
                        logger.debug("[IntentResolver] 触发词候选: '%s' score=%d (script=%s)",
                                    trigger, score, script["name"])

            if best_match:
                logger.info("[IntentResolver] 触发词命中: '%s' → script=%s (score=%d)",
                           text, best_match["entities"]["script_name"], best_score)
            else:
                logger.debug("[IntentResolver] 触发词未命中: '%s'", text)
            return best_match
        finally:
            await db.close()
    except Exception as e:
        logger.error("[IntentResolver] 触发词匹配异常: %s (text='%s')", e, text, exc_info=True)
    return None


# ============================================================
# LLM 兜底 — 两步决策: (1) 是否系统功能调用 (2) 归到哪个意图 / free_chat
# ============================================================
_LLM_SYSTEM_PROMPT_TEMPLATE = """你是展厅导览机器人"{robot_name}"的意图分类器。

【两步判断】
第一步: 这句话是"系统功能指令"还是"自由问答/闲聊"?
  - 系统功能指令 = 能映射到展厅可执行的具体操作 (导览/设备控制/专场切换/流程启动等)
  - 自由问答 = 闲聊寒暄/访客身份/展项知识/实时信息/开放性问题 → intent=free_chat

第二步: 若是系统功能, 从下表选一个最匹配的 intent:
  start_tour, end_tour, play_resource, take_photo, play_next, skip_exhibit, ask_explain,
  navigate_to, wait_please, resume_please, interactive_discuss, set_time, ask_current,
  query_help, query_status, list_exhibits, list_exhibit_resources,
  lights_on, lights_off, all_on, all_off, ad_screen_on, ad_screen_off,
  switch_special, go_charging, weather_query

若是自由问答/闲聊, intent="free_chat".

【语义细则】
- start_tour: 开始参观/开始讲解/发出流程触发词 (含未知短语但像命令)
- navigate_to: 明确去某展项, target 必须是展厅实际地点名 (大岛台/小岛台/CAVE 等)
- list_exhibit_resources: 询问某展项"有什么内容/资源" (带展项名, 不导航)
- interactive_discuss: 想讨论某话题, entities.topic
- switch_special: 切换专场, entities.special_name
- weather_query: 询问天气/温度/下雨
- free_chat: 寒暄/你好/谢谢/身份问候/关于我是谁/访客信息/展厅知识/开放问答

【输出格式】
{{"is_system_function": true/false, "intent":"意图名", "confidence":0.85, "entities":{{}}, "reply":""}}

- is_system_function=false 时, intent 必须 = "free_chat", confidence 反映"这是闲聊"的把握.
- entities 可含: resource_index, target, minutes, special_name, topic
- confidence 是你对这次"分类"本身的把握, 不是对答复内容的把握. 模棱两可时调低.
- 只返回纯 JSON, 不要 markdown, 不要思考标签."""


async def _build_llm_system_prompt() -> str:
    """延迟组装 _LLM_SYSTEM_PROMPT, 注入当前机器人名称"""
    from app.engine.names import get_robot_name
    name = await get_robot_name()
    return _LLM_SYSTEM_PROMPT_TEMPLATE.format(robot_name=name)


async def _llm_resolve(text: str, world_state_dict: dict | None = None) -> dict:
    """LLM意图识别"""
    config = get_config().get("llm", {})
    url = config.get("base_url", "")
    api_key = config.get("api_key", "")
    model = config.get("model", "minimax-m2.5")
    timeout = config.get("timeout_seconds", 15)

    if not url or not api_key:
        return {"intent": "chitchat", "confidence": 0.2, "entities": {}, "source": "no_llm"}

    user_msg = text
    if world_state_dict:
        ctx = world_state_dict.get("context_str", "")
        es = world_state_dict.get("engine_state", "")
        state_hint = f"[对话阶段: {es}]" if es else ""
        ctx_hint = f"[环境: {ctx}]" if ctx else ""
        if state_hint or ctx_hint:
            user_msg = f"{state_hint}{ctx_hint} 用户说: {text}"

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": await _build_llm_system_prompt()},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.2,
                "max_tokens": 256,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            async with session.post(url, json=payload, headers=headers) as resp:
                result = await resp.json()
                content = ""
                if "choices" in result and result["choices"]:
                    content = result["choices"][0].get("message", {}).get("content", "")
                return _parse_llm_response(content)
    except Exception as e:
        logger.error("[IntentResolver] LLM调用失败: %s", e)
        return {"intent": "chitchat", "confidence": 0.2, "entities": {}, "source": "llm_error"}


def _parse_llm_response(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines).strip()
    try:
        data = json.loads(content)
        raw_conf = float(data.get("confidence", 0.5))
        # LLM 自报 confidence 未校准, 大概率偏高; cap 在 0.85
        clamped_conf = min(0.85, max(0.0, raw_conf))
        intent = str(data.get("intent", "chitchat") or "chitchat")
        is_sys = bool(data.get("is_system_function", True))

        # 两步决策结果归一:
        # - is_system_function=false OR intent=free_chat → 统一出 chitchat (下游走 Q&A 兜底)
        # - is_system_function=true 且 intent 在白名单 → 返回该意图
        # 只有在能得到高置信度确认"是某个系统功能"时才让它走 tour_control 链路,
        # 否则一律认为是自由问答, 避免 LLM 硬拟合把闲聊分到 navigate_to 等.
        if not is_sys or intent == "free_chat":
            return {
                "intent": "chitchat",
                "confidence": clamped_conf,
                "entities": {},
                "reply": data.get("reply", ""),
                "source": "llm_free_chat",
                "llm_self_conf": raw_conf,
                "is_system_function": False,
            }
        return {
            "intent": intent,
            "confidence": clamped_conf,
            "entities": data.get("entities", {}) or {},
            "reply": data.get("reply", ""),
            "source": "llm",
            "llm_self_conf": raw_conf,
            "is_system_function": True,
        }
    except (json.JSONDecodeError, ValueError):
        return {"intent": "chitchat", "confidence": 0.3, "entities": {}, "source": "llm_parse_fail"}


# ============================================================
# 状态感知置信度衰减
# ============================================================
# 状态-意图不匹配表：{(state, intent): decay_factor}
# decay_factor 乘以原始置信度，<1.0 表示降低
_STATE_DECAY = {
    # EXPLANATION 状态下用户不太可能想"重新开始参观"
    ("EXPLANATION", "start_tour"): 0.6,
    # IDLE 状态下用户不太可能在说"下一个"（没有上下文）
    ("IDLE", "play_next"): 0.5,
    ("IDLE", "skip_exhibit"): 0.5,
    ("IDLE", "play_resource"): 0.6,
    ("IDLE", "resume_please"): 0.5,
    # END 状态下不太可能是"继续"
    ("END", "resume_please"): 0.5,
    # DISCUSSING 状态下大部分交互意图不太合理
    ("DISCUSSING", "start_tour"): 0.5,
    ("DISCUSSING", "play_resource"): 0.5,
    ("DISCUSSING", "navigate_to"): 0.6,
}


def _apply_state_confidence_decay(result: dict, engine_state: str) -> dict:
    """根据当前状态衰减不匹配意图的置信度"""
    if not result or not engine_state:
        return result
    key = (engine_state, result.get("intent", ""))
    decay = _STATE_DECAY.get(key)
    if decay is not None:
        old_conf = result["confidence"]
        result["confidence"] = round(old_conf * decay, 3)
        logger.debug("[IntentResolver] 状态衰减: %s + %s: %.2f → %.3f",
                     engine_state, result["intent"], old_conf, result["confidence"])
    return result


# ============================================================
# 统一入口
# ============================================================
async def resolve_intent(text: str, world_state=None,
                         skip_llm_fallback: bool = False) -> dict:
    """
    统一意图识别入口

    优先级: 规则(≥0.9) > 触发词 > LLM > 规则(低置信度) > 兜底

    Args:
        text: 用户说的话
        world_state: WorldState实例（可选，提供上下文）
        skip_llm_fallback: 调用方已确定不会用 LLM 意图分类结果 (典型: QA 路径
            QueryRouter 已分到非 TOUR_CONTROL), 跳掉慢 LLM 节省 5-10s。
            注意只跳 LLM 那一段, rule_match / 同义词 / 拼音回退 / trigger_word
            都还会跑, 控制类高置信意图仍然能正常命中。

    Returns:
        {"intent": str, "confidence": float, "entities": dict, "reply": str, "source": str, "raw_text": str}
    """
    # 去唤醒词
    clean = _strip_wake_word(text)
    if not clean:
        return {"intent": "chitchat", "confidence": 0.5, "entities": {},
                "reply": "您好，请问有什么可以帮您？", "source": "empty", "raw_text": text}

    # 0. 流程触发词精确匹配优先 (admin 注册的明确口令, 最高优先级)
    #    解决问题: 触发词如 "开始日常参观讲解" 含子串 "讲解",
    #    会被 ask_explain 规则 (rule_prio=0.85 但实际 conf 0.53) 抢先 return,
    #    导致触发词永远命中不了。
    #    策略: score>=4 (精确 / 去标点精确) 立刻返回; 低分 (子串/拼音) 走完
    #    规则后回来统一选优, 避免误触。
    early_trigger = await _match_trigger_word(clean)
    if early_trigger and early_trigger.get("score_tier", 0) >= 4:
        early_trigger["raw_text"] = text
        logger.info(
            "[IntentResolver] 触发词精确命中 (优先级最高) intent=%s script_id=%s text=%r",
            early_trigger.get("intent"),
            (early_trigger.get("entities") or {}).get("script_id"),
            clean,
        )
        return early_trigger

    # 1. 规则匹配
    rule_result = _rule_match(clean)

    # 状态相关调整
    engine_state = ""
    if world_state:
        engine_state = world_state.dialog.engine_state if hasattr(world_state, 'dialog') else ""
        browse_mode = world_state.browse.mode if hasattr(world_state, 'browse') else ""

        if rule_result:
            if engine_state == "ASK_NEXT":
                # 浏览模式或ASK_NEXT下纯数字直接当play_resource（序号选择）
                if re.match(r'^[一二三四五六七八九十\d]+$', clean.strip()):
                    rule_result["intent"] = "play_resource"
                    rule_result["entities"]["resource_index"] = _parse_resource_index(clean)
                    rule_result["confidence"] = 0.95

            # 置信度衰减：当前状态下不太合理的意图降低置信度
            rule_result = _apply_state_confidence_decay(rule_result, engine_state)

    # 决定是否跳过 LLM: 用 rule_prio(规则优先级/上限) 而非 real confidence,
    # 否则同一条规则高优先级但部分匹配会被错误 gate 掉.
    if rule_result and rule_result.get("rule_prio", rule_result.get("confidence", 0)) >= 0.85:
        rule_result["raw_text"] = text
        logger.info(
            "[IntentResolver] 规则命中 source=rule intent=%s conf=%.2f quality=%.2f prio=%.2f text=%r",
            rule_result["intent"], rule_result["confidence"],
            rule_result.get("match_quality", 1.0), rule_result.get("rule_prio", 0),
            clean,
        )
        return rule_result

    # 1.5 同义/谐音/错字词典 (设备/资源/机器人控制): 规则没中但属常见指令变体
    # 先于 rule_pinyin: 同义词的 L1(字面量相等) 是强信号, 比拼音同音匹配更可靠
    # 能避免 "点亮"(dianliang) 被 rule_pinyin 误抢到 "电量"(dianliang) 的 query_status.
    try:
        from app.engine.intent_synonyms import synonym_match
        syn_result = synonym_match(clean)
        if syn_result and syn_result["confidence"] >= 0.70:
            syn_result["raw_text"] = text
            logger.info(
                "[IntentResolver] 同义词命中 source=%s intent=%s conf=%.2f term=%r text=%r",
                syn_result.get("source"), syn_result["intent"],
                syn_result["confidence"], syn_result.get("matched_term"), clean,
            )
            return syn_result
    except Exception as e:
        logger.debug("[IntentResolver] 同义词匹配异常, 跳过: %s", e)

    # 1.6 规则拼音回退: 处理 ASR 谐音(残官→参观, 讲洁→讲解, 带我残官→带我参观)
    # 只对无槽位规则生效, 置信度比原规则略低但高于 LLM 阈值
    if not rule_result or rule_result.get("rule_prio", rule_result.get("confidence", 0)) < 0.85:
        # 1.6a 纯字面量拼音回退
        py_result = _rule_match_pinyin(clean)
        if py_result and py_result["confidence"] >= 0.75:
            py_result["raw_text"] = text
            logger.info("[IntentResolver] 拼音规则命中 source=rule_pinyin intent=%s conf=%.2f text=%r",
                        py_result["intent"], py_result["confidence"], clean)
            return py_result
        # 1.6b 拼音前缀扫描提取 navigate_to target (谐音: 带我残官大脑台 → 大脑台)
        nav_py = _extract_nav_target_by_pinyin(clean)
        if nav_py:
            nav_py["raw_text"] = text
            logger.info(
                "[IntentResolver] 拼音前缀命中 source=rule_pinyin_target intent=%s target=%s conf=%.2f",
                nav_py["intent"], nav_py["entities"].get("target"), nav_py["confidence"],
            )
            return nav_py

    # 2. 触发词匹配 (低分回退路径) — 精确匹配在第 0 步已经处理, 这里只接子串/拼音
    trigger_result = early_trigger   # 复用上面那次 query 的结果, 不再重新查 DB
    if trigger_result:
        trigger_result["raw_text"] = text
        logger.info(
            "[IntentResolver] 触发词命中 source=trigger intent=%s conf=%.2f score=%d ratio=%.2f text=%r",
            trigger_result["intent"], trigger_result["confidence"],
            trigger_result.get("score_tier", 0), trigger_result.get("match_ratio", 0), clean,
        )
        return trigger_result

    # 3. LLM兜底前的快速早出口: QA 路径 (skip_llm_fallback=True) 时不跑 LLM
    # rule/synonym/pinyin/trigger 都已尝试且未命中, LLM 几乎不会改变分类结论
    # (QA 类输入 LLM 大多回 chitchat, 不会触发 _tour_intents 覆盖),
    # 多花的 5-10s 纯浪费, 直接出兜底结果让 SmartEngine 走 AnswerEngine。
    if skip_llm_fallback:
        if rule_result:
            rule_result["raw_text"] = text
            logger.info(
                "[IntentResolver] 跳 LLM (QA 路径), 用低置信度规则 intent=%s conf=%.2f text=%r",
                rule_result["intent"], rule_result["confidence"], clean,
            )
            return rule_result
        logger.info("[IntentResolver] 跳 LLM (QA 路径), 无规则命中 → chitchat 兜底 text=%r", clean)
        return {"intent": "chitchat", "confidence": 0.3, "entities": {},
                "source": "no_llm_skip", "raw_text": text}

    # 3. LLM兜底（传入状态上下文帮助 LLM 做更准确的分类）
    ws_dict = None
    if world_state:
        ws_dict = {
            "context_str": world_state.to_context_string() if hasattr(world_state, 'to_context_string') else "",
            "engine_state": engine_state,
        }
    llm_result = await _llm_resolve(clean, ws_dict)
    llm_result["raw_text"] = text

    # 4. LLM也不行，回退到低置信度规则结果
    if llm_result.get("confidence", 0) < 0.4 and rule_result:
        rule_result["raw_text"] = text
        return rule_result

    logger.info(
        "[IntentResolver] LLM结果 source=%s intent=%s conf=%.2f self_conf=%.2f text=%r",
        llm_result.get("source", "llm"), llm_result["intent"],
        llm_result.get("confidence", 0), llm_result.get("llm_self_conf", 0), clean,
    )
    return llm_result
