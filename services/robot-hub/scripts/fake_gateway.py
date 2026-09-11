"""可运行的 VoiceGateway-Public Fake，仅供本地/shadow 联调。

启动前设置 V5_GATEWAY_CLIENT_TOKEN（至少 32 字符），然后：
    python scripts/fake_gateway.py

可用 FAKE_GATEWAY_SCENARIO=success|retryable_503|timeout|invalid_result 注入场景。
Fake 不连接 Core，也不会执行 DISPLAY/SHOW_TEXT。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


MAX_BODY_BYTES = 1024 * 1024
app = FastAPI(title="VoiceGateway-Public Fake", version="1.0.0")
_stored: dict[str, tuple[bytes, dict[str, Any]]] = {}
_lock = asyncio.Lock()


def _token() -> str:
    value = os.environ.get("V5_GATEWAY_CLIENT_TOKEN", "")
    if len(value) < 32:
        raise RuntimeError("V5_GATEWAY_CLIENT_TOKEN 必须至少 32 字符")
    return value


@app.get("/health")
async def health():
    return {"status": "UP"}


@app.get("/ready")
async def ready():
    return {"status": "READY", "core": "UP", "fake": True}


@app.post("/v1/input-events")
async def input_events(request: Request):
    expected = f"Bearer {_token()}"
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied, expected):
        return JSONResponse(status_code=401, content={"retryable": False, "error": "unauthorized"})

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"retryable": False, "error": "too_large"})
    try:
        event = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(status_code=422, content={"retryable": False, "error": "invalid_json"})

    required = {
        "schemaVersion", "requestId", "tenantId", "currentUnitId", "targetScreenId",
        "locale", "channel", "inputType", "knowledgeBaseIds", "text", "source",
    }
    if not isinstance(event, dict) or not required.issubset(event):
        return JSONResponse(status_code=422, content={"retryable": False, "error": "invalid_event"})
    if event.get("schemaVersion") != "1.0.0" or event.get("channel") != "VOICE":
        return JSONResponse(status_code=422, content={"retryable": False, "error": "contract"})

    scenario = os.environ.get("FAKE_GATEWAY_SCENARIO", "success").strip().lower()
    if scenario == "retryable_503":
        return JSONResponse(status_code=503, content={"retryable": True, "error": "fake_temporary"})
    if scenario == "timeout":
        await asyncio.sleep(float(os.environ.get("FAKE_GATEWAY_TIMEOUT_SECONDS", "30")))

    request_id = str(event["requestId"])
    async with _lock:
        previous = _stored.get(request_id)
        if previous:
            previous_body, previous_result = previous
            if previous_body != body:
                return JSONResponse(
                    status_code=409,
                    content={"retryable": False, "error": "request_id_conflict"},
                )
            return previous_result

        stable_result_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"fake-result:{request_id}"))
        transcript_id = hashlib.sha256((request_id + ":transcript").encode()).hexdigest()[:32]
        result = {
            "schemaVersion": "1.0.0",
            "requestId": request_id,
            "tenantId": event["tenantId"],
            "currentUnitId": event["currentUnitId"],
            "targetScreenId": event["targetScreenId"],
            "locale": event["locale"],
            "resultId": stable_result_id,
            "transcriptId": transcript_id,
            "status": "ANSWER",
            "answerText": f"Fake Core 已收到问题：{event['text']}",
            # 故意携带屏幕命令，用于验证 V5 只读 answerText、不执行命令。
            "outputCommands": [
                {"type": "DISPLAY", "payload": {"action": "SHOW_TEXT", "text": "FAKE_ONLY"}}
            ],
        }
        if scenario == "invalid_result":
            result["tenantId"] = "mismatched-tenant"
        _stored[request_id] = (body, result)
        return result


if __name__ == "__main__":
    _token()  # 在绑定端口前失败，避免无 Token 的 Fake 意外运行。
    uvicorn.run(app, host="127.0.0.1", port=8780, log_level="info")
