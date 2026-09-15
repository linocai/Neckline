"""Local source admission and traceable structural reads; never sample source prose.

Offsets always address the unchanged Python source string (not a normalized copy).
Pagination limits catalogue transport only: every block remains addressable and
searchable. A caller's explicit request budget applies to a complete evidence unit,
including its headings, units, notes and adjacent qualifying statements.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from hashlib import sha256
import re
from typing import Any, Mapping
from urllib.parse import quote, unquote

INDEX_VERSION = "k10-source-index-3.3.0-b70-qualified-3"
# Local read transport granularity, not a source/report quota. Larger prose
# remains searchable through exact sentence locators; nothing is sampled or
# discarded. Physical model capacity must never enlarge this reading unit.
MAX_FRAGMENT_CHARACTERS = 12_000
MAX_OUTLINE_LOCATORS = 128
_PREVIEW_CHARACTERS = 240


def _merge_support_ranges(ranges):
    merged = []
    priority = {'heading': 0, 'sentence_context': 1, 'qualifier': 2, 'footnote': 3}
    for start, end, kind in sorted(ranges):
        if merged and start < merged[-1][1]:
            a, b, previous_kind = merged[-1]
            merged[-1] = (a, max(b, end), max((previous_kind, kind), key=priority.__getitem__))
        else:
            merged.append((start, end, kind))
    return merged


class _SupportRanges:
    """Shared disjoint context with exact overlap counts, without per-child copies."""
    def __init__(self, ranges):
        self.ranges = _merge_support_ranges(ranges)
        self.starts = [a for a, _, _ in self.ranges]
        self.ends = [b for _, b, _ in self.ranges]
        self.prefix = [0]
        for a, b, _ in self.ranges:
            self.prefix.append(self.prefix[-1] + b-a)

    def overlap(self, start, end):
        first, last = bisect_right(self.ends, start), bisect_left(self.starts, end)
        if first >= last:
            return 0
        return (self.prefix[last] - self.prefix[first]
                - max(0, start-self.starts[first]) - max(0, self.ends[last-1]-end))


class _ReferenceIndex:
    """Parse references once; overlapping support ranges share indexed lookups."""
    def __init__(self, text: str):
        self.matches = [(match.start(), match.end(), f'[{match[1]}]' if match[1] else match[2])
            for match in re.finditer(r'\[(\d+)\]|([①②③④⑤⑥⑦⑧⑨⑩])', text)]
        self.starts = [start for start, _, _ in self.matches]
        self.ranges: dict[tuple[int, int], tuple[str, ...]] = {}
        self.parent_support: dict[str, _SupportRanges] = {}

    def within(self, start: int, end: int) -> tuple[str, ...]:
        key = (start, end)
        if key not in self.ranges:
            first, last = bisect_left(self.starts, start), bisect_left(self.starts, end)
            self.ranges[key] = tuple(dict.fromkeys(ref for _, stop, ref in self.matches[first:last] if stop <= end))
        return self.ranges[key]


@dataclass(frozen=True)
class MaterialAdmission:
    state: str
    reason: str
    content_sha256: str
    requires_current_event_locator: bool = False


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    number: int
    text: str


@dataclass(frozen=True)
class _Block:
    locator: str
    kind: str
    start: int
    end: int
    line_start: int
    line_end: int
    headings: tuple[tuple[int, int], ...] = ()
    parent_locator: str | None = None
    parent_qualifiers: tuple[tuple[int, int], ...] = ()


def _source_text(document: Any) -> str:
    if not getattr(document, "analysis_text", None):
        # Restart/query restoration loads the immutable raw source row. Use
        # the same deterministic representation as initial understanding, so
        # saved paragraph/table locators do not change into raw HTML offsets.
        from .discovery import DiscoveryDocument, prepare_document_for_analysis
        if isinstance(document, DiscoveryDocument):
            document = prepare_document_for_analysis(document)
    for name in ("analysis_text", "original_text", "excerpt"):
        value = getattr(document, name, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _metadata(document: Any) -> Mapping[str, Any]:
    value = getattr(document, "metadata", None)
    return value if isinstance(value, Mapping) else {}


def _titles(document: Any) -> list[str]:
    values = [getattr(document, "title", None), *(_metadata(document).get(k) for k in ("title", "documentTitle"))]
    return list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))


def _title(document: Any) -> str:
    return " · ".join(_titles(document))


_PROSPECTUS = r"(?:招股说明书|招股意向书|招股书|prospectus)"
_EDITION = r"(?:申报稿|注册稿|注册制|草案|修订稿|修订版|更新稿|上会稿|预披露稿|封卷稿|补充材料|补充文件|摘要|draft|amended|amendment|supplement|supplemental|preliminary|final|updated|registration|no\d+|forms?[- ]?[sf][- ]?1|\d{4}年(?:\d{1,2}月(?:\d{1,2}日)?)?|\d{4}(?:年|[-./]\d{1,2})?(?:[-./]\d{1,2})?)"


def _is_prospectus_document_title(value: str) -> bool:
    """Recognize document titles, including edition suffixes, not news mentioning one."""
    compact = re.sub(r"\s+", "", value).casefold().strip()
    compact = re.sub(r"\.(?:pdf|html?|docx?)(?:\?.*)?$", "", compact)
    # News predicates describe an event concerning a filing, rather than naming it.
    news_predicate = r"(?:新闻|报道|解读|发布|公布|获批|受理|问询|披露|递交|提交|撤回|更新(?!稿|版)|显示|透露|称|回应|拟上市|据悉|怎么看|分析师|news|reports?|accordingto)"
    matches = list(re.finditer(_PROSPECTUS, compact, flags=re.I))
    if not matches:
        return False
    match = matches[-1]
    suffix = compact[match.end():]
    suffix = re.sub(r"[\s()（）\[\]【】《》:：_\-—·.,，。]", "", suffix)
    # No tail, or only document edition words/dates. A news sentence following
    # “招股说明书” cannot pass this check.
    edition_tail = re.fullmatch(rf"(?:{_EDITION})*", suffix, flags=re.I) is not None
    if not edition_tail:
        return False
    prefix = compact[:match.start()]
    if re.search(news_predicate, prefix, flags=re.I):
        return False
    return True


def _body_is_prospectus(text: str) -> bool:
    # A document's own cover heading plus filing sections is stronger than a
    # paragraph saying “the prospectus states ...”. Scan locally; no model call.
    cover = any(_is_prospectus_document_title(line.text) for line in _lines(text)
                if line.text.strip() and len(line.text) <= 512)
    if not cover:
        return False
    compact = re.sub(r"\s+", "", text).casefold()
    declaration = any(x in compact for x in ("本招股说明书", "发行人声明", "招股说明书摘要", "本次发行概况", "aboutthisprospectus"))
    sections = ("重大事项提示", "募集资金运用", "保荐机构", "发行人", "riskfactors", "useofproceeds", "tableofcontents")
    return declaration and sum(x in compact for x in sections) >= 2


_BACKGROUND_REPORT = r"(?:年度报告|半年度报告|季度报告|年报|annualreport|interimreport|quarterlyreport|form10[- ]?[kq])"


def _is_background_report_title(value: str) -> bool:
    compact = re.sub(r"\s+", "", value).casefold().strip()
    compact = re.sub(r"\.(?:pdf|html?|docx?)(?:\?.*)?$", "", compact)
    matches = list(re.finditer(_BACKGROUND_REPORT, compact, re.I))
    if not matches:
        return False
    match = matches[-1]
    prefix = compact[:match.start()]
    if re.search(r"新闻|报道|解读|显示|披露|发布|澄清|业绩|预告|快报|问询|news|accordingto", prefix):
        return False
    tail = re.sub(r"[()（）\[\]【】《》:：_\-—·.,，。]", "", compact[match.end():])
    return re.fullmatch(rf"(?:{_EDITION}|全文|更正后|摘要|\d+)*", tail, re.I) is not None


def requires_current_event_locator(document: Any) -> bool:
    declared = " ".join(str(_metadata(document).get(k, "")) for k in ("documentType", "contentType"))
    if any(_is_background_report_title(title) for title in _titles(document)):
        return True
    if re.search(r"annual.?report|interim.?report|quarterly.?report|年度报告|半年度报告|季度报告", declared, re.I) and not re.search(r"news|新闻|报道", declared, re.I):
        return True
    text = _source_text(document)
    cover = any(_is_background_report_title(line.text) for line in _lines(text) if len(line.text) <= 512)
    return cover and "本报告" in text and sum(term in text for term in
        ("公司简介和主要财务指标", "经营情况讨论与分析", "财务报告", "审计报告")) >= 2


def admit_material(document: Any) -> MaterialAdmission:
    """Exclude the prospectus itself consistently, including cached/untitled versions."""
    text = _source_text(document)
    declared = " ".join(str(_metadata(document).get(k, "")) for k in ("documentType", "contentType"))
    declared_prospectus = bool(re.search(_PROSPECTUS, declared, re.I)) and not re.search(r"news|新闻|报道", declared, re.I)
    excluded = any(_is_prospectus_document_title(t) for t in _titles(document)) or declared_prospectus or _body_is_prospectus(text)
    background = not excluded and requires_current_event_locator(document)
    return MaterialAdmission("excluded" if excluded else "admit",
        "prospectus_document" if excluded else "background_requires_event_question" if background else "source_admitted",
        sha256(text.encode("utf-8")).hexdigest(), background)


def _lines(text: str) -> list[_Line]:
    result = []
    offset = 0
    for number, raw in enumerate(text.splitlines(keepends=True), 1):
        end = offset + len(raw.rstrip("\r\n"))
        result.append(_Line(offset, end, number, text[offset:end]))
        offset += len(raw)
    return result


def _heading_level(value: str) -> int | None:
    value = value.strip()
    if not value or len(value) > 240:
        return None
    markdown = re.match(r"^(#{1,6})\s+\S", value)
    if markdown:
        return len(markdown[1])
    if re.match(r"^第[一二三四五六七八九十百零〇\d]+[章节部分]\s*", value):
        return 1 if "章" in value or "部分" in value else 2
    if re.match(r"^[一二三四五六七八九十]+[、.．]\s*[^。！？!?]+$", value):
        return 2
    if re.match(r"^[（(][一二三四五六七八九十\d]+[）)]\s*[^。！？!?]+$", value):
        return 3
    if re.match(r"^\d+(?:\.\d+)+\s+[^。！？!?]+$", value):
        return min(value.split()[0].count(".") + 1, 6)
    return None


def _is_table_line(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    return value.startswith("|") or value.count("|") >= 2 or "\t" in value or bool(re.search(r"\S\s{2,}\S.*\s{2,}\S", value))


def _qualifier(value: str) -> bool:
    return bool(re.match(r"^(?:[\s>*]*)(?:(?i:note[s]?:|footnote|unit[s]?:|period:|however\b|but\b|subject to\b|not\b|only\b)|注(?:释|意)?\s*[:：\d]|尾注|脚注|附注|说明[:：]|来源[:：]|数据来源|单位[:：]|口径[:：]|报告期[:：]|统计期间|截至|特别提示|风险提示|但(?:[，,是])?|不过|然而|上述|前述|其中|仅(?:为|供|指|包括)|尚未|并未|不(?:构成|代表|适用|包括)|\[\d+\]\s|[①②③④⑤⑥⑦⑧⑨⑩]|\*\s)", value))


def _sentence_qualifier(value: str) -> bool:
    # Prose often names its subject before stating the condition. Preserve
    # modal obligations, negation, conditional
    # connectives, effectiveness and measurement qualifications. This is a
    # conservative context-retention rule, not a classifier of legal meaning:
    # retaining an extra sentence is preferable to removing a prerequisite.
    # Paragraph/table boundaries still use the narrower structural label rule.
    return _qualifier(value) or bool(re.search(
        r'尚(?:未|需|须|待)|仍(?:需|须|待)|有待|必须|应当|应该|'
        r'(?<![所刚供内外按军])需(?!求|品)|(?<![胡触])须|'
        r'(?<![供响反效适顺相感呼对])应(?!用|答|对|急|届|收|付|聘|邀|诉|酬|景|声|试|力|变)|'
        r'未(?!来|央|名)|'
        r'不(?!断|同(?!意)|少(?!于)|锈|动产|错)|无法|无(?:须|需|权|效|约束|保证|承诺)|能否|是否|'
        r'仅(?:为|系|供|指|限)|只(?:有|要|能|可|是|为|限)|若(?!干)|如果|倘|除非|一旦|'
        r'可能|或将|(?<![虚模])拟(?!合)|预计|预期|有望|前提|(?:为|先决|必要|附加|附带|限制性)条件|'
        r'条件(?:下|成就|满足|未|尚|不|是|为)|取决于|假设|为准|生效|方可|方能|才能|才可|'
        r'可(?:撤销|取消|终止)|'
        r'\b(?:subject\s+to|contingent|conditional|uncertain(?:ty)?|not|no|without|only|unless|if|'
        r'must|shall|should|require[sd]?|pending|upon|provided|may|might|could|would|'
        r'estimated?|expected?|provisional)\b', value, re.I))


def _caption(value: str) -> bool:
    return bool(re.match(r"^\s*(?:表\s*[\d一二三四五六七八九十]|table\s*\d|[（(]?单位\s*[:：]|[（(]?报告期\s*[:：]|[（(]?统计期间\s*[:：])", value, re.I))


def _paragraph_blocks(text: str) -> list[_Block]:
    lines = _lines(text)
    result = []
    headings: list[tuple[int, int, int]] = []
    index = 0
    tables = 0
    while index < len(lines):
        line = lines[index]
        if not line.text.strip():
            index += 1
            continue
        level = _heading_level(line.text)
        if level is not None:
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, line.start, line.end))
            result.append(_Block(f"heading:{line.number}", "heading", line.start, line.end, line.number, line.number,
                                 tuple((a,b) for _,a,b in headings[:-1])))
            index += 1
            continue
        ancestry = tuple((a,b) for _,a,b in headings)
        if _is_table_line(line.text):
            tables += 1
            end_index = index
            while end_index + 1 < len(lines) and _is_table_line(lines[end_index+1].text):
                end_index += 1
            # Keep notes even when the extraction inserts a blank line after a table.
            cursor = end_index + 1
            while cursor < len(lines):
                if not lines[cursor].text.strip():
                    cursor += 1
                    continue
                if not _qualifier(lines[cursor].text):
                    break
                end_index = cursor
                cursor += 1
                # Wrapped continuation lines belong to this note until a paragraph
                # boundary/heading/table, including negation at the end of the note.
                while cursor < len(lines) and lines[cursor].text.strip() and _heading_level(lines[cursor].text) is None and not _is_table_line(lines[cursor].text):
                    end_index = cursor
                    cursor += 1
            start = line.start
            first_line = line.number
            while result and result[-1].kind == "paragraph" and result[-1].headings == ancestry and _caption(text[result[-1].start:result[-1].end]):
                previous = result.pop()
                start, first_line = previous.start, previous.line_start
            result.append(_Block(f"table:{tables}", "table", start, lines[end_index].end, first_line, lines[end_index].number, ancestry))
            index = end_index + 1
            continue
        end_index = index
        while end_index + 1 < len(lines):
            following = lines[end_index+1]
            if not following.text.strip() or _heading_level(following.text) is not None or _is_table_line(following.text):
                break
            # Table captions/units must stay attachable to the following table.
            if _caption(following.text):
                break
            end_index += 1
        result.append(_Block(f"paragraph:{line.number}", "paragraph", line.start, lines[end_index].end,
                             line.number, lines[end_index].number, ancestry))
        index = end_index + 1
    return result


def _continuous_ranges(text: str, ranges) -> tuple[tuple[int, int], ...]:
    """Combine original adjacent qualifications without copying their text."""
    merged = []
    for start, end in sorted(set(ranges)):
        if merged and (start <= merged[-1][1] or re.compile(r'\s*').fullmatch(text, merged[-1][1], start)):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _blocks(text: str) -> list[_Block]:
    """Refine oversized paragraphs at real sentence endings, retaining offsets.

    An indivisible long sentence is left explicitly unreadable. Commas, byte
    counts and arbitrary character positions cannot manufacture evidence units.
    Old paragraph/line locators remain catalogue parents, never aliases for the
    first sentence or the entire flattened source.
    """
    result = []
    paragraphs = _paragraph_blocks(text)
    reference_index = _ReferenceIndex(text)
    for position, block in enumerate(paragraphs):
        if block.kind != "paragraph" or block.end - block.start <= MAX_FRAGMENT_CHARACTERS:
            result.append(block)
            continue
        value = text[block.start:block.end]
        # A dot between digits or inside an identifier is not a sentence end.
        endings = list(re.finditer(r'(?:[。！？!?]+|(?<!\d)\.(?=\s|$))[”’"\')）]*(?:\[\d+\]|[①②③④⑤⑥⑦⑧⑨⑩])*', value))
        if not any(match.end() < len(value.rstrip()) for match in endings):
            # There is no real subdivision. Keep the original locator and
            # explicitly request a structured source instead of fabricating a
            # sentence that is merely the same entire paragraph.
            result.append(block)
            continue
        start = block.start
        children = []
        for end in [*(block.start + match.end() for match in endings), block.end]:
            if end <= start:
                continue
            if text[start:end].strip():
                children.append(_Block(f"sentence:{block.line_start}:{start}", "sentence", start, end,
                    block.line_start, block.line_end, block.headings, block.locator))
            start = end
        # Inherit structural caveats before footnote ranges are merged. A
        # numbered caveat can also be a definition; its kind must not erase it.
        qualifiers = [(a, b) for a, b, kind in _support(text, block, paragraphs, position=position, footnotes={},
                      reference_index=reference_index)
                      if kind == 'qualifier']
        qualifiers.extend((child.start, child.end) for child in children
                          if _sentence_qualifier(text[child.start:child.end]))
        qualifiers = _continuous_ranges(text, qualifiers)
        # Every child shares the same immutable ranges; sparse/interleaved
        # caveats must not create quadratic copies or repeated capacity scans.
        result.extend(replace(child, parent_qualifiers=qualifiers) for child in children)
    return result


def parse_find_location(location: Any) -> tuple[int, str] | None:
    if not isinstance(location, str):
        return None
    match = re.fullmatch(r"find:(?:(\d+):)?(.+)", location)
    if not match:
        return None
    term = unquote(match[2]).strip()
    return (int(match[1] or 0), term) if term else None


def catalogue_restart_location(location: Any) -> str | None:
    """An obsolete ordinal cursor must restart against the current index."""
    parsed = parse_find_location(location)
    if parsed:
        return f"find:0:{quote(parsed[1], safe='')}"
    if isinstance(location, str) and re.fullmatch(r'outline(?::\d+)?', location):
        return 'outline'
    within = re.fullmatch(r'within:(paragraph:\d+):\d+', location or '')
    return f'within:{within[1]}:0' if within else None


def scoped_find(location: Any, question: Mapping[str, Any]) -> bool:
    parsed = parse_find_location(location)
    if parsed is None:
        return False
    normalize = lambda value: re.sub(r"[\W_]+", "", str(value).casefold())
    term = normalize(parsed[1])
    return bool(term) and term in normalize(" ".join(str(question.get(key, "")) for key in
        ("question", "supportCondition", "refuteCondition", "missingEvidence")))


def direct_location(location: Any) -> bool:
    return isinstance(location, str) and re.fullmatch(
        r"(?:(?:paragraph|table|line):\d+|sentence:\d+:\d+|sentences:\d+:\d+:\d+|within:paragraph:\d+:\d+)", location) is not None


def resize_catalogue(value: Mapping[str, Any], count: int) -> dict[str, Any]:
    """Shrink a transport page and preserve an exact cursor to every omitted entry."""
    locators = value['locators'][:max(1, count)]
    following = value['offset'] + len(locators)
    query, parent = value.get('query'), value.get('catalogueParent')
    more = following < value['matchingLocatorCount']
    next_location = (f"within:{parent}:{following}" if parent else
        f"find:{following}:{quote(query, safe='')}" if query else f"outline:{following}") if more else None
    return {**value, 'locators': locators, 'visibleLocatorCount': len(locators),
        'nextLocation': next_location, 'locatorsTruncated': more}


def _preview(text: str) -> str | None:
    """An intact heading/sentence preview, never a cut factual assertion."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    if len(first) <= _PREVIEW_CHARACTERS:
        return first
    match = re.search(r"[。！？!?](?:[”’\"']|$)?", first)
    return first[:match.end()] if match and match.end() <= _PREVIEW_CHARACTERS else None


