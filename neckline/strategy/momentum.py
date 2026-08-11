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

⚠ **`loss_warning_pct` / `loss_warning_action`(V2.3.2-⑤)不是退出机制** —— 它们是
「−5% 对外触发什么」的语义声明(K8.md §十九),本引擎与 `eval/exit_sim.py` 的判分链
**一行都不读**,回测行为与它们无关。

仓位纪律（1.8/§2.1）：单笔 ≤ single_cap、最多 max_positions 只、总敞口 ≤
max_exposure_frac×初始资金。次周单笔减半（P10 挂起项）作可选开关，供验证性回测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional

import polars as pl

from neckline.backtest.broker import Broker
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
    # —— K2 研究扩展（§五B B4.0/B5.1/B5.3；一律默认关闭 = 与 K1 逐位相同，禁改上面既有字段语义）——
    require_mainline_member: bool = False          # B4:True 时 AND 面板的 is_mainline_member 列
    take_profit_fixed: Optional[float] = None      # B5:固定止盈 +X%(如 0.15;None=不启用,K1 回落止盈不变)
    high_elasticity_half: bool = False             # B5.3:True 时高弹票(创科北)单笔减半参与(而非黑名单剔除)
                                                   # 仅在 forbid_high_elasticity=False 时有意义;默认 False=不改 K1 sizing
    # —— K3 研究扩展(§五C B2 超跌买点;一律默认 None/off = 与 K1 逐位相同,禁改上面既有字段语义)——
    # 仅当 buypoint="oversold" 时被引用;默认 buypoint="pullback" 路径绝不触及下列字段/K3 列。
    oversold_depth_col: Optional[str] = None       # ret_1d|ret_5d|ret_10d|ret_20d|ret_5d_pct
    oversold_depth_max: Optional[float] = None     # 深度阈值(≤触发)
    oversold_trend: Optional[str] = None           # up|down|mid(趋势背景;None=不分趋势)
    oversold_pullback_max: Optional[float] = None  # dist_from_high_20d ≤(回撤门槛,臂③用)
    oversold_confirm: Optional[str] = None         # reclaim_ma5|reclaim_ma10|stabilize(启动确认,臂③)
    oversold_confirm_vol: Optional[float] = None   # 放量确认倍数(收复类)
    oversold_vol_max: Optional[float] = None       # 缩量上限(stabilize 用)
    # —— 排序（选 top-N 填仓；近零 alpha 下影响小，默认取最浅回调=最贴前高）——
    rank_by: str = "dist_from_high_20d"
    rank_desc: bool = True
    # —— 退出 ——
    stop_pct: Optional[float] = 0.05               # -5% 止损（None=不设止损）
    take_profit_retrace: Optional[float] = None    # 回落止盈阈值（None=不设）
    # V2.2-⑤ / §3.11-E **唯一一处类型放宽**:`int` → `Optional[int]`,`None` = **不设时间退出**
    # (与上一行 `stop_pct: Optional[float]` 的 `None` 语义同构)。⛔ **否决用哨兵位 9999**
    # ——哨兵位是"看不出来"的病。**默认值仍是 3、一字未动** → 旧 config 加载吃默认,K1 回测
    # 逐位不变(护栏 `tests/test_v13_exit_guardrail.py`;⚠ 六年真回测那一层已随策略档案迁出
    # 本仓、恒 skip,见 §七 P4-54,故逻辑层护栏是本仓唯一还跑得动的那道)。
    max_hold_days: Optional[int] = 3               # 时间退出（交易日）；v1.3 = 非浮盈单时间退出档；None=不设
    cooldown_days: int = 0                         # 同票亏损后冷却
    # —— v1.3 退出规则改革(§五 v1.3-①;一律默认 None/False = 与 K1 逐位相同,禁改上面字段语义)——
    # 默认值铁律:K1/K2/K3/v1.2 落库 config 均无这两字段,加载吃默认 → 时间退出仍在
    # max_hold_days 无条件触发 → K1 回测逐位不变(护栏 tests/test_v13_exit_guardrail)。
    # 新分支只在 time_exit_only_if_unprofitable=True 且 max_hold_days_profit 非空时进入。
    max_hold_days_profit: Optional[int] = None     # 浮盈单硬上限(交易日);None=不启用浮盈豁免=K1 行为
    time_exit_only_if_unprofitable: bool = False   # 时间退出仅对非浮盈单;False=无差别时间退出=K1 行为
    # —— V2.3.2-⑤ 退出字段语义换血(K8.md §十九;`v2.3-k8` 起)————————————————————
    # 🔴 **这两个字段是「对外语义」,回测/判分链一行都不读它们**:`_exit_reason` /
    # `exit_sim.py` 继续只认 `stop_pct`(那是**回测口径**)。它们回答的是另一个问题 ——
    # 「−5% 触发的是什么」:`loss_warning_action="review"` = **亏损警戒 + 由用户完成离场
    # 决策**,⛔ 系统**不得**据此触发任何自动卖出(K8.md §十三 逐字)。
    # ⚠ **默认 `None` = 该章程没有声明过这个语义**(⛔ 不是"声明为强制条件单"):老 config
    # 加载吃默认 → K1 六年回测与 `stop_is_advisory` 的老行为**逐位不变**(护栏
    # `tests/test_v13_exit_guardrail.py` + `brain.STOP_ADVISORY_CHARTERS` 白名单回退)。
    # ⛔ `stop_pct` 的字段名 / 默认值 `0.05` / 单一源地位**一字不动** —— 本版改的是它
    # **触发什么**,不是把 0.05 搬家,更不是在这里抄第二份(§五 ⑤-B / 〇b 红线 7)。
    loss_warning_pct: Optional[float] = None       # 亏损警戒线(K8 §十九 = 0.05);None=该章程未声明
    loss_warning_action: Optional[str] = None      # 警戒后的动作;"review"=交用户决策,⛔ 永不自动卖出
    # —— 仓位纪律 ——
    single_cap: float = 20000.0
    max_positions: int = 5
    max_exposure_frac: float = 0.60
    week_halving: bool = False                      # P10 挂起项开关：单周亏损后次周单笔减半
    week_halving_threshold: float = 0.05            # 挂起项定义口径：单周实现亏损 ≥ 本值×初始资金触发
                                                    # （5%=挂起项「次周单笔减半」；区别于 §2.1 已采纳的 2% 强制复盘线）


