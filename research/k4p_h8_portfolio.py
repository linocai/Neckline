"""K4 前置 · 战役三 H8:幸存细胞组合级生存测试(贵档梯;仅对 H7 幸存者)。

预注册见 `research/k4_pre3_report.md` §2。**梯子纪律**:H7 事件研究幸存的细胞才升 H8。

**H7 判定结果(见 k4p_h7_bounce.py):四细胞全灭**——无一同时满足「全期净期望>0 且
左尾不肥于域基线 且 2026 分段非负」:
    · 年线上×非涨停大红 / 年线上×涨停大红:全期净期望≤0(挂第一关);
    · 年线下×非涨停大红:全期 +1.69%(h3,全表最强信号)且左尾≈基线,但 **2026 −0.62%**(挂 2026 闸);
    · 年线下×涨停大红(诱多复核):全期 +0.88% 转正,但左尾肥于基线 **且** 2026 −3.96%(双挂)。
故按预注册:**H7 全灭 → 战役终,H8 主判「未达」,K4 机械半身定格为纯避坑,不进组合级。**

**⚠ buy_gate 口径冲突登记 + 最接近意图的可行口径**:预注册 §2 设想「run_pf 的 buy_gate
外部注入表达细胞入场条件」。**读 momentum.py 确认 buy_gate 实为「允许开新仓的交易日集合」
(line 195 `t not in self._buy_gate`),是日期级闸门,无法表达个股入场条件**;而 K3 遗留的
`buypoint="oversold"` 字段 depth 是「≤阈值(向下超跌)」,无法表达 H7 的「大红向上」。二者
皆不可用。**最接近预注册意图(外部注入 + 生产零改动)的可行口径 = 预过滤研究面板到细胞行 +
config `strength="none"/buypoint="none"`(入场 mask 退化为 base_universe_expr,恰好命中预过滤
留下的细胞行)**——引擎价格/退出走 adjusted_daily(全市场),panel 仅作候选源,预过滤安全;
neckline/strategy/ 零改动。此为口径偏离登记,不静默改定义。

**以下组合级为「越界诚实补充」**:H7 未过闸本不进 H8;此表专为兑现裁决书「写透为什么死」
+ 与 K3 血训(信号级正期望 → -5% 止损/hold≤5 组合纪律下翻负)对拍,**不参与升级判决**
(判决已由 H7 2026 闸门定案)。对拍对象 = 星细胞「年线下×非涨停大红」(H7 全期最强 +1.69%)。

独立可重跑:`python research/k4p_h8_portfolio.py`
"""

from __future__ import annotations

import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.strategy import brain  # noqa: E402
from neckline.strategy.momentum import MomentumConfig  # noqa: E402
from k4p_common import base_expr, oneword_event_expr  # noqa: E402
import lab  # noqa: E402

K3_PANEL = Path(__file__).resolve().parent / "_cache" / "k3_panel.parquet"
FROZEN_END = date(2026, 7, 17)
IN = (date(2020, 1, 1), date(2024, 12, 31))       # 样本内(2020 ma250 全 null → 星细胞实际 2021+)
OUT = (date(2025, 1, 1), date(2026, 7, 17))       # 样本外冻结(主判,与 K1 严格可比)
Y2026 = (date(2026, 1, 1), date(2026, 7, 17))     # 2026 段(硬门禁)
REBOUND_END = date(2026, 7, 24)                    # 止损后反弹前视只用 qfq 收盘(不改判决窗)

