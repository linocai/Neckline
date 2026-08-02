"""V2-⑨-D 判分引擎唯一源:`_sim_one` 下沉的**逐位对拍** + 成交层 + 纪律参数单一源。

**这一条不过就不许继续**(plan §五 V2-⑨ 验收第一条)。对拍分两层:

    1. **源码逐位**:本文件内嵌一份搬迁前 `research/h9_exit_reform.py` 的
       `ReTrade` + `_sim_one` **冻结源文本**,与 `inspect.getsource()` 取到的现役
       实现逐字比对 —— 连注释和默认参数都不许差一个字节。
    2. **行为逐位**:把冻结源 `exec` 出一份独立可调用体(用**独立 new 出来的**
       `Broker()`,不共享 `exit_sim.BROKER`),在数百条随机造数上比对
       `ReTrade` 的每一个字段与派生属性。造数覆盖 stop / retrace / time / end 四种
       退出与「停牌顺延」「跌停卖不出」两条 continue 分支(测试自己断言覆盖到了,
       防止哪天造数退化成只跑一个分支还一路绿灯)。

真实 K 线上的对拍见 `scripts/smoke_eval_exit_sim.py`(读 `research/_cache/k3_panel.parquet`,
不进 CI —— 研究缓存不是 CI 依赖)。
"""

from __future__ import annotations

import ast
import inspect
import random
import re
import textwrap
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import pytest

from neckline.backtest.broker import Broker
from neckline.backtest.portfolio import ClosedTrade
from neckline.calendar import trading_days_between
from neckline.eval import exit_sim

_REPO = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════════
# 冻结源(搬迁前 `research/h9_exit_reform.py` 逐字副本 —— ⛔ 永远不要"顺手更新"它)
# ══════════════════════════════════════════════════════════════════════════

_FROZEN_SRC = '''@dataclass
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
'''


def _frozen_namespace() -> dict:
    """把冻结源 exec 成一份独立可调用体。

    `BROKER` / `SLIP` **独立 new 一份**(不 import `exit_sim` 的那两个对象)——
    这样连「有人偷偷改了 `Broker()` 的构造参数」都会被这条对拍照出来。
    """
    broker = Broker()
    ns = {
        "dataclass": dataclass, "date": date, "Optional": Optional,
        "ClosedTrade": ClosedTrade, "trading_days_between": trading_days_between,
        "BROKER": broker, "SLIP": broker.slippage_bp / 10000.0,
    }
    exec(compile(_FROZEN_SRC, "<frozen_h9_sim_one>", "exec"), ns)  # noqa: S102 - 冻结副本,非外部输入
    return ns


_FROZEN = _frozen_namespace()


# ══════════════════════════════════════════════════════════════════════════
# 造数(随机游走 + 停牌缺口 + 跌停日)
# ══════════════════════════════════════════════════════════════════════════

_CAL = trading_days_between(date(2024, 1, 2), date(2024, 6, 28))


def _make_case(rng: random.Random):
    """随机造一只票的价格图 + 跌停集 + 一笔入场单 + 一组退出参数。"""
    code = f"T{rng.randint(1, 5)}.SZ"
    px = 10.0
    days, o, l, c = [], [], [], []
    for d in _CAL:
        if rng.random() < 0.06:               # 6% 概率停牌:该日整行不存在
            continue
        drift = rng.uniform(-0.09, 0.09)
        op = round(px * (1 + rng.uniform(-0.03, 0.03)), 2)
        cl = round(max(0.5, px * (1 + drift)), 2)
        lo = round(min(op, cl) * (1 - abs(rng.uniform(0, 0.03))), 2)
        days.append(d); o.append(op); l.append(lo); c.append(cl)
        px = cl
    if len(days) < 20:
        return None
    pm = {code: {"idx": {d: i for i, d in enumerate(days)}, "o": o, "l": l, "c": c}}
    ld = {(code, d) for d in days if rng.random() < 0.05}
    cal_idx = {d: i for i, d in enumerate(_CAL)}
    buy_date = days[rng.randrange(0, max(1, len(days) - 3))]
    shares = rng.choice([100, 300, 1000, 2600])
    buy_price = pm[code]["o"][pm[code]["idx"][buy_date]]
    t = ClosedTrade(ts_code=code, buy_date=buy_date, sell_date=buy_date, shares=shares,
                    buy_price=buy_price, sell_price=buy_price,
                    buy_fees=Broker()._buy_fees(shares * buy_price), sell_fees=0.0, reason="")
    kw = dict(
        base_hold=rng.randint(1, 8),
        retrace=round(rng.uniform(0.03, 0.12), 4),
        stop=round(rng.uniform(0.02, 0.10), 4),
        v1=rng.random() < 0.5,
        v2=rng.random() < 0.4,
        v2_gate=round(rng.uniform(0.04, 0.12), 4),
        v2_wide=round(rng.uniform(0.05, 0.15), 4),
        hard_cap=rng.randint(8, 20),
    )
    return t, pm, ld, _CAL, cal_idx, kw


