"""1.9 汇总:策略规则 v1 组装 + 样本外对照 dummy 基准 + 落大脑版本表。

规则 v1 = P1-P10 逐项过堂后的采纳集合(见 stage1_report.md 各节)。诚实原则:若样本外
盈利因子仍 <1,如实报告——不为凑验收线过拟合。市场过滤器(P1)是争议项,rule v1 主档
按「用户未拍板前先不硬开」处理,同时给出开/关两版对照供决策。

运行:
  python -m research.rule_v1            # 只跑对照、打印(不写库)
  python -m research.rule_v1 --commit   # 打印 + 落 strategy_versions 大脑表(v1)
"""

from __future__ import annotations

import sys

import polars as pl

from neckline.strategy.momentum import MomentumConfig
from neckline.strategy.dummy import DummyStrategy
from neckline.backtest.engine import BacktestEngine
from neckline.strategy import brain
from research import lab

# —— 规则 v1 采纳参数(过堂结论,见报告)——————————————————————————————
RULE_V1 = dict(
    strength="none",            # P3:强势筛选削 edge,否决
    buypoint="pullback",        # P3:回调低吸弱优于突破;贴合均值回归
    forbid_green_bigdown=None,  # P4:否决(被禁那批反而更好)
    forbid_far_from_high=None,  # P5:否决(被打趴的最会反弹)
    forbid_new_days=None,       # P6:次新过滤组合级不改善(-13.3%→-15.4%),否决
    forbid_high_elasticity=True,  # P6:禁高弹(=主板 only)。结构性风控选择(-5% 止损匹配 10% 涨跌幅品种、
                                  # 契合 §2.2「易跌停高弹入黑名单」),并压住灾难尾部(含高弹的裸基线样本外 -98.6%)。
                                  # ⚠诚实:样本内 PF 0.93→1.02 的"转正"【不复现】于样本外(0.837→0.814,反略差)=样本内过拟合,
                                  # 不作 alpha 主张;out-of-sample 中性,保留仅为风控与设计意图,用户可平替关闭。
    stop_pct=0.05,              # 1.3/P4:-5% 最优(PF 最高、回撤最低)+ 纪律强制
    take_profit_retrace=0.05,   # P7:回落 5% 微优(PF 0.922→0.931,证据弱,待 walk-forward 复核)
    max_hold_days=5,            # P8:hold=5 最优(印证 4-7 日打平桶)
    cooldown_days=0,            # P9:见结论
    single_cap=20000.0, max_positions=5, max_exposure_frac=0.60,  # P10 纪律章程
    week_halving=False,         # P10 挂起项:默认关(用户决策)
)


def dummy_baseline(start, end):
    """plan 验收字面基准:等权买 5 只、持有 5 日(全域,仅流动性)。同 12 万本金/费用。"""
    strat = DummyStrategy(n_positions=5, hold_days=5, min_price=2.0)
    eng = BacktestEngine(strat, start, end, initial_cash=lab.INITIAL_CASH,
                         adjusted_daily=lab.adjusted_daily_cached(start, end))
    return eng.run()


def main(commit: bool):
    lab.get_panel()
    S, E = lab.SAMPLE_IN_START, lab.SAMPLE_IN_END
    O_S, O_E = lab.SAMPLE_OUT_START, lab.SAMPLE_OUT_END
    gate = lab.bull_days()
    cfg = MomentumConfig(**RULE_V1)

    print("=" * 78)
    print("规则 v1 vs dummy 基准 vs 纪律化全域基线(样本内 & 样本外)")
    print("=" * 78)
    rows = []
    for label, window in [("样本内 2020-2024", (S, E)), ("样本外 2025-2026", (O_S, O_E))]:
        s, e = window
        rep_v1, pf_v1 = lab.run_pf(cfg, s, e)
        rep_v1g, _ = lab.run_pf(cfg, s, e, buy_gate=gate)
        rep_broad, _ = lab.run_pf(MomentumConfig(strength="none", buypoint="none", stop_pct=0.05,
                                                 max_hold_days=5), s, e)
        rep_dum = dummy_baseline(s, e)
        rows.append(lab.summary_row(rep_v1, f"[{label}] 规则v1(市场过滤关)"))
        rows.append(lab.summary_row(rep_v1g, f"[{label}] 规则v1(市场过滤开)"))
        rows.append(lab.summary_row(rep_broad, f"[{label}] 纪律化全域基线"))
        rows.append(lab.summary_row(rep_dum, f"[{label}] dummy(等权5只hold5)"))
    print(lab.fmt(pl.DataFrame(rows)))

    # 样本外规则 v1 分层
    print("\n" + "=" * 78)
    print("规则 v1(市场过滤关)样本外分层:按年 / 按市场状态")
    print("=" * 78)
    rep_v1, pf_out = lab.run_pf(cfg, O_S, O_E)
    print("[按年]"); print(lab.fmt(lab.stratify_by_year(pf_out.closed_trades)))
    print("[按市场状态]"); print(lab.fmt(lab.stratify_by_state(pf_out.closed_trades)))

    if commit:
        # 落大脑 v1(用样本外指标做定版证据)
        rep_in, _ = lab.run_pf(cfg, S, E)
        rep_out, _ = lab.run_pf(cfg, O_S, O_E)
        metrics = {
            "in_sample": {"total_ret": rep_in.total_return, "pf": rep_in.profit_factor,
                          "win_rate": rep_in.win_rate, "max_dd": rep_in.max_drawdown, "n": rep_in.n_trades},
            "out_sample": {"total_ret": rep_out.total_return, "pf": rep_out.profit_factor,
                           "win_rate": rep_out.win_rate, "max_dd": rep_out.max_drawdown, "n": rep_out.n_trades},
        }
        changelog = (
            "阶段1 P1-P10 过堂定版。采纳:strength=none(P3 强势系统性削edge)/买点pullback(P3)/-5%止损"
            "(1.3 全网格最优 PF+回撤)/hold5(P8,印证4-7日打平桶)/回落止盈5%(P7 弱证据)/主板only禁高弹"
            "(P6 风控/结构,非alpha)/仓位2万·5只·敞口60%。否决:强势筛选/绿盘大阴线禁买(P4)/距前高禁买"
            "(P5)/次新过滤/系统性冷却(P9 网格反证)。待用户拍板:MA20 市场过滤(P1 实时闸门双窗变差,建议不采纳)、"
            "次周减半(P10 空效应)。诚实定性:日线2-5日母战法【无正net edge】(A股此频率均值回归),v1 是"
            "【减损纪律版】——样本外 PF 0.814<1(仍净亏 -10.7%)但远跑赢 dummy(-65%)与裸纪律基线(-98.6%);"
            "禁高弹样本内转正(PF 1.02)【不复现】于样本外=过拟合,不作 alpha 主张。alpha 悬而未决,留给更快情绪"
            "信号(阶段2 情绪仪表盘)与实盘LLM审判(回测盲区)。"
        )
        rule = {"config": RULE_V1, "market_filter_default": False, "week_halving_default": False}
        v = brain.save_version("v1", rule, changelog, metrics=metrics, activate=True)
        print(f"\n[大脑] 已落 strategy_versions:{v.version} active={v.is_active} created={v.created_at}")
        print(f"       样本外 PF={metrics['out_sample']['pf']:.3f} 总收益={metrics['out_sample']['total_ret']:.2%}")


if __name__ == "__main__":
    main(commit="--commit" in sys.argv)
