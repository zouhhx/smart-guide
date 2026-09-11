#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Import PPT materials from an Excel manifest into the exhibition media library
and generate page-level Markdown knowledge units.

The script intentionally uses only Python standard-library modules so it can run
on the 61 machine without installing openpyxl or other packages.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


DEFAULT_EXCEL = (
    "C:/Users/64930/Documents/WXWork/1688858274685838/Cache/File/2026-06/"
    "\u4e92\u52a8\u95ee\u7b54\u77e5\u8bc6\u5e93300\u4e2a\u6587\u4ef6.xlsx"
)

PPTX_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def clean(value: object) -> str:
    return str(value or "").strip()


def safe_name(value: str, limit: int = 80) -> str:
    value = clean(value)
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "未命名")[:limit]


def md_escape(value: str) -> str:
    return clean(value).replace("\r\n", "\n").replace("\r", "\n")


def yaml_scalar(value: str) -> str:
    value = clean(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def short_hash(value: str, length: int = 10) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def read_xlsx_rows(path: Path, sheet_name: str | None = None) -> list[list[str]]:
    with zipfile.ZipFile(path) as zf:
        shared_strings = read_shared_strings(zf)
        sheet_path = find_sheet_path(zf, sheet_name)
        root = ET.fromstring(zf.read(sheet_path))

    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", ns):
        values: dict[int, str] = {}
        max_col = -1
        for cell in row.findall("x:c", ns):
            ref = cell.attrib.get("r", "")
            col = column_index(ref)
            max_col = max(max_col, col)
            values[col] = cell_text(cell, shared_strings, ns)
        if max_col >= 0:
            rows.append([values.get(i, "") for i in range(max_col + 1)])
    return rows


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("x:si", ns):
        parts = [node.text or "" for node in item.findall(".//x:t", ns)]
        strings.append("".join(parts))
    return strings


def find_sheet_path(zf: zipfile.ZipFile, sheet_name: str | None) -> str:
    ns = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("rel:Relationship", ns)}
    sheets = workbook.findall(".//x:sheets/x:sheet", ns)
    chosen = None
    if sheet_name:
        for sheet in sheets:
            if sheet.attrib.get("name") == sheet_name:
                chosen = sheet
                break
    if chosen is None:
        chosen = sheets[0]
    rel_id = chosen.attrib.get(f"{{{ns['r']}}}id")
    target = rel_map.get(rel_id or "", "worksheets/sheet1.xml")
    return posixpath.normpath(posixpath.join("xl", target))


def column_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref.upper())
    if not letters:
        return 0
    index = 0
    for ch in letters.group(1):
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def cell_text(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", ns)).strip()
    value = cell.find("x:v", ns)
    if value is None or value.text is None:
        return ""
    raw = value.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw)].strip()
        except (ValueError, IndexError):
            return ""
    return raw.strip()


def manifest_records(excel_path: Path) -> list[dict[str, str]]:
    rows = read_xlsx_rows(excel_path, "文件清单")
    if not rows:
        return []
    header = [clean(v) for v in rows[0]]
    col = {name: idx for idx, name in enumerate(header)}
    name_col = col.get("文件名称", 0)
    url_col = col.get("文件地址", 1)
    folder_col = col.get("所属文件夹名称", 2)

    records = []
    for index, row in enumerate(rows[1:], start=1):
        name = clean(row[name_col] if name_col < len(row) else "")
        url = clean(row[url_col] if url_col < len(row) else "")
        folder = clean(row[folder_col] if folder_col < len(row) else "")
        if not name or not re.match(r"https?://", url, re.I):
            continue
        records.append({"index": index, "name": name, "url": url, "folder": folder})
    return records


def unique_media_name(record: dict[str, str], used: set[str]) -> str:
    ext = Path(urllib.parse.urlparse(record["url"]).path).suffix.lower() or Path(record["name"]).suffix.lower() or ".pptx"
    base = safe_name(Path(record["name"]).stem, 74)
    candidate = f"ppt300_{record['index']:03d}_{base}{ext}"
    n = 2
    while candidate.lower() in used:
        candidate = f"ppt300_{record['index']:03d}_{base}_{n}{ext}"
        n += 1
    used.add(candidate.lower())
    return candidate


def existing_media_name(record: dict[str, str], library: dict[str, object]) -> str:
    expected_id = f"ppt300:{short_hash(record['url'], 16)}"
    for row in library.get("files") or []:
        if not isinstance(row, dict):
            continue
        if clean(row.get("id")) == expected_id or clean(row.get("sourceUrl")) == record["url"]:
            return clean(row.get("name"))
    return ""


