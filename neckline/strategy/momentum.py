"""母战法可配置策略(plan 1.3/1.4/1.7/1.8，§2.2）。engine-native：实现 `Strategy`
接口，喂给 `BacktestEngine` 跑组合回测（带 T+1 / 涨跌停锁 / 停牌 / 滑点手续费）。

同码（§2.6）：选股信号全部来自 `neckline.strategy.signals`（与事件研究同一份定义）。
策略在构造时接收 `build_research_panel` 产出的**特征面板**，按日切片查信号——特征
是比值/布尔（anchor 不变），供选股；**退出用引擎给的 `context.market_slice` 价格**
（引擎自己的前复权锚点，与 `Portfolio` 记录的 buy_price 同锚，比值一致，见下）。

退出机制（可配，路径依赖，本引擎逐日 mark-to-market 后决策、T+1 开盘成交）：
    · 止损（1.3）：持仓日收盘或最低价 ≤ buy_price×(1−stop_pct) → 次日开盘卖出。
      日线近似：破位在**收盘确认、次日开盘成交**，较券商 intraday 条件单偏保守
      （跳空时多计损失，honest 方向）。§2.1 -5% 单一常量口径。
    · 回落止盈（1.4）：自建仓以来收盘峰值回落 ≥ take_profit_retrace → 次日开盘卖出。
    · 时间退出（1.4）：持有满 max_hold_days 交易日 → 卖出（印证「4–7 自然日打平」）。
    · 冷却（1.7）：某票**亏损**卖出后 cooldown_days 交易日内不再买入。

仓位纪律（1.8/§2.1）：单笔 ≤ single_cap、最多 max_positions 只、总敞口 ≤
max_exposure_frac×初始资金。次周单笔减半（P10 挂起项）作可选开关，供验证性回测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional

import polars as pl

from neckline.backtest.strategy import BacktestContext, Order, Strategy
from neckline.calendar import trading_days_between
from neckline.strategy import signals as S


@dataclass
class MomentumConfig:
    # —— 选股 ——
    strength: str = "volprice"          # limitup_gene | ret20 | ret20_pct | volprice | none
    strength_min_count: int = 1         # limitup_gene 用
    strength_min_ret: float = 0.15      # ret20 用
    strength_min_pct: float = 0.90      # ret20_pct 用
    buypoint: str = "pullback"          # pullback | breakout | either | none
    breakout_vol_expand: float = 1.5
    # —— 禁买过滤（P4/P5/P6，True=启用该过滤）——
    forbid_green_bigdown: Optional[float] = None   # 如 -0.03
    forbid_far_from_high: Optional[float] = None   # 如 -0.15
    forbid_new_days: Optional[int] = None          # 如 120
    forbid_high_elasticity: bool = False
    # 选股附加收紧（研究探针用；None=不启用）
    shallow_pullback: Optional[float] = None       # dist_from_high_20d >= 此值(如 -0.05)
    max_turnover: Optional[float] = None           # turnover_rate <= 此值
    # —— 排序（选 top-N 填仓；近零 alpha 下影响小，默认取最浅回调=最贴前高）——
    rank_by: str = "dist_from_high_20d"
    rank_desc: bool = True
    # —— 退出 ——
    stop_pct: Optional[float] = 0.05               # -5% 止损（None=不设止损）
    take_profit_retrace: Optional[float] = None    # 回落止盈阈值（None=不设）
    max_hold_days: int = 3                         # 时间退出（交易日）
    cooldown_days: int = 0                         # 同票亏损后冷却
    # —— 仓位纪律 ——
    single_cap: float = 20000.0
    max_positions: int = 5
    max_exposure_frac: float = 0.60
    week_halving: bool = False                      # P10 挂起项开关：单周亏损后次周单笔减半
    week_halving_threshold: float = 0.05            # 挂起项定义口径：单周实现亏损 ≥ 本值×初始资金触发
                                                    # （5%=挂起项「次周单笔减半」；区别于 §2.1 已采纳的 2% 强制复盘线）


class MomentumStrategy(Strategy):
    def __init__(self, panel: pl.DataFrame, config: MomentumConfig, initial_cash: float = 120000.0) -> None:
        self.config = config
        self.initial_cash = initial_cash
        # 预筛：选股域 + 强势 + 买点 + 禁买过滤 一次性算成布尔，按日切片(快)
        self._entry_mask = self._build_entry_mask()
        filtered = panel.filter(self._entry_mask)
        self._by_date: Dict[date, pl.DataFrame] = {}
        for (d,), sub in filtered.group_by(["trade_date"]):
            self._by_date[d] = sub
        # 状态
        self._peak_close: Dict[str, float] = {}     # 持仓票自建仓以来收盘峰值
        self._cooldown_until: Dict[str, date] = {}   # 冷却到期日(含)
        self._week_loss: Dict[tuple, float] = {}     # (iso_year,iso_week) -> 已实现亏损累计
        self._processed_closed = 0                   # 已处理的 closed_trades 数(增量扫描)

    # —— 选股域 + 信号 → 单一布尔表达式 ——
    def _build_entry_mask(self) -> pl.Expr:
        from neckline.research.panel import base_universe_expr

        c = self.config
        mask = base_universe_expr()
        # 强势
        if c.strength == "limitup_gene":
            mask = mask & S.strength_limitup_gene(c.strength_min_count)
        elif c.strength == "ret20":
            mask = mask & S.strength_ret_rank(c.strength_min_ret)
        elif c.strength == "ret20_pct":
            mask = mask & S.strength_ret_rank_pct(c.strength_min_pct)
        elif c.strength == "volprice":
            mask = mask & S.strength_volprice()
        # 买点
        if c.buypoint == "pullback":
            mask = mask & S.buy_pullback()
        elif c.buypoint == "breakout":
            mask = mask & S.buy_breakout(c.breakout_vol_expand)
        elif c.buypoint == "either":
            mask = mask & (S.buy_pullback() | S.buy_breakout(c.breakout_vol_expand))
        # 禁买
        if c.forbid_green_bigdown is not None:
            mask = mask & ~S.forbid_green_bigdown(c.forbid_green_bigdown)
        if c.forbid_far_from_high is not None:
            mask = mask & ~S.forbid_far_from_high(c.forbid_far_from_high)
        if c.forbid_new_days is not None:
            mask = mask & ~S.forbid_new_stock(c.forbid_new_days)
        if c.forbid_high_elasticity:
            mask = mask & ~S.forbid_high_elasticity()
        if c.shallow_pullback is not None:
            mask = mask & (pl.col("dist_from_high_20d") >= c.shallow_pullback)
        if c.max_turnover is not None:
            mask = mask & (pl.col("turnover_rate") <= c.max_turnover)
        return mask

    def on_day(self, context: BacktestContext) -> List[Order]:
        c = self.config
        orders: List[Order] = []
        pf = context.portfolio
        t = context.trade_date

        # ---- 0) 增量消化已成交平仓(引擎在 T+1 开盘撮合后落入 closed_trades)：
        #        更新冷却(亏损卖出) + 周实现亏损(次周减半用) + 清理已平仓的峰值状态 ----
        self._consume_closed_trades(pf)

        # 引擎当日价格(前复权,与 Portfolio.buy_price 同锚)
        ms = context.market_slice
        close_lookup = dict(zip(ms["ts_code"].to_list(), ms["close"].to_list()))
        low_lookup = dict(zip(ms["ts_code"].to_list(), ms["low"].to_list()))

        # ---- 1) 退出决策(对每个可卖持仓) ----
        selling: set = set()
        for ts_code, pos in list(pf.positions.items()):
            cur = close_lookup.get(ts_code)
            if cur is not None:
                self._peak_close[ts_code] = max(self._peak_close.get(ts_code, pos.buy_price), cur)
            if not pf.can_sell(ts_code, t):
                continue  # T+1 未满，今日不可卖
            reason = self._exit_reason(ts_code, pos, t, close_lookup, low_lookup)
            if reason:
                orders.append(Order(ts_code=ts_code, side="sell", shares=pos.shares, reason=reason))
                selling.add(ts_code)

        # ---- 2) 买入决策(填补空位，纪律约束) ----
        held_after = set(pf.positions.keys()) - selling
        open_slots = c.max_positions - len(held_after)
        if open_slots <= 0:
            return orders

        # 敞口预算(已持仓市值 + 本轮拟买；卖出的今日尚未成交，保守按仍持有算敞口)
        cur_exposure = sum(
            pos.shares * close_lookup.get(code, pos.buy_price) for code, pos in pf.positions.items()
        )
        exposure_budget = c.max_exposure_frac * self.initial_cash - cur_exposure
        if exposure_budget <= 0:
            return orders

        day_slice = self._by_date.get(t)
        if day_slice is None or day_slice.is_empty():
            return orders
        # 排除已持仓 / 冷却中
        blocked = set(pf.positions.keys())
        cands = day_slice.filter(~pl.col("ts_code").is_in(list(blocked)))
        if self._cooldown_until:
            active_cd = [code for code, until in self._cooldown_until.items() if until >= t]
            if active_cd:
                cands = cands.filter(~pl.col("ts_code").is_in(active_cd))
        if cands.is_empty():
            return orders
        cands = cands.sort(c.rank_by, descending=c.rank_desc, nulls_last=True)
        picks = cands["ts_code"].to_list()[:open_slots]

        single_cap = self._effective_single_cap(t)
        for code in picks:
            budget = min(single_cap, exposure_budget)
            if budget < 100 * (close_lookup.get(code) or 1e9):  # 不足一手直接跳过
                continue
            orders.append(Order(ts_code=code, side="buy", target_value=budget, reason="母战法建仓"))
            exposure_budget -= budget
            if exposure_budget <= 0:
                break
        return orders

    def _exit_reason(self, ts_code, pos, t, close_lookup, low_lookup) -> Optional[str]:
        c = self.config
        cur = close_lookup.get(ts_code)
        low = low_lookup.get(ts_code)
        # 止损(收盘或最低破位)
        if c.stop_pct is not None:
            stop_price = pos.buy_price * (1 - c.stop_pct)
            if (cur is not None and cur <= stop_price) or (low is not None and low <= stop_price):
                return f"止损(-{c.stop_pct:.0%})"
        # 回落止盈
        if c.take_profit_retrace is not None and cur is not None:
            peak = self._peak_close.get(ts_code, pos.buy_price)
            if peak > 0 and cur <= peak * (1 - c.take_profit_retrace):
                return f"回落止盈(-{c.take_profit_retrace:.0%})"
        # 时间退出
        held = len(trading_days_between(pos.buy_date, t))
        if held >= c.max_hold_days:
            return f"时间退出({held}日)"
        return None

    def _consume_closed_trades(self, pf) -> None:
        """增量扫描新平仓：亏损→设冷却；累计到 ISO 周亏损；清理峰值状态。"""
        from neckline.calendar import next_trading_day

        closed = pf.closed_trades
        for i in range(self._processed_closed, len(closed)):
            ct = closed[i]
            self._peak_close.pop(ct.ts_code, None)
            iso = ct.sell_date.isocalendar()
            key = (iso[0], iso[1])
            self._week_loss[key] = self._week_loss.get(key, 0.0) + min(ct.pnl, 0.0)
            if ct.pnl < 0 and self.config.cooldown_days > 0:
                cd = ct.sell_date
                for _ in range(self.config.cooldown_days):
                    cd = next_trading_day(cd)
                self._cooldown_until[ct.ts_code] = cd
        self._processed_closed = len(closed)

    def _effective_single_cap(self, t: date) -> float:
        c = self.config
        if not c.week_halving:
            return c.single_cap
        # 上一 ISO 周实现亏损 ≥ 阈值×初始资金 → 本周单笔减半（P10 挂起项验证）。
        # 上一 ISO 周 = t 往前 7 天所在的 ISO 周（直接 iso[1]-1 会在年初 week 1→0 出界，
        # 减 7 天始终落在紧邻的上一 ISO 周，跨年也正确）。
        from datetime import timedelta

        prev = (t - timedelta(days=7)).isocalendar()
        prev_loss = self._week_loss.get((prev[0], prev[1]), 0.0)
        if prev_loss <= -c.week_halving_threshold * self.initial_cash:
            return c.single_cap * 0.5
        return c.single_cap


__all__ = ["MomentumConfig", "MomentumStrategy"]