def _fields(rt) -> Optional[tuple]:
    if rt is None:
        return None
    return (rt.ts_code, rt.buy_date, rt.sell_date, rt.sell_price, rt.reason, rt.exempt,
            rt.held_sessions, rt.shares, rt.sell_fees, rt.pnl, rt.cost_basis, rt.pnl_pct)


class TestFrozenPairing:
    """搬迁前后逐位对拍(⑨ 验收第一条)。"""

    def test_source_is_byte_identical(self):
        """源码逐字对拍:注释 / 默认参数 / 空行全都不许差。"""
        live = (inspect.getsource(exit_sim.ReTrade).rstrip()
                + "\n\n\n"
                + inspect.getsource(exit_sim._sim_one).rstrip() + "\n")
        assert live == _FROZEN_SRC, (
            "`neckline/eval/exit_sim.py` 的 ReTrade/_sim_one 与搬迁前的冻结源不再逐字相同 —— "
            "判分口径是审计基准,改它必须走「新版本 + 重新对拍」,不许就地改"
        )

    def test_behaviour_bit_identical_on_random_cases(self):
        """400 条随机造数逐字段比对,并断言四种退出与两条 continue 分支都被覆盖到。"""
        rng = random.Random(20260802)          # 固定种子:失败可复现
        frozen = _FROZEN["_sim_one"]
        seen_reasons, seen_none, compared = set(), 0, 0
        for _ in range(400):
            case = _make_case(rng)
            if case is None:
                continue
            t, pm, ld, cal, cal_idx, kw = case
            a = exit_sim._sim_one(t, pm, ld, cal, cal_idx, **kw)
            b = frozen(t, pm, ld, cal, cal_idx, **kw)
            assert _fields(a) == _fields(b), f"逐位对拍失败:kw={kw} code={t.ts_code} buy={t.buy_date}"
            compared += 1
            if a is None:
                seen_none += 1
            else:
                seen_reasons.add(a.reason)
        assert compared >= 300, f"造数退化(只比了 {compared} 条)"
        assert {"stop", "retrace", "time"} <= seen_reasons, f"退出分支覆盖不足:{seen_reasons}"

    def test_end_branch_and_delay_branches_covered(self):
        """`end`(数据末端强平)+ 跌停卖不出顺延 + 停牌顺延三条窄分支,单独造数覆盖。"""
        frozen = _FROZEN["_sim_one"]
        cal = _CAL[:10]
        cal_idx = {d: i for i, d in enumerate(cal)}
        code = "E1.SZ"
        # 一路阴跌:第 2 天就触发 stop,但那天之后每一天都跌停卖不出 → 一直顺延到末端 → end
        days = list(cal)
        o = [10.0] + [9.0] * 9
        c = [10.0] + [8.0] * 9
        low = [9.9] + [7.9] * 9
        pm = {code: {"idx": {d: i for i, d in enumerate(days)}, "o": o, "l": low, "c": c}}
        ld = {(code, d) for d in days[1:]}
        t = ClosedTrade(ts_code=code, buy_date=days[0], sell_date=days[0], shares=1000,
                        buy_price=10.0, sell_price=10.0, buy_fees=5.0, sell_fees=0.0, reason="")
        kw = dict(base_hold=5, retrace=0.08, stop=0.05, v1=True, hard_cap=15)
        a = exit_sim._sim_one(t, pm, ld, cal, cal_idx, **kw)
        b = frozen(t, pm, ld, cal, cal_idx, **kw)
        assert a is not None and a.reason == "end"
        assert _fields(a) == _fields(b)

        # 停牌顺延:触发日(cal[1] 收盘跌破止损)之后的 cal[2] 整行缺失 → 卖不掉,
        # 顺延到 cal[3] 重判、cal[4] 开盘成交。同样一组价格**不缺行**时应当卖在 cal[2],
        # 两者对照才证明确实走了那条 continue 分支(而不是碰巧同一天)。
        closes = [10.0] + [9.0] * 9
        full = {code: {"idx": {d: i for i, d in enumerate(cal)},
                       "o": [10.0] * 10, "l": [9.9] * 10, "c": closes}}
        t2 = ClosedTrade(ts_code=code, buy_date=cal[0], sell_date=cal[0], shares=1000,
                         buy_price=10.0, sell_price=10.0, buy_fees=5.0, sell_fees=0.0, reason="")
        no_gap = exit_sim._sim_one(t2, full, set(), cal, cal_idx, **kw)
        assert no_gap is not None and no_gap.reason == "stop" and no_gap.sell_date == cal[2]

        days2 = [d for i, d in enumerate(cal) if i != 2]
        pm2 = {code: {"idx": {d: i for i, d in enumerate(days2)},
                      "o": [10.0] * len(days2), "l": [9.9] * len(days2),
                      "c": [closes[i] for i, d in enumerate(cal) if i != 2]}}
        a2 = exit_sim._sim_one(t2, pm2, set(), cal, cal_idx, **kw)
        b2 = frozen(t2, pm2, set(), cal, cal_idx, **kw)
        assert a2 is not None and a2.reason == "stop" and a2.sell_date == cal[4]
        assert _fields(a2) == _fields(b2)

    def test_research_三处_import_的是同一个函数对象(self):
        """`research/h9_exit_reform.py` / `drill.py` / `exam.py` 用的必须是本模块这一份。"""
        import sys

        sys.path.insert(0, str(_REPO / "research"))
        try:
            import drill  # noqa: PLC0415
            import h9_exit_reform as h9  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        assert h9._sim_one is exit_sim._sim_one
        assert h9.ReTrade is exit_sim.ReTrade
        assert h9.BROKER is exit_sim.BROKER
        assert drill._sim_one is exit_sim._sim_one
        assert drill.SLIP == exit_sim.SLIP