def build_entry_mask(config: MomentumConfig) -> pl.Expr:
    """选股域 + 强势 + 买点 + 禁买过滤 → 单一布尔表达式。

    模块级纯函数(阶段 0/1 原是 `MomentumStrategy._build_entry_mask` 实例方法,阶段 2
    提炼出来——§2.6/§3.8「同码三跑道」铁律:报告管线(`neckline/report/candidates.py`)
    与回测策略必须调**同一份**函数选出同一批候选,不得在报告管线里另写一份信号逻辑。
    提炼只是把 `self.config` 换成显式参数,行为完全不变。
    """
    from neckline.research.panel import base_universe_expr

    c = config
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
    elif c.buypoint == "oversold":
        # K3 超跌买点(§五C B2)。仅此分支引用 buy_oversold 及其 K3 特征列;
        # 默认 pullback 路径不进此分支,K1 逐位不变(护栏单测 test_k3_oversold_guardrail)。
        mask = mask & S.buy_oversold(
            depth_col=c.oversold_depth_col, depth_max=c.oversold_depth_max,
            trend=c.oversold_trend, pullback_max=c.oversold_pullback_max,
            confirm=c.oversold_confirm, confirm_vol=c.oversold_confirm_vol,
            vol_max=c.oversold_vol_max,
        )
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
    # K2 B4:主线成员 mask(默认 False = 不引用该列,与 K1 逐位相同;True 才 AND)
    if c.require_mainline_member:
        mask = mask & pl.col("is_mainline_member")
    return mask


