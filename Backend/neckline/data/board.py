"""板块分类(plan 0.4b 涨跌停衍生表的板块判定层)。

优先用 `stock_basic.market` 字段(TuShare 原生分类,实测值:主板/创业板/科创板/
北交所,权威、无需自己猜前缀)。代码前缀正则只作 `market` 缺失时的 fallback。

黑名单/分类口径(继承 LinoN 教训,§3.7):按板块【整段正则】,禁止枚举精确子段
——旧写法 "300" 会漏 301/302,旧写法 "688" 会漏 689(科创板 CDR)。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class Board(str, Enum):
    MAIN = "MAIN"    # 主板(含沪深主板、B 股等未归类项默认落这里)
    GEM = "GEM"      # 创业板
    STAR = "STAR"    # 科创板
    BSE = "BSE"      # 北交所


_MARKET_NAME_MAP = {
    "主板": Board.MAIN,
    "创业板": Board.GEM,
    "科创板": Board.STAR,
    "北交所": Board.BSE,
}

# 顺序不可换:STAR(688/689)必须先判,否则会被下面的 "8" 前缀 fallback 误吞进 BSE。
_STAR_RE = re.compile(r"^(688|689)")
_GEM_RE = re.compile(r"^30")
_BSE_RE = re.compile(r"^(920|8|4)")


def classify_by_code(ts_code: str) -> Board:
    """代码前缀 fallback 分类,`market` 字段缺失 / 未知值时用。"""
    code = ts_code.split(".")[0]
    if _STAR_RE.match(code):
        return Board.STAR
    if _GEM_RE.match(code):
        return Board.GEM
    if _BSE_RE.match(code):
        return Board.BSE
    return Board.MAIN


def classify(market: Optional[str], ts_code: str) -> Board:
    """优先 `stock_basic.market` 字段,缺失/未知值时退化代码前缀正则。"""
    if market and market in _MARKET_NAME_MAP:
        return _MARKET_NAME_MAP[market]
    return classify_by_code(ts_code)


__all__ = ["Board", "classify", "classify_by_code"]
