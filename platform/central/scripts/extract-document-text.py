#!/usr/bin/env python3
import html
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
MAX_CHUNK_CHARS = 1800


def chunk_text(paragraphs, prefix):
    chunks = []
    current = []
    current_length = 0
    for paragraph in paragraphs:
        value = " ".join(str(paragraph or "").split()).strip()
        if not value:
            continue
        if current and current_length + len(value) + 1 > MAX_CHUNK_CHARS:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        while len(value) > MAX_CHUNK_CHARS:
            head, value = value[:MAX_CHUNK_CHARS], value[MAX_CHUNK_CHARS:]
            if current:
                chunks.append("\n".join(current))
                current = []
                current_length = 0
            chunks.append(head)
        if value:
            current.append(value)
            current_length += len(value) + 1
    if current:
        chunks.append("\n".join(current))
    return [
        {"page": index, "title": f"{prefix}{index}", "text": value}
        for index, value in enumerate(chunks, start=1)
        if value.strip()
    ]


def extract_docx(path):
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.findall(".//w:p", WORD_NS):
        texts = [html.unescape(node.text or "") for node in paragraph.findall(".//w:t", WORD_NS)]
        value = "".join(texts).strip()
        if value:
            paragraphs.append(value)
    return chunk_text(paragraphs, "文档片段")


def resolve_pdftotext():
    configured = os.environ.get("PDFTOTEXT_EXE", "").strip()
    candidates = [
        configured,
        shutil.which("pdftotext") or "",
        r"C:\Program Files\Git\mingw64\bin\pdftotext.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("pdftotext not found; set PDFTOTEXT_EXE")


def extract_pdf(path):
    result = subprocess.run(
        [resolve_pdftotext(), "-layout", "-enc", "UTF-8", str(path), "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(error or f"pdftotext exited with {result.returncode}")
    pages = result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n").split("\f")
    units = []
    for page_number, page_text in enumerate(pages, start=1):
        paragraphs = [line.strip() for line in page_text.splitlines() if line.strip()]
        for unit in chunk_text(paragraphs, f"PDF第{page_number}页片段"):
            units.append({
                "page": len(units) + 1,
                "title": unit["title"],
                "text": unit["text"],
                "sourcePage": page_number,
            })
    return units


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: extract-document-text.py <file.pdf|file.docx>")
    path = Path(sys.argv[1])
    if not path.is_file():
        raise SystemExit("a readable document file is required")
    extension = path.suffix.lower()
    if extension == ".pdf":
        units = extract_pdf(path)
    elif extension == ".docx":
        units = extract_docx(path)
    else:
        raise SystemExit("only PDF and DOCX are supported")
    print(json.dumps({"file": path.name, "units": units}, ensure_ascii=False))


if __name__ == "__main__":
    main()
