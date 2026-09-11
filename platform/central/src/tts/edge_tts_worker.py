import argparse
import asyncio
import json
import os
import sys

try:
    import edge_tts
except ImportError as exc:
    print(
        json.dumps(
            {
                "ok": False,
                "error": "Python package edge-tts is not installed. Run: python -m pip install edge-tts",
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


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


async def generate_speech(text: str, voice: str, output: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    communicate = edge_tts.Communicate(text, normalize_voice(voice))
    await communicate.save(output)


async def generate_speech_with_retry(text: str, voice: str, output: str, retries: int) -> None:
    last_error = None
    for attempt in range(retries + 1):
        try:
            if os.path.exists(output):
                os.remove(output)
            await generate_speech(text, voice, output)
            return
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            await asyncio.sleep(0.5 * (attempt + 1))
    raise last_error


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate mp3 with Microsoft Edge TTS.")
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    with open(args.text_file, "r", encoding="utf-8") as handle:
        text = handle.read().strip()

    if not text:
        print(json.dumps({"ok": False, "error": "text is required"}), file=sys.stderr)
        return 1

    asyncio.run(generate_speech_with_retry(text, args.voice, args.output, max(0, args.retries)))

    if not os.path.exists(args.output) or os.path.getsize(args.output) <= 0:
        print(json.dumps({"ok": False, "error": "empty audio generated"}), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "voice": normalize_voice(args.voice),
                "output": args.output,
                "size": os.path.getsize(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
