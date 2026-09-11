from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pypinyin import Style, lazy_pinyin


class KeywordError(ValueError):
    """中文唤醒词无法转换为模型token。"""


def _load_tokens(path: Path) -> set[str]:
    values: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        token = line.split()[0]
        if not token.startswith("<"):
            values.add(token)
    return values


def _segment_syllable(syllable: str, vocabulary: set[str]) -> list[str]:
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
        raise KeywordError(f"模型tokens无法表示拼音：{syllable}")
    return list(result)


def tokenize_chinese_keyword(text: str, tokens_path: Path) -> list[str]:
    keyword = "".join(text.split())
    if not keyword:
        raise KeywordError("唤醒词不能为空")
    if not tokens_path.is_file():
        raise KeywordError(f"KWS tokens不存在：{tokens_path}")

    vocabulary = _load_tokens(tokens_path)
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
    return output


def write_keywords_file(text: str, tokens_path: Path, output_path: Path) -> str:
    tokens = tokenize_chinese_keyword(text, tokens_path)
    line = f"{' '.join(tokens)} @{text}"
    if output_path.is_file():
        existing = output_path.read_text(encoding="utf-8").strip()
        if existing == line:
            return line
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(line + "\n", encoding="utf-8")
    return line
