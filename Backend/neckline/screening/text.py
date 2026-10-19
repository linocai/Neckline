"""原件正文清洗与摘取。"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Sequence

_BLOCK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "section", "table"}
_SKIP_TAGS = {"script", "style", "iframe", "noscript"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def plain_text(raw: str | None) -> str:
    """HTML 或纯文本 → 规整的纯文本（段落保留为换行）。"""
    if not raw:
        return ""
    if "<" in raw and ">" in raw:
        parser = _TextExtractor()
        parser.feed(raw)
        parser.close()
        raw = "".join(parser.parts)
    text = html.unescape(raw)
    text = re.sub(r"[ \t　\xa0​]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def excerpt_for(text: str, needles: list[str], *, lead: int, window: int, limit: int) -> str:
    """长文只取导语加命中词前后的片段，按原文顺序拼接，总长不超过 limit。"""
    if len(text) <= limit:
        return text
    spans = [(0, min(lead, len(text)))]
    for needle in needles:
        start = 0
        while (pos := text.find(needle, start)) >= 0:
            spans.append((max(0, pos - window), min(len(text), pos + len(needle) + window)))
            start = pos + len(needle)
    spans.sort()
    merged: list[list[int]] = []
    for begin, end in spans:
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])
    pieces, total = [], 0
    for begin, end in merged:
        piece = text[begin:end]
        if total + len(piece) > limit:
            piece = piece[: max(0, limit - total)]
        if piece:
            pieces.append(piece)
            total += len(piece)
        if total >= limit:
            break
    return " …… ".join(pieces)


def section_heading(text: str, names: Sequence[str]) -> str | None:
    """正文里这家公司的分节小标题。认「【简称：…】」，以及行首的「简称：…」「简称(代码)：…」。"""
    if not text:
        return None
    best: tuple[int, str] | None = None
    for name in sorted({n for n in names if isinstance(n, str) and len(n) >= 2}, key=len, reverse=True):
        escaped = re.escape(name)
        patterns = (
            re.compile(rf"(【{escaped}[：:][^】\n]{{1,120}}】)"),
            re.compile(rf"(?:^|\n)\s*({escaped}(?:[（(][^）)\n]{{0,24}}[）)])?[：:][^\n]{{1,120}})"),
        )
        for pattern in patterns:
            match = pattern.search(text)
            if match is None:
                continue
            heading = match.group(1).strip()
            if heading and (best is None or match.start() < best[0]):
                best = (match.start(), heading)
    return None if best is None else best[1]
