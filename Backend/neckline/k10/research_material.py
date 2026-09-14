"""Local source admission and traceable structural reads; never sample source prose.

Offsets always address the unchanged Python source string (not a normalized copy).
Pagination limits catalogue transport only: every block remains addressable and
searchable. A caller's explicit request budget applies to a complete evidence unit,
including its headings, units, notes and adjacent qualifying statements.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any, Mapping
from urllib.parse import quote, unquote

INDEX_VERSION = "k10-source-index-3.3.0"
# Legacy offline read compatibility only. Production reads pass the complete
# selected unit through actual request preflight; this is not a strategy quota.
MAX_FRAGMENT_CHARACTERS = 12_000
MAX_OUTLINE_LOCATORS = 128
_PREVIEW_CHARACTERS = 240


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


def _caption(value: str) -> bool:
    return bool(re.match(r"^\s*(?:表\s*[\d一二三四五六七八九十]|table\s*\d|[（(]?单位\s*[:：]|[（(]?报告期\s*[:：]|[（(]?统计期间\s*[:：])", value, re.I))


def _blocks(text: str) -> list[_Block]:
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


def _support(text: str, block: _Block, blocks: list[_Block], *, position: int | None = None,
             footnotes: Mapping[str, list[_Block]] | None = None) -> list[tuple[int, int, str]]:
    result = [(a,b,"heading") for a,b in block.headings]
    if position is None:
        position = blocks.index(block)
    # A direct caveat in the following paragraph qualifies the selected facts.
    for following_position in range(position+1, len(blocks)):
        following = blocks[following_position]
        if following.kind == "heading" or following.headings != block.headings:
            break
        if not _qualifier(text[following.start:following.end]):
            break
        result.append((following.start, following.end, "qualifier"))
    # Resolve explicit numbered footnote references without dropping a distant note.
    references = set(re.findall(r"\[(\d+)\]|([①②③④⑤⑥⑦⑧⑨⑩])", text[block.start:block.end]))
    if references:
        if footnotes is None:
            footnotes = _footnotes(text, blocks)
        for number, circled in references:
            for other in footnotes.get(f"[{number}]" if number else circled, []):
                if other is not block:
                    result.append((other.start, other.end, "footnote"))
    return list(dict.fromkeys(result))


def _footnotes(text: str, blocks: list[_Block]) -> dict[str, list[_Block]]:
    result: dict[str, list[_Block]] = {}
    for block in blocks:
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
           footnotes: Mapping[str, list[_Block]] | None = None) -> dict[str, Any]:
    support = _support(text,block,blocks,position=position,footnotes=footnotes)
    total = block.end-block.start + sum(b-a for a,b,_ in support)
    value = {"locator":block.locator,"kind":block.kind,"startOffset":block.start,"endOffset":block.end,
             "lineStart":block.line_start,"lineEnd":block.line_end,"characters":block.end-block.start,
             "evidenceUnitCharacters":total,"readable":total<=budget,
             "headingPath":[text[a:b] for a,b in block.headings]}
    preview = _preview(text[block.start:block.end])
    if preview is not None:
        value["preview"] = preview
    return value


def document_outline(document: Any, *, offset: int = 0, query: str | None = None,
                     max_characters: int | None = None) -> dict[str, Any]:
    text = _source_text(document)
    budget = _budget(max_characters) if max_characters is not None else max(1, len(text)*2)
    admission = admit_material(document)
    if admission.state == "excluded":
        return {**_identity(document,text),"status":"excluded","reason":admission.reason,"locatorCount":0,"locators":[]}
    if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:
        raise ValueError("Invalid local catalogue offset")
    blocks = _blocks(text)
    terms = [x.casefold() for x in (query or "").split()]
    matches = [b for b in blocks if not terms or all(term in (text[b.start:b.end]+" "+" ".join(text[a:z] for a,z in b.headings)).casefold() for term in terms)]
    selected = matches[offset:offset+MAX_OUTLINE_LOCATORS]
    following = offset+len(selected)
    next_location = ((f"find:{following}:{quote(query,safe='')}" if query else f"outline:{following}")
                     if following<len(matches) else None)
    title = _title(document)
    footnotes = _footnotes(text, blocks)
    entries = {block.locator: _entry(text, block, blocks, budget, position=position, footnotes=footnotes)
               for position, block in enumerate(blocks)}
    if max_characters is None:
        for entry in entries.values():
            entry["readable"] = None
            entry["requiresRequestPreflight"] = True
    return {**_identity(document,text),"documentId":getattr(document,"document_id"),"revision":getattr(document,"revision"),
            "title":title if len(title)<=512 else None,"titleSha256":sha256(title.encode()).hexdigest(),
            "locatorCount":len(blocks),"matchingLocatorCount":len(matches),"visibleLocatorCount":len(selected),
            "locatorsTruncated":next_location is not None,"nextLocation":next_location,"offset":offset,"query":query,
            "unreadableLocatorCount":sum(entry["readable"] is False for entry in entries.values()),
            "requestBudgetChecked":max_characters is not None,
            "readInstructions":"Read an exact locator. Search all original blocks with find:<space-separated exact terms>; follow nextLocation for remaining catalogue entries. Previews guide selection and are not complete evidence; read the locator with its qualifiers before citing.",
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
        match = re.fullmatch(r"find:(?:(\d+):)?(.+)",location)
        return document_outline(document,offset=int(match[1] or 0),query=unquote(match[2]),max_characters=budget) if match else None
    blocks = _blocks(text)
    selected = next((b for b in blocks if b.locator == location),None)
    if selected is None and isinstance(location,str) and re.fullmatch(r"line:\d+",location):
        line = int(location.split(':')[1])
        selected = next((b for b in blocks if b.line_start<=line<=b.line_end),None)
    if selected is None:
        return None
    details = _entry(text,selected,blocks,budget)
    if not details['readable']:
        return {**identity,**details,"needsLocator":True,"status":"not_safely_readable","needsStructuredSource":True,
                "requiredCharacters":details['evidenceUnitCharacters'],"availableCharacters":budget,
                "locatorHint":f"line:{selected.line_start}","unreadableLocatorCount":1,
                "reason":"The complete structural unit and its qualifiers exceed this request budget; no text has been sliced."}
    context = [{"kind":kind,"startOffset":a,"endOffset":b,"text":text[a:b],"sha256":sha256(text[a:b].encode()).hexdigest()}
               for a,b,kind in _support(text,selected,blocks)]
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
    if len(excerpt)>budget:
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
    if len(text)<=budget:
        return {**base,"textMode":"full_text","text":text,"isExcerpt":False,"needsLocator":False,
                "contentRanges":[{"startOffset":0,"endOffset":len(text),"sha256":sha256(text.encode()).hexdigest()}]}
    return {**base,"textMode":"structural_outline","text":"","isExcerpt":True,"needsLocator":True,
            "sourceIndex":document_outline(document,max_characters=budget if budget else None)}


__all__=["INDEX_VERSION","MAX_FRAGMENT_CHARACTERS","MAX_OUTLINE_LOCATORS","MaterialAdmission","admit_material",
         "bounded_excerpt","document_outline","read_locator","source_material_for_understand","requires_current_event_locator"]
