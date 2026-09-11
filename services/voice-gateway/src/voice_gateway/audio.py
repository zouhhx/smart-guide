from __future__ import annotations

import queue
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


class AudioError(RuntimeError):
    """音频源或格式错误。"""


@dataclass(frozen=True)
class AudioData:
    samples: np.ndarray
    sample_rate: int

    @property
    def duration_seconds(self) -> float:
        return float(len(self.samples)) / self.sample_rate


def read_pcm16_wav(path: str | Path, target_rate: int = 16000) -> AudioData:
    filename = Path(path)
    if not filename.is_file():
        raise AudioError(f"WAV文件不存在：{filename}")

    try:
        with wave.open(str(filename), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, OSError) as exc:
        raise AudioError(f"无法读取WAV：{exc}") from exc

    if sample_width != 2:
        raise AudioError("第一期WAV输入仅支持16-bit PCM")
    if channels not in (1, 2):
        raise AudioError("第一期WAV输入仅支持单声道或双声道")

    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1, dtype=np.float32)
    if sample_rate != target_rate:
        samples = resample_linear(samples, sample_rate, target_rate)
    return AudioData(np.ascontiguousarray(samples, dtype=np.float32), target_rate)


def resample_linear(
    samples: np.ndarray, source_rate: int, target_rate: int
) -> np.ndarray:
    if source_rate <= 0 or target_rate <= 0:
        raise AudioError("采样率必须大于0")
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if source_rate == target_rate or len(samples) == 0:
        return samples.copy()
    output_length = max(1, round(len(samples) * target_rate / source_rate))
    source_positions = np.arange(len(samples), dtype=np.float64)
    target_positions = np.arange(output_length, dtype=np.float64) * (
        source_rate / target_rate
    )
    target_positions = np.minimum(target_positions, len(samples) - 1)
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


def frame_audio(
    samples: np.ndarray, frame_samples: int, pad_final: bool = True
) -> Iterator[np.ndarray]:
    if frame_samples <= 0:
        raise AudioError("frame_samples必须大于0")
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    for start in range(0, len(samples), frame_samples):
        frame = samples[start : start + frame_samples]
        if len(frame) < frame_samples:
            if not pad_final:
                return
            frame = np.pad(frame, (0, frame_samples - len(frame)))
        yield np.ascontiguousarray(frame, dtype=np.float32)


def audio_levels(samples: np.ndarray) -> tuple[float, float, float]:
    samples = np.asarray(samples, dtype=np.float32)
    if len(samples) == 0:
        return -120.0, -120.0, 0.0
    rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
    peak = float(np.max(np.abs(samples)))
    rms_dbfs = 20.0 * np.log10(max(rms, 1e-6))
    peak_dbfs = 20.0 * np.log10(max(peak, 1e-6))
    clipping = float(np.mean(np.abs(samples) >= 0.999))
    return rms_dbfs, peak_dbfs, clipping


def assess_audio_levels(
    rms_dbfs: float, peak_dbfs: float, clipping_ratio: float
) -> tuple[str, str]:
    """给实机接线测试提供保守的电平判断，不替代正式声学验收。"""
    if peak_dbfs < -55.0:
        return "no_signal", "几乎没有输入信号，请检查粉色Mic In、接收机输出和Windows输入设备"
    if clipping_ratio >= 0.001 or peak_dbfs > -0.5:
        return "clipping", "输入存在削波风险，请降低接收机输出或Windows麦克风增益"
    if rms_dbfs < -35.0:
        return "too_quiet", "讲话电平偏小，建议提高接收机输出或Windows麦克风级别"
    if rms_dbfs > -10.0:
        return "too_loud", "平均电平偏大，请适当降低输入增益"
    return "ok", "讲话电平处于建议范围"


def assess_recognized_speech_levels(
    rms_dbfs: float, peak_dbfs: float, clipping_ratio: float
) -> tuple[str, str]:
    """已被VAD和ASR确认的讲话只检查削波，不评价说话者音量大小。"""
    if clipping_ratio >= 0.001 or peak_dbfs > -0.5:
        return "clipping", "检测到削波风险，请降低输入增益"
    return "normal", "有效语音，未检测到削波"


