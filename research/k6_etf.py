"""K6 · ETF 宽基/行业双动量轮动战役 runner(策略族谱 §2-②/候选二)。

预注册依据:`research/k6_etf_report.md`(池子七只 / 规则 / 网格 / 对照臂 / 判决口径
全预注册,本文件不改预注册)。**生产零改动**:只读生产层数据封装(`_call` 限频包装、
`neckline.data.adjust.apply_qfq` 前复权),新数据只落 `research/_cache/etf_*`,不进
生产湖、不碰生产表。

跑法:
    source .venv/bin/activate
    python research/k6_etf.py              # 用缓存(无则拉数),跑全部臂,打印报告块
    python research/k6_etf.py --refresh    # 强制重拉 TuShare 落缓存

设计口径(报告 §0/§1 登记):
- 数据:`fund_daily`(不复权 OHLC)+ `fund_adj`(复权因子),前复权 = qfq(收/开)。
  ETF 价格不可得区段【不】用指数顶替(池内该期可交易标的动态处理,早期池小如实登记)。
- 主日历 = 510300 交易日(SSE 全程覆盖);月末 = 每 YYYYMM 内最后交易日(信号日);
  执行日 = 次月首个交易日(开盘价成交,信号用月末收盘 → 次日开盘执行,无前视)。
- 动量:月末收盘(前复权)按「L 个月末步长」算比值。绝对闸门 = 510300 过去 12 月末
  收益 <0 → 全仓货基;否则相对动量取过去 L 月收益最高 2 只等权(货基不参与排名)。
- 可排名条件:该 ETF 在信号月末 index j 及 j-L 均有月末收盘(即上市 ≥ L 个月末)。
- 费用:双边合计 0.1%/次调仓 → 成本 = 组合净值 × 0.001 × 单边换手(单边换手 =
  0.5·Σ|Δw|;整仓切换 Σ|Δw|=2 → 单边=1.0 → 成本 0.1%)。
- 日频净值:执行日开盘换仓(扣费)→ 每日收盘盯市;最大回撤在日净值曲线上算。
- 窗口:样本内 2016-02(受 12 月末闸门 + 池内标的约束的实际起点)~ 2024-12;
  样本外 2025-01 ~ 2026-07;2026 分段 = 2026-01 ~ 2026-07-24;逐年 + MDD + whipsaw。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.data.adjust import apply_qfq  # noqa: E402
from neckline.data.tushare_client import _call  # noqa: E402  (复用限频/重试包装)

CACHE_DIR = Path(__file__).resolve().parent / "_cache"
PANEL_PATH = CACHE_DIR / "etf_panel.parquet"
RAW_DAILY_PATH = CACHE_DIR / "etf_fund_daily.parquet"
RAW_ADJ_PATH = CACHE_DIR / "etf_fund_adj.parquet"

START = "20150101"
END = "20260725"

# —— 预注册标的池(七只,定死) ————————————————————————————————
CASH_ETF = "511990.SH"  # 货基(现金腿,不参与相对动量排名)
RISK_ETFS = [
    "510300.SH",  # 沪深300(亦作绝对动量闸门 + 基准)
    "510500.SH",  # 中证500
    "159915.SZ",  # 创业板
    "588000.SH",  # 科创50
    "512890.SH",  # 红利低波
    "518880.SH",  # 黄金
]
POOL = RISK_ETFS + [CASH_ETF]
GATE_ETF = "510300.SH"

NAME = {
    "510300.SH": "沪深300", "510500.SH": "中证500", "159915.SZ": "创业板",
    "588000.SH": "科创50", "512890.SH": "红利低波", "518880.SH": "黄金",
    "511990.SH": "货基",
}

L_GRID = [1, 3, 6, 12]
GATE_MONTHS = 12
COST_RATE = 0.001          # 双边合计 0.1%/次整仓切换
TOP_N = 2

# 货基现金腿的年化 carry(显式登记的假设)。数据坑:511990(华宝添益货币ETF)
# NAV 恒 ~100 且 fund_adj 因子恒 =1.0——TuShare 不把货基收益计入复权因子(收益以
# 额外份额/现金分配),故直接用其 qfq 序列做现金腿会把 carry 当 0(系统性低估:
# 各臂 41% 月份在现金)。用显式常数年化 carry 合成货基总收益序列替代;主口径 2.0%
# (A股货基/逆回购 2016-2026 中枢约 2-2.5%),并在敏感性节报 {0.0, 2.0, 2.5} 三档。
CASH_CARRY_ANNUAL = 0.020
_TRADING_DAYS_YR = 244

# 窗口(实际起点见 §0 登记)
IN_START, IN_END = "20160101", "20241231"
OUT_START, OUT_END = "20250101", "20261231"
Y2026_START, Y2026_END = "20260101", "20261231"


# ══════════════════════════════════════════════════════════════════════
# 数据层:拉取 + 前复权 + 缓存
# ══════════════════════════════════════════════════════════════════════

# fund_adj 单次调用有 ~2600 行硬上限(实测:全历史一次调用只回最近 2600 行,
# 2015 段被截断 → adj_factor 缺失 → qfq 变 null)。按日期窗口分段拉取绕开上限。
_ADJ_WINDOWS = [("20150101", "20191231"), ("20200101", "20261231")]


def pull_raw() -> tuple[pl.DataFrame, pl.DataFrame]:
    """逐票拉 fund_daily(单次全历史,实测 2808 行无截断)/ fund_adj(分段绕开
    2600 行/次上限,dedupe),拼成两张长表;对每票登记实际起点。"""
    daily_frames, adj_frames = [], []
    print("== 拉取 TuShare fund_daily / fund_adj(7 只 ETF)==")
    for code in POOL:
        rd = _call("fund_daily", ts_code=code, start_date=START, end_date=END)
        if not rd.ok:
            raise RuntimeError(f"fund_daily {code} 失败: {rd.reason}")
        dd = pl.from_pandas(rd.data)
        daily_frames.append(dd)

        adj_parts = []
        for w0, w1 in _ADJ_WINDOWS:
            ra = _call("fund_adj", ts_code=code, start_date=w0, end_date=w1)
            if not ra.ok:
                raise RuntimeError(f"fund_adj {code} [{w0},{w1}] 失败: {ra.reason}")
            if len(ra.data) > 0:
                adj_parts.append(pl.from_pandas(ra.data))
        aa = (pl.concat(adj_parts, how="vertical_relaxed")
              .unique(subset=["ts_code", "trade_date"]) if adj_parts
              else pl.DataFrame(schema={"ts_code": pl.Utf8, "trade_date": pl.Utf8,
                                        "adj_factor": pl.Float64}))
        adj_frames.append(aa)
        print(f"  {code} {NAME[code]}: daily={len(dd)} 行 起={dd['trade_date'].min()} "
              f"止={dd['trade_date'].max()} | adj={len(aa)} 行 起={aa['trade_date'].min()}")
    daily = pl.concat(daily_frames, how="vertical_relaxed")
    adj = pl.concat(adj_frames, how="vertical_relaxed")
    return daily, adj


def build_panel(refresh: bool = False) -> pl.DataFrame:
    """构建前复权面板并缓存。列:ts_code, trade_date(str YYYYMMDD), open, close,
    close_qfq, open_qfq。"""
    if PANEL_PATH.exists() and not refresh:
        print(f"== 载入缓存 {PANEL_PATH.name} ==")
        return pl.read_parquet(PANEL_PATH)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    daily, adj = pull_raw()
    daily.write_parquet(RAW_DAILY_PATH)
    adj.write_parquet(RAW_ADJ_PATH)

    # 规范 trade_date 为 str;合并 adj_factor
    daily = daily.with_columns(pl.col("trade_date").cast(pl.Utf8))
    adj = adj.with_columns(pl.col("trade_date").cast(pl.Utf8)).select(
        ["ts_code", "trade_date", "adj_factor"])
    merged = daily.join(adj, on=["ts_code", "trade_date"], how="left")
    # adj_factor 是分红除息日跳变的分段常数;任何残留缺口按票内前向→后向填充
    # (前向 = 上一次已知因子沿用到下次分红,是正确重建;后向仅补最早缺口)。
    merged = (merged.sort(["ts_code", "trade_date"])
              .with_columns(pl.col("adj_factor").forward_fill().backward_fill()
                            .over("ts_code")))
    n_null = merged.filter(pl.col("adj_factor").is_null()).height
    if n_null:
        print(f"  [warn] 填充后仍有 {n_null} 行 adj_factor 缺失")
    # 前复权(复用生产层 apply_qfq;latest_adj_factor 按每票全历史最新一条)
    qfq = apply_qfq(merged, price_cols=("open", "close"))
    panel = qfq.select([
        "ts_code", "trade_date", "open", "close", "open_qfq", "close_qfq",
    ]).sort(["ts_code", "trade_date"])
    panel.write_parquet(PANEL_PATH)
    print(f"== 面板缓存 → {PANEL_PATH} ({len(panel)} 行) ==")
    return panel


# ══════════════════════════════════════════════════════════════════════
# 日历 / 月末 / 宽表
# ══════════════════════════════════════════════════════════════════════

@dataclass
class MarketData:
    master_dates: list[str]                        # 510300 交易日(升序)
    months: list[str]                              # YYYYMM 升序
    month_end: dict[str, str]                      # YYYYMM -> 月内最后交易日
    month_first: dict[str, str]                    # YYYYMM -> 月内首个交易日
    close_qfq: dict[str, dict[str, float]]         # date -> {ts_code: close_qfq}(前向填充)
    open_qfq: dict[str, dict[str, float]]          # date -> {ts_code: open_qfq}
    me_close: dict[str, dict[str, float]]          # 月末收盘(前复权),date -> {ts: close}
    listed_start: dict[str, str]                   # ts_code -> 首个有价日


def apply_cash_carry(md: "MarketData", carry: float) -> None:
    """就地把货基现金腿(CASH_ETF)的开/收盘覆盖为按 carry 年化复利的合成总收益序列
    (起点 100,按主日历天数复利)。carry=0 → 恒 100(等价原始 flat NAV)。"""
    r_daily = (1.0 + carry) ** (1.0 / _TRADING_DAYS_YR) - 1.0
    for k, d in enumerate(md.master_dates):
        p = 100.0 * (1.0 + r_daily) ** k
        if d in md.close_qfq:
            md.close_qfq[d][CASH_ETF] = p
        if d in md.open_qfq:
            md.open_qfq[d][CASH_ETF] = p
        if d in md.me_close:
            md.me_close[d][CASH_ETF] = p


def prepare(panel: pl.DataFrame, cash_carry: float = CASH_CARRY_ANNUAL) -> MarketData:
    master_dates = sorted(
        panel.filter(pl.col("ts_code") == GATE_ETF)["trade_date"].to_list())
    # YYYYMM 分组
    months_seen: list[str] = []
    month_end: dict[str, str] = {}
    month_first: dict[str, str] = {}
    for d in master_dates:
        ym = d[:6]
        if ym not in month_first:
            month_first[ym] = d
            months_seen.append(ym)
        month_end[ym] = d
    months = sorted(months_seen)

    # 宽表:date × ts_code,前向填充(仅在上市区间内;上市前保持 null,不访问)
    wide_c = panel.pivot(values="close_qfq", index="trade_date", on="ts_code").sort("trade_date")
    wide_o = panel.pivot(values="open_qfq", index="trade_date", on="ts_code").sort("trade_date")
    ffill_cols = [c for c in wide_c.columns if c != "trade_date"]
    wide_c = wide_c.with_columns([pl.col(c).forward_fill() for c in ffill_cols])
    wide_o = wide_o.with_columns([pl.col(c).forward_fill() for c in ffill_cols])

    def to_dict(w: pl.DataFrame) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        cols = [c for c in w.columns if c != "trade_date"]
        for row in w.iter_rows(named=True):
            d = row["trade_date"]
            out[d] = {c: row[c] for c in cols if row[c] is not None}
        return out

    close_qfq = to_dict(wide_c)
    open_qfq = to_dict(wide_o)

    # 月末收盘(用于动量信号):每 YYYYMM 的 month_end 日的 close_qfq
    me_close: dict[str, dict[str, float]] = {}
    for ym in months:
        d = month_end[ym]
        me_close[d] = close_qfq.get(d, {})

    # 首个有价日(登记实际起点)
    listed_start: dict[str, str] = {}
    for code in POOL:
        sub = panel.filter(pl.col("ts_code") == code)["trade_date"]
        if len(sub) > 0:
            listed_start[code] = sub.min()

    md = MarketData(master_dates, months, month_end, month_first,
                    close_qfq, open_qfq, me_close, listed_start)
    apply_cash_carry(md, cash_carry)
    return md


# ══════════════════════════════════════════════════════════════════════
# 信号
# ══════════════════════════════════════════════════════════════════════

def gate_on(md: MarketData, i: int) -> bool | None:
    """绝对动量闸门:510300 过去 12 月末收益 <0 → True(退货基)。
    历史不足(i<12)返回 None(该月不产生信号)。"""
    if i < GATE_MONTHS:
        return None
    d_now = md.month_end[md.months[i]]
    d_past = md.month_end[md.months[i - GATE_MONTHS]]
    c_now = md.me_close.get(d_now, {}).get(GATE_ETF)
    c_past = md.me_close.get(d_past, {}).get(GATE_ETF)
    if c_now is None or c_past is None:
        return None
    return (c_now / c_past - 1.0) < 0.0


def rank_top(md: MarketData, i: int, L: int) -> list[str]:
    """相对动量:过去 L 月末收益最高的 TOP_N 只风险 ETF(等价可排名条件:i-L≥0
    且该票在 i 与 i-L 月末均有前复权收盘)。返回按动量降序的 ts_code 列表。"""
    if i - L < 0:
        return []
    d_now = md.month_end[md.months[i]]
    d_past = md.month_end[md.months[i - L]]
    now = md.me_close.get(d_now, {})
    past = md.me_close.get(d_past, {})
    scored = []
    for code in RISK_ETFS:
        c_now, c_past = now.get(code), past.get(code)
        if c_now is None or c_past is None:
            continue
        scored.append((code, c_now / c_past - 1.0))
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return [c for c, _ in scored[:TOP_N]]


def target_weights_dm(md: MarketData, i: int, L: int) -> dict[str, float] | None:
    """H-ETF1 目标权重。None = 无有效信号(历史不足)。"""
    g = gate_on(md, i)
    if g is None:
        return None
    if g:
        return {CASH_ETF: 1.0}
    top = rank_top(md, i, L)
    if len(top) < TOP_N:
        return None
    return {c: 1.0 / TOP_N for c in top}


def target_weights_ew(md: MarketData, i: int) -> dict[str, float] | None:
    """对照臂:等权持有当期可交易的全部风险 ETF(月度再平衡)。"""
    if i < GATE_MONTHS:
        return None  # 与主臂对齐起点,可比
    d_now = md.month_end[md.months[i]]
    now = md.me_close.get(d_now, {})
    live = [c for c in RISK_ETFS if now.get(c) is not None]
    if not live:
        return None
    w = 1.0 / len(live)
    return {c: w for c in live}


# ══════════════════════════════════════════════════════════════════════
# 日频组合模拟
# ══════════════════════════════════════════════════════════════════════

@dataclass
class SimResult:
    dates: list[str]
    equity: list[float]
    rebalances: list[tuple[str, dict[str, float], float]]  # (exec_date, weights, cost)
    total_cost: float = 0.0
    n_trades: int = 0                                       # 换手>0 的调仓次数
    total_oneway_turnover: float = 0.0


def simulate(md: MarketData, weight_fn, buy_hold: bool = False) -> SimResult:
    """按「月末信号 → 次月首日开盘执行」模拟日频净值。
    weight_fn(i) -> 目标权重 dict 或 None(无信号跳过)。
    buy_hold=True:只在首次有信号时建仓,其后不再调仓(基准用)。"""
    # 构建执行事件:月 i 末信号 → 次月(i+1)首日执行
    exec_map: dict[str, dict[str, float]] = {}
    last_w: dict[str, float] | None = None
    first_exec: str | None = None
    for i in range(len(md.months) - 1):
        w = weight_fn(i)
        if w is None:
            continue
        exec_date = md.month_first[md.months[i + 1]]
        if buy_hold:
            if first_exec is None:
                exec_map[exec_date] = w
                first_exec = exec_date
            # buy_hold:后续不再写事件
        else:
            exec_map[exec_date] = w
            if first_exec is None:
                first_exec = exec_date
        last_w = w
    _ = last_w

    dates_out, equity_out = [], []
    rebs: list[tuple[str, dict[str, float], float]] = []
    total_cost = 0.0
    n_trades = 0
    total_oneway = 0.0

    equity = 1.0
    shares: dict[str, float] = {}
    holding = False
    if first_exec is None:
        return SimResult([], [], [], 0.0, 0, 0.0)

    for d in md.master_dates:
        if d < first_exec:
            continue
        if d in exec_map:
            w_target = exec_map[d]
            opn = md.open_qfq.get(d, {})
            # 缺开盘价的目标标的:退化用收盘(极少见);仍缺则跳过该标的
            def px(c: str) -> float | None:
                return opn.get(c) or md.close_qfq.get(d, {}).get(c)

            if holding:
                val_open = sum(shares[c] * (px(c) or 0.0) for c in shares)
            else:
                val_open = equity
            w_before = ({c: shares[c] * (px(c) or 0.0) / val_open for c in shares}
                        if holding and val_open > 0 else {})
            codes = set(w_target) | set(w_before)
            oneway = 0.5 * sum(abs(w_target.get(c, 0.0) - w_before.get(c, 0.0)) for c in codes)
            cost = val_open * COST_RATE * oneway
            val_after = val_open - cost
            new_shares: dict[str, float] = {}
            for c, wt in w_target.items():
                if wt <= 0:
                    continue
                p = px(c)
                if p and p > 0:
                    new_shares[c] = wt * val_after / p
            shares = new_shares
            equity = val_after
            holding = True
            if oneway > 1e-9:
                total_cost += cost
                total_oneway += oneway
                n_trades += 1
                rebs.append((d, dict(w_target), cost))
        # 收盘盯市
        if holding:
            cl = md.close_qfq.get(d, {})
            equity = sum(shares[c] * (cl.get(c) or 0.0) for c in shares)
        dates_out.append(d)
        equity_out.append(equity)

    return SimResult(dates_out, equity_out, rebs, total_cost, n_trades, total_oneway)


# ══════════════════════════════════════════════════════════════════════
# 指标
# ══════════════════════════════════════════════════════════════════════

def _slice(res: SimResult, start: str, end: str) -> tuple[list[str], list[float]]:
    ds, es = [], []
    for d, e in zip(res.dates, res.equity):
        if start <= d <= end:
            ds.append(d); es.append(e)
    return ds, es


def seg_return(res: SimResult, start: str, end: str) -> float | None:
    ds, es = _slice(res, start, end)
    if len(es) < 2:
        return None
    return es[-1] / es[0] - 1.0


def max_drawdown(equity: list[float]) -> float:
    peak = -1e18
    mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, e / peak - 1.0)
    return mdd


def cagr(res: SimResult, start: str, end: str) -> float | None:
    ds, es = _slice(res, start, end)
    if len(es) < 2:
        return None
    years = (int(ds[-1][:4]) * 365 + int(ds[-1][4:6]) * 30 + int(ds[-1][6:8])
             - (int(ds[0][:4]) * 365 + int(ds[0][4:6]) * 30 + int(ds[0][6:8]))) / 365.0
    if years <= 0:
        return None
    return (es[-1] / es[0]) ** (1.0 / years) - 1.0


def yearly_returns(res: SimResult) -> dict[str, float]:
    out: dict[str, float] = {}
    years = sorted({d[:4] for d in res.dates})
    for y in years:
        r = seg_return(res, f"{y}0101", f"{y}1231")
        if r is not None:
            out[y] = r
    return out


@dataclass
class ArmReport:
    label: str
    res: SimResult
    full: float | None = None
    cagr_full: float | None = None
    in_s: float | None = None
    out_s: float | None = None
    y2026: float | None = None
    mdd: float | None = None
    yearly: dict[str, float] = field(default_factory=dict)


def report_arm(label: str, res: SimResult) -> ArmReport:
    a = res.dates[0] if res.dates else START
    b = res.dates[-1] if res.dates else END
    return ArmReport(
        label=label, res=res,
        full=seg_return(res, a, b),
        cagr_full=cagr(res, a, b),
        in_s=seg_return(res, IN_START, IN_END),
        out_s=seg_return(res, OUT_START, OUT_END),
        y2026=seg_return(res, Y2026_START, Y2026_END),
        mdd=max_drawdown(res.equity),
        yearly=yearly_returns(res),
    )


def pct(x: float | None) -> str:
    return "  n/a " if x is None else f"{x * 100:+7.2f}%"


# ══════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="强制重拉 TuShare 落缓存")
    args = ap.parse_args()

    panel = build_panel(refresh=args.refresh)
    md = prepare(panel)

    print("\n===== §0 数据侦察登记 =====")
    for code in POOL:
        ls = md.listed_start.get(code, "n/a")
        n = panel.filter(pl.col("ts_code") == code).height
        print(f"  {code} {NAME[code]:<6} 起={ls}  行数={n}")
    print(f"  主日历(510300)交易日数={len(md.master_dates)}  "
          f"月份数={len(md.months)}  首月={md.months[0]}  末月={md.months[-1]}")
    first_signal_i = GATE_MONTHS
    print(f"  首个有效信号月 index={first_signal_i} = {md.months[first_signal_i]} "
          f"→ 首执行日 = {md.month_first[md.months[first_signal_i+1]]}")
    print(f"  货基现金腿 carry(主口径)= {CASH_CARRY_ANNUAL:.1%}/年(合成;"
          f"511990 fund_adj 恒=1.0 不含货基收益,见 §0 数据坑登记)")

    # 各臂
    arms: list[ArmReport] = []
    for L in L_GRID:
        res = simulate(md, lambda i, L=L: target_weights_dm(md, i, L))
        arms.append(report_arm(f"H-ETF1 L={L}", res))
    ew = report_arm("对照·等权风险ETF", simulate(md, lambda i: target_weights_ew(md, i)))
    # 基准与主臂/对照臂同起点(首个有效信号月 2016-02),apples-to-apples。
    bench = report_arm("基准·持有沪深300", simulate(
        md, lambda i: ({GATE_ETF: 1.0} if i >= GATE_MONTHS else None), buy_hold=True))

    print("\n===== §主结果:各臂全局指标 =====")
    hdr = f"{'臂':<20}{'全期':>10}{'年化':>10}{'样本内16-24':>12}{'样本外25-26':>12}{'2026段':>10}{'MDD':>10}"
    print(hdr)
    for a in arms + [ew, bench]:
        print(f"{a.label:<20}{pct(a.full):>10}{pct(a.cagr_full):>10}"
              f"{pct(a.in_s):>12}{pct(a.out_s):>12}{pct(a.y2026):>10}{pct(a.mdd):>10}")

    print("\n===== §逐年收益 =====")
    years = sorted({y for a in arms + [ew, bench] for y in a.yearly})
    print(f"{'臂':<20}" + "".join(f"{y:>9}" for y in years))
    for a in arms + [ew, bench]:
        print(f"{a.label:<20}" + "".join(
            (f"{a.yearly[y]*100:>8.1f}%" if y in a.yearly else f"{'—':>9}") for y in years))

    print("\n===== §whipsaw / 费用 =====")
    print(f"{'臂':<20}{'调仓次数':>10}{'年均':>8}{'累计单边换手':>14}{'累计费用(净值)':>16}{'费用/终值%':>12}")
    span_years = (int(md.master_dates[-1][:4]) - int(md.months[GATE_MONTHS][:4])) or 1
    for a in arms + [ew]:
        r = a.res
        final_eq = r.equity[-1] if r.equity else 1.0
        fee_share = r.total_cost / final_eq * 100 if final_eq else 0.0
        print(f"{a.label:<20}{r.n_trades:>10}{r.n_trades/span_years:>8.1f}"
              f"{r.total_oneway_turnover:>14.2f}{r.total_cost:>16.4f}{fee_share:>12.3f}")

    print("\n===== §信号诊断(闸门触发 / 持仓分布) =====")
    for L in L_GRID:
        cash_months = 0
        hold_count: dict[str, int] = {}
        total_signals = 0
        for i in range(GATE_MONTHS, len(md.months) - 1):
            w = target_weights_dm(md, i, L)
            if w is None:
                continue
            total_signals += 1
            if list(w.keys()) == [CASH_ETF]:
                cash_months += 1
            for c in w:
                if c != CASH_ETF:
                    hold_count[c] = hold_count.get(c, 0) + 1
        top = sorted(hold_count.items(), key=lambda kv: kv[1], reverse=True)
        holds = " ".join(f"{NAME[c]}:{n}" for c, n in top)
        print(f"  L={L:<2} 信号月={total_signals} 全仓货基月={cash_months} "
              f"({cash_months/total_signals*100:.0f}%) | 持仓频次 {holds}")

    print("\n===== §现金腿 carry 敏感性(EW/HS300 不含货基,不受影响) =====")
    print(f"{'carry':>7} | " + " ".join(f"{'L='+str(L):>10}" for L in L_GRID)
          + f"{'等权对照':>12}")
    for cc in [0.0, 0.020, 0.025]:
        md_cc = prepare(panel, cash_carry=cc)
        row = []
        for L in L_GRID:
            r = simulate(md_cc, lambda i, L=L: target_weights_dm(md_cc, i, L))
            row.append(seg_return(r, r.dates[0], r.dates[-1]))
        ewf = ew.full
        print(f"{cc:>6.1%} | " + " ".join(pct(x) for x in row) + f"{pct(ewf):>12}")
    print("  (等权对照全期 = 见上表;判据看各 L 是否越过等权对照线)")

    print("\n===== §判决口径速览(主口径 carry=2.0%) =====")
    for a in arms:
        beat_bench = (a.full is not None and bench.full is not None and a.full > bench.full)
        beat_ew = (a.full is not None and ew.full is not None and a.full > ew.full)
        surv2026 = (a.y2026 is not None and a.y2026 >= 0)
        verdict = "过" if (beat_bench and beat_ew and surv2026) else "否决/降级"
        print(f"  {a.label}: vs沪深300={'胜' if beat_bench else '负'} | "
              f"vs等权={'胜' if beat_ew else '负'} | 2026非负={'是' if surv2026 else '否'} → {verdict}")

    n_pass = sum(
        1 for a in arms
        if a.full is not None and bench.full is not None and ew.full is not None
        and a.full > bench.full and a.full > ew.full
        and a.y2026 is not None and a.y2026 >= 0)
    war = ("否决/降级(仅单一 L 过双基准 → 触发预注册「仅单一 L 有效即判过拟合弃」)"
           if n_pass <= 1 else "多档同向,继续法庭档消融")
    print(f"\n  【战役级裁决】过双基准+2026非负的 L 档数 = {n_pass}/{len(L_GRID)} → {war}")
    print("  (且该单档对等权的增量在货基 carry 假设噪声内、全靠 2025 单一 regime,详见报告 §2)")


if __name__ == "__main__":
    main()
