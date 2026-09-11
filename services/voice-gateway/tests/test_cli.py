import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from voice_gateway import cli
from voice_gateway.audio import AudioError


def fake_config(sample_rate=16000):
    return SimpleNamespace(
        audio=SimpleNamespace(
            input_device=7,
            sample_rate=sample_rate,
            frame_samples=512,
            capture_sample_rate=None,
        ),
        activation=SimpleNamespace(
            mode="kws",
            level_threshold_dbfs=-35.0,
        ),
        kws=SimpleNamespace(keyword="你好思德"),
    )


class FakeEvents:
    def __init__(self):
        self.records = []

    def emit(self, name, **fields):
        self.records.append((name, fields))
        return {"event_type": name, **fields}


class FakePipeline:
    def __init__(self):
        self.events = FakeEvents()
        self.state = SimpleNamespace(value="IDLE")
        self.reset_calls = 0
        self.frames_seen = 0
        self.activation_mode = "level"
        self.level_threshold_dbfs = -35.0

    def reset_runtime(self):
        self.reset_calls += 1

    def run(self, frames, flush_at_end=False):
        del flush_at_end
        for _ in frames:
            self.frames_seen += 1
        if False:
            yield None

    def set_level_threshold_dbfs(self, value):
        self.level_threshold_dbfs = float(value)
        return self.level_threshold_dbfs


class FakeSource:
    def __init__(self, mode="normal", frames=()):
        self.mode = mode
        self._frames = list(frames)
        self.capture_rate = 48000
        self.dropped_blocks = 0
        self.callback_status_events = 0
        self.last_callback_status = ""
        self.discard_calls = 0
        self.exit_exc_type = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        del exc, traceback
        self.exit_exc_type = exc_type
        return False

    def frames(self):
        if self.mode == "audio_error":
            raise AudioError("device disconnected")
        for frame in self._frames:
            yield frame
        if self.mode == "interrupt":
            raise KeyboardInterrupt

    def discard_pending(self):
        self.discard_calls += 1
        return 0


