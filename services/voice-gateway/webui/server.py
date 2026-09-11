from __future__ import annotations

import argparse
import atexit
import copy
import json
import logging
import math
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from datetime import datetime
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import parse_qs, unquote, urlparse

from pypinyin import Style, lazy_pinyin


APP_VERSION = "0.3.8"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
SYSTEM_VOLUME_WRITE_ENABLED = True
WEB_LOG_MAX_BYTES = 5 * 1024 * 1024
WEB_LOG_BACKUPS = 3
CONSOLE_LOG_MAX_BYTES = 8 * 1024 * 1024
CONSOLE_LOG_BACKUPS = 3
SUPERVISOR_INTERVAL_SECONDS = 2.0
RESTART_BACKOFF_SECONDS = (2.0, 5.0, 10.0, 30.0, 60.0)
STABLE_RESET_SECONDS = 300.0
STARTUP_TIMEOUT_SECONDS = 120.0
HEARTBEAT_TIMEOUT_SECONDS = 45.0
RECOGNITION_TIMEOUT_SECONDS = 120.0
GATEWAY_EVENT_NAMES = (
    "audio_heartbeat",
    "mic_started",
    "mic_stopped",
    "mic_reconnect_wait",
    "activation_config",
    "wake_detected",
    "level_triggered",
    "speech_segment",
    "speech_wait_timeout",
    "asr_result",
)
LOCAL_MODEL_API_URL = "http://192.168.16.61:3001/api/asr-text"
LOCAL_MODEL_TARGET = "2"
LOCAL_MODEL_SOURCE = "microphone"
LOCAL_MODEL_TIMEOUT_SECONDS = 15.0
LOCAL_MODEL_WORKER_COUNT = 2
WAKE_NOTIFICATION_QUEUE_SIZE = 4
LOG = logging.getLogger("voice_gateway_webui")


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class LocalModelRequestError(RuntimeError):
    pass