def _budget(max_characters: int | None) -> int:
    value = MAX_FRAGMENT_CHARACTERS if max_characters is None else max_characters
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Material reads require a positive explicit request budget")
    return value


def _support_parts(text, block, blocks, *, position=None, footnotes=None, reference_index=None):
    if reference_index is None:
        reference_index = _ReferenceIndex(text)
    if block.parent_locator:
        if block.parent_locator not in reference_index.parent_support:
            shared = [(a, b, 'heading') for a, b in block.headings]
            shared.extend((a, b, 'qualifier') for a, b in block.parent_qualifiers)
            shared.extend(_note_closure(text, [(a, b) for a, b, _ in shared], blocks, footnotes, reference_index))
            reference_index.parent_support[block.parent_locator] = _SupportRanges(shared)
        common = reference_index.parent_support[block.parent_locator]
        result = []
    else:
        common = _SupportRanges(())
        result = [(a, b, 'heading') for a, b in block.headings]
    if position is None:
        position = blocks.index(block)
    if block.parent_locator:
        # A sentence is not a free-standing summary. Keep its adjacent context
        # (including antecedents) and the existing qualifier/footnote closure.
        for index in (position - 1, position + 1):
            if 0 <= index < len(blocks) and blocks[index].parent_locator == block.parent_locator:
                other = blocks[index]
                result.append((other.start, other.end, "sentence_context"))
    # A direct caveat in the following paragraph qualifies the selected facts.
    for following_position in range(position+1, len(blocks)):
        if block.parent_locator:
            # The original parent and following-sentence closure was computed
            # above; scanning every remaining sibling would be quadratic.
            break
        following = blocks[following_position]
        if following.kind == "heading" or following.headings != block.headings:
            break
        if not _qualifier(text[following.start:following.end]):
            break
        result.append((following.start, following.end, "qualifier"))
    result.extend(_note_closure(text, [(block.start, block.end), *((a, b) for a, b, _ in result)],
                                blocks, footnotes, reference_index))
    return common, result


