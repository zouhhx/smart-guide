"""
WorldState — 全局实时状态模型

维护展厅所有实体(机器人/访客/展项/环境)的当前状态，
为SmartEngine决策提供一致的只读快照。

设计原则：
- 单例，进程内共享
- 所有状态变更通过 set_*/transition 方法，自动记录日志
- to_dict() 返回快照，供 LLM 上下文使用
"""

import time
import json
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("hub.engine.world_state")


# ============================================================
# 数据类
# ============================================================
@dataclass
class RobotState:
    """机器人状态"""
    robot_id: str = ""
    name: str = ""
    status: str = "idle"           # idle/navigating/speaking/listening/charging/offline
    battery: int = 100
    position: str = ""             # 当前点位名
    position_id: str = ""          # 当前点位ID
    people_in_view: int = 0        # 视野内人数
    nearest_distance: float = 0.0 # 最近人员距离
    connected: bool = False
    last_update: float = 0.0


@dataclass
class VisitorState:
    """当前访客状态"""
    is_present: bool = False        # 是否有人在
    name: str = ""                  # 已知访客名（空=陌生人）
    face_id: str = ""               # 人脸ID
    interaction_count: int = 0      # 互动次数
    preferences: list = field(default_factory=list)  # 提取的兴趣标签


@dataclass
class ExhibitState:
    """展项/展区状态"""
    current_exhibit_id: str = ""    # 当前讲解的展项
    current_exhibit_name: str = ""
    current_resource_id: str = ""  # 当前播放的资源
    current_resource_name: str = ""
    current_special_id: int = 0    # 当前专场
    current_special_name: str = ""


@dataclass
class DialogState:
    """对话状态"""
    mode: str = "idle"              # idle/flow/smart (当前对话模式)
    engine_state: str = "IDLE"     # SmartEngine状态机: IDLE/WELCOME/EXPLANATION/ASK_NEXT/END
    last_intent: str = ""          # 上一次识别的意图
    last_user_text: str = ""       # 上一次用户说的话
    last_bot_reply: str = ""      # 上一次机器人回复
    turn_count: int = 0            # 当前对话轮次
    is_speaking: bool = False      # 机器人正在说话(用于自干扰过滤)
    speaking_started_at: float = 0.0
    last_speaking_done_at: float = 0.0  # bot 最近一次说完话的时间 (wait_until_idle 用)
    wait_count: int = 0            # 连续无响应计数
    max_wait: int = 3              # 最大无响应次数后回IDLE
    conversation_history: list = field(default_factory=list)  # [{role, content}, ...]
    max_history: int = 20          # 最大对话历史条数


@dataclass
class BrowseState:
    """交互浏览状态（多轮展项/资源浏览循环）"""
    mode: str = ""                      # "": 非浏览, "list_exhibits", "list_resources", "playing", "list_specials"
    exhibits_cache: list = field(default_factory=list)   # [{id, name, area_name}, ...]
    resources_cache: list = field(default_factory=list)   # [{id, name, type, ...}, ...]
    specials_cache: list = field(default_factory=list)    # [{id, name}, ...]
    selected_exhibit_id: str = ""
    selected_exhibit_name: str = ""
    # 挂起快照: 浏览中遇到无关输入时, 暂存现场, 等用户答完追问"继续吗"再恢复。
    # awaiting_resume=True 表示已 speak "要继续吗", 下一轮用户回复决定恢复/丢弃。
    suspended_mode: str = ""
    suspended_exhibits_cache: list = field(default_factory=list)
    suspended_resources_cache: list = field(default_factory=list)
    suspended_specials_cache: list = field(default_factory=list)
    suspended_selected_exhibit_id: str = ""
    suspended_selected_exhibit_name: str = ""
    awaiting_resume: bool = False


