"""从公告文本读主要财务数字。读不到的字段留空并写入 missing，不估算、不拿相近行充数。

定期报告按列结构取数：三季报是本报告期、本报告期同比、年初至报告期末、年初至报告期末同比；
一季报和半年报是本报告期、上年同期、同比（若有追溯调整，上年同期取「调整后」）；
年报是本年、上年、同比。金额按表头或标签里的「元」「万元」换成元。
业绩预告读归母与扣非的本期区间、上年同期和同比区间；业绩快报读营业总收入、归母、扣非的本期、上年同期和增减幅度。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

_NOT_BODY = ("提示性公告", "更正", "英文版", "审计报告", "内部控制", "问询", "说明会", "延期", "异议")
_PERIOD_KEYS = (
    ("第三季度报告", "q3"),
    ("三季度报告", "q3"),
    ("第一季度报告", "q1"),
    ("一季度报告", "q1"),
    ("半年度报告", "h1"),
    ("年度报告", "annual"),
)
_PERIOD_LABEL = {"q1": "第一季度", "h1": "半年度", "q3": "第三季度", "annual": "年度"}

_DEDUCTED_LABELS = (
    "归属于上市公司股东的扣除非经常性损益后的净利润",
    "归属于上市公司股东的扣除非经常性损益的净利润",
    "扣除非经常性损益后的归属于上市公司股东的净利润",
    "扣除非经常性损益后的净利润",
)
_NET_LABEL = "归属于上市公司股东的净利润"
_REVENUE_LABELS = ("营业收入（万元）", "营业收入（元）", "营业收入")
_EXPRESS_REVENUE = ("营业总收入",)

_ROW_STOPS = (
    "营业收入（万元）",
    "营业收入（元）",
    "营业总收入",
    "营业利润",
    "利润总额",
    "归属于上市公司股东的扣除非经常性损益后的净利润",
    "归属于上市公司股东的扣除非经常性损益的净利润",
    "扣除非经常性损益后的归属于上市公司股东的净利润",
    "扣除非经常性损益后的净利润",
    "归属于上市公司股东的净利润",
    "经营活动产生的现金流量净额",
    "基本每股收益",
    "稀释每股收益",
    "加权平均净资产收益率",
    "总资产（元）",
    "本报告期末",
    "营业收入",
)

_LAYOUT = {
    "q3": ("num", "pct", "num", "pct"),
    "simple": ("num", "num", "pct"),
    "restated": ("num", "num", "num", "pct"),
    "annual": ("num", "num", "pct", "num"),
}
_UP = frozenset({"增长", "上升", "增加", "减亏"})
_DOWN = frozenset({"下降", "减少", "增亏"})
_NUM = r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?"
_RANGE_RE = re.compile(
    rf"(?:(盈利|亏损|扭亏为盈)\s*[:：]\s*)?({_NUM})\s*(万元|元)?\s*"
    rf"(?:~|～|–|—|－|至|-)\s*({_NUM})\s*(万元|元)?(?!\s*[%％年])"
)
# 百分数区间（含左右共用一个百分号的「100～200%」「100至200%」）整段算一个，再去掉单个百分数
# 连接符只认「～」「至」「–」「—」，减号只在紧贴两个数字时算连接（「100-200%」）；
# 「2,000 -50%」是上年同期金额加负同比，不能当成区间吞掉金额。
_PCT_TOKEN_RE = re.compile(
    r"[-+－]?\d[\d,，.]*(?:\s*[%％])?(?:\s*[~～至–—]\s*|-(?=\d))[-+－]?\d[\d,，.]*\s*[%％]"
    r"|[-+－]?\d[\d,，.]*\s*[%％]")
_ONE_RE = re.compile(rf"(?:(盈利|亏损|扭亏为盈)\s*[:：]\s*)?({_NUM})\s*(万元|元)?(?!\s*[%％年])")
_YOY_RANGE_RE = re.compile(
    r"(?:比上年同期|较上年同期|同比)\s*(增长|下降|上升|减少|增加|减亏|增亏)\s*[:：]?\s*"
    rf"({_NUM})\s*[%％]?\s*(?:~|～|–|—|－|至|-)\s*({_NUM})\s*[%％]?"
)
_YOY_ONE_RE = re.compile(
    r"(?:比上年同期|较上年同期|同比)\s*(增长|下降|上升|减少|增加|减亏|增亏)\s*[:：]?\s*"
    rf"({_NUM})\s*[%％]"
)
_DATE_RE = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*[至到]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
# 扭亏或基数为负时同比写「不适用」，和「--」一样按空格位读，不然整行对不上列
_TOKEN_RE = re.compile(rf"--+|—|－|不适用|(?<![\d.])-(?![\d.,])|(?P<num>{_NUM})(?P<pct>[%％])?")
_UNIT_RE = re.compile(r"单位\s*[:：]\s*(?:人民币)?\s*(万元|元)")
_CENT = Decimal("0.01")


@dataclass
class PeriodicRow:
    """一行主要会计数据。金额是元；同比是百分数，57.39 表示 +57.39%。"""
    current: float | None = None
    yoy: float | None = None
    prior: float | None = None
    ytd: float | None = None
    ytd_yoy: float | None = None


@dataclass
class PeriodicFigures:
    period: str | None = None
    revenue: PeriodicRow = field(default_factory=PeriodicRow)
    net_profit: PeriodicRow = field(default_factory=PeriodicRow)
    deducted: PeriodicRow = field(default_factory=PeriodicRow)
    non_recurring_current: float | None = None
    non_recurring_ytd: float | None = None
    missing: list[str] = field(default_factory=list)


@dataclass
class AmountRange:
    low: float | None = None
    high: float | None = None


@dataclass
class ForecastLine:
    """state 为盈利、亏损或扭亏。区间金额是元，同比是百分数。"""
    state: str | None = None
    current: AmountRange = field(default_factory=AmountRange)
    prior: float | None = None
    yoy: AmountRange = field(default_factory=AmountRange)


@dataclass
class ForecastFigures:
    period: str | None = None
    net_profit: ForecastLine = field(default_factory=ForecastLine)
    deducted: ForecastLine = field(default_factory=ForecastLine)
    missing: list[str] = field(default_factory=list)


@dataclass
class ExpressLine:
    current: float | None = None
    prior: float | None = None
    yoy: float | None = None


@dataclass
class ExpressFigures:
    period: str | None = None
    revenue: ExpressLine = field(default_factory=ExpressLine)
    net_profit: ExpressLine = field(default_factory=ExpressLine)
    deducted: ExpressLine = field(default_factory=ExpressLine)
    missing: list[str] = field(default_factory=list)


def filing_kind(title: str) -> str | None:
    """按公告标题区分定期报告、业绩预告、业绩快报。摘要算定期报告；更正、提示性公告等不是正文。"""
    text = re.sub(r"\s+", "", title or "")
    if not text or any(word in text for word in _NOT_BODY):
        return None
    if "业绩预告" in text:
        return "forecast"
    if "业绩快报" in text:
        return "express"
    if any(key in text for key, _ in _PERIOD_KEYS):
        return "periodic"
    return None


def parse_periodic(text: str) -> PeriodicFigures:
    figures = PeriodicFigures(period=_detect_period(text))
    if figures.period is None:
        figures.missing = ["报告类型"]
        return figures
    body = _prepare(text)
    section = _between(body, "主要会计数据和财务指标", (
        "非经常性损益项目", "分季度主要财务指标", "境内外会计准则", "公司最近三个会计年度",
    ))
    layout = _table_layout(figures.period, section)
    _fill_periodic_row(figures.revenue, section, _REVENUE_LABELS, layout)
    deducted_at = _find_label(section, _DEDUCTED_LABELS)
    _fill_periodic_row(figures.net_profit, section, (_NET_LABEL,), layout, skip_inside=deducted_at)
    _fill_periodic_row(figures.deducted, section, _DEDUCTED_LABELS, layout)
    figures.non_recurring_current, figures.non_recurring_ytd = _non_recurring(body, figures.period)
    figures.missing = _periodic_missing(figures, layout)
    return figures


def parse_forecast(text: str) -> ForecastFigures:
    figures = ForecastFigures()
    section = _forecast_section(_prepare(text))
    if not section:
        figures.missing = _forecast_missing(figures)
        return figures
    found = _DATE_RE.search(section)
    if found:
        y1, m1, d1, y2, m2, d2 = (int(g) for g in found.groups())
        figures.period = f"{y1}年{m1}月{d1}日至{y2}年{m2}月{d2}日"
    state = _forecast_state(section)
    unit = _section_unit(section)
    net_at, ded_at = _find_net(section), _find_label(section, _DEDUCTED_LABELS)
    if net_at is not None:
        figures.net_profit = _forecast_line(_span(section, net_at, ded_at), unit, state)
    if ded_at is not None:
        figures.deducted = _forecast_line(_span(section, ded_at, None), unit, state)
    figures.missing = _forecast_missing(figures)
    return figures


def parse_express(text: str) -> ExpressFigures:
    figures = ExpressFigures(period=_express_period(text))
    section = _between(_prepare(text), "主要财务数据和指标", ("本报告期末", "经营业绩和财务状况"))
    if not section:
        figures.missing = _express_missing(figures)
        return figures
    unit = _section_unit(section)
    deducted_at = _find_label(section, _DEDUCTED_LABELS)
    _fill_express_line(figures.revenue, section, _EXPRESS_REVENUE, unit)
    _fill_express_line(figures.net_profit, section, (_NET_LABEL,), unit, skip_inside=deducted_at)
    _fill_express_line(figures.deducted, section, _DEDUCTED_LABELS, unit)
    figures.missing = _express_missing(figures)
    return figures


def summary_text(figures: PeriodicFigures | ForecastFigures | ExpressFigures) -> str:
    """一段中文摘要，只写读到的数字，缺的列在末尾如实列出。"""
    if isinstance(figures, PeriodicFigures):
        parts = _periodic_summary(figures)
    elif isinstance(figures, ForecastFigures):
        parts = _forecast_summary(figures)
    else:
        parts = _express_summary(figures)
    body = "；".join(part for part in parts if part)
    text = f"【公告 PDF 读数】{body}。" if body else "【公告 PDF 读数】"
    if figures.missing:
        text += "未能读出：" + "、".join(figures.missing) + "。"
    return text


def _prepare(text: str) -> str:
    return re.sub(r"(?m)^\s*\d{1,3}\s*$", "", text or "")


def _compact(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        if ch == "(":
            ch = "（"
        elif ch == ")":
            ch = "）"
        chars.append(ch)
        index.append(i)
    return "".join(chars), index


def _detect_period(text: str) -> str | None:
    head = re.sub(r"\s+", "", text[:8000])
    best: tuple[int, str, int] | None = None
    for key, kind in _PERIOD_KEYS:
        at = head.find(key)
        if at < 0:
            continue
        if best is None or at < best[0] or (at == best[0] and len(key) > best[2]):
            best = (at, kind, len(key))
    return None if best is None else best[1]


def _between(text: str, start_key: str, end_keys: tuple[str, ...]) -> str:
    compact, index = _compact(text)
    at = compact.find(start_key)
    if at < 0:
        return ""
    rest_compact = compact[at:]
    rest_index = index[at:]
    ends = [len(text)]
    for key in end_keys:
        found = rest_compact.find(key, len(start_key))
        if found >= 0:
            ends.append(rest_index[found])
    return text[index[at]:min(ends)]


def _section_unit(text: str) -> int | None:
    found = _UNIT_RE.search(text)
    if not found:
        return None
    return 10_000 if found.group(1) == "万元" else 1


def _label_unit(label: str, section: str) -> int | None:
    if "万元" in label:
        return 10_000
    if "元" in label:
        return 1
    return _section_unit(section)


def _find_label(text: str, labels: tuple[str, ...]) -> tuple[int, int] | None:
    compact, index = _compact(text)
    best: tuple[int, int] | None = None
    for label in labels:
        at = compact.find(label)
        if at < 0:
            continue
        span = (at, at + len(label))
        if best is None or span[0] < best[0] or (span[0] == best[0] and span[1] > best[1]):
            best = span
    if best is None:
        return None
    return index[best[0]], index[best[1] - 1] + 1


def _find_net(text: str, skip_inside: tuple[int, int] | None = None) -> tuple[int, int] | None:
    compact, index = _compact(text)
    start = 0
    while True:
        at = compact.find(_NET_LABEL, start)
        if at < 0:
            return None
        origin = index[at]
        prefix = compact[max(0, at - 16):at]
        covered = skip_inside is not None and skip_inside[0] <= origin < skip_inside[1]
        if covered or prefix.endswith(("后的", "损益的", "的扣除")):
            start = at + 1
            continue
        return origin, index[at + len(_NET_LABEL) - 1] + 1


def _window(text: str, label: tuple[int, int]) -> str:
    compact, index = _compact(text)
    label_end = None
    for pos, origin in enumerate(index):
        if origin >= label[1]:
            label_end = pos
            break
    if label_end is None:
        label_end = len(compact)
    stops = []
    for key in _ROW_STOPS:
        at = compact.find(key, label_end)
        if at >= 0:
            stops.append(index[at])
    end = min(stops) if stops else len(text)
    return text[label[1]:end]


def _tokenize(text: str) -> list[tuple[str, str]]:
    found = []
    for match in _TOKEN_RE.finditer(text):
        if match.group("num") is None:
            found.append(("missing", ""))
        elif match.group("pct"):
            found.append(("pct", match.group("num")))
        else:
            found.append(("num", match.group("num")))
    return found


def _as_decimal(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "").replace("，", ""))


def _yuan(raw: str, scale: int) -> float:
    return float((_as_decimal(raw) * scale).quantize(_CENT, rounding=ROUND_HALF_UP))


def _pct(raw: str) -> float:
    return float(_as_decimal(raw).quantize(_CENT, rounding=ROUND_HALF_UP))


def _consume_unit(text: str, end: int) -> tuple[int, int | None]:
    """标签和「（元）」「（万元）」可能被拆成两行，单位紧跟在标签后面。"""
    found = re.match(r"\s*[（(]\s*(万元|元)\s*[)）]", text[end:])
    if not found:
        return end, None
    return end + found.end(), 10_000 if found.group(1) == "万元" else 1


def _table_layout(period: str, section: str) -> str:
    if period == "q3":
        return "q3"
    if period != "annual":
        revenue = _find_label(section, _REVENUE_LABELS)
        head = section[:revenue[0]] if revenue else section
        if "调整前" in head and "调整后" in head:
            return "restated"
    if period == "annual":
        return "annual"
    return "simple"


def _fill_periodic_row(row: PeriodicRow, section: str, labels: tuple[str, ...], layout: str,
                       skip_inside: tuple[int, int] | None = None) -> None:
    if labels == (_NET_LABEL,):
        located = _find_net(section, skip_inside)
        label_text = _NET_LABEL
    else:
        located = _find_label(section, labels)
        label_text = "" if located is None else _compact(section[located[0]:located[1]])[0]
    if located is None:
        return
    end, inline_unit = _consume_unit(section, located[1])
    located = (located[0], end)
    scale = inline_unit if inline_unit is not None else _label_unit(label_text or labels[0], section)
    tokens = _tokenize(_window(section, located))
    kinds = _LAYOUT[layout]
    # 列数必须对上；「--」「不适用」这类空格位只能出现在同比列，金额列必须是数字
    if scale is None or len(tokens) != len(kinds):
        return
    for (kind, _raw), expected in zip(tokens, kinds):
        if kind != expected and not (kind == "missing" and expected == "pct"):
            return
    values = [raw if kind != "missing" else None for kind, raw in tokens]

    def money(raw: str | None) -> float | None:
        return None if raw is None else _yuan(raw, scale)

    def pct(raw: str | None) -> float | None:
        return None if raw is None else _pct(raw)

    if layout == "q3":
        row.current, row.yoy = money(values[0]), pct(values[1])
        row.ytd, row.ytd_yoy = money(values[2]), pct(values[3])
    elif layout == "restated":
        row.current, row.prior, row.yoy = money(values[0]), money(values[2]), pct(values[3])
    else:
        row.current, row.prior, row.yoy = money(values[0]), money(values[1]), pct(values[2])


def _non_recurring(text: str, period: str) -> tuple[float | None, float | None]:
    section = _between(text, "非经常性损益项目", ("其他符合非经常性损益", "将《公开发行", "主要会计数据和财务指标发生变动"))
    scale = _section_unit(section)
    found = re.search(r"合\s*计([^\u4e00-\u9fff]{0,160})", section)
    if scale is None or not found:
        return None, None
    numbers = []
    for kind, raw in _tokenize(found.group(1)):
        if kind == "missing":
            numbers.append(None)
        elif kind == "num":
            numbers.append(_yuan(raw, scale))
    while numbers and numbers[-1] is None:
        numbers.pop()
    if period == "q3":
        if len(numbers) == 2 and numbers[0] is not None and numbers[1] is not None:
            return numbers[0], numbers[1]
        return None, None
    if numbers and numbers[0] is not None:
        return numbers[0], None
    return None, None


def _periodic_missing(figures: PeriodicFigures, layout: str) -> list[str]:
    missing: list[str] = []
    rows = (("营业收入", figures.revenue), ("归母净利润", figures.net_profit), ("扣非净利润", figures.deducted))
    for name, row in rows:
        if layout == "q3":
            fields = (("本报告期", row.current), ("本报告期同比", row.yoy),
                      ("年初至报告期末", row.ytd), ("年初至报告期末同比", row.ytd_yoy))
        elif layout == "annual":
            fields = (("本年", row.current), ("上年", row.prior), ("同比", row.yoy))
        else:
            fields = (("本报告期", row.current), ("上年同期", row.prior), ("同比", row.yoy))
        missing.extend(f"{name}{suffix}" for suffix, value in fields if value is None)
    if figures.non_recurring_current is None:
        missing.append("非经常性损益合计本报告期" if layout == "q3" else "非经常性损益合计")
    if layout == "q3" and figures.non_recurring_ytd is None:
        missing.append("非经常性损益合计年初至报告期末")
    return missing


def _forecast_section(text: str) -> str:
    start = text.find("本期业绩预计情况")
    if start < 0:
        start = text.find("业绩预告期间")
    if start < 0:
        return ""
    rest = text[start:]
    ends = []
    for marker in ("业绩变动原因", "与会计师事务所"):
        at = rest.find(marker)
        if at > 0:
            ends.append(at)
    quarter = re.search(r"其中[，,：:\s]*\d{4}\s*年", rest)
    if quarter and quarter.start() > 0:
        ends.append(quarter.start())
    return rest[:min(ends)] if ends else rest


def _forecast_state(section: str) -> str | None:
    if "属于扭亏为盈" in section or re.search(r"[☑√✓✔☒]\s*扭亏为盈", section):
        return "扭亏"
    if "为负值" in section:
        return "亏损"
    if "为正值" in section:
        return "盈利"
    return None


def _span(text: str, label: tuple[int, int], following: tuple[int, int] | None) -> str:
    compact, index = _compact(text)
    stops = []
    if following is not None:
        stops.append(following[0])
    for key in ("营业收入", "营业总收入", "基本每股收益"):
        at = compact.find(key)
        if at >= 0 and index[at] > label[1]:
            stops.append(index[at])
    end = min(stops) if stops else len(text)
    return text[label[1]:end]


def _forecast_line(span: str, section_unit: int | None, section_state: str | None) -> ForecastLine:
    line = ForecastLine()
    collapsed = re.sub(r"\s+", " ", span)
    yoy_match, yoy_low, yoy_high = _take_yoy(collapsed)
    amounts = collapsed
    if yoy_match is not None and yoy_low is not None and yoy_high is not None:
        amounts = collapsed[:yoy_match.start()] + " " + collapsed[yoy_match.end():]
        line.yoy = AmountRange(_pct(format(yoy_low, "f")), _pct(format(yoy_high, "f")))
    # 没被认成同比的百分数（如「同比：100%」）先整段去掉，免得它的数字前缀被当成金额
    amounts = _PCT_TOKEN_RE.sub(" ", amounts)
    ranged = _RANGE_RE.search(amounts)
    prior_src = amounts
    current_word = None
    if ranged:
        current_word = ranged.group(1)
        scale = _range_scale(ranged.group(3), ranged.group(5), section_unit)
        if scale is not None:
            low, high = _signed_pair(current_word, _as_decimal(ranged.group(2)), _as_decimal(ranged.group(4)))
            line.current = AmountRange(_money(low, scale), _money(high, scale))
        prior_src = amounts[:ranged.start()] + " " + amounts[ranged.end():]
    if ranged is None:
        # 定值预告：本期是一个确定金额，表里依次是本期、上年同期。只有一个金额时分不清是哪一列，留缺失。
        singles = list(_ONE_RE.finditer(amounts))
        if len(singles) < 2:
            return line
        first, second = singles[0], singles[1]
        scale = _token_scale(first.group(3), section_unit)
        if scale is not None:
            value = _money(_signed_one(first.group(1), _as_decimal(first.group(2))), scale)
            line.current = AmountRange(value, value)
            current_word = first.group(1)
        prior_scale = _token_scale(second.group(3), section_unit)
        if prior_scale is not None:
            line.prior = _money(_signed_one(second.group(1), _as_decimal(second.group(2))), prior_scale)
    else:
        single = _ONE_RE.search(prior_src)
        if single:
            scale = _token_scale(single.group(3), section_unit)
            if scale is not None:
                value = _signed_one(single.group(1), _as_decimal(single.group(2)))
                line.prior = _money(value, scale)
    if line.current.low is not None:
        line.state = _line_state(current_word, line.current.low, line.current.high, section_state)
    return line


def _take_yoy(text: str) -> tuple[re.Match[str] | None, Decimal | None, Decimal | None]:
    ranged = _YOY_RANGE_RE.search(text)
    if ranged:
        low, high = _signed_pair(ranged.group(1), _as_decimal(ranged.group(2)), _as_decimal(ranged.group(3)))
        return ranged, low, high
    single = _YOY_ONE_RE.search(text)
    if single:
        value, _ = _signed_pair(single.group(1), _as_decimal(single.group(2)), _as_decimal(single.group(2)))
        return single, value, value
    return None, None, None


def _signed_pair(direction: str | None, first: Decimal, second: Decimal) -> tuple[Decimal, Decimal]:
    if direction == "亏损" and first > 0 and second > 0:
        first, second = -first, -second
    elif direction in _DOWN:
        first, second = -abs(first), -abs(second)
    elif direction in _UP:
        first, second = abs(first), abs(second)
    return (first, second) if first <= second else (second, first)


def _signed_one(word: str | None, value: Decimal) -> Decimal:
    if word == "亏损" and value > 0:
        return -value
    return value


def _range_scale(left: str | None, right: str | None, section_unit: int | None) -> int | None:
    scales = [item for item in (_token_scale(left, None), _token_scale(right, None)) if item]
    if len(set(scales)) > 1:
        return None
    return scales[0] if scales else section_unit


def _token_scale(unit: str | None, section_unit: int | None) -> int | None:
    if unit == "万元":
        return 10_000
    if unit == "元":
        return 1
    return section_unit


def _money(amount: Decimal, scale: int) -> float:
    return float((amount * scale).quantize(_CENT, rounding=ROUND_HALF_UP))


def _line_state(word: str | None, low: float | None, high: float | None, section_state: str | None) -> str | None:
    if word == "扭亏为盈":
        return "扭亏"
    if word == "亏损" or (high is not None and high < 0):
        return "亏损"
    if section_state == "扭亏" and (word == "盈利" or (low is not None and low > 0)):
        return "扭亏"
    if word == "盈利" or (low is not None and low > 0):
        return "盈利"
    return section_state


def _forecast_missing(figures: ForecastFigures) -> list[str]:
    missing: list[str] = []
    if figures.period is None:
        missing.append("报告期")
    pairs = (("归母净利润", figures.net_profit), ("扣非净利润", figures.deducted))
    for name, line in pairs:
        if line.state is None:
            missing.append(f"{name}盈亏")
        if line.current.low is None:
            missing.append(f"{name}下限")
        if line.current.high is None:
            missing.append(f"{name}上限")
        if line.prior is None:
            missing.append(f"{name}上年同期")
        if line.yoy.low is None:
            missing.append(f"{name}同比下限")
        if line.yoy.high is None:
            missing.append(f"{name}同比上限")
    return missing


def _express_period(text: str) -> str | None:
    found = re.search(
        r"(\d{4})\s*年\s*(前三季度|半年度|第一季度|第三季度|一季度|三季度|年度)",
        text[:2000],
    )
    if not found:
        return None
    return f"{found.group(1)}年{found.group(2)}"


def _fill_express_line(line: ExpressLine, section: str, labels: tuple[str, ...], unit: int | None,
                       skip_inside: tuple[int, int] | None = None) -> None:
    located = _find_net(section, skip_inside) if labels == (_NET_LABEL,) else _find_label(section, labels)
    if located is None or unit is None:
        return
    tokens = _tokenize(_window(section, located))
    if len(tokens) != 3 or tuple(kind for kind, _ in tokens) != _LAYOUT["simple"]:
        return
    line.current = _yuan(tokens[0][1], unit)
    line.prior = _yuan(tokens[1][1], unit)
    line.yoy = _pct(tokens[2][1])


def _express_missing(figures: ExpressFigures) -> list[str]:
    missing: list[str] = []
    if figures.period is None:
        missing.append("报告期")
    rows = (("营业总收入", figures.revenue), ("归母净利润", figures.net_profit), ("扣非净利润", figures.deducted))
    for name, line in rows:
        if line.current is None:
            missing.append(f"{name}本期")
        if line.prior is None:
            missing.append(f"{name}上年同期")
        if line.yoy is None:
            missing.append(f"{name}增减幅度")
    return missing


def _fmt_amount(yuan: float) -> str:
    sign = "-" if yuan < 0 else ""
    cents = abs(int(round(yuan * 100)))
    if cents >= 10_000_000_000:
        return f"{sign}{cents / 10_000_000_000:.2f} 亿元"
    return f"{sign}{cents / 1_000_000:.2f} 万元"


def _fmt_pct(value: float) -> str:
    number = round(value, 2)
    if number > 0:
        return f"+{number:.2f}%"
    return f"{number:.2f}%"


def _fmt_money_span(low: float | None, high: float | None) -> str | None:
    if low is None and high is None:
        return None
    if low is None or high is None or _fmt_amount(low) == _fmt_amount(high):
        return _fmt_amount(low if low is not None else high)
    return f"{_fmt_amount(low)}至 {_fmt_amount(high)}"


def _fmt_pct_span(low: float | None, high: float | None) -> str | None:
    if low is None and high is None:
        return None
    if low is None or high is None or _fmt_pct(low) == _fmt_pct(high):
        return _fmt_pct(low if low is not None else high)
    return f"{_fmt_pct(low)}至{_fmt_pct(high)}"


def _metric(name: str, amount: float | None, yoy: float | None, prior: float | None = None,
            prior_name: str = "上年同期") -> str | None:
    if amount is None:
        return None
    text = f"{name} {_fmt_amount(amount)}"
    notes = []
    if prior is not None:
        notes.append(f"{prior_name} {_fmt_amount(prior)}")
    if yoy is not None:
        notes.append(f"同比 {_fmt_pct(yoy)}")
    if notes:
        text += "（" + "，".join(notes) + "）"
    return text


def _periodic_summary(figures: PeriodicFigures) -> list[str]:
    if figures.period is None:
        return []
    label = _PERIOD_LABEL[figures.period]
    if figures.period == "q3":
        current = _join_metrics((
            _metric("营业收入", figures.revenue.current, figures.revenue.yoy),
            _metric("归属于上市公司股东的净利润", figures.net_profit.current, figures.net_profit.yoy),
            _metric("扣除非经常性损益后的净利润", figures.deducted.current, figures.deducted.yoy),
        ))
        ytd = _join_metrics((
            _metric("营业收入", figures.revenue.ytd, figures.revenue.ytd_yoy),
            _metric("归属于上市公司股东的净利润", figures.net_profit.ytd, figures.net_profit.ytd_yoy),
            _metric("扣除非经常性损益后的净利润", figures.deducted.ytd, figures.deducted.ytd_yoy),
        ))
        parts = []
        if current:
            parts.append(f"{label}：{current}")
        if ytd:
            parts.append(f"年初至报告期末：{ytd}")
    else:
        prior_name = "上年" if figures.period == "annual" else "上年同期"
        current = _join_metrics((
            _metric("营业收入", figures.revenue.current, figures.revenue.yoy, figures.revenue.prior, prior_name),
            _metric("归属于上市公司股东的净利润", figures.net_profit.current, figures.net_profit.yoy,
                    figures.net_profit.prior, prior_name),
            _metric("扣除非经常性损益后的净利润", figures.deducted.current, figures.deducted.yoy,
                    figures.deducted.prior, prior_name),
        ))
        parts = [f"{label}：{current}"] if current else []
    non_recurring = []
    if figures.non_recurring_current is not None:
        non_recurring.append(("本报告期 " if figures.period == "q3" else "") + _fmt_amount(figures.non_recurring_current))
    if figures.non_recurring_ytd is not None:
        non_recurring.append("年初至报告期末 " + _fmt_amount(figures.non_recurring_ytd))
    if non_recurring:
        parts.append("非经常性损益合计：" + "，".join(non_recurring))
    return parts


def _forecast_summary(figures: ForecastFigures) -> list[str]:
    parts = []
    body = _join_metrics((_forecast_metric("归属于上市公司股东的净利润", figures.net_profit),
                          _forecast_metric("扣除非经常性损益后的净利润", figures.deducted)))
    if figures.period or body:
        prefix = f"业绩预告（{figures.period}）" if figures.period else "业绩预告"
        parts.append(f"{prefix}：{body}" if body else prefix)
    return parts


def _forecast_metric(name: str, line: ForecastLine) -> str | None:
    low, high = line.current.low, line.current.high
    if line.state == "亏损" and all(v is None or v < 0 for v in (low, high)):
        # 「亏损」已经说明方向，金额写绝对值，小的在前：亏损 5600 万元至 6000 万元
        low, high = (None if high is None else -high), (None if low is None else -low)
    amount = _fmt_money_span(low, high)
    if amount is None and line.prior is None and line.yoy.low is None:
        return None
    text = name + "："
    if line.state:
        text += line.state + " "
    if amount:
        text += amount
    notes = []
    if line.prior is not None:
        notes.append(f"上年同期 {_fmt_amount(line.prior)}")
    yoy = _fmt_pct_span(line.yoy.low, line.yoy.high)
    if yoy:
        notes.append(f"同比 {yoy}")
    if notes:
        text += "（" + "，".join(notes) + "）"
    return text.strip()


def _express_summary(figures: ExpressFigures) -> list[str]:
    body = _join_metrics((
        _metric("营业总收入", figures.revenue.current, figures.revenue.yoy, figures.revenue.prior),
        _metric("归属于上市公司股东的净利润", figures.net_profit.current, figures.net_profit.yoy, figures.net_profit.prior),
        _metric("扣除非经常性损益后的净利润", figures.deducted.current, figures.deducted.yoy, figures.deducted.prior),
    ))
    if figures.period is None and not body:
        return []
    prefix = f"业绩快报（{figures.period}）" if figures.period else "业绩快报"
    return [f"{prefix}：{body}" if body else prefix]


def _join_metrics(items: tuple[str | None, ...]) -> str:
    return "，".join(item for item in items if item)
