#!/usr/bin/env python3
import html
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def extract(path: Path):
    slides = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda name: int(re.search(r"slide(\d+)\.xml$", name).group(1)),
        )
        for page, name in enumerate(names, start=1):
            root = ET.fromstring(archive.read(name))
            texts = [html.unescape((node.text or "").strip()) for node in root.findall(".//a:t", NS)]
            texts = [value for value in texts if value]
            slides.append({
                "page": page,
                "title": texts[0] if texts else f"第{page}页",
                "text": "\n".join(texts),
            })
    return slides


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: extract-pptx-text.py <file.pptx>")
    path = Path(sys.argv[1])
    if path.suffix.lower() != ".pptx" or not path.is_file():
        raise SystemExit("a readable .pptx file is required")
    print(json.dumps({"file": path.name, "slides": extract(path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
