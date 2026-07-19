"""回测报告(plan 0.7):净值曲线、胜率、盈亏比、最大回撤、年化、盈利因子。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List

import polars as pl

from neckline.backtest.portfolio import ClosedTrade, Portfolio

TRADING_DAYS_PER_YEAR = 252


@dataclass
class BacktestReport:
    equity_curve: pl.DataFrame  # columns: trade_date, equity
    closed_trades: List[ClosedTrade]
    initial_cash: float
    final_equity: float
    total_return: float
    annualized_return: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    profit_loss_ratio: float
    n_trades: int
    n_trading_days: int

    def summary(self) -> str:
        return (
            f"回测报告(交易日数={self.n_trading_days})\n"
            f"  初始资金:      {self.initial_cash:,.2f}\n"
            f"  期末权益:      {self.final_equity:,.2f}\n"
            f"  总收益率:      {self.total_return * 100:.2f}%\n"
            f"  年化收益率:    {self.annualized_return * 100:.2f}%\n"
            f"  最大回撤:      {self.max_drawdown * 100:.2f}%\n"
            f"  成交回合数:    {self.n_trades}\n"
            f"  胜率:          {self.win_rate * 100:.2f}%\n"
            f"  盈亏比:        {self.profit_loss_ratio:.2f}\n"
            f"  盈利因子:      {self.profit_factor:.2f}\n"
        )


def build_report(portfolio: Portfolio, equity_curve: List[tuple], initial_cash: float) -> BacktestReport:
    """`equity_curve`: List[(trade_date, equity)],升序。"""
    ec_df = pl.DataFrame(equity_curve, schema=["trade_date", "equity"], orient="row")
    n_days = len(ec_df)
    final_equity = float(ec_df["equity"][-1]) if n_days else initial_cash
    total_return = final_equity / initial_cash - 1 if initial_cash else 0.0

    if n_days >= 2:
        years = n_days / TRADING_DAYS_PER_YEAR
        annualized_return = (1 + total_return) ** (1 / years) - 1 if years > 0 and (1 + total_return) > 0 else 0.0
    else:
        annualized_return = 0.0

    max_drawdown = _max_drawdown(ec_df["equity"].to_list())

    trades = portfolio.closed_trades
    n_trades = len(trades)
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / n_trades if n_trades else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    avg_win = (gross_profit / len(wins)) if wins else 0.0
    avg_loss = (gross_loss / len(losses)) if losses else 0.0
    profit_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else (float("inf") if avg_win > 0 else 0.0)

    return BacktestReport(
        equity_curve=ec_df,
        closed_trades=trades,
        initial_cash=initial_cash,
        final_equity=final_equity,
        total_return=total_return,
        annualized_return=annualized_return,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        profit_factor=profit_factor,
        profit_loss_ratio=profit_loss_ratio,
        n_trades=n_trades,
        n_trading_days=n_days,
    )


def _max_drawdown(equity_values: List[float]) -> float:
    if not equity_values:
        return 0.0
    peak = equity_values[0]
    max_dd = 0.0
    for v in equity_values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


__all__ = ["BacktestReport", "build_report"]