def download_file(url: str, target: Path, retries: int = 2) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target.stat().st_size
    tmp = target.with_suffix(target.suffix + ".downloading")
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "exhibition-hall-importer/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
            tmp.replace(target)
            return target.stat().st_size
        except Exception:
            if tmp.exists():
                tmp.unlink()
            if attempt >= retries:
                raise
            time.sleep(2 * (attempt + 1))
    return 0


def pptx_slides(path: Path) -> list[dict[str, object]]:
    if path.suffix.lower() != ".pptx" or not path.exists():
        return [{"page": 1, "title": Path(path).stem, "text": ""}]
    slides: list[dict[str, object]] = []
    try:
        with zipfile.ZipFile(path) as zf:
            slide_names = sorted(
                [name for name in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)],
                key=lambda item: int(re.search(r"slide(\d+)\.xml$", item).group(1)),
            )
            for idx, slide_name in enumerate(slide_names, start=1):
                root = ET.fromstring(zf.read(slide_name))
                texts = [html.unescape(node.text or "").strip() for node in root.findall(".//a:t", PPTX_NS)]
                texts = [text for text in texts if text]
                title = texts[0] if texts else f"P{idx}"
                slides.append({"page": idx, "title": title, "text": "\n".join(texts)})
    except Exception as err:
        print(f"[WARN] PPTX parse failed for {path}: {err}", file=sys.stderr)
    return slides or [{"page": 1, "title": Path(path).stem, "text": ""}]


def make_commentary(title: str, slide_text: str) -> dict[str, str]:
    body = re.sub(r"\s+", " ", clean(slide_text))
    if not body:
        body = title
    concise = f"本页快速介绍“{title}”，重点关注页面主线和核心价值。{body[:80]}"
    standard = f"本页围绕“{title}”展开说明。{body[:220]}".strip()
    detailed = (
        f"本页详细介绍“{title}”。讲解时应先说明页面主题，再结合页面文字展开核心能力、"
        f"应用场景和价值收益。页面原始信息包括：{body[:520]}"
    )
    return {
        "brief": concise,
        "standard": standard,
        "detail": detailed,
        "combined": f"【精炼版】\n{concise}\n\n【标准版】\n{standard}\n\n【详细版】\n{detailed}",
    }


