from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
DOWNLOADS = MODELS / "_downloads"

ASSETS = [
    {
        "name": "kws",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile.tar.bz2",
        "archive": "kws.tar.bz2",
        "target": "kws",
        "required": [
            "tokens.txt",
            "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
            "decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
            "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        ],
    },
    {
        "name": "asr",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        "archive": "asr.tar.bz2",
        "target": "asr",
        "required": ["model.int8.onnx", "tokens.txt"],
    },
]

VAD_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "silero_vad.onnx"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "VoiceGateway-P1/0.1"})
    print(f"下载：{url}")
    with urllib.request.urlopen(request, timeout=60) as response, path.open("wb") as out:
        total = int(response.headers.get("Content-Length", "0"))
        copied = 0
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            out.write(block)
            copied += len(block)
            if total:
                print(f"  {copied / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB", end="\r")
    print()


def extract_flat(archive: Path, target: Path, required: list[str]) -> None:
    temp = target.with_name(target.name + "-extracting")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    with tarfile.open(archive, "r:bz2") as tar:
        tar.extractall(temp, filter="data")
    roots = [item for item in temp.iterdir() if item.is_dir()]
    source = roots[0] if len(roots) == 1 else temp
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise RuntimeError(f"{archive.name}解压后缺少：{missing}")
    if target.exists():
        shutil.rmtree(target)
    if source == temp:
        temp.rename(target)
    else:
        source.rename(target)
        shutil.rmtree(temp)


def main() -> int:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    lock: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "assets": {},
    }
    for asset in ASSETS:
        target = MODELS / str(asset["target"])
        required = list(asset["required"])
        if not all((target / name).is_file() for name in required):
            archive = DOWNLOADS / str(asset["archive"])
            if not archive.is_file():
                download(str(asset["url"]), archive)
            extract_flat(archive, target, required)
        lock["assets"][str(asset["name"])] = {
            "source": asset["url"],
            "files": {
                name: sha256(target / name)
                for name in required
            },
        }

    vad = MODELS / "vad" / "silero_vad.onnx"
    if not vad.is_file():
        download(VAD_URL, vad)
    lock["assets"]["vad"] = {
        "source": VAD_URL,
        "files": {"silero_vad.onnx": sha256(vad)},
    }
    lock_path = MODELS / "models.lock.json"
    lock_path.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"模型准备完成：{MODELS}")
    print(f"摘要文件：{lock_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"模型下载失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)