# ══════════════════════════════════════════════════════════════════════════
# 纪律参数单一源
# ══════════════════════════════════════════════════════════════════════════

class TestScoreKwFromCharter:
    def test_maps_v133_charter_to_research_score_kw(self, monkeypatch):
        """v1.3.3 章程翻出来的判分参数,必须**恰好等于**研究侧冻结的 `SCORE_KW`。

        这是"生产与研究吃同一份口径"最直接的证据:研究侧那组数是 STRATEGY_LAB §六
        写死的现役纪律新规,生产侧完全由章程算出来、代码里一个字面量都没有。
        """
        monkeypatch.setattr(
            "neckline.strategy.brain.active_config",
            lambda db_path=None: {
                "stop_pct": 0.05, "take_profit_retrace": 0.08, "max_hold_days": 5,
                "max_hold_days_profit": 15, "time_exit_only_if_unprofitable": True,
                "single_cap": 20000.0,
            },
        )
        assert exit_sim.score_kw_from_charter() == dict(
            base_hold=5, retrace=0.08, stop=0.05, v1=True, hard_cap=15
        )

    def test_k1_style_charter_disables_exempt_and_pins_hard_cap(self, monkeypatch):
        """没开浮盈续命 → `v1=False` 且 `hard_cap` 退回 `base_hold`(K1 逐位行为)。"""
        monkeypatch.setattr(
            "neckline.strategy.brain.active_config",
            lambda db_path=None: {
                "stop_pct": 0.05, "take_profit_retrace": 0.05, "max_hold_days": 5,
                "max_hold_days_profit": None, "time_exit_only_if_unprofitable": None,
            },
        )
        kw = exit_sim.score_kw_from_charter()
        assert kw["v1"] is False and kw["hard_cap"] == kw["base_hold"] == 5

    def test_no_active_charter_fails_loud(self, monkeypatch):
        monkeypatch.setattr("neckline.strategy.brain.active_config", lambda db_path=None: {})
        with pytest.raises(ValueError, match="无现役版本"):
            exit_sim.score_kw_from_charter()
        with pytest.raises(ValueError, match="single_cap"):
            exit_sim.notional_from_charter()

    def test_missing_required_field_fails_loud(self, monkeypatch):
        monkeypatch.setattr(
            "neckline.strategy.brain.active_config",
            lambda db_path=None: {"stop_pct": 0.05, "max_hold_days": 5},
        )
        with pytest.raises(ValueError, match="take_profit_retrace"):
            exit_sim.score_kw_from_charter()


