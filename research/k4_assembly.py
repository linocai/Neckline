"""K4 机械半身组装 + 联合削尾验收(路线图第三步)。

**性质**:非新假设战役——工程组装 + **联合验收**。六层避坑规则各自的单层效果已由
K4 前置三役证明(`k4_pre_report.md` / `k4_pre2_report.md` / `k4_pre3_report.md`),
本 runner 出的新信息只有三样:
    ① 并集后的**联合削尾效果**(A / A+B 剔除后,剩余池左尾 p5 + 次日跌停暴露 vs 全体);
    ② **覆盖率**(硬剔占域 / 硬剔+标注占域,防"剔到无票可选");
    ③ 规则间**重叠度**(两两命中占域比 + 各规则单独命中占域比,情报包排序用)。

**规格(k4_assembly_report.md §1,v3 判决集,用户已批"全部按判决来")**:
  A 硬剔(候选池级,机器执行):A1 换手>10%(H2)/ A2 题材持续≥4天(H6)/
     A3 年线下涨停日(K3臂④+战役三)/ A4 = base 域既有卫生线(即 base_expr 本身)。
  B 回避标注(减分,机器不禁止,情报包展示):B1 量能堆积大涨(H1 cnt3≥2&ret≥5%&放量)/
     B2 双金叉态(H4)/ B3 题材持续2-3天(H6)/ B4 追强大红(close>ma20 & ret_1d>5%,H5)。

**特征计算全部照抄三役 runner,不重新发明**:
  · cnt3 = `k4p_common.add_k4p_features` 的 `vol_above_ma20_cnt3`;
  · 双金叉态 state4 = `k4p_h4_cross.add_macd_kdj`(MACD 12/26/9 + KDJ 9/3/3,warmup 34);
  · 题材持续天数 persist = `k4p_h6_theme.industry_persistence`(top20% 行业中位数强度日,
    连续天数),此处改 **left-join** 保留全部域行(H6 是 inner join 只留强度日成员);
  · 年线下 TREND_BELOW / 涨停 = `k4p_h7_bounce`(ma250 + is_limit_up,源 limit_derived)。

**度量口径沿三役**(k4p_common:T+1 开盘 fwd;净扣双边 30bp;左尾 p5/p10;
  次日/持有3日跌停暴露;2026 分段单列;hold_table 与 eventstudy 逐位对拍)。
**生产零改动**:未碰 `neckline/strategy/`;底面板用 k3_panel(≤2026-07-17,与 panel_full
  逐位可比,含 ma250)。runner 独立可重跑:`python research/k4_assembly.py`
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.research.eventstudy import event_study  # noqa: E402
from k4p_common import (  # noqa: E402
    add_k4p_features, base_expr, oneword_event_expr, hold_table, exposure_row, fmt, year_2026_expr,
)
from k4p_h4_cross import add_macd_kdj  # noqa: E402
from k4p_h6_theme import load_industry_map, industry_persistence  # noqa: E402

K3_PANEL = Path(__file__).resolve().parent / "_cache" / "k3_panel.parquet"
FROZEN_END = date(2026, 7, 17)          # 战役一/二/三冻结窗(与 panel_full 逐位可比)

# —— 趋势轴(照抄 k4p_h7_bounce)——
TREND_BELOW = pl.col("ma250").is_not_null() & ~((pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up"))

# —— 规则参数(全部钉在三役判决口径,不新造)——
A1_TURNOVER_HI = 10.0                    # H2:换手 >10%(turnover_rate 单位为百分数)
A2_PERSIST_MIN = 4                       # H6:题材持续 ≥4 天
B1_UP, B1_MULT, B1_CNT3 = 0.05, 1.5, 2   # H1 主判决格:ret≥5% & vol>vol_ma20×1.5 & cnt3≥2
B4_UP = 0.05                             # H5:>5% 大红


def load_panel() -> pl.DataFrame:
    """k3_panel 切至冻结窗(承战役三 §R0:与 panel_full 逐位可比,末周前瞻收益完整)。"""
    return pl.read_parquet(K3_PANEL).filter(pl.col("trade_date") <= FROZEN_END)


def build_features(panel: pl.DataFrame) -> pl.DataFrame:
    """在全 7.8M 面板上叠加三役所需特征(EMA/rolling/行业强度须在全序列上算,不能先切域)。"""
    # H1 cnt3 + 次日 OHLC + 跌停暴露(add_k4p_features 幂等重算 k3_panel 已有的 fwd_ld_*)。
    df = add_k4p_features(panel)
    # H4 MACD/KDJ 四态(逐票 EMA 递推,warmup 34)。
    df = add_macd_kdj(df)
    # H6 行业静态映射 + 逐日强度日持续天数(top20%),left-join 保留全部域行。
    imap = load_industry_map()
    df = df.join(imap, on="ts_code", how="left")
    persist = industry_persistence(df, q=0.80)                  # (trade_date, industry, persist)
    df = df.join(persist, on=["trade_date", "industry"], how="left")
    return df


def add_rule_masks(dom: pl.DataFrame) -> pl.DataFrame:
    """在**基础域**上加 7 条规则命中列(A1-A3 硬剔 / B1-B4 标注)。A4=base_expr 本身,无独立列。"""
    persist = pl.col("persist")
    return dom.with_columns(
        # —— A 硬剔 ——
        (pl.col("turnover_rate") > A1_TURNOVER_HI).fill_null(False).alias("A1_turnover"),
        (persist >= A2_PERSIST_MIN).fill_null(False).alias("A2_theme4"),
        (TREND_BELOW & pl.col("is_limit_up")).fill_null(False).alias("A3_belowlu"),
        # —— B 标注 ——
        (
            (pl.col("vol_above_ma20_cnt3") >= B1_CNT3)
            & (pl.col("ret_1d") >= B1_UP)
            & (pl.col("vol") > pl.col("vol_ma20") * B1_MULT)
            & ~oneword_event_expr()
        ).fill_null(False).alias("B1_stack"),
        (pl.col("state4") == "①双金叉态").fill_null(False).alias("B2_dualcross"),
        ((persist >= 2) & (persist <= 3)).fill_null(False).alias("B3_theme23"),
        (
            (pl.col("close") > pl.col("ma20")) & (pl.col("ret_1d") > B4_UP)
        ).fill_null(False).alias("B4_chasered"),
    )


RULE_COLS = ["A1_turnover", "A2_theme4", "A3_belowlu", "B1_stack", "B2_dualcross", "B3_theme23", "B4_chasered"]
A_COLS = ["A1_turnover", "A2_theme4", "A3_belowlu"]
B_COLS = ["B1_stack", "B2_dualcross", "B3_theme23", "B4_chasered"]


def _hit_any(cols: List[str]) -> pl.Expr:
    e = pl.col(cols[0])
    for c in cols[1:]:
        e = e | pl.col(c)
    return e


def layer_metrics(df: pl.DataFrame, label: str, hold: int) -> dict:
    """一层一行:hold_table(n/win/net/p5/p10) + exposure_row(next_ld/hold3_ld)。"""
    ht = hold_table(df, (hold,)).to_dicts()[0]
    ex = exposure_row(df, label)
    return {
        "层": label, "n": ht["n"], "win": ht["win_rate"], "net": ht["mean_net"],
        "p5": ht["p5_net"], "p10": ht["p10_net"],
        "next_ld": ex["next_ld_rate"], "hold3_ld": ex["hold3_ld_rate"],
    }


def three_layers(dom: pl.DataFrame) -> Dict[str, pl.DataFrame]:
    a_hit = _hit_any(A_COLS)
    ab_hit = _hit_any(RULE_COLS)
    return {
        "全体(域内)": dom,
        "硬剔后(A)": dom.filter(~a_hit),
        "硬剔+标注后(A+B)": dom.filter(~ab_hit),
    }


def layer_table(dom: pl.DataFrame, hold: int) -> pl.DataFrame:
    return pl.DataFrame([layer_metrics(v, k, hold) for k, v in three_layers(dom).items()])


def coverage(dom: pl.DataFrame) -> pl.DataFrame:
    """覆盖率:硬剔占域 / 硬剔+标注占域(分母=全部基础域行,即候选宇宙)。另附 buyable 子集口径。"""
    n = dom.height
    n_buy = dom.filter(pl.col("fwd_buyable")).height
    a_hit = _hit_any(A_COLS)
    ab_hit = _hit_any(RULE_COLS)
    b_only = _hit_any(B_COLS) & ~a_hit
    na = dom.filter(a_hit).height
    nab = dom.filter(ab_hit).height
    nb_only = dom.filter(b_only).height
    na_buy = dom.filter(a_hit & pl.col("fwd_buyable")).height
    nab_buy = dom.filter(ab_hit & pl.col("fwd_buyable")).height
    return pl.DataFrame([
        {"口径": "全部域行", "域行数": n, "硬剔A命中": na, "硬剔占域%": 100 * na / n,
         "B独占(非A)": nb_only, "A+B命中": nab, "A+B占域%": 100 * nab / n, "剩余可选%": 100 * (n - nab) / n},
        {"口径": "buyable子集", "域行数": n_buy, "硬剔A命中": na_buy, "硬剔占域%": 100 * na_buy / n_buy,
         "B独占(非A)": -1, "A+B命中": nab_buy, "A+B占域%": 100 * nab_buy / n_buy, "剩余可选%": 100 * (n_buy - nab_buy) / n_buy},
    ])


def overlap_matrix(dom: pl.DataFrame) -> pl.DataFrame:
    """重叠度:各规则单独命中占域比(对角)+ 两两联合命中占域比(下三角对称)。单位 %。"""
    n = dom.height
    short = {"A1_turnover": "A1换手", "A2_theme4": "A2题材4", "A3_belowlu": "A3线下涨停",
             "B1_stack": "B1堆积", "B2_dualcross": "B2双金叉", "B3_theme23": "B3题材23", "B4_chasered": "B4追强"}
    aggs = []
    for c in RULE_COLS:
        aggs.append((pl.col(c).sum() / n * 100).alias(f"__solo__{c}"))
    for i, ci in enumerate(RULE_COLS):
        for cj in RULE_COLS[i + 1:]:
            aggs.append(((pl.col(ci) & pl.col(cj)).sum() / n * 100).alias(f"__pair__{ci}__{cj}"))
    got = dom.select(aggs).to_dicts()[0]
    rows = []
    for ci in RULE_COLS:
        row = {"规则": short[ci]}
        for cj in RULE_COLS:
            if ci == cj:
                row[short[cj]] = got[f"__solo__{ci}"]
            else:
                key = f"__pair__{ci}__{cj}" if f"__pair__{ci}__{cj}" in got else f"__pair__{cj}__{ci}"
                row[short[cj]] = got[key]
        rows.append(row)
    return pl.DataFrame(rows)


def stratified(dom: pl.DataFrame, group_col: str, hold: int = 3) -> pl.DataFrame:
    """分层(year/board):每层×每 group 值一行 net + p5 + next_ld,看削尾是否同向。"""
    layers = three_layers(dom)
    rows: List[dict] = []
    gvals = sorted([v for v in dom[group_col].unique().to_list() if v is not None])
    for gv in gvals:
        for lname, ldf in layers.items():
            sub = ldf.filter(pl.col(group_col) == gv)
            if sub.height == 0:
                continue
            ht = hold_table(sub, (hold,)).to_dicts()[0]
            ex = exposure_row(sub, lname)
            rows.append({group_col: gv, "层": lname, "n": ht["n"], "net": ht["mean_net"],
                         "p5": ht["p5_net"], "next_ld": ex["next_ld_rate"]})
    return pl.DataFrame(rows)


def close_ma20_split(dom: pl.DataFrame, hold: int = 3) -> pl.DataFrame:
    """close>ma20(追强上行子域)vs close≤ma20 上,三层削尾对照。"""
    rows: List[dict] = []
    for sub_name, sub_expr in (("close>ma20", pl.col("close") > pl.col("ma20")),
                               ("close≤ma20", pl.col("close") <= pl.col("ma20"))):
        sub = dom.filter(sub_expr)
        for lname, ldf in three_layers(sub).items():
            ht = hold_table(ldf, (hold,)).to_dicts()[0]
            ex = exposure_row(ldf, lname)
            rows.append({"子域": sub_name, "层": lname, "n": ht["n"], "net": ht["mean_net"],
                         "p5": ht["p5_net"], "next_ld": ex["next_ld_rate"]})
    return pl.DataFrame(rows)


def acceptance(dom: pl.DataFrame) -> None:
    """验收线逐条判定(①削尾+2026同向;②覆盖率;③留佐证,组合外证)。"""
    full = layer_metrics(dom, "全体", 3)
    ab = layer_metrics(dom.filter(~_hit_any(RULE_COLS)), "A+B后", 3)
    dom26 = dom.filter(year_2026_expr())
    full26 = layer_metrics(dom26, "全体26", 3)
    ab26 = layer_metrics(dom26.filter(~_hit_any(RULE_COLS)), "A+B后26", 3)

    n = dom.height
    na = dom.filter(_hit_any(A_COLS)).height
    nab = dom.filter(_hit_any(RULE_COLS)).height
    cov_a = 100 * na / n
    cov_ab = 100 * nab / n

    # ① 削尾:A+B 后 p5 不比全体更负(更干净)且 next_ld 不比全体更高(更低),全期 + 2026 同向。
    p5_ok = ab["p5"] >= full["p5"]
    ld_ok = ab["next_ld"] <= full["next_ld"]
    p5_ok26 = ab26["p5"] >= full26["p5"]
    ld_ok26 = ab26["next_ld"] <= full26["next_ld"]
    pass1 = p5_ok and ld_ok and p5_ok26 and ld_ok26
    # ② 覆盖率:硬剔 <30%,A+B <70%。
    pass2 = (cov_a < 30.0) and (cov_ab < 70.0)

    print("\n## ★ 验收线逐条判定")
    print(f"① 削尾+2026同向:")
    print(f"   全期  p5 全体 {full['p5']:.4f} → A+B {ab['p5']:.4f}(更干净={p5_ok});"
          f" next_ld 全体 {full['next_ld']:.4f} → A+B {ab['next_ld']:.4f}(更低={ld_ok})")
    print(f"   2026  p5 全体 {full26['p5']:.4f} → A+B {ab26['p5']:.4f}(更干净={p5_ok26});"
          f" next_ld 全体 {full26['next_ld']:.4f} → A+B {ab26['next_ld']:.4f}(更低={ld_ok26})")
    print(f"   → ① {'过' if pass1 else '挂'}")
    print(f"② 覆盖率:硬剔A {cov_a:.2f}%(<30 {'✓' if cov_a<30 else '✗'});"
          f" A+B {cov_ab:.2f}%(<70 {'✓' if cov_ab<70 else '✗'}) → ② {'过' if pass2 else '挂'}")
    print(f"③ K1 现役选股集合零影响:K4 纯 advisory,未碰 neckline/strategy/、现役 config 逐位不变"
          f"(git 与 config 逐位断言另证)→ 约定为过,佐证见报告 §落库")
    print(f"\n=== 联合验收总判:{'全过线(可落库)' if (pass1 and pass2) else '未过线(不落库)'} ===")
    # 供报告引用的关键数字回吐
    print(f"[METRICS] net_full_h3={full['net']:.4f} net_ab_h3={ab['net']:.4f} "
          f"p5_full={full['p5']:.4f} p5_ab={ab['p5']:.4f} ld_full={full['next_ld']:.4f} ld_ab={ab['next_ld']:.4f} "
          f"cov_a={cov_a:.2f} cov_ab={cov_ab:.2f} "
          f"p5_full26={full26['p5']:.4f} p5_ab26={ab26['p5']:.4f} ld_full26={full26['next_ld']:.4f} ld_ab26={ab26['next_ld']:.4f}")


def main() -> None:
    panel = load_panel()
    feat = build_features(panel)
    dom = add_rule_masks(feat.filter(base_expr()))

    print("# K4 机械半身组装 + 联合削尾验收 —— 结果")
    print(f"\n面板 k3_panel 切至 ≤{FROZEN_END}:{panel.height} 行;基础域(base_expr,全板块+非次新)"
          f"{dom.height} 行(buyable {dom.filter(pl.col('fwd_buyable')).height})。")

    # 口径交叉核对(承三役:hold_table.mean_net == eventstudy.event_study.mean_net)
    chk = event_study(dom, pl.col("ts_code").is_not_null(), hold_days=(3,))
    mine = hold_table(dom, (3,))
    assert abs(float(chk["mean_net"][0]) - float(mine["mean_net"][0])) < 1e-9, "口径与 eventstudy 不一致!"
    print("[口径交叉核对通过:hold_table.mean_net == eventstudy.event_study.mean_net]")

    print("\n## 覆盖率(硬剔占域 / 硬剔+标注占域)")
    print(fmt(coverage(dom), intcols=("域行数", "硬剔A命中", "B独占(非A)", "A+B命中")))

    print("\n## 三层对比 A:全期 hold=3(净扣30bp;p5/p10 左尾;next_ld/hold3_ld 跌停暴露)")
    print(fmt(layer_table(dom, 3)))
    print("\n## 三层对比 A':全期 hold=5")
    print(fmt(layer_table(dom, 5)))

    print("\n## 三层对比 B:2026 分段单列(生存视角,hold=3/5)")
    dom26 = dom.filter(year_2026_expr())
    print("\n### 2026 hold=3")
    print(fmt(layer_table(dom26, 3)))
    print("\n### 2026 hold=5")
    print(fmt(layer_table(dom26, 5)))

    print("\n## 重叠度矩阵(对角=各规则单独命中占域%;非对角=两两联合命中占域%)")
    print(fmt(overlap_matrix(dom), floatfmt="{:.2f}", intcols=()))

    print("\n## 分层 C:年份(三层 net + p5 + next_ld,hold=3)")
    print(fmt(stratified(dom, "year", 3), intcols=("n", "year")))

    print("\n## 分层 D:板块(board,三层,hold=3)")
    print(fmt(stratified(dom, "board", 3), intcols=("n",)))

    print("\n## 分层 E:close>ma20 上行子域 vs close≤ma20(三层,hold=3)")
    print(fmt(close_ma20_split(dom, 3), intcols=("n",)))

    acceptance(dom)


if __name__ == "__main__":
    main()
