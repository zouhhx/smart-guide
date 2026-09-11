import copy
import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from urllib import request as urllib_request
from unittest.mock import patch

from webui.server import (
    ApiError,
    AppHttpServer,
    ApplicationState,
    ConfigStore,
    DEFAULT_HOST,
    DEFAULT_PORT,
    GatewaySupervisor,
    HEARTBEAT_TIMEOUT_SECONDS,
    LOCAL_MODEL_API_URL,
    LOCAL_MODEL_SOURCE,
    LOCAL_MODEL_TARGET,
    LocalModelResponder,
    RESTART_BACKOFF_SECONDS,
    SYSTEM_VOLUME_WRITE_ENABLED,
    WakeWordNotifier,
    WebUiHandler,
    build_parser,
    tokenize_keyword,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit=-1):
        return self.payload


class LocalModelResponderTests(unittest.TestCase):
    def setUp(self):
        self.responder = LocalModelResponder()

    def tearDown(self):
        self.responder.close()

    def _wait_for(self, expected_status, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.responder.snapshot()
            if state["status"] == expected_status:
                return state
            time.sleep(0.01)
        self.fail(
            "Local model state did not become "
            f"{expected_status}: {self.responder.snapshot()}"
        )

    def test_request_uses_fixed_lan_contract_and_utf8_reply(self):
        response = FakeHttpResponse(
            json.dumps(
                {
                    "ok": True,
                    "seq": 7,
                    "reply": "中国移动提供移动通信与宽带服务。",
                    "target": "2",
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )
        with patch(
            "webui.server.urllib_request.urlopen",
            return_value=response,
        ) as urlopen:
            result = self.responder._request_once(
                "介绍一下中国移动"
            )

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, LOCAL_MODEL_API_URL)
        self.assertEqual(
            body,
            {
                "text": "介绍一下中国移动",
                "target": LOCAL_MODEL_TARGET,
                "source": LOCAL_MODEL_SOURCE,
            },
        )
        self.assertEqual(
            result["reply"],
            "中国移动提供移动通信与宽带服务。",
        )
        self.assertEqual(result["seq"], 7)

    def test_submit_is_nonblocking_and_updates_result_in_worker(self):
        started = threading.Event()
        release = threading.Event()

        def fake_request(question):
            started.set()
            release.wait(timeout=1.0)
            return {"ok": True, "seq": 8, "reply": "后台回答"}

        with patch.object(
            self.responder,
            "_request_once",
            side_effect=fake_request,
        ):
            before = time.perf_counter()
            submitted = self.responder.submit("session-1", "问题一")
            elapsed = time.perf_counter() - before
            self.assertLess(elapsed, 0.1)
            self.assertEqual(submitted["status"], "pending")
            self.assertTrue(started.wait(timeout=1.0))
            release.set()
            completed = self._wait_for("success")

        self.assertEqual(completed["question"], "问题一")
        self.assertEqual(completed["answer"], "后台回答")
        self.assertEqual(completed["seq"], 8)

    def test_new_question_does_not_wait_for_or_accept_old_answer(self):
        first_started = threading.Event()
        release_first = threading.Event()

        def fake_request(question):
            if question == "旧问题":
                first_started.set()
                release_first.wait(timeout=1.0)
                return {"ok": True, "seq": 1, "reply": "旧回答"}
            return {"ok": True, "seq": 2, "reply": "新回答"}

        with patch.object(
            self.responder,
            "_request_once",
            side_effect=fake_request,
        ):
            self.responder.submit("old", "旧问题")
            self.assertTrue(first_started.wait(timeout=1.0))
            self.responder.submit("new", "新问题")
            completed = self._wait_for("success")
            release_first.set()

        self.assertEqual(completed["session_id"], "new")
        self.assertEqual(completed["question"], "新问题")
        self.assertEqual(completed["answer"], "新回答")
        self.assertEqual(completed["seq"], 2)

    def test_actions_answer_is_supported_as_compatibility_fallback(self):
        response = FakeHttpResponse(
            json.dumps(
                {
                    "ok": True,
                    "actions": [
                        {
                            "params": {
                                "answer": "从actions读取的回答"
                            }
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )
        with patch(
            "webui.server.urllib_request.urlopen",
            return_value=response,
        ):
            result = self.responder._request_once("测试")

        self.assertEqual(result["reply"], "从actions读取的回答")

    def test_close_permanently_rejects_late_submissions(self):
        self.responder.close()

        with patch.object(
            self.responder,
            "_ensure_workers_locked",
        ) as ensure_workers:
            after_close = self.responder.submit("session-2", "问题二")

        ensure_workers.assert_not_called()
        self.assertEqual(after_close["status"], "idle")
        self.assertEqual(after_close["question"], "")
        self.assertTrue(self.responder._closed)
        self.assertTrue(self.responder._stop_event.is_set())


class WakeWordNotifierTests(unittest.TestCase):
    def setUp(self):
        self.notifier = WakeWordNotifier()

    def tearDown(self):
        self.notifier.close()

    def test_request_uses_existing_asr_endpoint_and_fixed_contract(self):
        response = FakeHttpResponse(b'{"ok":true}')
        with patch(
            "webui.server.urllib_request.urlopen",
            return_value=response,
        ) as urlopen:
            self.notifier._request_once("你好思德")

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, LOCAL_MODEL_API_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            body,
            {
                "text": "你好思德",
                "target": LOCAL_MODEL_TARGET,
                "source": LOCAL_MODEL_SOURCE,
            },
        )
        self.assertEqual(
            request.headers["Content-type"],
            "application/json; charset=utf-8",
        )

    def test_notify_is_nonblocking_and_sends_in_worker(self):
        started = threading.Event()
        release = threading.Event()

        def fake_request(wake_word):
            self.assertEqual(wake_word, "你好思德")
            started.set()
            release.wait(timeout=1.0)

        with patch.object(
            self.notifier,
            "_request_once",
            side_effect=fake_request,
        ):
            before = time.perf_counter()
            accepted = self.notifier.notify("你好思德")
            elapsed = time.perf_counter() - before
            self.assertTrue(accepted)
            self.assertLess(elapsed, 0.1)
            self.assertTrue(started.wait(timeout=1.0))
            release.set()


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir(parents=True)
        (self.root / "models" / "kws").mkdir(parents=True)
        source_config = json.loads(
            (PROJECT_ROOT / "config" / "default.json").read_text(
                encoding="utf-8"
            )
        )
        (self.root / "config" / "default.json").write_text(
            json.dumps(source_config, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.root / "models" / "kws" / "tokens.txt").write_bytes(
            (
                PROJECT_ROOT
                / "models"
                / "kws"
                / "tokens.txt"
            ).read_bytes()
        )
        self.store = ConfigStore(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_keyword_is_tokenized(self):
        keyword, tokens = tokenize_keyword(
            "你好思德",
            self.root / "models" / "kws" / "tokens.txt",
        )
        self.assertEqual(keyword, "你好思德")
        self.assertGreater(len(tokens), 4)

    def test_update_writes_backup_and_web_metadata(self):
        updated = self.store.update(
            {
                "audio": {"input_device": 9},
                "kws": {
                    "keyword": "你好思德",
                    "score": 1.8,
                    "threshold": 0.3,
                },
                "vad": {
                    "threshold": 0.52,
                    "min_silence_seconds": 0.7,
                    "min_speech_seconds": 0.25,
                    "max_speech_seconds": 15,
                },
                "session": {"cooldown_ms": 200},
            }
        )
        self.assertEqual(updated["audio"]["input_device"], 9)
        self.assertEqual(updated["kws"]["score"], 1.8)
        self.assertIn("keyword_tokens_preview", updated["_webui"])
        self.assertEqual(
            len(list(self.store.backup_dir.glob("default-*.json"))),
            1,
        )

    def test_invalid_config_does_not_replace_active_file(self):
        before = copy.deepcopy(self.store.load())
        with self.assertRaises(ApiError):
            self.store.update(
                {
                    "kws": {
                        "keyword": "Hello",
                        "score": 1.5,
                        "threshold": 0.25,
                    }
                }
            )
        self.assertEqual(self.store.load(), before)

    def test_config_backups_are_bounded(self):
        for index in range(self.store.BACKUP_LIMIT + 7):
            self.store.update(
                {
                    "kws": {
                        "score": 1.0 + index * 0.01,
                        "threshold": 0.25,
                    }
                }
            )

        backups = list(
            self.store.backup_dir.glob("default-*.json")
        )
        self.assertEqual(len(backups), self.store.BACKUP_LIMIT)

    def test_vad_cross_field_validation(self):
        with self.assertRaisesRegex(ApiError, "最长语音"):
            self.store.update(
                {
                    "vad": {
                        "threshold": 0.5,
                        "min_silence_seconds": 0.6,
                        "min_speech_seconds": 2,
                        "max_speech_seconds": 2,
                    }
                }
            )

    def test_activation_mode_and_level_threshold_are_validated(self):
        updated = self.store.update(
            {
                "activation": {
                    "mode": "level",
                    "level_threshold_dbfs": -42,
                }
            }
        )
        self.assertEqual(updated["activation"]["mode"], "level")
        self.assertEqual(updated["activation"]["level_threshold_dbfs"], -42)
        with self.assertRaises(ApiError):
            self.store.update(
                {"activation": {"level_threshold_dbfs": -5}}
            )


class FakeRunningGateway:
    def __init__(
        self,
        running=True,
        start_error=None,
        stop_error=None,
    ):
        self.running = running
        self.desired = running
        self.start_error = start_error
        self.stop_error = stop_error
        self.stop_calls = 0
        self.start_calls = 0
        self.wait_calls = 0
        self.threshold_updates = []

    def is_running(self):
        return self.running

    def desired_running(self):
        return self.desired

    def status(self):
        return {
            "running": self.running,
            "service_state": "RUNNING" if self.running else "STOPPED",
        }

    def stop(self):
        self.stop_calls += 1
        self.desired = False
        if self.stop_error is not None:
            raise self.stop_error
        self.running = False
        return self.status()

    def start(self):
        self.start_calls += 1
        self.desired = True
        if self.start_error is not None:
            error = self.start_error
            self.start_error = None
            raise error
        self.running = True
        return {
            "running": True,
            "service_state": "STARTING",
        }

    def wait_until_ready(self, timeout=30.0):
        self.wait_calls += 1
        self.running = True
        return {
            "running": True,
            "service_state": "RUNNING",
        }

    def set_level_threshold(self, value):
        self.threshold_updates.append(float(value))
        return {
            "mode": "level",
            "level_threshold_dbfs": float(value),
            "applied_to_running_gateway": self.running,
        }


class FakeChildProcess:
    pid = 123

    def __init__(self):
        self.returncode = None
        self.stdout = None
        self.stdin = io.StringIO()
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class ApplicationStateTests(ConfigStoreTests):
    def _app_with_gateway(self, gateway):
        app = ApplicationState.__new__(ApplicationState)
        app._control_lock = threading.RLock()
        app.config_store = self.store
        app.gateway = gateway
        return app

    def test_save_config_restarts_running_gateway(self):
        gateway = FakeRunningGateway()
        app = self._app_with_gateway(gateway)

        result = app.save_config(
            {
                "kws": {
                    "keyword": "你好思德",
                    "score": 1.6,
                    "threshold": 0.28,
                }
            }
        )

        self.assertFalse(result["pending_restart"])
        self.assertTrue(result["restarted"])
        self.assertTrue(result["status"]["running"])
        self.assertEqual(gateway.stop_calls, 1)
        self.assertEqual(gateway.start_calls, 1)
        self.assertEqual(gateway.wait_calls, 1)
        self.assertIn("自动重启", result["message"])

    def test_level_threshold_is_saved_and_pushed_without_restart(self):
        gateway = FakeRunningGateway()
        app = self._app_with_gateway(gateway)

        result = app.update_level_threshold(
            {"level_threshold_dbfs": -41}
        )

        self.assertEqual(gateway.threshold_updates, [-41.0])
        self.assertEqual(gateway.stop_calls, 0)
        self.assertEqual(gateway.start_calls, 0)
        self.assertEqual(
            result["config"]["activation"]["level_threshold_dbfs"],
            -41.0,
        )

    def test_save_config_keeps_stopped_gateway_stopped(self):
        gateway = FakeRunningGateway(running=False)
        app = self._app_with_gateway(gateway)

        result = app.save_config(
            {"kws": {"score": 1.7, "threshold": 0.27}}
        )

        self.assertFalse(result["restarted"])
        self.assertFalse(result["status"]["running"])
        self.assertEqual(gateway.stop_calls, 0)
        self.assertEqual(gateway.start_calls, 0)

    def test_closed_application_rejects_mutating_operations(self):
        operations = (
            ("start", lambda app: app.start_gateway()),
            (
                "save",
                lambda app: app.save_config(
                    {"kws": {"score": 1.7, "threshold": 0.27}}
                ),
            ),
        )
        for name, operation in operations:
            with self.subTest(operation=name):
                gateway = FakeRunningGateway(running=False)
                app = self._app_with_gateway(gateway)
                app._close_lock = threading.Lock()
                app._closed = True
                app._shutdown_event = threading.Event()
                app._shutdown_event.set()
                before = self.store.load()

                with self.assertRaises(ApiError) as raised:
                    operation(app)
                self.assertEqual(raised.exception.status, 503)
                self.assertEqual(gateway.start_calls, 0)
                self.assertEqual(gateway.stop_calls, 0)
                self.assertEqual(self.store.load(), before)

    def test_invalid_config_is_rejected_before_gateway_restart(self):
        gateway = FakeRunningGateway()
        app = self._app_with_gateway(gateway)

        with self.assertRaises(ApiError):
            app.save_config({"kws": {"keyword": "invalid"}})

        self.assertEqual(gateway.stop_calls, 0)
        self.assertEqual(gateway.start_calls, 0)

    def test_restart_failure_restores_previous_config_and_service(self):
        gateway = FakeRunningGateway(
            start_error=ApiError("test start failure", 500)
        )
        app = self._app_with_gateway(gateway)

        with self.assertRaisesRegex(
            ApiError, "新配置启动失败.*已恢复原配置"
        ):
            app.save_config(
                {"kws": {"score": 1.8, "threshold": 0.29}}
            )

        self.assertEqual(gateway.stop_calls, 2)
        self.assertEqual(gateway.start_calls, 2)
        self.assertEqual(gateway.wait_calls, 1)
        self.assertEqual(self.store.load()["kws"]["score"], 1.5)
        self.assertTrue(gateway.running)

    def test_stop_failure_restores_config_and_desired_running_state(self):
        gateway = FakeRunningGateway(
            stop_error=ApiError("test stop failure", 500)
        )
        app = self._app_with_gateway(gateway)

        with self.assertRaisesRegex(
            ApiError, "无法停止语音服务；已恢复原配置"
        ):
            app.save_config(
                {"kws": {"score": 1.8, "threshold": 0.29}}
            )

        self.assertEqual(gateway.stop_calls, 1)
        self.assertEqual(gateway.start_calls, 1)
        self.assertTrue(gateway.desired)
        self.assertEqual(self.store.load()["kws"]["score"], 1.5)

    def test_restore_defaults_restarts_running_gateway(self):
        self.store.update(
            {"kws": {"score": 2.0, "threshold": 0.3}}
        )
        gateway = FakeRunningGateway()
        app = self._app_with_gateway(gateway)

        result = app.restore_default_config()

        self.assertTrue(result["gateway_restarted"])
        self.assertEqual(result["config"]["kws"]["score"], 1.5)
        self.assertEqual(gateway.stop_calls, 1)
        self.assertEqual(gateway.start_calls, 1)

    def test_concurrent_saves_are_serialized_across_restart(self):
        class SlowGateway(FakeRunningGateway):
            def stop(self):
                self.stop_calls += 1
                self.running = False
                time.sleep(0.04)
                return self.status()

        gateway = SlowGateway()
        app = self._app_with_gateway(gateway)
        barrier = threading.Barrier(3)
        errors = []

        def save(score):
            try:
                barrier.wait()
                app.save_config(
                    {"kws": {"score": score, "threshold": 0.25}}
                )
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=save, args=(1.6,))
        second = threading.Thread(target=save, args=(1.7,))
        first.start()
        second.start()
        barrier.wait()
        first.join(timeout=2)
        second.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual(gateway.stop_calls, 2)
        self.assertEqual(gateway.start_calls, 2)

    def test_realtime_log_cursor_returns_only_new_lines(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        gateway._append_console("first")
        gateway._append_console("second")

        initial = gateway.log_updates(0)
        self.assertEqual(initial["cursor"], 2)
        self.assertFalse(initial["reset"])
        self.assertEqual(len(initial["lines"]), 2)
        self.assertTrue(initial["lines"][0].endswith("first"))

        gateway._append_console("third")
        incremental = gateway.log_updates(initial["cursor"])
        self.assertEqual(incremental["cursor"], 3)
        self.assertEqual(len(incremental["lines"]), 1)
        self.assertTrue(incremental["lines"][0].endswith("third"))

        reset = gateway.log_updates(999)
        self.assertTrue(reset["reset"])
        self.assertEqual(len(reset["lines"]), 3)

        stream_reset = gateway.log_updates(
            reset["cursor"], "previous-web-process"
        )
        self.assertTrue(stream_reset["reset"])
        self.assertEqual(
            stream_reset["stream_id"], gateway._log_stream_id
        )

    def test_realtime_log_cursor_catches_up_without_dropping_burst(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        for index in range(300):
            gateway._append_console(f"burst-{index:03d}")

        first = gateway.log_updates(
            0, gateway._log_stream_id, limit=220
        )
        second = gateway.log_updates(
            first["cursor"], gateway._log_stream_id, limit=220
        )
        combined = first["lines"] + second["lines"]

        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertEqual(len(combined), 300)
        self.assertTrue(combined[0].endswith("burst-000"))
        self.assertTrue(combined[-1].endswith("burst-299"))

    def test_realtime_reports_truncated_ring_buffer(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        gateway._console_lines = deque(maxlen=3)
        for index in range(5):
            gateway._append_console(f"line-{index}")

        result = gateway.log_updates(
            0, gateway._log_stream_id, limit=3
        )
        self.assertTrue(result["reset"])
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["lines"]), 3)

    def test_wake_log_and_status_are_visible_in_same_realtime_snapshot(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        with patch.object(gateway._wake_notifier, "notify"):
            gateway._record_gateway_line(
                'wake_detected {"keyword": "你好思德"}'
            )
        result = gateway.realtime(0, gateway._log_stream_id)

        self.assertEqual(
            result["status"]["pipeline_state"], "LISTENING"
        )
        self.assertEqual(result["status"]["last_wake"], "你好思德")
        self.assertEqual(len(result["lines"]), 1)

    def test_wake_detection_posts_keyword_without_blocking(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        with patch.object(
            gateway._wake_notifier,
            "notify",
            return_value=True,
        ) as notify:
            started = time.perf_counter()
            gateway._record_gateway_line(
                'wake_detected {"keyword":"你好思德"}'
            )
            elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.1)
        notify.assert_called_once_with("你好思德")
        self.assertEqual(gateway.status()["last_wake"], "你好思德")

    def test_level_trigger_listens_without_posting_fake_wake_word(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        gateway._activation_mode = "level"
        with patch.object(gateway._wake_notifier, "notify") as notify:
            gateway._record_gateway_line(
                'level_triggered {"threshold_dbfs":-38,"rms_dbfs":-30}'
            )

        status = gateway.status()
        notify.assert_not_called()
        self.assertEqual(status["pipeline_state"], "LISTENING")
        self.assertEqual(status["activation_mode"], "level")
        self.assertEqual(status["level_threshold_dbfs"], -38.0)
        self.assertEqual(status["last_wake"], "")

    def test_running_level_gateway_accepts_threshold_control_command(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        process = FakeChildProcess()
        gateway._process = process
        gateway._activation_mode = "level"

        result = gateway.set_level_threshold(-43)

        self.assertTrue(result["applied_to_running_gateway"])
        self.assertEqual(result["level_threshold_dbfs"], -43.0)
        self.assertEqual(
            json.loads(process.stdin.getvalue()),
            {
                "command": "set_level_threshold",
                "threshold_dbfs": -43.0,
            },
        )

    def test_asr_result_is_submitted_to_local_model_without_blocking(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        with patch.object(
            gateway._local_model,
            "submit",
            return_value={"status": "pending"},
        ) as submit:
            started = time.perf_counter()
            gateway._record_gateway_line(
                'asr_result {"session_id":"s-1","text":"介绍一下中国移动"}'
            )
            elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.1)
        submit.assert_called_once_with("s-1", "介绍一下中国移动")
        self.assertEqual(
            gateway.status()["last_asr"]["text"],
            "介绍一下中国移动",
        )

    def test_new_wake_clears_previous_asr_and_model_answer(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        gateway._last_asr = {"text": "旧问题"}
        gateway._local_model._state.update(
            {
                "status": "success",
                "question": "旧问题",
                "answer": "旧回答",
            }
        )

        with patch.object(gateway._wake_notifier, "notify"):
            gateway._record_gateway_line(
                'wake_detected {"keyword": "你好思德"}'
            )
        status = gateway.status()

        self.assertIsNone(status["last_asr"])
        self.assertEqual(status["last_model"]["status"], "idle")
        self.assertEqual(status["last_model"]["answer"], "")

    def test_stale_gateway_reader_cannot_update_new_process_state(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        current_process = object()
        stale_process = object()
        gateway._process = current_process

        gateway._record_gateway_line(
            'wake_detected {"keyword": "旧进程"}',
            stale_process,
        )

        self.assertEqual(gateway._pipeline_state, "IDLE")
        self.assertEqual(gateway._last_wake, "")
        self.assertEqual(gateway._console_sequence, 0)

    def test_heartbeat_updates_health_without_entering_logs(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )

        gateway._record_gateway_line(
            'audio_heartbeat {"state":"IDLE","dropped_blocks":2,'
            '"callback_status_events":3,"reconnect_count":1}'
        )

        self.assertIsNotNone(gateway._last_heartbeat_at)
        self.assertEqual(gateway._audio_dropped_blocks, 2)
        self.assertEqual(gateway._audio_callback_status_events, 3)
        self.assertEqual(gateway._audio_reconnect_count, 1)
        self.assertEqual(gateway._console_sequence, 0)
        self.assertFalse(gateway.console_log.exists())

    def test_heartbeat_cancels_stale_process_recovery(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        process = FakeChildProcess()
        gateway._process = process
        gateway._desired_running = True
        gateway._service_state = "RECOVERING"
        gateway._next_restart_at = time.monotonic() + 30
        gateway._last_error = "previous heartbeat timeout"

        gateway._record_gateway_line(
            'audio_heartbeat {"state":"IDLE","dropped_blocks":0,'
            '"callback_status_events":0,"reconnect_count":0}',
            process,
        )

        status = gateway.status()
        self.assertEqual(status["service_state"], "RUNNING")
        self.assertFalse(status["restart_pending"])
        self.assertEqual(status["last_error"], "")

    def test_start_enables_parent_exit_and_heartbeat_protocol(self):
        gateway_path = self.root / "VoiceGateway-G10C-Test.exe"
        gateway_path.touch()
        gateway = GatewaySupervisor(
            self.root,
            gateway_path,
            self.store.path,
            self.store,
        )
        process = FakeChildProcess()

        with (
            patch(
                "webui.server.subprocess.Popen",
                return_value=process,
            ) as popen,
            patch("webui.server.threading.Thread") as thread_type,
        ):
            gateway.start()

        thread_type.return_value.start.assert_called_once_with()
        _, kwargs = popen.call_args
        with self.subTest(setting="stdin"):
            self.assertIs(kwargs.get("stdin"), subprocess.PIPE)
        with self.subTest(setting="parent_exit"):
            self.assertEqual(
                kwargs["env"].get("VOICE_GATEWAY_EXIT_WITH_PARENT"),
                "1",
            )
        with self.subTest(setting="heartbeat"):
            self.assertEqual(
                kwargs["env"].get("VOICE_GATEWAY_HEARTBEAT_SECONDS"),
                "10",
            )

    def test_legacy_mic_started_disables_heartbeat_timeout(self):
        now = time.monotonic()
        legacy = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        legacy_process = FakeChildProcess()
        legacy._process = legacy_process
        legacy._desired_running = True
        legacy._service_state = "STARTING"
        legacy._record_gateway_line(
            'mic_started {"device": 1}',
            legacy_process,
        )
        legacy._last_heartbeat_at = (
            now - HEARTBEAT_TIMEOUT_SECONDS - 1.0
        )

        legacy.maintain(now=now)

        self.assertFalse(legacy_process.terminated)
        self.assertEqual(legacy.status()["service_state"], "RUNNING")

    def test_heartbeat_protocol_enables_heartbeat_timeout(self):
        now = time.monotonic()
        current = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        current_process = FakeChildProcess()
        current._process = current_process
        current._desired_running = True
        current._service_state = "STARTING"
        current._record_gateway_line(
            'mic_started {"device": 1, "heartbeat_protocol": 1}',
            current_process,
        )
        current._last_heartbeat_at = (
            now - HEARTBEAT_TIMEOUT_SECONDS - 1.0
        )

        current.maintain(now=now)

        self.assertTrue(current_process.terminated)
        self.assertEqual(
            current.status()["service_state"], "RECOVERING"
        )

    def test_asr_text_cannot_impersonate_gateway_events(self):
        for marker in (
            "mic_started",
            'audio_heartbeat {"state":"IDLE"}',
        ):
            with self.subTest(marker=marker):
                gateway = GatewaySupervisor(
                    self.root,
                    self.root / "VoiceGateway-G10C-Test.exe",
                    self.store.path,
                    self.store,
                )
                gateway._service_state = "RUNNING"
                gateway._pipeline_state = "LISTENING"
                payload = {
                    "text": f"普通识别文本包含 {marker}",
                    "status": "success",
                }

                gateway._record_gateway_line(
                    "asr_result "
                    + json.dumps(payload, ensure_ascii=False)
                )

                self.assertEqual(gateway._last_asr, payload)
                self.assertEqual(
                    gateway._service_state, "RUNNING"
                )
                self.assertEqual(
                    gateway._pipeline_state, "COOLDOWN"
                )
                self.assertEqual(gateway._console_sequence, 1)

    def test_console_log_rotates_at_small_threshold(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )

        with (
            patch("webui.server.CONSOLE_LOG_MAX_BYTES", 80),
            patch("webui.server.CONSOLE_LOG_BACKUPS", 2),
        ):
            for index in range(8):
                gateway._write_console_record(
                    f"entry-{index}-" + "x" * 40
                )

        rotated = [
            gateway.console_log,
            gateway.console_log.with_name(
                f"{gateway.console_log.name}.1"
            ),
            gateway.console_log.with_name(
                f"{gateway.console_log.name}.2"
            ),
        ]
        self.assertTrue(all(path.is_file() for path in rotated))
        self.assertFalse(
            gateway.console_log.with_name(
                f"{gateway.console_log.name}.3"
            ).exists()
        )
        self.assertIn(
            "entry-7",
            gateway.console_log.read_text(encoding="utf-8"),
        )
        self.assertTrue(
            all(path.stat().st_size <= 80 for path in rotated)
        )

    def test_restart_backoff_is_bounded(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        actual = []
        with gateway._lock:
            gateway._desired_running = True
            for index in range(len(RESTART_BACKOFF_SECONDS) + 2):
                gateway._next_restart_at = None
                actual.append(
                    gateway._schedule_restart_locked(
                        f"failure-{index}",
                        now=1000.0 + index * 100.0,
                    )
                )

        self.assertEqual(
            actual,
            [2.0, 5.0, 10.0, 30.0, 60.0, 60.0, 60.0],
        )

    def test_heartbeat_timeout_terminates_only_current_process(self):
        class FakeProcess:
            pid = 123

            def __init__(self):
                self.returncode = None
                self.terminated = False

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        process = FakeProcess()
        gateway._process = process
        gateway._desired_running = True
        gateway._service_state = "RUNNING"
        gateway._pipeline_state = "IDLE"
        gateway._heartbeat_expected = True
        now = time.monotonic()
        gateway._last_heartbeat_at = (
            now - HEARTBEAT_TIMEOUT_SECONDS - 1.0
        )

        status = gateway.maintain(now=now)

        self.assertTrue(process.terminated)
        self.assertEqual(status["service_state"], "RECOVERING")
        self.assertTrue(status["desired_running"])
        self.assertTrue(status["restart_pending"])

    def test_manual_stop_cancels_pending_recovery(self):
        gateway = GatewaySupervisor(
            self.root,
            self.root / "VoiceGateway-G10C-Test.exe",
            self.store.path,
            self.store,
        )
        gateway._desired_running = True
        gateway._service_state = "RECOVERING"
        gateway._next_restart_at = time.monotonic() + 30

        status = gateway.stop()

        self.assertFalse(status["desired_running"])
        self.assertFalse(status["restart_pending"])
        self.assertEqual(status["service_state"], "STOPPED")


class StaticAssetTests(unittest.TestCase):
    def test_dashboard_contains_required_controls(self):
        page = (
            PROJECT_ROOT / "webui" / "static" / "index.html"
        ).read_text(encoding="utf-8")
        for identifier in (
            'id="primaryAction"',
            'id="deviceSelect"',
            'id="volumeSlider"',
            'id="inputMeter"',
            'id="inputMeterFill"',
            'id="activationMode"',
            'id="levelTriggerControls"',
            'id="levelThreshold"',
            'id="applyLevelThreshold"',
            'id="keywordInput"',
            'id="kwsScore"',
            'id="vadSilence"',
            'id="kwsListeningIndicator"',
            'id="kwsListeningTitle"',
            'id="kwsListeningHint"',
            'id="kwsListeningBadge"',
            'id="modelAnswer"',
            'id="modelStatusBadge"',
            'id="logOutput"',
            "局域网访问已开启",
            "语音服务会自动重启并立即加载新设置",
        ):
            self.assertIn(identifier, page)
        for removed_identifier in (
            'id="vadThreshold"',
            'id="vadMinSpeech"',
            'id="vadMaxSpeech"',
            'id="pendingNotice"',
        ):
            self.assertNotIn(removed_identifier, page)

    def test_dashboard_uses_guarded_50ms_incremental_polling(self):
        script = (
            PROJECT_ROOT / "webui" / "static" / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn("const REALTIME_POLL_MS = 50", script)
        self.assertIn("scheduleRealtime(nextDelay)", script)
        self.assertIn("state.realtimeBusy", script)
        self.assertIn("/api/realtime?after=", script)
        self.assertNotIn("setInterval(refreshLogs", script)
        self.assertNotIn("setInterval(refreshRealtime", script)
        self.assertNotIn("renderPendingRestart", script)
        self.assertIn("renderModelResult(status.last_model)", script)
        self.assertIn("renderLeadershipLog(status)", script)
        self.assertIn('/api/activation/level-threshold', script)
        self.assertIn('mode: selectedActivationMode()', script)

    def test_dashboard_has_live_kws_listening_feedback(self):
        page = (
            PROJECT_ROOT / "webui" / "static" / "index.html"
        ).read_text(encoding="utf-8")
        script = (
            PROJECT_ROOT / "webui" / "static" / "app.js"
        ).read_text(encoding="utf-8")
        styles = (
            PROJECT_ROOT / "webui" / "static" / "styles.css"
        ).read_text(encoding="utf-8")

        indicator = page.index('id="kwsListeningIndicator"')
        recent_result = page.index('id="lastAsrText"')
        self.assertLess(indicator, recent_result)
        self.assertIn('role="status"', page)
        self.assertIn('aria-live="polite"', page)
        self.assertIn("renderListeningState(status)", script)
        self.assertIn('pipelineState === "LISTENING"', script)
        self.assertIn("唤醒成功，可以讲话", script)
        self.assertIn("数字人正在听，请说出您的问题", script)
        self.assertIn(".listening-indicator.is-listening", styles)
        self.assertIn("@keyframes listening-wave", styles)

    def test_dashboard_orders_model_answer_between_asr_and_logs(self):
        page = (
            PROJECT_ROOT / "webui" / "static" / "index.html"
        ).read_text(encoding="utf-8")
        recent = page.index("<h3>最近识别</h3>")
        answer = page.index("<h3>模型回答</h3>")
        logs = page.index("<h3>运行日志</h3>")
        self.assertLess(recent, answer)
        self.assertLess(answer, logs)
        self.assertIn(
            "仅展示当前唤醒词、ASR识别结果和模型回答",
            page,
        )

    def test_dashboard_and_backend_contain_no_cloud_model_integration(self):
        combined = "\n".join(
            (
                (PROJECT_ROOT / "webui" / "server.py").read_text(
                    encoding="utf-8"
                ),
                (
                    PROJECT_ROOT / "webui" / "static" / "index.html"
                ).read_text(encoding="utf-8"),
                (
                    PROJECT_ROOT / "webui" / "static" / "app.js"
                ).read_text(encoding="utf-8"),
            )
        ).casefold()
        self.assertNotIn("deep" + "seek", combined)

    def test_wake_notification_uses_existing_post_contract(self):
        backend = (
            PROJECT_ROOT / "webui" / "server.py"
        ).read_text(encoding="utf-8")
        documentation = (
            PROJECT_ROOT / "docs" / "KWS-WAKE-NOTIFY-API.md"
        ).read_text(encoding="utf-8")

        self.assertNotIn('parsed.path == "/api/wake-events"', backend)
        self.assertIn("self._wake_notifier.notify", backend)
        self.assertIn(
            "POST http://192.168.16.61:3001/api/asr-text",
            documentation,
        )
        self.assertIn('"text": "你好思德"', documentation)
        self.assertIn('"target": "2"', documentation)
        self.assertIn('"source": "microphone"', documentation)


class SafetyRegressionTests(unittest.TestCase):
    def test_release_build_uses_current_source_and_checks_core_version(self):
        script = (
            PROJECT_ROOT / "scripts" / "build-test-package.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '$env:PYTHONPATH = Join-Path $Root "src"',
            script,
        )
        self.assertIn("Packaged core version mismatch", script)
        release_script = (
            PROJECT_ROOT
            / "webui"
            / "build-deployment-package.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('"KWS-WAKE-NOTIFY-API.md"', release_script)

    def test_system_volume_write_is_enabled_for_remote_host_build(self):
        self.assertTrue(SYSTEM_VOLUME_WRITE_ENABLED)

    def test_auto_restart_uses_only_the_gateway_process_handle(self):
        source = (
            PROJECT_ROOT / "webui" / "server.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("CTRL_BREAK_EVENT", source)
        self.assertIn("self.gateway.stop()", source)
        self.assertIn("self.gateway.start()", source)
        self.assertIn("status = self.gateway.wait_until_ready", source)
        self.assertIn("self._control_lock", source)

    def test_web_manager_listens_on_all_ipv4_interfaces_by_default(self):
        self.assertEqual(DEFAULT_HOST, "0.0.0.0")
        self.assertEqual(DEFAULT_PORT, 8765)
        arguments = build_parser().parse_args([])
        self.assertEqual(arguments.host, "0.0.0.0")
        self.assertEqual(arguments.port, 8765)

    def test_lan_firewall_helper_is_scoped_to_local_subnet(self):
        helper = (
            PROJECT_ROOT / "deploy" / "06-enable-lan-access.cmd"
        ).read_text(encoding="utf-8")
        self.assertIn("localport=8765", helper)
        self.assertIn("remoteip=LocalSubnet", helper)

    def test_launcher_explicitly_enables_lan_binding(self):
        launcher = (
            PROJECT_ROOT / "deploy" / "05-web-manager.cmd"
        ).read_text(encoding="utf-8")
        self.assertIn("--host 0.0.0.0", launcher)

    def test_realtime_transport_uses_keep_alive_and_unbuffered_child(self):
        source = (
            PROJECT_ROOT / "webui" / "server.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(WebUiHandler.protocol_version, "HTTP/1.1")
        self.assertIn('environment["PYTHONUNBUFFERED"] = "1"', source)
        self.assertIn('parsed.path == "/api/realtime"', source)


if __name__ == "__main__":
    unittest.main()
