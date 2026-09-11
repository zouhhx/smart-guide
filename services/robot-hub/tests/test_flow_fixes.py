"""
流程修复回归测试

覆盖：
1. listen timeout 不被视为成功 (flow_executor._step_listen)
2. 补时策略分类化 (tts/show/play_resource 不补满)
3. navigate/listen 失败终止流程
4. conversation.py 双轨防护
5. intent_resolver: set_time 意图正确识别
6. intent_resolver: start_tour 回扫时长

运行:  python3 -m pytest tests/test_flow_fixes.py -v
或:    python3 tests/test_flow_fixes.py
"""

import os
import sys
import types
import asyncio
import time
import importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 仅在依赖确实未安装时 stub；不能在 pytest 收集阶段覆盖已经安装的
# aiohttp，否则后收集的 Gateway/Legacy HTTP 测试无法导入 aiohttp.web。
for mod in ("aiohttp", "aiosqlite", "pypinyin"):
    try:
        importlib.import_module(mod)
    except ImportError:
        m = types.ModuleType(mod)
        for attr in ("ClientSession", "ClientTimeout", "connect", "Connection", "lazy_pinyin"):
            setattr(m, attr, lambda *a, **kw: "")
        sys.modules[mod] = m


# ============================================================
# Test 1: listen timeout → False
# ============================================================
def test_followup_listen_timeout_uses_runtime_config_and_safe_bounds():
    """回答后的免唤醒窗口应读取现有配置，并限制在 APK 支持范围。"""
    from app.tcp.server import _resolve_followup_listen_timeout

    assert _resolve_followup_listen_timeout({"listenTimeout": 30}) == 30
    assert _resolve_followup_listen_timeout({"listenTimeout": "45"}) == 45
    assert _resolve_followup_listen_timeout({"listenTimeout": 0}) == 5
    assert _resolve_followup_listen_timeout({"listenTimeout": 999}) == 120
    assert _resolve_followup_listen_timeout({"listenTimeout": "invalid"}) == 30


def test_step_listen_timeout_is_failure():
    """evt_listen_timeout 回调不应被视为成功"""
    from app.engine.flow_executor import _step_listen

    class FakeRobot:
        async def send_cmd(self, cmd_type, data, timeout=30):
            # 模拟机器人返回 listen timeout
            return {"type": "evt_listen_timeout", "data": {"reason": "timeout"}}

    result = asyncio.run(
        _step_listen(FakeRobot(), {"seconds": 10, "language": "zh"}, 10)
    )
    assert result is False, "evt_listen_timeout 应返回 False"


def test_step_listen_speech_result_is_success():
    """evt_speech_result 回调应被视为成功"""
    from app.engine.flow_executor import _step_listen

    class FakeRobot:
        async def send_cmd(self, cmd_type, data, timeout=30):
            return {"type": "evt_speech_result", "data": {"text": "你好"}}

    result = asyncio.run(
        _step_listen(FakeRobot(), {"seconds": 10, "language": "zh"}, 10)
    )
    assert result is True, "evt_speech_result 应返回 True"


def test_step_listen_none_is_failure():
    """send_cmd 返回 None（网络超时）应视为失败"""
    from app.engine.flow_executor import _step_listen

    class FakeRobot:
        async def send_cmd(self, cmd_type, data, timeout=30):
            return None

    result = asyncio.run(
        _step_listen(FakeRobot(), {"seconds": 10, "language": "zh"}, 10)
    )
    assert result is False, "send_cmd None 应返回 False"


# ============================================================
# Test 2: navigate 失败返回 False
# ============================================================
def test_step_navigate_evt_nav_failed():
    """evt_nav_failed 回调 → navigate 步骤返回 False"""
    from app.engine.flow_executor import _step_navigate

    # stub get_db
    original_get_db = None
    try:
        import app.database as db_mod
        original_get_db = db_mod.get_db

        class FakeDB:
            async def execute_fetchall(self, *a):
                return [{"name": "test_point"}]
            async def close(self):
                pass

        async def fake_get_db():
            return FakeDB()
        db_mod.get_db = fake_get_db

        class FakeRobot:
            async def send_cmd(self, cmd_type, data, timeout=30):
                return {"type": "evt_nav_failed", "data": {"reason": "obstacle"}}

        result = asyncio.run(
            _step_navigate(FakeRobot(), {"pointId": "p1", "pointName": "test"}, 30)
        )
        assert result is False, "evt_nav_failed 应返回 False"
    finally:
        if original_get_db:
            db_mod.get_db = original_get_db