# ══════════════════════════════════════════════════════════════════════════
# 成交层(考官线 §九)
# ══════════════════════════════════════════════════════════════════════════

_KW = dict(base_hold=5, retrace=0.08, stop=0.05, v1=True, hard_cap=15)


def _flat_maps(code="A.SZ", opens=None, closes=None, lows=None):
    cal = _CAL[:12]
    n = len(cal)
    opens = opens or [10.0] * n
    closes = closes or [10.0] * n
    lows = lows or [9.95] * n
    pm = {code: {"idx": {d: i for i, d in enumerate(cal)}, "o": opens, "l": lows, "c": closes}}
    return pm, set(), cal, {d: i for i, d in enumerate(cal)}


class TestFillLayer:
    def test_not_buyable_is_not_a_zero_return(self):
        pm, ld, cal, cal_idx = _flat_maps()
        r = exit_sim.fill_and_score("A.SZ", cal[0], buyable=False, pm=pm, ld=ld, cal=cal,
                                    cal_idx=cal_idx, score_kw=_KW, notional=20000.0)
        assert r.filled is False and r.fill_code == exit_sim.FILL_NOT_BUYABLE
        assert r.ret == 0.0 and r.buy_price is None    # 0 是"没成交",不是"收益为零"

    def test_no_t1_at_data_end(self):
        pm, ld, cal, cal_idx = _flat_maps()
        r = exit_sim.fill_and_score("A.SZ", cal[-1], buyable=True, pm=pm, ld=ld, cal=cal,
                                    cal_idx=cal_idx, score_kw=_KW, notional=20000.0)
        assert r.fill_code == exit_sim.FILL_NO_T1 and r.filled is False

    def test_ceiling_price_blocks_chase(self):
        """开盘高于卡上冻结的最高追价 → 未成交(「追不进」≠「买了亏 0」)。"""
        n = len(_CAL[:12])
        pm, ld, cal, cal_idx = _flat_maps(opens=[10.0] + [11.5] * (n - 1))
        blocked = exit_sim.fill_and_score("A.SZ", cal[0], buyable=True, pm=pm, ld=ld, cal=cal,
                                          cal_idx=cal_idx, score_kw=_KW, notional=20000.0,
                                          ceiling_price=11.0)
        assert blocked.filled is False and blocked.fill_code == exit_sim.FILL_ABOVE_CEILING
        assert blocked.gap_pct == pytest.approx(15.0)
        passed = exit_sim.fill_and_score("A.SZ", cal[0], buyable=True, pm=pm, ld=ld, cal=cal,
                                         cal_idx=cal_idx, score_kw=_KW, notional=20000.0,
                                         ceiling_price=11.5)
        assert passed.filled is True and passed.fill_code == exit_sim.FILL_OK

    def test_ceiling_none_is_baseline(self):
        n = len(_CAL[:12])
        pm, ld, cal, cal_idx = _flat_maps(opens=[10.0] + [11.5] * (n - 1))
        r = exit_sim.fill_and_score("A.SZ", cal[0], buyable=True, pm=pm, ld=ld, cal=cal,
                                    cal_idx=cal_idx, score_kw=_KW, notional=20000.0)
        assert r.filled is True and r.buy_price == 11.5

    def test_entry_price_has_no_slippage_but_exit_does(self):
        """成交层用竞价单一价(无滑点),退出层照旧含滑点 —— 两层口径差刻意保留。"""
        pm, ld, cal, cal_idx = _flat_maps(
            opens=[10.0] * 12, closes=[10.0, 10.0, 8.0] + [8.0] * 9, lows=[9.9] * 12)
        r = exit_sim.fill_and_score("A.SZ", cal[0], buyable=True, pm=pm, ld=ld, cal=cal,
                                    cal_idx=cal_idx, score_kw=_KW, notional=20000.0)
        assert r.buy_price == 10.0                                   # 无滑点
        assert r.exit_reason == "stop"
        assert r.buy_date == cal[1] and r.exit_date == cal[3]
        assert r.filled is True

    def test_score_kw_is_required_keyword(self):
        """`score_kw` 没有默认值 —— 有默认就等于在判分层埋一份纪律参数。"""
        sig = inspect.signature(exit_sim.fill_and_score)
        assert sig.parameters["score_kw"].default is inspect.Parameter.empty
        assert sig.parameters["notional"].default is inspect.Parameter.empty


