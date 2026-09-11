from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np


def read(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError(f"只支持单声道16-bit PCM：{path}")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").copy()
        return samples, wav.getframerate()


def main() -> int:
    parser = argparse.ArgumentParser(description="拼接唤醒词、静音和问题WAV")
    parser.add_argument("wake")
    parser.add_argument("question")
    parser.add_argument("output")
    parser.add_argument("--gap", type=float, default=0.6)
    args = parser.parse_args()
    wake, wake_rate = read(Path(args.wake))
    question, question_rate = read(Path(args.question))
    if wake_rate != question_rate:
        raise ValueError("两个WAV采样率必须相同")
    silence = np.zeros(round(args.gap * wake_rate), dtype=np.int16)
    tail = np.zeros(round(1.5 * wake_rate), dtype=np.int16)
    output = np.concatenate((wake, silence, question, tail))
    with wave.open(args.output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(wake_rate)
        wav.writeframes(output.astype("<i2").tobytes())
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

