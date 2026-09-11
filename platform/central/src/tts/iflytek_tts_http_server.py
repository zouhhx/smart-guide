import argparse
import base64
import hashlib
import hmac
import json
import os
import ssl
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

import websocket


HOST = "tts-api.xfyun.cn"
REQUEST_PATH = "/v2/tts"
WS_URL = f"wss://{HOST}{REQUEST_PATH}"

VOICE_ALIASES = {
    "xiaoyan": "xiaoyan",
    "aisjiuxu": "aisjiuxu",
    "aisxping": "aisxping",
    "aisjinger": "aisjinger",
    "aisbabyxu": "aisbabyxu",
}


def normalize_voice(voice: str) -> str:
    value = (voice or os.getenv("IFLYTEK_TTS_VOICE") or "xiaoyan").strip()
    return VOICE_ALIASES.get(value.lower(), value)


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def build_auth_url() -> str:
    api_key = env_required("IFLYTEK_API_KEY")
    api_secret = env_required("IFLYTEK_API_SECRET").encode("utf-8")
    date = format_datetime(datetime.now(timezone.utc), usegmt=True)
    signature_origin = f"host: {HOST}\ndate: {date}\nGET {REQUEST_PATH} HTTP/1.1"
    signature_sha = hmac.new(
        api_secret,
        signature_origin.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    signature = base64.b64encode(signature_sha).decode("utf-8")
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    params = {
        "authorization": base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8"),
        "date": date,
        "host": HOST,
    }
    return f"{WS_URL}?{urlencode(params)}"


def synthesize(text: str, voice: str, output: str, timeout: int) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    if os.path.exists(output):
        os.remove(output)

    payload = {
        "common": {
            "app_id": env_required("IFLYTEK_APP_ID"),
        },
        "business": {
            "aue": os.getenv("IFLYTEK_TTS_AUE", "lame"),
            "auf": os.getenv("IFLYTEK_TTS_AUF", "audio/L16;rate=16000"),
            "vcn": normalize_voice(voice),
            "tte": "UTF8",
        },
        "data": {
            "status": 2,
            "text": base64.b64encode(text.encode("utf-8")).decode("utf-8"),
        },
    }

    ws = websocket.create_connection(
        build_auth_url(),
        timeout=timeout,
        sslopt={"cert_reqs": ssl.CERT_NONE},
    )
    size = 0
    try:
        ws.send(json.dumps(payload, ensure_ascii=False))
        with open(output, "ab") as f:
            while True:
                raw = ws.recv()
                if not raw:
                    continue
                resp = json.loads(raw)
                code = int(resp.get("code", -1))
                if code != 0:
                    raise RuntimeError(f"iFlytek TTS error {code}: {resp.get('message') or resp}")

                data = resp.get("data") or {}
                audio = data.get("audio")
                if audio:
                    chunk = base64.b64decode(audio)
                    f.write(chunk)
                    size += len(chunk)

                if int(data.get("status", 0)) == 2:
                    break
    finally:
        ws.close()

    return size


class Handler(BaseHTTPRequestHandler):
    server_version = "ExhibitionIFlytekTTS/1.0"

    def do_GET(self):
        if self.path != "/health":
            self._json(404, {"ok": False, "error": "not found"})
            return
        configured = all(os.getenv(name, "").strip() for name in [
            "IFLYTEK_APP_ID",
            "IFLYTEK_API_KEY",
            "IFLYTEK_API_SECRET",
        ])
        self._json(200, {
            "ok": True,
            "provider": "iflytek",
            "configured": configured,
            "voice": normalize_voice(""),
            "serverTime": int(time.time() * 1000),
        })

    def do_POST(self):
        if self.path != "/tts":
            self._json(404, {"ok": False, "error": "not found"})
            return

        try:
            length = int(self.headers.get("content-length") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            text = str(payload.get("text") or "").strip()
            voice = normalize_voice(str(payload.get("voice") or ""))
            output = str(payload.get("output") or "").strip()
            retries = max(0, int(payload.get("retries", 1)))
            timeout = max(3, int(payload.get("timeout", os.getenv("IFLYTEK_TTS_TIMEOUT", "15"))))

            if not text:
                self._json(400, {"ok": False, "error": "text is required"})
                return
            if not output:
                self._json(400, {"ok": False, "error": "output is required"})
                return

            last_error = None
            size = 0
            for attempt in range(retries + 1):
                try:
                    size = synthesize(text, voice, output, timeout)
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt >= retries:
                        raise
                    time.sleep(0.5 * (attempt + 1))

            if size <= 0:
                raise RuntimeError("empty audio generated")
            self._json(200, {"ok": True, "provider": "iflytek", "voice": voice, "output": output, "size": size})
        except Exception as exc:
            self._json(500, {"ok": False, "provider": "iflytek", "error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, format, *args):
        return

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3011)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"ok": True, "provider": "iflytek", "host": args.host, "port": args.port}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