@dataclass
class ExecutionState:
    """命令执行跟踪（防卡死）"""
    pending_command: str = ""           # "navigate:大岛台" / "play:937"
    pending_since: float = 0.0
    last_command: str = ""
    last_result: str = ""               # ok/error/timeout
    last_time: float = 0.0
    consecutive_failures: int = 0


@dataclass
class RecentContext:
    """短时上下文记忆 — 支持指代消解和打断恢复"""
    last_candidates: list = field(default_factory=list)   # 上次模糊匹配的候选列表
    last_target: str = ""                                  # 上次用户指定的目标
    last_action: str = ""                                  # 上次执行的动作
    last_action_result: str = ""                           # ok/failed/timeout
    last_action_detail: str = ""                           # 人类可读的执行细节
    suspended_state: str = ""                              # 被打断时的引擎状态
    suspended_plan_index: int = -1                         # 被打断时的计划位置
    suspended_resume_hint: str = ""                        # 恢复时的提示动作
    suspended_context_snippet: str = ""                    # 被打断时的上下文片段（恢复时用）
    last_mentioned_exhibit: str = ""                       # 最近一次提到的展项名
    last_mentioned_exhibit_id: str = ""                    # 最近一次提到的展项ID
    low_confidence_streak: int = 0                         # 连续低置信度轮数
    last_input_text: str = ""                              # 上一次用户输入（去重用）
    last_input_time: float = 0.0                           # 上一次任意 ASR 到达时间戳 (handle_speech 入口刷新, 用于 wait_until_idle 感知)
    last_record_time: float = 0.0                          # 上一次成功 record_input 时间戳 (去重判定用, 仅过完所有过滤的 ASR 才更新)


@dataclass
class EnvironmentState:
    """环境状态"""
    lights_on: bool = True
    devices_status: dict = field(default_factory=dict)  # {device_id: on/off}


