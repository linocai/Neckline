"""K5 · 可转债双低轮动战役 runner(第二引擎 · 族谱候选一 ⑥)。

预注册依据:`research/k5_cb_report.md`(定义/剔除规则/判决口径已钉死,不得改)。
研究档铁律:所有新数据只落 `research/_cache/cb_*`,绝不进 data/ 生产湖、不碰生产表。

数据架构(Phase-0 侦察定案,§0):
  - 券种全集 + 静态属性 + 退市: TuShare `cb_basic`(含 806 退市券 → 无幸存者偏差)
  - 日行情(价/量/额): TuShare `cb_daily`(按 trade_date 批量,收盘完备≈100%)
  - 转股价值/溢价率: akshare `bond_zh_cov_value_analysis`(逐券,含退市券全历史)
        溢价率自洽重算 = TuShare_close / akshare_转股价值 - 1(交叉核对与东财口径 6 位一致)
  - 强赎/到赎: TuShare `cb_call`(按 ann_date 分年拉,绕开 2000 行截断,全期 2918 事件)
  - 正股 ST: TuShare `namechange`(分页,建 ST 区间 → 逐日 as-of 标)
  - 基准: TuShare `index_daily` 000300.SH(沪深300)/ 000832.CSI(中证转债)

用法:
  python research/k5_cb.py build-cache [--sample N]   # 拉数落 _cache(长任务,~10min)
  python research/k5_cb.py backtest                    # 读 _cache 跑 H-CB1/CB2 + 网格
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.data.tushare_client import _call  # noqa: E402

CACHE = Path(__file__).resolve().parent / "_cache"

# —— 预注册常量(先 commit 不许看结果回改)——
WIN_START = date(2018, 1, 1)
WIN_END = date(2026, 7, 24)
IN_END = date(2024, 12, 31)          # 样本内 2018-2024
OOS_START = date(2025, 1, 1)          # 样本外 2025-2026-07
Y2026_START = date(2026, 1, 1)        # ★2026 分段一票否决

FEE_PER_SIDE = 0.001                  # 单边 0.1% → 双边合计 0.2%(预注册)
MIN_ISSUE_SIZE = 1e8                  # 债券余额<1亿 剔除(以 issue_size 静态代理,§0 登记)
MIN_REMAIN_YEARS = 0.5                # 剩余期限<0.5年 剔除
RATING_FLOOR = "AA-"                  # 评级门槛 ≥AA-(以 issue_rating 至发行口径,无前视)

# 评级序(高→低);≥AA- = rank ≤ RATING_FLOOR 的 rank
_RATING_ORDER = ["AAA", "AAA-", "AA+", "AA", "AA-", "A+", "A", "A-",
                 "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-",
                 "CCC", "CC", "C", "D"]
_RATING_RANK = {r: i for i, r in enumerate(_RATING_ORDER)}
_RATING_FLOOR_RANK = _RATING_RANK[RATING_FLOOR]

CB_BASIC = CACHE / "cb_basic.parquet"
CB_DAILY = CACHE / "cb_daily.parquet"
CB_CALL = CACHE / "cb_call.parquet"
CB_PREM = CACHE / "cb_premium.parquet"
CB_NAME = CACHE / "cb_namechange.parquet"
CB_INDEX = CACHE / "cb_index.parquet"


# ======================================================================
#  Phase-0 / cache build
# ======================================================================
def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _trading_days() -> List[str]:
    """SSE 交易日 (YYYYMMDD) in [WIN_START, WIN_END]."""
    r = _call("trade_cal", exchange="SSE", start_date=_yyyymmdd(WIN_START),
              end_date=_yyyymmdd(WIN_END), is_open="1")
    if not r.ok:
        raise RuntimeError(f"trade_cal 失败: {r.reason}")
    df = r.data
    return sorted(df["cal_date"].astype(str).tolist())


def build_cb_basic() -> "object":
    import pandas as pd
    if CB_BASIC.exists():  # 断点续拉:已有则读盘,不重拉(配额宝贵)
        b = pl.read_parquet(CB_BASIC).to_pandas()
        print(f"[cb_basic] 已存在,跳过拉取({len(b)} 券)→ {CB_BASIC.name}")
        return b
    fields = ("ts_code,bond_short_name,stk_code,stk_short_name,list_date,delist_date,"
              "issue_size,remain_size,conv_price,first_conv_price,value_date,maturity_date,"
              "conv_start_date,conv_end_date,issue_rating,newest_rating,cb_type")
    r = _call("cb_basic", fields=fields)
    if not r.ok:
        raise RuntimeError(f"cb_basic 失败: {r.reason}")
    b = r.data.copy()
    for c in ["list_date", "delist_date", "maturity_date", "conv_start_date", "conv_end_date", "value_date"]:
        b[c] = b[c].astype("string")
    # 与 2018-2026 窗口相关:未在 2018 前退市 且 已发行(list_date<=窗口末)
    def _relevant(row) -> bool:
        dd = row["delist_date"]
        ld = row["list_date"]
        if ld is None or (isinstance(ld, float)):
            return False
        if ld is not None and str(ld) > _yyyymmdd(WIN_END):
            return False
        if dd is not None and str(dd) != "<NA>" and str(dd) != "" and str(dd) < _yyyymmdd(WIN_START):
            return False
        return True
    b = b[b.apply(_relevant, axis=1)].reset_index(drop=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    pl.from_pandas(b).write_parquet(CB_BASIC)
    print(f"[cb_basic] 相关券 {len(b)} 只(总 delist 非空 {b['delist_date'].notna().sum()})→ {CB_BASIC.name}")
    return b


def _flush_daily(frames, path) -> "object":
    import pandas as pd
    big = pd.concat(frames, ignore_index=True)
    big["trade_date"] = big["trade_date"].astype(str)
    big = big.drop_duplicates(subset=["ts_code", "trade_date"]).reset_index(drop=True)
    pl.from_pandas(big).write_parquet(path)
    return big


def build_cb_daily(days: List[str]) -> None:
    """按 trade_date 逐日拉全市场转债行情。断点续拉:已有 CB_DAILY 里的天不重拉,
    每 100 天存盘一次——配额中途耗尽也只丢最多 100 天,可原地续跑。"""
    import pandas as pd
    done: set = set()
    frames: list = []
    if CB_DAILY.exists():
        prev = pl.read_parquet(CB_DAILY)
        done = set(prev["trade_date"].to_list())
        frames.append(prev.to_pandas())
    todo = [d for d in days if d not in done]
    print(f"[cb_daily] 断点续拉:已有 {len(done)} 天,续拉 {len(todo)} 天")
    t0 = time.time()
    for i, d in enumerate(todo):
        r = _call("cb_daily", trade_date=d)
        if r.ok and len(r.data):
            frames.append(r.data[["ts_code", "trade_date", "pre_close", "close", "vol", "amount"]])
        if (i + 1) % 100 == 0:
            big = _flush_daily(frames, CB_DAILY)
            print(f"[cb_daily] {i+1}/{len(todo)} 天(累计 {big['trade_date'].nunique()} 天)"
                  f"  用时 {time.time()-t0:.0f}s  [已存盘]")
    big = _flush_daily(frames, CB_DAILY)
    print(f"[cb_daily] {len(big)} 行(去重券 {big['ts_code'].nunique()},{big['trade_date'].nunique()} 天)→ {CB_DAILY.name}")


def build_cb_call() -> None:
    import pandas as pd
    if CB_CALL.exists():
        print(f"[cb_call] 已存在,跳过 → {CB_CALL.name}")
        return
    frames = []
    for y in range(WIN_START.year, WIN_END.year + 1):
        r = _call("cb_call", start_date=f"{y}0101", end_date=f"{y}1231")
        if r.ok and len(r.data):
            frames.append(r.data)
    big = pd.concat(frames, ignore_index=True)
    for c in ["ann_date", "call_date"]:
        if c in big.columns:
            big[c] = big[c].astype("string")
    big = big.drop_duplicates(subset=["ts_code", "ann_date", "call_type"]).reset_index(drop=True)
    pl.from_pandas(big[["ts_code", "call_type", "is_call", "ann_date", "call_date", "call_price"]]).write_parquet(CB_CALL)
    print(f"[cb_call] {len(big)} 事件(强赎 {(big['call_type']=='强赎').sum()})→ {CB_CALL.name}")


def build_namechange() -> None:
    import pandas as pd
    if CB_NAME.exists():
        print(f"[namechange] 已存在,跳过 → {CB_NAME.name}")
        return
    frames = []
    offset = 0
    while True:
        r = _call("namechange", fields="ts_code,name,start_date,end_date,change_reason",
                  limit=8000, offset=offset)
        if not r.ok or len(r.data) == 0:
            break
        frames.append(r.data)
        if len(r.data) < 8000:
            break
        offset += 8000
    big = pd.concat(frames, ignore_index=True).drop_duplicates()
    for c in ["start_date", "end_date"]:
        big[c] = big[c].astype("string")
    # 只留含 ST 的记录(ST/*ST/退市风险)
    big = big[big["name"].str.contains("ST", na=False)].reset_index(drop=True)
    pl.from_pandas(big).write_parquet(CB_NAME)
    print(f"[namechange] ST 区间 {len(big)} 条(去重股 {big['ts_code'].nunique()})→ {CB_NAME.name}")


def build_index() -> None:
    import pandas as pd
    if CB_INDEX.exists():
        print(f"[index] 已存在,跳过 → {CB_INDEX.name}")
        return
    frames = []
    for code in ["000300.SH", "000832.CSI"]:
        r = _call("index_daily", ts_code=code, start_date=_yyyymmdd(WIN_START), end_date=_yyyymmdd(WIN_END))
        if r.ok and len(r.data):
            df = r.data[["ts_code", "trade_date", "close"]].copy()
            frames.append(df)
    big = pd.concat(frames, ignore_index=True)
    big["trade_date"] = big["trade_date"].astype(str)
    pl.from_pandas(big).write_parquet(CB_INDEX)
    print(f"[index] {len(big)} 行 → {CB_INDEX.name}")


CB_PREM_ATT = CACHE / "cb_premium_attempted.txt"  # 断点续拉:已尝试券(成功/空/失败都记)


def build_premium(codes: List[str], sample: Optional[int] = None) -> None:
    """逐券拉 akshare 东财转股价值/溢价率(含退市券全历史)。断点续拉:已尝试过的券
    不重拉(记在 sidecar),每 100 券存盘一次——东财限流/中断也可原地续跑。"""
    import akshare as ak
    import pandas as pd
    if sample:
        codes = codes[:sample]
    attempted: set = set()
    if CB_PREM_ATT.exists():
        attempted = set(CB_PREM_ATT.read_text().split())
    frames = []
    if CB_PREM.exists():
        frames.append(pl.read_parquet(CB_PREM).to_pandas())
    todo = [c for c in codes if c not in attempted]
    print(f"[premium] 断点续拉:已尝试 {len(attempted)},续拉 {len(todo)}/{len(codes)}")

    def _flush(pending_att: list) -> None:
        if frames:
            big = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_code", "date"])
            pl.from_pandas(big).write_parquet(CB_PREM)
        with CB_PREM_ATT.open("a") as f:
            for c in pending_att:
                f.write(c + "\n")

    failed = []
    pending_att = []
    t0 = time.time()
    for i, ts in enumerate(todo):
        sym = ts.split(".")[0]
        ok = False
        for attempt in range(3):
            try:
                df = ak.bond_zh_cov_value_analysis(symbol=sym)
                if df is not None and len(df):
                    df = df[["日期", "转股价值", "转股溢价率"]].copy()
                    df.columns = ["date", "conv_value", "ak_premium"]
                    df["ts_code"] = ts
                    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
                    frames.append(df)
                    ok = True
                break
            except Exception as e:
                if attempt == 2:
                    failed.append((ts, str(e)[:40]))
                time.sleep(0.5 * (attempt + 1))
        if not ok and ts not in [f[0] for f in failed]:
            failed.append((ts, "empty"))
        pending_att.append(ts)  # 无论成功/空/失败都记为已尝试,避免续拉时反复重试死券
        time.sleep(0.25)  # 东财限速保护
        if (i + 1) % 100 == 0:
            _flush(pending_att)
            pending_att = []
            print(f"[premium] {i+1}/{len(todo)} 券  用时 {time.time()-t0:.0f}s  失败 {len(failed)}  [已存盘]")
    _flush(pending_att)
    big = pl.read_parquet(CB_PREM).to_pandas() if CB_PREM.exists() else pd.DataFrame()
    print(f"[premium] {len(big)} 行(成功券 {big['ts_code'].nunique() if len(big) else 0},"
          f"失败 {len(failed)})→ {CB_PREM.name}")
    if failed:
        print("  失败样例:", failed[:10])


def cmd_build_cache(sample: Optional[int]) -> None:
    print("=== build-cache 开始 ===")
    b = build_cb_basic()
    days = _trading_days()
    print(f"[trade_cal] {len(days)} 个交易日 {days[0]}~{days[-1]}")
    build_cb_daily(days)
    build_cb_call()
    build_namechange()
    build_index()
    codes = b["ts_code"].tolist()
    build_premium(codes, sample=sample)
    print("=== build-cache 完成 ===")


# ======================================================================
#  Phase-0 质量线判定(预注册 §0:≥80% 存续覆盖 / 溢价率完备率 ≥90% /
#  退市券不缺失 —— 幸存者偏差是本族第一死穴)
# ======================================================================
def cmd_phase0() -> None:
    basic = pl.read_parquet(CB_BASIC)
    if "cb_type" in basic.columns:
        basic = basic.filter(pl.col("cb_type").is_null() | (pl.col("cb_type") == "CB"))
    daily = pl.read_parquet(CB_DAILY)
    prem = pl.read_parquet(CB_PREM) if CB_PREM.exists() else pl.DataFrame(
        {"ts_code": [], "date": [], "conv_value": [], "ak_premium": []})

    n_cb = basic.height
    b2 = basic.with_columns([
        pl.col("list_date").str.strptime(pl.Date, "%Y%m%d", strict=False).alias("ld"),
        pl.col("delist_date").str.strptime(pl.Date, "%Y%m%d", strict=False).alias("dd"),
    ])
    n_delisted = b2.filter(pl.col("dd").is_not_null()).height
    daily_codes = set(daily["ts_code"].unique().to_list())
    prem_codes = set(prem["ts_code"].unique().to_list()) if prem.height else set()
    delisted_codes = set(b2.filter(pl.col("dd").is_not_null())["ts_code"].to_list())

    print("=== Phase-0 数据侦察(§0 质量线判定)===")
    print(f"[券种全集] CB 普通转债 {n_cb} 只;其中已退市 {n_delisted} 只 "
          f"({100*n_delisted/max(n_cb,1):.0f}%)")
    print(f"[幸存者偏差自检] 退市券出现在 cb_daily 的 {len(delisted_codes & daily_codes)}"
          f"/{len(delisted_codes)},出现在 premium 的 {len(delisted_codes & prem_codes)}"
          f"/{len(delisted_codes)}  —— 越接近满值越无幸存者偏差")

    # 每日行情去重 (ts_code, trade_date);premium 去重 (ts_code, date)
    daily_u = daily.select(["ts_code", "trade_date", "close", "vol"]).unique(subset=["ts_code", "trade_date"])
    prem_u = prem.select(["ts_code", "date"]).unique(subset=["ts_code", "date"]).rename({"date": "trade_date"}) \
        if prem.height else pl.DataFrame({"ts_code": [], "trade_date": []})
    # daily × premium 左连,标记该 bond-day 是否有溢价率
    joined = daily_u.join(prem_u.with_columns(pl.lit(True).alias("has_prem")),
                          on=["ts_code", "trade_date"], how="left") \
        .with_columns(pl.col("has_prem").fill_null(False))
    joined = joined.with_columns(pl.col("trade_date").str.slice(0, 4).alias("y"))

    print("\n[逐年覆盖率 & 溢价率完备率](存续=当年有 list 且未在年初前退市)")
    print("  年   | 存续券 | 有行情 | 覆盖% | bond-day | 有溢价率 | 完备%")
    cov_ok = True
    prem_ok = True
    for y in range(2018, 2027):
        ys, ye = date(y, 1, 1), date(y, 12, 31)
        alive = b2.filter((pl.col("ld") <= ye) & (pl.col("dd").is_null() | (pl.col("dd") >= ys)))
        n_alive = alive.height
        yj = joined.filter(pl.col("y") == str(y))
        n_daily_codes = yj["ts_code"].n_unique()
        n_bd = yj.height
        n_bd_prem = yj.filter(pl.col("has_prem")).height
        cov = 100 * n_daily_codes / max(n_alive, 1)
        comp = 100 * n_bd_prem / max(n_bd, 1)
        if 2018 <= y <= 2026:
            cov_ok = cov_ok and (cov >= 80)
            prem_ok = prem_ok and (comp >= 90)
        print(f"  {y} |  {n_alive:4d}  |  {n_daily_codes:4d}  | {cov:5.1f} | {n_bd:7d} | {n_bd_prem:8d} | {comp:5.1f}")

    print(f"\n[判定] 覆盖率线(≥80%):{'通过' if cov_ok else '未过'}  "
          f"溢价率完备率线(≥90%):{'通过' if prem_ok else '未过'}  "
          f"退市券:{'不缺失' if len(delisted_codes & daily_codes) >= 0.9*len(delisted_codes) else '疑缺失'}")
    verdict = cov_ok and prem_ok and (len(delisted_codes & daily_codes) >= 0.9 * len(delisted_codes))
    print(f"[Phase-0 结论] {'可行 → 开跑 H-CB1' if verdict else '不过线 → 战役止步,列缺口清单诚实交割'}")


# ======================================================================
#  panel:join + 硬剔除 → 逐日可交易域
# ======================================================================
def _rating_pass(r: Optional[str]) -> bool:
    if r is None:
        return False
    r = str(r).strip().replace("sti", "").replace(" ", "")
    rank = _RATING_RANK.get(r)
    if rank is None:
        return False
    return rank <= _RATING_FLOOR_RANK


def _to_date(s: str) -> Optional[date]:
    if s is None or s == "" or s == "<NA>" or (isinstance(s, float)):
        return None
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def build_panel(rating_gate: bool = True) -> pl.DataFrame:
    """join 全部源 → 逐 (ts_code, date) 行,含硬剔除 flag 与 tradable 布尔。"""
    basic = pl.read_parquet(CB_BASIC)
    daily = pl.read_parquet(CB_DAILY)
    prem = pl.read_parquet(CB_PREM)
    call = pl.read_parquet(CB_CALL)
    name = pl.read_parquet(CB_NAME)

    # 只留普通可转债(排除可交换债 EB 等,cb_type 非 CB 的)
    if "cb_type" in basic.columns:
        basic = basic.filter(pl.col("cb_type").is_null() | (pl.col("cb_type") == "CB"))

    df = daily.join(prem.rename({"date": "trade_date"}), on=["ts_code", "trade_date"], how="left")
    df = df.join(basic, on="ts_code", how="inner")

    # 溢价率自洽重算:premium = close/conv_value - 1(conv_value 缺 → null)
    df = df.with_columns([
        (pl.col("close") / pl.col("conv_value") - 1.0).alias("premium"),
    ])
    df = df.with_columns([
        (pl.col("close") + 100.0 * pl.col("premium")).alias("double_low"),
        pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("d"),
        pl.col("maturity_date").str.strptime(pl.Date, "%Y%m%d", strict=False).alias("mat_d"),
    ])

    # —— 强赎/到赎 as-of:某券【已承诺赎回】公告 ann_date<=当日 → 此后剔除 ——
    # 【生死线口径修正,与预注册"已公告强赎"对齐】cb_call.is_call 有 5 态,只有
    # 发行人【已承诺赎回】的公告才构成剔除依据:
    #   公告实施强赎 / 公告提示强赎(已决定强赎)、公告到期赎回(临期赎回,券将消失)。
    # 【不构成剔除】公告不强赎(发行人放弃本次强赎,券继续存续,885 事件)、
    #   已满足强赎条件(仅触发条件、未公告赎回决定,1145 事件)——原实现取全部
    #   is_call 的 min(ann_date) 会把"公告不强赎/满足条件"也当剔除信号,永久错杀
    #   大量正常存续券日。回售事件 cb_call 接口不含,以剩余期限<0.5年近似覆盖(登记)。
    _CALL_COMMIT = ["公告实施强赎", "公告提示强赎", "公告到期赎回"]
    call_excl = call.filter(pl.col("is_call").is_in(_CALL_COMMIT))
    call_min = (call_excl.with_columns(pl.col("ann_date").str.strptime(pl.Date, "%Y%m%d", strict=False).alias("ann_d"))
                    .group_by("ts_code").agg(pl.col("ann_d").min().alias("call_ann_d")))
    df = df.join(call_min, on="ts_code", how="left")

    # —— 正股 ST as-of:namechange 区间覆盖当日(end_date 空=至今)——
    # 构 (stk_code, start, end) 区间;逐日判断成本高 → 用 join_asof 近似:
    # 标记该正股在当日是否处于任一 ST 区间。
    st = name.with_columns([
        pl.col("start_date").str.strptime(pl.Date, "%Y%m%d", strict=False).alias("st_start"),
        pl.col("end_date").str.strptime(pl.Date, "%Y%m%d", strict=False).alias("st_end"),
    ]).select(["ts_code", "st_start", "st_end"]).rename({"ts_code": "stk_code"})
    # 对每个 (stk_code) 收集区间;用 explode-free 的做法:join 后按区间过滤
    df = df.join(st, on="stk_code", how="left")
    df = df.with_columns(
        ((pl.col("st_start").is_not_null()) & (pl.col("d") >= pl.col("st_start")) &
         (pl.col("st_end").is_null() | (pl.col("d") <= pl.col("st_end")))).alias("_st_hit")
    )
    st_flag = df.group_by(["ts_code", "trade_date"]).agg(pl.col("_st_hit").any().alias("st_flag"))
    df = df.drop(["st_start", "st_end", "_st_hit"]).unique(subset=["ts_code", "trade_date"])
    df = df.join(st_flag, on=["ts_code", "trade_date"], how="left")

    # —— 剩余期限(年)——
    df = df.with_columns(
        ((pl.col("mat_d") - pl.col("d")).dt.total_days() / 365.25).alias("rem_years")
    )
    # —— 停牌:vol==0 ——
    df = df.with_columns((pl.col("vol") == 0).alias("suspended"))

    # —— 评级 pass(issue_rating)——
    if rating_gate:
        pass_codes = set(basic.filter(
            pl.col("issue_rating").map_elements(_rating_pass, return_dtype=pl.Boolean)
        )["ts_code"].to_list())
        df = df.with_columns(pl.col("ts_code").is_in(list(pass_codes)).alias("rating_ok"))
    else:
        df = df.with_columns(pl.lit(True).alias("rating_ok"))

    # —— tradable:全部硬剔除 AND ——
    df = df.with_columns([
        (
            pl.col("double_low").is_not_null()
            & pl.col("close").is_not_null() & (pl.col("close") > 0)
            & (~pl.col("suspended"))
            & (pl.col("call_ann_d").is_null() | (pl.col("d") < pl.col("call_ann_d")))  # 未公告强赎/到赎
            & (pl.col("rem_years") >= MIN_REMAIN_YEARS)
            & (pl.col("issue_size") >= MIN_ISSUE_SIZE)
            & (~pl.col("st_flag").fill_null(False))
            & pl.col("rating_ok")
        ).alias("tradable")
    ])
    return df.sort(["trade_date", "ts_code"])


# ======================================================================
#  组合模拟(逐日,含中途退市/强赎强制退出;换手计费)
# ======================================================================
def _rebalance_dates(all_days: List[str], freq: str) -> List[str]:
    """freq ∈ {'w','2w','m'}:周/双周/月 轮动日(区间首个交易日锚)。"""
    if freq == "m":
        seen = set()
        out = []
        for d in all_days:
            ym = d[:6]
            if ym not in seen:
                seen.add(ym); out.append(d)
        return out
    step = 5 if freq == "w" else 10
    return [all_days[i] for i in range(0, len(all_days), step)]


def simulate(panel: pl.DataFrame, n: int, freq: str,
             days: List[str]) -> pl.DataFrame:
    """双低轮动组合逐日净值。返回 DataFrame(date, nav)。初始资金 = 现金 1.0。

    - 选股:轮动日在 tradable 域取 double_low 最小 N 只等权。
    - 持有期:持仓每日按当日 close 估值;停牌/缺价 carry 上一有效价。
      持仓券中途退市(价格序列断)按最后有效价平仓;as-of 公告强赎 → 当日 close 平仓转现金。
    - 计费:轮动换手(买名义+卖名义)× FEE_PER_SIDE(单边 0.1% → 双边合计 0.2%)。
    """
    px = {(r["trade_date"], r["ts_code"]): r["close"] for r in
          panel.select(["trade_date", "ts_code", "close"]).iter_rows(named=True)}
    trad = panel.filter(pl.col("tradable"))
    cand: Dict[str, List[Tuple[float, str]]] = {}
    for r in trad.select(["trade_date", "ts_code", "double_low"]).iter_rows(named=True):
        cand.setdefault(r["trade_date"], []).append((r["double_low"], r["ts_code"]))
    call_d = {r["ts_code"]: r["call_ann_d"] for r in
              panel.select(["ts_code", "call_ann_d"]).unique().iter_rows(named=True)}
    last_px_day: Dict[str, str] = {}
    for (d, ts) in px.keys():
        if ts not in last_px_day or d > last_px_day[ts]:
            last_px_day[ts] = d

    rebal = set(_rebalance_dates(days, freq))
    holdings: Dict[str, float] = {}   # ts_code -> shares
    last_close: Dict[str, float] = {}  # ts_code -> 最近有效 close(估值/carry)
    cash = 1.0
    navs = []

    for d in days:
        # 1) 强制退出(先于估值):退市按最后价、as-of 强赎按当日价平仓
        for ts in list(holdings.keys()):
            ca = call_d.get(ts)
            dd = _to_date(d)
            if d > last_px_day.get(ts, d):                        # 退市:无后续价
                cash += holdings[ts] * last_close.get(ts, 0.0)
                del holdings[ts]
            elif ca is not None and dd is not None and dd >= ca:  # 已公告强赎/到赎
                p = px.get((d, ts), last_close.get(ts, 0.0))
                cash += holdings[ts] * p
                del holdings[ts]

        # 2) 刷新持仓当日估值价(有当日价则更新,否则 carry)
        for ts in holdings:
            p = px.get((d, ts))
            if p is not None and p > 0:
                last_close[ts] = p

        # 3) 当前净值
        pv = cash + sum(holdings[ts] * last_close.get(ts, 0.0) for ts in holdings)

        # 4) 轮动日重配(等权 top-N,含换手计费)
        if d in rebal and pv > 0:
            targets = [ts for _, ts in sorted(cand.get(d, []))[:n]]
            if targets:
                cur_val = {ts: holdings.get(ts, 0.0) * last_close.get(ts, 0.0)
                           for ts in set(list(holdings) + targets)}
                tgt_val = pv / len(targets)
                buys = sells = 0.0
                for ts in cur_val:
                    tv = tgt_val if ts in targets else 0.0
                    cv = cur_val[ts]
                    if tv >= cv:
                        buys += tv - cv
                    else:
                        sells += cv - tv
                pv -= FEE_PER_SIDE * (buys + sells)
                # 扣费后按等权重建
                tgt_val2 = pv / len(targets)
                holdings = {}
                for ts in targets:
                    p = px.get((d, ts), last_close.get(ts))
                    if p and p > 0:
                        holdings[ts] = tgt_val2 / p
                        last_close[ts] = p
                cash = 0.0
                pv = cash + sum(holdings[ts] * last_close.get(ts, 0.0) for ts in holdings)
        navs.append((d, pv))

    return pl.DataFrame({"trade_date": [x[0] for x in navs], "nav": [x[1] for x in navs]})


# ======================================================================
#  基准 + 指标
# ======================================================================
def equal_weight_benchmark(panel: pl.DataFrame, days: List[str]) -> pl.DataFrame:
    """全样本转债等权对照(每日持全部 tradable 券等权,双周再平衡,同费率)。"""
    return simulate(panel, n=100000, freq="2w", days=days)  # N 极大 = 全域等权


def index_nav(code: str, days: List[str]) -> pl.DataFrame:
    idx = pl.read_parquet(CB_INDEX).filter(pl.col("ts_code") == code)
    idx = idx.filter(pl.col("trade_date").is_in(days)).sort("trade_date")
    if idx.is_empty():
        return pl.DataFrame({"trade_date": days, "nav": [1.0] * len(days)})
    base = idx["close"][0]
    return idx.select(["trade_date", (pl.col("close") / base).alias("nav")])


def metrics(nav: pl.DataFrame, label: str) -> Dict:
    nav = nav.sort("trade_date")
    vals = nav["nav"].to_list()
    dates = nav["trade_date"].to_list()
    if len(vals) < 2 or vals[0] <= 0:
        return {"label": label, "years": 0}
    n_years = (_to_date(dates[-1]) - _to_date(dates[0])).days / 365.25
    total = vals[-1] / vals[0] - 1
    cagr = (vals[-1] / vals[0]) ** (1 / n_years) - 1 if n_years > 0 else 0
    # maxDD
    peak = vals[0]; mdd = 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    # 日收益左尾
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals)) if vals[i - 1] > 0]
    rets_sorted = sorted(rets)
    worst = rets_sorted[0] if rets else 0
    var5 = rets_sorted[int(0.05 * len(rets))] if rets else 0
    import statistics
    vol = statistics.pstdev(rets) * (252 ** 0.5) if len(rets) > 1 else 0
    sharpe = cagr / vol if vol > 0 else 0
    return {"label": label, "years": round(n_years, 2), "total": total, "cagr": cagr,
            "mdd": mdd, "sharpe": sharpe, "worst_day": worst, "var5": var5}


def yearly_returns(nav: pl.DataFrame) -> Dict[str, float]:
    nav = nav.sort("trade_date")
    out = {}
    by_year: Dict[str, List[float]] = {}
    for r in nav.iter_rows(named=True):
        by_year.setdefault(r["trade_date"][:4], []).append(r["nav"])
    for y, vs in by_year.items():
        out[y] = vs[-1] / vs[0] - 1 if vs[0] > 0 else 0
    return out


def window_cagr(nav: pl.DataFrame, start: date, end: date) -> Optional[float]:
    nav = nav.sort("trade_date")
    sub = nav.filter((pl.col("trade_date") >= _yyyymmdd(start)) & (pl.col("trade_date") <= _yyyymmdd(end)))
    vals = sub["nav"].to_list(); dates = sub["trade_date"].to_list()
    if len(vals) < 2 or vals[0] <= 0:
        return None
    yrs = (_to_date(dates[-1]) - _to_date(dates[0])).days / 365.25
    return (vals[-1] / vals[0]) ** (1 / yrs) - 1 if yrs > 0 else None


def window_total(nav: pl.DataFrame, start: date, end: date) -> Optional[float]:
    nav = nav.sort("trade_date")
    sub = nav.filter((pl.col("trade_date") >= _yyyymmdd(start)) & (pl.col("trade_date") <= _yyyymmdd(end)))
    vals = sub["nav"].to_list()
    if len(vals) < 2 or vals[0] <= 0:
        return None
    return vals[-1] / vals[0] - 1


# ======================================================================
#  backtest 主流程
# ======================================================================
def cmd_backtest() -> None:
    panel = build_panel(rating_gate=True)
    days = sorted(panel["trade_date"].unique().to_list())
    print(f"[panel] {panel.height} 行,{len(days)} 交易日,可交易券日 {panel.filter(pl.col('tradable')).height}")
    # 逐日可交易券数(诊断池子演化)
    diag = (panel.filter(pl.col("tradable")).group_by(pl.col("trade_date").str.slice(0, 4).alias("y"))
            .agg(pl.col("ts_code").n_unique().alias("n_bonds")).sort("y"))
    print("[池子] 逐年不同可交易券数:", {r["y"]: r["n_bonds"] for r in diag.iter_rows(named=True)})

    # —— H-CB1 主格 N=20 双周 ——
    main = simulate(panel, n=20, freq="2w", days=days)
    ew = equal_weight_benchmark(panel, days)
    hs300 = index_nav("000300.SH", days)
    zzcb = index_nav("000832.CSI", days)

    print("\n=== H-CB1 主格(N=20,双周)全期指标 ===")
    for nav, lab in [(main, "双低N20双周"), (ew, "转债等权对照"), (hs300, "沪深300"), (zzcb, "中证转债")]:
        m = metrics(nav, lab)
        print(f"  {lab:12s} CAGR={m.get('cagr',0):+.2%}  总={m.get('total',0):+.1%}  "
              f"MDD={m.get('mdd',0):.1%}  Sharpe={m.get('sharpe',0):.2f}  "
              f"最差日={m.get('worst_day',0):.2%}  VaR5={m.get('var5',0):.2%}")

    print("\n=== 分窗口 CAGR(样本内/样本外/2026段)===")
    for nav, lab in [(main, "双低N20双周"), (ew, "转债等权"), (hs300, "沪深300")]:
        ci = window_cagr(nav, WIN_START, IN_END)
        co = window_cagr(nav, OOS_START, WIN_END)
        c26 = window_total(nav, Y2026_START, WIN_END)
        print(f"  {lab:12s} 样本内18-24={_f(ci)}  样本外25-26={_f(co)}  2026段(总)={_f(c26)}")

    print("\n=== 逐年收益 ===")
    ym = yearly_returns(main); ye = yearly_returns(ew); yh = yearly_returns(hs300)
    for y in sorted(ym.keys()):
        print(f"  {y}: 双低={ym[y]:+.1%}  等权={ye.get(y,0):+.1%}  沪深300={yh.get(y,0):+.1%}")

    # —— 九格敏感性 ——
    print("\n=== 九格敏感性(CAGR / 2026段总收益)===")
    grid = {}
    for n in [10, 20, 30]:
        for freq, flab in [("w", "周"), ("2w", "双周"), ("m", "月")]:
            nav = simulate(panel, n=n, freq=freq, days=days)
            cagr = metrics(nav, f"N{n}{flab}").get("cagr", 0)
            c26 = window_total(nav, Y2026_START, WIN_END)
            mdd = metrics(nav, "x").get("mdd", 0)
            grid[(n, flab)] = (cagr, c26, mdd)
            print(f"  N={n:2d} {flab:2s}: CAGR={cagr:+.2%}  2026段={_f(c26)}  MDD={mdd:.1%}")

    # —— H-CB2 regime 叠加(对主格)——
    print("\n=== H-CB2 regime 叠加(中位价滚动3年分位>80% 降仓)===")
    for mode, mlab in [(0.5, "降半仓"), (0.0, "空仓")]:
        nav = simulate_regime(panel, n=20, freq="2w", days=days, derisk=mode)
        m = metrics(nav, mlab)
        c26 = window_total(nav, Y2026_START, WIN_END)
        print(f"  {mlab}: CAGR={m.get('cagr',0):+.2%}  MDD={m.get('mdd',0):.1%}  "
              f"最差日={m.get('worst_day',0):.2%}  2026段={_f(c26)}")
    base_m = metrics(main, "base")
    print(f"  [对照]主格无叠加: CAGR={base_m['cagr']:+.2%}  MDD={base_m['mdd']:.1%}  最差日={base_m['worst_day']:.2%}")


def _f(x: Optional[float]) -> str:
    return f"{x:+.2%}" if x is not None else "N/A"


def simulate_regime(panel: pl.DataFrame, n: int, freq: str, days: List[str], derisk: float) -> pl.DataFrame:
    """在主格上叠加估值 regime 闸门:全市场中位 double_low(用可交易券中位价代理)
    滚动 3 年分位 >80% 的轮动日 → 仓位乘 derisk(0.5 半仓 / 0 空仓)。
    简化实现:先算主格 nav 的日收益,再按 regime 缩放暴露(0 暴露日收益=0)。"""
    # 每日全市场中位 close(可交易域)
    med = (panel.filter(pl.col("tradable")).group_by("trade_date")
           .agg(pl.col("close").median().alias("med_close")).sort("trade_date"))
    med = med.with_columns(pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("d"))
    mvals = med["med_close"].to_list(); mdays = med["trade_date"].to_list()
    # 滚动 3 年(~750 交易日)分位
    W = 750
    high_regime = {}
    for i, d in enumerate(mdays):
        lo = max(0, i - W)
        window = mvals[lo:i + 1]
        if len(window) >= 250:
            rank = sum(1 for v in window if v <= mvals[i]) / len(window)
            high_regime[d] = rank > 0.80
        else:
            high_regime[d] = False
    base = simulate(panel, n=n, freq=freq, days=days).sort("trade_date")
    vals = base["nav"].to_list(); bdays = base["trade_date"].to_list()
    # 用 regime 缩放日收益;regime 状态用轮动日锚定(整个持有期沿用该日状态)
    rebal = _rebalance_dates(days, freq)
    rebal_set = set(rebal)
    exposure = 1.0
    out = [1.0]
    for i in range(1, len(vals)):
        d = bdays[i]
        if d in rebal_set:
            exposure = derisk if high_regime.get(d, False) else 1.0
        r = (vals[i] / vals[i - 1] - 1) if vals[i - 1] > 0 else 0
        out.append(out[-1] * (1 + exposure * r))
    return pl.DataFrame({"trade_date": bdays, "nav": out})


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    bc = sub.add_parser("build-cache")
    bc.add_argument("--sample", type=int, default=None)
    sub.add_parser("phase0")
    sub.add_parser("backtest")
    args = ap.parse_args()
    if args.cmd == "build-cache":
        cmd_build_cache(args.sample)
    elif args.cmd == "phase0":
        cmd_phase0()
    elif args.cmd == "backtest":
        cmd_backtest()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