def _note_closure(text, pending, blocks, footnotes, reference_index):
    result = []
    scanned, resolved = set(), set()
    while pending:
        start, end = pending.pop()
        if (start, end) in scanned:
            continue
        scanned.add((start, end))
        for reference in reference_index.within(start, end):
            if reference in resolved:
                continue
            resolved.add(reference)
            if footnotes is None:
                footnotes = _footnotes(text, blocks)
            for other in footnotes.get(reference, []):
                result.append((other.start, other.end, "footnote"))
                pending.append((other.start, other.end))
    return result


def _support(text: str, block: _Block, blocks: list[_Block], *, position: int | None = None,
             footnotes: Mapping[str, list[_Block]] | None = None,
             reference_index: _ReferenceIndex | None = None) -> list[tuple[int, int, str]]:
    common, extra = _support_parts(text, block, blocks, position=position, footnotes=footnotes,
                                   reference_index=reference_index)
    # The enclosing qualifier range may already contain an adjacent sentence
    # or the selected sentence itself. Count and send each original character
    # once, without dropping any of the complete qualifying context.
    ranges = []
    for start, end, kind in (*common.ranges, *extra):
        if end <= block.start or start >= block.end:
            ranges.append((start, end, kind))
        else:
            if start < block.start:
                ranges.append((start, block.start, kind))
            if end > block.end:
                ranges.append((block.end, end, kind))
    return _merge_support_ranges(ranges)