class MomentumStrategy(Strategy):
    def __init__(
        self,
        panel: pl.DataFrame,
        config: MomentumConfig,
        initial_cash: float = 120000.0,
        buy_gate: Optional[set] = None,
    ) -> None:
        self.config = config
        self.initial_cash = initial_cash
        # 市场过滤器闸门(P1 争议项):一组「允许开新仓」的交易日。为 None 时不设闸门
        # (全天可开仓);提供时,不在集合里的交易日只做退出、不开新仓。市场状态由外部
        # (同一 signals.market_state_labels)算好注入——保持 §2.6 同码,不把指数状态塞进
        # 个股面板。退出决策不受闸门影响(风控优先于择时)。
        self._buy_gate = buy_gate
        # 预筛：选股域 + 强势 + 买点 + 禁买过滤 一次性算成布尔，按日切片(快)
        self._entry_mask = build_entry_mask(self.config)
        filtered = panel.filter(self._entry_mask)
        self._by_date: Dict[date, pl.DataFrame] = {}
        for (d,), sub in filtered.group_by(["trade_date"]):
            self._by_date[d] = sub
        # 状态
        self._peak_close: Dict[str, float] = {}     # 持仓票自建仓以来收盘峰值
        self._cooldown_until: Dict[str, date] = {}   # 冷却到期日(含)
        self._week_loss: Dict[tuple, float] = {}     # (iso_year,iso_week) -> 已实现亏损累计
        self._processed_closed = 0                   # 已处理的 closed_trades 数(增量扫描)
        # v1.3 浮盈豁免时间退出:持仓票在 D5 判净浮盈 >0 后一次性豁免续命,eff_max 从
        # max_hold_days 抬到 max_hold_days_profit(硬上限);默认档不写此表(逐位不变)。
        self._eff_max: Dict[str, int] = {}
        # 卖出费估算用引擎既有 fee 模型(默认 Broker,与 research/h9_exit_reform.py §2
        # 对拍口径一致)——**回测侧走引擎精确双边 fee,不走 neckline/fees.py 实盘估算**。
        self._fee_broker = Broker()

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
        # 市场过滤器闸门(P1):不在允许日集合内 → 今日只退出不开新仓。
        if self._buy_gate is not None and t not in self._buy_gate:
            return orders
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
        # B5.3 高弹票单笔减半(默认 False 不改 K1;需要板块信息时才建查找表)
        he_boards = set(S.HIGH_ELASTICITY_BOARDS) if c.high_elasticity_half else set()
        board_lookup = (
            dict(zip(cands["ts_code"].to_list(), cands["board"].to_list()))
            if c.high_elasticity_half and "board" in cands.columns else {}
        )
        for code in picks:
            cap = single_cap
            if he_boards and board_lookup.get(code) in he_boards:
                cap = single_cap * 0.5
            budget = min(cap, exposure_budget)
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
        # 固定止盈(B5:+X% 落袋;默认 None=不启用,K1 回落止盈行为不变)
        if c.take_profit_fixed is not None and cur is not None:
            if cur >= pos.buy_price * (1 + c.take_profit_fixed):
                return f"固定止盈(+{c.take_profit_fixed:.0%})"
        # 回落止盈
        if c.take_profit_retrace is not None and cur is not None:
            peak = self._peak_close.get(ts_code, pos.buy_price)
            if peak > 0 and cur <= peak * (1 - c.take_profit_retrace):
                return f"回落止盈(-{c.take_profit_retrace:.0%})"
        # 时间退出(两档,见 _time_exit_reason)
        held = len(trading_days_between(pos.buy_date, t))
        return self._time_exit_reason(ts_code, pos, held, cur)

    def _time_exit_reason(self, ts_code, pos, held: int, cur) -> Optional[str]:
        """时间退出两档。

        **默认档(K1 逐位不变)**:`time_exit_only_if_unprofitable=False` 或未设浮盈硬
        上限 → 与 v1.3 前完全相同——held>=max_hold_days 无条件时间退出。新字段默认
        None/False 恒走此分支,`_eff_max`/`_fee_broker` 永不被触碰(护栏单测锁死)。

        **v1.3 条件档**(`time_exit_only_if_unprofitable=True` 且 `max_hold_days_profit`
        非空):镜像 `research/h9_exit_reform.py::_sim_one` 的 V1——止损/回落已在
        `_exit_reason` 前置分支判过,此处只管时间退出。持仓恰达 D5(held==max_hold_days)
        时算收盘净浮盈:>0 → 一次性豁免续命(`_eff_max` 抬到硬上限,此后交回落/止损管
        到 max_hold_days_profit 无条件退)、≤0(或停牌无价)→ 照旧时间退出。

        **V2.2-⑤ 第三档:`max_hold_days is None` = 章程不设时间退出**(K8 §十三「上涨效率
        下降 → 保留主观换股权」,`v2.2-k8` 起)。与 `stop_pct is None` 同构:引擎**永不**因
        持有天数卖出,`_eff_max` 也永不被写。⛔ 不许在这里拿一个默认天数顶上。
        """
        c = self.config
        # —— 无时间退出条款(V2.2-⑤ / §3.11-E)——恒不触发,先于两档判定 ——
        if c.max_hold_days is None:
            return None
        # —— 默认档:K1 无条件时间退出 ——
        if not (c.time_exit_only_if_unprofitable and c.max_hold_days_profit is not None):
            if held >= c.max_hold_days:
                return f"时间退出({held}日)"
            return None
        # —— v1.3 条件档 ——
        if held < c.max_hold_days:
            return None
        eff_max = self._eff_max.get(ts_code, c.max_hold_days)
        if eff_max == c.max_hold_days:            # 尚未豁免
            # 仅在恰达 D5 判净浮盈(与 _sim_one 的 held==base_hold 判定同拍);未豁免则退出
            if held == c.max_hold_days and cur is not None and self._d5_net_float(pos, cur) > 0:
                self._eff_max[ts_code] = c.max_hold_days_profit   # 一次性豁免续命
                return None
            return f"时间退出({held}日)"
        # 已豁免:硬上限无条件退出
        if held >= c.max_hold_days_profit:
            return f"时间退出(硬上限{held}日)"
        return None

    def _d5_net_float(self, pos, cur: float) -> float:
        """D5 收盘净浮盈(扣双边费):close×shares − buy×shares − buy_fees − 估算卖出费。
        卖出费用引擎既有 fee 模型(`Broker._sell_fees`,含最低佣金/印花税/过户费),与
        `research/h9_exit_reform.py` §2 对拍口径逐位一致。回测侧不走 `neckline/fees.py`。"""
        sell_fee = self._fee_broker._sell_fees(pos.shares * cur)
        return pos.shares * (cur - pos.buy_price) - pos.buy_fees - sell_fee

    def _consume_closed_trades(self, pf) -> None:
        """增量扫描新平仓：亏损→设冷却；累计到 ISO 周亏损；清理峰值状态。"""
        from neckline.calendar import next_trading_day

        closed = pf.closed_trades
        for i in range(self._processed_closed, len(closed)):
            ct = closed[i]
            self._peak_close.pop(ct.ts_code, None)
            self._eff_max.pop(ct.ts_code, None)   # 平仓即清豁免态,同票再买从头判 D5
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


__all__ = ["MomentumConfig", "MomentumStrategy", "build_entry_mask"]