def list_input_devices() -> list[dict[str, object]]:
    import sounddevice as sd

    devices: list[dict[str, object]] = []
    for index, info in enumerate(sd.query_devices()):
        if int(info["max_input_channels"]) > 0:
            devices.append(
                {
                    "index": index,
                    "name": str(info["name"]),
                    "inputs": int(info["max_input_channels"]),
                    "default_sample_rate": int(float(info["default_samplerate"])),
                }
            )
    return devices


class MicrophoneSource:
    """PortAudio/WASAPI麦克风源；输出固定16 kHz、512采样点音频帧。"""

    def __init__(
        self,
        device: int | str | None,
        target_rate: int = 16000,
        frame_samples: int = 512,
        capture_rate: int | None = None,
    ) -> None:
        import sounddevice as sd

        self._sd = sd
        self.device = device
        try:
            info = sd.query_devices(device, "input")
        except Exception as exc:
            raise AudioError(f"无法打开录音设备 {device!r}：{exc}") from exc
        self.capture_rate = capture_rate or int(float(info["default_samplerate"]))
        self.target_rate = target_rate
        self.frame_samples = frame_samples
        self._capture_block = max(1, round(frame_samples * self.capture_rate / target_rate))
        self._queue: queue.Queue[np.ndarray | Exception] = queue.Queue(maxsize=64)
        self.dropped_blocks = 0
        self.callback_status_events = 0
        self.last_callback_status = ""
        self._stream = None

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            # PortAudio can report a transient overflow while continuing to
            # deliver valid samples. Treating every callback flag as fatal made
            # one brief driver hiccup stop an otherwise healthy 24/7 stream.
            # A truly stalled device is still detected by frames()'s timeout.
            self.callback_status_events += 1
            self.last_callback_status = str(status)
        mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
        try:
            self._queue.put_nowait(mono)
        except queue.Full:
            # Keep the newest audio when the consumer is temporarily busy
            # (normally during synchronous ASR). Retaining old blocks would
            # add avoidable wake-up latency after recognition completes.
            self.dropped_blocks += 1
            try:
                self._queue.get_nowait()
            except queue.Empty:
                # The consumer may have drained the queue between Full and
                # get_nowait(). Give the newest block one non-blocking chance
                # instead of discarding it unnecessarily.
                try:
                    self._queue.put_nowait(mono)
                except queue.Full:
                    pass
                return
            try:
                self._queue.put_nowait(mono)
            except queue.Full:
                # There is normally only one PortAudio callback producer, but
                # do not ever block the real-time callback if that assumption
                # changes in a driver/backend.
                return

    def __enter__(self) -> "MicrophoneSource":
        try:
            self._stream = self._sd.InputStream(
                device=self.device,
                channels=1,
                samplerate=self.capture_rate,
                blocksize=self._capture_block,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            raise AudioError(f"启动录音设备失败：{exc}") from exc
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        stream = self._stream
        self._stream = None
        if stream is not None:
            cleanup_error: Exception | None = None
            try:
                if exc_type is None:
                    stream.stop()
                else:
                    abort = getattr(stream, "abort", None)
                    if abort is not None:
                        abort()
                    else:
                        stream.stop()
            except Exception as exc:  # PortAudio backend errors vary by host API.
                cleanup_error = exc
            try:
                stream.close()
            except Exception as exc:
                cleanup_error = cleanup_error or exc
            if exc_type is None and cleanup_error is not None:
                raise AudioError(
                    f"关闭录音设备失败：{cleanup_error}"
                ) from cleanup_error

    def frames(self) -> Iterator[np.ndarray]:
        pending = np.empty(0, dtype=np.float32)
        while True:
            try:
                block = self._queue.get(timeout=5.0)
            except queue.Empty as exc:
                raise AudioError("连续5秒未收到麦克风数据，请检查设备是否被占用") from exc
            if isinstance(block, Exception):
                raise block
            if self.capture_rate != self.target_rate:
                block = resample_linear(block, self.capture_rate, self.target_rate)
            pending = np.concatenate((pending, block))
            while len(pending) >= self.frame_samples:
                yield np.ascontiguousarray(
                    pending[: self.frame_samples], dtype=np.float32
                )
                pending = pending[self.frame_samples :]

    def discard_pending(self) -> int:
        """Discard queued audio captured while the pipeline was blocked."""
        discarded = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return discarded
            discarded += 1