def frontmatter(data: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {yaml_scalar(str(item))}")
        else:
            lines.append(f"{key}: {yaml_scalar(str(value))}")
    lines.append("---")
    return "\n".join(lines)


def write_markdown_unit(
    vault_dir: Path,
    record: dict[str, str],
    media_name: str,
    slide: dict[str, object],
    commentary: dict[str, str],
    dry_run: bool,
) -> Path:
    page = int(slide["page"])
    ppt_title = Path(record["name"]).stem
    slide_title = safe_name(str(slide.get("title") or f"P{page}"), 48)
    folder = safe_name(record.get("folder") or "未分类", 64)
    unit_name = f"{page:02d}-{safe_name(ppt_title, 52)}-{slide_title}"
    unit_dir = vault_dir / folder / safe_name(ppt_title, 64) / unit_name
    unit_id = f"PPT300-{record['index']:03d}-P{page:02d}-{short_hash(record['url'])}"
    tags = [folder, ppt_title, slide_title, "PPT知识单元"]
    base_fm = {
        "id": unit_id,
        "title": f"{ppt_title} / P{page} / {slide_title}",
        "type": "ppt_page",
        "status": "published",
        "resource": media_name,
        "page": str(page),
        "source_from": record.get("folder", ""),
        "tags": tags,
    }
    raw_text = md_escape(str(slide.get("text") or ""))
    if dry_run:
        return unit_dir
    unit_dir.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}

    def fm(slice_id: str, slice_type: str, suffix: str) -> str:
        return frontmatter({
            **base_fm,
            "id": f"{unit_id}-{slice_id}",
            "type": slice_type,
            "title": f"{base_fm['title']} / {suffix}",
        })

    files["README.md"] = f"""{fm('README', 'ppt_page_readme', 'README')}

# 样板单元：{ppt_title} / P{page} / {slide_title}

## 核心定位

本单元来自 `{record['name']}` 第 {page} 页，用于展厅讲解、语音问答、向量检索和播控联动。

## 文件入口

1. [[00-L0运行对象]]
2. [[01-L1原始资料登记]]
3. [[02-L2知识对象清单]]
4. [[03-L3知识包配置]]
5. [[04-三档讲解词]]
6. [[05-FAQ问答口径]]
7. [[06-敏感问题兜底]]
8. [[07-测试记录模板]]
"""
    files["00-L0运行对象.md"] = f"""{fm('L0', 'runtime_object', '运行对象')}

## 运行对象

```yaml
unit_id: {unit_id}
project: {ppt_title}
source_page: {page}
exhibit: {slide_title}
terminal:
  - 大屏
  - 数字人
  - 语音问答
scenario:
  - 领导快速参观
  - 常规客户接待
  - 深入业务交流
default_script: 标准版
resource_file: {media_name}
```
"""
    files["01-L1原始资料登记.md"] = f"""{fm('L1', 'source_register', '原始资料登记')}

## 原始资料登记

| 字段 | 内容 |
|---|---|
| PPT文件名 | {record['name']} |
| 下载地址 | {record['url']} |
| 所属文件夹 | {record.get('folder', '')} |
| 页码 | {page} |
| 页面标题 | {slide_title} |

## 页面显性文字

{raw_text or '待从页面截图或人工校验补充。'}
"""
    files["02-L2知识对象清单.md"] = f"""{fm('L2', 'knowledge_objects', '知识对象清单')}

## 知识对象清单

| 对象类型 | 内容 |
|---|---|
| 核心概念 | {slide_title} |
| 所属材料 | {ppt_title} |
| 所属专题 | {record.get('folder', '')} |
| 页面事实 | {raw_text[:260] or '待人工补充'} |
| 边界口径 | 不新增未出现在页面或已确认资料中的指标、案例和承诺。 |
"""
    files["03-L3知识包配置.md"] = f"""{fm('L3', 'knowledge_package', '知识包配置')}

## 知识包配置

| 用户意图 | 调用内容 |
|---|---|
| 快速介绍 / 简单说 / 30秒 | 04-三档讲解词.md / 精炼版 |
| 默认介绍 / 讲一下 / 这页讲什么 | 04-三档讲解词.md / 标准版 |
| 详细讲 / 展开讲 / 讲深一点 | 04-三档讲解词.md / 详细版 |
| 明确问题 | 05-FAQ问答口径.md |
| 指标 / 报价 / 工期 / 接口 / 客户名单 | 06-敏感问题兜底.md |
"""
    files["04-三档讲解词.md"] = f"""{fm('COMMENTARY', 'commentary_scripts', '三档讲解词')}

## 精炼版

{commentary['brief']}

## 标准版

{commentary['standard']}

## 详细版

{commentary['detail']}
"""
    files["05-FAQ问答口径.md"] = f"""{fm('FAQ', 'faq_basis', 'FAQ问答口径')}

## 可转FAQ

问：{slide_title}主要讲什么？
答：{commentary['standard']}

问：{ppt_title}里的{slide_title}解决什么问题？
答：本页围绕{slide_title}展开，回答时以页面原始文字和已确认材料为准，重点说明应用场景、能力和价值。

问：当前这页有什么价值？
答：可从页面呈现的能力、场景和收益方向进行说明；涉及具体指标时，不把展示指标直接讲成项目承诺。

问：{slide_title}能怎么落地？
答：落地需要结合客户业务场景、数据基础、系统接入和现场部署条件评估，现场不直接承诺价格、工期或最终效果。
"""
    files["06-敏感问题兜底.md"] = f"""{fm('SENSITIVE', 'sensitive_boundary', '敏感问题兜底')}

## 敏感问题兜底

| 类型 | 典型问题 | 前台回答方向 | 后台处理 |
|---|---|---|---|
| 指标 | 这个准确率或提升比例能保证吗？ | 当前指标用于说明能力方向，具体项目需结合数据质量、部署范围和验收标准确认。 | metric_boundary |
| 商务 | 多少钱？ | 价格需要结合建设范围、系统接入、设备数量和服务要求测算。 | business_handoff |
| 工期 | 多久能上线？ | 工期取决于数据接入、系统改造、现场部署和测试范围。 | delivery_handoff |
| 数据安全 | 数据会不会外传？ | 可按客户数据安全要求、本地化部署、权限控制和日志审计机制设计。 | data_security |
| 案例细节 | 能讲客户合同或内部数据吗？ | 未公开客户信息、合同细节和内部数据不在现场展开。 | case_boundary |
"""
    files["07-测试记录模板.md"] = f"""{fm('TEST', 'test_template', '测试记录模板')}

## 测试记录模板

| 测试类型 | 示例问题 | 期望 |
|---|---|---|
| 默认讲解 | 介绍一下当前内容 | 标准版 |
| 快速讲解 | 快速介绍一下 | 精炼版 |
| 详细讲解 | 展开讲讲 | 详细版 |
| FAQ | {slide_title}解决什么问题？ | FAQ |
| 指标边界 | 这个指标能承诺吗？ | 敏感兜底 |
| 数据安全 | 数据怎么保护？ | 数据安全口径 |
"""
    for name, content in files.items():
        (unit_dir / name).write_text(content.strip() + "\n", encoding="utf-8")
    return unit_dir


