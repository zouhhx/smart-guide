from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import __version__
from .audio import (
    AudioError,
    MicrophoneSource,
    assess_audio_levels,
    audio_levels,
    frame_audio,
    list_input_devices,
    read_pcm16_wav,
)
from .config import ConfigError, load_config, required_model_paths
from .engines import ModelError, SherpaAsrEngine, SherpaKwsEngine, SherpaVadEngine
from .events import EventWriter
from .keyword import KeywordError, write_keywords_file
from .integrity import verify_model_lock
from .pipeline import VoicePipeline


def default_config_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config" / "default.json"
    return Path(__file__).resolve().parents[2] / "config" / "default.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-gateway",
        description="展厅中英混合KWS/VAD/ASR离线语音网关",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--config", default=str(default_config_path()), help="配置JSON路径"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="检查环境、模型和麦克风设备")
    sub.add_parser("devices", help="列出录音设备")

    probe = sub.add_parser("probe", help="录制数秒并检查麦克风输入电平")
    probe.add_argument("--device", help="设备索引或名称，默认使用配置")
    probe.add_argument("--seconds", type=float, default=8.0, help="测试时长，默认8秒")

    keyword = sub.add_parser("keyword", help="生成中文唤醒词token文件")
    keyword.add_argument("--text", help="覆盖配置中的唤醒词")

    asr = sub.add_parser("asr", help="仅测试自动中英ASR")
    asr.add_argument("wav", help="16-bit PCM WAV文件")

    vad = sub.add_parser("vad", help="仅测试VAD断句")
    vad.add_argument("wav", help="16-bit PCM WAV文件")

    kws = sub.add_parser("kws", help="仅测试中文唤醒词KWS")
    kws.add_argument("wav", help="16-bit PCM WAV文件")

    file_parser = sub.add_parser("file", help="运行文件版KWS→VAD→ASR")
    file_parser.add_argument("wav", help="包含唤醒词和问题的WAV文件")
    file_parser.add_argument(
        "--bypass-kws", action="store_true", help="跳过KWS，直接测试VAD和ASR"
    )

    mic = sub.add_parser("mic", help="运行实时麦克风KWS→VAD→ASR")
    mic.add_argument("--device", help="设备索引或名称，默认使用配置")
    mic.add_argument(
        "--bypass-kws", action="store_true", help="跳过KWS，直接测试VAD和ASR"
    )
    mic.add_argument(
        "--seconds", type=float, default=0, help="运行秒数；0表示直到Ctrl+C"
    )
    return parser


def _create_engines(config, bypass_kws: bool = False):  # noqa: ANN001
    kws = None
    activation = getattr(config, "activation", None)
    activation_mode = getattr(activation, "mode", "kws")
    if config.kws.enabled and not bypass_kws and activation_mode == "kws":
        print("正在加载中文唤醒词KWS模型……", flush=True)
        kws = SherpaKwsEngine(config.kws, config.audio.sample_rate)
    print("正在加载VAD模型……", flush=True)
    vad = SherpaVadEngine(config.vad, config.audio.sample_rate)
    print("正在加载自动中英ASR模型……", flush=True)
    asr = SherpaAsrEngine(config.asr, config.audio.sample_rate)
    return kws, vad, asr


def _pipeline(config, bypass_kws: bool = False) -> VoicePipeline:  # noqa: ANN001
    kws, vad, asr = _create_engines(config, bypass_kws)
    events = EventWriter(
        config.logging.directory,
        config.logging.level,
        config.logging.retention_days,
        config.logging.results_directory,
    )
    return VoicePipeline(
        device_id=config.device_id,
        sample_rate=config.audio.sample_rate,
        keyword=config.kws.keyword,
        kws=kws,
        vad=vad,
        asr=asr,
        events=events,
        bypass_kws=bypass_kws,
        cooldown_ms=config.session.cooldown_ms,
        speech_wait_timeout_seconds=config.session.speech_wait_timeout_seconds,
        activation_mode=getattr(
            getattr(config, "activation", None), "mode", "kws"
        ),
        level_threshold_dbfs=getattr(
            getattr(config, "activation", None),
            "level_threshold_dbfs",
            -35.0,
        ),
    )