def _footnotes(text: str, blocks: list[_Block]) -> dict[str, list[_Block]]:
    result: dict[str, list[_Block]] = {}
    # A definition can itself be refined into sentences. Its first marker
    # still refers to the complete original paragraph, never only child one.
    parents: dict[str, _Block] = {}
    for block in blocks:
        key = block.parent_locator or block.locator
        first = parents.get(key)
        parents[key] = replace(first, end=block.end, line_end=block.line_end) if first else block
    for block in parents.values():
        match = re.match(r"\s*(\[\d+\]|[①②③④⑤⑥⑦⑧⑨⑩])", text[block.start:block.end])
        if match:
            result.setdefault(match[1], []).append(block)
    return result


def _identity(document: Any, text: str) -> dict[str, Any]:
    return {"sourceRef":{"documentId":getattr(document,"document_id"),"revision":getattr(document,"revision")},
            "sourceContentSha256":sha256(text.encode("utf-8")).hexdigest(),"indexVersion":INDEX_VERSION,
            "offsetUnit":"python_unicode_codepoint","publishedAt":getattr(document,"published_at",None),
            "fetchedAt":getattr(document,"fetched_at",None),"contentVersionAtCutoff":_metadata(document).get("contentVersionAtCutoff")}


def _entry(text: str, block: _Block, blocks: list[_Block], budget: int, *, position: int | None = None,
           footnotes: Mapping[str, list[_Block]] | None = None,
           reference_index: _ReferenceIndex | None = None) -> dict[str, Any]:
    common, extra = _support_parts(text,block,blocks,position=position,footnotes=footnotes,reference_index=reference_index)
    # Count the union exactly without copying a parent's shared qualifications
    # into every catalogue entry. Materialize support only for an actual read.
    unique = _merge_support_ranges([(block.start, block.end, 'sentence_context'), *extra])
    total = common.prefix[-1] + sum(b-a-common.overlap(a,b) for a,b,_ in unique)
    value = {"locator":block.locator,"kind":block.kind,"startOffset":block.start,"endOffset":block.end,
             "lineStart":block.line_start,"lineEnd":block.line_end,"characters":block.end-block.start,
             "evidenceUnitCharacters":total,"readable":total<=min(budget, MAX_FRAGMENT_CHARACTERS),
             "headingPath":[text[a:b] for a,b in block.headings]}
    preview = _preview(text[block.start:block.end])
    if preview is not None:
        value["preview"] = preview
    if block.parent_locator:
        value["parentLocator"] = block.parent_locator
    return value


