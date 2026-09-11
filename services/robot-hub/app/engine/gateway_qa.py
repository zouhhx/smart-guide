"""VoiceGateway-Public 问答适配器。

边界：只创建/校验 InputEvent、调用 8780、校验 FinalResult，并返回
``answerText`` 与诊断信息。本模块不判断意图、不执行 outputCommands、不调用 TTS。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import httpx

from app.config import get_config


logger = logging.getLogger("hub.engine.gateway_qa")

SCHEMA_VERSION = "1.0.0"
ADAPTER_VERSION = "v5-gateway-qa/1.0.0"
MAX_BODY_BYTES = 1024 * 1024
FINAL_STATUSES = {
    "ANSWER",
    "PARTIAL",
    "CLARIFICATION_REQUIRED",
    "ABSTAINED",
    "BLOCKED",
    "FAILED",
    "COMMAND_COMPLETED",
}
RETRYABLE_HTTP_STATUSES = {408, 425, 429}
TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{32,}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")

SERVICE_UNAVAILABLE_TEXT = "知识服务暂时不可用，请稍后再试。"
CLARIFICATION_TEXT = "请换一种更具体的说法，我再帮您查询。"
ABSTAINED_TEXT = "当前知识库没有找到可靠答案，请咨询现场工作人员。"


class GatewayQAError(RuntimeError):
    """可诊断、可判定是否重试的 Gateway 错误。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class GatewayQASettings:
    mode: str
    base_url: str = ""
    bearer_token_env: str = "V5_GATEWAY_CLIENT_TOKEN"
    bearer_token: str = ""
    tenant_id: str = ""
    site_id: str = ""
    knowledge_base_id: str = ""
    locale: str = "zh-CN"
    web_enabled: bool = False
    request_timeout_seconds: float = 18.0
    max_attempts: int = 2
    target_screen_id: str = ""
    default_current_unit_id: str = ""
    target_screen_by_robot: Mapping[str, str] | None = None
    default_current_unit_by_robot: Mapping[str, str] | None = None
    result_cache_capacity: int = 512
    result_cache_ttl_seconds: float = 1800.0

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "GatewayQASettings":
        raw = dict(config.get("gateway_qa") or {})
        env = os.environ if environ is None else environ
        token_env = str(raw.get("bearer_token_env") or "V5_GATEWAY_CLIENT_TOKEN")
        settings = cls(
            # 部署环境可通过 systemd EnvironmentFile 启用，不需要现场修改 YAML。
            mode=str(env.get("V5_GATEWAY_QA_MODE") or raw.get("mode") or "off").strip().lower(),
            base_url=str(env.get("V5_GATEWAY_BASE_URL") or raw.get("base_url") or "").strip(),
            bearer_token_env=token_env,
            bearer_token=str(env.get(token_env) or ""),
            tenant_id=str(env.get("V5_GATEWAY_TENANT_ID") or raw.get("tenant_id") or "").strip(),
            site_id=str(env.get("V5_GATEWAY_SITE_ID") or raw.get("site_id") or "").strip(),
            knowledge_base_id=str(
                env.get("V5_GATEWAY_KNOWLEDGE_BASE_ID") or raw.get("knowledge_base_id") or ""
            ).strip(),
            locale=str(raw.get("locale") or "zh-CN").strip(),
            web_enabled=raw.get("web_enabled", False),
            request_timeout_seconds=float(raw.get("request_timeout_seconds", 18)),
            max_attempts=int(raw.get("max_attempts", 2)),
            target_screen_id=str(
                env.get("V5_GATEWAY_TARGET_SCREEN_ID") or raw.get("target_screen_id") or ""
            ).strip(),
            default_current_unit_id=str(
                env.get("V5_GATEWAY_DEFAULT_CURRENT_UNIT_ID")
                or raw.get("default_current_unit_id")
                or ""
            ).strip(),
            target_screen_by_robot=dict(raw.get("target_screen_by_robot") or {}),
            default_current_unit_by_robot=dict(raw.get("default_current_unit_by_robot") or {}),
            result_cache_capacity=int(raw.get("result_cache_capacity", 512)),
            result_cache_ttl_seconds=float(raw.get("result_cache_ttl_seconds", 1800)),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.mode not in {"off", "shadow", "active"}:
            raise ValueError("gateway_qa.mode 必须是 off、shadow 或 active")
        if self.mode == "off":
            return

        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port != 8780
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("gateway_qa.base_url 必须是 http://<局域网IP>:8780，且不能包含路径或凭据")
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
        except ValueError as exc:
            raise ValueError("gateway_qa.base_url 必须使用 IP 地址，不能使用主机名") from exc
        if not (address.is_private or address.is_loopback):
            raise ValueError("gateway_qa.base_url 必须是可信局域网 IP")
        if not TOKEN_RE.fullmatch(self.bearer_token):
            raise ValueError(
                f"环境变量 {self.bearer_token_env} 必须包含至少 32 个合法 Token 字符"
            )
        for field_name, value in (
            ("tenant_id", self.tenant_id),
            ("site_id", self.site_id),
            ("knowledge_base_id", self.knowledge_base_id),
            ("locale", self.locale),
        ):
            _require_identifier(field_name, value)
        if self.web_enabled is not False:
            raise ValueError("gateway_qa.web_enabled 必须为 false")
        if not 0 < self.request_timeout_seconds <= 18:
            raise ValueError("gateway_qa.request_timeout_seconds 必须在 0 到 18 秒之间")
        if not 1 <= self.max_attempts <= 2:
            raise ValueError("gateway_qa.max_attempts 必须为 1 或 2")
        has_site_defaults = bool(self.target_screen_id or self.default_current_unit_id)
        if has_site_defaults:
            _require_identifier("target_screen_id", self.target_screen_id)
            _require_identifier("default_current_unit_id", self.default_current_unit_id)
        for mapping_name, mapping in (
            ("target_screen_by_robot", self.target_screen_by_robot),
            ("default_current_unit_by_robot", self.default_current_unit_by_robot),
        ):
            for robot_id, value in mapping.items():
                _require_identifier(f"{mapping_name}.{robot_id}", str(value))
        if set(self.target_screen_by_robot) != set(self.default_current_unit_by_robot):
            raise ValueError("targetScreenId 与 defaultCurrentUnitId 的机器人映射必须完全一致")
        if not has_site_defaults and not self.target_screen_by_robot:
            raise ValueError(
                "必须配置站点 target_screen_id/default_current_unit_id，"
                "或为每台机器人配置对应映射"
            )
        if not 1 <= self.result_cache_capacity <= 10000:
            raise ValueError("result_cache_capacity 超出允许范围")
        if not 1 <= self.result_cache_ttl_seconds <= 86400:
            raise ValueError("result_cache_ttl_seconds 超出允许范围")

    def context_for_robot(self, robot_id: str) -> tuple[str, str]:
        target = str(
            (self.target_screen_by_robot or {}).get(robot_id) or self.target_screen_id
        ).strip()
        default_unit = str(
            (self.default_current_unit_by_robot or {}).get(robot_id)
            or self.default_current_unit_id
        ).strip()
        _require_identifier(f"targetScreenId({robot_id})", target)
        _require_identifier(f"currentUnitId({robot_id})", default_unit)
        return target, default_unit


@dataclass(frozen=True)
class GatewayQAResponse:
    answer_text: str
    status: str
    request_id: str
    result_id: str
    transcript_id: str
    should_speak: bool
    diagnostics: Mapping[str, Any]


@dataclass
class _ReplayEntry:
    result_id: str
    spoken: bool
    expires_at: float


class ResultReplayGuard:
    """按机器人隔离的有界 requestId/resultId 播报保护。"""

    def __init__(self, capacity: int, ttl_seconds: float, *, clock: Callable[[], float] = time.monotonic):
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._items: dict[str, OrderedDict[str, _ReplayEntry]] = {}

    def _bucket(self, robot_id: str) -> OrderedDict[str, _ReplayEntry]:
        now = self._clock()
        bucket = self._items.setdefault(robot_id, OrderedDict())
        for request_id in list(bucket):
            if bucket[request_id].expires_at <= now:
                del bucket[request_id]
        return bucket

    def register(self, robot_id: str, request_id: str, result_id: str) -> str:
        bucket = self._bucket(robot_id)
        previous = bucket.get(request_id)
        if previous:
            bucket.move_to_end(request_id)
            if previous.result_id != result_id:
                return "conflict"
            return "duplicate" if previous.spoken else "pending"
        bucket[request_id] = _ReplayEntry(
            result_id=result_id,
            spoken=False,
            expires_at=self._clock() + self.ttl_seconds,
        )
        while len(bucket) > self.capacity:
            bucket.popitem(last=False)
        return "new"

    def mark_spoken(self, robot_id: str, request_id: str, result_id: str) -> bool:
        bucket = self._bucket(robot_id)
        entry = bucket.get(request_id)
        if not entry or entry.result_id != result_id:
            return False
        entry.spoken = True
        entry.expires_at = self._clock() + self.ttl_seconds
        bucket.move_to_end(request_id)
        return True


class GatewayQAAdapter:
    def __init__(
        self,
        settings: GatewayQASettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        now_factory: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ):
        settings.validate()
        self.settings = settings
        self._clock = clock
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self._uuid_factory = uuid_factory
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        self.replay_guard = ResultReplayGuard(
            settings.result_cache_capacity,
            settings.result_cache_ttl_seconds,
            clock=clock,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def build_input_event(
        self,
        text: str,
        robot_id: str,
        *,
        current_unit_id: str | None,
    ) -> tuple[dict[str, Any], bytes]:
        if not isinstance(text, str) or not text.strip():
            raise GatewayQAError("invalid_text", "问答文本不能为空")
        _require_identifier("robot_id", robot_id)
        target_screen_id, default_unit_id = self.settings.context_for_robot(robot_id)
        unit_id = str(current_unit_id or "").strip() or default_unit_id
        _require_identifier("currentUnitId", unit_id)

        request_id = str(self._uuid_factory())
        event_id = str(self._uuid_factory())
        created_at = self._now_factory().astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        event = {
            "schemaVersion": SCHEMA_VERSION,
            "requestId": request_id,
            "tenantId": self.settings.tenant_id,
            "createdAt": created_at,
            "status": "RECEIVED",
            "eventId": event_id,
            "channel": "VOICE",
            "inputType": "QUERY",
            "siteId": self.settings.site_id,
            "currentUnitId": unit_id,
            "targetScreenId": target_screen_id,
            "visitorProfile": {
                "visitorId": None,
                "role": "GENERAL",
                "organizationType": None,
                "industry": None,
                "visitPurpose": None,
                "painPoints": [],
                "maturityLevel": None,
            },
            "source": {
                # VoiceGateway-Public v0.3.16 允许稳定的大写来源标识；沿用 V5
                # 接入计划中的名称，便于 Core 侧区分展厅机器人流量。
                "type": "EXHIBITION_ROBOT",
                "sourceId": robot_id,
                "adapterVersion": ADAPTER_VERSION,
            },
            "knowledgeBaseIds": [self.settings.knowledge_base_id],
            "text": text,
            "deviceId": robot_id,
            "locale": self.settings.locale,
            "webEnabled": False,
            "metadata": {"eventName": "ASR_FINAL"},
        }
        self._validate_input_event(event)
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_BODY_BYTES:
            raise GatewayQAError("request_too_large", "InputEvent 超过 1 MiB")
        return event, payload

    async def query(
        self,
        text: str,
        robot_id: str,
        *,
        current_unit_id: str | None,
    ) -> GatewayQAResponse:
        event, payload = self.build_input_event(text, robot_id, current_unit_id=current_unit_id)
        deadline = self._clock() + self.settings.request_timeout_seconds
        last_error: GatewayQAError | None = None

        for attempt in range(1, self.settings.max_attempts + 1):
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            try:
                # httpx 的 connect/read/write 超时分别计时，不能单独保证整次尝试
                # 不超过剩余总期限；外层 wait_for 才是 18 秒硬边界。
                result = await asyncio.wait_for(
                    self._post_once(payload, timeout_seconds=remaining),
                    timeout=remaining,
                )
                validated = self._validate_final_result(result, event)
                replay = self.replay_guard.register(
                    robot_id, validated["requestId"], validated["resultId"]
                )
                if replay == "conflict":
                    logger.error(
                        "[GatewayQA] 协议冲突 robot=%s requestId=%s",
                        robot_id,
                        validated["requestId"],
                    )
                    return self._to_response(validated, attempt, replay, should_speak=False)
                if replay == "duplicate":
                    logger.warning(
                        "[GatewayQA] 忽略已播报重放 robot=%s requestId=%s resultId=%s",
                        robot_id,
                        validated["requestId"],
                        validated["resultId"],
                    )
                    return self._to_response(validated, attempt, replay, should_speak=False)
                return self._to_response(validated, attempt, replay, should_speak=True)
            except GatewayQAError as exc:
                last_error = exc
            except (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError) as exc:
                last_error = GatewayQAError("network_error", str(exc), retryable=True)
            except httpx.TimeoutException as exc:
                last_error = GatewayQAError("timeout", str(exc), retryable=True)
            except asyncio.TimeoutError as exc:
                # wait_for 使用的正是本轮全部剩余期限；命中即表示总期限已耗尽，
                # 不能再发起一个注定超时的第二次请求。
                raise GatewayQAError("timeout", str(exc), retryable=True) from exc

            if not last_error.retryable or attempt >= self.settings.max_attempts:
                raise last_error
            if deadline - self._clock() <= 0:
                break
            logger.warning(
                "[GatewayQA] 第 %d 次请求失败，将在总期限内重试 code=%s requestId=%s",
                attempt,
                last_error.code,
                event["requestId"],
            )
            await asyncio.sleep(0)

        raise last_error or GatewayQAError("timeout", "Gateway QA 总等待时间已耗尽")

    def mark_spoken(self, robot_id: str, request_id: str, result_id: str) -> bool:
        return self.replay_guard.mark_spoken(robot_id, request_id, result_id)

    async def _post_once(self, payload: bytes, *, timeout_seconds: float) -> dict[str, Any]:
        url = self.settings.base_url.rstrip("/") + "/v1/input-events"
        headers = {
            "Authorization": f"Bearer {self.settings.bearer_token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }
        try:
            async with self._client.stream(
                "POST",
                url,
                content=payload,
                headers=headers,
                timeout=httpx.Timeout(timeout_seconds),
            ) as response:
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > MAX_BODY_BYTES:
                            raise GatewayQAError("response_too_large", "Gateway 响应超过 1 MiB")
                    except ValueError:
                        raise GatewayQAError("invalid_content_length", "Gateway Content-Length 非法")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BODY_BYTES:
                        raise GatewayQAError("response_too_large", "Gateway 响应超过 1 MiB")
                parsed = _parse_json_body(bytes(body))
                if not 200 <= response.status_code < 300:
                    explicit_retryable = parsed.get("retryable") if isinstance(parsed, dict) else None
                    retryable = (
                        explicit_retryable is True
                        or response.status_code in RETRYABLE_HTTP_STATUSES
                        or 500 <= response.status_code <= 599
                    )
                    if explicit_retryable is False:
                        retryable = False
                    raise GatewayQAError(
                        f"http_{response.status_code}",
                        f"Gateway 返回 HTTP {response.status_code}",
                        retryable=retryable,
                    )
                if not isinstance(parsed, dict):
                    raise GatewayQAError("invalid_json", "Gateway 成功响应必须是 JSON 对象")
                return parsed
        except GatewayQAError:
            raise

    @staticmethod
    def _validate_input_event(event: Mapping[str, Any]) -> None:
        required = {
            "schemaVersion", "requestId", "tenantId", "createdAt", "status", "eventId",
            "channel", "inputType", "siteId", "currentUnitId", "targetScreenId",
            "visitorProfile", "source", "knowledgeBaseIds", "text", "deviceId", "locale",
            "webEnabled", "metadata",
        }
        if set(event) != required:
            raise GatewayQAError("input_contract_error", "InputEvent 字段集合不符合 1.0.0")
        if event["schemaVersion"] != SCHEMA_VERSION or event["status"] != "RECEIVED":
            raise GatewayQAError("input_contract_error", "InputEvent 版本或状态非法")
        if event["channel"] != "VOICE" or event["inputType"] != "QUERY":
            raise GatewayQAError("input_contract_error", "InputEvent 通道或类型非法")
        if event["webEnabled"] is not False:
            raise GatewayQAError("input_contract_error", "webEnabled 必须为 false")
        if len(event["knowledgeBaseIds"]) != 1:
            raise GatewayQAError("input_contract_error", "knowledgeBaseIds 必须恰好一个值")
        if "sessionId" in event:
            raise GatewayQAError("input_contract_error", "首期 InputEvent 不得包含 sessionId")
        if event["source"].get("type") != "EXHIBITION_ROBOT":
            raise GatewayQAError("input_contract_error", "source.type 非法")
        if event["metadata"] != {"eventName": "ASR_FINAL"}:
            raise GatewayQAError("input_contract_error", "metadata 含有未允许字段")

    @staticmethod
    def _validate_final_result(result: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
        if result.get("schemaVersion") != SCHEMA_VERSION:
            raise GatewayQAError("result_contract_error", "FinalResult.schemaVersion 非法")
        for key in ("requestId", "tenantId", "currentUnitId", "targetScreenId", "locale"):
            if result.get(key) != event.get(key):
                raise GatewayQAError("result_identity_mismatch", f"FinalResult.{key} 与请求不一致")
        for key in ("resultId", "transcriptId"):
            value = result.get(key)
            if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
                raise GatewayQAError("result_contract_error", f"FinalResult.{key} 非法")
        status = result.get("status")
        if status not in FINAL_STATUSES:
            raise GatewayQAError("result_contract_error", "FinalResult.status 不是合法终态")
        answer_text = result.get("answerText")
        if answer_text is not None and not isinstance(answer_text, str):
            raise GatewayQAError("result_contract_error", "FinalResult.answerText 必须是字符串")
        commands = result.get("outputCommands")
        if not isinstance(commands, list):
            raise GatewayQAError("result_contract_error", "FinalResult.outputCommands 必须是数组")
        if len(commands) > 1:
            raise GatewayQAError("result_contract_error", "FinalResult.outputCommands 最多一个")
        for command in commands:
            if not isinstance(command, dict):
                raise GatewayQAError("result_contract_error", "FinalResult.outputCommands 结构非法")
            if command.get("schemaVersion") != SCHEMA_VERSION:
                raise GatewayQAError("result_contract_error", "OutputCommand.schemaVersion 非法")
            if command.get("status") != "READY" or command.get("capability") != "DISPLAY":
                raise GatewayQAError("result_contract_error", "OutputCommand 状态或能力非法")
            if command.get("action") not in {"SHOW_TEXT", "NAVIGATE_PRESENTATION"}:
                raise GatewayQAError("result_contract_error", "OutputCommand.action 非法")
            if not isinstance(command.get("payload"), dict):
                raise GatewayQAError("result_contract_error", "outputCommands.payload 必须是对象")
        normalized = dict(result)
        normalized["answerText"] = (answer_text or "").strip()
        return normalized

    @staticmethod
    def _to_response(
        result: Mapping[str, Any],
        attempts: int,
        replay: str,
        *,
        should_speak: bool,
    ) -> GatewayQAResponse:
        status = str(result["status"])
        answer = str(result.get("answerText") or "").strip()
        if status == "CLARIFICATION_REQUIRED" and not answer:
            answer = CLARIFICATION_TEXT
        elif status == "ABSTAINED" and not answer:
            answer = ABSTAINED_TEXT
        elif status in {"BLOCKED", "FAILED", "COMMAND_COMPLETED"}:
            answer = SERVICE_UNAVAILABLE_TEXT
        elif status in {"ANSWER", "PARTIAL"} and not answer:
            raise GatewayQAError("result_contract_error", f"{status} 必须包含 answerText")
        return GatewayQAResponse(
            answer_text=answer,
            status=status,
            request_id=str(result["requestId"]),
            result_id=str(result["resultId"]),
            transcript_id=str(result["transcriptId"]),
            should_speak=should_speak,
            diagnostics={"attempts": attempts, "replay": replay, "status": status},
        )


def _require_identifier(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value) or value == "现场提供":
        raise ValueError(f"{field_name} 必须是现场提供的合法非空标识")


def _parse_json_body(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayQAError("invalid_json", "Gateway 返回非法 UTF-8 JSON") from exc


_adapter: GatewayQAAdapter | None = None
_adapter_signature: tuple[Any, ...] | None = None


def get_gateway_qa_mode(
    config: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    raw = (config or get_config()).get("gateway_qa") or {}
    env = os.environ if environ is None else environ
    return str(env.get("V5_GATEWAY_QA_MODE") or raw.get("mode") or "off").strip().lower()


def validate_gateway_qa_config(config: Mapping[str, Any] | None = None) -> GatewayQASettings:
    return GatewayQASettings.from_config(config or get_config())


def get_gateway_qa_adapter(config: Mapping[str, Any] | None = None) -> GatewayQAAdapter | None:
    global _adapter, _adapter_signature
    settings = validate_gateway_qa_config(config)
    if settings.mode == "off":
        return None
    signature = (
        settings.mode,
        settings.base_url,
        settings.bearer_token_env,
        settings.tenant_id,
        settings.site_id,
        settings.knowledge_base_id,
        settings.locale,
        settings.target_screen_id,
        settings.default_current_unit_id,
        tuple(sorted((settings.target_screen_by_robot or {}).items())),
        tuple(sorted((settings.default_current_unit_by_robot or {}).items())),
    )
    if _adapter is None or _adapter_signature != signature:
        _adapter = GatewayQAAdapter(settings)
        _adapter_signature = signature
    return _adapter


async def close_gateway_qa_adapter() -> None:
    global _adapter, _adapter_signature
    if _adapter is not None:
        await _adapter.close()
    _adapter = None
    _adapter_signature = None


async def answer_via_gateway(text: str, world_state: Any, robot_id: str) -> dict[str, Any] | None:
    """供 AnswerEngine 调用；off 返回 None，shadow 只诊断，active 返回统一 QA 结果。"""
    adapter = get_gateway_qa_adapter()
    if adapter is None:
        return None
    # V5 的 current_exhibit_id 是本地数据库数字主键，不等于 Core 的
    # currentUnitId。首期没有两套 ID 的权威映射，因此始终使用 Gateway
    # 现场配置提供的 currentUnitId，避免把本地展项 ID 错发给 Core。
    current_unit_id = None
    try:
        response = await adapter.query(text, robot_id, current_unit_id=current_unit_id)
        logger.info(
            "[GatewayQA] 完成 mode=%s robot=%s status=%s attempts=%s requestId=%s",
            adapter.settings.mode,
            robot_id,
            response.status,
            response.diagnostics.get("attempts"),
            response.request_id,
        )
        if adapter.settings.mode == "shadow":
            return None
        return {
            "answer": response.answer_text,
            "source": "gateway_qa",
            "confidence": 1.0,
            "resume_policy": "none",
            "action": None,
            "extra": {
                "gateway": {
                    "request_id": response.request_id,
                    "result_id": response.result_id,
                    "transcript_id": response.transcript_id,
                    "status": response.status,
                    "should_speak": response.should_speak,
                    "diagnostics": dict(response.diagnostics),
                }
            },
        }
    except (GatewayQAError, ValueError) as exc:
        code = getattr(exc, "code", "configuration_error")
        logger.error("[GatewayQA] 请求失败 mode=%s robot=%s code=%s", adapter.settings.mode, robot_id, code)
        if adapter.settings.mode == "shadow":
            return None
        return {
            "answer": SERVICE_UNAVAILABLE_TEXT,
            "source": f"gateway_qa_error:{code}",
            "confidence": 1.0,
            "resume_policy": "none",
            "action": None,
            "extra": {"gateway": {"should_speak": True, "error_code": code}},
        }


def mark_gateway_result_spoken(robot_id: str, request_id: str, result_id: str) -> bool:
    if _adapter is None:
        return False
    return _adapter.mark_spoken(robot_id, request_id, result_id)
