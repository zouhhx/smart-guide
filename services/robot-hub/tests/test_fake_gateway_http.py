import json

import httpx
import pytest

from app.engine.gateway_qa import GatewayQAAdapter
from scripts import fake_gateway
from tests.test_gateway_qa import settings


@pytest.mark.asyncio
async def test_runnable_fake_gateway_health_replay_and_conflict(monkeypatch):
    token = "a" * 32
    monkeypatch.setenv("V5_GATEWAY_CLIENT_TOKEN", token)
    monkeypatch.setenv("FAKE_GATEWAY_SCENARIO", "success")
    fake_gateway._stored.clear()

    app_transport = httpx.ASGITransport(app=fake_gateway.app)
    async with httpx.AsyncClient(transport=app_transport, base_url="http://fake") as client:
        health = await client.get("/health")
        ready = await client.get("/ready")
        assert health.json()["status"] == "UP"
        assert ready.json()["core"] == "UP"

        builder = GatewayQAAdapter(settings())
        try:
            event, payload = builder.build_input_event("云猴是什么？", "robot-001", current_unit_id=None)
        finally:
            await builder.close()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        first = await client.post("/v1/input-events", content=payload, headers=headers)
        replay = await client.post("/v1/input-events", content=payload, headers=headers)
        assert first.status_code == 200
        assert replay.status_code == 200
        assert first.json() == replay.json()
        assert first.json()["answerText"].startswith("Fake Core")

        conflict_event = dict(event)
        conflict_event["text"] = "不同内容"
        conflict_payload = json.dumps(
            conflict_event, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        conflict = await client.post(
            "/v1/input-events", content=conflict_payload, headers=headers
        )
        assert conflict.status_code == 409
        assert conflict.json()["retryable"] is False
