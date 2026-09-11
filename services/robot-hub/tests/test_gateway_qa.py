from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import httpx
import pytest

from app.engine.gateway_qa import (
    GatewayQAAdapter,
    GatewayQAError,
    GatewayQASettings,
    ResultReplayGuard,
    get_gateway_qa_mode,
)
from app.engine.query_router import TOUR_CONTROL, classify_query
from tests.fake_gateway import FakeGatewayTransport, make_final_result


def settings(**overrides) -> GatewayQASettings:
    values = {
        "mode": "active",
        "base_url": "http://127.0.0.1:8780",
        "bearer_token_env": "V5_GATEWAY_CLIENT_TOKEN",
        "bearer_token": "a" * 32,
        "tenant_id": "tenant-001",
        "site_id": "site-001",
        "knowledge_base_id": "kb-001",
        "locale": "zh-CN",
        "web_enabled": False,
        "request_timeout_seconds": 18,
        "max_attempts": 2,
        "target_screen_id": "screen-site-default",
        "default_current_unit_id": "unit-site-default",
        "target_screen_by_robot": {"robot-001": "screen-001"},
        "default_current_unit_by_robot": {"robot-001": "unit-default"},
    }
    values.update(overrides)
    return GatewayQASettings(**values)


def test_config_only_allows_8780_lan_and_env_token():
    settings().validate()
    with pytest.raises(ValueError):
        settings(base_url="http://127.0.0.1:8765").validate()
    with pytest.raises(ValueError):
        settings(base_url="http://127.0.0.1:8780/v1/input-events").validate()
    with pytest.raises(ValueError):
        settings(base_url="https://127.0.0.1:8780").validate()
    with pytest.raises(ValueError):
        settings(bearer_token="short").validate()
    with pytest.raises(ValueError):
        settings(target_screen_by_robot={"robot-001": "现场提供"}).validate()


def test_deployment_environment_can_enable_gateway_without_editing_yaml():
    config = {
        "gateway_qa": {
            "mode": "off",
            "base_url": "http://192.168.22.231:8780",
            "bearer_token_env": "V5_GATEWAY_CLIENT_TOKEN",
            "tenant_id": "",
            "site_id": "",
            "knowledge_base_id": "",
            "target_screen_id": "",
            "default_current_unit_id": "",
        }
    }
    resolved = GatewayQASettings.from_config(
        config,
        environ={
            "V5_GATEWAY_QA_MODE": "active",
            "V5_GATEWAY_CLIENT_TOKEN": "a" * 32,
            "V5_GATEWAY_TENANT_ID": "tenant-live",
            "V5_GATEWAY_SITE_ID": "site-live",
            "V5_GATEWAY_KNOWLEDGE_BASE_ID": "kb-live",
            "V5_GATEWAY_TARGET_SCREEN_ID": "screen-live",
            "V5_GATEWAY_DEFAULT_CURRENT_UNIT_ID": "unit-live",
        },
    )
    assert resolved.mode == "active"
    assert resolved.base_url == "http://192.168.22.231:8780"
    assert resolved.tenant_id == "tenant-live"
    assert resolved.context_for_robot("robot-001") == ("screen-live", "unit-live")
    assert get_gateway_qa_mode(
        config, environ={"V5_GATEWAY_QA_MODE": "active"}
    ) == "active"


@pytest.mark.parametrize(
    "text",
    [
        "开灯",
        "开灯吧",
        "帮我开一下灯",
        "麻烦把灯打开",
        "关灯呢",
        "把灯关了吧",
    ],
)
def test_lighting_control_phrases_never_route_to_gateway(text):
    assert classify_query(text)["qtype"] == TOUR_CONTROL


def test_site_context_defaults_apply_to_any_connected_robot():
    cfg = settings(
        target_screen_by_robot={},
        default_current_unit_by_robot={},
    )
    assert cfg.context_for_robot("robot-live-123") == (
        "screen-site-default",
        "unit-site-default",
    )


