import argparse
import asyncio
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import edge_tts


VOICE_ALIASES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",
    "xiaoyi": "zh-CN-XiaoyiNeural",
    "yunxi": "zh-CN-YunxiNeural",
    "yunxia": "zh-CN-YunxiaNeural",
    "xiaoxuan": "zh-CN-XiaoxuanNeural",
    "yunjian": "zh-CN-YunjianNeural",
    "yunyang": "zh-CN-YunyangNeural",
    "andrew": "en-US-AndrewNeural",
    "emma": "en-US-EmmaNeural",
}


def normalize_voice(voice: str) -> str:
    value = (voice or "xiaoxiao").strip()
    return VOICE_ALIASES.get(value.lower(), value)


async def generate_speech(text: str, voice: str, output: str, retries: int) -> None:
    last_error = None
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    for attempt in range(retries + 1):
        try:
            if os.path.exists(output):
                os.remove(output)
            communicate = edge_tts.Communicate(text, normalize_voice(voice))
            await communicate.save(output)
            return
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            await asyncio.sleep(0.5 * (attempt + 1))
    raise last_error


class Handler(BaseHTTPRequestHandler):
    server_version = "ExhibitionEdgeTTS/1.0"

    def do_GET(self):
        if self.path != "/health":
            self._json(404, {"ok": False, "error": "not found"})
            return
        self._json(200, {"ok": True, "serverTime": int(time.time() * 1000)})

    def do_POST(self):
        if self.path != "/tts":
            self._json(404, {"ok": False, "error": "not found"})
            return

        try:
            length = int(self.headers.get("content-length") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            text = str(payload.get("text") or "").strip()
            voice = normalize_voice(str(payload.get("voice") or "xiaoxiao"))
            output = str(payload.get("output") or "").strip()
            retries = int(payload.get("retries", 2))

            if not text:
                self._json(400, {"ok": False, "error": "text is required"})
                return
            if not output:
                self._json(400, {"ok": False, "error": "output is required"})
                return

            asyncio.run(generate_speech(text, voice, output, max(0, retries)))
            size = os.path.getsize(output) if os.path.exists(output) else 0
            if size <= 0:
                self._json(500, {"ok": False, "error": "empty audio generated"})
                return
            self._json(200, {"ok": True, "voice": voice, "output": output, "size": size})
        except Exception as exc:
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

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
    print(json.dumps({"ok": True, "host": args.host, "port": args.port}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