def document_outline(document: Any, *, offset: int = 0, query: str | None = None,
                     max_characters: int | None = None, parent: str | None = None) -> dict[str, Any]:
    text = _source_text(document)
    budget = _budget(max_characters) if max_characters is not None else max(1, len(text)*2)
    admission = admit_material(document)
    if admission.state == "excluded":
        return {**_identity(document,text),"status":"excluded","reason":admission.reason,"locatorCount":0,"locators":[]}
    if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:
        raise ValueError("Invalid local catalogue offset")
    blocks = _blocks(text)
    terms = [x.casefold() for x in (query or "").split()]
    matches = [b for b in blocks if (parent is None or b.parent_locator == parent) and
        (not terms or all(term in (text[b.start:b.end]+" "+" ".join(text[a:z] for a,z in b.headings)).casefold() for term in terms))]
    selected = matches[offset:offset+MAX_OUTLINE_LOCATORS]
    following = offset+len(selected)
    next_location = ((f"within:{parent}:{following}" if parent else f"find:{following}:{quote(query,safe='')}" if query else f"outline:{following}")
                     if following<len(matches) else None)
    title = _title(document)
    footnotes = _footnotes(text, blocks)
    reference_index = _ReferenceIndex(text)
    entries = {block.locator: _entry(text, block, blocks, budget, position=position, footnotes=footnotes,
                                   reference_index=reference_index)
               for position, block in enumerate(blocks)}
    if max_characters is None:
        for entry in entries.values():
            if entry["evidenceUnitCharacters"] <= MAX_FRAGMENT_CHARACTERS:
                entry["readable"] = None
            entry["requiresRequestPreflight"] = True
    return {**_identity(document,text),"documentId":getattr(document,"document_id"),"revision":getattr(document,"revision"),
            "title":title if len(title)<=512 else None,"titleSha256":sha256(title.encode()).hexdigest(),
            "locatorCount":len(blocks),"matchingLocatorCount":len(matches),"visibleLocatorCount":len(selected),
            "locatorsTruncated":next_location is not None,"nextLocation":next_location,"offset":offset,"query":query,
            "catalogueParent": parent,
            "unreadableLocatorCount":sum(entry["readable"] is False for entry in entries.values()),
            "requestBudgetChecked":max_characters is not None,
            "readInstructions":"Read an exact paragraph/table/sentence locator. Oversized paragraphs have sentence children. Search all original blocks with find:<space-separated exact terms>; follow nextLocation for remaining catalogue entries. Previews guide selection and are not complete evidence; read the locator with its qualifiers before citing.",
            "locators":[entries[b.locator] for b in selected]}