@pytest.mark.asyncio
async def test_input_event_exact_mapping_uses_original_asr_and_no_session():
    adapter = GatewayQAAdapter(
        settings(),
        transport=FakeGatewayTransport(),
        now_factory=lambda: datetime(2026, 8, 24, 6, 0, tzinfo=timezone.utc),
    )
    try:
        event, payload = adapter.build_input_event("开灯吧？", "robot-001", current_unit_id="unit-live")
        decoded = json.loads(payload.decode("utf-8"))
        assert decoded == event
        assert event["text"] == "开灯吧？"
        assert event["currentUnitId"] == "unit-live"
        assert event["targetScreenId"] == "screen-001"
        assert event["knowledgeBaseIds"] == ["kb-001"]
        assert event["source"]["type"] == "EXHIBITION_ROBOT"
        assert event["metadata"] == {"eventName": "ASR_FINAL"}
        assert event["visitorProfile"]["role"] == "GENERAL"
        assert event["visitorProfile"]["visitorId"] is None
        assert "sessionId" not in event
        assert len(payload) <= 1024 * 1024
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_retry_reuses_identical_request_bytes_and_identity():
    transport = FakeGatewayTransport([httpx.ConnectError("first connection failed")])
    adapter = GatewayQAAdapter(settings(), transport=transport)
    try:
        response = await adapter.query("云猴是什么？", "robot-001", current_unit_id=None)
        assert response.answer_text == "这是来自 Core 的答案。"
        assert response.diagnostics["attempts"] == 2
        assert len(transport.calls) == 2
        assert transport.calls[0]["body"] == transport.calls[1]["body"]
        first = transport.calls[0]["event"]
        second = transport.calls[1]["event"]
        assert first["requestId"] == second["requestId"]
        assert first["eventId"] == second["eventId"]
        assert first["createdAt"] == second["createdAt"]
    finally:
        await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 409, 413, 415, 422])
async def test_non_retryable_http_statuses_are_attempted_once(status):
    transport = FakeGatewayTransport([
        httpx.Response(status, json={"retryable": False, "error": "rejected"})
    ])
    adapter = GatewayQAAdapter(settings(), transport=transport)
    try:
        with pytest.raises(GatewayQAError) as caught:
            await adapter.query("问题", "robot-001", current_unit_id=None)
        assert caught.value.retryable is False
        assert len(transport.calls) == 1
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_retryable_503_is_retried_with_same_body():
    transport = FakeGatewayTransport([
        httpx.Response(503, json={"retryable": True}),
    ])
    adapter = GatewayQAAdapter(settings(), transport=transport)
    try:
        await adapter.query("问题", "robot-001", current_unit_id=None)
        assert len(transport.calls) == 2
        assert transport.calls[0]["body"] == transport.calls[1]["body"]
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_total_deadline_is_a_hard_limit():
    async def slow_response(request, event, body):
        await asyncio.sleep(1)
        return httpx.Response(200, request=request, json=make_final_result(event))

    class SlowTransport(FakeGatewayTransport):
        async def handle_async_request(self, request):
            body = await request.aread()
            event = json.loads(body.decode("utf-8"))
            self.calls.append({"body": body, "event": event, "url": str(request.url)})
            return await slow_response(request, event, body)

    transport = SlowTransport()
    adapter = GatewayQAAdapter(
        settings(request_timeout_seconds=0.05, max_attempts=2), transport=transport
    )
    started = time.monotonic()
    try:
        with pytest.raises(GatewayQAError) as caught:
            await adapter.query("问题", "robot-001", current_unit_id=None)
        assert caught.value.code == "timeout"
        assert time.monotonic() - started < 0.5
        assert len(transport.calls) == 1
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_final_result_identity_mismatch_never_retries():
    def mismatch(request, event, body):
        return httpx.Response(
            200, request=request, json=make_final_result(event, tenantId="wrong-tenant")
        )

    transport = FakeGatewayTransport([mismatch])
    adapter = GatewayQAAdapter(settings(), transport=transport)
    try:
        with pytest.raises(GatewayQAError) as caught:
            await adapter.query("问题", "robot-001", current_unit_id=None)
        assert caught.value.code == "result_identity_mismatch"
        assert len(transport.calls) == 1
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_output_commands_are_validated_but_answer_text_is_only_spoken_payload():
    adapter = GatewayQAAdapter(settings(), transport=FakeGatewayTransport())
    try:
        response = await adapter.query("问题", "robot-001", current_unit_id=None)
        assert response.answer_text == "这是来自 Core 的答案。"
        assert "不得由 V5 执行" not in response.answer_text
    finally:
        await adapter.close()