# ============================================================
# WorldState 单例
# ============================================================
class WorldState:
    """世界状态模型 — 全局单例"""

    _instance = None

    @classmethod
    def get_instance(cls) -> "WorldState":
        if cls._instance is None:
            cls._instance = WorldState()
        return cls._instance

    def __init__(self):
        self.robots: Dict[str, RobotState] = {}     # robot_id -> state
        self.visitor = VisitorState()
        self.exhibit = ExhibitState()
        self.dialog = DialogState()
        self.browse = BrowseState()
        self.execution = ExecutionState()
        self.environment = EnvironmentState()
        self.recent = RecentContext()
        self._last_sync = 0.0

    # ---- 机器人状态 ----
    def update_robot(self, robot_id: str, **kwargs):
        """更新机器人状态"""
        if robot_id not in self.robots:
            self.robots[robot_id] = RobotState(robot_id=robot_id)
        robot = self.robots[robot_id]
        for k, v in kwargs.items():
            if hasattr(robot, k):
                setattr(robot, k, v)
        robot.last_update = time.time()

    def get_robot(self, robot_id: str) -> Optional[RobotState]:
        return self.robots.get(robot_id)

    def get_active_robot(self) -> Optional[RobotState]:
        """获取第一个在线机器人"""
        for r in self.robots.values():
            if r.connected and r.status != "offline":
                return r
        return None

    def robot_disconnected(self, robot_id: str):
        if robot_id in self.robots:
            self.robots[robot_id].connected = False
            self.robots[robot_id].status = "offline"

    # ---- 访客状态 ----
    def visitor_detected(self, name: str = "", face_id: str = "", distance: float = 0):
        """检测到人"""
        self.visitor.is_present = True
        if name:
            self.visitor.name = name
        if face_id:
            self.visitor.face_id = face_id

    def visitor_left(self):
        """人离开 — 清空整个 VisitorState, 防止下一个人来时残留旧 name/face_id"""
        self.visitor.is_present = False
        self.visitor.name = ""
        self.visitor.face_id = ""
        self.visitor.interaction_count = 0
        self.visitor.preferences = []

    def visitor_no_person(self):
        """视野无人"""
        self.visitor.is_present = False

    # ---- 展项状态 ----
    def set_current_exhibit(self, exhibit_id: str, name: str = ""):
        self.exhibit.current_exhibit_id = exhibit_id
        self.exhibit.current_exhibit_name = name
        # 同步更新短时记忆中的最近展项
        if exhibit_id:
            self.recent.last_mentioned_exhibit_id = exhibit_id
        if name:
            self.recent.last_mentioned_exhibit = name

    def set_current_resource(self, resource_id: str, name: str = ""):
        self.exhibit.current_resource_id = resource_id
        self.exhibit.current_resource_name = name

    def set_current_special(self, special_id: int, name: str = ""):
        self.exhibit.current_special_id = special_id
        self.exhibit.current_special_name = name

    # ---- 对话状态 ----
    _VALID_STATES = frozenset({"IDLE", "WELCOME", "EXPLANATION", "ASK_NEXT", "DISCUSSING", "DISCUSS_DONE", "END"})

    def transition(self, new_state: str):
        """SmartEngine状态机转移"""
        if new_state not in self._VALID_STATES:
            logger.error("[WorldState] 非法状态 '%s', 回落 IDLE", new_state)
            new_state = "IDLE"
        old = self.dialog.engine_state
        self.dialog.engine_state = new_state
        # 状态切换时清除可能残留的 is_speaking 标志
        if self.dialog.is_speaking:
            self.dialog.is_speaking = False
            self.dialog.speaking_started_at = 0
        logger.info("[WorldState] 状态转移: %s → %s", old, new_state)

    def set_speaking(self, speaking: bool):
        """标记机器人说话状态（自干扰过滤用）"""
        self.dialog.is_speaking = speaking
        if speaking:
            self.dialog.speaking_started_at = time.time()
        else:
            # 说完话, 记下时间. wait_until_idle / 对话节奏判断需要这个.
            self.dialog.last_speaking_done_at = time.time()

    def record_turn(self, user_text: str, intent: str, bot_reply: str = ""):
        """记录一轮对话"""
        self.dialog.last_user_text = user_text
        self.dialog.last_intent = intent
        self.dialog.last_bot_reply = bot_reply
        self.dialog.turn_count += 1
        self.visitor.interaction_count += 1
        self.dialog.wait_count = 0  # 有交互就清零

    def increment_wait(self):
        """无响应计数+1"""
        self.dialog.wait_count += 1

    def reset_dialog(self):
        """重置对话状态（回到IDLE）"""
        self.dialog.engine_state = "IDLE"
        self.dialog.turn_count = 0
        self.dialog.wait_count = 0
        self.dialog.last_intent = ""
        self.dialog.last_user_text = ""
        self.dialog.last_bot_reply = ""
        self.dialog.is_speaking = False
        self.dialog.conversation_history = []
        self.exhibit.current_exhibit_id = ""
        self.exhibit.current_exhibit_name = ""
        self.exhibit.current_resource_id = ""
        self.exhibit.current_resource_name = ""
        # 保留last_input用于去重，其余全部重置
        saved_input = self.recent.last_input_text
        saved_time = self.recent.last_input_time
        self.recent = RecentContext()
        self.recent.last_input_text = saved_input
        self.recent.last_input_time = saved_time
        self.reset_browse()

    def suspend_activity(self, plan_index: int = -1, resume_hint: str = "",
                         context_snippet: str = ""):
        """挂起当前活动（被打断时调用）"""
        self.recent.suspended_state = self.dialog.engine_state
        self.recent.suspended_plan_index = plan_index
        self.recent.suspended_resume_hint = resume_hint
        self.recent.suspended_context_snippet = context_snippet
        logger.info("[WorldState] 挂起活动: state=%s plan_index=%d hint=%s snippet=%s",
                    self.recent.suspended_state, plan_index, resume_hint, context_snippet[:30])

    def pop_suspended(self) -> dict:
        """取出并清除挂起的活动，返回 {state, plan_index, resume_hint, context_snippet} 或空 dict"""
        if not self.recent.suspended_state:
            return {}
        info = {
            "state": self.recent.suspended_state,
            "plan_index": self.recent.suspended_plan_index,
            "resume_hint": self.recent.suspended_resume_hint,
            "context_snippet": self.recent.suspended_context_snippet,
        }
        self.recent.suspended_state = ""
        self.recent.suspended_plan_index = -1
        self.recent.suspended_resume_hint = ""
        self.recent.suspended_context_snippet = ""
        logger.info("[WorldState] 恢复挂起活动: %s", info)
        return info

    def record_action(self, action: str, result: str, detail: str = ""):
        """记录最近一次动作执行结果"""
        self.recent.last_action = action
        self.recent.last_action_result = result
        self.recent.last_action_detail = detail

    # ---- 输入去重与质量保护 ----
    def is_duplicate_input(self, text: str, window: float = 3.0) -> bool:
        """检查是否与上次成功记录的输入重复（window 秒内完全相同的文本）.

        注意: 用 last_record_time 而不是 last_input_time。前者只在 record_input
        被调用时刷新, 后者每个 ASR 进入 handle_speech 入口都刷, 用作去重基准
        会让"距上次"永远是 0, 误把所有重复的 ASR 都判成重复。
        """
        if not self.recent.last_input_text or text != self.recent.last_input_text:
            return False
        return (time.time() - self.recent.last_record_time) < window

    def record_input(self, text: str):
        """记录当前用户输入（用于去重）"""
        self.recent.last_input_text = text
        self.recent.last_input_time = time.time()
        self.recent.last_record_time = time.time()

    def record_low_confidence(self, is_low: bool):
        """记录置信度状态（连续低置信度 → 收缩模式）"""
        if is_low:
            self.recent.low_confidence_streak += 1
        else:
            self.recent.low_confidence_streak = 0

    def update_mentioned_exhibit(self, exhibit_id: str, exhibit_name: str):
        """更新最近一次提到的展项（用于指代消解）"""
        if exhibit_id:
            self.recent.last_mentioned_exhibit_id = exhibit_id
        if exhibit_name:
            self.recent.last_mentioned_exhibit = exhibit_name

    # ---- 对话历史 ----
    def add_to_history(self, role: str, content: str):
        """添加对话记录（user/assistant）"""
        if not content:
            return
        self.dialog.conversation_history.append({"role": role, "content": content})
        # 保持在限定条数内
        if len(self.dialog.conversation_history) > self.dialog.max_history:
            self.dialog.conversation_history = self.dialog.conversation_history[-self.dialog.max_history:]

    # ---- 浏览状态 ----
    def reset_browse(self):
        """重置浏览状态(包含挂起快照)"""
        self.browse.mode = ""
        self.browse.exhibits_cache = []
        self.browse.resources_cache = []
        self.browse.specials_cache = []
        self.browse.selected_exhibit_id = ""
        self.browse.selected_exhibit_name = ""
        self.clear_suspended_browse()

    def suspend_browse(self):
        """把当前 browse 现场拷到 suspended_*, 清空 active 字段。下一轮用户答"继续"再 resume."""
        b = self.browse
        b.suspended_mode = b.mode
        b.suspended_exhibits_cache = list(b.exhibits_cache)
        b.suspended_resources_cache = list(b.resources_cache)
        b.suspended_specials_cache = list(b.specials_cache)
        b.suspended_selected_exhibit_id = b.selected_exhibit_id
        b.suspended_selected_exhibit_name = b.selected_exhibit_name
        b.mode = ""
        b.exhibits_cache = []
        b.resources_cache = []
        b.specials_cache = []
        b.selected_exhibit_id = ""
        b.selected_exhibit_name = ""

    def resume_browse(self) -> bool:
        """从 suspended_* 还原 active 字段。无快照返 False."""
        b = self.browse
        if not b.suspended_mode:
            return False
        b.mode = b.suspended_mode
        b.exhibits_cache = list(b.suspended_exhibits_cache)
        b.resources_cache = list(b.suspended_resources_cache)
        b.specials_cache = list(b.suspended_specials_cache)
        b.selected_exhibit_id = b.suspended_selected_exhibit_id
        b.selected_exhibit_name = b.suspended_selected_exhibit_name
        self.clear_suspended_browse()
        return True

    def clear_suspended_browse(self):
        b = self.browse
        b.suspended_mode = ""
        b.suspended_exhibits_cache = []
        b.suspended_resources_cache = []
        b.suspended_specials_cache = []
        b.suspended_selected_exhibit_id = ""
        b.suspended_selected_exhibit_name = ""
        b.awaiting_resume = False

    # ---- 执行跟踪 ----
    def command_started(self, cmd_name: str):
        """记录命令开始"""
        self.execution.pending_command = cmd_name
        self.execution.pending_since = time.time()

    def command_completed(self, result: str):
        """记录命令完成"""
        self.execution.last_command = self.execution.pending_command
        self.execution.last_result = result
        self.execution.last_time = time.time()
        self.execution.pending_command = ""
        self.execution.pending_since = 0.0
        if result == "ok":
            self.execution.consecutive_failures = 0
        else:
            self.execution.consecutive_failures += 1

    def is_stuck(self, timeout: float = 60.0) -> bool:
        """检查是否卡死"""
        if self.execution.pending_command and self.execution.pending_since > 0:
            return (time.time() - self.execution.pending_since) > timeout
        return False

    # ---- 快照 ----
    def to_dict(self) -> dict:
        """返回状态快照，供LLM上下文使用"""
        active_robot = self.get_active_robot()
        return {
            "robot": asdict(active_robot) if active_robot else {"status": "offline"},
            "visitor": asdict(self.visitor),
            "exhibit": asdict(self.exhibit),
            "dialog": {
                "engine_state": self.dialog.engine_state,
                "turn_count": self.dialog.turn_count,
                "wait_count": self.dialog.wait_count,
                "last_intent": self.dialog.last_intent,
                "is_speaking": self.dialog.is_speaking,
            },
            "browse": asdict(self.browse),
            "execution": asdict(self.execution),
            "environment": asdict(self.environment),
            "timestamp": time.time(),
        }

    def to_context_string(self) -> str:
        """生成给LLM的简洁上下文字符串"""
        r = self.get_active_robot()
        parts = []
        if r:
            parts.append(f"机器人: {r.name or r.robot_id}, 状态={r.status}, 电量={r.battery}%, 位置={r.position}")
        parts.append(f"访客: {'在' if self.visitor.is_present else '不在'}"
                     f"{f'({self.visitor.name})' if self.visitor.name else '(陌生人)'}")
        parts.append(f"对话: 状态={self.dialog.engine_state}, 轮次={self.dialog.turn_count}")
        if self.exhibit.current_exhibit_name:
            parts.append(f"当前展项: {self.exhibit.current_exhibit_name}")
        if self.exhibit.current_special_name:
            parts.append(f"当前专场: {self.exhibit.current_special_name}")
        if self.browse.mode:
            parts.append(f"浏览模式: {self.browse.mode}")
            if self.browse.selected_exhibit_name:
                parts.append(f"已选展项: {self.browse.selected_exhibit_name}")
        if self.execution.pending_command:
            parts.append(f"执行中: {self.execution.pending_command}")
        if self.execution.consecutive_failures > 0:
            parts.append(f"连续失败: {self.execution.consecutive_failures}次")
        if self.dialog.engine_state == "DISCUSSING":
            parts.append("正在话题讨论中")
        elif self.dialog.engine_state == "DISCUSS_DONE":
            parts.append("话题讨论刚结束")
        return " | ".join(parts)


# 模块级便捷函数
def get_world_state() -> WorldState:
    return WorldState.get_instance()