def command_doctor(config) -> int:  # noqa: ANN001
    print(f"voice-gateway {__version__}")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    try:
        import sherpa_onnx

        print(f"sherpa-onnx: {getattr(sherpa_onnx, '__version__', '已安装')}")
    except ImportError as exc:
        print(f"[失败] sherpa-onnx未安装：{exc}")
        return 2

    missing = False
    for name, path in required_model_paths(config).items():
        state = "正常" if path.is_file() else "缺失"
        print(f"[{state}] {name}: {path}")
        missing = missing or not path.is_file()

    integrity_failed = False
    if not missing:
        print("\n模型SHA-256：")
        try:
            for item in verify_model_lock(config.project_root):
                state = "正常" if item.valid else "不匹配"
                print(f"[{state}] {item.asset}/{item.filename}")
                integrity_failed = integrity_failed or not item.valid
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[失败] 无法验证模型摘要：{exc}")
            integrity_failed = True

    print("\n录音输入设备：")
    audio_failed = False
    try:
        devices = list_input_devices()
        if not devices:
            print("  未发现录音输入设备")
            audio_failed = True
        for item in devices:
            print(
                f"  {item['index']}: {item['name']} "
                f"({item['inputs']}ch, {item['default_sample_rate']}Hz)"
            )
    except Exception as exc:  # 诊断命令不能因声卡API异常中断
        print(f"  枚举失败：{exc}")
        audio_failed = True
    if missing:
        return 3
    if integrity_failed:
        return 4
    return 5 if audio_failed else 0


def command_devices() -> int:
    for item in list_input_devices():
        print(json.dumps(item, ensure_ascii=False))
    return 0