def test_replay_guard_is_robot_scoped_and_detects_conflict():
    guard = ResultReplayGuard(capacity=2, ttl_seconds=60)
    assert guard.register("robot-001", "request-1", "result-1") == "new"
    assert guard.register("robot-001", "request-1", "result-1") == "pending"
    assert guard.mark_spoken("robot-001", "request-1", "result-1") is True
    assert guard.register("robot-001", "request-1", "result-1") == "duplicate"
    assert guard.register("robot-001", "request-1", "result-2") == "conflict"
    assert guard.register("robot-002", "request-1", "result-2") == "new"


@pytest.mark.asyncio
async def test_blocked_and_command_completed_never_execute_commands():
    for status in ("BLOCKED", "FAILED", "COMMAND_COMPLETED"):
        def response_factory(request, event, body, final_status=status):
            return httpx.Response(
                200,
                request=request,
                json=make_final_result(event, status=final_status, answerText="危险指令"),
            )

        adapter = GatewayQAAdapter(settings(), transport=FakeGatewayTransport([response_factory]))
        try:
            response = await adapter.query("问题", "robot-001", current_unit_id=None)
            assert response.answer_text == "知识服务暂时不可用，请稍后再试。"
        finally:
            await adapter.close()


@pytest.mark.asyncio
async def test_answer_engine_gateway_result_precedes_openclaw_and_retrievers(monkeypatch):
    from app.engine import answer_engine

    observed = {}

    async def fake_gateway(text, world_state, robot_id):
        observed["text"] = text
        return {
            "answer": "Gateway answer",
            "source": "gateway_qa",
            "confidence": 1.0,
            "resume_policy": "none",
            "action": None,
            "extra": {"gateway": {"should_speak": True}},
        }

    async def forbidden(*args, **kwargs):
        raise AssertionError("active Gateway 后不应调用 OpenClaw/旧 retriever")

    monkeypatch.setattr(answer_engine, "answer_via_gateway", fake_gateway)
    monkeypatch.setattr(answer_engine, "answer_via_legacy", forbidden)
    monkeypatch.setattr(answer_engine.decision_bridge, "should_handoff", forbidden)
    monkeypatch.setattr(answer_engine.hall_knowledge_retriever, "retrieve", forbidden)

    result = await answer_engine.answer(
        "hall_knowledge",
        "它是什么？（内部已改写）",
        {},
        object(),
        "robot-001",
        gateway_text="它是什么？",
    )
    assert result["answer"] == "Gateway answer"
    assert result["source"] == "gateway_qa"
    assert observed["text"] == "它是什么？"


@pytest.mark.asyncio
async def test_tour_control_bypasses_gateway_adapter(monkeypatch):
    from app.engine import answer_engine

    async def forbidden(*args, **kwargs):
        raise AssertionError("TOUR_CONTROL 不得调用 InputGateway")

    monkeypatch.setattr(answer_engine, "answer_via_gateway", forbidden)
    result = await answer_engine.answer(
        TOUR_CONTROL,
        "帮我开一下灯",
        {},
        object(),
        "robot-001",
        gateway_text="帮我开一下灯",
    )
    assert result["source"] == "tour_control_passthrough"


def test_gateway_turn_token_becomes_invalid_after_interrupt():
    from app.engine.smart_engine import SmartEngine

    engine = SmartEngine.__new__(SmartEngine)
    engine.robot_id = "robot-001"
    engine._gateway_turn_seq = 0
    engine._active_gateway_turn_token = None

    token = engine._begin_gateway_turn()
    assert engine._is_gateway_turn_current(token) is True
    engine._invalidate_gateway_turn("hard_cancel:test")
    assert engine._is_gateway_turn_current(token) is False
