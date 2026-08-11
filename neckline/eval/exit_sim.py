"""**判分引擎唯一源**(plan §五 V2-⑨-D)。一笔「D0 选中 → D+1 开盘买入 → 按纪律
退出」的模拟成交与判分,全项目只有这一份实现。

**为什么会有这次下沉**(⑨-D 原文):架构稿要求「判分复用考官线成交层 + h9 退出
模拟器,判分引擎唯一源不另写」,但现实是它住在 `research/h9_exit_reform.py::_sim_one`
里,而 **`research/` 反向 import `neckline/`** —— 生产不能倒过来 import 研究件。
故把 `_sim_one` 及其 `SLIP` / `BROKER`(以及 `_sim_one` 的返回类型 `ReTrade`)
**原地搬到本模块**,`research/h9_exit_reform.py` / `drill.py` / `exam.py` 三处改为
import 它。方向仍是 research → neckline。

**搬迁纪律(逐位对拍锁死)**:`ReTrade` / `_sim_one` 的函数体、默认参数、注释
**逐字未改**;`tests/test_eval_exit_sim.py::TestFrozenPairing` 内嵌一份搬迁前的
**冻结副本**,在数千条随机造数上逐字段比对(退出日 / 退出价 / 原因 / 豁免 / 持有
天数 / pnl / pnl_pct 全等)。`scripts/smoke_eval_exit_sim.py` 用真实 `k3_panel`
的真单再对一遍。**这条不过就不许继续**(⑨ 验收第一条)。

⚠ **`_sim_one` 的默认参数是研究侧的历史遗留,生产侧一律不许用**
(`base_hold=5, retrace=0.05, stop=0.05` 是 K1 口径的旧默认)。生产调用方必须
`**score_kw_from_charter()` 显式传全五项 —— 纪律参数的单一源是现役章程
(`brain.active_config()`),不是本模块的字面量(§铁律)。默认值保留只为**搬迁
逐位等价**,`tests/test_eval_exit_sim.py` 有守门单测断言生产侧调用点全部显式传参。

**两层的分工**(别混用):

    · **成交层**(`fill_and_score`,考官线 §九)—— 回答「买不买得进、按什么价买」:
      T+1 竞价单一价成交(**无滑点**,与研究侧 `drill._score_pick` 的唯一口径差),
      可选**最高追价上限**(卡上冻结的 `max_chase`,开盘超过就判未成交)。
    · **退出层**(`_sim_one`)—— 回答「按纪律什么时候卖、卖多少钱」:止损 → 回落
      止盈 → 时间退出,决策日 close/low 判定,T+1 开盘撮合含滑点,跌停卖不出 /
      停牌顺延。

**语义红线**:本模块产出的是**回看审计口径的模拟成交**,⛔ 不是收益预测、不是
建议、不进任何在线判据(⑨-C2:两条对照臂与判分结果只进周报与策略线迭代输入)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from neckline.backtest.broker import Broker
from neckline.backtest.portfolio import ClosedTrade
from neckline.calendar import trading_days_between

logger = logging.getLogger(__name__)

BROKER = Broker()
SLIP = BROKER.slippage_bp / 10000.0

# 浮点比较容差(工程不变量,同 `verification_rules.EPS` / `sentinel/holding.py::_EPS`
# 先例)。用在「开盘价是不是超过了最高追价」这一处比较上,非策略参数。
EPS = 1e-9


# ══════════════════════════════════════════════════════════════════════════
# 退出层 —— 交易重放模拟器(**自 `research/h9_exit_reform.py` 逐字搬入,勿改**)
# ══════════════════════════════════════════════════════════════════════════
#
# 复刻引擎逐日退出口径:止损→回落止盈→时间退出;决策日 close/low 判定;T+1 开盘
# 撮合含滑点;跌停卖不出/停牌顺延。V1/V2/V3 只改退出规则,入场集合固定 = K1 实际
# 1288 单——消融口径 clean:同一批入场、不同退出。

@dataclass
class ReTrade:
    """重放后的一次回合(入场沿用原单,退出由模拟器重derive)。"""
    src: ClosedTrade
    sell_date: date
    sell_price: float
    reason: str          # stop | retrace | time | end
    exempt: bool         # V1/V3:第5日净浮盈豁免续命
    held_sessions: int   # 含买卖两端

    @property
    def ts_code(self) -> str:
        return self.src.ts_code

    @property
    def buy_date(self) -> date:
        return self.src.buy_date

    @property
    def shares(self) -> int:
        return self.src.shares

    @property
    def sell_fees(self) -> float:
        return BROKER._sell_fees(self.src.shares * self.sell_price)

    @property
    def pnl(self) -> float:
        return self.src.shares * (self.sell_price - self.src.buy_price) - self.src.buy_fees - self.sell_fees

    @property
    def cost_basis(self) -> float:
        return self.src.shares * self.src.buy_price + self.src.buy_fees

    @property
    def pnl_pct(self) -> float:
        cb = self.cost_basis
        return self.pnl / cb if cb else 0.0


def _sim_one(t: ClosedTrade, pm: dict, ld: set, cal: list, cal_idx: dict, *,
             base_hold: int = 5, retrace: float = 0.05, stop: float = 0.05,
             v1: bool = False, v2: bool = False, v2_gate: float = 0.08,
             v2_wide: float = 0.08, hard_cap: int = 15) -> Optional[ReTrade]:
    p = pm.get(t.ts_code)
    if p is None or t.buy_date not in cal_idx:
        return None
    k0 = cal_idx[t.buy_date]
    buy_price = t.buy_price
    peak = buy_price
    eff_max = base_hold
    exempt = False
    pidx = p["idx"]
    for k in range(k0, len(cal)):
        d = cal[k]
        j = pidx.get(d)
        cl = p["c"][j] if j is not None else None
        lo = p["l"][j] if j is not None else None
        if cl is not None and cl > peak:
            peak = cl
        if d == t.buy_date:
            continue                         # 买入当日 T+1 未满,不可卖
        held = k - k0 + 1                     # == trading_days_between(buy_date, d)(全历日历连续)
        band = retrace
        if v2 and peak >= buy_price * (1 + v2_gate):
            band = v2_wide                    # V2:浮盈达 +8% 后放宽回落带
        reason: Optional[str] = None
        if j is not None:                     # 有数据才判止损/回落(停牌日只能时间退出,同引擎)
            stop_price = buy_price * (1 - stop)
            if (cl is not None and cl <= stop_price) or (lo is not None and lo <= stop_price):
                reason = "stop"
            elif peak > 0 and cl is not None and cl <= peak * (1 - band):
                reason = "retrace"
        if reason is None and held >= base_hold:
            if v1 and held == base_hold and not exempt and eff_max == base_hold:
                if j is not None:             # 第5日净浮盈 >0(扣双边费)→ 豁免时间退出
                    sell_fee_est = BROKER._sell_fees(t.shares * cl)
                    net_float = t.shares * (cl - buy_price) - t.buy_fees - sell_fee_est
                    if net_float > 0:
                        exempt = True
                        eff_max = hard_cap
                if not exempt:
                    reason = "time"
            elif held >= eff_max:
                reason = "time"
        if reason:
            nk = k + 1
            if nk >= len(cal):                # 数据末端无法 T+1 撮合 → 末日收盘强平(记 end)
                px = round((cl if cl is not None else buy_price) * (1 - SLIP), 2)
                return ReTrade(t, d, px, "end", exempt, len(trading_days_between(t.buy_date, d)))
            nd = cal[nk]
            nj = pidx.get(nd)
            if nj is not None and (t.ts_code, nd) not in ld:
                px = round(p["o"][nj] * (1 - SLIP), 2)
                return ReTrade(t, nd, px, reason, exempt, len(trading_days_between(t.buy_date, nd)))
            continue                          # 跌停卖不出/停牌 → 顺延,次日重判(同引擎)
    return None


def _sim_one_h(t: ClosedTrade, pm: dict, ld: set, cal: list, cal_idx: dict, *,
               base_hold: Optional[int] = None, retrace: Optional[float] = None,
               stop: float = 0.05, v1: bool = False, v2: bool = False,
               v2_gate: float = 0.08, v2_wide: float = 0.08,
               hard_cap: Optional[int] = None,
               horizon: Optional[int] = None) -> Optional[ReTrade]:
    """**判分模拟器 · 地平线版**(§七 **P0-56**,2026-08-11 新增)。

    🔴 **为什么是一个新函数,而不是就地改 `_sim_one`**:`_sim_one` 是**审计基准**,
    被 `tests/test_eval_exit_sim.py::TestFrozenPairing::test_source_is_byte_identical`
    **逐字冻结**,那条守门的原话是「判分口径是审计基准,改它必须走**新版本 + 重新对拍**,
    ⛔ 不许就地改」。本函数就是那个「新版本」;**冻结那份一个字节都没动**,
    K4 / K7 时代历史样本的判分口径因此可证明零改动。

    **与 `_sim_one` 的三处差异(全部只在新章程口径下才生效)**:

    1. `base_hold=None` → **永不因持有天数退出**(章程已按 §2.1 第 2 条退役时间退出,
       与 `momentum.py::_time_exit` 第三档同口径,⛔ 不拿默认天数顶上);
    2. `retrace=None` → **永不因回落退出**(回落止盈同批退役);
    3. `horizon` → **评分地平线**(`SCORING_HORIZON_DAYS`,用户 2026-08-11 拍板 15 个
       交易日):两档退役后只剩 −5% 止损,一笔没跌到止损的单子会一直持有、判分没有终点,
       故由它收口。触发时 `reason` 记 **`horizon`**,⛔ **不记 `time`** —— `time` 是
       「纪律说该走」、`horizon` 是「测量窗口到头了」,把后者写成前者等于在成绩单里
       把用户已经删掉的时间退出又讲了一遍。

    ⚠ **`horizon` 排在止损 / 回落 / 时间退出之后判**:第 15 天同时踩到止损与地平线时,
    记的必须是 `stop`(纪律真的触发了),⛔ 不能被测量窗口盖掉。

    ✅ **重新对拍**:`horizon=None` 时本函数与 `_sim_one` **逐字段全等**,由
    `TestHorizonVersionPairing` 在随机造数上锁死 —— 新版本没有偷偷改动老口径。
    """
    p = pm.get(t.ts_code)
    if p is None or t.buy_date not in cal_idx:
        return None
    k0 = cal_idx[t.buy_date]
    buy_price = t.buy_price
    peak = buy_price
    eff_max = base_hold
    exempt = False
    pidx = p["idx"]
    for k in range(k0, len(cal)):
        d = cal[k]
        j = pidx.get(d)
        cl = p["c"][j] if j is not None else None
        lo = p["l"][j] if j is not None else None
        if cl is not None and cl > peak:
            peak = cl
        if d == t.buy_date:
            continue                         # 买入当日 T+1 未满,不可卖
        held = k - k0 + 1
        band = retrace
        if v2 and peak >= buy_price * (1 + v2_gate):
            band = v2_wide
        reason: Optional[str] = None
        if j is not None:
            stop_price = buy_price * (1 - stop)
            if (cl is not None and cl <= stop_price) or (lo is not None and lo <= stop_price):
                reason = "stop"
            elif band is not None and peak > 0 and cl is not None and cl <= peak * (1 - band):
                reason = "retrace"
        if reason is None and base_hold is not None and held >= base_hold:
            if v1 and held == base_hold and not exempt and eff_max == base_hold:
                if j is not None:
                    sell_fee_est = BROKER._sell_fees(t.shares * cl)
                    net_float = t.shares * (cl - buy_price) - t.buy_fees - sell_fee_est
                    if net_float > 0:
                        exempt = True
                        eff_max = hard_cap
                if not exempt:
                    reason = "time"
            elif held >= eff_max:
                reason = "time"
        if reason is None and horizon is not None and held >= horizon:
            reason = "horizon"
        if reason:
            nk = k + 1
            if nk >= len(cal):
                px = round((cl if cl is not None else buy_price) * (1 - SLIP), 2)
                return ReTrade(t, d, px, "end", exempt, len(trading_days_between(t.buy_date, d)))
            nd = cal[nk]
            nj = pidx.get(nd)
            if nj is not None and (t.ts_code, nd) not in ld:
                px = round(p["o"][nj] * (1 - SLIP), 2)
                return ReTrade(t, nd, px, reason, exempt, len(trading_days_between(t.buy_date, nd)))
            continue
    return None


def _pick_sim(score_kw: Dict[str, Any]):
    """按 kw 的形状选判分实现 —— **唯一一处分派**(⛔ 调用方别各写一遍 if)。

    · 章程**有**时间退出(`base_hold` 非 None、无 `horizon`)→ **冻结版 `_sim_one`**,
      历史样本的判分口径因此**逐字节不变**(K4 / K7 时代的成绩单仍可比)。
    · 章程**没有**时间退出(`v2.2-k8` 起)→ **地平线版 `_sim_one_h`**。

    🔴 判据取 `horizon` 在不在,**不取 `base_hold is None`**:`score_kw_from_charter`
    只在两档退役时才挂 `horizon`,两者本应同时成立;但万一有人手捏一份
    `base_hold=None` 却没给 `horizon` 的 kw,落到冻结版会当场 `TypeError`
    (`held >= None`)——**那正是我们要的**:配错就炸,⛔ 别悄悄兜住。
    """
    return _sim_one_h if score_kw.get("horizon") is not None else _sim_one


#: 公开别名 —— 新代码用 `sim_one`,`_sim_one` 保留是为了 research 三处的既有写法
#: (`h9._sim_one(...)`)一字不改地继续工作。两者是**同一个函数对象**。
sim_one = _sim_one


# ══════════════════════════════════════════════════════════════════════════
# 纪律参数:唯一源 = 现役章程(⛔ 不许在本模块或调用方写死 0.05 / 0.08)
# ══════════════════════════════════════════════════════════════════════════

#: `MomentumConfig` 字段 → `_sim_one` 关键字参数的映射(**唯一一处**;这两套命名
#: 是历史形成的,别在别的地方再翻译一遍)。`v2`/`v2_gate`/`v2_wide` 是 h9 研究里
#: 被**否决**的分段回落变体,现役章程没有对应字段 → 生产侧永远不启用,故不在表内。
_CHARTER_TO_SIM_KW: Tuple[Tuple[str, str], ...] = (
    ("max_hold_days", "base_hold"),
    ("take_profit_retrace", "retrace"),
    ("stop_pct", "stop"),
)

#: 🔴 **评分地平线(交易日)—— 2026-08-11 用户拍板 `15`,§七 P0-56 的最后一件**。
#:
#: **它是什么**:回看审计时「一个篮子的成绩算到第几天为止」。⛔ **它不是交易纪律**,
#: 不改变任何人的操作,也**不下发给客户端**、不进哨兵、不进任何在线判据。
#:
#: **为什么需要它**:`v2.2-k8` 把时间退出(`max_hold_days=5`)与浮盈硬上限
#: (`max_hold_days_profit=15`)双双退役后,判分只剩 −5% 止损这一条退出 —— 一笔没跌到
#: 止损的单子会**一直持有**,判分没有终点。此前这个终点是那两个字段**白送**的。
#:
#: **为什么是 15**:那正是退役前的**实际有效地平线**(`hard_cap=15`)。取它,新样本与
#: `K4-pack` / `K7-pack` 时代的历史样本**仍在同一把尺子上**;换任何别的数,分层成绩单
#: 里的历史对照全部作废(⚠ 这是选它的**主要理由**,不是"15 有什么道理")。
#:
#: ⛔ **改这个数 = 换掉成绩单的尺子** —— 必须像换包一样走用户拍板,并在变更日志里写明
#: 「自某日起的样本与之前不可直接比较」。⛔ 别在别处抄一份字面量。
SCORING_HORIZON_DAYS = 15


def score_kw_from_charter(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """把**现役章程**的纪律参数翻成 `_sim_one` 的关键字参数(生产侧判分的唯一入口)。

    读的是 `brain.active_config()`(§铁律「纪律参数全读现役
    `brain.get_active().rule["config"]`」),⛔ 本函数不含任何纪律数字字面量。

    映射(与 `strategy/momentum.py` 的两档条件退出同口径)::

        max_hold_days                → base_hold
        take_profit_retrace          → retrace
        stop_pct                     → stop
        time_exit_only_if_unprofitable → v1        (浮盈续命开关)
        max_hold_days_profit         → hard_cap    (浮盈单硬上限;未配置 → 退回 base_hold)

    **`max_hold_days` / `take_profit_retrace` 可以是 `None`**(§七 **P0-56**):`v2.2-k8` 起
    这两档已按 §2.1 第 2 条**刻意退役**,`None` = **没有这一档**,与 `momentum.py::_time_exit`
    的第三档、`precall.py::has_time_exit_clause` 同一口径。此时判分改由**评分地平线**
    (`SCORING_HORIZON_DAYS`)收口 —— ⛔ **绝不许把 5 / 0.08 写回 `strategy_versions`**
    「补全」它,那等于把用户明令退役的机械纪律静默复活。

    **`stop_pct` 仍是必需**:两档退役后,止损是判分仅存的纪律退出,没有它判分没有意义。

    **无现役章程 → `ValueError` fail loud**:判分没有纪律参数就没有意义,静默套一个
    默认值等于伪造审计口径(项目里「没有」与「没看」必须分得开)。
    """
    from neckline.strategy import brain

    cfg = brain.active_config(db_path=db_path)
    if not cfg:
        raise ValueError(
            "score_kw_from_charter:策略大脑无现役版本,判分参数没有单一源 —— "
            "拒绝套用默认值(伪造审计口径比算不出更糟)"
        )
    kw: Dict[str, Any] = {sim_key: cfg.get(cfg_key) for cfg_key, sim_key in _CHARTER_TO_SIM_KW}
    if kw["stop"] is None:
        raise ValueError(
            "score_kw_from_charter:现役章程没有 `stop_pct` —— 时间退出与回落止盈退役之后,"
            "止损是判分**仅存的**纪律退出,没有它这份判分不代表任何纪律口径"
        )
    kw["base_hold"] = None if kw["base_hold"] is None else int(kw["base_hold"])
    kw["retrace"] = None if kw["retrace"] is None else float(kw["retrace"])
    kw["stop"] = float(kw["stop"])
    v1 = bool(cfg.get("time_exit_only_if_unprofitable") or False)
    hard_cap = cfg.get("max_hold_days_profit")
    kw["v1"] = v1
    # 浮盈续命没开、或没配硬上限 → `hard_cap` 退回 `base_hold`,使 `_sim_one` 的
    # `eff_max` 分支与「无差别时间退出」逐位等价(K1 行为)。
    kw["hard_cap"] = int(hard_cap) if (v1 and hard_cap is not None) else kw["base_hold"]
    # 🔴 **只有在章程不设时间退出时才挂地平线**(P0-56 定案)。章程有时间退出的历史口径
    # **一个字节都不受影响** —— `horizon` 缺席 → `_sim_one` 的地平线分支永不进入,
    # K1 / v1.3 两档的逐位不变护栏因此原样成立(§3.11-E「放宽后必须跑逐位不变护栏」)。
    if kw["base_hold"] is None:
        kw["horizon"] = SCORING_HORIZON_DAYS
    return kw


def forward_span_days(kw: Dict[str, Any]) -> int:
    """判分要向前看几个交易日 —— **唯一一处**推导(⛔ 调用方别再各写一遍)。

    取值次序:`hard_cap`(浮盈硬上限)→ `base_hold`(时间退出档)→ `horizon`(评分地平线,
    章程不设时间退出时由 `score_kw_from_charter` 挂上)。**三者皆无才报错。**

    🔴 **⛔ 永不拿 `1` 顶上**(§七 P0-56)。原先三处调用方各写着
    `int(kw.get("hard_cap") or kw.get("base_hold") or 1)`,那个 `1` 是一个**静默哨兵位**:
    章程把两档退役之后,它会把前向窗口悄悄取成 **1 个交易日** —— 判分照跑、数字照出、
    报告照落盘,**看不出错**。§3.11-E 明文否决过哨兵位(原话「哨兵位是"看不出来"的病」);
    那条讲的是 `9999`,而 `1` 是同一个病。
    """
    for key in ("hard_cap", "base_hold", "horizon"):
        v = kw.get(key)
        if v is not None:
            return int(v)
    raise ValueError(
        "forward_span_days:`hard_cap` / `base_hold` / `horizon` 三者皆为 None —— 前向窗口"
        "没有来源(§七 P0-56)。⛔ 拒绝默认成 1 个交易日:那会让判分在一个几乎必然"
        "「还没走完」的窗口上出数,且看不出来。走 `score_kw_from_charter()` 拿 kw 就不会"
        "落到这里(它在章程无时间退出时会挂 `horizon=SCORING_HORIZON_DAYS`)。"
    )


def notional_from_charter(db_path: Optional[Path] = None) -> float:
    """判分建仓名义额 = 现役章程的**单笔上限** `single_cap`。

    ⚠ `pnl_pct` 对名义额**近乎不变**(只影响最低佣金 5 元与 100 股整手取整这两处
    的相对权重),取 `single_cap` 是为了让费用口径落在真实纪律的量级上,不是为了
    模拟真实仓位。无现役章程 / 缺字段 → `ValueError`(同上,不套默认值)。
    """
    from neckline.strategy import brain

    cfg = brain.active_config(db_path=db_path)
    cap = (cfg or {}).get("single_cap")
    if cap is None:
        raise ValueError("notional_from_charter:现役章程 config 缺 single_cap,判分名义额没有单一源")
    return float(cap)


# ══════════════════════════════════════════════════════════════════════════
# 成交层 —— 考官线 §九 竞价成交判定(`research/exam.py::_sim_entry` 的生产实现)
# ══════════════════════════════════════════════════════════════════════════

FILL_NOT_BUYABLE = "not_buyable"          # D0 面板已判次日一字 / 停牌
FILL_NO_T1 = "no_t1"                      # 数据末端,没有 T+1 可买
FILL_T1_SUSPENDED = "t1_suspended"        # T+1 当日无行情(停牌 / 数据缺口)
FILL_ABOVE_CEILING = "above_ceiling"      # 开盘价高于卡上冻结的最高追价 → 不追
FILL_UNRESOLVED = "unresolved"            # 退出没解出来:取不到价,**或前向窗口还没走完**
FILL_OK = "ok"


@dataclass(frozen=True)
class FillScore:
    """一次「D0 选中 → T+1 竞价买入 → 按纪律退出」的判分结果(**纯值对象**)。

    `filled=False` 时 `ret` 恒为 `0.0` —— 那不是「收益是零」,而是**这笔根本没成交**;
    读者必须靠 `filled` / `fill_code` 区分,⛔ 不许把未成交的 0 混进收益均值里
    (`metrics.py` 的聚合一律先按 `filled` 过滤,单测锁死)。
    """

    ts_code: str
    decision_date: date
    filled: bool
    fill_code: str
    fill_reason: str
    gap_pct: Optional[float] = None       # T+1 开盘 / D0 收盘 − 1(百分数,如 +2.31)
    buy_date: Optional[date] = None
    buy_price: Optional[float] = None
    shares: int = 0
    ret: float = 0.0                      # `pnl_pct`(扣双边费)
    hold: int = 0                         # 含买卖两端的持有交易日数
    exempt: bool = False
    exit_reason: Optional[str] = None     # stop | retrace | time | end
    exit_date: Optional[date] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "decision_date": self.decision_date.strftime("%Y%m%d"),
            "filled": bool(self.filled),
            "fill_code": self.fill_code,
            "fill_reason": self.fill_reason,
            "gap_pct": self.gap_pct,
            "buy_date": self.buy_date.strftime("%Y%m%d") if self.buy_date else None,
            "buy_price": self.buy_price,
            "shares": int(self.shares),
            "ret": float(self.ret),
            "hold": int(self.hold),
            "exempt": bool(self.exempt),
            "exit_reason": self.exit_reason,
            "exit_date": self.exit_date.strftime("%Y%m%d") if self.exit_date else None,
        }


_FILL_REASON_TEXT: Dict[str, str] = {
    FILL_NOT_BUYABLE: "买不进(次日一字 / 停牌)",
    FILL_NO_T1: "买不进(无 T+1,数据末端)",
    FILL_T1_SUSPENDED: "买不进(T+1 无行情)",
    FILL_ABOVE_CEILING: "未成交(开盘价高于卡上最高追价)",
    FILL_UNRESOLVED: "退出未解出(取不到价,或前向窗口还没走完)——⛔ 不进收益均值",
}


def fill_and_score(
    code: str,
    decision_date: date,
    *,
    buyable: bool,
    pm: dict,
    ld: set,
    cal: list,
    cal_idx: dict,
    score_kw: Dict[str, Any],
    notional: float,
    ceiling_price: Optional[float] = None,
) -> FillScore:
    """判一只票的**可交易收益**:D0 收盘后选中 → T+1 竞价单一价买入 → 走 `_sim_one`。

    · **入场价 = T+1 开盘价,无滑点**(竞价单一价成交;这是考官线 §九 与研究侧
      `drill._score_pick`〔开盘价 × (1+滑点)〕的唯一口径差,刻意保留)。
    · `ceiling_price`:卡上冻结的**最高追价**(绝对价,来自 `card_json.members[]
      .max_chase`)。开盘价高于它 → 判**未成交**,`ret=0` 且 `filled=False`
      —— 「追不进」与「买了亏 0」是两件事。`None` = 不设上限(基线口径)。
    · `score_kw`:**必填**,由 `score_kw_from_charter()` 产出。⛔ 不给默认值 ——
      给了默认就等于在本模块埋一份纪律参数(§铁律)。

    ⚠ **前向数据不足时不截断、不外推**:`_sim_one` 走到日历末端会以末日收盘强平并
    记 `reason="end"`,调用方(`metrics.py`)据此把该笔标为「尚未走完」,不混进
    已完成样本 —— 装作走完了是最不诚实的一种四舍五入。
    """
    reason_of = lambda c: _FILL_REASON_TEXT.get(c, c)  # noqa: E731

    if not buyable or code not in pm or decision_date not in cal_idx:
        return FillScore(code, decision_date, False, FILL_NOT_BUYABLE, reason_of(FILL_NOT_BUYABLE))
    k0 = cal_idx[decision_date]
    if k0 + 1 >= len(cal):
        return FillScore(code, decision_date, False, FILL_NO_T1, reason_of(FILL_NO_T1))
    t1 = cal[k0 + 1]
    pidx = pm[code]["idx"]
    if t1 not in pidx or decision_date not in pidx:
        return FillScore(code, decision_date, False, FILL_T1_SUSPENDED, reason_of(FILL_T1_SUSPENDED))

    buy_open = pm[code]["o"][pidx[t1]]
    c0 = pm[code]["c"][pidx[decision_date]]
    gap = round((buy_open / c0 - 1.0) * 100.0, 2) if c0 else None
    if ceiling_price is not None and buy_open > float(ceiling_price) + EPS:
        return FillScore(
            code, decision_date, False, FILL_ABOVE_CEILING,
            f"未成交:开盘 {buy_open:.2f} 高于最高追价 {float(ceiling_price):.2f}", gap_pct=gap,
        )

    buy_price = round(buy_open, 2)
    shares = int(float(notional) // buy_price // 100) * 100
    if shares < 100:
        shares = 100
    buy_fees = BROKER._buy_fees(shares * buy_price)
    t = ClosedTrade(ts_code=code, buy_date=t1, sell_date=t1, shares=shares,
                    buy_price=buy_price, sell_price=buy_price, buy_fees=buy_fees,
                    sell_fees=0.0, reason="")
    rt = _pick_sim(score_kw)(t, pm, ld, cal, cal_idx, **score_kw)
    if rt is None:
        # `_sim_one` 走完整段日历都没解出退出:要么价缺失,要么**前向窗口还没走完**。
        # 两种都不是"收益为 0",调用方必须按「算不出」处理(`metrics` 单独计数)。
        return FillScore(code, decision_date, True, FILL_UNRESOLVED, reason_of(FILL_UNRESOLVED),
                         gap_pct=gap, buy_date=t1, buy_price=buy_price, shares=shares)
    return FillScore(
        code, decision_date, True, FILL_OK,
        f"成交(开盘 {gap:+.2f}%)" if gap is not None else "成交",
        gap_pct=gap, buy_date=t1, buy_price=buy_price, shares=shares,
        ret=float(rt.pnl_pct), hold=int(rt.held_sessions), exempt=bool(rt.exempt),
        exit_reason=rt.reason, exit_date=rt.sell_date,
    )


# ══════════════════════════════════════════════════════════════════════════
# 价格图装配(生产侧;research 侧自带 `_price_maps` / `_build_pm`,口径相同)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PriceMaps:
    """`fill_and_score` / `_sim_one` 需要的四件套。`ok=False` = 这段窗口没数据
    (调用方按「判不了」处理,⛔ 不许拿空图当"全都买不进")。"""

    pm: dict
    ld: set
    cal: list
    cal_idx: dict
    qfq_anchor: Optional[date] = None
    ok: bool = True
    note: Optional[str] = None


def build_price_maps(
    codes: Sequence[str],
    start: date,
    end: date,
    *,
    parquet_dir: Optional[Path] = None,
) -> PriceMaps:
    """从生产 parquet 装配价格图(**前复权**,锚点固定在 `end`)+ 跌停集 + 日历。

    与 `research/h9_exit_reform.py::_price_maps` 同口径(前复权 daily + `limit_derived`
    的 `is_limit_down`),差别只在**没有进程级全局缓存**:生产侧一次评价跑多个窗口,
    全局缓存会让第二个窗口悄悄吃到第一个窗口的锚点。

    ⚠ **qfq 锚点 = `end`**:同一只票在不同窗口里的绝对价会差一个常数因子,**收益比
    不变**(2 位小数取整偶有微差,`research/drill.py::cmd_selfcheck` 已实证)。
    """
    import polars as pl

    from neckline.backtest.engine import load_adjusted_daily
    from neckline.data.market_data import scan_table_range

    want = [c for c in dict.fromkeys(codes) if c]
    cal = trading_days_between(start, end)
    cal_idx = {d: i for i, d in enumerate(cal)}
    if not want or not cal:
        return PriceMaps({}, set(), cal, cal_idx, qfq_anchor=end, ok=False,
                         note="无成员代码或窗口内无交易日")

    adj = load_adjusted_daily(start, end, parquet_dir=parquet_dir)
    if adj.is_empty():
        return PriceMaps({}, set(), cal, cal_idx, qfq_anchor=end, ok=False,
                         note=f"[{start},{end}] 区间无 daily 数据")
    adj = adj.filter(pl.col("ts_code").is_in(want))
    pm: dict = {}
    for (code,), g in adj.group_by(["ts_code"]):
        g = g.sort("trade_date")
        d = g["trade_date"].to_list()
        pm[code] = {
            "idx": {dt: i for i, dt in enumerate(d)},
            "o": g["open"].to_list(), "l": g["low"].to_list(), "c": g["close"].to_list(),
        }

    ld: set = set()
    try:
        lim = scan_table_range("limit_derived", start, end, parquet_dir=parquet_dir)
        if not lim.is_empty():
            lim = lim.filter(pl.col("ts_code").is_in(want) & pl.col("is_limit_down"))
            ld = set(zip(lim["ts_code"].to_list(), lim["trade_date"].to_list()))
    except Exception:  # noqa: BLE001 —— 跌停集缺失只影响「卖不出顺延」,不该掀翻判分
        logger.warning("[exit_sim] limit_derived 读取失败,本次判分不做「跌停卖不出」顺延", exc_info=True)

    return PriceMaps(pm, ld, cal, cal_idx, qfq_anchor=end, ok=bool(pm),
                     note=None if pm else "窗口内这些代码一行行情都没有")


__all__ = [
    "BROKER", "SLIP", "EPS",
    "ReTrade", "_sim_one", "sim_one",
    "score_kw_from_charter", "notional_from_charter", "forward_span_days",
    "SCORING_HORIZON_DAYS",
    "FillScore", "fill_and_score",
    "FILL_OK", "FILL_NOT_BUYABLE", "FILL_NO_T1", "FILL_T1_SUSPENDED",
    "FILL_ABOVE_CEILING", "FILL_UNRESOLVED",
    "PriceMaps", "build_price_maps",
]
