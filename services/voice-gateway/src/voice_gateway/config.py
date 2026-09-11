from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """配置不可用。"""


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int
    frame_samples: int
    input_device: int | str | None
    capture_sample_rate: int | None
    channels: int


@dataclass(frozen=True)
class ActivationConfig:
    mode: str
    level_threshold_dbfs: float


@dataclass(frozen=True)
class KwsConfig:
    enabled: bool
    keyword: str
    tokens: Path
    encoder: Path
    decoder: Path
    joiner: Path
    keywords_file: Path
    num_threads: int
    max_active_paths: int
    score: float
    threshold: float
    trailing_blanks: int


@dataclass(frozen=True)
class VadConfig:
    model: Path
    threshold: float
    min_silence_seconds: float
    min_speech_seconds: float
    max_speech_seconds: float
    buffer_seconds: int


@dataclass(frozen=True)
class AsrConfig:
    model: Path
    tokens: Path
    language: str
    use_itn: bool
    num_threads: int
    replacements: dict[str, str]


@dataclass(frozen=True)
class SessionConfig:
    cooldown_ms: int
    file_tail_silence_seconds: float
    speech_wait_timeout_seconds: float


@dataclass(frozen=True)
class LoggingConfig:
    directory: Path
    results_directory: Path
    level: str
    retention_days: int


@dataclass(frozen=True)
class AppConfig:
    schema_version: int
    device_id: str
    project_root: Path
    audio: AudioConfig
    activation: ActivationConfig
    kws: KwsConfig
    vad: VadConfig
    asr: AsrConfig
    session: SessionConfig
    logging: LoggingConfig


def _required(data: dict[str, Any], key: str, section: str) -> Any:
    if key not in data:
        raise ConfigError(f"配置缺少 {section}.{key}")
    return data[key]


def _path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在：{config_path}")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取配置：{exc}") from exc

    root = config_path.parent.parent
    audio = _required(raw, "audio", "root")
    activation = raw.get("activation", {})
    kws = _required(raw, "kws", "root")
    vad = _required(raw, "vad", "root")
    asr = _required(raw, "asr", "root")
    session = _required(raw, "session", "root")
    logging = _required(raw, "logging", "root")
    runtime_override = os.environ.get("VOICE_GATEWAY_DATA_DIR")
    if runtime_override:
        runtime_base = Path(os.path.expandvars(runtime_override)).resolve()
        keywords_file = runtime_base / "runtime" / "keywords.txt"
        logging_directory = runtime_base / "logs"
        results_directory = runtime_base / "results"
    else:
        keywords_file = _path(root, _required(kws, "keywords_file", "kws"))
        logging_directory = _path(root, logging.get("directory", "logs"))
        results_directory = _path(
            root, logging.get("results_directory", "results")
        )

    result = AppConfig(
        schema_version=int(_required(raw, "schema_version", "root")),
        device_id=str(_required(raw, "device_id", "root")).strip(),
        project_root=root,
        audio=AudioConfig(
            sample_rate=int(_required(audio, "sample_rate", "audio")),
            frame_samples=int(_required(audio, "frame_samples", "audio")),
            input_device=audio.get("input_device"),
            capture_sample_rate=(
                int(audio["capture_sample_rate"])
                if audio.get("capture_sample_rate") is not None
                else None
            ),
            channels=int(audio.get("channels", 1)),
        ),
        activation=ActivationConfig(
            mode=str(activation.get("mode", "kws")).strip().lower(),
            level_threshold_dbfs=float(
                activation.get("level_threshold_dbfs", -35.0)
            ),
        ),
        kws=KwsConfig(
            enabled=bool(kws.get("enabled", True)),
            keyword=str(_required(kws, "keyword", "kws")).strip(),
            tokens=_path(root, _required(kws, "tokens", "kws")),
            encoder=_path(root, _required(kws, "encoder", "kws")),
            decoder=_path(root, _required(kws, "decoder", "kws")),
            joiner=_path(root, _required(kws, "joiner", "kws")),
            keywords_file=keywords_file,
            num_threads=int(kws.get("num_threads", 2)),
            max_active_paths=int(kws.get("max_active_paths", 4)),
            score=float(kws.get("score", 1.5)),
            threshold=float(kws.get("threshold", 0.25)),
            trailing_blanks=int(kws.get("trailing_blanks", 1)),
        ),
        vad=VadConfig(
            model=_path(root, _required(vad, "model", "vad")),
            threshold=float(vad.get("threshold", 0.5)),
            min_silence_seconds=float(vad.get("min_silence_seconds", 0.9)),
            min_speech_seconds=float(vad.get("min_speech_seconds", 0.25)),
            max_speech_seconds=float(vad.get("max_speech_seconds", 15.0)),
            buffer_seconds=int(vad.get("buffer_seconds", 30)),
        ),
        asr=AsrConfig(
            model=_path(root, _required(asr, "model", "asr")),
            tokens=_path(root, _required(asr, "tokens", "asr")),
            language=str(asr.get("language", "zh")),
            use_itn=bool(asr.get("use_itn", True)),
            num_threads=int(asr.get("num_threads", 4)),
            replacements={
                str(source): str(target)
                for source, target in asr.get("replacements", {}).items()
                if str(source)
            },
        ),
        session=SessionConfig(
            cooldown_ms=int(session.get("cooldown_ms", 800)),
            file_tail_silence_seconds=float(
                session.get("file_tail_silence_seconds", 1.2)
            ),
            speech_wait_timeout_seconds=float(
                session.get("speech_wait_timeout_seconds", 8.0)
            ),
        ),
        logging=LoggingConfig(
            directory=logging_directory,
            results_directory=results_directory,
            level=str(logging.get("level", "INFO")).upper(),
            retention_days=int(logging.get("retention_days", 30)),
        ),
    )
    validate_config(result)
    return result


