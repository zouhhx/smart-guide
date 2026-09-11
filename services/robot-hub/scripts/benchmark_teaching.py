"""Fake-only concurrent HTTP smoke benchmark. Does not measure RobotOS or real 61."""
import asyncio
import json
import math
import time
from aiohttp import web
from scripts.fake_teaching_61 import Fake61
from app.engine.legacy_qa import LegacyQAAdapter, LegacyQASettings
from app.engine.legacy_teaching import LegacyTeachingSettings, LegacyDraftAdapter, TeachingSession, freeze_draft_submission
from app.engine.teaching_runtime import AnalysisAdapter


def percentiles(values):
    values = sorted(values)
    return {f"p{p}_ms": round(values[max(0, math.ceil(len(values) * p / 100) - 1)], 3) for p in (50, 95, 99)}


async def main():
    fake = Fake61()
    runner = web.AppRunner(fake.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    settings = LegacyTeachingSettings(True, base + "/api/knowledge/qa-drafts", "FAKE", fake.token,
        120, 10, 2, 50, 4000, analyze_endpoint=base + "/api/knowledge/teaching/analyze",
        analyze_bearer_token=fake.token)
    qa = LegacyQAAdapter(LegacyQASettings("active", base + "/api/asr-text", "2", "microphone", 2))
    timings = {"qa_baseline": [], "qa_during_teaching": [], "analysis": [], "draft": []}
    async def measure(bucket, awaitable):
        start = time.perf_counter()
        await awaitable
        timings[bucket].append((time.perf_counter() - start) * 1000)
    async def qa_series(bucket):
        for _ in range(30):
            await measure(bucket, qa.ask("问题", require_structured=True))
    async def teaching(i):
        s = TeachingSession.create("问题")
        s.fragments = ["工作人员现场补充"]; s.reception_id = "session-" + str(i); s.terminal_reason = "button"
        payload = dict(schemaVersion="1.0.0", analysisId="a-" + str(i), teachingTurnId=s.turn_id,
                       revision=1, robotId="fake-" + str(i), sessionId=s.reception_id, question=s.question,
                       accumulatedTranscript=s.answer, fragmentCount=1, isFinal=True, completionReason="button")
        adapter = LegacyDraftAdapter(settings)
        try:
            await asyncio.gather(measure("analysis", AnalysisAdapter(settings).analyze(payload)),
                                 measure("draft", adapter.submit(freeze_draft_submission(s, "fake-" + str(i)))))
        finally:
            await adapter.close()
    try:
        await qa_series("qa_baseline")
        await asyncio.gather(qa_series("qa_during_teaching"), *(teaching(i) for i in range(8)))
        print(json.dumps({"environment": "Windows loopback Fake61, 8 concurrent teaching turns, 30 QA requests per phase",
                          "results": {k: percentiles(v) for k, v in timings.items()},
                          "drafts": len(fake.drafts)}, ensure_ascii=True))
    finally:
        await qa.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
