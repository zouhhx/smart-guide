import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from voice_gateway.events import BEIJING_FIXED_TIMEZONE, EventWriter


class EventWriterTests(unittest.TestCase):
    def test_machine_human_and_result_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = EventWriter(
                root / "logs",
                results_directory=root / "results",
            )
            record = writer.emit(
                "asr_result",
                event="<|Speech|>",
                event_type="must-not-overwrite",
                text="介绍一下这个项目。",
                language="zh",
                speech_duration_ms=1200,
                level_status="normal",
                rms_dbfs=-38.0,
                peak_dbfs=-20.0,
            )

            self.assertEqual(record["event_type"], "asr_result")
            self.assertEqual(record["source_event_type"], "must-not-overwrite")
            self.assertEqual(record["event"], "<|Speech|>")
            self.assertTrue(record["timestamp_utc"].endswith("Z"))
            self.assertTrue(record["timestamp_beijing"].endswith("+08:00"))
            self.assertEqual(record["timezone"], "Asia/Shanghai")

            machine = json.loads(writer.path.read_text(encoding="utf-8").strip())
            result = json.loads(
                writer.results_path.read_text(encoding="utf-8").strip()
            )
            latest = json.loads(
                writer.latest_result_path.read_text(encoding="utf-8").strip()
            )
            human = writer.human_path.read_text(encoding="utf-8")
            self.assertEqual(machine, result)
            self.assertEqual(result, latest)
            self.assertIn("[ASR]", human)
            self.assertIn("介绍一下这个项目。", human)

    def test_running_process_switches_to_current_date_lazily(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = EventWriter(root / "logs")
            writer._set_paths(
                datetime(2000, 1, 1, tzinfo=BEIJING_FIXED_TIMEZONE)
            )

            writer.emit("wake_detected", keyword="你好思德")

            current_suffix = datetime.now(BEIJING_FIXED_TIMEZONE).strftime(
                "%Y%m%d"
            )
            self.assertEqual(writer._date_suffix, current_suffix)
            self.assertTrue(
                (
                    root
                    / "logs"
                    / f"voice-gateway-{current_suffix}.jsonl"
                ).is_file()
            )

    def test_disk_failure_does_not_break_event_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = EventWriter(root / "logs")
            writer.path = root / "missing" / "events.jsonl"

            record = writer.emit(
                "wake_detected",
                keyword="你好思德",
            )

            self.assertEqual(record["event_type"], "wake_detected")
            self.assertGreater(writer._disk_retry_after, 0)
            self.assertFalse(writer._disk_ready)

    def test_initial_directory_failure_recovers_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = EventWriter._prepare_directories_and_prune
            attempts = 0

            def fail_once(writer):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("temporary disk failure")
                return original(writer)

            with patch.object(
                EventWriter,
                "_prepare_directories_and_prune",
                autospec=True,
                side_effect=fail_once,
            ):
                writer = EventWriter(root / "logs")
                self.assertFalse(writer._disk_ready)
                writer._disk_retry_after = 0.0
                writer.emit("wake_detected", keyword="你好思德")

            self.assertTrue(writer._disk_ready)
            self.assertTrue(writer.path.is_file())
            self.assertEqual(attempts, 2)

    def test_large_daily_log_rotates_without_per_event_scanning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = EventWriter(root / "logs")
            writer.emit("wake_detected", keyword="你好思德")
            first_size = writer.path.stat().st_size
            writer.MAX_FILE_BYTES = first_size + 8

            writer.emit("wake_detected", keyword="你好思德")

            rotated = list(
                writer.directory.glob(
                    f"{writer.path.name}.*"
                )
            )
            self.assertEqual(len(rotated), 1)
            self.assertTrue(writer.path.is_file())
            self.assertLessEqual(rotated[0].stat().st_size, first_size + 8)

    def test_capacity_pruning_removes_oldest_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = EventWriter(root / "logs")
            old = writer.directory / "voice-gateway-old.jsonl.1"
            newer = writer.directory / "voice-gateway-new.jsonl.1"
            old.write_bytes(b"a" * 80)
            newer.write_bytes(b"b" * 80)
            old.touch()
            newer.touch()
            old_stat = old.stat()
            newer_stat = newer.stat()
            old_time = min(old_stat.st_mtime, newer_stat.st_mtime) - 10
            os.utime(old, (old_time, old_time))
            writer.MAX_FILE_BYTES = 10
            writer.LOG_TOTAL_MAX_BYTES = 100

            writer._prepare_directories_and_prune()

            self.assertFalse(old.exists())
            self.assertTrue(newer.exists())

    def test_continuous_rotation_stays_within_total_log_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = EventWriter(root / "logs")
            writer.emit("wake_detected", keyword="你好思德")
            writer.MAX_FILE_BYTES = (
                max(
                    writer.path.stat().st_size,
                    writer.human_path.stat().st_size,
                )
                * 2
                + 16
            )
            writer.LOG_TOTAL_MAX_BYTES = writer.MAX_FILE_BYTES * 6

            with patch.object(writer.logger, "info"):
                for _ in range(40):
                    writer.emit("wake_detected", keyword="你好思德")

            total = sum(
                path.stat().st_size
                for path in writer.directory.glob("voice-gateway-*")
            )
            self.assertLessEqual(total, writer.LOG_TOTAL_MAX_BYTES)

    def test_startup_pruning_reserves_space_for_active_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_dir = root / "logs"
            log_dir.mkdir()
            for index in range(6):
                (log_dir / f"voice-gateway-old-{index}.jsonl").write_bytes(
                    b"x" * 300
                )

            with (
                patch.object(EventWriter, "MAX_FILE_BYTES", 300),
                patch.object(EventWriter, "LOG_TOTAL_MAX_BYTES", 1800),
            ):
                writer = EventWriter(log_dir)
                writer.emit("wake_detected", keyword="你好思德")

                total = sum(
                    path.stat().st_size
                    for path in log_dir.glob("voice-gateway-*")
                )
                self.assertLessEqual(total, writer.LOG_TOTAL_MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
