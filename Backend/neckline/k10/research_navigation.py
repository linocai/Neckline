"""Remove a recognized publisher widget without moving source coordinates."""
from __future__ import annotations

from hashlib import sha256
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

NAVIGATION_VERSION = "sina-ranked-news-widget-v1"


def sina_source(value: Mapping[str, Any]) -> bool:
    candidates = [value]
    candidates.extend(value[k] for k in ('metadata', 'provenance') if isinstance(value.get(k), Mapping))
    for row in candidates:
        for key in ('publisher', 'media', 'url', 'canonicalUrl'):
            text = row.get(key)
            if isinstance(text, str):
                try:
                    host = urlsplit(text if '://' in text else '//'+text).hostname
                except ValueError:
                    continue
                if host in {'finance.sina.com.cn', 'finance.sina.cn'}:
                    return True
    return False


def navigation_view(text: str, *, enabled: bool) -> tuple[str, dict[str, Any] | None]:
    """Recognize the complete 01–10 news widget, never generic numbered facts.

    Require the publisher's three distinctive navigation columns and all ten
    ranks. The surrounding excerpt ellipses and article prose stay verbatim.
    Spaces retain every original line/character offset used by saved locators.
    """
    if not enabled:
        return text, None
    spans = []
    for first in re.finditer(r'-[ \t]*01/', text):
        prefix = text[:first.start()]
        if prefix.rsplit('\n', 1)[-1].strip() and not prefix.rstrip().endswith('[...]'):
            continue
        ranks = list(re.finditer(r'(?:^|\n)[ \t]*-[ \t]*(0[1-9]|10)/', text[first.start():]))
        if [m[1] for m in ranks[:10]] != [f'{n:02}' for n in range(1,11)]:
            continue
        segment = text[first.start():]
        if any('\n' in segment[a.end():b.start()] for a,b in zip(ranks[:9],ranks[1:10])):
            continue
        last_start = first.start()+ranks[9].start()
        end = text.find('\n', last_start+1)
        end = len(text) if end < 0 else end
        gap = text.find('[...]', last_start, end)
        if gap >= 0:
            end = gap
        block = text[first.start():end]
        if not all(label in block for label in ('操盘必读', '股海导航', '四大证券报头版头条')):
            continue
        spans.append((first.start(), end))
    if not spans:
        return text, None
    chars = list(text)
    for start, end in spans:
        chars[start:end] = [c if c in '\r\n' else ' ' for c in text[start:end]]
    visible = ''.join(chars)
    return visible, {'version':NAVIGATION_VERSION, 'originalTextSha256':sha256(text.encode()).hexdigest(),
        'visibleTextSha256':sha256(visible.encode()).hexdigest(), 'offsetUnit':'python_unicode_codepoint',
        'removedNavigationRanges':[{'startOffset':a,'endOffset':b} for a,b in spans]}
