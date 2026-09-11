"""Local Fake 61: no production calls, no TTS, drafts remain pending review."""
import asyncio
import json
from aiohttp import web


class Fake61:
    def __init__(self, token="fake-teaching-token"):
        self.token = token
        self.drafts = {}
        self.bodies = []
        self.analysis_calls = []
        self.qa_calls = []
        self.draft_errors = []
        self.analysis_delay = 0
        self.draft_delay = 0
        self.analysis_overrides = {}
        self.qa_response = {"answerStatus": "ABSTAINED", "reason": "NO_KNOWLEDGE", "teachable": True, "reply": ""}
        self.app = web.Application(client_max_size=1024 * 1024)
        self.app.router.add_post("/api/asr-text", self.qa)
        self.app.router.add_post("/api/knowledge/teaching/analyze", self.analyze)
        self.app.router.add_post("/api/knowledge/qa-drafts", self.draft)

    def auth(self, request):
        if request.headers.get("Authorization") != "Bearer " + self.token:
            raise web.HTTPUnauthorized()

    async def qa(self, request):
        self.qa_calls.append(await request.json())
        return web.json_response(self.qa_response)

    async def analyze(self, request):
        self.auth(request)
        payload = await request.json()
        self.analysis_calls.append(payload)
        await asyncio.sleep(self.analysis_delay)
        result = {k: payload[k] for k in ("schemaVersion", "analysisId", "teachingTurnId", "revision")}
        result.update(status="OK", relevance="RELATED", completeness="SUFFICIENT", factuality="UNVERIFIED",
                      canReplay=True, replayAnswer=payload["accumulatedTranscript"], warnings=[], evidenceRefs=[])
        result.update(self.analysis_overrides)
        return web.json_response(result)

    async def draft(self, request):
        self.auth(request)
        body = await request.read()
        self.bodies.append(body)
        await asyncio.sleep(self.draft_delay)
        if self.draft_errors:
            status, payload = self.draft_errors.pop(0)
            if isinstance(payload, str):
                return web.Response(status=status, text=payload)
            return web.json_response(payload, status=status)
        payload = json.loads(body)
        if not payload.get("question") or not payload.get("rawAnswer"):
            raise web.HTTPUnprocessableEntity()
        key = payload["submissionId"]
        if key in self.drafts:
            previous_body, result = self.drafts[key]
            if body != previous_body:
                return web.json_response({"retryable": False}, status=409)
        else:
            result = {"submissionId": key, "draftId": "draft-" + str(len(self.drafts) + 1), "status": "PENDING_REVIEW"}
            self.drafts[key] = (body, result)
        return web.json_response(result)


if __name__ == "__main__":
    web.run_app(Fake61().app, host="127.0.0.1", port=3001)
