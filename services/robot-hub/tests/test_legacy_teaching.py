import asyncio
import json
import time
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import web

from app.engine import legacy_teaching, teaching_runtime
from app.engine.legacy_teaching import LegacyTeachingSettings, LegacyTeachingError, LegacyDraftAdapter, TeachingSession, freeze_draft_submission
from app.engine.teaching_runtime import AnalysisAdapter, TeachingRuntime, split_replay, clean_transcript, reception_id
from app.engine.smart_engine import SmartEngine
from scripts.fake_teaching_61 import Fake61


def settings(base="http://127.0.0.1:3001"):
    return LegacyTeachingSettings(
        enabled=True, draft_endpoint=base + "/api/knowledge/qa-drafts",
        bearer_token_env="TEST_TOKEN", bearer_token="fake-teaching-token",
        session_timeout_seconds=120, request_timeout_seconds=0.4, max_attempts=2,
        max_fragments=50, max_answer_chars=4000, allowed_robot_ids=("robot_001",),
        analyze_endpoint=base + "/api/knowledge/teaching/analyze", analyze_bearer_token="fake-teaching-token",
        analysis_timeout_seconds=0.15, final_analysis_timeout_seconds=0.15, replay_wait_seconds=0.03)


@pytest_asyncio.fixture
async def fake():
    service = Fake61()
    runner = web.AppRunner(service.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield service, settings(f"http://127.0.0.1:{port}")
    await runner.cleanup()


def make_engine():
    e = SmartEngine.__new__(SmartEngine)
    e.robot_id = "robot_001"
    e.conn = SimpleNamespace(send=AsyncMock(), teaching_protocol_version=2)
    e._legacy_qa_turn_seq = 0
    e._active_legacy_qa_turn_token = None
    e._speak = AsyncMock(return_value=True)
    return e


def create_runtime(monkeypatch, config):
    monkeypatch.setattr(teaching_runtime, "get_legacy_teaching_settings", lambda: config)
    monkeypatch.setattr(legacy_teaching, "get_legacy_teaching_settings", lambda: config)
    e = make_engine()
    r = e._teaching_runtime = TeachingRuntime(e, config)
    s = r.start("如何管理设备？")
    r.started(s.turn_id, True)
    return e, r, s


async def cleanup(runtime):
    runtime.clear("test")
    await asyncio.gather(*list(runtime.tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_final_asr_can_arrive_after_capture_deadline_without_losing_tail(fake, monkeypatch):
    _, config = fake
    e, r, s = create_runtime(monkeypatch, config)
    r.transcribing(s.turn_id, "deadline_120s")
    assert s.status == "TRANSCRIBING"
    fixed_deadline = s.transcribing_until
    r.transcribing(s.turn_id, "button")
    assert s.transcribing_until == fixed_deadline
    s.expires_at = time.monotonic() - 5
    full = "先保存现场画面，再通知调度人员，最后登记处理结果。"
    r.fragment(s.turn_id, 1, full)
    r.finish(s.turn_id, "deadline_120s", 1, False)
    await asyncio.sleep(.15)
    assert r.recent is s and s.answer == full
    assert s.draft_state == "SUBMITTED"
    assert full in e._speak.call_args.args[0]
    await cleanup(r)


@pytest.mark.asyncio
async def test_transcribing_cannot_extend_or_revive_expired_session(fake, monkeypatch):
    _, config = fake
    _, r, s = create_runtime(monkeypatch, config)
    r.transcribing("old-turn", "button")
    assert s.status == "COLLECTING"
    r.transcribing(s.turn_id, "button")
    s.transcribing_until = time.monotonic() - 1
    r.transcribing(s.turn_id, "button")
    r.fragment(s.turn_id, 1, "晚到结果")
    assert r.active is None and r.recent is None and s.invalid
    await cleanup(r)


@pytest.mark.asyncio
async def test_unannounced_late_transcribing_is_not_accepted(fake, monkeypatch):
    _, config = fake
    _, r, s = create_runtime(monkeypatch, config)
    s.expires_at = time.monotonic() - 3
    r.transcribing(s.turn_id, "button")
    assert s.status == "COLLECTING"
    await cleanup(r)


def test_replay_matching_and_cleaning():
    assert split_replay("现在会了吗？") == (True, "")
    assert split_replay("统一管理设备。现在会了吗？") == (True, "统一管理设备")
    assert split_replay("他问我现在会了吗")[0] is False
    assert split_replay("开灯后讲解完毕")[0] is False
    assert clean_transcript("嗯，啊。") == ""
    assert clean_transcript("阿里和额尔古纳不支持3台设备。") == "阿里和额尔古纳不支持3台设备"


def test_config_default_off_and_tokens(monkeypatch):
    assert not LegacyTeachingSettings.from_config({}).enabled
    monkeypatch.delenv("V5_LEGACY_QA_DRAFT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="环境变量"):
        LegacyTeachingSettings.from_config({"legacy_teaching": {"enabled": True,
            "draft_endpoint": "http://192.168.16.61:3001/api/knowledge/qa-drafts"}})


@pytest.mark.parametrize("port", [3001, 3481])
def test_teaching_accepts_production_and_confirmed_staging_ports(port):
    settings(f"http://192.168.16.61:{port}").validate()


@pytest.mark.parametrize("draft_port,analysis_port", [(3001, 3481), (3481, 3001)])
def test_teaching_rejects_mixed_production_and_staging_ports(draft_port, analysis_port):
    config = replace(settings(f"http://192.168.16.61:{draft_port}"),
                     analyze_endpoint=f"http://192.168.16.61:{analysis_port}/api/knowledge/teaching/analyze")
    with pytest.raises(ValueError, match="同一可信"):
        config.validate()


@pytest.mark.parametrize("name,status,reason,teachable", [
    ("qa-answered", "ANSWERED", None, False),
    ("qa-no-knowledge", "ABSTAINED", "NO_KNOWLEDGE", True),
    ("qa-staff-only", "ABSTAINED", "STAFF_ONLY", False),
])
@pytest.mark.asyncio
async def test_actual_staging_qa_responses(fake, name, status, reason, teachable):
    from app.engine.legacy_qa import LegacyQAAdapter, LegacyQASettings

    service, config = fake
    response = json.loads((Path(__file__).parent / f"fixtures/61-v1-staging/{name}.json").read_text(encoding="utf-8"))
    service.qa_response = response
    endpoint = config.draft_endpoint.replace("/api/knowledge/qa-drafts", "/api/asr-text")
    adapter = LegacyQAAdapter(LegacyQASettings("active", endpoint, "2", "microphone", 1,
                                             robot_contract_enabled=True))
    try:
        result = await adapter.ask("云猴是什么？", require_structured=True,
                                   robot_id="robot_001", session_id="fixture-replay")
        assert result.answer_status == status
        assert result.reply == response["reply"]
        assert result.reason == reason and result.teachable is teachable
        assert service.qa_calls[-1]["suppressTts"] is True
    finally:
        await adapter.close()


@pytest.mark.parametrize("name,can_replay", [("analysis-probe-01", False),
                                            ("analysis-diagnostic-singleline", True)])
@pytest.mark.asyncio
async def test_actual_staging_analysis_response_and_correlation(fake, name, can_replay):
    service, config = fake
    record = json.loads((Path(__file__).parent / f"fixtures/61-v1-staging/{name}.json").read_text(encoding="utf-8"))
    service.analysis_overrides = record["response"]
    adapter = AnalysisAdapter(config)
    result = await adapter.analyze(record["request"])
    assert result["factuality"] == "UNVERIFIED" and result["canReplay"] is can_replay
    # The captured response must be rejected for a different teaching turn.
    with pytest.raises(LegacyTeachingError) as caught:
        await adapter.analyze({**record["request"], "teachingTurnId": "another-turn"})
    assert caught.value.code == "analysis_correlation"


@pytest.mark.asyncio
async def test_actual_staging_analysis_http_failure_is_unavailable():
    record = json.loads((Path(__file__).parent / "fixtures/61-v1-staging/analysis-diagnostic-multiline.json").read_text(encoding="utf-8"))

    async def handler(request):
        return web.json_response(record["response"], status=record["httpStatus"])

    app = web.Application()
    app.router.add_post("/api/knowledge/teaching/analyze", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        port = site._server.sockets[0].getsockname()[1]
        with pytest.raises(LegacyTeachingError) as caught:
            await AnalysisAdapter(settings(f"http://127.0.0.1:{port}")).analyze(record["request"])
        assert caught.value.code == "analysis_http"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("name", ["draft-create-01", "draft-replay-01", "draft-conflict-01"])
@pytest.mark.asyncio
async def test_actual_staging_draft_responses(fake, name):
    from app.engine.legacy_teaching import FrozenDraftSubmission

    service, config = fake
    record = json.loads((Path(__file__).parent / f"fixtures/61-v1-staging/{name}.json").read_text(encoding="utf-8"))
    service.draft_errors = [(record["httpStatus"], record["response"])]
    frozen = FrozenDraftSubmission(record["request"]["submissionId"],
                                   json.dumps(record["request"], ensure_ascii=False).encode("utf-8"))
    adapter = LegacyDraftAdapter(config)
    try:
        if record["httpStatus"] == 409:
            with pytest.raises(LegacyTeachingError) as caught:
                await adapter.submit(frozen)
            assert caught.value.code == "http_409" and not caught.value.retryable
            assert len(service.bodies) == 1
        else:
            result = await adapter.submit(frozen)
            assert result["draftId"] == record["response"]["draftId"]
            assert result["submissionId"] == frozen.submission_id
            assert result["status"] == "PENDING_REVIEW"
    finally:
        await adapter.close()


def test_frozen_snapshot_optional_analysis():
    s = TeachingSession.create("问题")
    s.reception_id = "reception"
    s.terminal_reason = "button"
    s.fragments = ["第一段", "第二段"]
    frozen = freeze_draft_submission(s, "robot_001")
    body = json.loads(frozen.body)
    assert body["rawAnswer"] == "第一段\n第二段"
    assert body["suggestedAnswer"] == "" and "analysis" not in body
    assert body["schemaVersion"] == "1.0.0"
    assert body["source"] == "EXHIBITION_ROBOT"
    assert body["teachingTurnId"] == s.turn_id
    s.fragments.append("迟到")
    assert json.loads(frozen.body)["rawAnswer"] == "第一段\n第二段"


def test_teaching_requires_confirmed_robot_contract(monkeypatch):
    monkeypatch.delenv("V5_LEGACY_TEACHING_ENABLED", raising=False)
    monkeypatch.delenv("V5_LEGACY_QA_MODE", raising=False)
    monkeypatch.setenv("V5_LEGACY_QA_DRAFT_TOKEN", "fake-teaching-token")
    monkeypatch.setenv("V5_LEGACY_TEACHING_ANALYZE_TOKEN", "fake-teaching-token")
    config = {"legacy_qa": {"mode": "active"}, "legacy_teaching": {
        "enabled": True,
        "draft_endpoint": "http://192.168.16.61:3001/api/knowledge/qa-drafts"}}
    with pytest.raises(ValueError, match="robot_contract_enabled"):
        LegacyTeachingSettings.from_config(config)
    config["legacy_qa"]["robot_contract_enabled"] = True
    assert LegacyTeachingSettings.from_config(config).enabled


@pytest.mark.asyncio
async def test_fixed_byte_retry_replay_and_conflict(fake):
    service, config = fake
    service.draft_errors = [(503, {"retryable": True})]
    adapter = LegacyDraftAdapter(config)
    s = TeachingSession.create("问题")
    s.fragments = ["原始答案"]
    frozen = freeze_draft_submission(s, "robot_001")
    try:
        one = await adapter.submit(frozen)
        two = await adapter.submit(frozen)
        assert one == two and one["status"] == "PENDING_REVIEW"
        assert len(service.drafts) == 1
        assert service.bodies == [frozen.body] * 3
        s.fragments.append("不同内容")
        with pytest.raises(LegacyTeachingError, match="409"):
            await adapter.submit(freeze_draft_submission(s, "robot_001"))
        assert len(service.bodies) == 4
    finally:
        await adapter.close()


@pytest.mark.parametrize("status,body", [(401, {}), (403, {}), (409, {}), (422, {}), (503, {"retryable": False}), (200, "invalid"), (200, {"status": "PUBLISHED", "draftId": "x"})])
@pytest.mark.asyncio
async def test_errors_do_not_retry(fake, status, body):
    service, config = fake
    service.draft_errors = [(status, body)]
    adapter = LegacyDraftAdapter(config)
    s = TeachingSession.create("问题"); s.fragments = ["答案"]
    try:
        with pytest.raises(LegacyTeachingError):
            await adapter.submit(freeze_draft_submission(s, "robot_001"))
        assert len(service.bodies) == 1
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_total_timeout(fake):
    service, config = fake
    service.draft_delay = 0.8
    adapter = LegacyDraftAdapter(config)
    s = TeachingSession.create("问题"); s.fragments = ["答案"]
    start = time.monotonic()
    try:
        with pytest.raises(LegacyTeachingError):
            await adapter.submit(freeze_draft_submission(s, "robot_001"))
        assert time.monotonic() - start < 0.65
        assert len(service.bodies) == 1
    finally:
        await adapter.close()


@pytest.mark.parametrize("reason", ["button", "silence_10s", "deadline_120s", "replay_query"])
@pytest.mark.asyncio
async def test_all_finishes_once_and_automatic_readback(fake, monkeypatch, reason):
    service, config = fake
    e, r, s = create_runtime(monkeypatch, config)
    try:
        await e._handle_legacy_teaching_speech("先开灯，再导航", s.turn_id, 1)
        r.fragment(s.turn_id, 2, "播放功能也支持")
        r.finish(s.turn_id, reason, 2, reason == "replay_query")
        r.finish(s.turn_id, reason, 2, reason == "replay_query")
        assert r.active is None
        await asyncio.sleep(0.1)
        assert len(service.drafts) == 1
        assert s.answer == "先开灯，再导航\n播放功能也支持"
        assert e._speak.await_count == 1
        assert not service.analysis_calls
        assert r.memory() is s
    finally: await cleanup(r)


@pytest.mark.asyncio
async def test_slow_network_releases_capture_replay_falls_back_without_resubmit(fake, monkeypatch):
    service, config = fake
    service.analysis_delay = 0.4; service.draft_delay = 0.2
    e, r, s = create_runtime(monkeypatch, config)
    try:
        r.fragment(s.turn_id, 1, "现场原文")
        start = time.monotonic()
        r.finish(s.turn_id, "button", 1)
        assert time.monotonic() - start < 0.05 and r.active is None
        await r.replay()
        assert "现场原文" in e._speak.await_args.args[0]
        assert "已提交" not in e._speak.await_args.args[0]
        await r.replay()
        await asyncio.sleep(0.25)
        # Explicit replays supersede the not-yet-started automatic readback.
        assert len(service.bodies) == 1 and e._speak.await_count == 2
    finally: await cleanup(r)


@pytest.mark.parametrize("bad", [{"canReplay": False}, {"factuality": "CONFLICTING"}, {"warnings": ["sensitive"]}])
@pytest.mark.asyncio
async def test_analysis_judgments_do_not_block_staff_readback(fake, monkeypatch, bad):
    service, config = fake; service.analysis_overrides = bad
    e, r, s = create_runtime(monkeypatch, config)
    try:
        r.fragment(s.turn_id, 1, "未经证实的内容")
        r.finish(s.turn_id, "button", 1)
        s.risk = True
        s.analysis = {"canReplay": False, "relevance": "UNRELATED", **bad}
        s.analysis_revision = 1
        await r.replay()
        assert e._speak.await_args.args[0] == "我记下了，您刚才说的是：未经证实的内容。"
        assert not service.analysis_calls
    finally: await cleanup(r)


@pytest.mark.parametrize("text,reason,last_seq", [("嗯，啊", "button", 1), ("答案", "button", 2), ("答案", "invalid", 1)])
@pytest.mark.asyncio
async def test_empty_or_missing_fragment_never_submits(fake, monkeypatch, text, reason, last_seq):
    service, config = fake
    e, r, s = create_runtime(monkeypatch, config)
    try:
        r.fragment(s.turn_id, 1, text)
        r.finish(s.turn_id, reason, last_seq)
        await asyncio.sleep(0.03)
        assert not service.bodies and r.memory() is None and not e._speak.called
    finally: await cleanup(r)


@pytest.mark.asyncio
async def test_duplicate_conflict_limit_cancel_and_stale_turn(fake, monkeypatch):
    service, config = fake
    e, r, s = create_runtime(monkeypatch, config)
    try:
        r.fragment("wrong", 1, "无效")
        r.fragment(s.turn_id, 1, "答案")
        r.fragment(s.turn_id, 1, "答案")
        assert s.fragments == ["答案"]
        r.fragment(s.turn_id, 1, "冲突")
        assert r.active is None
        s = r.start("新问题"); r.started(s.turn_id, True)
        r.fragment(s.turn_id, 1, "字" * 4001)
        assert r.active is None and r.memory() is None
        s = r.start("第三问"); r.started(s.turn_id, True)
        r.fragment(s.turn_id, 1, "资料"); r.cancel(s.turn_id, "cancel_button")
        await asyncio.sleep(0.02)
        assert not service.bodies
    finally: await cleanup(r)


@pytest.mark.asyncio
async def test_disconnect_late_results_and_ttl(fake, monkeypatch):
    service, config = fake; service.analysis_delay = 0.05
    e, r, s = create_runtime(monkeypatch, config)
    try:
        r.fragment(s.turn_id, 1, "答案"); r.finish(s.turn_id, "button", 1)
        e.conn = SimpleNamespace(send=AsyncMock())
        await asyncio.sleep(0.08)
        assert r.memory() is None and not e._speak.called
        e.conn = r.connection
        s.memory_until = time.monotonic() - 1
        assert r.memory() is None
        assert reception_id("robot_A") != reception_id("robot_B")
        assert reception_id("robot_A", reset=True) != s.reception_id
    finally: await cleanup(r)


@pytest.mark.asyncio
async def test_protocol_capability_and_prompt_ready_gate(monkeypatch):
    config = settings()
    monkeypatch.setattr(legacy_teaching, "get_legacy_teaching_settings", lambda: config)
    monkeypatch.setattr(teaching_runtime, "get_legacy_teaching_settings", lambda: config)
    e = make_engine()
    await e._start_legacy_teaching("问题")
    r = e._teaching_runtime
    try:
        s = r.active
        assert s.status == "PROMPTING"
        assert e._speak.await_args.kwargs["extra_data"]["teaching"]["protocolVersion"] == 2
        r.started(s.turn_id, True)
        expiry = s.expires_at
        r.started(s.turn_id, True)
        assert s.expires_at == expiry
        e2 = make_engine(); e2.conn.teaching_protocol_version = 1
        await e2._start_legacy_teaching("问题")
        assert not hasattr(e2, "_teaching_runtime")
    finally: await cleanup(r)


@pytest.mark.parametrize("overrides", [{"analysisId": "wrong"}, {"revision": True}, {"canReplay": True, "replayAnswer": ""}, {"factuality": "SUPPORTED", "evidenceRefs": []}])
@pytest.mark.asyncio
async def test_analysis_contract_rejects_mismatch(fake, overrides):
    service, config = fake; service.analysis_overrides = overrides
    with pytest.raises(LegacyTeachingError):
        await AnalysisAdapter(config).analyze({"schemaVersion": "1.0.0", "analysisId": "a", "teachingTurnId": "t",
            "revision": 1, "isFinal": True, "accumulatedTranscript": "答案"})

@pytest.mark.asyncio
async def test_unrelated_fragments_are_read_in_order_without_model(fake, monkeypatch):
    service, config = fake
    e, r, s = create_runtime(monkeypatch, config)
    try:
        r.fragment(s.turn_id, 1, "嗯，今天下午参观展厅")
        r.fragment(s.turn_id, 2, "明天上午九点见")
        r.finish(s.turn_id, "button", 2)
        await asyncio.sleep(0.1)
        assert not service.analysis_calls
        assert e._speak.await_count == 1
        assert e._speak.await_args.args[0] == "我记下了，您刚才说的是：今天下午参观展厅。明天上午九点见。"
        assert s.draft_state == "SUBMITTED" and s.analysis_state == "READY"
        assert s.answer == "嗯，今天下午参观展厅\n明天上午九点见"
    finally: await cleanup(r)


@pytest.mark.asyncio
async def test_tts_guard_rechecks_after_lock(monkeypatch):
    e = make_engine()
    e._tts_lock = asyncio.Lock()
    token = e._begin_legacy_qa_turn()
    await e._tts_lock.acquire()
    task = asyncio.create_task(SmartEngine._speak(e, "旧答案", teaching_turn_token=token))
    await asyncio.sleep(0)
    e._invalidate_legacy_qa_turn("new_question")
    e._tts_lock.release()
    assert await task is False


@pytest.mark.asyncio
async def test_new_question_before_background_replay_starts_invalidates_it(fake, monkeypatch):
    service, config = fake
    e, r, s = create_runtime(monkeypatch, config)
    try:
        r.fragment(s.turn_id, 1, "答案")
        r.finish(s.turn_id, "replay_query", 1, True)
        e._invalidate_legacy_qa_turn("new_question_before_task_start")
        await asyncio.sleep(0.05)
        assert not e._speak.called
    finally: await cleanup(r)


@pytest.mark.asyncio
async def test_teaching_tcp_order_and_old_connection_cannot_cancel(fake, monkeypatch):
    from app.tcp import server
    from app.engine import smart_engine
    service, config = fake
    e, r, s = create_runtime(monkeypatch, config)
    e.conn.robot_id = e.robot_id
    monkeypatch.setitem(server._robot_connections, e.robot_id, e.conn)
    monkeypatch.setattr(smart_engine, "get_smart_engine", lambda rid: e)
    try:
        await server._handle_event(e.conn, "evt_speech_result", 1,
            {"speechMode": "TEACHING", "teachingTurnId": s.turn_id, "fragmentSeq": 1, "text": "开灯和导航都是资料"})
        await server._handle_event(e.conn, "evt_teaching_finished", 2,
            {"teachingTurnId": s.turn_id, "completionReason": "button", "lastFragmentSeq": 1, "replayRequested": False})
        old_connection = SimpleNamespace(robot_id=e.robot_id)
        await server._handle_event(old_connection, "evt_teaching_reset", 3, {})
        assert r.memory() is s
        await asyncio.sleep(0.05)
        assert len(service.drafts) == 1 and e._speak.await_count == 1
        await server._handle_event(e.conn, "evt_teaching_reset", 4, {"teachingTurnId": "older"})
        assert r.memory() is s
        await server._handle_event(e.conn, "evt_teaching_reset", 5, {"teachingTurnId": s.turn_id})
        assert r.memory() is None
    finally: await cleanup(r)


@pytest.mark.asyncio
async def test_whitelist_disable_clears_memory(fake, monkeypatch):
    service, config = fake
    e, r, s = create_runtime(monkeypatch, config)
    try:
        r.fragment(s.turn_id, 1, "答案"); r.finish(s.turn_id, "button", 1)
        monkeypatch.setattr(teaching_runtime, "get_legacy_teaching_settings", lambda: replace(config, allowed_robot_ids=()))
        assert r.memory() is None
        await asyncio.sleep(0)
        assert not e._speak.called and s.invalid
    finally: await cleanup(r)


@pytest.mark.parametrize("response,expected", [
    ({"answerStatus": "ANSWERED", "reason": None, "teachable": False, "reply": "可靠答案"}, False),
    ({"answerStatus": "ABSTAINED", "reason": "NO_KNOWLEDGE", "teachable": True, "reply": ""}, True),
    ({"answerStatus": "ABSTAINED", "reason": "NO_KNOWLEDGE", "teachable": False, "reply": ""}, False),
    ({"answerStatus": "ABSTAINED", "reason": "STAFF_ONLY", "teachable": False, "reply": ""}, False),
    ({"answerStatus": "FAILED", "reason": "SERVICE_ERROR", "teachable": False, "reply": ""}, False),
    ({"answerStatus": "ANSWERED", "reason": None, "teachable": True, "reply": "冲突"}, False),
    ({"answerStatus": "UNKNOWN", "teachable": False, "reply": ""}, False),
    ({"answerStatus": "ABSTAINED", "reason": "NO_KNOWLEDGE", "reply": ""}, False),
])
@pytest.mark.asyncio
async def test_qa_entry_and_request_identity(fake, monkeypatch, response, expected):
    from app.engine import legacy_qa
    service, config = fake
    service.qa_response = response
    adapter = legacy_qa.LegacyQAAdapter(legacy_qa.LegacyQASettings("active",
        config.draft_endpoint.replace("/api/knowledge/qa-drafts", "/api/asr-text"), "2", "microphone", 1, robot_contract_enabled=True))
    monkeypatch.setattr(legacy_qa, "get_legacy_qa_adapter", lambda: adapter)
    monkeypatch.setattr(legacy_teaching, "get_legacy_teaching_settings", lambda: config)
    try:
        result = await legacy_qa.answer_via_legacy("原始问题呢？", None, "robot_001")
        assert bool(result["extra"]["legacy_qa"].get("teaching_required")) is expected
        if not expected: assert result["answer"]
        sent = service.qa_calls[-1]
        assert sent["text"] == "原始问题呢？"
        assert sent["suppressTts"] is True and sent["clientMode"] == "ROBOT"
        assert sent["robotId"] == "robot_001" and sent["sessionId"]
    finally: await adapter.close()
