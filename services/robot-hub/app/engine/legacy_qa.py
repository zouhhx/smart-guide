"""老系统语音问答适配器。

只负责把原始 ASR 最终文本发送到固定的老系统接口，并返回 reply。
不判断意图、不执行 actions、不调用 TTS。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

import aiohttp

from app.config import get_config


logger = logging.getLogger("hub.engine.legacy_qa")

SERVICE_UNAVAILABLE_TEXT = "抱歉，问答服务暂时不可用，请稍后再试。"
_MAX_RESPONSE_BYTES = 1024 * 1024


class LegacyQAError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LegacyQAResponse:
    answer_status: str
    reason: str | None
    reply: str
    trace_id: str | None
    structured: bool = True
    teachable: bool = False


@dataclass(frozen=True)
class LegacyQASettings:
    mode: str
    endpoint: str
    target: str
    source: str
    timeout_seconds: float
    robot_contract_enabled: bool = False

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "LegacyQASettings":
        raw = dict(config.get("legacy_qa") or {})
        mode = os.getenv("V5_LEGACY_QA_MODE", str(raw.get("mode", "off"))).strip().lower()
        settings = cls(
            mode=mode,
            endpoint=str(raw.get("endpoint", "")).strip(),
            target=str(raw.get("target", "2")).strip(),
            source=str(raw.get("source", "microphone")).strip(),
            timeout_seconds=float(raw.get("timeout_seconds", 18)),
            robot_contract_enabled=raw.get("robot_contract_enabled", False) is True,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.mode not in {"off", "active"}:
            raise ValueError("legacy_qa.mode 必须是 off 或 active")
        if self.mode == "off" and not self.endpoint:
            return

        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname is None
            or parsed.port not in {3001, 3481}
            or parsed.path != "/api/asr-text"
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("legacy_qa.endpoint 必须是 http://<局域网IP>:<3001或3481>/api/asr-text")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ValueError("legacy_qa.endpoint 必须使用 IP 地址") from exc
        if not address.is_private:
            raise ValueError("legacy_qa.endpoint 必须使用局域网 IP")
        if not self.target:
            raise ValueError("legacy_qa.target 不能为空")
        if not self.source:
            raise ValueError("legacy_qa.source 不能为空")
        if not 0 < self.timeout_seconds <= 20:
            raise ValueError("legacy_qa.timeout_seconds 必须在 0 到 20 秒之间")


class LegacyQAAdapter:
    def __init__(self, settings: LegacyQASettings):
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def ask(self, text: str, *, require_structured: bool = False, robot_id: str = "", session_id: str = "") -> LegacyQAResponse:
        question = str(text or "").strip()
        if not question:
            raise LegacyQAError("invalid_text", "问答文本不能为空")

        payload = {
            "text": question,
            "target": self.settings.target,
            "source": self.settings.source,
        }
        # 61确认新契约后统一打开；默认保持现场三字段请求。
        if self.settings.robot_contract_enabled:
            payload["suppressTts"] = True
            if robot_id:
                payload.update(clientMode="ROBOT", robotId=robot_id, sessionId=session_id)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > _MAX_RESPONSE_BYTES:
            raise LegacyQAError("request_too_large", "问答请求超过1MiB")
        timeout = aiohttp.ClientTimeout(total=self.settings.timeout_seconds)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=timeout)

        try:
            async with self._session.post(
                self.settings.endpoint,
                data=body,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json",
                },
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise LegacyQAError("http_error", f"老系统返回 HTTP {response.status}")
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > _MAX_RESPONSE_BYTES:
                    raise LegacyQAError("response_too_large", "老系统响应超过 1 MiB")
                raw = b""
                async for chunk in response.content.iter_chunked(65536):
                    raw += chunk
                    if len(raw) > _MAX_RESPONSE_BYTES:
                        raise LegacyQAError("response_too_large", "老系统响应超过1MiB")
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise LegacyQAError("response_too_large", "老系统响应超过 1 MiB")
        except LegacyQAError:
            raise
        except TimeoutError as exc:
            raise LegacyQAError("timeout", "老系统请求超时") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise LegacyQAError("network_error", str(exc)) from exc

        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LegacyQAError("invalid_json", "老系统返回非法 JSON") from exc
        if not isinstance(result, dict):
            raise LegacyQAError("invalid_response", "老系统响应必须是 JSON 对象")

        # actions 属于老系统其他终端的协议，V5 明确忽略。
        reply = result.get("reply", "")
        if not isinstance(reply, str):
            raise LegacyQAError("invalid_reply", "老系统 reply 必须是字符串")
        reply = reply.strip()

        status = result.get("answerStatus")
        if status is None:
            if require_structured:
                raise LegacyQAError("missing_answer_status", "老系统响应缺少 answerStatus")
            if not reply:
                raise LegacyQAError("missing_reply", "老系统响应缺少有效 reply")
            return LegacyQAResponse("ANSWERED", None, reply, None, structured=False)
        if not isinstance(status, str):
            raise LegacyQAError("invalid_answer_status", "answerStatus 必须是字符串")
        status = status.strip().upper()
        reason_value = result.get("reason")
        if reason_value is not None and not isinstance(reason_value, str):
            raise LegacyQAError("invalid_reason", "reason 必须是字符串或 null")
        reason = reason_value.strip().upper() if isinstance(reason_value, str) else None
        trace_value = result.get("traceId")
        if trace_value is not None and not isinstance(trace_value, str):
            raise LegacyQAError("invalid_trace_id", "traceId 必须是字符串或 null")
        trace_id = trace_value.strip() if isinstance(trace_value, str) and trace_value.strip() else None
        teachable = result.get("teachable", False)
        if type(teachable) is not bool or (require_structured and "teachable" not in result):
            raise LegacyQAError("invalid_teachable", "teachable必须是布尔值")
        if teachable and (status != "ABSTAINED" or reason != "NO_KNOWLEDGE"):
            raise LegacyQAError("conflicting_teachable", "教学状态冲突")

        if status == "ANSWERED":
            if not reply or reason:
                raise LegacyQAError(
                    "invalid_answered",
                    "ANSWERED 必须包含有效 reply 且 reason 为空",
                )
        elif status == "ABSTAINED":
            if not reason or reply:
                raise LegacyQAError(
                    "invalid_abstained",
                    "ABSTAINED 必须携带 NO_KNOWLEDGE 且 reply 为空",
                )
        elif status == "FAILED":
            if reason != "SERVICE_ERROR" or reply:
                raise LegacyQAError(
                    "invalid_failed",
                    "FAILED 必须携带 SERVICE_ERROR 且 reply 为空",
                )
        else:
            raise LegacyQAError("unknown_answer_status", f"未知 answerStatus: {status}")
        return LegacyQAResponse(status, reason, reply, trace_id, teachable=teachable)


_adapter: LegacyQAAdapter | None = None


def get_legacy_qa_mode(config: Mapping[str, Any] | None = None) -> str:
    return LegacyQASettings.from_config(config or get_config()).mode


def validate_legacy_qa_config(config: Mapping[str, Any] | None = None) -> LegacyQASettings:
    return LegacyQASettings.from_config(config or get_config())


def get_legacy_qa_adapter(config: Mapping[str, Any] | None = None) -> LegacyQAAdapter | None:
    global _adapter
    settings = validate_legacy_qa_config(config)
    if settings.mode == "off":
        return None
    if _adapter is None or _adapter.settings != settings:
        _adapter = LegacyQAAdapter(settings)
    return _adapter


async def close_legacy_qa_adapter() -> None:
    global _adapter
    if _adapter is not None:
        await _adapter.close()
        _adapter = None


async def answer_via_legacy(text: str, world_state: Any, robot_id: str) -> dict[str, Any] | None:
    del world_state  # 首期合同不使用 V5 状态，也不向老系统附加字段。
    adapter = get_legacy_qa_adapter()
    if adapter is None:
        return None
    try:
        from app.engine.legacy_teaching import (
            TEACHING_DISABLED_TEXT,
            get_legacy_teaching_settings,
        )

        teaching_enabled = get_legacy_teaching_settings().enabled
        from app.engine.teaching_runtime import reception_id
        response = await adapter.ask(text, require_structured=teaching_enabled,
                                     robot_id=robot_id, session_id=reception_id(robot_id))
        eligible = teaching_enabled and response.teachable and response.reason == "NO_KNOWLEDGE"
        logger.info(
            "[LegacyQA] 完成 robot=%s status=%s reply_chars=%d structured=%s",
            robot_id,
            response.answer_status,
            len(response.reply),
            response.structured,
        )
        if response.answer_status == "ANSWERED":
            answer = response.reply
            should_speak = True
        elif response.answer_status == "ABSTAINED":
            answer = "" if eligible else TEACHING_DISABLED_TEXT
            should_speak = not eligible
        else:
            answer = SERVICE_UNAVAILABLE_TEXT
            should_speak = True
        return {
            "answer": answer,
            "source": "legacy_qa",
            "confidence": 1.0,
            "resume_policy": "none",
            "extra": {
                "legacy_qa": {
                    "should_speak": should_speak,
                    "answer_status": response.answer_status,
                    "reason": response.reason,
                    "trace_id": response.trace_id,
                    "teaching_required": bool(
                        teaching_enabled
                        and response.answer_status == "ABSTAINED"
                        and response.reason == "NO_KNOWLEDGE"
                        and response.teachable
                    ),
                }
            },
        }
    except (LegacyQAError, ValueError) as exc:
        code = getattr(exc, "code", "config_error")
        logger.error("[LegacyQA] 请求失败 robot=%s code=%s", robot_id, code)
        return {
            "answer": SERVICE_UNAVAILABLE_TEXT,
            "source": f"legacy_qa_error:{code}",
            "confidence": 1.0,
            "resume_policy": "none",
            "extra": {"legacy_qa": {"should_speak": True, "error_code": code}},
        }