def read_locator(document: Any, location: str | None, *, max_characters: int | None = None) -> dict[str, Any] | None:
    text = _source_text(document)
    identity = _identity(document,text)
    admission = admit_material(document)
    if admission.state == "excluded":
        return {**identity,"status":"excluded","reason":admission.reason,"needsLocator":False}
    budget = _budget(max_characters)
    if location == "outline":
        return document_outline(document,max_characters=budget)
    if isinstance(location,str) and re.fullmatch(r"outline:\d+",location):
        return document_outline(document,offset=int(location.split(':')[1]),max_characters=budget)
    if isinstance(location,str) and location.startswith('find:'):
        parsed = parse_find_location(location)
        return document_outline(document,offset=parsed[0],query=parsed[1],max_characters=budget) if parsed else None
    within = re.fullmatch(r"within:(paragraph:\d+):(\d+)", location or "")
    if within:
        return document_outline(document, parent=within[1], offset=int(within[2]), max_characters=budget)
    blocks = _blocks(text)
    span = re.fullmatch(r"sentences:(\d+):(\d+):(\d+)", location or "")
    if span:
        parent, start, end = f"paragraph:{span[1]}", int(span[2]), int(span[3])
        covered = [(i, b) for i, b in enumerate(blocks)
                   if b.parent_locator == parent and b.start >= start and b.end <= end]
        # Only original sentence boundaries are addressable. A caller cannot
        # turn a character slice or a cross-paragraph gap into a source unit.
        if not covered or covered[0][1].start != start or covered[-1][1].end != end:
            return None
        first, last = covered[0][1], covered[-1][1]
        # All children share the complete immutable parent qualifications.
        # Expanding that tuple once per covered sentence is quadratic for a
        # long excerpt with sparse caveats; retain the first child's tuple.
        combined = replace(first, locator=location, kind='sentence_span', end=last.end)
        blocks = [*blocks[:covered[0][0]], combined, *blocks[covered[-1][0]+1:]]
    selected = next((b for b in blocks if b.locator == location),None)
    children = [b for b in blocks if b.parent_locator == location]
    if selected is None and isinstance(location,str) and re.fullmatch(r"line:\d+",location):
        line = int(location.split(':')[1])
        matching = [b for b in blocks if b.line_start<=line<=b.line_end]
        children = [b for b in matching if b.parent_locator]
        selected = None if children else next(iter(matching), None)
    if children:
        return {**document_outline(document, parent=children[0].parent_locator, max_characters=budget),
                "needsLocator": True, "status": "requires_refined_locator", "requestedLocator": location,
                "locatorHint": children[0].locator}
    if selected is None:
        return None
    reference_index = _ReferenceIndex(text)
    details = _entry(text,selected,blocks,budget,reference_index=reference_index)
    if not details['readable']:
        return {**identity,**details,"needsLocator":True,"status":"not_safely_readable","needsStructuredSource":True,
                "requiredCharacters":details['evidenceUnitCharacters'],"availableCharacters":min(budget, MAX_FRAGMENT_CHARACTERS),
                "locatorHint":f"line:{selected.line_start}","unreadableLocatorCount":1,
                "reason":"The complete structural unit and its qualifiers exceed this request budget; no text has been sliced."}
    context = [{"kind":kind,"startOffset":a,"endOffset":b,"text":text[a:b],"sha256":sha256(text[a:b].encode()).hexdigest()}
               for a,b,kind in _support(text,selected,blocks,reference_index=reference_index)]
    return {**identity,**details,"needsLocator":False,"text":text[selected.start:selected.end],
            "textSha256":sha256(text[selected.start:selected.end].encode()).hexdigest(),"supportingContext":context}


