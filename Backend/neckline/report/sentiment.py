"""情绪仪表盘(plan 2.1/§2.3)。从 `limit_derived`(0.4b 自算涨跌停衍生表)+ `daily`
算当日涨停/跌停家数、连板最高高度、炸板率、昨日涨停股今日平均溢价,输出**明日
仓位额度三态**(满额/半额/休息)。

**诚实标注(§2.3 硬性要求,不可省略)**:下面这组三态阈值是第一版启发式,尚未经过
回测验证。阶段 1 的 P1 结论明确否决了"用滞后指标做实时开仓闸门"(MA20 闸门样本
内外双双变差,见 `research/stage1_report.md` P1 节)——情绪面阈值当前处在同一坑位
风险中,不能假装它已经验证过。市场择时的职责从 P1 否决的 MA20 转移到这里,但转移
本身不等于"这组新阈值就是对的",只是换了一个更快的候选信号来源,仍待阶段 2+ 用
实盘归因持续迭代调整。阈值全部收成命名常量,方便后续回测/复盘时定点修改。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from datetime import date
from typing import Optional, Tuple

import polars as pl

from neckline.calendar import prev_trading_day
from neckline.data.market_data import get_market_slice

# —— 仓位额度三态 ——————————————————————————————————————————————————
FULL = "满额"
HALF = "半额"
REST = "休息"
_TIER_ORDER = (REST, HALF, FULL)

# —— 启发式阈值(未回测,§2.3 硬性要求原样标注;命名常量便于定点调整)——————————
MIN_LIMIT_UP_FOR_FULL = 40          # 涨停家数达到此档且炸板率、跌停家数不超限 → 满额
MAX_LIMIT_DOWN_FOR_FULL = 10
MAX_ZABAN_RATE_FOR_FULL = 0.30
MIN_LIMIT_UP_FOR_REST = 15          # 涨停家数低于此档 → 直接休息(市场太弱,机会不足)
MIN_ZABAN_RATE_FOR_REST = 0.50      # 炸板率过半 → 直接休息(情绪退潮明显)
PREMIUM_WARN_THRESHOLD = -0.02      # 昨日涨停股今日平均溢价 ≤ -2% → 额度下调一档(警戒)


@dataclass
class SentimentDashboard:
    trade_date: date
    limit_up_count: int
    limit_down_count: int
    zaban_count: int
    zaban_rate: float
    max_consec_limit_up: int
    prev_limit_up_premium_avg: Optional[float]  # None = 昨日无涨停股或数据缺失,非"溢价为0"
    prev_limit_up_sample: int
    position_quota: str
    quota_reason: str


def _base_tier(limit_up: int, limit_down: int, zaban_rate: float) -> str:
    if limit_up < MIN_LIMIT_UP_FOR_REST or zaban_rate >= MIN_ZABAN_RATE_FOR_REST or limit_down > limit_up:
        return REST
    if limit_up >= MIN_LIMIT_UP_FOR_FULL and zaban_rate <= MAX_ZABAN_RATE_FOR_FULL and limit_down <= MAX_LIMIT_DOWN_FOR_FULL:
        return FULL
    return HALF


def _downgrade(tier: str) -> str:
    i = _TIER_ORDER.index(tier)
    return _TIER_ORDER[max(0, i - 1)]


def _prev_limit_up_premium(trade_date: date, parquet_dir: Optional[Path]) -> Tuple[Optional[float], int]:
    """昨日涨停股今日平均溢价 = mean(今日收盘/昨日收盘 - 1),样本为【昨日命中涨停】的票。
    昨日无涨停票 / 任一日 daily 缺失 → (None, 0),调用方据此不做该项的降级判断。"""
    prev_day = prev_trading_day(trade_date)
    prev_limit = get_market_slice(prev_day, table="limit_derived", parquet_dir=parquet_dir)
    if prev_limit.is_empty():
        return None, 0
    prev_up_codes = prev_limit.filter(pl.col("is_limit_up"))["ts_code"].to_list()
    if not prev_up_codes:
        return None, 0

    today_daily = get_market_slice(trade_date, table="daily", parquet_dir=parquet_dir)
    prev_daily = get_market_slice(prev_day, table="daily", parquet_dir=parquet_dir)
    if today_daily.is_empty() or prev_daily.is_empty():
        return None, 0

    today_close = dict(zip(today_daily["ts_code"].to_list(), today_daily["close"].to_list()))
    prev_close = dict(zip(prev_daily["ts_code"].to_list(), prev_daily["close"].to_list()))
    rets = []
    for code in prev_up_codes:
        c0, c1 = prev_close.get(code), today_close.get(code)
        if c0 and c1:
            rets.append(c1 / c0 - 1)
    if not rets:
        return None, 0
    return sum(rets) / len(rets), len(rets)


def compute_sentiment(trade_date: date, parquet_dir: Optional[Path] = None) -> SentimentDashboard:
    """算当日情绪仪表盘 + 明日仓位额度三态。`limit_derived`/`daily` 当日缺数据 →
    各计数按 0 处理(优雅降级,不崩;调用方可从 quota_reason 文案里看出数据缺失)。"""
    limit_today = get_market_slice(trade_date, table="limit_derived", parquet_dir=parquet_dir)
    limit_up_count = limit_down_count = zaban_count = max_consec = 0
    if not limit_today.is_empty():
        limit_up_count = int(limit_today.filter(pl.col("is_limit_up")).height)
        limit_down_count = int(limit_today.filter(pl.col("is_limit_down")).height)
        zaban_count = int(limit_today.filter(pl.col("is_zaban")).height)
        if limit_up_count:
            max_consec = int(limit_today.filter(pl.col("is_limit_up"))["consec_limit_up_days"].max() or 0)

    denom = zaban_count + limit_up_count
    zaban_rate = (zaban_count / denom) if denom > 0 else 0.0

    premium_avg, premium_n = _prev_limit_up_premium(trade_date, parquet_dir)

    tier = _base_tier(limit_up_count, limit_down_count, zaban_rate)
    reason_parts = [f"涨停{limit_up_count}家/跌停{limit_down_count}家/炸板率{zaban_rate:.0%}/最高连板{max_consec}板"]
    if premium_avg is not None and premium_avg <= PREMIUM_WARN_THRESHOLD:
        downgraded = _downgrade(tier)
        if downgraded != tier:
            reason_parts.append(
                f"昨日涨停股今日平均溢价{premium_avg:+.1%}(样本{premium_n}只,≤{PREMIUM_WARN_THRESHOLD:.0%}警戒线)→ 额度下调一档"
            )
        tier = downgraded
    reason_parts.append("(三态阈值第一版为启发式,未经回测验证,实盘归因迭代中,见 PROJECT_PLAN §2.3)")

    return SentimentDashboard(
        trade_date=trade_date,
        limit_up_count=limit_up_count,
        limit_down_count=limit_down_count,
        zaban_count=zaban_count,
        zaban_rate=zaban_rate,
        max_consec_limit_up=max_consec,
        prev_limit_up_premium_avg=premium_avg,
        prev_limit_up_sample=premium_n,
        position_quota=tier,
        quota_reason=";".join(reason_parts),
    )


__all__ = [
    "SentimentDashboard",
    "compute_sentiment",
    "FULL",
    "HALF",
    "REST",
]
