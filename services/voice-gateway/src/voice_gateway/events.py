from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


BEIJING_FIXED_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def format_beijing(value: datetime) -> str:
    return value.astimezone(BEIJING_FIXED_TIMEZONE).isoformat(timespec="milliseconds")


class EventWriter:
    DISK_RETRY_SECONDS = 60.0
    MAX_FILE_BYTES = 8 * 1024 * 1024
    LOG_TOTAL_MAX_BYTES = 128 * 1024 * 1024
    RESULTS_TOTAL_MAX_BYTES = 32 * 1024 * 1024

    def __init__(
        self,
        directory: Path,
        level: str = "INFO",
        retention_days: int = 30,
        results_directory: Path | None = None,
    ) -> None:
        self.directory = directory
        self.results_directory = results_directory or directory / "results"
        self.retention_days = retention_days
        self._date_suffix = ""
        self._disk_retry_after = 0.0
        self._disk_ready = False
        self._disk_lock = threading.Lock()
        self.latest_result_path = self.results_directory / "latest-asr-result.json"
        self.logger = logging.getLogger("voice_gateway")
        self.logger.setLevel(getattr(logging, level, logging.INFO))
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.logger.addHandler(handler)
        self._set_paths(datetime.now(BEIJING_FIXED_TIMEZONE))
        try:
            self._prepare_directories_and_prune()
            self._disk_ready = True
        except OSError as exc:
            self._disk_ready = False
            self._disk_retry_after = time.monotonic() + self.DISK_RETRY_SECONDS
            self.logger.warning("事件日志暂不可写，将稍后重试：%s", exc)

    def _set_paths(self, local_now: datetime) -> None:
        date_suffix = f"{local_now:%Y%m%d}"
        self._date_suffix = date_suffix
        self.path = self.directory / f"voice-gateway-{date_suffix}.jsonl"
        self.human_path = self.directory / f"voice-gateway-{date_suffix}.log"
        self.results_path = (
            self.results_directory / f"asr-results-{date_suffix}.jsonl"
        )

    def _prepare_directories_and_prune(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.results_directory.mkdir(parents=True, exist_ok=True)
        self._prune(
            self.directory,
            self.retention_days,
            "voice-gateway-*",
        )
        self._prune(
            self.results_directory,
            self.retention_days,
            "asr-results-*",
        )
        self._prune_capacity(
            self.directory,
            "voice-gateway-*",
            self.LOG_TOTAL_MAX_BYTES,
            {self.path, self.human_path},
            2 * self.MAX_FILE_BYTES,
        )
        self._prune_capacity(
            self.results_directory,
            "asr-results-*",
            self.RESULTS_TOTAL_MAX_BYTES,
            {self.results_path, self.latest_result_path},
            self.MAX_FILE_BYTES,
        )

    def _ensure_current_date(self, local_now: datetime) -> None:
        date_suffix = f"{local_now:%Y%m%d}"
        if date_suffix != self._date_suffix:
            self._set_paths(local_now)
            self._disk_ready = False
        if not self._disk_ready:
            self._prepare_directories_and_prune()
            self._disk_ready = True

    @staticmethod
    def _prune(directory: Path, retention_days: int, pattern: str) -> None:
        cutoff = datetime.now() - timedelta(days=retention_days)
        for path in directory.glob(pattern):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink()
            except OSError:
                # 日志清理失败不能阻止语音主链路启动。
                continue

    @staticmethod
    def _prune_capacity(
        directory: Path,
        pattern: str,
        maximum_bytes: int,
        protected: set[Path],
        reserve_bytes: int = 0,
    ) -> None:
        if maximum_bytes <= 0:
            return
        target_bytes = max(0, maximum_bytes - max(0, reserve_bytes))
        protected_paths = {path.resolve() for path in protected}
        entries: list[tuple[float, int, Path]] = []
        total_bytes = 0
        for path in directory.glob(pattern):
            try:
                stat = path.stat()
            except OSError:
                continue
            total_bytes += stat.st_size
            if path.resolve() not in protected_paths:
                entries.append((stat.st_mtime, stat.st_size, path))
        for _, size, path in sorted(
            entries, key=lambda item: (item[0], str(item[2]))
        ):
            if total_bytes <= target_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total_bytes -= size

    def _append_with_rotation(
        self,
        path: Path,
        text: str,
        *,
        capacity_directory: Path,
        capacity_pattern: str,
        capacity_bytes: int,
        capacity_reserve_bytes: int,
        protected: set[Path],
    ) -> None:
        payload = (text + "\n").encode("utf-8")
        try:
            current_size = path.stat().st_size
        except FileNotFoundError:
            current_size = 0
        if (
            current_size > 0
            and current_size + len(payload) > self.MAX_FILE_BYTES
        ):
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            rotated = path.with_name(
                f"{path.name}.{timestamp}-{uuid.uuid4().hex[:8]}"
            )
            path.replace(rotated)
            self._prune_capacity(
                capacity_directory,
                capacity_pattern,
                capacity_bytes,
                protected,
                capacity_reserve_bytes,
            )
        with path.open("ab") as output:
            output.write(payload)

    def emit(self, event_name: str, **fields: Any) -> dict[str, Any]:
        now = datetime.now(UTC)
        reserved = {
            "log_schema_version",
            "event_type",
            "timestamp_utc",
            "timestamp_beijing",
            "timezone",
        }
        safe_fields: dict[str, Any] = {}
        for key, value in fields.items():
            safe_fields[f"source_{key}" if key in reserved else key] = value
        record = {
            "log_schema_version": "1.1",
            "event_type": event_name,
            "timestamp_utc": format_utc(now),
            "timestamp_beijing": format_beijing(now),
            "timezone": "Asia/Shanghai",
            **safe_fields,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        # stdout is the live control channel used by the Web manager. Emit it
        # before best-effort disk logging so a full/unavailable disk cannot
        # block KWS/VAD/ASR state propagation.
        self.logger.info(
            "%s %s", event_name, json.dumps(safe_fields, ensure_ascii=False)
        )
        monotonic_now = time.monotonic()
        if monotonic_now >= self._disk_retry_after:
            try:
                with self._disk_lock:
                    self._ensure_current_date(
                        now.astimezone(BEIJING_FIXED_TIMEZONE)
                    )
                    log_paths = {self.path, self.human_path}
                    self._append_with_rotation(
                        self.path,
                        line,
                        capacity_directory=self.directory,
                        capacity_pattern="voice-gateway-*",
                        capacity_bytes=self.LOG_TOTAL_MAX_BYTES,
                        capacity_reserve_bytes=2 * self.MAX_FILE_BYTES,
                        protected=log_paths,
                    )
                    self._append_with_rotation(
                        self.human_path,
                        self._human_line(record),
                        capacity_directory=self.directory,
                        capacity_pattern="voice-gateway-*",
                        capacity_bytes=self.LOG_TOTAL_MAX_BYTES,
                        capacity_reserve_bytes=2 * self.MAX_FILE_BYTES,
                        protected=log_paths,
                    )
                    if event_name == "asr_result":
                        self._append_with_rotation(
                            self.results_path,
                            line,
                            capacity_directory=self.results_directory,
                            capacity_pattern="asr-results-*",
                            capacity_bytes=self.RESULTS_TOTAL_MAX_BYTES,
                            capacity_reserve_bytes=self.MAX_FILE_BYTES,
                            protected={
                                self.results_path,
                                self.latest_result_path,
                            },
                        )
                        temporary = self.latest_result_path.with_name(
                            f".{self.latest_result_path.name}."
                            f"{uuid.uuid4().hex}.tmp"
                        )
                        try:
                            temporary.write_text(
                                json.dumps(
                                    record,
                                    ensure_ascii=False,
                                    indent=2,
                                )
                                + "\n",
                                encoding="utf-8",
                            )
                            temporary.replace(self.latest_result_path)
                        finally:
                            try:
                                temporary.unlink(missing_ok=True)
                            except OSError:
                                pass
                self._disk_retry_after = 0.0
            except OSError as exc:
                self._disk_ready = False
                self._disk_retry_after = (
                    time.monotonic() + self.DISK_RETRY_SECONDS
                )
                self.logger.warning(
                    "事件日志写入失败，语音链路继续运行，稍后重试：%s",
                    exc,
                )
        return record

    @staticmethod
    def _human_line(record: dict[str, Any]) -> str:
        timestamp = str(record["timestamp_beijing"])
        event_type = str(record["event_type"])
        if event_type == "asr_result":
            return (
                f"{timestamp} [ASR] text={json.dumps(record.get('text', ''), ensure_ascii=False)} "
                f"language={record.get('language', '')} "
                f"duration_ms={record.get('speech_duration_ms', 0)} "
                f"level={record.get('level_status', '')} "
                f"rms={record.get('rms_dbfs', '')}dBFS "
                f"peak={record.get('peak_dbfs', '')}dBFS"
            )
        if event_type == "wake_detected":
            return f"{timestamp} [WAKE] keyword={record.get('keyword', '')}"
        if event_type == "speech_wait_timeout":
            return f"{timestamp} [TIMEOUT] wake后未检测到问题语音"
        if event_type == "speech_segment":
            return (
                f"{timestamp} [SPEECH] duration_ms={record.get('duration_ms', 0)} "
                f"level={record.get('level_status', '')} "
                f"rms={record.get('rms_dbfs', '')}dBFS "
                f"peak={record.get('peak_dbfs', '')}dBFS"
            )
        if event_type == "mic_started":
            return (
                f"{timestamp} [START] device={record.get('device', '')} "
                f"capture={record.get('capture_sample_rate', '')}Hz"
            )
        if event_type == "mic_stopped":
            return (
                f"{timestamp} [STOP] dropped_blocks={record.get('dropped_blocks', 0)}"
            )
        return f"{timestamp} [{event_type.upper()}] {json.dumps(record, ensure_ascii=False)}"