# ══════════════════════════════════════════════════════════════════════════
# 守门:`neckline/eval/` 内不许有第二份判分实现 + 无硬编纪律数字
# ══════════════════════════════════════════════════════════════════════════

def _code_constants(path: Path):
    """源码里**非 docstring** 的字面量常量(docstring 里写 0.05 是解释,不是硬编)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body and isinstance(node.body[0], ast.Expr):
                doc_ids.add(id(node.body[0].value))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and id(n) not in doc_ids]


class TestSingleScoringImplementationGuard:
    """⑨-C2 验收第 ② 条:随机臂必须走同一套 `exit_sim` 判分。"""

    def test_only_one_scoring_implementation_in_eval_package(self):
        """`neckline/eval/` 里定义退出模拟 / 成交判定的函数只能有那一份。"""
        pkg = _REPO / "neckline" / "eval"
        defining = []
        for f in sorted(pkg.glob("*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and re.fullmatch(
                    r"_?(sim_one|simulate_exit|replay|fill_and_score|_score_pick|_sim_entry)", node.name
                ):
                    defining.append(f"{f.name}::{node.name}")
        assert defining == ["exit_sim.py::_sim_one", "exit_sim.py::fill_and_score"], (
            f"neckline/eval/ 内出现了第二份判分实现:{defining}"
        )

    def test_other_eval_modules_call_exit_sim_not_reimplement(self):
        """除 `exit_sim.py` 外,`neckline/eval/` 与复盘引擎都必须 import 它来判分。"""
        others = [f for f in (_REPO / "neckline" / "eval").glob("*.py")
                  if f.name not in ("exit_sim.py", "__init__.py")]
        assert others, "eval 包里应当还有别的模块(metrics/placebo/calibration)"
        for f in others:
            src = f.read_text(encoding="utf-8")
            if "fill_and_score" in src or "sim_one" in src:
                assert "from neckline.eval.exit_sim import" in src or \
                       "from neckline.eval import exit_sim" in src, f"{f.name} 没有 import 判分唯一源"

    def test_no_hardcoded_discipline_numbers_outside_frozen_sim(self):
        """判分模块除**冻结的 `_sim_one` 默认参数**外,不许再出现 0.05 / 0.08。

        那三个默认参数是搬迁逐位等价的代价(改了就不是同一份实现了),已在模块
        docstring 明文警告「生产侧一律不许用默认值」,并由上面的
        `test_score_kw_is_required_keyword` 从调用面堵死。
        """
        src_lines = (_REPO / "neckline" / "eval" / "exit_sim.py").read_text(encoding="utf-8").splitlines()
        frozen_lines = {i for i, ln in enumerate(src_lines, 1)
                        if "base_hold: int = 5" in ln or "v1: bool = False" in ln}
        # 冻结签名占三行,取其起止范围
        lo, hi = min(frozen_lines), max(frozen_lines) + 1
        for node in _code_constants(_REPO / "neckline" / "eval" / "exit_sim.py"):
            v = node.value
            if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= node.lineno <= hi:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                assert float(v) not in (0.05, 0.08), f"第 {node.lineno} 行硬编纪律比例 {v}"

    def test_review_and_eval_modules_have_no_discipline_literals(self):
        """复盘 / 评价 / 安慰剂三处一个纪律数字都不许写死(全部读章程)。"""
        targets = list((_REPO / "neckline" / "eval").glob("*.py")) + \
            [_REPO / "neckline" / "review" / "basket_review.py"]
        for f in targets:
            if not f.exists() or f.name == "exit_sim.py":
                continue
            for node in _code_constants(f):
                v = node.value
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    assert float(v) not in (0.05, 0.08), f"{f.name} 第 {node.lineno} 行硬编纪律比例 {v}"


def test_docstring_warns_about_defaults():
    """模块 docstring 必须把「默认参数生产不许用」写清楚(下一个人会照着抄)。"""
    doc = textwrap.dedent(exit_sim.__doc__ or "")
    assert "生产侧一律不许用" in doc and "score_kw_from_charter" in doc