def validate_config(config: AppConfig) -> None:
    if config.schema_version != 1:
        raise ConfigError(f"不支持的schema_version：{config.schema_version}")
    if not config.device_id:
        raise ConfigError("device_id不能为空")
    if config.audio.sample_rate != 16000:
        raise ConfigError("第一期内部采样率固定为16000 Hz")
    if config.audio.frame_samples != 512:
        raise ConfigError("Silero VAD第一期帧长固定为512 samples")
    if config.audio.channels != 1:
        raise ConfigError("第一期内部声道固定为单声道")
    if config.activation.mode not in {"kws", "level"}:
        raise ConfigError("activation.mode must be kws or level")
    if not -60.0 <= config.activation.level_threshold_dbfs <= -15.0:
        raise ConfigError(
            "activation.level_threshold_dbfs must be between -60 and -15 dBFS"
        )
    if not config.kws.keyword:
        raise ConfigError("kws.keyword不能为空")
    if not 0.0 < config.kws.threshold < 1.0:
        raise ConfigError("kws.threshold必须在0和1之间")
    if not 0.0 < config.vad.threshold < 1.0:
        raise ConfigError("vad.threshold必须在0和1之间")
    if config.vad.min_silence_seconds <= 0:
        raise ConfigError("vad.min_silence_seconds必须大于0")
    if config.vad.max_speech_seconds <= config.vad.min_speech_seconds:
        raise ConfigError("VAD最长语音必须大于最短语音")
    if config.asr.num_threads < 1 or config.kws.num_threads < 1:
        raise ConfigError("模型线程数必须大于0")
    if config.session.cooldown_ms < 0:
        raise ConfigError("session.cooldown_ms不能小于0")
    if config.session.speech_wait_timeout_seconds <= 0:
        raise ConfigError("session.speech_wait_timeout_seconds必须大于0")
    if config.logging.retention_days < 1:
        raise ConfigError("logging.retention_days必须大于0")


def required_model_paths(config: AppConfig) -> dict[str, Path]:
    paths = {
        "VAD model": config.vad.model,
        "ASR model": config.asr.model,
        "ASR tokens": config.asr.tokens,
    }
    if config.activation.mode == "kws":
        paths = {
            "KWS tokens": config.kws.tokens,
            "KWS encoder": config.kws.encoder,
            "KWS decoder": config.kws.decoder,
            "KWS joiner": config.kws.joiner,
            **paths,
        }
    return paths
