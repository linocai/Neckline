#!/usr/bin/env python3
"""判分口径**真 K 线**逐位对拍(plan §五 V2-⑨-D 验收第一条的真数据那一半)。

`tests/test_eval_exit_sim.py` 在随机造数上比对了搬迁前后的 `_sim_one`;本脚本把
同一件事放到**真实 K 线**上再做一遍:读 `research/_cache/k3_panel.parquet`(已含
qfq 价 + `is_limit_down`),用真实的 (代码, 交易日) 组合造出成百上千笔入场单,
把**搬迁前的冻结副本**与**现役 `neckline/eval/exit_sim.py`** 各跑一遍,逐字段比对。

**为什么不进 CI**:`k3_panel.parquet` 是研究缓存(gitignore),CI 环境没有它;
研究缓存不该成为单测的前置依赖。**为什么还要有它**:随机造数覆盖分支,真 K 线
覆盖"真实市场长什么样"(连续跌停、长期停牌、除权跳空……),两者互补。

⚠ **2026-08-08 起本脚本默认会直接跳过**:`research/_cache/`(5.5G 研究面板缓存)随
策略研究档案迁出一并删除,`research/` 目录在本仓已不存在 —— 脚本自带的
「面板不存在 → 打印提示后跳过」分支会命中,**这是预期行为不是故障**。要真跑起来,
先重建面板再用 `--panel` 指路::

    # runner 在 ~/Lino/whynotme/Archive/Neckline量化研究档案_K2-K7/research/
    # 把 k3_panel.py + lab.py + k4p_common.py 拷回本仓 research/ 后跑(耗时以小时计)
    python -m research.k3_panel
    python scripts/smoke_eval_exit_sim.py --panel research/_cache/k3_panel.parquet

随机造数那一半(`tests/test_eval_exit_sim.py::TestFrozenPairing`)**不受影响,照常在
全量套件里跑** —— 判分口径的守门没有因为这次迁出出现缺口。

用法::

    python scripts/smoke_eval_exit_sim.py                 # 默认 800 笔
    python scripts/smoke_eval_exit_sim.py --n 2000 --seed 42
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

import polars as pl  # noqa: E402

from neckline.backtest.portfolio import ClosedTrade  # noqa: E402
from neckline.eval import exit_sim  # noqa: E402

K3_PANEL = Path(__file__).resolve().parent.parent / "research" / "_cache" / "k3_panel.parquet"


def _frozen():
    """从单测里取那份**搬迁前的冻结源**(单一事实源:冻结副本只存在一处)。"""
    from tests.test_eval_exit_sim import _FROZEN

    return _FROZEN["_sim_one"]


def _fields(rt):
    if rt is None:
        return None
    return (rt.ts_code, rt.buy_date, rt.sell_date, rt.sell_price, rt.reason, rt.exempt,
            rt.held_sessions, rt.shares, round(rt.sell_fees, 10), round(rt.pnl, 10),
            round(rt.cost_basis, 10), round(rt.pnl_pct, 12))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=800, help="对拍笔数(默认 800)")
    ap.add_argument("--seed", type=int, default=20260802)
    ap.add_argument("--panel", default=str(K3_PANEL))
    args = ap.parse_args()

    panel_path = Path(args.panel)
    if not panel_path.exists():
        print(f"[smoke] 找不到面板 {panel_path} —— 研究缓存不在,跳过真 K 线对拍。", file=sys.stderr)
        return 2

    p = pl.read_parquet(panel_path)
    need = {"ts_code", "trade_date", "open", "low", "close"}
    missing = need - set(p.columns)
    if missing:
        print(f"[smoke] 面板缺列 {missing}", file=sys.stderr)
        return 2

    cal = p.select("trade_date").unique().sort("trade_date")["trade_date"].to_list()
    cal_idx = {d: i for i, d in enumerate(cal)}
    rng = random.Random(args.seed)
    codes = sorted(set(p["ts_code"].to_list()))
    picked = rng.sample(codes, min(300, len(codes)))
    sub = p.filter(pl.col("ts_code").is_in(picked))

    pm = {}
    for (code,), g in sub.group_by(["ts_code"]):
        g = g.sort("trade_date")
        dts = g["trade_date"].to_list()
        pm[code] = {"idx": {d: i for i, d in enumerate(dts)},
                    "o": g["open"].to_list(), "l": g["low"].to_list(), "c": g["close"].to_list()}
    ld = set()
    if "is_limit_down" in sub.columns:
        ldf = sub.filter(pl.col("is_limit_down"))
        ld = set(zip(ldf["ts_code"].to_list(), ldf["trade_date"].to_list()))

    frozen = _frozen()
    live = exit_sim._sim_one
    kw_grid = [
        dict(base_hold=5, retrace=0.08, stop=0.05, v1=True, hard_cap=15),   # 现役 v1.3.3 口径
        dict(base_hold=5, retrace=0.05, stop=0.05),                         # K1 口径
        dict(base_hold=8, retrace=0.10, stop=0.06, v1=True, v2=True, hard_cap=20),
    ]

    compared = mismatched = 0
    reasons: dict = {}
    while compared < args.n:
        code = rng.choice(list(pm))
        days = list(pm[code]["idx"])
        if len(days) < 30:
            continue
        buy_date = days[rng.randrange(0, len(days) - 1)]
        if buy_date not in cal_idx:
            continue
        buy_price = pm[code]["o"][pm[code]["idx"][buy_date]]
        if buy_price is None or buy_price <= 0:
            continue
        shares = int(40000.0 // buy_price // 100) * 100 or 100
        t = ClosedTrade(ts_code=code, buy_date=buy_date, sell_date=buy_date, shares=shares,
                        buy_price=buy_price, sell_price=buy_price,
                        buy_fees=exit_sim.BROKER._buy_fees(shares * buy_price),
                        sell_fees=0.0, reason="")
        kw = kw_grid[compared % len(kw_grid)]
        a, b = live(t, pm, ld, cal, cal_idx, **kw), frozen(t, pm, ld, cal, cal_idx, **kw)
        compared += 1
        if _fields(a) != _fields(b):
            mismatched += 1
            print(f"[smoke] ✗ 不一致 {code} {buy_date} kw={kw}\n  live ={_fields(a)}\n  froz ={_fields(b)}")
        key = a.reason if a is not None else "none"
        reasons[key] = reasons.get(key, 0) + 1

    print(f"\n# 判分口径真 K 线逐位对拍(面板 {panel_path.name})\n")
    print(f"- 对拍 **{compared}** 笔真实 (代码, 交易日) 入场单 × 3 组退出参数轮换")
    print(f"- 退出原因分布:{', '.join(f'{k}={v}' for k, v in sorted(reasons.items()))}")
    print(f"- **不一致 {mismatched} 笔** → 判定:**{'过' if mismatched == 0 else '挂'}**")
    if mismatched == 0:
        print("- 结论:`_sim_one` 下沉到 `neckline/eval/exit_sim.py` 后,在真实 K 线上"
              "与搬迁前的冻结副本**逐位相同**(退出日 / 退出价 / 原因 / 豁免 / 持有天数 / "
              "pnl / pnl_pct 全等)。")
    return 0 if mismatched == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