class CommandMicTests(unittest.TestCase):
    def test_parent_control_updates_level_threshold(self):
        pipeline = FakePipeline()
        output = io.StringIO()

        with redirect_stdout(output):
            cli._handle_parent_control_line(
                pipeline,
                b'{"command":"set_level_threshold","threshold_dbfs":-42}\n',
            )

        self.assertEqual(pipeline.level_threshold_dbfs, -42.0)
        self.assertIn('"level_threshold_dbfs":-42.0', output.getvalue())

    def test_heartbeat_environment_is_bounded(self):
        for raw, expected in (
            ("", 0.0),
            ("invalid", 0.0),
            ("nan", 0.0),
            ("-1", 0.0),
            ("0.001", 5.0),
            ("10", 10.0),
            ("90", 60.0),
        ):
            with (
                self.subTest(raw=raw),
                patch.dict(
                    cli.os.environ,
                    {"VOICE_GATEWAY_HEARTBEAT_SECONDS": raw},
                ),
            ):
                self.assertEqual(
                    cli._heartbeat_interval_seconds(),
                    expected,
                )

    def test_reconnects_without_reloading_pipeline(self):
        config = fake_config()
        pipeline = FakePipeline()
        stalled = FakeSource(mode="audio_error")
        recovered = FakeSource(
            mode="interrupt",
            frames=[np.zeros(512, dtype=np.float32)],
        )
        trace = []
        attempts = 0

        def source_factory(**kwargs):
            nonlocal attempts
            del kwargs
            attempts += 1
            trace.append(f"open-{attempts}")
            if attempts == 1:
                raise AudioError("device unavailable")
            return stalled if attempts == 2 else recovered

        original_reset = pipeline.reset_runtime

        def traced_reset():
            trace.append("reset")
            original_reset()

        pipeline.reset_runtime = traced_reset

        with (
            patch.object(cli, "_start_parent_liveness_watch"),
            patch.object(
                cli, "_pipeline", return_value=pipeline
            ) as make_pipeline,
            patch.object(
                cli, "MicrophoneSource", side_effect=source_factory
            ),
            patch.object(cli.time, "sleep") as sleep,
            patch.object(
                cli, "_heartbeat_interval_seconds", return_value=0.0
            ),
            redirect_stdout(io.StringIO()),
        ):
            code = cli.command_mic(config, None, False, 0)

        self.assertEqual(code, 0)
        make_pipeline.assert_called_once_with(config, False)
        self.assertEqual(attempts, 3)
        self.assertEqual(
            [item.args[0] for item in sleep.call_args_list],
            [1.0, 2.0],
        )
        for index, item in enumerate(trace):
            if item.startswith("open-"):
                self.assertGreater(index, 0)
                self.assertEqual(trace[index - 1], "reset")

        waits = [
            fields
            for name, fields in pipeline.events.records
            if name == "mic_reconnect_wait"
        ]
        self.assertEqual(
            [item["retry_in_seconds"] for item in waits],
            [1.0, 2.0],
        )
        self.assertEqual(
            [item["reconnect_count"] for item in waits],
            [1, 2],
        )
        starts = [
            fields
            for name, fields in pipeline.events.records
            if name == "mic_started"
        ]
        self.assertEqual(len(starts), 2)
        stopped = [
            fields
            for name, fields in pipeline.events.records
            if name == "mic_stopped"
        ]
        self.assertEqual(
            [item["reconnecting"] for item in stopped],
            [True, False],
        )

    def test_heartbeat_is_limited_by_consumed_samples(self):
        config = fake_config(sample_rate=1024)
        pipeline = FakePipeline()
        source = FakeSource(
            frames=[
                np.zeros(512, dtype=np.float32)
                for _ in range(5)
            ]
        )
        output = io.StringIO()

        with (
            patch.object(cli, "_start_parent_liveness_watch"),
            patch.object(cli, "_pipeline", return_value=pipeline),
            patch.object(cli, "MicrophoneSource", return_value=source),
            patch.object(
                cli, "_heartbeat_interval_seconds", return_value=1.0
            ),
            patch.object(cli.time, "sleep") as sleep,
            redirect_stdout(output),
        ):
            code = cli.command_mic(config, None, False, 0)

        self.assertEqual(code, 0)
        sleep.assert_not_called()
        heartbeat_lines = [
            line
            for line in output.getvalue().splitlines()
            if line.startswith("audio_heartbeat ")
        ]
        self.assertEqual(len(heartbeat_lines), 2)
        payload = json.loads(heartbeat_lines[0].split(" ", 1)[1])
        self.assertEqual(payload["state"], "IDLE")
        self.assertTrue(
            {
                "dropped_blocks",
                "callback_status_events",
                "discarded_stale_blocks",
                "reconnect_count",
            }.issubset(payload)
        )
        self.assertFalse(
            any(
                name == "audio_heartbeat"
                for name, _ in pipeline.events.records
            )
        )

    def test_finite_run_fails_when_device_never_connected(self):
        config = fake_config()
        pipeline = FakePipeline()

        class FakeClock:
            def __init__(self):
                self.now = 0.0

            def monotonic(self):
                return self.now

            def sleep(self, seconds):
                self.now += seconds

        clock = FakeClock()

        with (
            patch.object(cli, "_start_parent_liveness_watch"),
            patch.object(cli, "_pipeline", return_value=pipeline),
            patch.object(
                cli,
                "MicrophoneSource",
                side_effect=AudioError("device unavailable"),
            ) as source_factory,
            patch.object(
                cli.time, "monotonic", side_effect=clock.monotonic
            ),
            patch.object(cli.time, "sleep", side_effect=clock.sleep),
            patch.object(
                cli, "_heartbeat_interval_seconds", return_value=0.0
            ),
            redirect_stdout(io.StringIO()),
        ):
            code = cli.command_mic(config, None, False, 1.0)

        self.assertNotEqual(code, 0)
        self.assertEqual(source_factory.call_count, 1)
        self.assertFalse(
            any(
                name == "mic_started"
                for name, _ in pipeline.events.records
            )
        )

    def test_finite_run_fails_when_stream_yields_no_audio(self):
        config = fake_config()
        pipeline = FakePipeline()
        source = FakeSource(mode="audio_error")

        class FakeClock:
            def __init__(self):
                self.now = 0.0

            def monotonic(self):
                return self.now

            def sleep(self, seconds):
                self.now += seconds

        clock = FakeClock()
        with (
            patch.object(cli, "_start_parent_liveness_watch"),
            patch.object(cli, "_pipeline", return_value=pipeline),
            patch.object(cli, "MicrophoneSource", return_value=source),
            patch.object(
                cli.time, "monotonic", side_effect=clock.monotonic
            ),
            patch.object(cli.time, "sleep", side_effect=clock.sleep),
            patch.object(
                cli, "_heartbeat_interval_seconds", return_value=0.0
            ),
            redirect_stdout(io.StringIO()),
        ):
            code = cli.command_mic(config, None, False, 1.0)

        self.assertNotEqual(code, 0)
        self.assertEqual(pipeline.frames_seen, 0)

    def test_non_audio_pipeline_error_is_not_retried(self):
        config = fake_config()
        pipeline = FakePipeline()
        source = FakeSource(
            frames=[np.zeros(512, dtype=np.float32)]
        )

        def crashing_run(frames, flush_at_end=False):
            del flush_at_end
            next(iter(frames))
            raise RuntimeError("inference bug")
            yield None

        pipeline.run = crashing_run

        with (
            patch.object(cli, "_start_parent_liveness_watch"),
            patch.object(cli, "_pipeline", return_value=pipeline),
            patch.object(
                cli, "MicrophoneSource", return_value=source
            ) as source_factory,
            patch.object(cli.time, "sleep") as sleep,
            patch.object(
                cli, "_heartbeat_interval_seconds", return_value=0.0
            ),
            redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "inference bug"),
        ):
            cli.command_mic(config, None, False, 0)

        self.assertEqual(source_factory.call_count, 1)
        sleep.assert_not_called()
        self.assertIs(source.exit_exc_type, RuntimeError)
        self.assertFalse(
            any(
                name == "mic_reconnect_wait"
                for name, _ in pipeline.events.records
            )
        )


if __name__ == "__main__":
    unittest.main()
