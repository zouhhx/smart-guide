"""老系统拒答后的人工教学与待审核 QA 草稿适配器。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

import aiohttp

from app.config import get_config


logger = logging.getLogger("hub.engine.legacy_teaching")

TEACHING_PROMPT = "这个问题我暂时没有找到准确资料，请现场工作人员为您补充说明。"
TEACHING_SUCCESS_TEXT = "好的，我已经记录下来了，审核通过后我就能回答这个问题了。"
TEACHING_CANCEL_TEXT = "好的，本次学习已经取消。"
TEACHING_DISABLED_TEXT = "这个问题我一时回答不上来，您可以稍后再问我，或者问问现场工作人员。"

_MAX_RESPONSE_BYTES = 1024 * 1024
_FINISH_PHRASES = ("讲解完毕", "回答完毕")
_CANCEL_PHRASES = frozenset(("取消录入", "不用录了"))
_TERMINAL_PUNCTUATION = " 。.！!？?，,；;：:\t\r\n"


class LegacyTeachingError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LegacyTeachingSettings:
    enabled: bool
    draft_endpoint: str
    bearer_token_env: str
    bearer_token: str = field(repr=False)
    session_timeout_seconds: int
    request_timeout_seconds: float
    max_attempts: int
    max_fragments: int
    max_answer_chars: int
    allowed_robot_ids: tuple[str, ...] = ()
    analyze_endpoint: str = "http://192.168.16.61:3001/api/knowledge/teaching/analyze"
    analyze_bearer_token: str = field(default="", repr=False)
    analysis_min_interval_seconds: float = 3
    analysis_timeout_seconds: float = 5
    final_analysis_timeout_seconds: float = 5
    replay_wait_seconds: float = 2
    memory_ttl_seconds: float = 600

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "LegacyTeachingSettings":
        raw = dict(config.get("legacy_teaching") or {})
        enabled = _env_bool(
            "V5_LEGACY_TEACHING_ENABLED",
            bool(raw.get("enabled", False)),
        )
        token_env = str(
            raw.get("bearer_token_env", "V5_LEGACY_QA_DRAFT_TOKEN")
        ).strip()
        settings = cls(
            enabled=enabled,
            draft_endpoint=str(raw.get("draft_endpoint", "")).strip(),
            bearer_token_env=token_env,
            bearer_token=os.getenv(token_env, "").strip() if token_env else "",
            session_timeout_seconds=int(raw.get("session_timeout_seconds", 120)),
            request_timeout_seconds=float(raw.get("request_timeout_seconds", 10)),
            max_attempts=int(raw.get("max_attempts", 2)),
            max_fragments=int(raw.get("max_fragments", 50)),
            max_answer_chars=int(raw.get("max_answer_chars", 4000)),
            allowed_robot_ids=tuple(raw.get("allowed_robot_ids") or ()),
            analyze_endpoint=str(raw.get("analyze_endpoint", "http://192.168.16.61:3001/api/knowledge/teaching/analyze")),
            analyze_bearer_token=os.getenv(str(raw.get("analyze_bearer_token_env", "V5_LEGACY_TEACHING_ANALYZE_TOKEN")), "").strip(),
            analysis_min_interval_seconds=float(raw.get("analysis_min_interval_seconds", 3)),
            analysis_timeout_seconds=float(raw.get("analysis_timeout_seconds", 5)),
            final_analysis_timeout_seconds=float(raw.get("final_analysis_timeout_seconds", 5)),
            replay_wait_seconds=float(raw.get("replay_wait_seconds", 2)),
            memory_ttl_seconds=float(raw.get("memory_ttl_seconds", 600)),
        )
        settings.validate()
        if enabled and os.getenv("V5_LEGACY_QA_MODE", str((config.get("legacy_qa") or {}).get("mode", "off"))) != "active":
            raise ValueError("教学启用要求 legacy_qa.mode=active")
        if enabled and (config.get("legacy_qa") or {}).get("robot_contract_enabled") is not True:
            raise ValueError("教学启用前必须确认61新契约并开启 legacy_qa.robot_contract_enabled")
        return settings

    def validate(self) -> None:
        if not self.enabled:
            return
        parsed = urlsplit(self.draft_endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname is None
            or parsed.port not in {3001, 3481}
            or parsed.path != "/api/knowledge/qa-drafts"
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "legacy_teaching.draft_endpoint 必须是 "
                "http://<局域网IP>:<3001或3481>/api/knowledge/qa-drafts"
            )
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ValueError("legacy_teaching.draft_endpoint 必须使用 IP 地址") from exc
        if not address.is_private:
            raise ValueError("legacy_teaching.draft_endpoint 必须使用局域网 IP")
        if not self.bearer_token_env or not self.bearer_token:
            raise ValueError("人工教学启用时必须通过环境变量提供草稿写入凭据")
        analysis_url = urlsplit(self.analyze_endpoint)
        if (analysis_url.scheme != "http" or analysis_url.hostname != parsed.hostname
                or analysis_url.port != parsed.port or analysis_url.path != "/api/knowledge/teaching/analyze"
                or analysis_url.query or analysis_url.fragment or analysis_url.username or analysis_url.password):
            raise ValueError("教学分析接口必须使用同一可信局域网61主机及指定路径")
        if not self.analyze_bearer_token or any(c.isspace() for c in self.analyze_bearer_token + self.bearer_token):
            raise ValueError("人工教学启用时必须通过环境变量提供合法分析与草稿凭据")
        if (self.session_timeout_seconds != 120 or self.max_fragments > 50 or self.max_answer_chars > 4000
                or not 0 < self.analysis_timeout_seconds <= 5
                or not 0 < self.final_analysis_timeout_seconds <= 5
                or not 0 < self.replay_wait_seconds <= 2
                or self.analysis_min_interval_seconds < 3 or not 0 < self.memory_ttl_seconds <= 600
                or self.request_timeout_seconds > 10):
            raise ValueError("教学期限或容量超出首期约定上限")
        if not 1 <= self.session_timeout_seconds <= 600:
            raise ValueError("legacy_teaching.session_timeout_seconds 必须在 1 到 600 秒之间")
        if not 0 < self.request_timeout_seconds <= 20:
            raise ValueError("legacy_teaching.request_timeout_seconds 必须在 0 到 20 秒之间")
        if not 1 <= self.max_attempts <= 2:
            raise ValueError("legacy_teaching.max_attempts 必须在 1 到 2 之间")
        if not 1 <= self.max_fragments <= 100:
            raise ValueError("legacy_teaching.max_fragments 必须在 1 到 100 之间")
        if not 1 <= self.max_answer_chars <= 20000:
            raise ValueError("legacy_teaching.max_answer_chars 必须在 1 到 20000 之间")


@dataclass
class TeachingSession:
    turn_id: str
    submission_id: str
    question: str
    status: str
    started_at: float
    expires_at: float
    fragments: list[str] = field(default_factory=list)
    terminal_reason: str = ""
    reception_id: str = ""
    analysis: dict | None = None
    analysis_revision: int = -1

    @classmethod
    def create(cls, question: str) -> "TeachingSession":
        return cls(
            turn_id=str(uuid.uuid4()),
            submission_id=str(uuid.uuid4()),
            question=question.strip(),
            status="PROMPTING",
            started_at=time.monotonic(),
            expires_at=0.0,
        )

    def begin_collecting(self, timeout_seconds: int) -> None:
        now = time.monotonic()
        self.status = "COLLECTING"
        self.started_at = now
        self.expires_at = now + timeout_seconds

    @property
    def answer(self) -> str:
        return "\n".join(fragment for fragment in self.fragments if fragment).strip()


@dataclass(frozen=True)
class FrozenDraftSubmission:
    submission_id: str
    body: bytes


def parse_teaching_utterance(text: str) -> tuple[str, str]:
    """返回 (fragment|finish|cancel, 可保存的文本)。"""
    raw = str(text or "").strip()
    normalized = raw.rstrip(_TERMINAL_PUNCTUATION)
    if normalized in _CANCEL_PHRASES:
        return "cancel", ""
    for phrase in _FINISH_PHRASES:
        if normalized.endswith(phrase):
            prefix = normalized[: -len(phrase)].rstrip(_TERMINAL_PUNCTUATION)
            return "finish", prefix
    return "fragment", raw


def freeze_draft_submission(
    session: TeachingSession,
    robot_id: str,
) -> FrozenDraftSubmission:
    payload = {
        "schemaVersion": "1.0.0",
        "submissionId": session.submission_id,
        "question": session.question,
        "rawAnswer": session.answer,
        "suggestedAnswer": "",
        "sessionId": session.reception_id,
        "teachingTurnId": session.turn_id,
        "completionReason": session.terminal_reason,
        "source": "EXHIBITION_ROBOT",
        "robotId": robot_id,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        ),
    }
    if session.analysis is not None and session.analysis_revision == len(session.fragments):
        payload["analysis"] = {k: session.analysis[k] for k in ("relevance", "completeness", "factuality", "warnings")}
        if session.analysis.get("canReplay") and session.analysis.get("factuality") != "CONFLICTING":
            payload["suggestedAnswer"] = session.analysis["replayAnswer"]
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > _MAX_RESPONSE_BYTES:
        raise LegacyTeachingError("request_too_large", "草稿请求超过1MiB")
    return FrozenDraftSubmission(session.submission_id, body)


class LegacyDraftAdapter:
    def __init__(self, settings: LegacyTeachingSettings):
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def submit(self, submission: FrozenDraftSubmission) -> dict[str, str]:
        deadline = time.monotonic() + self.settings.request_timeout_seconds
        last_error: LegacyTeachingError | None = None
        for attempt in range(1, self.settings.max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                result = await asyncio.wait_for(self._submit_once(submission.body, remaining), timeout=remaining)
                if result.get("submissionId") != submission.submission_id:
                    raise LegacyTeachingError("submission_mismatch", "草稿响应关联不一致")
                logger.info(
                    "[LegacyTeaching] 草稿提交成功 submission=%s draft=%s attempt=%d",
                    submission.submission_id,
                    result["draftId"],
                    attempt,
                )
                return result
            except (asyncio.TimeoutError, TimeoutError) as exc:
                raise LegacyTeachingError("timeout", "草稿提交总期限已到", retryable=True) from exc
            except LegacyTeachingError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.settings.max_attempts:
                    raise
                logger.warning(
                    "[LegacyTeaching] 草稿提交暂时失败 submission=%s code=%s attempt=%d",
                    submission.submission_id,
                    exc.code,
                    attempt,
                )
        if last_error is not None:
            raise last_error
        raise LegacyTeachingError("timeout", "草稿提交超时", retryable=True)

    async def _submit_once(self, body: bytes, timeout_seconds: float) -> dict[str, str]:
        if len(body) > _MAX_RESPONSE_BYTES:
            raise LegacyTeachingError("request_too_large", "草稿请求超过1MiB")
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.post(
                self.settings.draft_endpoint,
                data=body,
                headers={
                    "Authorization": f"Bearer {self.settings.bearer_token}",
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json",
                },
                allow_redirects=False,
                timeout=timeout,
            ) as response:
                status = response.status
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > _MAX_RESPONSE_BYTES:
                    raise LegacyTeachingError("response_too_large", "草稿响应超过 1 MiB")
                raw = b""
                async for chunk in response.content.iter_chunked(65536):
                    raw += chunk
                    if len(raw) > _MAX_RESPONSE_BYTES:
                        raise LegacyTeachingError("response_too_large", "草稿响应超过1MiB")
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise LegacyTeachingError("response_too_large", "草稿响应超过 1 MiB")
                if status < 200 or status >= 300:
                    retryable = status in {408, 425, 429, 500, 502, 503, 504}
                    try:
                        error = json.loads(raw)
                        if isinstance(error, dict) and error.get("retryable") is False:
                            retryable = False
                    except (ValueError, UnicodeDecodeError):
                        retryable = False
                    retry_after = response.headers.get("Retry-After", "0")
                    if retryable and retry_after != "0":
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            delay = timeout_seconds
                        if delay >= timeout_seconds:
                            retryable = False
                        elif delay > 0:
                            await asyncio.sleep(delay)
                    raise LegacyTeachingError(
                        f"http_{status}",
                        f"草稿接口返回 HTTP {status}",
                        retryable=retryable,
                    )
        except LegacyTeachingError:
            raise
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise LegacyTeachingError("timeout", "草稿提交超时", retryable=True) from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise LegacyTeachingError("network_error", "草稿网络异常", retryable=True) from exc

        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LegacyTeachingError("invalid_json", "草稿接口返回非法 JSON") from exc
        if not isinstance(result, dict):
            raise LegacyTeachingError("invalid_response", "草稿响应必须是 JSON 对象")
        draft_id = result.get("draftId")
        status = result.get("status")
        if not isinstance(draft_id, str) or not draft_id.strip():
            raise LegacyTeachingError("missing_draft_id", "草稿响应缺少 draftId")
        if status != "PENDING_REVIEW":
            raise LegacyTeachingError("invalid_status", "草稿响应状态不是 PENDING_REVIEW")
        return {"draftId": draft_id.strip(), "status": status, "submissionId": result.get("submissionId")}


_adapter: LegacyDraftAdapter | None = None


def get_legacy_teaching_settings(
    config: Mapping[str, Any] | None = None,
) -> LegacyTeachingSettings:
    return LegacyTeachingSettings.from_config(config or get_config())


def validate_legacy_teaching_config(
    config: Mapping[str, Any] | None = None,
) -> LegacyTeachingSettings:
    return get_legacy_teaching_settings(config)


def get_legacy_draft_adapter(
    config: Mapping[str, Any] | None = None,
) -> LegacyDraftAdapter | None:
    global _adapter
    settings = get_legacy_teaching_settings(config)
    if not settings.enabled:
        return None
    if _adapter is None or _adapter.settings != settings:
        _adapter = LegacyDraftAdapter(settings)
    return _adapter


async def close_legacy_draft_adapter() -> None:
    global _adapter
    if _adapter is not None:
        await _adapter.close()
        _adapter = None
