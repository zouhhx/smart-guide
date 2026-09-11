"""
访客记忆查询模块测试
"""
import os, sys, types, asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for mod in ("aiohttp", "aiosqlite", "pypinyin"):
    if mod not in sys.modules:
        m = types.ModuleType(mod)
        for attr in ("ClientSession", "ClientTimeout", "connect", "Connection", "lazy_pinyin", "Row"):
            setattr(m, attr, lambda *a, **kw: "")
        sys.modules[mod] = m


# ============================================================
# 分类器测试
# ============================================================
def test_classify_identity_check():
    from app.engine.visitor_memory_query import classify_memory_query
    assert classify_memory_query("你认识我吗") == "identity_check"
    assert classify_memory_query("你还认识我吗") == "identity_check"
    assert classify_memory_query("你记得我吗") == "identity_check"
    assert classify_memory_query("我是谁") == "identity_check"
    assert classify_memory_query("你见过我吗") == "identity_check"


def test_classify_ask_name():
    from app.engine.visitor_memory_query import classify_memory_query
    assert classify_memory_query("我叫什么") == "ask_name"
    assert classify_memory_query("我叫啥") == "ask_name"
    assert classify_memory_query("知道我的名字吗") == "ask_name"
    assert classify_memory_query("你记得我名字吗") == "ask_name"


def test_classify_visit_count():
    from app.engine.visitor_memory_query import classify_memory_query
    assert classify_memory_query("我来过几次") == "visit_count"
    assert classify_memory_query("我第几次来") == "visit_count"
    assert classify_memory_query("这是我第几次来") == "visit_count"
    assert classify_memory_query("我来过这里吗") == "visit_count"


def test_classify_recall_history():
    from app.engine.visitor_memory_query import classify_memory_query
    assert classify_memory_query("我昨天和你说了什么") == "recall_history"
    assert classify_memory_query("上次我问了什么") == "recall_history"
    assert classify_memory_query("我们上回聊过什么") == "recall_history"
    assert classify_memory_query("我之前说过什么") == "recall_history"
    assert classify_memory_query("我们聊过什么") == "recall_history"


def test_classify_interest_query():
    from app.engine.visitor_memory_query import classify_memory_query
    assert classify_memory_query("我喜欢什么") == "interest_query"
    assert classify_memory_query("我最关注什么") == "interest_query"
    assert classify_memory_query("我的兴趣") == "interest_query"


def test_classify_none():
    """非记忆查询应返回 None"""
    from app.engine.visitor_memory_query import classify_memory_query
    assert classify_memory_query("开灯") is None
    assert classify_memory_query("带我参观大岛台") is None
    assert classify_memory_query("你好") is None
    assert classify_memory_query("") is None
    assert classify_memory_query("机械臂什么原理") is None


# ============================================================
# 未绑定访客的回答
# ============================================================
def test_unknown_visitor_identity():
    from app.engine.visitor_memory_query import answer_memory_query
    result = asyncio.run(
        answer_memory_query("identity_check", None)
    )
    assert result
    assert "录" in result or "见面" in result or "认识" in result


def test_unknown_visitor_name():
    from app.engine.visitor_memory_query import answer_memory_query
    result = asyncio.run(
        answer_memory_query("ask_name", None)
    )
    assert "告诉" in result or "名字" in result


def test_unknown_visitor_visit_count():
    from app.engine.visitor_memory_query import answer_memory_query
    result = asyncio.run(
        answer_memory_query("visit_count", None)
    )
    assert "第一次" in result


# ============================================================
# 已绑定访客的回答（轻量，不查 DB）
# ============================================================
def test_known_visitor_identity_returning():
    from app.engine.visitor_memory_query import answer_memory_query
    visitor = {"id": 1, "name": "张三", "display_name": "张三", "visit_count": 3}
    result = asyncio.run(
        answer_memory_query("identity_check", visitor)
    )
    assert "张三" in result
    assert "欢迎回来" in result or "认识" in result


def test_known_visitor_identity_first_time():
    from app.engine.visitor_memory_query import answer_memory_query
    visitor = {"id": 1, "name": "李四", "display_name": "李四", "visit_count": 1}
    result = asyncio.run(
        answer_memory_query("identity_check", visitor)
    )
    assert "李四" in result


