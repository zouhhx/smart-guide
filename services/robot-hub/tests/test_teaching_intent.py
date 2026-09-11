import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine import teaching_intent
from app.engine.teaching_intent import classify_followup, FollowupIntent
from app.engine.teaching_runtime import TeachingRuntime
from app.engine.smart_engine import SmartEngine


@pytest.mark.asyncio
@pytest.mark.parametrize('text',[
    '现在会了吗？', '学会了吗', '你学会了吗？', '你现在学会了吗', '您记住了没有？',
    '你再复述一下。', '再说一遍', '请你再讲一下', '把我刚才教你的讲一下',
])
async def test_common_natural_requests_do_not_wait_for_model(text):
    ask=AsyncMock(side_effect=AssertionError('fast expression must not call model'))
    assert (await classify_followup(text,'问题',has_memory=True,ask=ask)).intent=='REPLAY'
    ask.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('text,intent',[
    ('来，考考你，刚刚那套步骤还能完整讲出来不？','REPLAY'),
    ('我刚说的你还记得多少？','REPLAY'),
    ('现在会了吗？这道菜。','REPLAY'),
    ('已经审核发布了吗？','STATUS'),
    ('他问我学会了吗是什么意思？','OTHER'),
    ('不要复述了','OTHER'),
    ('你学会了吗？今天南京天气怎么样？','UNCERTAIN'),
])
async def test_contextual_classifier_decides_beyond_fixed_phrases(text,intent):
    ask=AsyncMock(return_value=json.dumps({'intent':intent}))
    result=await classify_followup(text,'现场处理步骤是什么',has_memory=True,ask=ask)
    assert result==FollowupIntent(intent,'semantic')
    data=json.loads(ask.call_args.kwargs['messages'][0]['content'])
    assert data['utterance']==text
    assert data['teaching_question']=='现场处理步骤是什么'


@pytest.mark.asyncio
async def test_unrelated_business_question_does_not_add_model_latency():
    ask=AsyncMock()
    result=await classify_followup('智慧家庭的应用场景有哪些？','设备操作',has_memory=True,ask=ask)
    assert result.intent=='OTHER'; ask.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('output',['', 'not json', '{"intent":"EXECUTE_COMMAND"}', '[]'])
async def test_invalid_classifier_output_cannot_escape_to_external_qa(output):
    result=await classify_followup('我刚说的你还记得多少？','问题',has_memory=True,ask=AsyncMock(return_value=output))
    assert result==FollowupIntent('UNCERTAIN','unavailable')


@pytest.mark.asyncio
async def test_classifier_timeout_is_bounded():
    async def slow(**kwargs): await asyncio.sleep(5)
    started=time.monotonic()
    result=await classify_followup('我刚说的你还记得多少？','问题',has_memory=True,ask=slow,timeout=.02)
    assert result.intent=='UNCERTAIN' and time.monotonic()-started < .3


@pytest.mark.asyncio
async def test_first_connection_can_complete_after_old_deadline():
    async def cold_connection(**kwargs):
        await asyncio.sleep(2.1)
        return '{"intent":"REPLAY"}'
    result=await classify_followup('我刚说的你还记得多少？','问题',has_memory=True,ask=cold_connection)
    assert result==FollowupIntent('REPLAY','semantic')


@pytest.mark.asyncio
async def test_failure_logs_type_without_credentials_or_utterance(caplog):
    result=await classify_followup('我刚说的你还记得多少？','问题',has_memory=True,
        ask=AsyncMock(side_effect=RuntimeError('SECRET credential and private utterance')))
    assert result==FollowupIntent('UNCERTAIN','unavailable')
    assert 'error_type=RuntimeError' in caplog.text
    assert 'SECRET' not in caplog.text and '我刚说的' not in caplog.text


def runtime():
    engine=SmartEngine.__new__(SmartEngine)
    engine.robot_id='test'
    engine.conn=SimpleNamespace()
    engine._legacy_qa_turn_seq=0; engine._active_legacy_qa_turn_token=None
    engine._speak=AsyncMock()
    r=TeachingRuntime.__new__(TeachingRuntime)
    r.engine=engine; r.connection=engine.conn; r.active=None
    r.recent=SimpleNamespace(question='原问题',draft_state='SUBMITTED')
    r.memory=lambda:r.recent
    r.replay=AsyncMock(return_value=True)
    engine._teaching_runtime=r
    return engine,r


@pytest.mark.asyncio
async def test_replay_uses_runtime_not_generated_answer(monkeypatch):
    e,r=runtime()
    assert await r.handle_followup('你再复述一下')
    assert r.replay.call_args.args[0] is r.recent
    e._speak.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_memory_is_handled_locally():
    e,r=runtime(); r.recent=None
    assert await r.handle_followup('你学会了吗')
    r.replay.assert_not_awaited()
    assert '没有可复述' in e._speak.call_args.args[0]


@pytest.mark.asyncio
async def test_status_does_not_claim_published(monkeypatch):
    e,r=runtime()
    monkeypatch.setattr(teaching_intent,'classify_followup',AsyncMock(return_value=FollowupIntent('STATUS','semantic')))
    assert await r.handle_followup('已经发布了吗')
    text=e._speak.call_args.args[0]
    assert '以后台为准' in text and '已提交审核' in text
    r.replay.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_semantic_result_cannot_answer_new_turn(monkeypatch):
    e,r=runtime()
    async def classify(*args,**kwargs):
        e._invalidate_legacy_qa_turn('new_speech')
        return FollowupIntent('REPLAY','semantic')
    monkeypatch.setattr(teaching_intent,'classify_followup',classify)
    assert await r.handle_followup('刚才我是怎么说的')
    r.replay.assert_not_awaited(); e._speak.assert_not_awaited()


@pytest.mark.asyncio
async def test_followup_is_before_general_router(monkeypatch):
    e,r=runtime(); e._check_health=AsyncMock()
    monkeypatch.setattr('app.engine.smart_engine._load_trigger_phrases',AsyncMock(side_effect=AssertionError('must not reach general route')))
    await e._handle_speech_impl('你再复述一下')
    r.replay.assert_awaited_once()


@pytest.mark.asyncio
async def test_learning_confirmation_after_restart_does_not_go_to_61(monkeypatch):
    e,_=runtime(); e._teaching_runtime=None; e._check_health=AsyncMock()
    monkeypatch.setattr('app.engine.smart_engine._load_trigger_phrases',AsyncMock(side_effect=AssertionError('must not reach general route')))
    await e._handle_speech_impl('你学会了吗？')
    assert '还没有可复述' in e._speak.call_args.args[0]


@pytest.mark.asyncio
async def test_stale_runtime_after_reconnect_must_not_swallow_new_question():
    e,r=runtime(); e.conn=SimpleNamespace(); r.recent=None
    assert not await r.handle_followup('这个颜色还可以吗？')


def test_reconnect_detaches_old_teaching_runtime(monkeypatch):
    from app.engine import smart_engine
    e,r=runtime(); r.clear=lambda reason:None
    monkeypatch.setitem(smart_engine._engines,'reconnect-regression',e)
    connection=SimpleNamespace()
    assert smart_engine.create_smart_engine('reconnect-regression',connection) is e
    assert e.conn is connection
    assert e._teaching_runtime is None