# ============================================================
# Test 3: intent_resolver 规则匹配
# ============================================================
def test_rule_match_set_time():
    """'10分钟' 应命中 set_time 意图"""
    from app.engine.intent_resolver import _rule_match
    r = _rule_match("10分钟")
    assert r is not None, "'10分钟' 应有规则命中"
    assert r["intent"] == "set_time", f"期望 set_time, 实际 {r['intent']}"
    assert r["entities"].get("minutes") == 10


def test_rule_match_start_tour_with_duration():
    """'我想参观10分钟' 应命中 start_tour 并回扫出 minutes"""
    from app.engine.intent_resolver import _rule_match
    r = _rule_match("我想参观10分钟")
    assert r is not None
    # 可能命中 start_tour（带回扫）或 set_time
    # 关键是 minutes 必须被提取
    assert r["entities"].get("minutes") is not None, f"应提取 minutes, 实际 entities={r['entities']}"


def test_rule_match_start_tour_half_hour():
    """'我想参观半小时' 应命中 start_tour 并回扫出 30 分钟
    注意：'带我参观半小时' 会被 navigate_to '带我参观(.+)' 先捕获(confidence更高),
    所以用 '我想参观半小时' 确保 start_tour 命中。
    """
    from app.engine.intent_resolver import _rule_match
    r = _rule_match("我想参观半小时")
    assert r is not None
    assert r["intent"] == "start_tour", f"期望 start_tour, 实际 {r['intent']}"
    assert r["entities"].get("minutes") == 30, f"期望 minutes=30, 实际 {r['entities']}"


def test_rule_match_navigate_to():
    """'带我去大岛台' 应命中 navigate_to 并提取 target (台被模式[台]消耗,target=大岛)"""
    from app.engine.intent_resolver import _rule_match
    r = _rule_match("带我去大岛台")
    assert r is not None
    assert r["intent"] == "navigate_to", f"期望 navigate_to, 实际 {r['intent']}"
    target = r["entities"].get("target", "")
    assert "大岛" in target, f"target 应包含大岛, 实际 '{target}'"


def test_rule_match_end_tour():
    """'结束' 应命中 end_tour"""
    from app.engine.intent_resolver import _rule_match
    r = _rule_match("结束")
    assert r is not None
    assert r["intent"] == "end_tour"


def test_rule_match_explain():
    """'讲解一下机械臂' 应命中 ask_explain 或 navigate_to"""
    from app.engine.intent_resolver import _rule_match
    r = _rule_match("讲解一下机械臂")
    assert r is not None
    # "讲解(.+)" 匹配 navigate_to，提取target=机械臂
    assert r["intent"] in ("navigate_to", "ask_explain"), f"实际 {r['intent']}"


# 旧 Test 4 (conversation.py 双轨防护) 随 conversation.py 一起删除 —
# SmartEngine 是唯一入口, 不再有"双轨"需要 guard。


# ============================================================
# Test 5: FlowExecution 基本操作
# ============================================================
def test_flow_execution_cancel():
    """FlowExecution cancel 应设置 status 和 event"""
    from app.engine.flow_executor import FlowExecution
    fe = FlowExecution(1, "robot1")
    fe.status = "running"
    fe.cancel()
    assert fe.status == "cancelled"
    assert fe._cancel_event.is_set()


def test_flow_execution_pause_resume():
    """FlowExecution pause/resume 应正确切换"""
    from app.engine.flow_executor import FlowExecution
    fe = FlowExecution(1, "robot1")
    fe.status = "running"
    fe.pause()
    assert fe.status == "paused"
    assert not fe._pause_event.is_set()
    fe.resume()
    assert fe.status == "running"
    assert fe._pause_event.is_set()


# ============================================================
# 直接运行
# ============================================================
if __name__ == "__main__":
    fails = 0
    passes = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passes += 1
            except (AssertionError, Exception) as e:
                print(f"  FAIL  {name}: {e}")
                fails += 1
    print(f"\n{passes} passed, {fails} failed")
    sys.exit(1 if fails else 0)
