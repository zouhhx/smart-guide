from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntegrityResult:
    asset: str
    filename: str
    expected: str
    actual: str

    @property
    def valid(self) -> bool:
        return self.expected.lower() == self.actual.lower()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_model_lock(project_root: Path) -> list[IntegrityResult]:
    lock_path = project_root / "models" / "models.lock.json"
    if not lock_path.is_file():
        raise FileNotFoundError(f"模型摘要文件不存在：{lock_path}")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    output: list[IntegrityResult] = []
    for asset, metadata in lock.get("assets", {}).items():
        for filename, expected in metadata.get("files", {}).items():
            path = project_root / "models" / asset / filename
            actual = sha256(path) if path.is_file() else "MISSING"
            output.append(
                IntegrityResult(
                    asset=str(asset),
                    filename=str(filename),
                    expected=str(expected),
                    actual=actual,
                )
            )
    return output