def load_library(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"generatedAt": now_iso(), "files": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {"generatedAt": now_iso(), "files": raw}
    raw.setdefault("files", [])
    return raw


def upsert_library_item(library: dict[str, object], item: dict[str, object]) -> None:
    rows = library.setdefault("files", [])
    assert isinstance(rows, list)
    for idx, row in enumerate(rows):
        if clean(row.get("id")) == clean(item.get("id")) or clean(row.get("name")) == clean(item.get("name")):
            rows[idx] = {**row, **item}
            return
    rows.append(item)


def public_url(base_url: str, media_name: str) -> str:
    return f"{base_url.rstrip('/')}/{urllib.parse.quote(media_name)}"


def run(args: argparse.Namespace) -> int:
    excel_path = Path(args.excel)
    media_dir = Path(args.media_dir)
    vault_dir = Path(args.vault_dir)
    library_path = Path(args.library)
    records = manifest_records(excel_path)
    if args.offset:
        records = records[args.offset :]
    if args.limit:
        records = records[: args.limit]

    library = load_library(library_path)
    existing_rows = library.get("files") or []
    used_names = {clean(row.get("name")).lower() for row in existing_rows if isinstance(row, dict)}
    summary = {"records": 0, "downloaded": 0, "slides": 0, "markdownUnits": 0, "errors": []}

    for record in records:
        summary["records"] += 1
        media_name = existing_media_name(record, library) or unique_media_name(record, used_names)
        used_names.add(media_name.lower())
        target = media_dir / media_name
        try:
            if args.download:
                size = download_file(record["url"], target)
                summary["downloaded"] += 1
            else:
                size = target.stat().st_size if target.exists() else 0
            slides = pptx_slides(target if target.exists() else Path(record["name"]))
            commentary_pages: dict[str, str] = {}
            unit_dirs = []
            for slide in slides:
                commentary = make_commentary(str(slide.get("title") or ""), str(slide.get("text") or ""))
                page = str(slide["page"])
                commentary_pages[page] = commentary["combined"]
                unit_dir = write_markdown_unit(vault_dir, record, media_name, slide, commentary, args.dry_run)
                unit_dirs.append(str(unit_dir))
                summary["slides"] += 1
                summary["markdownUnits"] += 1

            item = {
                "index": len(library.get("files") or []) + 1,
                "id": f"ppt300:{short_hash(record['url'], 16)}",
                "title": Path(record["name"]).stem,
                "name": media_name,
                "originalName": record["name"],
                "description": record.get("folder", ""),
                "keywords": "、".join(filter(None, [record.get("folder", ""), Path(record["name"]).stem])),
                "commentary": "&&".join(commentary_pages[key] for key in sorted(commentary_pages, key=lambda x: int(x))),
                "commentaryPages": commentary_pages,
                "programTitle": record.get("folder", ""),
                "ext": Path(media_name).suffix.lower(),
                "type": "ppt",
                "size": size,
                "slideCount": len(slides),
                "url": public_url(args.public_file_base_url, media_name),
                "sourceUrl": record["url"],
                "thumbnailUrl": "",
                "knowledgeUnitDirs": unit_dirs,
                "importedAt": now_iso(),
            }
            if not args.dry_run:
                upsert_library_item(library, item)
            print(f"[OK] {record['index']:03d} {record['name']} -> {media_name} ({len(slides)} pages)")
        except Exception as err:
            message = f"{record['index']:03d} {record['name']}: {err}"
            print(f"[ERROR] {message}", file=sys.stderr)
            summary["errors"].append(message)

    if not args.dry_run:
        library["updatedAt"] = now_iso()
        rows = library.get("files") or []
        for idx, row in enumerate(rows, start=1):
            if isinstance(row, dict):
                row["index"] = idx
        library_path.parent.mkdir(parents=True, exist_ok=True)
        library_path.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["errors"] else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Import PPT manifest into media library and Markdown knowledge vault.")
    parser.add_argument("--excel", default=DEFAULT_EXCEL, help="Excel manifest path.")
    parser.add_argument("--media-dir", default=str(root / "media"), help="Target media directory.")
    parser.add_argument("--library", default=str(root / "media" / "library.json"), help="media/library.json path.")
    parser.add_argument("--vault-dir", default=str(root / "data" / "obsidian-vault"), help="Markdown knowledge vault directory.")
    parser.add_argument("--public-file-base-url", default="http://192.168.16.61:3001", help="Public base URL for downloaded PPT files.")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many manifest rows.")
    parser.add_argument("--limit", type=int, default=0, help="Limit records for test import.")
    parser.add_argument("--download", action="store_true", help="Download PPT files into media-dir.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing files.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