def test_known_visitor_ask_name():
    from app.engine.visitor_memory_query import answer_memory_query
    visitor = {"id": 1, "name": "王五", "display_name": "王五", "visit_count": 2}
    result = asyncio.run(
        answer_memory_query("ask_name", visitor)
    )
    assert "王五" in result


def test_known_visitor_visit_count():
    from app.engine.visitor_memory_query import answer_memory_query
    visitor = {"id": 1, "name": "张三", "display_name": "张三", "visit_count": 5}
    result = asyncio.run(
        answer_memory_query("visit_count", visitor)
    )
    assert "5" in result or "五" in result
    assert "张三" in result


# ============================================================
# Followup 响应分类
# ============================================================
def test_followup_yes():
    from app.engine.visitor_memory_query import classify_followup_response
    assert classify_followup_response("要") == "yes"
    assert classify_followup_response("好") == "yes"
    assert classify_followup_response("好的") == "yes"
    assert classify_followup_response("继续") == "yes"
    assert classify_followup_response("嗯") == "yes"
    assert classify_followup_response("要继续") == "yes"
    assert classify_followup_response("想看这个") == "yes"
    assert classify_followup_response("就看这个") == "yes"


def test_followup_no():
    from app.engine.visitor_memory_query import classify_followup_response
    assert classify_followup_response("不") == "no"
    assert classify_followup_response("不要") == "no"
    assert classify_followup_response("不用") == "no"
    assert classify_followup_response("换个") == "no"
    assert classify_followup_response("换别的") == "no"
    assert classify_followup_response("看别的") == "no"
    assert classify_followup_response("不感兴趣") == "no"


def test_followup_other():
    from app.engine.visitor_memory_query import classify_followup_response
    # 明确其他意图
    assert classify_followup_response("开灯") == "other"
    assert classify_followup_response("带我去机械臂") == "other"
    assert classify_followup_response("") == "other"
    assert classify_followup_response("今天天气不错") == "other"


# ============================================================
# Followup 在 answer_memory_query_ex 中的产出
# ============================================================
def test_recent_exhibit_returns_followup():
    """有历史展项时 _recent_exhibit 应该返回带 followup 的 tuple"""
    from app.engine import visitor_memory_query as vmq

    class FakeDB:
        async def execute_fetchall(self, *a, **kw):
            return [{"exhibit_id": 42, "name": "大岛台", "last_at": "2026-04-15 10:00:00"}]
        async def close(self): pass

    async def fake_get_db():
        return FakeDB()
    # 直接 patch 模块内已 import 的 get_db 绑定
    original = vmq.get_db
    vmq.get_db = fake_get_db
    try:
        ans, fu = asyncio.run(
            vmq.answer_memory_query_ex("recent_exhibit", {"id":1,"name":"张三","visit_count":2})
        )
        assert "大岛台" in ans
        assert fu is not None
        assert fu["action"] == "nav_exhibit"
        assert fu["exhibit_id"] == 42
        assert fu["exhibit_name"] == "大岛台"
    finally:
        vmq.get_db = original


# ============================================================
# SmartEngine 集成点检查
# ============================================================
def test_smart_engine_has_memory_query_intercept():
    with open("app/engine/smart_engine.py", encoding="utf-8") as f:
        source = f.read()
    assert "visitor_memory_query" in source
    assert "classify_memory_query" in source
    assert "answer_memory_query" in source


def test_smart_engine_has_followup_intercept():
    with open("app/engine/smart_engine.py", encoding="utf-8") as f:
        source = f.read()
    assert "_memory_followup" in source
    assert "classify_followup_response" in source
    assert "nav_exhibit" in source


def test_register_sweeper_exists():
    from app.engine import visitor_service
    assert hasattr(visitor_service, "sweep_register_timeouts")
    assert hasattr(visitor_service, "start_register_sweeper")
    assert hasattr(visitor_service, "stop_register_sweeper")


# ============================================================
# 运行器
# ============================================================
if __name__ == "__main__":
    fails = passes = 0
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
