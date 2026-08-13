"""持仓票「当日无 EOD 行」的陈旧度与原因判定(plan §五 v1.4-①-B / §七 P0-2)。

**为什么要有这一层**:`002036.SZ`(联创电子)自 2026-07-23 起无行情分区,而
`stock_basic.list_status` 仍 `L` —— 持仓卡拿不到当日收盘价。此前的行为是**静默沿用最后
一个有价的口径**(实时源拉不到 → price=0.0 兜底),用户看不出「这个价是哪天的」。本模块
把这件事**显式化**:沿用最后有效收盘价可以,但必须同时告诉用户**陈旧了几个交易日、最后
成交日是哪天、以及为什么**(§3.8「没有」与「没看」必须能分开)。

**三种 reason(诊断只决定标签取值,口径由 plan 定死)**:
  · `suspended` —— 当日在 `suspend_d` 停牌名单里(**2026-07-28 真 token 探活确认 600 元
    档可用**;`002036.SZ` 自 20260723 起连续在榜 = 真停牌,非数据源缺口;当日全市场同类
    9 只 = 普遍现象非个案)。
  · `data_gap` —— 全市场当日有数据、停牌名单里也没有它,却唯独这只票没行 = 数据源缺口。
  · `unknown` —— 停牌名单本身拿不到(接口失败 / 该日未落盘)→ **如实说不知道**,绝不猜
    成 suspended(猜错方向会让「时间退出判定挂起」这个豁免建立在臆测上)。

**锚点是「全市场最近一个有 EOD 数据的交易日」,不是「今天」** —— 盘中(16:35 之前)当日
EOD 本就没落盘,若拿今天当锚点会把**每一只**正常票都误报成「陈旧 1 天」。锚点取不到
(全市场都没数据)→ 一律 `unknown`、`stale=False`,不产出任何假警报。

本模块**只读 parquet + 交易日历,不联网**(`GET /positions` 请求期要用,不能挂网络)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import polars as pl

logger = logging.getLogger(__name__)

REASON_SUSPENDED = "suspended"
REASON_DATA_GAP = "data_gap"
REASON_UNKNOWN = "unknown"

# 找「全市场最近一个有 EOD 的交易日」时最多往回翻几个交易日(长假 + 断跑几天的余量)。
_MARKET_ANCHOR_LOOKBACK_DAYS = 15
# 找某只票「最后一个有行的交易日」时最多往回翻几个交易日(约一个季度;超出即如实说不知道,
# 不假装算得出 —— 长停牌票的真实停牌起点可能在更早,`lastCloseDate=""` 就是诚实答案)。
_CODE_LOOKBACK_DAYS = 60


@dataclass(frozen=True)
class PriceStale:
    """一只持仓票的价格陈旧度。`stale_days` = 该票**没有 EOD 行**的交易日数
    (从最后成交日的次个交易日算到市场锚点日,含锚点日)。"""
    stale_days: int
    last_close_date: str     # 'YYYYMMDD';回看窗口内都找不到 → ""(如实留空,不臆造)
    reason: str              # suspended | data_gap | unknown

    def to_public_dict(self) -> Dict[str, object]:
        return {
            "staleDays": self.stale_days,
            "lastCloseDate": self.last_close_date,
            "reason": self.reason,
        }


def _trading_days_back(anchor: date, n: int) -> List[date]:
    """`anchor` 及其之前的 n 个交易日(降序)。用 `trading_days_between` 一次取,
    避免逐自然日 `prev_trading_day` 循环(CLAUDE.md 记的 trade_cal 覆盖坑)。"""
    from neckline.calendar import trading_days_between

    days = trading_days_between(anchor - timedelta(days=int(n * 2.2) + 20), anchor)
    return list(reversed(days))[:n]


def _day_codes(table: str, d: date, parquet_dir: Optional[Path] = None) -> Optional[set]:
    """某表某交易日分区里的 `ts_code` 集合;分区不存在 / 读失败 / 无该列 → None。

    ⚠ **刻意读单个分区文件、只取 `ts_code` 一列**,不走 `get_market_slice`(它是整表
    `scan_parquet` + 过滤):① 本函数在 `GET /positions` 请求期跑,整表扫 6 年 ×5500 行
    ×N 天太贵;② 整表扫会被**任何一个**坏分区连坐(v1.3.5 schema 漂移事故的读侧表现),
    而陈旧度只是附加标注,不该有这种连坐面。"""
    from neckline.data.market_data import day_file_path

    p = day_file_path(table, d, parquet_dir)
    if not p.exists():
        return None
    try:
        return set(pl.read_parquet(p, columns=["ts_code"])["ts_code"].to_list())
    except Exception:  # noqa: BLE001  单个坏/空分区不该让整条持仓链路挂
        logger.warning("读 %s 分区 %s 失败(按「该日无数据」处理)", table, d, exc_info=True)
        return None


def market_anchor_date(as_of: date, parquet_dir: Optional[Path] = None) -> Optional[date]:
    """全市场**最近一个真有 EOD 数据**的交易日(≤ as_of)。查不到 → None。

    判据是「分区文件存在**且非空**」——backfill 早年落过一批 0 行空文件(v1.3.5 事故的
    脏基准来源),只判 `exists()` 会把空分区当成有数据。"""
    for d in _trading_days_back(as_of, _MARKET_ANCHOR_LOOKBACK_DAYS):
        codes = _day_codes("daily", d, parquet_dir)
        if codes:
            return d
    return None


def _suspended_codes(anchor: date, parquet_dir: Optional[Path] = None) -> Optional[set]:
    """锚点日的停牌代码集合;该日 `suspend_d` 未落盘 / 读失败 → None(= 不知道)。"""
    return _day_codes("suspend_d", anchor, parquet_dir)


def resolve_price_stale(
    codes: Sequence[str],
    as_of: date,
    parquet_dir: Optional[Path] = None,
) -> Dict[str, PriceStale]:
    """`{ts_code: PriceStale}`,**只含真的陈旧的票**(当日有 EOD 行的票不在返回集)。

    调用方(`GET /positions`)对不在返回集的票原样不填 `priceStale`(null)。全市场锚点
    取不到 → 返回空 dict(不产假警报,见模块头)。
    """
    codes = [c for c in dict.fromkeys(codes) if c]
    if not codes:
        return {}
    anchor = market_anchor_date(as_of, parquet_dir)
    if anchor is None:
        logger.info("全市场近 %d 个交易日均无 EOD 数据,陈旧度判定跳过(不产假警报)",
                    _MARKET_ANCHOR_LOOKBACK_DAYS)
        return {}

    present = _day_codes("daily", anchor, parquet_dir) or set()
    missing = [c for c in codes if c not in present]
    if not missing:
        return {}

    suspended = _suspended_codes(anchor, parquet_dir)
    # 逐个交易日往回翻,**一旦所有待判票都找到最后成交日就停** —— 正常票 1 次读盘即出,
    # 停牌 N 天的票 N+1 次;只有「回看窗口内一行都没有」的极端票才翻满窗口。
    window = _trading_days_back(anchor, _CODE_LOOKBACK_DAYS)   # 降序,window[0] == anchor
    pending = set(missing)
    last_seen: Dict[str, date] = {}
    scanned = 0
    for d in window[1:]:            # 锚点日已知这些票都没有,从前一个交易日开始翻
        if not pending:
            break
        scanned += 1
        day_codes = _day_codes("daily", d, parquet_dir)
        if not day_codes:
            continue
        for code in list(pending):
            if code in day_codes:
                last_seen[code] = d
                pending.discard(code)

    out: Dict[str, PriceStale] = {}
    for code in missing:
        seen = last_seen.get(code)
        if seen is not None:
            # 没有 EOD 行的交易日数 = 锚点窗口里晚于最后成交日的交易日个数(含锚点日)。
            stale_days = sum(1 for d in window if d > seen)
            last_date = seen.strftime("%Y%m%d")
        else:
            # 回看窗口内一行都没有 → 只知道「至少翻过的这些天都没有」,给下界 + 留空日期。
            stale_days = scanned + 1
            last_date = ""
        if suspended is None:
            reason = REASON_UNKNOWN
        elif code in suspended:
            reason = REASON_SUSPENDED
        else:
            reason = REASON_DATA_GAP
        out[code] = PriceStale(stale_days=stale_days, last_close_date=last_date, reason=reason)
    return out


__all__ = [
    "PriceStale",
    "REASON_SUSPENDED",
    "REASON_DATA_GAP",
    "REASON_UNKNOWN",
    "market_anchor_date",
    "resolve_price_stale",
]