# —— 星细胞入场条件(H7 年线下×非涨停大红)——
TREND_BELOW = pl.col("ma250").is_not_null() & ~((pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up"))
STAR_CELL = (
    base_expr()
    & (pl.col("close") <= pl.col("ma20"))
    & (pl.col("ret_1d") >= 0.05)
    & ~oneword_event_expr()
    & TREND_BELOW
    & ~pl.col("is_limit_up")
)

_PANEL: Optional[pl.DataFrame] = None
_PF_CACHE: Dict[Tuple[str, tuple], Tuple[object, object]] = {}


def panel() -> pl.DataFrame:
    global _PANEL
    if _PANEL is None:
        _PANEL = pl.read_parquet(K3_PANEL).filter(pl.col("trade_date") <= FROZEN_END)
    return _PANEL


def cell_panel() -> pl.DataFrame:
    return panel().filter(STAR_CELL)


def k1_cfg() -> MomentumConfig:
    return MomentumConfig(**brain.active_config())


def cell_cfg(main_only: bool) -> MomentumConfig:
    """继承 K1 现役纪律(止损/止盈/hold/仓位),入场换成「预过滤=星细胞」(strength/buypoint=none)。
    main_only=True → 叠加 forbid_high_elasticity(与 K1 同 MAIN-only 板块口径,最公平的 -5% 对照);
    False → 全板块(=H7 星细胞信号 +1.69% 的实际度量域)。其余禁买过滤清零,入场纯由预过滤决定。"""
    base = dict(brain.active_config())
    base.update(
        strength="none", buypoint="none",
        forbid_high_elasticity=main_only, forbid_new_days=None,
        forbid_green_bigdown=None, forbid_far_from_high=None,
        shallow_pullback=None, max_turnover=None,
    )
    return MomentumConfig(**base)


def _f(x, p=4) -> str:
    if x is None or (isinstance(x, float) and x != x):
        return "nan"
    if isinstance(x, float) and abs(x) > 1e6:
        return "inf"
    return f"{x:.{p}f}"


def _md(header: List[str], rows: List[List[str]]) -> str:
    line = "| " + " | ".join(header) + " |"
    sep = "|" + "|".join("---" if i == 0 else "--:" for i in range(len(header))) + "|"
    return "\n".join([line, sep] + ["| " + " | ".join(r) + " |" for r in rows])


def _pf(label: str, cfg: MomentumConfig, w, use_cell: bool) -> Tuple[object, object]:
    key = (label, w)
    if key not in _PF_CACHE:
        p = cell_panel() if use_cell else panel()
        _PF_CACHE[key] = lab.run_pf(cfg, w[0], w[1], panel=p)
    return _PF_CACHE[key]


def configs() -> Dict[str, Tuple[MomentumConfig, bool]]:
    """(config, use_cell_panel)。K1 用全面板真实对手;细胞用预过滤面板。"""
    return {
        "K1(现役对手·裸池)": (k1_cfg(), False),
        "星细胞组合级·全板块": (cell_cfg(main_only=False), True),
        "星细胞组合级·MAIN only": (cell_cfg(main_only=True), True),
    }


def _sum_row(label: str, rep) -> List[str]:
    return [label, str(rep.n_trades), _f(rep.total_return), _f(rep.max_drawdown),
            _f(rep.win_rate, 3), _f(rep.profit_factor, 3), _f(rep.final_equity, 0)]


def portfolio_table(w, title: str) -> str:
    hdr = ["config", "n", "total_ret", "max_dd", "win", "pf", "final_eq"]
    rows = [_sum_row(name, _pf(name, cfg, w, uc)[0]) for name, (cfg, uc) in configs().items()]
    return f"**{title}**\n\n" + _md(hdr, rows)


def survival_2026() -> str:
    hdr = ["config", "2026 n", "2026 total_ret", "2026 max_dd", "2026 pf", "生存判定"]
    rows = []
    for name, (cfg, uc) in configs().items():
        rep, _ = _pf(name, cfg, Y2026, uc)
        verdict = "—(基线)" if name.startswith("K1") else ("✅生存" if (rep.total_return or 0) >= 0 else "❌否决")
        rows.append([name, str(rep.n_trades), _f(rep.total_return), _f(rep.max_drawdown),
                     _f(rep.profit_factor, 3), verdict])
    return ("**★ 2026 段生存测试(2026-01-01~2026-07-17,硬门禁:total_ret<0 即一票否决)**\n\n" + _md(hdr, rows))


def _stop_rebound_stats(pf, qfq: pl.DataFrame, ndays: int = 5) -> dict:
    """止损触发率 + 止损后 ndays 交易日 close 是否反弹回卖出价上方(扫在地板嫌疑)。承 k3_b2_portfolio。"""
    closed = pf.closed_trades
    n = len(closed)
    if n == 0:
        return {"n": 0, "n_stop": 0, "stop_frac": float("nan"), "rebound_frac": float("nan"), "med_rebound": float("nan")}
    stops = [t for t in closed if t.reason.startswith("止损")]
    by_code: Dict[str, Tuple[list, list]] = {}
    for code, sub in qfq.sort("trade_date").group_by("ts_code"):
        c = code[0] if isinstance(code, tuple) else code
        by_code[c] = (sub["trade_date"].to_list(), sub["close"].to_list())
    rebounds = []
    for t in stops:
        ds, cs = by_code.get(t.ts_code, (None, None))
        if ds is None:
            continue
        i = bisect_right(ds, t.sell_date)
        j = i + ndays - 1
        if j < len(cs):
            rebounds.append(cs[j] / t.sell_price - 1.0)
    reb = pl.Series(rebounds) if rebounds else None
    return {
        "n": n, "n_stop": len(stops), "stop_frac": len(stops) / n,
        "rebound_frac": float((reb > 0).mean()) if reb is not None and reb.len() else float("nan"),
        "med_rebound": float(reb.median()) if reb is not None and reb.len() else float("nan"),
    }


def stoploss_table() -> str:
    qfq = lab.adjusted_daily_cached(OUT[0], REBOUND_END)
    hdr = ["config", "回合n", "止损n", "止损率", "止损后5日反弹率", "止损后5日中位涨跌", "读数"]
    rows = []
    for name, (cfg, uc) in configs().items():
        _, pf = _pf(name, cfg, OUT, uc)
        s = _stop_rebound_stats(pf, qfq, 5)
        note = "过半止损后反弹(扫地板嫌疑)" if (s["rebound_frac"] == s["rebound_frac"] and s["rebound_frac"] >= 0.5) else ""
        rows.append([name, str(s["n"]), str(s["n_stop"]), _f(s["stop_frac"], 3),
                     _f(s["rebound_frac"], 3), _f(s["med_rebound"]), note])
    return ("**止损扫损率 × 波动天性交互(样本外冻结窗回合;止损后5交易日是否反弹回卖出价上方=扫地板)**\n\n" + _md(hdr, rows))


def stratify_table() -> str:
    out = ["**分层(星细胞·全板块,样本外冻结已平仓回合;by 买入年 / 上证MA20 状态)**", ""]
    cfg, uc = configs()["星细胞组合级·全板块"]
    _, pf = _pf("星细胞组合级·全板块", cfg, OUT, uc)
    by_year = lab.stratify_by_year(pf.closed_trades)
    by_state = lab.stratify_by_state(pf.closed_trades)
    out.append("\n_by year_\n"); out.append(lab.fmt(by_year) if not by_year.is_empty() else "(空)")
    out.append("\n_by state_\n"); out.append(lab.fmt(by_state) if not by_state.is_empty() else "(空)")
    # K1 对照分层
    ck1, _ = configs()["K1(现役对手·裸池)"]
    _, pfk1 = _pf("K1(现役对手·裸池)", ck1, OUT, False)
    out.append("\n_K1 对照 by year_\n"); out.append(lab.fmt(lab.stratify_by_year(pfk1.closed_trades)))
    out.append("\n_K1 对照 by state_\n"); out.append(lab.fmt(lab.stratify_by_state(pfk1.closed_trades)))
    return "\n".join(out)


def main() -> None:
    print("# H8 组合级生存测试 —— 结果")
    print(f"\n面板 k3_panel ≤{FROZEN_END}:{panel().height} 行。星细胞预过滤面板 = {cell_panel().height} 行。")
    print(f"窗口 IN={IN}(2020 ma250 全 null → 星细胞实际 2021+) OUT={OUT} 2026={Y2026}。")
    print("\n## H8 主判:未达(H7 四细胞全灭,无幸存者升级;判决已定于 H7 2026 闸门)")
    print("以下组合级为越界诚实补充(见模块 docstring),不参与升级判决。对拍星细胞=年线下×非涨停大红。\n")

    print("## 组合级回测 vs K1 裸池(信号级 +1.69% → 组合级?)\n")
    print(portfolio_table(IN, "样本内 2020-2024(星细胞实际 2021+)")); print()
    print(portfolio_table(OUT, "样本外冻结 2025-01~2026-07-17(主判可比)")); print()
    print(survival_2026()); print()

    print("## 止损交互(K3 血训直面:止损率 + 止损后反弹率=扫地板)\n")
    print(stoploss_table()); print()

    print("## 分层\n")
    print(stratify_table())


if __name__ == "__main__":
    main()
