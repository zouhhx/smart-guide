import asyncio
import importlib
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for module_name in ("aiohttp", "aiosqlite", "pypinyin", "yaml"):
    try:
        importlib.import_module(module_name)
    except ImportError:
        module = types.ModuleType(module_name)
        for attribute in (
            "ClientSession", "ClientTimeout", "connect", "Connection", "lazy_pinyin",
            "safe_load",
        ):
            setattr(module, attribute, lambda *args, **kwargs: "")
        sys.modules[module_name] = module

from app.engine.smart_engine import SmartEngine, _strip_terminal_speech_punctuation


class FakeWorldState:
    def __init__(self, mode="list_exhibits"):
        self.recent = SimpleNamespace(last_input_time=0)
        self.browse = SimpleNamespace(
            mode=mode,
            exhibits_cache=[],
            selected_exhibit_name="",
        )
        self.dialog = SimpleNamespace(engine_state="IDLE")
        self.reset_count = 0

    def reset_browse(self):
        self.reset_count += 1
        self.browse.mode = ""


def test_hard_cancel_also_exits_browse_mode():
    engine = SmartEngine.__new__(SmartEngine)
    engine.robot_id = "robot_001"
    engine.ws = FakeWorldState()
    engine._handle_lock = asyncio.Lock()
    engine._queued_asr = None
    engine._queued_asr_raw = None
    engine._tts_cancel = asyncio.Event()
    engine._invalidate_gateway_turn = lambda reason="": None
    engine._invalidate_legacy_qa_turn = lambda reason="": None
    engine._interrupt_current_activity = AsyncMock(return_value=False)
    engine._speak = AsyncMock()
    engine._log_turn = lambda *args, **kwargs: None

    asyncio.run(engine.handle_speech("停下。"))

    assert engine.ws.reset_count == 1
    assert engine.ws.browse.mode == ""


def test_punctuated_exit_phrase_exits_browse_mode():
    engine = SmartEngine.__new__(SmartEngine)
    engine.ws = FakeWorldState()
    engine._end_tour = AsyncMock()

    assert _strip_terminal_speech_punctuation("不用了。") == "不用了"
    handled = asyncio.run(
        engine._handle_browse_input(
            "navigate_to",
            {"target": "不用了。"},
            "不用了。",
            "",
        )
    )

    assert handled is True
    assert engine.ws.reset_count == 1
    engine._end_tour.assert_awaited_once()


def test_unknown_text_in_exhibit_list_falls_through_to_qa():
    engine = SmartEngine.__new__(SmartEngine)
    engine.ws = FakeWorldState()
    engine.executor = SimpleNamespace(find_exhibit_by_name=AsyncMock(return_value=None))
    engine._speak = AsyncMock()

    handled = asyncio.run(
        engine._handle_browse_input(
            "navigate_to",
            {"target": "支持哪些类型的智能服务？"},
            "支持哪些类型的智能服务？",
            "",
        )
    )

    assert handled is False
    engine._speak.assert_not_awaited()


if __name__ == "__main__":
    test_hard_cancel_also_exits_browse_mode()
    test_punctuated_exit_phrase_exits_browse_mode()
    test_unknown_text_in_exhibit_list_falls_through_to_qa()
    print("3 passed")