def command_probe(config, device: str | None, seconds: float) -> int:  # noqa: ANN001
    if not 1.0 <= seconds <= 60.0:
        raise AudioError("probe测试时长必须在1到60秒之间")
    selected = _parse_device(device, config.audio.input_device)
    source = MicrophoneSource(
        device=selected,
        target_rate=config.audio.sample_rate,
        frame_samples=config.audio.frame_samples,
        capture_rate=config.audio.capture_sample_rate,
    )
    print(
        f"请持续正常讲话 {seconds:g} 秒：device={selected!r}, "
        f"capture={source.capture_rate}Hz",
        flush=True,
    )
    frames: list[np.ndarray] = []
    started = time.monotonic()
    with source:
        for frame in source.frames():
            frames.append(frame)
            if time.monotonic() - started >= seconds:
                break
    samples = np.concatenate(frames) if frames else np.empty(0, dtype=np.float32)
    rms, peak, clipping = audio_levels(samples)
    status, message = assess_audio_levels(rms, peak, clipping)
    result = {
        "device": selected,
        "capture_sample_rate": source.capture_rate,
        "duration_seconds": round(len(samples) / config.audio.sample_rate, 2),
        "rms_dbfs": round(rms, 2),
        "peak_dbfs": round(peak, 2),
        "clipping_ratio": round(clipping, 6),
        "dropped_blocks": source.dropped_blocks,
        "status": status,
        "message": message,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if status == "ok" and source.dropped_blocks == 0 else 8


def command_keyword(config, text: str | None) -> int:  # noqa: ANN001
    keyword = text or config.kws.keyword
    line = write_keywords_file(keyword, config.kws.tokens, config.kws.keywords_file)
    print(f"唤醒词：{keyword}")
    print(f"模型token：{line}")
    print(f"文件：{config.kws.keywords_file}")
    return 0


def command_asr(config, wav: str) -> int:  # noqa: ANN001
    audio = read_pcm16_wav(wav, config.audio.sample_rate)
    engine = SherpaAsrEngine(config.asr, config.audio.sample_rate)
    result = engine.transcribe(audio.samples)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0 if result.text else 4


def command_vad(config, wav: str) -> int:  # noqa: ANN001
    audio = read_pcm16_wav(wav, config.audio.sample_rate)
    engine = SherpaVadEngine(config.vad, config.audio.sample_rate)
    segments = []
    for frame in frame_audio(audio.samples, config.audio.frame_samples):
        segments.extend(engine.process(frame))
    segments.extend(engine.flush())
    for index, segment in enumerate(segments, 1):
        rms, peak, clipping = audio_levels(segment.samples)
        print(
            json.dumps(
                {
                    "segment": index,
                    "start_seconds": round(
                        segment.start_sample / config.audio.sample_rate, 3
                    ),
                    "duration_seconds": round(
                        len(segment.samples) / config.audio.sample_rate, 3
                    ),
                    "rms_dbfs": round(rms, 2),
                    "peak_dbfs": round(peak, 2),
                    "clipping_ratio": round(clipping, 6),
                },
                ensure_ascii=False,
            )
        )
    return 0 if segments else 5


def command_kws(config, wav: str) -> int:  # noqa: ANN001
    audio = read_pcm16_wav(wav, config.audio.sample_rate)
    engine = SherpaKwsEngine(config.kws, config.audio.sample_rate)
    detected: list[str] = []
    for frame in frame_audio(audio.samples, config.audio.frame_samples):
        result = engine.process(frame)
        if result:
            detected.append(result)
            print(json.dumps({"detected": result}, ensure_ascii=False))
    return 0 if detected else 6


def command_file(config, wav: str, bypass_kws: bool) -> int:  # noqa: ANN001
    audio = read_pcm16_wav(wav, config.audio.sample_rate)
    tail = np.zeros(
        round(config.session.file_tail_silence_seconds * config.audio.sample_rate),
        dtype=np.float32,
    )
    samples = np.concatenate((audio.samples, tail))
    pipeline = _pipeline(config, bypass_kws)
    results = list(
        pipeline.run(frame_audio(samples, config.audio.frame_samples), flush_at_end=True)
    )
    print(json.dumps([item.to_dict() for item in results], ensure_ascii=False, indent=2))
    return 0 if results else 7


def _parse_device(value: str | None, configured):  # noqa: ANN001
    if value is None:
        return configured
    try:
        return int(value)
    except ValueError:
        return value


def _heartbeat_interval_seconds() -> float:
    raw = os.environ.get("VOICE_GATEWAY_HEARTBEAT_SECONDS", "").strip()
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    if not math.isfinite(value) or value <= 0:
        return 0.0
    # Heartbeats are a supervisor health signal, not a realtime telemetry
    # stream. Keep accidental environment overrides from producing per-frame
    # console I/O.
    return min(60.0, max(5.0, value))


def _handle_parent_control_line(pipeline, raw_line: bytes) -> None:  # noqa: ANN001
    try:
        payload = json.loads(raw_line.decode("utf-8"))
        if payload.get("command") != "set_level_threshold":
            raise ValueError("unsupported parent command")
        threshold = pipeline.set_level_threshold_dbfs(
            float(payload["threshold_dbfs"])
        )
        print(
            "activation_config "
            + json.dumps(
                {
                    "mode": pipeline.activation_mode,
                    "level_threshold_dbfs": threshold,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            flush=True,
        )
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"parent_control_error {exc}", file=sys.stderr, flush=True)


def _start_parent_liveness_watch(pipeline=None) -> None:  # noqa: ANN001
    if os.environ.get("VOICE_GATEWAY_EXIT_WITH_PARENT") != "1":
        return
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        return

    def wait_for_parent_pipe() -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                if pipeline is not None and line.strip():
                    _handle_parent_control_line(pipeline, line)
        except Exception:
            pass
        # The Web manager disappeared without a normal shutdown. Exit
        # immediately so its replacement cannot leave two microphone owners.
        os._exit(20)

    threading.Thread(
        target=wait_for_parent_pipe,
        name="parent-liveness",
        daemon=True,
    ).start()


def command_mic(config, device: str | None, bypass_kws: bool, seconds: float) -> int:  # noqa: ANN001
    if seconds < 0:
        raise AudioError("mic运行时长不能小于0")
    selected = _parse_device(device, config.audio.input_device)
    pipeline = _pipeline(config, bypass_kws)
    _start_parent_liveness_watch(pipeline)
    started = time.monotonic()
    heartbeat_seconds = _heartbeat_interval_seconds()
    heartbeat_samples = max(
        1, round(heartbeat_seconds * config.audio.sample_rate)
    )
    reconnect_delays = (1.0, 2.0, 5.0, 10.0, 30.0)
    reconnect_failures = 0
    reconnect_count = 0
    total_dropped = 0
    total_callback_status = 0
    total_discarded = 0
    last_audio_error: AudioError | None = None

    def time_remaining() -> float | None:
        if seconds <= 0:
            return None
        return seconds - (time.monotonic() - started)

    try:
        while True:
            remaining = time_remaining()
            if remaining is not None and remaining <= 0:
                break

            source: MicrophoneSource | None = None
            connected_at: float | None = None
            audio_error: AudioError | None = None
            try:
                pipeline.reset_runtime()
                source = MicrophoneSource(
                    device=selected,
                    target_rate=config.audio.sample_rate,
                    frame_samples=config.audio.frame_samples,
                    capture_rate=config.audio.capture_sample_rate,
                )
                samples_since_heartbeat = 0

                def limited_frames():
                    nonlocal samples_since_heartbeat
                    for frame in source.frames():
                        remaining_now = time_remaining()
                        if remaining_now is not None and remaining_now <= 0:
                            return
                        if heartbeat_seconds > 0:
                            samples_since_heartbeat += len(frame)
                            if samples_since_heartbeat >= heartbeat_samples:
                                samples_since_heartbeat %= heartbeat_samples
                                payload = {
                                    "state": pipeline.state.value,
                                    "activation_mode": getattr(
                                        pipeline, "activation_mode", "kws"
                                    ),
                                    "level_threshold_dbfs": getattr(
                                        pipeline,
                                        "level_threshold_dbfs",
                                        -35.0,
                                    ),
                                    "dropped_blocks": (
                                        total_dropped + source.dropped_blocks
                                    ),
                                    "callback_status_events": (
                                        total_callback_status
                                        + source.callback_status_events
                                    ),
                                    "discarded_stale_blocks": total_discarded,
                                    "reconnect_count": reconnect_count,
                                }
                                print(
                                    "audio_heartbeat "
                                    + json.dumps(
                                        payload,
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                    ),
                                    flush=True,
                                )
                        yield frame

                with source:
                    connected_at = time.monotonic()
                    print(
                        f"实时监听已开始：device={selected!r}, "
                        f"capture={source.capture_rate}Hz, "
                        f"keyword={config.kws.keyword!r}。按Ctrl+C停止。",
                        flush=True,
                    )
                    pipeline.events.emit(
                        "mic_started",
                        device=selected,
                        capture_sample_rate=source.capture_rate,
                        bypass_kws=bypass_kws,
                        activation_mode=getattr(
                            pipeline, "activation_mode", "kws"
                        ),
                        level_threshold_dbfs=getattr(
                            pipeline, "level_threshold_dbfs", -35.0
                        ),
                        reconnect_count=reconnect_count,
                        heartbeat_protocol=1,
                    )
                    for result in pipeline.run(
                        limited_frames(), flush_at_end=False
                    ):
                        print(
                            json.dumps(
                                result.to_dict(),
                                ensure_ascii=False,
                                indent=2,
                            )
                        )
                        total_discarded += source.discard_pending()
            except AudioError as exc:
                audio_error = exc
                last_audio_error = exc
            finally:
                if source is not None:
                    total_dropped += source.dropped_blocks
                    total_callback_status += source.callback_status_events
                    pipeline.events.emit(
                        "mic_stopped",
                        device=selected,
                        dropped_blocks=source.dropped_blocks,
                        callback_status_events=source.callback_status_events,
                        last_callback_status=source.last_callback_status,
                        reconnecting=audio_error is not None,
                    )

            if audio_error is None:
                last_audio_error = None
                break

            pipeline.reset_runtime()
            if (
                connected_at is not None
                and time.monotonic() - connected_at >= 60.0
            ):
                reconnect_failures = 0
            delay = reconnect_delays[
                min(reconnect_failures, len(reconnect_delays) - 1)
            ]
            reconnect_failures += 1
            reconnect_count += 1
            remaining = time_remaining()
            if remaining is not None:
                if remaining <= 0:
                    break
                delay = min(delay, remaining)
            pipeline.events.emit(
                "mic_reconnect_wait",
                device=selected,
                error=str(audio_error),
                retry_in_seconds=round(delay, 1),
                reconnect_count=reconnect_count,
                heartbeat_protocol=1,
            )
            time.sleep(delay)
    except KeyboardInterrupt:
        print("\n已停止。")
    print(
        "音频统计："
        f"dropped={total_dropped}, "
        f"callback_status={total_callback_status}, "
        f"discarded_stale={total_discarded}, "
        f"reconnects={reconnect_count}"
    )
    if seconds > 0 and last_audio_error is not None:
        print(f"麦克风在限定时间内始终无法连接：{last_audio_error}", file=sys.stderr)
        return 8
    return 0 if total_dropped == 0 else 9


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, OSError):
                pass
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "doctor":
            return command_doctor(config)
        if args.command == "devices":
            return command_devices()
        if args.command == "probe":
            return command_probe(config, args.device, args.seconds)
        if args.command == "keyword":
            return command_keyword(config, args.text)
        if args.command == "asr":
            return command_asr(config, args.wav)
        if args.command == "vad":
            return command_vad(config, args.wav)
        if args.command == "kws":
            return command_kws(config, args.wav)
        if args.command == "file":
            return command_file(config, args.wav, args.bypass_kws)
        if args.command == "mic":
            return command_mic(config, args.device, args.bypass_kws, args.seconds)
        raise RuntimeError(f"未知命令：{args.command}")
    except (ConfigError, AudioError, ModelError, KeywordError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"未处理错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