def _post_local_model_text(text: str) -> bytes:
    body = json.dumps(
        {
            "text": text,
            "target": LOCAL_MODEL_TARGET,
            "source": LOCAL_MODEL_SOURCE,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib_request.Request(
        LOCAL_MODEL_API_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
    )
    try:
        with urllib_request.urlopen(
            request, timeout=LOCAL_MODEL_TIMEOUT_SECONDS
        ) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
    except urllib_error.HTTPError as exc:
        status = int(exc.code)
        try:
            exc.close()
        finally:
            raise LocalModelRequestError(
                f"局域网模型接口返回HTTP {status}"
            ) from None
    except (
        urllib_error.URLError,
        TimeoutError,
        socket.timeout,
    ) as exc:
        raise LocalModelRequestError(
            "无法连接局域网模型或请求超时"
        ) from exc
    if len(payload) > 2 * 1024 * 1024:
        raise LocalModelRequestError("局域网模型返回内容过大")
    return payload


class WakeWordNotifier:
    """Posts a detected wake word without blocking the audio event reader."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._queue: queue.Queue[str] = queue.Queue(
            maxsize=WAKE_NOTIFICATION_QUEUE_SIZE
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def notify(self, wake_word: str) -> bool:
        normalized = str(wake_word).strip()
        if not normalized:
            return False
        with self._lock:
            if self._closed:
                return False
            self._ensure_worker_locked()
            try:
                self._queue.put_nowait(normalized)
            except queue.Full:
                LOG.warning("唤醒词通知队列已满，丢弃本次通知")
                return False
            return True

    def _ensure_worker_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="wake-word-notifier",
            daemon=True,
        )
        self._thread.start()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                wake_word = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._request_once(wake_word)
            except LocalModelRequestError as exc:
                LOG.warning("唤醒词发送失败：%s", exc)
            except Exception:
                LOG.exception("唤醒词发送发生未处理错误")
            finally:
                self._queue.task_done()

    def _request_once(self, wake_word: str) -> None:
        _post_local_model_text(wake_word)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)


class LocalModelResponder:
    """Posts final ASR text without blocking the gateway output reader."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._queue: queue.Queue[
            tuple[int, str, str]
        ] = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._closed = False
        self._request_id = 0
        self._state = self._empty_state()

    def _empty_state(self) -> dict[str, Any]:
        return {
            "status": "idle",
            "question": "",
            "answer": "",
            "duration_ms": None,
            "session_id": "",
            "request_id": self._request_id,
            "seq": None,
            "target": LOCAL_MODEL_TARGET,
            "source": LOCAL_MODEL_SOURCE,
            "error": "",
            "updated_at": "",
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                return copy.deepcopy(self._state)
            self._request_id += 1
            self._state = self._empty_state()
            self._discard_queued()
            return copy.deepcopy(self._state)

    def submit(
        self, session_id: str, question: str
    ) -> dict[str, Any]:
        normalized = str(question).strip()
        if not normalized:
            return self.reset()
        with self._lock:
            if self._closed:
                return copy.deepcopy(self._state)
            self._request_id += 1
            request_id = self._request_id
            self._state = {
                **self._empty_state(),
                "status": "pending",
                "question": normalized,
                "session_id": str(session_id),
                "request_id": request_id,
                "updated_at": (
                    datetime.now()
                    .astimezone()
                    .isoformat(timespec="seconds")
                ),
            }
            self._ensure_workers_locked()
            self._discard_queued()
            try:
                self._queue.put_nowait(
                    (request_id, str(session_id), normalized)
                )
            except queue.Full:
                self._state.update(
                    {
                        "status": "error",
                        "error": "模型请求队列繁忙，请稍后再试",
                    }
                )
            return copy.deepcopy(self._state)

    def _ensure_workers_locked(self) -> None:
        self._threads = [
            thread for thread in self._threads if thread.is_alive()
        ]
        self._stop_event.clear()
        while len(self._threads) < LOCAL_MODEL_WORKER_COUNT:
            thread = threading.Thread(
                target=self._worker_loop,
                name=(
                    "local-model-worker-"
                    f"{len(self._threads) + 1}"
                ),
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def _discard_queued(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
            else:
                self._queue.task_done()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                request_id, session_id, question = task
                self._execute_request(
                    request_id, session_id, question
                )
            finally:
                self._queue.task_done()

    def _execute_request(
        self, request_id: int, session_id: str, question: str
    ) -> None:
        with self._lock:
            if request_id != self._request_id:
                return
        started_at = time.perf_counter()
        try:
            response = self._request_once(question)
            self._complete(
                request_id,
                {
                    "status": "success",
                    "answer": response["reply"],
                    "seq": response.get("seq"),
                    "target": str(
                        response.get("target", LOCAL_MODEL_TARGET)
                    ),
                    "error": "",
                    "duration_ms": round(
                        (time.perf_counter() - started_at) * 1000
                    ),
                    "session_id": session_id,
                },
            )
        except LocalModelRequestError as exc:
            self._complete(
                request_id,
                {
                    "status": "error",
                    "error": str(exc),
                    "duration_ms": round(
                        (time.perf_counter() - started_at) * 1000
                    ),
                    "session_id": session_id,
                },
            )
        except Exception:
            LOG.exception("局域网模型请求发生未处理错误")
            self._complete(
                request_id,
                {
                    "status": "error",
                    "error": "局域网模型暂时不可用，请稍后重试",
                    "duration_ms": round(
                        (time.perf_counter() - started_at) * 1000
                    ),
                    "session_id": session_id,
                },
            )

    def _complete(
        self, request_id: int, updates: dict[str, Any]
    ) -> None:
        with self._lock:
            if request_id != self._request_id:
                return
            self._state.update(updates)
            self._state["request_id"] = request_id
            self._state["updated_at"] = (
                datetime.now()
                .astimezone()
                .isoformat(timespec="seconds")
            )

    def _request_once(self, question: str) -> dict[str, Any]:
        payload = _post_local_model_text(question)
        try:
            document = json.loads(payload.decode("utf-8"))
        except (
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise LocalModelRequestError(
                "局域网模型返回了无效的UTF-8 JSON"
            ) from exc
        if not isinstance(document, dict):
            raise LocalModelRequestError("局域网模型返回格式异常")
        if document.get("ok") is False:
            detail = document.get("error") or document.get("message")
            raise LocalModelRequestError(
                str(detail or "局域网模型未能处理请求")
            )
        reply = document.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            try:
                reply = document["actions"][0]["params"]["answer"]
            except (KeyError, IndexError, TypeError):
                reply = ""
        if not isinstance(reply, str) or not reply.strip():
            raise LocalModelRequestError("局域网模型未返回有效回答")
        return {**document, "reply": reply.strip()}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._request_id += 1
            self._stop_event.set()
            self._discard_queued()
        deadline = time.monotonic() + 1.0
        for thread in list(self._threads):
            if (
                thread is not threading.current_thread()
                and thread.is_alive()
            ):
                thread.join(
                    timeout=max(0.0, deadline - time.monotonic())
                )


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _number(
    value: Any,
    name: str,
    minimum: float,
    maximum: float,
    *,
    integer: bool = False,
) -> int | float:
    try:
        parsed = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(f"{name} 必须是数字") from exc
    if not minimum <= parsed <= maximum:
        raise ApiError(f"{name} 必须在 {minimum:g} 到 {maximum:g} 之间")
    return parsed


@lru_cache(maxsize=8)
def _load_tokens(path: str) -> frozenset[str]:
    token_path = Path(path)
    if not token_path.is_file():
        raise ApiError(f"KWS tokens 不存在：{token_path}")
    output: set[str] = set()
    for line in token_path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value:
            token = value.split()[0]
            if not token.startswith("<"):
                output.add(token)
    return frozenset(output)


def _segment_syllable(syllable: str, vocabulary: frozenset[str]) -> list[str]:
    @lru_cache(maxsize=None)
    def solve(position: int) -> tuple[str, ...] | None:
        if position == len(syllable):
            return ()
        candidates = sorted(
            (
                token
                for token in vocabulary
                if syllable.startswith(token, position)
            ),
            key=len,
            reverse=True,
        )
        for token in candidates:
            rest = solve(position + len(token))
            if rest is not None:
                return (token, *rest)
        return None

    result = solve(0)
    if result is None:
        raise ApiError(f"当前KWS模型无法表示拼音：{syllable}")
    return list(result)


def tokenize_keyword(text: str, tokens_path: Path) -> tuple[str, list[str]]:
    keyword = "".join(str(text).split())
    if not 2 <= len(keyword) <= 10:
        raise ApiError("唤醒词长度必须为2到10个汉字")
    if not re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]+", keyword):
        raise ApiError("当前中文KWS模型仅允许汉字唤醒词")

    vocabulary = _load_tokens(str(tokens_path.resolve()))
    syllables = lazy_pinyin(
        keyword,
        style=Style.TONE,
        neutral_tone_with_five=False,
        errors=lambda value: list(value),
    )
    output: list[str] = []
    for syllable in syllables:
        if syllable in vocabulary:
            output.append(syllable)
        else:
            output.extend(_segment_syllable(syllable, vocabulary))
    return keyword, output


class ConfigStore:
    BACKUP_LIMIT = 20
    BACKUP_RETENTION_DAYS = 30

    DEFAULTS = {
        "audio": {"input_device": None},
        "activation": {
            "mode": "kws",
            "level_threshold_dbfs": -35.0,
        },
        "kws": {
            "keyword": "你好思德",
            "score": 1.5,
            "threshold": 0.25,
        },
        "vad": {
            "threshold": 0.50,
            "min_silence_seconds": 0.60,
            "min_speech_seconds": 0.25,
            "max_speech_seconds": 15.0,
        },
        "session": {"cooldown_ms": 150},
    }

    def __init__(self, root: Path, config_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.path = (
            config_path.resolve()
            if config_path is not None
            else self.root / "config" / "default.json"
        )
        self.runtime_dir = self.root / "webui-runtime"
        self.backup_dir = self.runtime_dir / "config-backups"
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ApiError(f"配置文件不存在：{self.path}", 500) from exc
            except (OSError, json.JSONDecodeError) as exc:
                raise ApiError(f"配置文件不可读：{exc}", 500) from exc

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            candidate = copy.deepcopy(self.load())
            self._apply_patch(candidate, patch)
            self._validate(candidate)
            self._backup_current()
            self._atomic_write(candidate)
            return candidate

    def restore_defaults(self) -> dict[str, Any]:
        return self.update(copy.deepcopy(self.DEFAULTS))

    def restore_snapshot(
        self, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            candidate = copy.deepcopy(snapshot)
            self._validate(candidate)
            self._atomic_write(candidate)
            return candidate

    def _apply_patch(
        self, candidate: dict[str, Any], patch: dict[str, Any]
    ) -> None:
        if not isinstance(patch, dict):
            raise ApiError("配置请求格式错误")
        for section in ("audio", "activation", "kws", "vad", "session"):
            values = patch.get(section)
            if values is None:
                continue
            if not isinstance(values, dict):
                raise ApiError(f"{section} 配置格式错误")
            allowed = set(self.DEFAULTS[section])
            unknown = set(values) - allowed
            if unknown:
                raise ApiError(
                    f"{section} 包含不支持的字段：{', '.join(sorted(unknown))}"
                )
            candidate.setdefault(section, {}).update(values)

    def _validate(self, candidate: dict[str, Any]) -> None:
        try:
            audio = candidate["audio"]
            activation = candidate.setdefault(
                "activation", copy.deepcopy(self.DEFAULTS["activation"])
            )
            kws = candidate["kws"]
            vad = candidate["vad"]
            session = candidate["session"]
        except KeyError as exc:
            raise ApiError(f"配置缺少必要区段：{exc.args[0]}") from exc

        device = audio.get("input_device")
        if device in ("", None):
            audio["input_device"] = None
        else:
            audio["input_device"] = int(
                _number(device, "录音设备编号", 0, 4096, integer=True)
            )

        mode = str(activation.get("mode", "kws")).strip().lower()
        if mode not in {"kws", "level"}:
            raise ApiError("触发模式必须是唤醒词模式或分贝触发模式")
        activation["mode"] = mode
        activation["level_threshold_dbfs"] = float(
            _number(
                activation.get("level_threshold_dbfs"),
                "分贝触发阈值",
                -60.0,
                -15.0,
            )
        )

        keyword, tokens = tokenize_keyword(
            str(kws.get("keyword", "")),
            self.root / "models" / "kws" / "tokens.txt",
        )
        kws["keyword"] = keyword
        kws["score"] = float(
            _number(kws.get("score"), "KWS score", 0.5, 4.0)
        )
        kws["threshold"] = float(
            _number(kws.get("threshold"), "KWS threshold", 0.10, 0.60)
        )

        vad["threshold"] = float(
            _number(vad.get("threshold"), "VAD threshold", 0.10, 0.90)
        )
        vad["min_silence_seconds"] = float(
            _number(
                vad.get("min_silence_seconds"),
                "VAD结束静音",
                0.20,
                2.00,
            )
        )
        vad["min_speech_seconds"] = float(
            _number(
                vad.get("min_speech_seconds"),
                "VAD最短语音",
                0.10,
                2.00,
            )
        )
        vad["max_speech_seconds"] = float(
            _number(
                vad.get("max_speech_seconds"),
                "VAD最长语音",
                2.0,
                30.0,
            )
        )
        if vad["max_speech_seconds"] <= vad["min_speech_seconds"]:
            raise ApiError("VAD最长语音必须大于最短语音")
        session["cooldown_ms"] = int(
            _number(
                session.get("cooldown_ms"),
                "冷却时间",
                0,
                2000,
                integer=True,
            )
        )
        candidate.setdefault("_webui", {})
        candidate["_webui"].update(
            {
                "last_saved_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "keyword_tokens_preview": " ".join(tokens),
            }
        )

    def _backup_current(self) -> None:
        if not self.path.is_file():
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = self.backup_dir / f"default-{stamp}.json"
        temporary = target.with_suffix(".json.tmp")
        try:
            temporary.write_bytes(self.path.read_bytes())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        self._prune_backups()

    def _prune_backups(self) -> None:
        cutoff = time.time() - self.BACKUP_RETENTION_DAYS * 86400
        backups: list[tuple[float, Path]] = []
        for path in self.backup_dir.glob("default-*.json"):
            try:
                modified = path.stat().st_mtime
                if modified < cutoff:
                    path.unlink()
                else:
                    backups.append((modified, path))
            except OSError:
                continue
        backups.sort(key=lambda item: (item[0], item[1].name))
        for _, path in backups[: -self.BACKUP_LIMIT]:
            try:
                path.unlink()
            except OSError:
                continue

    def _atomic_write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as output:
                output.write(
                    json.dumps(value, ensure_ascii=False, indent=2) + "\n"
                )
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class AudioEndpointController:
    def __init__(self, helper: Path) -> None:
        self.helper = helper.resolve()

    def available(self) -> bool:
        return self.helper.is_file()

    def list_endpoints(self) -> list[dict[str, Any]]:
        if not self.available():
            return []
        try:
            completed = subprocess.run(
                [str(self.helper), "list"],
                cwd=str(self.helper.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
            )
        except OSError as exc:
            raise ApiError(f"Windows音量控制组件启动失败：{exc}", 503) from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ApiError(f"Windows录音端点读取失败：{detail}", 503)
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ApiError("Windows音量控制组件返回了无效数据", 503) from exc
        return result if isinstance(result, list) else []

    def set_volume(self, endpoint_id: str, volume: float) -> None:
        if not self.available():
            raise ApiError("Windows音量控制组件尚未安装", 503)
        completed = subprocess.run(
            [
                str(self.helper),
                "set",
                "--id",
                endpoint_id,
                "--volume",
                f"{volume:.2f}",
            ],
            cwd=str(self.helper.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ApiError(f"Windows输入音量设置失败：{detail}", 503)


def _normalized_device_name(value: str) -> str:
    value = value.casefold().replace("\r", " ").replace("\n", " ")
    return "".join(char for char in value if char.isalnum())


class DeviceService:
    def __init__(
        self,
        endpoint_controller: AudioEndpointController,
        volume_write_enabled: bool = False,
    ) -> None:
        self.endpoint_controller = endpoint_controller
        self.volume_write_enabled = volume_write_enabled

    def list_devices(self) -> list[dict[str, Any]]:
        try:
            import sounddevice as sd

            raw_devices = sd.query_devices()
            hostapis = sd.query_hostapis()
            default_input = int(sd.default.device[0])
        except Exception as exc:
            raise ApiError(f"无法枚举录音设备：{exc}", 503) from exc

        output: list[dict[str, Any]] = []
        for index, info in enumerate(raw_devices):
            if int(info["max_input_channels"]) <= 0:
                continue
            hostapi = hostapis[int(info["hostapi"])]["name"]
            output.append(
                {
                    "index": index,
                    "name": str(info["name"]).replace("\r", " ").replace("\n", " "),
                    "host_api": str(hostapi),
                    "inputs": int(info["max_input_channels"]),
                    "sample_rate": int(float(info["default_samplerate"])),
                    "is_default": index == default_input,
                    "recommended": "WASAPI" in str(hostapi).upper(),
                }
            )
        output.sort(
            key=lambda item: (
                not item["is_default"],
                not item["recommended"],
                item["index"],
            )
        )
        return output

    def volume_for_device(self, device_index: int | None) -> dict[str, Any]:
        endpoint = self.endpoint_for_device(device_index)
        if endpoint is None:
            return {
                "available": False,
                "message": "当前录音设备无法映射到Windows系统音量端点",
            }
        result = {
            "available": True,
            "writable": self.volume_write_enabled,
            **endpoint,
        }
        if not self.volume_write_enabled:
            result["message"] = (
                "当前只读显示：系统音量写入已暂时停用，以排查录音驱动异常"
            )
        return result

    def set_volume(self, device_index: int | None, value: float) -> dict[str, Any]:
        if not self.volume_write_enabled:
            raise ApiError(
                "系统输入音量写入已暂时停用，以避免再次触发录音驱动异常",
                503,
            )
        value = float(_number(value, "系统输入音量", 0, 100))
        endpoint = self.endpoint_for_device(device_index)
        if endpoint is None:
            raise ApiError("当前录音设备无法映射到Windows系统音量端点", 409)
        self.endpoint_controller.set_volume(str(endpoint["id"]), value)
        return self.volume_for_device(device_index)

    def endpoint_for_device(
        self, device_index: int | None
    ) -> dict[str, Any] | None:
        endpoints = self.endpoint_controller.list_endpoints()
        if not endpoints:
            return None
        return self._match_endpoint(device_index, endpoints)

    def _match_endpoint(
        self,
        device_index: int | None,
        endpoints: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if device_index is None:
            return next(
                (item for item in endpoints if item.get("is_default")),
                endpoints[0] if endpoints else None,
            )
        devices = self.list_devices()
        selected = next(
            (item for item in devices if item["index"] == device_index), None
        )
        if selected is None:
            return None
        target = _normalized_device_name(str(selected["name"]))
        exact = [
            item
            for item in endpoints
            if _normalized_device_name(str(item.get("name", ""))) == target
        ]
        if exact:
            return exact[0]
        candidates = []
        for item in endpoints:
            candidate = _normalized_device_name(str(item.get("name", "")))
            if candidate and (
                candidate in target
                or target in candidate
                or (
                    len(candidate) >= 6
                    and len(target) >= 6
                    and candidate[:6] == target[:6]
                )
            ):
                candidates.append(item)
        return candidates[0] if len(candidates) == 1 else None


class AudioLevelMonitor:
    def __init__(self, helper: Path) -> None:
        self.helper = helper.resolve()
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._endpoint_id = ""
        self._endpoint_name = ""
        self._peak = 0.0
        self._updated_at = 0.0
        self._last_error = ""

    def start(
        self, endpoint_id: str, endpoint_name: str
    ) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if (
                process is not None
                and process.poll() is None
                and self._endpoint_id == endpoint_id
            ):
                return self.status()

        self.stop()
        if not self.helper.is_file():
            raise ApiError("Windows音量检测组件尚未安装", 503)

        command = [
            str(self.helper),
            "monitor",
            "--id",
            endpoint_id,
            "--interval-ms",
            "80",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.helper.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise ApiError(f"麦克风输入音量检测启动失败：{exc}", 503) from exc

        with self._lock:
            self._process = process
            self._endpoint_id = endpoint_id
            self._endpoint_name = endpoint_name
            self._peak = 0.0
            self._updated_at = 0.0
            self._last_error = ""
            self._reader = threading.Thread(
                target=self._read_output,
                args=(process,),
                name="audio-level-monitor",
                daemon=True,
            )
            self._reader.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            self._process = None
            self._peak = 0.0
            self._updated_at = 0.0
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            active = process is not None and process.poll() is None
            peak = max(0.0, min(100.0, self._peak if active else 0.0))
            dbfs = (
                round(20.0 * math.log10(peak / 100.0), 1)
                if peak > 0.0001
                else -120.0
            )
            age_ms = (
                round((time.monotonic() - self._updated_at) * 1000)
                if self._updated_at > 0
                else None
            )
            return {
                "available": self.helper.is_file(),
                "active": active,
                "endpoint_name": self._endpoint_name,
                "peak": round(peak, 2),
                "dbfs": dbfs,
                "updated_age_ms": age_ms,
                "error": self._last_error,
            }

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    try:
                        payload = json.loads(line)
                        peak = float(payload["peak"])
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        with self._lock:
                            if self._process is process:
                                self._last_error = line.strip()
                        continue
                    with self._lock:
                        if self._process is not process:
                            return
                        self._peak = peak
                        self._updated_at = time.monotonic()
            return_code = process.wait()
        except Exception as exc:
            return_code = -1
            with self._lock:
                if self._process is process:
                    self._last_error = f"读取输入音量失败：{exc}"
        with self._lock:
            if self._process is process and return_code != 0:
                self._last_error = (
                    self._last_error
                    or f"输入音量检测组件已退出，代码 {return_code}"
                )


class GatewaySupervisor:
    def __init__(
        self,
        root: Path,
        gateway_path: Path,
        config_path: Path,
        config_store: ConfigStore,
    ) -> None:
        self.root = root.resolve()
        self.gateway_path = gateway_path.resolve()
        self.config_path = config_path.resolve()
        self.config_store = config_store
        self.log_dir = self.root / "logs"
        self.console_log = self.log_dir / "web-manager-console.log"
        self._console_log_lock = threading.Lock()
        try:
            self._console_log_size = self.console_log.stat().st_size
        except OSError:
            self._console_log_size = 0
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stop_requested = False
        self._desired_running = False
        self._started_at: float | None = None
        self._state_changed_at = time.monotonic()
        self._ready_since: float | None = None
        self._last_heartbeat_at: float | None = None
        self._heartbeat_expected = False
        self._next_restart_at: float | None = None
        self._restart_failures = 0
        self._restart_count = 0
        self._last_restart_reason = ""
        self._last_exit_code: int | None = None
        self._audio_dropped_blocks = 0
        self._audio_callback_status_events = 0
        self._audio_reconnect_count = 0
        self._cooldown_until: float | None = None
        self._service_state = "STOPPED"
        self._pipeline_state = "IDLE"
        self._last_error = ""
        self._last_wake = ""
        self._activation_mode = "kws"
        self._level_threshold_dbfs = -35.0
        self._last_asr: dict[str, Any] | None = self._read_latest_result()
        self._wake_notifier = WakeWordNotifier()
        self._local_model = LocalModelResponder()
        self._console_lines: deque[tuple[int, str]] = deque(maxlen=2000)
        self._console_sequence = 0
        self._log_stream_id = uuid.uuid4().hex

    def is_running(self) -> bool:
        with self._lock:
            return (
                self._process is not None
                and self._process.poll() is None
            )

    def desired_running(self) -> bool:
        with self._lock:
            return self._desired_running

    def start(self, *, automatic: bool = False) -> dict[str, Any]:
        with self._lock:
            self._desired_running = True
            if self.is_running():
                return self.status()
            now = time.monotonic()
            if not automatic:
                self._restart_failures = 0
                self._next_restart_at = None
            if not self.gateway_path.is_file():
                error = f"语音网关程序不存在：{self.gateway_path}"
                self._schedule_restart_locked(error, now)
                raise ApiError(error, 500)
            try:
                config = self.config_store.load()
            except ApiError as exc:
                self._schedule_restart_locked(str(exc), now)
                raise
            activation = config.get("activation", {})
            self._activation_mode = str(
                activation.get("mode", "kws")
            ).strip().lower()
            self._level_threshold_dbfs = float(
                activation.get("level_threshold_dbfs", -35.0)
            )
            device = config.get("audio", {}).get("input_device")
            command = [
                str(self.gateway_path),
                "--config",
                str(self.config_path),
                "mic",
            ]
            if device is not None:
                command.extend(["--device", str(device)])
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                # Console state remains available in memory and the child
                # event writer also degrades to stdout-only operation.
                pass
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PYTHONUNBUFFERED"] = "1"
            environment["VOICE_GATEWAY_HEARTBEAT_SECONDS"] = "10"
            environment["VOICE_GATEWAY_EXIT_WITH_PARENT"] = "1"
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(self.root),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=environment,
                )
            except OSError as exc:
                message = f"语音网关启动失败：{exc}"
                self._schedule_restart_locked(message, now)
                raise ApiError(message, 500) from exc
            self._process = process
            self._stop_requested = False
            self._started_at = now
            self._state_changed_at = now
            self._last_heartbeat_at = now
            self._heartbeat_expected = False
            self._ready_since = None
            self._next_restart_at = None
            self._service_state = "STARTING"
            self._pipeline_state = "IDLE"
            self._cooldown_until = None
            self._last_error = ""
            if automatic:
                self._restart_count += 1
                self._append_console(
                    "Web管理端：正在自动恢复语音网关"
                )
            else:
                self._append_console("Web管理端：正在启动语音网关")
            self._reader = threading.Thread(
                target=self._read_output,
                args=(process,),
                name="gateway-output",
                daemon=True,
            )
            self._reader.start()
            return self.status()

    def set_level_threshold(self, value: float) -> dict[str, Any]:
        threshold = float(
            _number(value, "分贝触发阈值", -60.0, -15.0)
        )
        with self._lock:
            process = self._process
            running = process is not None and process.poll() is None
            if running and self._activation_mode == "level":
                if process.stdin is None:
                    raise ApiError("语音进程控制通道不可用", 503)
                command = json.dumps(
                    {
                        "command": "set_level_threshold",
                        "threshold_dbfs": threshold,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                try:
                    process.stdin.write(command + "\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError, ValueError) as exc:
                    raise ApiError(
                        f"分贝阈值即时应用失败：{exc}", 503
                    ) from exc
            self._level_threshold_dbfs = threshold
            return {
                "mode": self._activation_mode,
                "level_threshold_dbfs": threshold,
                "applied_to_running_gateway": bool(
                    running and self._activation_mode == "level"
                ),
            }

    def stop(self, timeout: float = 8.0) -> dict[str, Any]:
        with self._lock:
            self._desired_running = False
            self._next_restart_at = None
            process = self._process
            reader = self._reader
            if process is None or process.poll() is not None:
                self._service_state = "STOPPED"
                self._pipeline_state = "IDLE"
                self._last_heartbeat_at = None
                self._heartbeat_expected = False
                self._ready_since = None
                return self.status()
            self._stop_requested = True
            self._service_state = "STOPPING"
            self._state_changed_at = time.monotonic()
            self._append_console("Web管理端：正在停止语音网关")

        self._terminate_process(process, timeout)
        if process.poll() is None:
            with self._lock:
                if self._process is process:
                    self._service_state = "ERROR"
                    self._last_error = "无法停止语音网关进程"
            raise ApiError("无法停止语音网关进程", 500)
        if (
            reader is not None
            and reader is not threading.current_thread()
            and reader.is_alive()
        ):
            reader.join(timeout=1.0)
        with self._lock:
            if self._process is process:
                self._service_state = "STOPPED"
                self._pipeline_state = "IDLE"
                self._last_heartbeat_at = None
                self._ready_since = None
            return self.status()

    @staticmethod
    def _terminate_process(
        process: subprocess.Popen[str], timeout: float = 8.0
    ) -> None:
        try:
            # Avoid Windows console control events: depending on the console
            # host and process group they can reach more than the intended
            # child. Terminate only the process handle created above.
            process.terminate()
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def wait_until_ready(self, timeout: float = 30.0) -> dict[str, Any]:
        with self._lock:
            process = self._process
        if process is None:
            raise ApiError("语音网关进程尚未启动", 500)
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._lock:
                if self._process is not process:
                    raise ApiError("语音网关启动进程已被替换", 409)
                if (
                    process.poll() is None
                    and self._service_state == "RUNNING"
                ):
                    return self._status_locked()
                if process.poll() is not None:
                    error = self._last_error or (
                        f"语音网关启动后异常退出，代码 {process.returncode}"
                    )
                    raise ApiError(error, 500)
                if time.monotonic() >= deadline:
                    raise ApiError("语音网关在规定时间内未准备就绪", 504)
            time.sleep(0.05)

    def maintain(self, now: float | None = None) -> dict[str, Any]:
        """Perform one low-frequency health/recovery pass."""
        current = time.monotonic() if now is None else float(now)
        process_to_stop: subprocess.Popen[str] | None = None
        stop_reason = ""
        should_start = False
        restart_message = ""
        with self._lock:
            if not self._desired_running:
                return self._status_locked()
            process = self._process
            running = process is not None and process.poll() is None
            if (
                running
                and self._ready_since is not None
                and current - self._ready_since >= STABLE_RESET_SECONDS
            ):
                self._restart_failures = 0
            if running:
                stop_reason = self._health_timeout_reason_locked(current)
                if not stop_reason:
                    return self._status_locked()
                process_to_stop = process
            else:
                if self._next_restart_at is None:
                    reason = self._last_error or "语音网关未运行"
                    delay = self._schedule_restart_locked(reason, current)
                    restart_message = (
                        f"Web管理端：{reason}，{delay:g}秒后自动重启"
                    )
                else:
                    restart_message = ""
                should_start = (
                    self._next_restart_at is not None
                    and current >= self._next_restart_at
                )
                if should_start:
                    self._next_restart_at = None

        if process_to_stop is not None:
            # Recheck after releasing the state lock so a heartbeat parsed at
            # the timeout boundary can cancel recovery before termination.
            with self._lock:
                check_now = (
                    time.monotonic() if now is None else current
                )
                if (
                    not self._desired_running
                    or self._process is not process_to_stop
                    or process_to_stop.poll() is not None
                    or self._health_timeout_reason_locked(check_now)
                    != stop_reason
                ):
                    return self._status_locked()
                already_pending = (
                    self._next_restart_at is not None
                    and self._next_restart_at > check_now
                )
                delay = self._schedule_restart_locked(
                    stop_reason, check_now
                )
                if not already_pending:
                    restart_message = (
                        f"Web管理端：检测到{stop_reason}，"
                        f"{delay:g}秒后自动重启"
                    )
            if restart_message:
                self._append_console(restart_message)
            with self._lock:
                if (
                    not self._desired_running
                    or self._process is not process_to_stop
                    or self._next_restart_at is None
                ):
                    return self._status_locked()
            self._terminate_process(process_to_stop, timeout=3.0)
            return self.status()
        if restart_message:
            self._append_console(restart_message)
        if should_start:
            try:
                return self.start(automatic=True)
            except ApiError as exc:
                self._append_console(
                    f"Web管理端：自动恢复失败：{exc}"
                )
        return self.status()

    def _health_timeout_reason_locked(self, now: float) -> str:
        if (
            self._service_state == "STARTING"
            and self._started_at is not None
            and now - self._started_at > STARTUP_TIMEOUT_SECONDS
        ):
            return "语音网关启动超时"
        if (
            self._pipeline_state == "RECOGNIZING"
            and now - self._state_changed_at
            > RECOGNITION_TIMEOUT_SECONDS
        ):
            return "本地ASR识别超时"
        if (
            self._heartbeat_expected
            and self._service_state in ("RUNNING", "RECOVERING")
            and self._pipeline_state != "RECOGNIZING"
            and self._last_heartbeat_at is not None
            and now - self._last_heartbeat_at > HEARTBEAT_TIMEOUT_SECONDS
        ):
            return "麦克风音频心跳超时"
        return ""

    def _schedule_restart_locked(
        self, reason: str, now: float | None = None
    ) -> float:
        current = time.monotonic() if now is None else float(now)
        if (
            self._next_restart_at is not None
            and self._next_restart_at > current
        ):
            return max(0.0, self._next_restart_at - current)
        if (
            self._ready_since is not None
            and current - self._ready_since >= STABLE_RESET_SECONDS
        ):
            self._restart_failures = 0
        delay = RESTART_BACKOFF_SECONDS[
            min(self._restart_failures, len(RESTART_BACKOFF_SECONDS) - 1)
        ]
        self._restart_failures += 1
        self._next_restart_at = current + delay
        self._last_restart_reason = reason
        self._last_error = reason
        self._service_state = "RECOVERING"
        self._pipeline_state = "IDLE"
        self._state_changed_at = current
        return delay

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict[str, Any]:
        now = time.monotonic()
        if (
            self._pipeline_state == "COOLDOWN"
            and self._cooldown_until is not None
            and now >= self._cooldown_until
        ):
            self._pipeline_state = "IDLE"
            self._state_changed_at = now
            self._cooldown_until = None
        process = self._process
        running = process is not None and process.poll() is None
        if (
            not running
            and not self._desired_running
            and self._service_state not in ("STOPPED", "ERROR")
        ):
            self._service_state = "STOPPED"
            self._pipeline_state = "IDLE"
        uptime = (
            round(now - self._started_at)
            if running and self._started_at is not None
            else 0
        )
        heartbeat_age = (
            round(now - self._last_heartbeat_at)
            if running and self._last_heartbeat_at is not None
            else None
        )
        restart_in = (
            max(0.0, self._next_restart_at - now)
            if self._desired_running
            and not running
            and self._next_restart_at is not None
            else 0.0
        )
        healthy = (
            running
            and self._service_state == "RUNNING"
            and not self._health_timeout_reason_locked(now)
        )
        if not self._desired_running:
            supervision_state = "STOPPED"
        elif healthy:
            supervision_state = "HEALTHY"
        elif running and self._service_state == "STARTING":
            supervision_state = "STARTING"
        elif running and self._service_state == "RECOVERING":
            supervision_state = "RECONNECTING"
        elif running:
            supervision_state = "UNHEALTHY"
        else:
            supervision_state = "RESTART_WAIT"
        return {
            "running": running,
            "desired_running": self._desired_running,
            "service_state": self._service_state,
            "pipeline_state": self._pipeline_state,
            "pid": process.pid if running else None,
            "uptime_seconds": uptime,
            "healthy": healthy,
            "supervision_state": supervision_state,
            "restart_pending": bool(restart_in > 0),
            "restart_in_seconds": math.ceil(restart_in),
            "restart_count": self._restart_count,
            "consecutive_failures": self._restart_failures,
            "last_restart_reason": self._last_restart_reason,
            "last_exit_code": self._last_exit_code,
            "heartbeat_age_seconds": heartbeat_age,
            "health_monitoring": (
                "heartbeat"
                if self._heartbeat_expected
                else "process_exit"
            ),
            "audio_dropped_blocks": self._audio_dropped_blocks,
            "audio_callback_status_events": (
                self._audio_callback_status_events
            ),
            "audio_reconnect_count": self._audio_reconnect_count,
            "last_error": self._last_error,
            "last_wake": self._last_wake,
            "activation_mode": self._activation_mode,
            "level_threshold_dbfs": self._level_threshold_dbfs,
            "last_asr": self._last_asr,
            "last_model": self._local_model.snapshot(),
        }

    def logs(self, limit: int = 200) -> list[str]:
        return self.log_snapshot(limit)["lines"]

    def log_snapshot(self, limit: int = 200) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            cursor = self._console_sequence
            stream_id = self._log_stream_id
            in_memory = [
                line for _, line in list(self._console_lines)[-limit:]
            ]
        if in_memory:
            return {
                "lines": in_memory,
                "cursor": cursor,
                "stream_id": stream_id,
            }
        if not self.console_log.is_file():
            return {
                "lines": [],
                "cursor": cursor,
                "stream_id": stream_id,
            }
        try:
            lines = self._tail_log_file(self.console_log, limit)
        except OSError:
            lines = []
        with self._lock:
            if (
                self._console_sequence != cursor
                or self._log_stream_id != stream_id
                or self._console_lines
            ):
                return {
                    "lines": [
                        line
                        for _, line in list(self._console_lines)[-limit:]
                    ],
                    "cursor": self._console_sequence,
                    "stream_id": self._log_stream_id,
                }
        return {
            "lines": lines,
            "cursor": cursor,
            "stream_id": stream_id,
        }

    def log_updates(
        self,
        after: int,
        stream_id: str | None = None,
        limit: int = 220,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        after = int(after)
        with self._lock:
            updates = self._log_updates_locked(after, stream_id, limit)
        if updates["reset"] and not updates["lines"]:
            snapshot = self.log_snapshot(limit)
            updates["lines"] = snapshot["lines"]
            updates["cursor"] = snapshot["cursor"]
            updates["stream_id"] = snapshot["stream_id"]
        return updates

    def _log_updates_locked(
        self,
        after: int,
        stream_id: str | None,
        limit: int,
    ) -> dict[str, Any]:
        latest_cursor = self._console_sequence
        first_sequence = (
            self._console_lines[0][0]
            if self._console_lines
            else latest_cursor + 1
        )
        stream_changed = (
            stream_id is not None
            and stream_id != self._log_stream_id
        )
        gap = after < first_sequence - 1
        reset = (
            stream_changed
            or after < 0
            or after > latest_cursor
            or gap
        )
        if not reset and after == latest_cursor:
            return {
                "lines": [],
                "cursor": after,
                "latest_cursor": latest_cursor,
                "stream_id": self._log_stream_id,
                "reset": False,
                "truncated": False,
                "has_more": False,
            }
        entries = list(self._console_lines)
        truncated = bool(gap or (reset and len(entries) > limit))
        if reset:
            batch = entries[-limit:]
            cursor = batch[-1][0] if batch else latest_cursor
            has_more = False
        else:
            pending = [
                entry for entry in entries if entry[0] > after
            ]
            batch = pending[:limit]
            cursor = batch[-1][0] if batch else after
            has_more = len(pending) > len(batch)
        return {
            "lines": [line for _, line in batch],
            "cursor": cursor,
            "latest_cursor": latest_cursor,
            "stream_id": self._log_stream_id,
            "reset": reset,
            "truncated": truncated,
            "has_more": has_more,
        }

    def realtime(
        self, after: int, stream_id: str | None = None
    ) -> dict[str, Any]:
        with self._lock:
            updates = self._log_updates_locked(
                int(after), stream_id, 220
            )
            status = self._status_locked()
        if updates["reset"] and not updates["lines"]:
            snapshot = self.log_snapshot(220)
            updates["lines"] = snapshot["lines"]
            updates["cursor"] = snapshot["cursor"]
            updates["stream_id"] = snapshot["stream_id"]
        return {
            "status": status,
            "lines": updates["lines"],
            "log_cursor": updates["cursor"],
            "log_stream_id": updates["stream_id"],
            "reset_logs": updates["reset"],
            "logs_truncated": updates["truncated"],
            "has_more": updates["has_more"],
        }

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self._record_gateway_line(line.rstrip(), process)
            return_code = process.wait()
        except Exception as exc:
            with self._lock:
                if self._process is process:
                    self._last_error = f"读取语音网关输出失败：{exc}"
            if process.poll() is None:
                self._terminate_process(process, timeout=3.0)
            return_code = (
                process.returncode
                if process.returncode is not None
                else -1
            )
        finally:
            for stream_name in ("stdout", "stdin"):
                stream = getattr(process, stream_name, None)
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
        message = ""
        with self._lock:
            if self._process is process:
                now = time.monotonic()
                self._last_exit_code = return_code
                self._last_heartbeat_at = None
                self._heartbeat_expected = False
                self._ready_since = None
                self._cooldown_until = None
                if self._stop_requested or not self._desired_running:
                    self._service_state = "STOPPED"
                    message = (
                        f"Web管理端：语音网关已停止，代码 {return_code}"
                    )
                else:
                    reason = (
                        self._last_error
                        or f"语音网关异常退出，代码 {return_code}"
                    )
                    delay = self._schedule_restart_locked(reason, now)
                    message = (
                        f"Web管理端：语音网关已退出，代码 {return_code}；"
                        f"{delay:g}秒后自动重启"
                    )
                self._pipeline_state = "IDLE"
                self._process = None
                if self._reader is threading.current_thread():
                    self._reader = None
        if message:
            self._append_console(message)

    def _record_gateway_line(
        self,
        line: str,
        process: subprocess.Popen[str] | None = None,
    ) -> None:
        if not line:
            return
        event_name, payload = self._parse_gateway_event(line)
        if event_name == "audio_heartbeat":
            with self._lock:
                if process is not None and self._process is not process:
                    return
                now = time.monotonic()
                self._last_heartbeat_at = now
                self._heartbeat_expected = True
                if self._service_state == "RECOVERING":
                    self._service_state = "RUNNING"
                    self._state_changed_at = now
                    self._ready_since = self._ready_since or now
                    self._next_restart_at = None
                    self._last_error = ""
                if payload:
                    state = str(payload.get("state", "")).upper()
                    if state in {
                        "IDLE",
                        "LISTENING",
                        "RECOGNIZING",
                        "COOLDOWN",
                    }:
                        self._pipeline_state = state
                    mode = str(
                        payload.get("activation_mode", "")
                    ).lower()
                    if mode in {"kws", "level"}:
                        self._activation_mode = mode
                    try:
                        self._level_threshold_dbfs = float(
                            payload["level_threshold_dbfs"]
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
                    for attribute, key in (
                        ("_audio_dropped_blocks", "dropped_blocks"),
                        (
                            "_audio_callback_status_events",
                            "callback_status_events",
                        ),
                        ("_audio_reconnect_count", "reconnect_count"),
                    ):
                        try:
                            setattr(self, attribute, int(payload[key]))
                        except (KeyError, TypeError, ValueError):
                            continue
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        record = f"{timestamp}  {line}"
        model_submission: tuple[str, str] | None = None
        wake_notification: str | None = None
        with self._lock:
            if process is not None and self._process is not process:
                return
            now = time.monotonic()
            if event_name == "mic_started":
                self._service_state = "RUNNING"
                self._pipeline_state = "IDLE"
                self._state_changed_at = now
                self._last_heartbeat_at = now
                self._ready_since = now
                self._last_error = ""
                if payload:
                    mode = str(
                        payload.get("activation_mode", "")
                    ).lower()
                    if mode in {"kws", "level"}:
                        self._activation_mode = mode
                    try:
                        self._level_threshold_dbfs = float(
                            payload["level_threshold_dbfs"]
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
                    try:
                        self._heartbeat_expected = (
                            int(payload.get("heartbeat_protocol", 0)) >= 1
                        )
                    except (TypeError, ValueError):
                        self._heartbeat_expected = False
                    try:
                        self._audio_reconnect_count = int(
                            payload["reconnect_count"]
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
            elif event_name == "activation_config":
                if payload:
                    mode = str(payload.get("mode", "")).lower()
                    if mode in {"kws", "level"}:
                        self._activation_mode = mode
                    try:
                        self._level_threshold_dbfs = float(
                            payload["level_threshold_dbfs"]
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
            elif event_name == "mic_reconnect_wait":
                self._service_state = "RECOVERING"
                self._pipeline_state = "IDLE"
                self._state_changed_at = now
                self._last_heartbeat_at = now
                if payload:
                    try:
                        self._heartbeat_expected = (
                            int(payload.get("heartbeat_protocol", 0)) >= 1
                        )
                    except (TypeError, ValueError):
                        pass
                    try:
                        self._audio_reconnect_count = int(
                            payload["reconnect_count"]
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
                    self._last_error = str(
                        payload.get("error", "麦克风正在重新连接")
                    )
            elif event_name == "wake_detected":
                self._pipeline_state = "LISTENING"
                self._state_changed_at = now
                self._last_asr = None
                self._local_model.reset()
                wake_word = (
                    str(payload.get("keyword", ""))
                    if payload
                    else ""
                )
                if wake_word:
                    self._last_wake = wake_word
                    wake_notification = wake_word
            elif event_name == "level_triggered":
                self._pipeline_state = "LISTENING"
                self._state_changed_at = now
                self._last_asr = None
                self._last_wake = ""
                self._local_model.reset()
                if payload:
                    try:
                        self._level_threshold_dbfs = float(
                            payload["threshold_dbfs"]
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
            elif event_name == "speech_segment":
                self._pipeline_state = "RECOGNIZING"
                self._state_changed_at = now
            elif event_name == "speech_wait_timeout":
                self._pipeline_state = "IDLE"
                self._state_changed_at = now
            elif event_name == "asr_result":
                if payload:
                    self._last_asr = payload
                    question = str(payload.get("text", "")).strip()
                    if question:
                        session_id = str(
                            payload.get("session_id")
                            or payload.get("id")
                            or uuid.uuid4().hex
                        )
                        model_submission = (session_id, question)
                    else:
                        self._local_model.reset()
                self._pipeline_state = "COOLDOWN"
                self._state_changed_at = now
                try:
                    cooldown = (
                        self.config_store.load()
                        .get("session", {})
                        .get("cooldown_ms", 150)
                    )
                except ApiError:
                    cooldown = 150
                self._cooldown_until = (
                    now + max(0.0, float(cooldown) / 1000.0)
                )
            elif event_name is None and (
                "错误：" in line or "未处理错误：" in line
            ):
                self._last_error = line
            self._append_console_record_locked(record)
        self._write_console_record(record)
        if wake_notification is not None:
            self._wake_notifier.notify(wake_notification)
        if model_submission is not None:
            self._local_model.submit(*model_submission)

    def close(self) -> None:
        self._wake_notifier.close()
        self._local_model.close()

    def _append_console(self, line: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        record = f"{timestamp}  {line}"
        with self._lock:
            self._append_console_record_locked(record)
        self._write_console_record(record)

    def _append_console_record_locked(self, record: str) -> None:
        self._console_sequence += 1
        self._console_lines.append(
            (self._console_sequence, record)
        )

    def _write_console_record(self, record: str) -> None:
        payload = (record + "\n").encode("utf-8", errors="replace")
        try:
            with self._console_log_lock:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                if (
                    self._console_log_size > 0
                    and self._console_log_size + len(payload)
                    > CONSOLE_LOG_MAX_BYTES
                ):
                    self._rotate_console_log_locked()
                with self.console_log.open("ab") as output:
                    output.write(payload)
                self._console_log_size += len(payload)
        except OSError:
            pass

    def _rotate_console_log_locked(self) -> None:
        for index in range(CONSOLE_LOG_BACKUPS, 0, -1):
            source = (
                self.console_log
                if index == 1
                else self.console_log.with_name(
                    f"{self.console_log.name}.{index - 1}"
                )
            )
            target = self.console_log.with_name(
                f"{self.console_log.name}.{index}"
            )
            if source.is_file():
                os.replace(source, target)
        self._console_log_size = 0

    @staticmethod
    def _tail_log_file(
        path: Path, limit: int, max_bytes: int = 256 * 1024
    ) -> list[str]:
        with path.open("rb") as source:
            source.seek(0, os.SEEK_END)
            size = source.tell()
            offset = max(0, size - max_bytes)
            source.seek(offset)
            payload = source.read()
        if offset > 0:
            _, separator, payload = payload.partition(b"\n")
            if not separator:
                payload = b""
        return payload.decode("utf-8", errors="replace").splitlines()[-limit:]

    @staticmethod
    def _parse_gateway_event(
        line: str,
    ) -> tuple[str | None, dict[str, Any] | None]:
        candidates: list[tuple[int, str, int]] = []
        levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        for event_name in GATEWAY_EVENT_NAMES:
            bare = f"{event_name} "
            if line.startswith(bare):
                candidates.append((0, event_name, len(bare)))
            for level in levels:
                at_start = f"{level} {event_name} "
                if line.startswith(at_start):
                    candidates.append(
                        (len(level) + 1, event_name, len(at_start))
                    )
                marker = f" {level} {event_name} "
                position = line.find(marker)
                if position >= 0:
                    candidates.append(
                        (
                            position + len(level) + 2,
                            event_name,
                            position + len(marker),
                        )
                    )
        if not candidates:
            return None, None
        _, event_name, payload_start = min(candidates)
        fragment = line[payload_start:].strip()
        try:
            value = json.loads(fragment)
            if isinstance(value, dict):
                return event_name, value
        except json.JSONDecodeError:
            pass
        return None, None

    @classmethod
    def _extract_event_json(
        cls, line: str, event_name: str
    ) -> dict[str, Any] | None:
        parsed_name, payload = cls._parse_gateway_event(line)
        return payload if parsed_name == event_name else None

    def _read_latest_result(self) -> dict[str, Any] | None:
        path = self.root / "results" / "latest-asr-result.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None


class ApplicationState:
    def __init__(
        self,
        root: Path,
        config_path: Path | None = None,
        gateway_path: Path | None = None,
        audio_helper: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self._control_lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._closed = False
        self._shutdown_event = threading.Event()
        self.config_store = ConfigStore(self.root, config_path)
        helper = (
            audio_helper.resolve()
            if audio_helper is not None
            else self.root
            / "webui"
            / "bin"
            / "VoiceGateway.AudioControl.exe"
        )
        self.endpoint_controller = AudioEndpointController(helper)
        self.devices = DeviceService(
            self.endpoint_controller,
            volume_write_enabled=SYSTEM_VOLUME_WRITE_ENABLED,
        )
        self.meter = AudioLevelMonitor(helper)
        gateway = (
            gateway_path.resolve()
            if gateway_path is not None
            else self.root / "VoiceGateway-G10C-Test.exe"
        )
        self.gateway = GatewaySupervisor(
            self.root,
            gateway,
            self.config_store.path,
            self.config_store,
        )
        self._supervisor_thread = threading.Thread(
            target=self._maintenance_loop,
            name="gateway-supervisor",
            daemon=True,
        )
        self._supervisor_thread.start()
        atexit.register(self.close)

    def _maintenance_loop(self) -> None:
        last_internal_error_at = 0.0
        while not self._shutdown_event.wait(
            SUPERVISOR_INTERVAL_SECONDS
        ):
            try:
                with self._control_lock:
                    self.gateway.maintain()
            except Exception:
                now = time.monotonic()
                if now - last_internal_error_at >= 60.0:
                    LOG.exception("语音网关监督检查失败")
                    last_internal_error_at = now

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._shutdown_event.set()
        if (
            self._supervisor_thread is not threading.current_thread()
            and self._supervisor_thread.is_alive()
        ):
            self._supervisor_thread.join(timeout=3.0)
        with self._control_lock:
            try:
                self.meter.stop()
            except Exception:
                LOG.exception("停止输入电平检测失败")
            try:
                self.gateway.stop()
            except ApiError as exc:
                # The parent-liveness pipe is a final cleanup boundary: if
                # graceful termination fails, process exit still closes the
                # pipe and makes the child terminate itself.
                LOG.warning("停止语音网关失败，退出时将由父子存活绑定清理：%s", exc)
            finally:
                close_gateway = getattr(self.gateway, "close", None)
                if callable(close_gateway):
                    close_gateway()

    def _ensure_open_locked(self) -> None:
        if getattr(self, "_closed", False):
            raise ApiError("Web管理端正在关闭，不能执行变更操作", 503)

    def bootstrap(self) -> dict[str, Any]:
        config = self.config_store.load()
        device = config.get("audio", {}).get("input_device")
        log_snapshot = self.gateway.log_snapshot(120)
        return {
            "version": APP_VERSION,
            "status": self.gateway.status(),
            "config": config,
            "devices": self.devices.list_devices(),
            "volume": self.devices.volume_for_device(device),
            "meter": self.meter.status(),
            "logs": log_snapshot["lines"],
            "log_cursor": log_snapshot["cursor"],
            "log_stream_id": log_snapshot["stream_id"],
        }

    def start_meter(self, device_index: int | None) -> dict[str, Any]:
        with self._control_lock:
            self._ensure_open_locked()
            endpoint = self.devices.endpoint_for_device(device_index)
            if endpoint is None:
                raise ApiError(
                    "当前录音设备无法映射到Windows输入音量检测端点",
                    409,
                )
            return self.meter.start(
                str(endpoint["id"]),
                str(endpoint.get("name", "Windows录音端点")),
            )

    def save_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        return self._save_and_apply(
            lambda: self.config_store.update(patch),
            "配置已保存",
        )

    def restore_default_config(self) -> dict[str, Any]:
        return self._save_and_apply(
            self.config_store.restore_defaults,
            "已恢复默认配置",
        )

    def update_level_threshold(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            self._ensure_open_locked()
            threshold = float(
                _number(
                    body.get("level_threshold_dbfs"),
                    "分贝触发阈值",
                    -60.0,
                    -15.0,
                )
            )
            previous = self.config_store.load()
            config = self.config_store.update(
                {
                    "activation": {
                        "level_threshold_dbfs": threshold,
                    }
                }
            )
            try:
                runtime = self.gateway.set_level_threshold(threshold)
            except ApiError:
                self.config_store.restore_snapshot(previous)
                raise
            return {
                "message": "分贝触发阈值已保存并即时应用",
                "config": config,
                "runtime": runtime,
                "status": self.gateway.status(),
            }

    def start_gateway(self) -> dict[str, Any]:
        with self._control_lock:
            self._ensure_open_locked()
            return self.gateway.start()

    def stop_gateway(self) -> dict[str, Any]:
        with self._control_lock:
            self._ensure_open_locked()
            return self.gateway.stop()

    def _save_and_apply(
        self,
        save_operation: Callable[[], dict[str, Any]],
        message: str,
    ) -> dict[str, Any]:
        with self._control_lock:
            self._ensure_open_locked()
            was_running = self.gateway.is_running()
            was_desired = self.gateway.desired_running()
            previous_config = self.config_store.load()
            config = save_operation()
            if not was_desired:
                return {
                    "message": message + "；语音服务当前未运行，将在下次启动时加载",
                    "config_saved": True,
                    "gateway_was_running": False,
                    "gateway_restarted": False,
                    "activation": "saved_for_next_start",
                    "pending_restart": False,
                    "restarted": False,
                    "config": config,
                    "status": self.gateway.status(),
                }

            try:
                self.gateway.stop()
            except ApiError as stop_error:
                try:
                    self.config_store.restore_snapshot(previous_config)
                except Exception as rollback_error:
                    raise ApiError(
                        f"{message}，但无法停止语音服务，且恢复原配置失败："
                        f"{rollback_error}",
                        500,
                    ) from stop_error
                try:
                    self.gateway.start()
                except ApiError as recovery_error:
                    raise ApiError(
                        f"{message}，但无法停止语音服务；原配置已恢复，"
                        f"自动运行状态恢复失败：{recovery_error}",
                        500,
                    ) from stop_error
                raise ApiError(
                    f"{message}，但无法停止语音服务；已恢复原配置："
                    f"{stop_error}",
                    500,
                ) from stop_error

            try:
                self.gateway.start()
                status = self.gateway.wait_until_ready(
                    timeout=STARTUP_TIMEOUT_SECONDS
                )
            except ApiError as activation_error:
                recovery_errors: list[str] = []
                try:
                    self.gateway.stop()
                except ApiError as cleanup_error:
                    recovery_errors.append(f"停止失败进程失败：{cleanup_error}")
                try:
                    self.config_store.restore_snapshot(previous_config)
                except Exception as rollback_error:
                    recovery_errors.append(f"恢复原配置失败：{rollback_error}")
                recovery_status: dict[str, Any] | None = None
                if not recovery_errors:
                    try:
                        self.gateway.start()
                        recovery_status = self.gateway.wait_until_ready(
                            timeout=STARTUP_TIMEOUT_SECONDS
                        )
                    except ApiError as recovery_error:
                        recovery_errors.append(
                            f"原配置重新启动失败：{recovery_error}"
                        )
                if recovery_errors:
                    raise ApiError(
                        f"新配置启动失败：{activation_error}；"
                        + "；".join(recovery_errors),
                        500,
                    ) from activation_error
                recovery_state = (
                    recovery_status or self.gateway.status()
                ).get("service_state", "STARTING")
                raise ApiError(
                    f"新配置启动失败：{activation_error}；"
                    f"已恢复原配置，语音服务状态为 {recovery_state}",
                    409,
                ) from activation_error

            state = status.get("service_state", "STARTING")
            state_message = (
                "并已恢复运行"
                if state == "RUNNING"
                else "，正在加载新配置"
            )
            return {
                "message": message + "并应用；语音服务已自动重启" + state_message,
                "config_saved": True,
                "gateway_was_running": was_running,
                "gateway_restarted": True,
                "activation": "restarted",
                "pending_restart": False,
                "restarted": True,
                "config": config,
                "status": status,
            }


class WebUiHandler(BaseHTTPRequestHandler):
    server_version = f"VoiceGatewayWebUI/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> ApplicationState:
        return self.server.app_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/bootstrap":
                self._json(self.app.bootstrap())
                return
            if parsed.path == "/api/status":
                self._json(self.app.gateway.status())
                return
            if parsed.path == "/api/realtime":
                query = parse_qs(parsed.query)
                try:
                    after = int(query.get("after", ["0"])[0])
                except ValueError as exc:
                    raise ApiError("日志游标必须是整数", 400) from exc
                stream_id = query.get("stream_id", [None])[0]
                self._json(
                    self.app.gateway.realtime(after, stream_id)
                )
                return
            if parsed.path == "/api/config":
                self._json(self.app.config_store.load())
                return
            if parsed.path == "/api/devices":
                self._json({"devices": self.app.devices.list_devices()})
                return
            if parsed.path == "/api/audio/volume":
                query = parse_qs(parsed.query)
                value = query.get("device_index", [""])[0]
                device = None if value == "" else int(value)
                self._json(self.app.devices.volume_for_device(device))
                return
            if parsed.path == "/api/audio/meter":
                self._json(self.app.meter.status())
                return
            if parsed.path == "/api/logs":
                query = parse_qs(parsed.query)
                limit = int(query.get("limit", ["200"])[0])
                self._json(self.app.gateway.log_snapshot(limit))
                return
            if parsed.path.startswith("/api/"):
                raise ApiError("接口不存在", 404)
            self._static(parsed.path)
        except ApiError as exc:
            self._error(exc)
        except (
            BrokenPipeError,
            ConnectionAbortedError,
            ConnectionResetError,
        ):
            return
        except Exception as exc:
            LOG.exception("GET %s failed", self.path)
            self._error(ApiError(f"服务器内部错误：{exc}", 500))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
            if parsed.path == "/api/gateway/start":
                self._json(
                    {
                        "message": "语音网关启动命令已提交",
                        "status": self.app.start_gateway(),
                    }
                )
                return
            if parsed.path == "/api/gateway/stop":
                self._json(
                    {
                        "message": "语音网关已停止",
                        "status": self.app.stop_gateway(),
                    }
                )
                return
            if parsed.path == "/api/config":
                self._json(self.app.save_config(body))
                return
            if parsed.path == "/api/config/defaults":
                self._json(self.app.restore_default_config())
                return
            if parsed.path == "/api/activation/level-threshold":
                self._json(self.app.update_level_threshold(body))
                return
            if parsed.path == "/api/audio/volume":
                device_value = body.get("device_index")
                device = (
                    None
                    if device_value in ("", None)
                    else int(device_value)
                )
                result = self.app.devices.set_volume(
                    device, body.get("volume")
                )
                self._json(
                    {"message": "Windows系统输入音量已更新", **result}
                )
                return
            if parsed.path == "/api/audio/meter/start":
                device_value = body.get("device_index")
                device = (
                    None
                    if device_value in ("", None)
                    else int(device_value)
                )
                self._json(self.app.start_meter(device))
                return
            if parsed.path == "/api/audio/meter/stop":
                self._json(self.app.meter.stop())
                return
            raise ApiError("接口不存在", 404)
        except ApiError as exc:
            self._error(exc)
        except Exception as exc:
            LOG.exception("POST %s failed", self.path)
            self._error(ApiError(f"服务器内部错误：{exc}", 500))

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024 * 1024:
            raise ApiError("请求内容过大", 413)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("请求JSON格式错误") from exc
        if not isinstance(value, dict):
            raise ApiError("请求内容必须是JSON对象")
        return value

    def _json(
        self, value: Any, status: int = HTTPStatus.OK
    ) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, error: ApiError) -> None:
        self._json({"error": str(error)}, error.status)

    def _static(self, request_path: str) -> None:
        relative = unquote(request_path).lstrip("/") or "index.html"
        static_root = self.app.root / "webui" / "static"
        target = (static_root / relative).resolve()
        if static_root.resolve() not in target.parents and target != static_root.resolve():
            raise ApiError("非法静态文件路径", 403)
        if not target.is_file():
            if "." not in Path(relative).name:
                target = static_root / "index.html"
            else:
                raise ApiError("页面不存在", 404)
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        payload = target.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            content_types.get(target.suffix.lower(), "application/octet-stream"),
        )
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.path == "/api/audio/meter" or self.path.startswith(
            "/api/realtime"
        ):
            return
        LOG.info("%s - %s", self.client_address[0], fmt % args)


class AppHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        app_state: ApplicationState,
    ) -> None:
        self.app_state = app_state
        super().__init__(address, WebUiHandler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Voice Gateway 本地 Web 管理端")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--root", type=Path, default=application_root())
    parser.add_argument("--config", type=Path)
    parser.add_argument("--gateway", type=Path)
    parser.add_argument("--audio-helper", type=Path)
    parser.add_argument(
        "--no-auto-start",
        action="store_true",
        help="启动Web管理端但不自动启动语音网关",
    )
    return parser


def configure_logging(root: Path) -> None:
    log_dir = root / "logs"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / "web-manager.log",
                maxBytes=WEB_LOG_MAX_BYTES,
                backupCount=WEB_LOG_BACKUPS,
                encoding="utf-8",
            )
        )
    except OSError as exc:
        print(
            f"警告：Web管理日志文件不可用，将仅输出到控制台：{exc}",
            file=sys.stderr,
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    configure_logging(root)
    app_state = ApplicationState(
        root,
        config_path=args.config,
        gateway_path=args.gateway,
        audio_helper=args.audio_helper,
    )
    try:
        server = AppHttpServer((args.host, args.port), app_state)
    except OSError as exc:
        print(f"错误：Web管理端启动失败：{exc}", file=sys.stderr)
        app_state.close()
        return 3

    listen_url = f"http://{args.host}:{args.port}/"
    browser_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    browser_url = f"http://{browser_host}:{args.port}/"
    LOG.info(
        "Voice Gateway Web 管理端已启动：监听 %s；本机访问 %s",
        listen_url,
        browser_url,
    )
    if not args.no_auto_start:
        try:
            app_state.start_gateway()
        except ApiError as exc:
            LOG.warning(
                "语音网关首次自动启动失败，将按退避策略继续恢复：%s",
                exc,
            )
    if args.open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(browser_url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        LOG.info("正在停止 Web 管理端")
    finally:
        server.shutdown()
        server.server_close()
        app_state.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