def bounded_excerpt(document: Any, *, max_characters: int | None = None) -> dict[str, Any]:
    text = _source_text(document)
    identity = _identity(document,text)
    admission = admit_material(document)
    if admission.state=='excluded':
        return {**identity,"status":"excluded","reason":admission.reason,"needsLocator":False}
    excerpt=getattr(document,'excerpt',None)
    if not isinstance(excerpt,str) or not excerpt.strip():
        return {**identity,"needsLocator":True,"locatorHint":"outline"}
    budget=_budget(max_characters)
    digest=sha256(excerpt.encode()).hexdigest()
    if len(excerpt)>min(budget, MAX_FRAGMENT_CHARACTERS):
        return {**identity,"status":"not_safely_readable","needsLocator":True,"needsStructuredSource":True,
                "locatorHint":"outline","characters":len(excerpt),"excerptSha256":digest}
    # A stored excerpt is explicitly an excerpt, not proof that omitted source
    # paragraphs contain no qualification. Where it occurs in the source, return
    # the whole enclosing evidence unit rather than a possibly misleading slice.
    start=text.find(excerpt)
    blocks=_blocks(text)
    if start>=0:
        block=next((b for b in blocks if b.start<=start and b.end>=start+len(excerpt)),None)
        if block:
            return {**read_locator(document,block.locator,max_characters=budget),"requestedMaterial":"stored_excerpt"}
        covered = [b for b in blocks if b.end > start and b.start < start+len(excerpt)]
        if covered and covered[0].parent_locator and all(b.parent_locator == covered[0].parent_locator for b in covered):
            location = f"sentences:{covered[0].line_start}:{covered[0].start}:{covered[-1].end}"
            return {**read_locator(document, location, max_characters=budget), "requestedMaterial": "stored_excerpt"}
        # An exact excerpt across different structural units is not a new
        # unqualified source. Supply their addressable catalogue for refinement.
        return {**document_outline(document, max_characters=budget), 'needsLocator': True,
                'status': 'requires_refined_locator', 'requestedMaterial': 'stored_excerpt',
                'locatorHint': covered[0].locator if covered else 'outline'}
    return {**identity,"text":excerpt,"characters":len(excerpt),"excerptSha256":digest,
            "materialKind":"stored_excerpt","notFullSource":True,"needsQualificationCheck":True,
            "qualificationReadLocation":"outline"}


