import asyncio
from dataclasses import replace

import pytest
from aiohttp import web

from app.engine import answer_engine
from app.engine.legacy_qa import (
    LegacyQAAdapter,
    LegacyQAError,
    LegacyQASettings,
)
from app.engine.smart_engine import SmartEngine


def _settings(endpoint: str) -> LegacyQASettings:
    return LegacyQASettings(
        mode="active",
        endpoint=endpoint,
        target="2",
        source="microphone",
        timeout_seconds=2,
        robot_contract_enabled=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("robot_contract_enabled", [False, True])
async def test_adapter_posts_exact_body_and_reads_only_reply(robot_contract_enabled):
    captured = {}

    async def handler(request):
        captured.update(await request.json())
        return web.json_response({
            "ok": True,
            "reply": "这是老系统回答。",
            "actions": [{"type": "show_qa", "target": "2"}],
        })

    app = web.Application()
    app.router.add_post("/api/asr-text", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    adapter = LegacyQAAdapter(replace(_settings(f"http://127.0.0.1:{port}/api/asr-text"),
                                      robot_contract_enabled=robot_contract_enabled))
    try:
        result = await adapter.ask("云猴是什么？")
        assert result.answer_status == "ANSWERED"
        assert result.reply == "这是老系统回答。"
        assert result.structured is False
        expected = {
            "text": "云猴是什么？",
            "target": "2",
            "source": "microphone",
        }
        if robot_contract_enabled:
            expected["suppressTts"] = True
        assert captured == expected
    finally:
        await adapter.close()
        await runner.cleanup()


def test_robot_contract_requires_explicit_boolean_config(monkeypatch):
    monkeypatch.delenv("V5_LEGACY_QA_MODE", raising=False)
    assert not LegacyQASettings.from_config({}).robot_contract_enabled
    assert not LegacyQASettings.from_config({"legacy_qa": {
        "robot_contract_enabled": "true"}}).robot_contract_enabled
    assert LegacyQASettings.from_config({"legacy_qa": {
        "robot_contract_enabled": True}}).robot_contract_enabled


@pytest.mark.parametrize("port", [3001, 3481])
def test_qa_accepts_production_and_confirmed_staging_ports(port):
    _settings(f"http://192.168.16.61:{port}/api/asr-text").validate()


@pytest.mark.parametrize("endpoint", [
    "http://192.168.16.61:3482/api/asr-text",
    "http://192.168.16.61:3481/other",
    "http://8.8.8.8:3481/api/asr-text",
])
def test_staging_does_not_relax_endpoint_boundary(endpoint):
    with pytest.raises(ValueError):
        _settings(endpoint).validate()


@pytest.mark.asyncio
async def test_adapter_parses_structured_abstention():
    async def handler(_request):
        return web.json_response({
            "answerStatus": "ABSTAINED",
            "teachable": True,
            "reason": "NO_KNOWLEDGE",
            "reply": "",
            "traceId": "trace-1",
        })

    app = web.Application()
    app.router.add_post("/api/asr-text", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    adapter = LegacyQAAdapter(_settings(f"http://127.0.0.1:{port}/api/asr-text"))
    try:
        result = await adapter.ask("不知道的问题", require_structured=True)
        assert result.answer_status == "ABSTAINED"
        assert result.reason == "NO_KNOWLEDGE"
        assert result.reply == ""
        assert result.trace_id == "trace-1"
    finally:
        await adapter.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_adapter_requires_status_when_teaching_enabled():
    async def handler(_request):
        return web.json_response({"reply": "旧接口答案"})

    app = web.Application()
    app.router.add_post("/api/asr-text", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    adapter = LegacyQAAdapter(_settings(f"http://127.0.0.1:{port}/api/asr-text"))
    try:
        with pytest.raises(LegacyQAError) as caught:
            await adapter.ask("测试", require_structured=True)
        assert caught.value.code == "missing_answer_status"
    finally:
        await adapter.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_structured_status_not_natural_language_controls_abstention():
    async def handler(request):
        payload = await request.json()
        if payload["text"] == "故障":
            return web.json_response({
                "answerStatus": "FAILED",
                "teachable": False,
                "reason": "SERVICE_ERROR",
                "reply": "",
            })
        return web.json_response({
            "answerStatus": "ANSWERED",
            "teachable": False,
            "reason": None,
            "reply": "我不知道只是答案中的自然语言。",
        })

    app = web.Application()
    app.router.add_post("/api/asr-text", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    adapter = LegacyQAAdapter(_settings(f"http://127.0.0.1:{port}/api/asr-text"))
    try:
        answered = await adapter.ask("自然语言", require_structured=True)
        failed = await adapter.ask("故障", require_structured=True)
        assert answered.answer_status == "ANSWERED"
        assert failed.answer_status == "FAILED"
    finally:
        await adapter.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_adapter_rejects_missing_reply():
    async def handler(_request):
        return web.json_response({"ok": True, "actions": []})

    app = web.Application()
    app.router.add_post("/api/asr-text", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    adapter = LegacyQAAdapter(_settings(f"http://127.0.0.1:{port}/api/asr-text"))
    try:
        with pytest.raises(LegacyQAError) as caught:
            await adapter.ask("测试")
        assert caught.value.code == "missing_reply"
    finally:
        await adapter.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_answer_engine_legacy_precedes_old_qa(monkeypatch):
    calls = []

    async def fake_legacy(text, world_state, robot_id):
        calls.append((text, world_state, robot_id))
        return {
            "answer": "老系统回答",
            "source": "legacy_qa",
            "confidence": 1.0,
            "resume_policy": "none",
            "extra": {},
        }

    monkeypatch.setattr(answer_engine, "answer_via_legacy", fake_legacy)
    world_state = object()
    result = await answer_engine.answer(
        "general_knowledge",
        "被处理后的文本",
        {},
        world_state,
        "robot_001",
        legacy_text="原始ASR文本",
    )
    assert result["answer"] == "老系统回答"
    assert calls == [("原始ASR文本", world_state, "robot_001")]


def test_legacy_turn_invalid_after_interrupt():
    engine = SmartEngine.__new__(SmartEngine)
    engine.robot_id = "robot_001"
    engine._legacy_qa_turn_seq = 0
    engine._active_legacy_qa_turn_token = None
    token = engine._begin_legacy_qa_turn()
    assert engine._is_legacy_qa_turn_current(token)
    engine._invalidate_legacy_qa_turn("test")
    assert not engine._is_legacy_qa_turn_current(token)
