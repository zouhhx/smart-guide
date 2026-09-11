"""Gateway QA 测试替身：实现请求幂等、重放和可注入错误。"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class FakeGatewayTransport(httpx.AsyncBaseTransport):
    def __init__(self, behaviors: list[Any] | None = None):
        self.behaviors = deque(behaviors or [])
        self.calls: list[dict[str, Any]] = []
        self._results: dict[str, tuple[bytes, dict[str, Any]]] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        event = json.loads(body.decode("utf-8"))
        self.calls.append({"body": body, "event": event, "url": str(request.url)})

        if self.behaviors:
            behavior = self.behaviors.popleft()
            if isinstance(behavior, Exception):
                raise behavior
            if isinstance(behavior, httpx.Response):
                behavior.request = request
                return behavior
            if callable(behavior):
                return behavior(request, event, body)

        request_id = event["requestId"]
        previous = self._results.get(request_id)
        if previous:
            old_body, result = previous
            if old_body != body:
                return httpx.Response(
                    409,
                    request=request,
                    json={"retryable": False, "error": "requestId payload conflict"},
                )
            return httpx.Response(200, request=request, json=result)

        result = make_final_result(event)
        self._results[request_id] = (body, result)
        return httpx.Response(200, request=request, json=result)


def make_final_result(event: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc)
    created_at_text = created_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    deadline_at_text = (created_at + timedelta(seconds=10)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    result = {
        "schemaVersion": "1.0.0",
        "requestId": event["requestId"],
        "tenantId": event["tenantId"],
        "createdAt": created_at_text,
        "currentUnitId": event["currentUnitId"],
        "targetScreenId": event["targetScreenId"],
        "locale": event["locale"],
        "resultId": "result-001",
        "transcriptId": "transcript-001",
        "status": "ANSWER",
        "answerText": "这是来自 Core 的答案。",
        "citationRefs": [],
        "missingInformation": [],
        "outputCommands": [
            {
                "schemaVersion": "1.0.0",
                "requestId": event["requestId"],
                "tenantId": event["tenantId"],
                "createdAt": created_at_text,
                "status": "READY",
                "commandId": "command-001",
                "resultId": "result-001",
                "targetScreenId": event["targetScreenId"],
                "capability": "DISPLAY",
                "action": "SHOW_TEXT",
                "payload": {
                    "question": event["text"],
                    "text": "不得由 V5 执行",
                    "currentUnitId": event["currentUnitId"],
                    "displayDurationSeconds": 30,
                    "locale": event["locale"],
                },
                "idempotencyKey": f'{event["requestId"]}:show-text',
                "deadlineAt": deadline_at_text,
            }
        ],
        "failureCode": None,
    }
    result.update(overrides)
    return result
