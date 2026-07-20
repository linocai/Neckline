"""阶段 1 研究工具箱(所有 P1–P10 runner 共用,保证同一设定下可比)。

两条度量跑道:
  1. 信号级事件研究(`neckline.research.eventstudy`):向量化、瞬时,测「信号本身」
     的前瞻收益期望——用于 P3 强势赛马、P4/P5/P6 禁买边际、P1 市场状态分层。
  2. 组合级回测(`MomentumStrategy` + `BacktestEngine`):带 T+1/涨跌停/停牌/滑点手续费
     + 仓位纪律 + 路径依赖退出——用于 P7 回落止盈、P8 时间退出、P9 冷却、P10 仓位、
     rule v1 与 dummy 基准对照。**每次 run 建全新 strategy 实例**(策略持状态,勿复用)。

分层(任务纪律 5「所有结果按年份 + 市场状态分段」):组合级在【已平仓回合】上按
买入日的年份 / 上证 MA20 上下分桶算胜率/盈利因子/总盈亏;信号级用 event_study_grouped。

回测设定(纪律章程 2.1/2.2,全研究同设定):初始 12 万、单笔 ≤2 万、≤5 只、敞口 ≤60%、
T+1、-5% 止损、真实费用 + 保守滑点(Broker 默认)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import polars as pl

from neckline.backtest.engine import BacktestEngine
from neckline.backtest.portfolio import ClosedTrade, Portfolio
from neckline.backtest.report import BacktestReport
from neckline.strategy.features import market_state_labels
from neckline.strategy.momentum import MomentumConfig, MomentumStrategy
from neckline.research.panel import (
    SAMPLE_IN_START, SAMPLE_IN_END, SAMPLE_OUT_START, SAMPLE_OUT_END, load_or_build_panel,
)

INITIAL_CASH = 120000.0
PANEL_CACHE = Path(__file__).resolve().parent / "_cache" / "panel_full.parquet"

_PANEL: Optional[pl.DataFrame] = None
_STATES: Optional[pl.DataFrame] = None


def get_panel(rebuild: bool = False) -> pl.DataFrame:
    """全期研究面板(2020-01 ~ 2026-07,含 fwd_* + ret_20d_pct),进程内缓存。"""
    global _PANEL
    if _PANEL is None or rebuild:
        _PANEL = load_or_build_panel(cache_path=PANEL_CACHE, rebuild=rebuild)
    return _PANEL


def market_states() -> pl.DataFrame:
    """上证 MA20 市场状态标签(trade_date -> sse_above_ma / year),进程内缓存。"""
    global _STATES
    if _STATES is None:
        _STATES = market_state_labels(SAMPLE_IN_START, SAMPLE_OUT_END)
    return _STATES


# ======================================================================
#  组合级回测
# ======================================================================

def run_pf(
    cfg: MomentumConfig,
    start: date,
    end: date,
    panel: Optional[pl.DataFrame] = None,
    initial_cash: float = INITIAL_CASH,
) -> Tuple[BacktestReport, Portfolio]:
    """跑一次组合回测,返回 (report, portfolio)。全新 strategy 实例(勿复用状态)。"""
    p = panel if panel is not None else get_panel()
    strat = MomentumStrategy(p, cfg, initial_cash=initial_cash)
    eng = BacktestEngine(strat, start, end, initial_cash=initial_cash)
    rep = eng.run()
    return rep, eng.last_portfolio


@dataclass
class TradeStats:
    n: int
    win_rate: float
    profit_factor: float
    total_pnl: float
    mean_pnl_pct: float

    def row(self) -> dict:
        return {
            "n": self.n,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "total_pnl": self.total_pnl,
            "mean_pnl_pct": self.mean_pnl_pct,
        }


def _stats(trades: List[ClosedTrade]) -> TradeStats:
    n = len(trades)
    if n == 0:
        return TradeStats(0, float("nan"), float("nan"), 0.0, float("nan"))
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    win_rate = len(wins) / n
    mean_pct = sum(t.pnl_pct for t in trades) / n
    return TradeStats(n, win_rate, pf, sum(pnls), mean_pct)


def trade_stats(trades: List[ClosedTrade]) -> TradeStats:
    return _stats(trades)


def _state_lookup() -> Tuple[Dict[date, bool], Dict[date, int]]:
    st = market_states()
    above = dict(zip(st["trade_date"].to_list(), st["sse_above_ma"].to_list()))
    yr = dict(zip(st["trade_date"].to_list(), st["year"].to_list()))
    return above, yr


def stratify_by_year(trades: List[ClosedTrade]) -> pl.DataFrame:
    """按买入日年份分层的回合统计。"""
    _, yr = _state_lookup()
    buckets: Dict[int, List[ClosedTrade]] = {}
    for t in trades:
        y = yr.get(t.buy_date, t.buy_date.year)
        buckets.setdefault(y, []).append(t)
    rows = []
    for y in sorted(buckets):
        s = _stats(buckets[y]).row()
        s = {"year": y, **s}
        rows.append(s)
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def stratify_by_state(trades: List[ClosedTrade]) -> pl.DataFrame:
    """按买入日上证 MA20 上/下方分层的回合统计(P1 判决依据)。"""
    above, _ = _state_lookup()
    buckets: Dict[str, List[ClosedTrade]] = {"SSE>MA20": [], "SSE<=MA20": [], "unknown": []}
    for t in trades:
        a = above.get(t.buy_date)
        key = "unknown" if a is None else ("SSE>MA20" if a else "SSE<=MA20")
        buckets[key].append(t)
    rows = []
    for key in ("SSE>MA20", "SSE<=MA20", "unknown"):
        if buckets[key]:
            rows.append({"state": key, **_stats(buckets[key]).row()})
    return pl.DataFrame(rows) if rows else pl.DataFrame()


# ======================================================================
#  表格渲染(markdown 报告用)
# ======================================================================

def fmt(df: pl.DataFrame, floatfmt: str = "{:.4f}", intcols: Tuple[str, ...] = ("n", "year", "hold_days")) -> str:
    if df is None or df.is_empty():
        return "(空)"
    cols = df.columns
    out_rows = []
    for r in df.iter_rows(named=True):
        cells = []
        for c in cols:
            v = r[c]
            if v is None:
                cells.append("nan")
            elif c in intcols and isinstance(v, (int, float)) and v == v:
                cells.append(str(int(v)))
            elif isinstance(v, float):
                cells.append("nan" if v != v else ("inf" if abs(v) > 1e6 else floatfmt.format(v)))
            else:
                cells.append(str(v))
        out_rows.append(cells)
    widths = [max(len(c), *(len(r[i]) for r in out_rows)) for i, c in enumerate(cols)]
    line = lambda cells: "| " + " | ".join(s.rjust(widths[i]) for i, s in enumerate(cells)) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    return "\n".join([line(cols), sep] + [line(r) for r in out_rows])


def summary_row(rep: BacktestReport, label: str) -> dict:
    """回测报告压成一行(多配置对照表用)。"""
    return {
        "config": label,
        "n_trades": rep.n_trades,
        "total_ret": rep.total_return,
        "ann_ret": rep.annualized_return,
        "max_dd": rep.max_drawdown,
        "win_rate": rep.win_rate,
        "pf": rep.profit_factor,
        "final_equity": rep.final_equity,
    }


__all__ = [
    "INITIAL_CASH", "PANEL_CACHE", "get_panel", "market_states", "run_pf",
    "TradeStats", "trade_stats", "stratify_by_year", "stratify_by_state",
    "fmt", "summary_row",
    "SAMPLE_IN_START", "SAMPLE_IN_END", "SAMPLE_OUT_START", "SAMPLE_OUT_END",
]