def source_material_for_understand(document: Any, *, max_characters: int) -> dict[str, Any]:
    """Select no arbitrary passages: exact admitted body or a searchable source index."""
    if isinstance(max_characters, bool) or not isinstance(max_characters, int) or max_characters < 0:
        raise ValueError("An explicit preflight budget must be nonnegative")
    budget = max_characters
    text=_source_text(document)
    admission=admit_material(document)
    base={**_identity(document,text),"materialAdmission":admission.state,"reason":admission.reason}
    if admission.state=='excluded':
        return {**base,"textMode":"excluded","text":"","isExcerpt":False,"needsLocator":False}
    if admission.requires_current_event_locator:
        return {**base,"textMode":"background_requires_event_question","text":"","isExcerpt":False,
                "needsLocator":True,"requiresCurrentEventQuestion":True}
    if len(text)<=min(budget, MAX_FRAGMENT_CHARACTERS):
        return {**base,"textMode":"full_text","text":text,"isExcerpt":False,"needsLocator":False,
                "contentRanges":[{"startOffset":0,"endOffset":len(text),"sha256":sha256(text.encode()).hexdigest()}]}
    return {**base,"textMode":"structural_outline","text":"","isExcerpt":True,"needsLocator":True,
            "sourceIndex":document_outline(document,max_characters=budget if budget else None)}


__all__=["INDEX_VERSION","MAX_FRAGMENT_CHARACTERS","MAX_OUTLINE_LOCATORS","MaterialAdmission","admit_material",
         "bounded_excerpt","document_outline","read_locator","source_material_for_understand","requires_current_event_locator",
         "parse_find_location", "scoped_find", "direct_location"]
