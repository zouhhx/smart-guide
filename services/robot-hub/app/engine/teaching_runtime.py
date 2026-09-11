"""教学 v2：每个机器人一份短期记录，所有网络工作在受管理的后台任务中。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import OrderedDict

import aiohttp

from app.engine.legacy_teaching import (
    LegacyDraftAdapter, LegacyTeachingError, TeachingSession, freeze_draft_submission,
    get_legacy_teaching_settings,
)

log = logging.getLogger("hub.engine.teaching")
REPLAY_PHRASES = {"现在会了吗", "学会了吗", "记住了吗", "现在知道了吗", "能回答刚才的问题了吗"}
NORMAL_ENDINGS = {"button", "silence_10s", "deadline_120s", "replay_query"}
PUNCT = " 。.！!？?，,；;：:\t\r\n"
_receptions = OrderedDict()
_runtimes = OrderedDict()


def reception_id(robot_id, reset=False):
    now = time.monotonic()
    old = _receptions.pop(robot_id, None)
    value = old[0] if old and now - old[1] < 600 and not reset else str(uuid.uuid4())
    _receptions[robot_id] = (value, now)
    while len(_receptions) > 128:
        _receptions.popitem(last=False)
    return value


def split_replay(text):
    """只接受整句或明确标点分隔的末尾问句；引述不匹配。"""
    clean = str(text or "").strip().rstrip(PUNCT)
    if clean in REPLAY_PHRASES:
        return True, ""
    for phrase in REPLAY_PHRASES:
        if clean.endswith(phrase):
            prefix = clean[:-len(phrase)]
            if prefix and prefix[-1] in "。！？!?；;\n":
                return True, prefix.rstrip(PUNCT)
    return False, str(text or "").strip()


def clean_transcript(text):
    parts = re.split(r"([，,。.!！?？；;\n])", text.strip())
    fillers = {"嗯", "啊", "额", "呃", "哦", "嗯嗯"}
    return "".join(p if p.strip() not in fillers else "" for p in parts).strip(PUNCT)


async def close_teaching_runtimes():
    tasks = set()
    for runtime in list(_runtimes.values()):
        tasks.update(runtime.tasks)
        runtime.clear("service_shutdown")
        tasks.update(runtime.tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _receptions.clear()


class AnalysisAdapter:
    def __init__(self, settings):
        self.settings = settings

    async def analyze(self, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > 1024 * 1024:
            raise LegacyTeachingError("request_too_large", "分析请求过大")
        timeout = self.settings.final_analysis_timeout_seconds if payload["isFinal"] else self.settings.analysis_timeout_seconds
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as client:
                async with client.post(
                    self.settings.analyze_endpoint, data=body, allow_redirects=False,
                    headers={"Authorization": "Bearer " + self.settings.analyze_bearer_token,
                             "Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
                ) as response:
                    if response.status != 200:
                        raise LegacyTeachingError("analysis_http", "分析服务异常")
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw) > 1024 * 1024:
                            raise LegacyTeachingError("response_too_large", "分析响应过大")
            result = json.loads(raw)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, TimeoutError, ValueError) as exc:
            raise LegacyTeachingError("analysis_unavailable", "分析不可用") from exc
        if not isinstance(result, dict):
            raise LegacyTeachingError("analysis_contract", "分析响应不是对象")
        for key in ("schemaVersion", "analysisId", "teachingTurnId", "revision"):
            if type(result.get(key)) is not type(payload[key]) or result[key] != payload[key]:
                raise LegacyTeachingError("analysis_correlation", "分析关联错误")
        if result.get("status") == "FAILED":
            raise LegacyTeachingError("analysis_failed", "分析失败")
        if any(not isinstance(result.get(k), str) for k in ("status", "relevance", "completeness", "factuality")):
            raise LegacyTeachingError("analysis_contract", "分析枚举类型错误")
        if (result.get("status") != "OK"
                or result.get("relevance") not in {"RELATED", "PARTIAL", "UNRELATED"}
                or result.get("completeness") not in {"SUFFICIENT", "INSUFFICIENT"}
                or result.get("factuality") not in {"SUPPORTED", "CONFLICTING", "UNVERIFIED"}
                or type(result.get("canReplay")) is not bool
                or not isinstance(result.get("replayAnswer"), str)
                or len(result["replayAnswer"]) > self.settings.max_answer_chars
                or not isinstance(result.get("warnings"), list)
                or not isinstance(result.get("evidenceRefs"), list)
                or (result["canReplay"] and not result["replayAnswer"].strip())
                or (result["factuality"] == "SUPPORTED" and not result["evidenceRefs"])):
            raise LegacyTeachingError("analysis_contract", "分析字段错误")
        return result


class TeachingRuntime:
    """状态方法不 await 网络；TCP可按接收顺序直接调用这些方法。"""
    def __init__(self, engine, settings=None, analysis=None, draft=None):
        self.engine = engine
        self.settings = settings or get_legacy_teaching_settings()
        self.connection = engine.conn
        self.active = None
        self.recent = None
        self.tasks = set()
        self.process_task = None
        self.final_task = None
        self.last_analysis_at = 0.0
        self.analysis_adapter = analysis or AnalysisAdapter(self.settings)
        self.draft_adapter = draft or LegacyDraftAdapter(self.settings)

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        def done(t):
            self.tasks.discard(t)
            if not t.cancelled() and t.exception():
                log.error("teaching background robot=%s error=%s", self.engine.robot_id, type(t.exception()).__name__)
        task.add_done_callback(done)
        return task

    def valid(self, session):
        return (self.engine.conn is self.connection and not session.invalid
                and (session is self.active or session is self.recent))

    def start(self, question):
        self.clear("replaced", reset_reception=False)
        _runtimes.pop(self.engine.robot_id, None)
        _runtimes[self.engine.robot_id] = self
        while len(_runtimes) > 128:
            _, oldest = _runtimes.popitem(last=False)
            oldest.clear("capacity", reset_reception=False)
        s = TeachingSession.create(question)
        s.reception_id = reception_id(self.engine.robot_id)
        s.invalid = False
        s.sequences = {}
        s.state_revision = 0
        s.analysis_state = "PENDING"
        s.draft_state = "PENDING"
        s.draft_id = ""
        s.memory_until = 0.0
        s.risk = False
        s.final_complete = False
        self.active = s
        self.recent = None
        self.engine._legacy_teaching_session = s
        self.spawn(self.watch(s))
        return s

    async def watch(self, s):
        prompt_deadline = time.monotonic() + 45
        while self.valid(s):
            await asyncio.sleep(0.5)
            current_settings = get_legacy_teaching_settings()
            if not current_settings.enabled or self.engine.robot_id not in current_settings.allowed_robot_ids:
                self.clear("feature_disabled")
                return
            if s is self.active:
                if s.status == "PROMPTING" and time.monotonic() >= prompt_deadline:
                    self.cancel(s.turn_id, "start_timeout")
                elif s.status == "COLLECTING" and time.monotonic() >= s.expires_at + 2:
                    # Android owns exact deadline/final sequence; missing finish is a fault.
                    self.cancel(s.turn_id, "finish_timeout")
                elif s.status == "TRANSCRIBING" and time.monotonic() >= s.transcribing_until:
                    self.cancel(s.turn_id, "transcription_timeout")
            elif s.memory_until and time.monotonic() >= s.memory_until:
                self.clear("memory_expired", reset_reception=False)
                return

    def started(self, turn_id, success):
        s = self.active
        if not s or s.turn_id != turn_id or s.status != "PROMPTING":
            return
        if success is not True:
            self.cancel(turn_id, "listen_error")
            return
        s.begin_collecting(120)
        self.state(s)

    def transcribing(self, turn_id, reason):
        """Capture has ended; allow a fixed, non-renewable final-ASR delivery budget."""
        s = self.active
        if not s or s.turn_id != turn_id or not self.valid(s):
            return
        if s.status == "TRANSCRIBING":
            return
        if s.status != "COLLECTING" or reason not in NORMAL_ENDINGS or time.monotonic() > s.expires_at + 2:
            return
        s.status = "TRANSCRIBING"
        s.transcribing_until = time.monotonic() + 25
        s.transcribing_reason = reason
        self.state(s, "transcribing")

    def fragment(self, turn_id, seq, text):
        s = self.active
        if not s or s.turn_id != turn_id or s.status not in {"COLLECTING", "TRANSCRIBING"} or not self.valid(s):
            return
        if s.status == "TRANSCRIBING" and time.monotonic() >= s.transcribing_until:
            self.cancel(turn_id, "transcription_timeout")
            return
        if type(seq) is not int or seq < 1 or not isinstance(text, str):
            self.cancel(turn_id, "fragment_protocol")
            return
        if seq in s.sequences:
            if s.sequences[seq] != text:
                self.cancel(turn_id, "fragment_conflict")
            return
        if seq != len(s.sequences) + 1:
            self.cancel(turn_id, "missing_fragment")
            return
        replay, fragment = split_replay(text)
        s.sequences[seq] = text
        if fragment.strip():
            if (len(s.fragments) >= self.settings.max_fragments
                    or len(s.answer) + len(fragment) + bool(s.fragments) > self.settings.max_answer_chars):
                self.cancel(turn_id, "answer_limit")
                return
            s.fragments.append(fragment.strip())
        if replay:
            self.finish(turn_id, "replay_query", seq, True)

    def schedule_process(self, s):
        if self.process_task is None or self.process_task.done():
            self.process_task = self.spawn(self.process(s))

    async def process(self, s):
        seen = -1
        while self.valid(s) and self.active is s and seen != len(s.fragments):
            await asyncio.sleep(max(0, self.settings.analysis_min_interval_seconds - (time.monotonic() - self.last_analysis_at)))
            if not self.valid(s) or self.active is not s:
                return
            seen = len(s.fragments)
            self.last_analysis_at = time.monotonic()
            await self.analyze(s, False)

    async def analyze(self, s, final):
        started_at = time.monotonic()
        revision = len(s.fragments)
        payload = {
            "schemaVersion": "1.0.0", "analysisId": str(uuid.uuid4()),
            "teachingTurnId": s.turn_id, "revision": revision,
            "robotId": self.engine.robot_id, "sessionId": s.reception_id,
            "question": s.question, "accumulatedTranscript": s.answer,
            "fragmentCount": revision, "isFinal": final,
            "completionReason": s.terminal_reason if final else None,
        }
        try:
            result = await self.analysis_adapter.analyze(payload)
            if not self.valid(s) or (s.final_complete and not final):
                return
            if result["factuality"] == "CONFLICTING" or result.get("warnings"):
                s.risk = True
            if revision >= s.analysis_revision:
                s.analysis = result
                s.analysis_revision = revision
                s.analysis_state = "READY"
        except LegacyTeachingError as exc:
            if self.valid(s):
                s.analysis_state = "FAILED"
                log.info("teaching analysis robot=%s turn=%s code=%s", self.engine.robot_id, s.turn_id, exc.code)
        finally:
            log.info("teaching analysis turn=%s revision=%d final=%s elapsed_ms=%.1f",
                     s.turn_id, revision, final, (time.monotonic() - started_at) * 1000)
            if self.valid(s):
                if final:
                    s.final_complete = True
                self.state(s)

    def finish(self, turn_id, reason, last_seq, replay=False):
        s = self.active
        if not s or s.turn_id != turn_id or s.status not in {"COLLECTING", "TRANSCRIBING"}:
            return
        if s.status == "TRANSCRIBING" and (time.monotonic() >= s.transcribing_until
                or (reason != s.transcribing_reason and reason != "replay_query")):
            self.cancel(turn_id, "transcription_finish_invalid")
            return
        if (reason not in NORMAL_ENDINGS or type(last_seq) is not int
                or last_seq != len(s.sequences) or type(replay) is not bool
                or replay != (reason == "replay_query")):
            self.cancel(turn_id, "finish_protocol")
            return
        s.status = "COMPLETED"
        s.terminal_reason = reason
        self.active = None
        self.engine._legacy_teaching_session = None
        self.engine._last_legacy_teaching_session = s
        if self.process_task and not self.process_task.done():
            self.process_task.cancel()
        if not clean_transcript(s.answer):
            s.invalid = True
            self.state(s, "empty_answer")
            if replay:
                token = self.engine._begin_legacy_qa_turn()
                self.spawn(self.engine._speak("尚未收到有效的现场补充。", teaching_turn_token=token))
            return
        self.recent = s
        s.memory_until = time.monotonic() + self.settings.memory_ttl_seconds
        self.state(s)
        # Freeze before starting either network task. A late model response never mutates bytes.
        frozen = freeze_draft_submission(s, self.engine.robot_id)
        # Staff transcription is read back verbatim after light cleanup. No relevance
        # or answer-quality model is needed for this user-authorized workflow.
        s.analysis_state = "READY"
        s.final_complete = True
        self.spawn(self.submit(s, frozen))
        self.state(s)
        token = self.engine._begin_legacy_qa_turn()
        self.spawn(self.replay(s, token=token))

    async def submit(self, s, frozen):
        started_at = time.monotonic()
        try:
            result = await self.draft_adapter.submit(frozen)
            if self.valid(s):
                s.draft_state = "SUBMITTED"
                s.draft_id = result["draftId"]
        except LegacyTeachingError as exc:
            if self.valid(s):
                s.draft_state = "FAILED"
                s.terminal_reason = "submission_uncertain" if exc.code in {"timeout", "network_error", "invalid_json", "submission_mismatch"} else "submit_failed"
        finally:
            log.info("teaching draft turn=%s submission=%s state=%s elapsed_ms=%.1f",
                     s.turn_id, frozen.submission_id, s.draft_state, (time.monotonic() - started_at) * 1000)
            await self.draft_adapter.close()
            if self.valid(s):
                self.state(s)

    def state(self, s, reason=""):
        s.state_revision += 1
        data = {"turnId": s.turn_id, "stateRevision": s.state_revision,
                "captureState": s.status, "analysisState": s.analysis_state,
                "draftState": s.draft_state, "reason": reason or s.terminal_reason}
        if s.draft_id:
            data["draftId"] = s.draft_id
        async def send():
            if self.engine.conn is self.connection and self.connection:
                await self.connection.send({"type": "cmd_teaching_state", "data": data})
        self.spawn(send())

    def cancel(self, turn_id, reason):
        s = self.active
        if s and (not turn_id or s.turn_id == turn_id):
            self.clear(reason, reset_reception=False)

    def clear(self, reason, reset_reception=True):
        if _runtimes.get(self.engine.robot_id) is self:
            _runtimes.pop(self.engine.robot_id, None)
        for task in list(self.tasks):
            if task is not asyncio.current_task():
                task.cancel()
        self.process_task = None
        self.final_task = None
        for s in (self.active, self.recent):
            if s:
                s.invalid = True
                s.status = "CANCELLED"
                self.state(s, reason)
        self.active = self.recent = None
        self.engine._legacy_teaching_session = None
        if reset_reception:
            reception_id(self.engine.robot_id, reset=True)
        self.spawn(self.draft_adapter.close())

    def memory(self):
        config = get_legacy_teaching_settings()
        if not config.enabled or self.engine.robot_id not in config.allowed_robot_ids:
            self.clear("feature_disabled")
            return None
        s = self.recent
        if (s and self.valid(s) and time.monotonic() < s.memory_until
                and s.reception_id == reception_id(self.engine.robot_id)):
            return s
        return None

    async def handle_followup(self, text):
        """Before ordinary QA: classify intent, but replay only the stored transcript."""
        from app.engine.teaching_intent import classify_followup
        # An already-obsolete runtime must not claim a new connection's ordinary question.
        # Connection changes during the await below still discard that older in-flight turn.
        if self.engine.conn is not self.connection:
            return False
        if self.active is not None:
            return False
        session = self.memory()
        snapshot = self.recent
        token = self.engine._begin_legacy_qa_turn()
        result = await classify_followup(text, session.question if session else "", has_memory=session is not None)
        if (not self.engine._is_legacy_qa_turn_current(token) or self.recent is not snapshot
                or self.engine.conn is not self.connection):
            return True
        if result.intent == "OTHER":
            return False
        log.info("teaching followup robot=%s intent=%s source=%s has_memory=%s",
                 self.engine.robot_id, result.intent, result.source, session is not None)
        if session is not None and self.memory() is not session:
            session = None
        if result.intent == "UNCERTAIN":
            await self.engine._speak("我没确定您指的是哪段内容，请说明是刚才的现场补充，还是另一个问题。", teaching_turn_token=token)
        elif session is None:
            await self.engine._speak("这轮已经没有可复述的现场补充，请重新教我一次。", teaching_turn_token=token)
        elif result.intent == "STATUS":
            state = session.draft_state
            text = ("刚才的补充已临时记住，草稿已提交审核，是否已正式发布以后台为准。" if state == "SUBMITTED"
                    else "刚才的补充已临时记住，草稿提交遇到问题，保存结果请查看后台确认。" if state == "FAILED"
                    else "刚才的补充已临时记住，草稿还在提交中。")
            await self.engine._speak(text, teaching_turn_token=token)
        else:
            await self.replay(session, token=token)
        return True

    async def replay(self, s=None, token=None):
        s = s or self.memory()
        if not s or not self.valid(s):
            return False
        token = token or self.engine._begin_legacy_qa_turn()
        if not self.engine._is_legacy_qa_turn_current(token):
            return True
        if not self.valid(s) or time.monotonic() >= s.memory_until or not self.engine._is_legacy_qa_turn_current(token):
            return True
        answer = clean_transcript(s.answer)
        answer = re.sub(r"[\r\n]+", "。", answer).rstrip(PUNCT)
        text = "我记下了，您刚才说的是：" + answer + "。"
        await self.engine._speak(text, teaching_turn_token=token)
        return True
