import tempfile
import unittest
from pathlib import Path

import numpy as np

from voice_gateway.engines import AsrOutput, SpeechSegment
from voice_gateway.events import EventWriter
from voice_gateway.pipeline import VoicePipeline


class FakeKws:
    def __init__(self):
        self.calls = 0

    def process(self, samples):
        self.calls += 1
        return "你好思德" if self.calls == 2 else None

    def reset(self):
        pass


class FakeVad:
    def __init__(self):
        self.calls = 0

    def process(self, samples):
        self.calls += 1
        if self.calls == 2:
            return [SpeechSegment(np.ones(16000, dtype=np.float32) * 0.1, 0)]
        return []

    def flush(self):
        return []

    def reset(self):
        self.calls = 0

    def is_speech_detected(self):
        return False


class FakeAsr:
    def transcribe(self, samples):
        return AsrOutput("请介绍这个展项", 123, "zh", "", "")


class SilentVad(FakeVad):
    def process(self, samples):
        self.calls += 1
        return []


class MemoryEvents:
    def __init__(self):
        self.records = []

    def emit(self, name, **fields):
        self.records.append((name, fields))
        return {"event_type": name, **fields}


class PipelineTests(unittest.TestCase):
    def test_level_mode_ignores_audio_below_threshold(self):
        events = MemoryEvents()
        vad = SilentVad()
        pipeline = VoicePipeline(
            device_id="test",
            sample_rate=16000,
            keyword="你好思德",
            kws=None,
            vad=vad,
            asr=FakeAsr(),
            events=events,
            cooldown_ms=0,
            activation_mode="level",
            level_threshold_dbfs=-35,
            level_attack_ms=64,
        )

        results = list(
            pipeline.run(
                [np.ones(512, dtype=np.float32) * 0.001 for _ in range(8)],
                flush_at_end=False,
            )
        )

        self.assertEqual(results, [])
        self.assertEqual(vad.calls, 0)
        self.assertFalse(any(name == "level_triggered" for name, _ in events.records))

    def test_level_mode_triggers_vad_and_returns_to_idle(self):
        events = MemoryEvents()
        pipeline = VoicePipeline(
            device_id="test",
            sample_rate=16000,
            keyword="你好思德",
            kws=None,
            vad=FakeVad(),
            asr=FakeAsr(),
            events=events,
            cooldown_ms=0,
            activation_mode="level",
            level_threshold_dbfs=-35,
            level_attack_ms=64,
        )

        results = list(
            pipeline.run(
                [np.ones(512, dtype=np.float32) * 0.1 for _ in range(3)],
                flush_at_end=False,
            )
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].wake_word, "")
        self.assertEqual(results[0].text, "请介绍这个展项")
        self.assertEqual(pipeline.state.value, "IDLE")
        self.assertTrue(any(name == "level_triggered" for name, _ in events.records))
        self.assertFalse(any(name == "wake_detected" for name, _ in events.records))

    def test_level_threshold_can_change_without_resetting_models(self):
        pipeline = VoicePipeline(
            device_id="test",
            sample_rate=16000,
            keyword="你好思德",
            kws=None,
            vad=FakeVad(),
            asr=FakeAsr(),
            events=MemoryEvents(),
            cooldown_ms=0,
            activation_mode="level",
            level_threshold_dbfs=-35,
            level_attack_ms=64,
        )
        frame = np.ones(512, dtype=np.float32) * 0.01
        self.assertEqual(
            list(pipeline.run([frame, frame], flush_at_end=False)),
            [],
        )

        self.assertEqual(pipeline.set_level_threshold_dbfs(-45), -45)
        results = list(pipeline.run([frame, frame], flush_at_end=False))

        self.assertEqual(len(results), 1)
        self.assertEqual(pipeline.level_threshold_dbfs, -45)
        with self.assertRaises(ValueError):
            pipeline.set_level_threshold_dbfs(-5)

    def test_wake_vad_asr_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = VoicePipeline(
                device_id="test",
                sample_rate=16000,
                keyword="你好思德",
                kws=FakeKws(),
                vad=FakeVad(),
                asr=FakeAsr(),
                events=EventWriter(Path(directory)),
                cooldown_ms=0,
            )
            frames = [np.zeros(512, dtype=np.float32) for _ in range(5)]
            results = list(pipeline.run(frames))
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].text, "请介绍这个展项")
            self.assertEqual(results[0].wake_word, "你好思德")
            self.assertEqual(results[0].status, "success")
            self.assertEqual(results[0].schema_version, "1.1")
            self.assertTrue(results[0].started_at_utc.endswith("Z"))
            self.assertTrue(results[0].started_at_beijing.endswith("+08:00"))
            self.assertEqual(results[0].audio_event, "")
            self.assertEqual(results[0].level_status, "normal")

    def test_returns_to_idle_when_no_speech_follows_wake(self):
        with tempfile.TemporaryDirectory() as directory:
            kws = FakeKws()
            pipeline = VoicePipeline(
                device_id="test",
                sample_rate=16000,
                keyword="你好思德",
                kws=kws,
                vad=SilentVad(),
                asr=FakeAsr(),
                events=EventWriter(Path(directory)),
                cooldown_ms=0,
                speech_wait_timeout_seconds=0.064,
            )
            frames = [np.zeros(512, dtype=np.float32) for _ in range(5)]
            results = list(pipeline.run(frames, flush_at_end=False))
            self.assertEqual(results, [])
            self.assertEqual(pipeline.state.value, "IDLE")

    def test_runtime_reset_clears_interrupted_session(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = VoicePipeline(
                device_id="test",
                sample_rate=16000,
                keyword="你好思德",
                kws=FakeKws(),
                vad=SilentVad(),
                asr=FakeAsr(),
                events=EventWriter(Path(directory)),
                cooldown_ms=0,
            )
            pipeline._start_session("你好思德")
            pipeline._waiting_samples = 4096

            pipeline.reset_runtime()

            self.assertEqual(pipeline.state.value, "IDLE")
            self.assertIsNone(pipeline._session_started)
            self.assertEqual(pipeline._wake_result, "")
            self.assertEqual(pipeline._waiting_samples, 0)


if __name__ == "__main__":
    unittest.main()
