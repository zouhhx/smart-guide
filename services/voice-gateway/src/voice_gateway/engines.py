from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import sherpa_onnx

from .config import AsrConfig, KwsConfig, VadConfig
from .keyword import write_keywords_file


class ModelError(RuntimeError):
    """模型或推理引擎不可用。"""


@dataclass(frozen=True)
class SpeechSegment:
    samples: np.ndarray
    start_sample: int


@dataclass(frozen=True)
class AsrOutput:
    text: str
    duration_ms: int
    language: str
    emotion: str
    audio_event: str


def normalize_sense_voice_tag(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("<|") and text.endswith("|>"):
        text = text[2:-2]
    return text.lower()


def _ensure_files(paths: dict[str, object]) -> None:
    missing = [f"{name}: {path}" for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ModelError("模型文件缺失：\n" + "\n".join(missing))


class SherpaKwsEngine:
    def __init__(self, config: KwsConfig, sample_rate: int) -> None:
        self.config = config
        self.sample_rate = sample_rate
        _ensure_files(
            {
                "tokens": config.tokens,
                "encoder": config.encoder,
                "decoder": config.decoder,
                "joiner": config.joiner,
            }
        )
        write_keywords_file(config.keyword, config.tokens, config.keywords_file)
        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(config.tokens),
            encoder=str(config.encoder),
            decoder=str(config.decoder),
            joiner=str(config.joiner),
            keywords_file=str(config.keywords_file),
            num_threads=config.num_threads,
            sample_rate=sample_rate,
            max_active_paths=config.max_active_paths,
            keywords_score=config.score,
            keywords_threshold=config.threshold,
            num_trailing_blanks=config.trailing_blanks,
            provider="cpu",
        )
        self._stream = self._spotter.create_stream()

    def process(self, samples: np.ndarray) -> str | None:
        self._stream.accept_waveform(self.sample_rate, samples)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            result = self._spotter.get_result(self._stream)
            if result:
                self._spotter.reset_stream(self._stream)
                return result
        return None

    def reset(self) -> None:
        self._spotter.reset_stream(self._stream)


class SherpaVadEngine:
    def __init__(self, config: VadConfig, sample_rate: int) -> None:
        self.config = config
        self.sample_rate = sample_rate
        _ensure_files({"silero_vad": config.model})
        silero = sherpa_onnx.SileroVadModelConfig(
            model=str(config.model),
            threshold=config.threshold,
            min_silence_duration=config.min_silence_seconds,
            min_speech_duration=config.min_speech_seconds,
            window_size=512,
            max_speech_duration=config.max_speech_seconds,
        )
        vad_config = sherpa_onnx.VadModelConfig(
            silero_vad=silero,
            sample_rate=sample_rate,
            num_threads=1,
            provider="cpu",
            debug=False,
        )
        self._vad = sherpa_onnx.VoiceActivityDetector(
            vad_config, buffer_size_in_seconds=config.buffer_seconds
        )

    def process(self, samples: np.ndarray) -> list[SpeechSegment]:
        self._vad.accept_waveform(samples)
        return self._drain()

    def flush(self) -> list[SpeechSegment]:
        self._vad.flush()
        return self._drain()

    def reset(self) -> None:
        self._vad.reset()

    def is_speech_detected(self) -> bool:
        return bool(self._vad.is_speech_detected())

    def _drain(self) -> list[SpeechSegment]:
        output: list[SpeechSegment] = []
        while not self._vad.empty():
            segment = self._vad.front
            output.append(
                SpeechSegment(
                    samples=np.asarray(segment.samples, dtype=np.float32).copy(),
                    start_sample=int(segment.start),
                )
            )
            self._vad.pop()
        return output


class SherpaAsrEngine:
    def __init__(self, config: AsrConfig, sample_rate: int) -> None:
        self.config = config
        self.sample_rate = sample_rate
        _ensure_files({"sense_voice": config.model, "tokens": config.tokens})
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(config.model),
            tokens=str(config.tokens),
            num_threads=config.num_threads,
            sample_rate=sample_rate,
            feature_dim=80,
            decoding_method="greedy_search",
            provider="cpu",
            language=config.language,
            use_itn=config.use_itn,
        )

    def transcribe(self, samples: np.ndarray) -> AsrOutput:
        started = time.perf_counter()
        stream = self._recognizer.create_stream()
        stream.accept_waveform(self.sample_rate, samples)
        self._recognizer.decode_stream(stream)
        result = stream.result
        text = str(getattr(result, "text", "")).strip()
        for source, target in self.config.replacements.items():
            text = text.replace(source, target)
        return AsrOutput(
            text=text,
            duration_ms=round((time.perf_counter() - started) * 1000),
            language=normalize_sense_voice_tag(getattr(result, "lang", "")),
            emotion=normalize_sense_voice_tag(getattr(result, "emotion", "")),
            audio_event=normalize_sense_voice_tag(getattr(result, "event", "")),
        )
