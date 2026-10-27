"""用 pypdf 读公告 PDF 的前几页文本，并抽出目录式标题和开头。

pypdf 按阅读顺序输出「标签、数字、数字」。封面若是图片，第一页可能没有字，调用方应多读几页。
只在读过的页覆盖全文、且每一页都没有文字时记 no_text（扫描件）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

_HEADING = re.compile(
    r"^(?:"
    r"第[0-9一二三四五六七八九十]{1,3}节\s*\S"
    r"|[一二三四五六七八九十]{1,3}、\s*\S"
    r"|[（(][一二三四五六七八九十]{1,3}[)）]\s*\S"
    r"|\d{1,2}[\.、．]\s*[\u4e00-\u9fff]"
    r")"
)


@dataclass(frozen=True)
class PdfText:
    pages: list[str]
    page_count: int
    truncated: bool
    error: str | None


def read_pdf(path: Path, *, max_pages: int) -> PdfText:
    try:
        reader = PdfReader(str(path))
        try:
            total = len(reader.pages)
            count = max(0, min(max_pages, total))
            pages = [(reader.pages[i].extract_text() or "") for i in range(count)]
        finally:
            close = getattr(reader, "close", None)
            if close is not None:
                close()
    except Exception:
        return PdfText([], 0, False, "pdf_unreadable")
    truncated = total > count
    blank = bool(pages) and all(not page.strip() for page in pages)
    error = "no_text" if (total == 0 or (blank and not truncated)) else None
    return PdfText(pages, total, truncated, error)


def headings(text: str) -> list[str]:
    """抽出「一、」「（一）」「第X节」「1.」这类标题行，供目录和开头使用。"""
    found: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if raw and len(raw) <= 120 and _HEADING.match(raw):
            found.append(raw)
    return found


def opening(text: str, chars: int) -> str:
    if chars <= 0:
        return ""
    return text[:chars]
