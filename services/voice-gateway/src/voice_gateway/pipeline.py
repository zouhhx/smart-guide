from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import Lock
from typing import Iterable, Iterator, Protocol

import numpy as np

from .audio import assess_recognized_speech_levels, audio_levels
from .engines import AsrOutput, SpeechSegment
from .events import EventWriter, format_beijing, format_utc


class PipelineState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    RECOGNIZING = "RECOGNIZING"
    COOLDOWN = "COOLDOWN"


class KwsEngine(Protocol):
    def process(self, samples: np.ndarray) -> str | None: ...
    def reset(self) -> None: ...


class VadEngine(Protocol):
    def process(self, samples: np.ndarray) -> list[SpeechSegment]: ...
    def flush(self) -> list[SpeechSegment]: ...
    def reset(self) -> None: ...
    def is_speech_detected(self) -> bool: ...


class AsrEngine(Protocol):
    def transcribe(self, samples: np.ndarray) -> AsrOutput: ...


@dataclass(frozen=True)
class SessionResult:
    schema_version: str
    session_id: str
    device_id: str
    started_at_utc: str
    started_at_beijing: str
    ended_at_utc: str
    ended_at_beijing: str
    wake_word: str
    speech_duration_ms: int
    asr_duration_ms: int
    text: str
    language: str
    emotion: str
    audio_event: str
    rms_dbfs: float
    peak_dbfs: float
    clipping_ratio: float
    level_status: str
    level_message: str
    status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class VoicePipeline:
    def __init__(
        self,
        device_id: str,
        sample_rate: int,
        keyword: str,
        kws: KwsEngine | None,
        vad: VadEngine,
        asr: AsrEngine,
        events: EventWriter,
        bypass_kws: bool = False,
        cooldown_ms: int = 800,
        speech_wait_timeout_seconds: float = 8.0,
        activation_mode: str = "kws",
        level_threshold_dbfs: float = -35.0,
        level_attack_ms: int = 160,
        level_preroll_ms: int = 480,
    ) -> None:
        self.device_id = device_id
        self.sample_rate = sample_rate
        self.keyword = keyword
        self.kws = kws
        self.vad = vad
        self.asr = asr
        self.events = events
        self.bypass_kws = bypass_kws
        self.activation_mode = activation_mode.strip().lower()
        if self.activation_mode not in {"kws", "level"}:
            raise ValueError("activation_mode must be 'kws' or 'level'")
        if not -60.0 <= level_threshold_dbfs <= -15.0:
            raise ValueError("level_threshold_dbfs must be between -60 and -15")
        self._level_lock = Lock()
        self._level_threshold_dbfs = float(level_threshold_dbfs)
        self._level_attack_samples_required = max(
            1, round(level_attack_ms * sample_rate / 1000)
        )
        self._level_preroll_samples_limit = max(
            1, round(level_preroll_ms * sample_rate / 1000)
        )
        self._level_attack_samples = 0
        self._level_preroll: deque[np.ndarray] = deque()
        self._level_preroll_samples = 0
        self.cooldown_ms = cooldown_ms
        self.speech_wait_timeout_samples = round(
            speech_wait_timeout_seconds * sample_rate
        )
        self.state = PipelineState.LISTENING if bypass_kws else PipelineState.IDLE
        self._session_started: datetime | None = None
        self._wake_result = "BYPASS" if bypass_kws else ""
        self._waiting_samples = 0

    def run(
        self, frames: Iterable[np.ndarray], flush_at_end: bool = True
    ) -> Iterator[SessionResult]:
        for frame in frames:
            if self.state == PipelineState.IDLE:
                if self.activation_mode == "level" and not self.bypass_kws:
                    yield from self._process_level_idle_frame(frame)
                    continue
                if self.kws is None:
                    raise RuntimeError("启用KWS时必须提供KWS引擎")
                detected = self.kws.process(frame)
                if detected:
                    self._start_session(detected)
                continue

            if self.state == PipelineState.LISTENING:
                yield from self._process_listening_frame(frame)

        if flush_at_end and self.state == PipelineState.LISTENING:
            for segment in self.vad.flush():
                yield self._recognize(segment)

    def _start_session(self, detected: str) -> None:
        self._session_started = datetime.now(UTC)
        self._wake_result = detected
        self._waiting_samples = 0
        self.vad.reset()
        self.state = PipelineState.LISTENING
        self.events.emit("wake_detected", keyword=detected, state=self.state.value)

    def _process_level_idle_frame(
        self, frame: np.ndarray
    ) -> Iterator[SessionResult]:
        stored = np.asarray(frame, dtype=np.float32).copy()
        self._level_preroll.append(stored)
        self._level_preroll_samples += len(stored)
        while (
            self._level_preroll
            and self._level_preroll_samples > self._level_preroll_samples_limit
        ):
            removed = self._level_preroll.popleft()
            self._level_preroll_samples -= len(removed)

        rms_dbfs, peak_dbfs, _ = audio_levels(stored)
        with self._level_lock:
            threshold = self._level_threshold_dbfs
        if rms_dbfs >= threshold:
            self._level_attack_samples += len(stored)
        else:
            self._level_attack_samples = 0
            return
        if self._level_attack_samples < self._level_attack_samples_required:
            return

        buffered = list(self._level_preroll)
        self._session_started = datetime.now(UTC)
        self._wake_result = ""
        self._waiting_samples = 0
        self.vad.reset()
        self.state = PipelineState.LISTENING
        self.events.emit(
            "level_triggered",
            threshold_dbfs=round(threshold, 1),
            rms_dbfs=round(rms_dbfs, 2),
            peak_dbfs=round(peak_dbfs, 2),
            state=self.state.value,
        )
        self._clear_level_detector()
        for buffered_frame in buffered:
            yield from self._process_listening_frame(buffered_frame)
            if self.state != PipelineState.LISTENING:
                break

    def _process_listening_frame(
        self, frame: np.ndarray
    ) -> Iterator[SessionResult]:
        self._waiting_samples += len(frame)
        for segment in self.vad.process(frame):
            yield self._recognize(segment)
            if self.state != PipelineState.LISTENING:
                return
        if (
            not self.bypass_kws
            and self.state == PipelineState.LISTENING
            and self._waiting_samples >= self.speech_wait_timeout_samples
            and not self.vad.is_speech_detected()
        ):
            self.events.emit(
                "speech_wait_timeout",
                keyword=self._wake_result or self.keyword,
                activation_mode=self.activation_mode,
                timeout_ms=round(
                    self.speech_wait_timeout_samples * 1000 / self.sample_rate
                ),
            )
            self._return_to_idle()

    def _recognize(self, segment: SpeechSegment) -> SessionResult:
        self.state = PipelineState.RECOGNIZING
        session_id = uuid.uuid4().hex
        started = self._session_started or datetime.now(UTC)
        speech_ms = round(len(segment.samples) * 1000 / self.sample_rate)
        rms, peak, clipping = audio_levels(segment.samples)
        level_status, level_message = assess_recognized_speech_levels(
            rms, peak, clipping
        )
        self.events.emit(
            "speech_segment",
            session_id=session_id,
            duration_ms=speech_ms,
            rms_dbfs=round(rms, 2),
            peak_dbfs=round(peak, 2),
            clipping_ratio=round(clipping, 6),
            level_status=level_status,
            level_message=level_message,
        )
        output = self.asr.transcribe(segment.samples)
        ended = datetime.now(UTC)
        result = SessionResult(
            schema_version="1.1",
            session_id=session_id,
            device_id=self.device_id,
            started_at_utc=format_utc(started),
            started_at_beijing=format_beijing(started),
            ended_at_utc=format_utc(ended),
            ended_at_beijing=format_beijing(ended),
            wake_word=(
                ""
                if self.activation_mode == "level" and not self.bypass_kws
                else self._wake_result or self.keyword
            ),
            speech_duration_ms=speech_ms,
            asr_duration_ms=output.duration_ms,
            text=output.text,
            language=output.language,
            emotion=output.emotion,
            audio_event=output.audio_event,
            rms_dbfs=round(rms, 2),
            peak_dbfs=round(peak, 2),
            clipping_ratio=round(clipping, 6),
            level_status=level_status,
            level_message=level_message,
            status="success" if output.text else "empty_text",
        )
        self.events.emit("asr_result", **result.to_dict())
        self.state = PipelineState.COOLDOWN
        if self.cooldown_ms > 0:
            time.sleep(self.cooldown_ms / 1000.0)
        if self.bypass_kws:
            self.vad.reset()
            self.state = PipelineState.LISTENING
            self._session_started = datetime.now(UTC)
            self._wake_result = "BYPASS"
            self._waiting_samples = 0
        else:
            self._return_to_idle()
        return result

    def _return_to_idle(self) -> None:
        self.vad.reset()
        if self.kws is not None:
            self.kws.reset()
        self.state = PipelineState.IDLE
        self._session_started = None
        self._wake_result = ""
        self._waiting_samples = 0
        self._clear_level_detector()

    @property
    def level_threshold_dbfs(self) -> float:
        with self._level_lock:
            return self._level_threshold_dbfs

    def set_level_threshold_dbfs(self, value: float) -> float:
        threshold = float(value)
        if not -60.0 <= threshold <= -15.0:
            raise ValueError("level threshold must be between -60 and -15 dBFS")
        with self._level_lock:
            self._level_threshold_dbfs = threshold
        self._level_attack_samples = 0
        return threshold

    def _clear_level_detector(self) -> None:
        self._level_attack_samples = 0
        self._level_preroll.clear()
        self._level_preroll_samples = 0

    def reset_runtime(self) -> None:
        """Reset transient KWS/VAD/session state after an audio interruption."""
        self.vad.reset()
        if self.kws is not None:
            self.kws.reset()
        self.state = (
            PipelineState.LISTENING
            if self.bypass_kws
            else PipelineState.IDLE
        )
        self._session_started = (
            datetime.now(UTC) if self.bypass_kws else None
        )
        self._wake_result = "BYPASS" if self.bypass_kws else ""
        self._waiting_samples = 0
        self._clear_level_detector()
