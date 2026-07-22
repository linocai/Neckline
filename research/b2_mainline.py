"""B2 · 主线识别器 + 2026-07-22 校准实验(板块层)。

数据源:同花顺概念板块 `ths_index`/`ths_daily`(板块指数日线)/`ths_member`(当前成分快照)。

**证据强度分级(每信号必标)**:
  · ② 板块指数动量、③ 成交额占比抬升 = **板块层 强**(纯板块指数,无成分映射)。
  · ① 板块内涨停贡献、④ 连板高度归属 = **个股层 中(成分洞)**——用**当前**成分快照
    映射历史涨停,带幸存者/前视偏差,降级标注。

B2.1 板块池清洗:剔除宽基样本股/成份股(码段 883xxx 整段 + name 后缀,承 board.py
黑名单口径,不枚举精确子段)。
B2.2 四类主线信号 → B2.3 粗合成(等权分位)排序 → 主线 top1-2 / 支线 next2-3 + 年龄。

运行:python -m research.b2_mainline
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import polars as pl

from neckline.config import settings
from neckline.data.market_data import scan_table_range
from research.p2_sector_age import add_board_age
from research import lab

PDIR = settings.parquet_dir
CACHE_DIR = Path(__file__).resolve().parent / "_cache"

# —— B2.1 宽基黑名单(整段,不枚举精确子段;承 board.py 口径)——
BROAD_CODE_RE = re.compile(r"^8833")           # 883300~883304 宽基样本股/成份股码段
BROAD_NAME_RE = re.compile(r"(样本股|成[份分]股)")

# —— B2.1(续)市场属性/风格/事件/宏观 buckets 黑名单(**非产业/概念主题**,整类剔除)——
# 依据:B2.1 明文「主线识别只保留真·概念/行业板块」。下列是 A 股众所周知的**非行业属性
# 板块**——资金通道(融资融券/沪深股通)、风格规模(中特估/破净/高股息)、公司事件(预增/
# 举牌/次新)、宏观政策(国企改革/一带一路/人民币)。它们成分动辄上千、指数成交额占据横
# 截面前列却不对应任何「题材主线」,污染 vol_share/涨停家数信号。**此表按类目一次性定义、
# 六年通用、不按校准日反调**(承研究铁律「不为对齐硬调到过拟合」)。company-concept 板块
# (华为/DeepSeek/比亚迪/苹果概念等)是真·题材,不入此表。
ATTRIBUTE_NAME_RE = re.compile(
    r"(融资融券|转融券|[沪深陆]股通|北向|QFII|MSCI|富时|标普|道琼斯|证金|汇金|社保|基金重仓"
    r"|中特估|漂亮\d|白马|蓝筹|破净|高股息|微盘|大盘股|中盘股|小盘股|绩优"
    r"|预增|预盈|中报|年报|季报|高送转|送转|举牌|增持|回购|摘帽|参股|创投|独角兽|次新|昨日涨停|昨日连板|首板|破发"
    r"|国企改革|国资|央企|一带一路|西部大开发|人民币|通胀|加息|降息"
    r")"
)

# 主线打分粗权重(等权;禁细调)——四信号各自当日横截面分位后等权平均。
# 强信号(板块指数,无成分映射):② 20d 动量、③ 成交额占比 level。
# 降级信号(当前成分快照映射历史,成分洞):① 涨停家数、④ 最高连板。
SIGNAL_COLS = ["sig_limitup_n", "sig_mom20", "sig_volshare", "sig_maxconsec"]
STRONG_SIGNALS = ["sig_mom20", "sig_volshare"]        # 板块层 强
WEAK_SIGNALS = ["sig_limitup_n", "sig_maxconsec"]     # 成分洞降级


def load_clean_boards() -> Tuple[pl.DataFrame, pl.DataFrame]:
    """返回 (清洗后 ths_index, 清洗后 ths_daily)。剔除宽基样本股/成份股。"""
    idx = pl.read_parquet(PDIR / "ths_index.parquet")
    codes = idx["ts_code"].to_list()
    names = idx["name"].to_list()
    keep = [
        c for c, n in zip(codes, names)
        if not BROAD_CODE_RE.match(c)
        and not BROAD_NAME_RE.search(n)
        and not ATTRIBUTE_NAME_RE.search(n)
    ]
    idx_clean = idx.filter(pl.col("ts_code").is_in(keep))
    daily = pl.read_parquet(PDIR / "ths_daily.parquet").filter(pl.col("ts_code").is_in(keep))
    daily = daily.sort(["ts_code", "trade_date"])
    return idx_clean, daily


def load_member_map() -> pl.DataFrame:
    """当前成分映射 con_code(个股)→ index_code(板块)。成分快照,带幸存者偏差。"""
    m = pl.read_parquet(PDIR / "ths_member.parquet")
    return m.select(["index_code", "con_code"])


# ======================================================================
#  B2.2 四类主线信号(板块层)
# ======================================================================

def add_index_signals(daily: pl.DataFrame) -> pl.DataFrame:
    """②③ 纯板块指数信号(强):20 日动量、10 日动量、站上 MA20 streak(板块年龄)、
    成交量占比(横截面)及其 5 日抬升斜率。"""
    d = add_board_age(daily)  # -> ma20, board_age, board_ret_20d, board_fwd3
    d = d.with_columns(
        (pl.col("close") / pl.col("close").shift(10).over("ts_code") - 1).alias("mom10"),
        (pl.col("close").shift(-5).over("ts_code") / pl.col("close") - 1).alias("board_fwd5"),
    )
    # ③ 成交量占比(当日横截面)+ 5 日平滑(供稳定的「资金主战场」注意力口径)+ 抬升斜率
    d = d.with_columns(
        (pl.col("vol") / pl.col("vol").sum().over("trade_date")).alias("vol_share")
    )
    d = d.with_columns(
        pl.col("vol_share").rolling_mean(5, min_samples=1).over("ts_code").alias("vol_share_5d"),
        (pl.col("vol_share") - pl.col("vol_share").shift(5).over("ts_code")).alias("volshare_slope"),
    )
    return d


def add_member_signals(daily: pl.DataFrame, member: pl.DataFrame,
                       limit_derived: pl.DataFrame) -> pl.DataFrame:
    """①④ 成分映射信号(降级):板块内涨停家数/占比、板块内最高连板。"""
    board_size = member.group_by("index_code").len().rename({"len": "board_size"})

    lu = limit_derived.filter(pl.col("is_limit_up")).select(
        pl.col("ts_code").alias("con_code"), "trade_date"
    )
    board_lu = (
        lu.join(member, on="con_code", how="inner")
        .group_by(["index_code", "trade_date"])
        .agg(pl.len().alias("board_limitup_n"))
    )
    consec = limit_derived.filter(pl.col("consec_limit_up_days") > 0).select(
        pl.col("ts_code").alias("con_code"), "trade_date", "consec_limit_up_days"
    )
    board_consec = (
        consec.join(member, on="con_code", how="inner")
        .group_by(["index_code", "trade_date"])
        .agg(pl.col("consec_limit_up_days").max().alias("board_max_consec"))
    )

    d = daily.join(
        board_lu.rename({"index_code": "ts_code"}), on=["ts_code", "trade_date"], how="left"
    ).join(
        board_consec.rename({"index_code": "ts_code"}), on=["ts_code", "trade_date"], how="left"
    ).join(
        board_size.rename({"index_code": "ts_code"}), on="ts_code", how="left"
    )
    d = d.with_columns(
        pl.col("board_limitup_n").fill_null(0),
        pl.col("board_max_consec").fill_null(0),
        pl.col("board_size").fill_null(1),
    ).with_columns(
        (pl.col("board_limitup_n") / pl.col("board_size")).alias("board_limitup_ratio")
    )
    return d


# ======================================================================
#  B2.3 主线打分 + 年龄
# ======================================================================

def score_mainlines(daily_sig: pl.DataFrame) -> pl.DataFrame:
    """四信号当日横截面分位(0~1)→ 等权平均 = mainline_score;当日排序取 rank。
    另给 clean 版(仅 ②③ 强信号等权)用于稳健性对照。"""
    d = daily_sig.with_columns(
        pl.col("board_limitup_n").cast(pl.Float64).alias("sig_limitup_n"),
        pl.col("board_ret_20d").alias("sig_mom20"),
        pl.col("vol_share").alias("sig_volshare"),
        pl.col("board_max_consec").cast(pl.Float64).alias("sig_maxconsec"),
    )
    # 当日横截面分位(pct rank, higher=更强);null 视为最弱
    rank_exprs = []
    for c in SIGNAL_COLS:
        rank_exprs.append(
            (pl.col(c).fill_null(pl.col(c).min().over("trade_date"))
             .rank(method="average").over("trade_date")
             / pl.len().over("trade_date")).alias(f"{c}_pct")
        )
    d = d.with_columns(rank_exprs)
    d = d.with_columns(
        pl.mean_horizontal([f"{c}_pct" for c in SIGNAL_COLS]).alias("mainline_score"),
        pl.mean_horizontal([f"{c}_pct" for c in STRONG_SIGNALS]).alias("mainline_score_clean"),
    )
    # 当日排序:score 降序 rank(1 = 最强)
    d = d.with_columns(
        pl.col("mainline_score").rank(method="ordinal", descending=True).over("trade_date").alias("rank_all"),
        pl.col("mainline_score_clean").rank(method="ordinal", descending=True).over("trade_date").alias("rank_clean"),
        # 「资金主战场」注意力口径(强、稳定):5 日平滑成交额占比横截面排名(供 B3/B4 成员判定)
        pl.col("vol_share_5d").rank(method="ordinal", descending=True).over("trade_date").alias("rank_att"),
    )
    return d


def label_tier(d: pl.DataFrame, rank_col: str = "rank_all") -> pl.DataFrame:
    """主线 = rank 1-2;支线 = rank 3-5;其余非主线。"""
    return d.with_columns(
        pl.when(pl.col(rank_col) <= 2).then(pl.lit("main"))
        .when(pl.col(rank_col) <= 5).then(pl.lit("branch"))
        .otherwise(pl.lit("none"))
        .alias("mainline_tier")
    )


def build_mainline_panel(cache: bool = True) -> Tuple[pl.DataFrame, pl.DataFrame]:
    """全流程装配:清洗→四信号→打分→分层。返回 (板块信号面板, 清洗后 ths_index)。"""
    idx_clean, daily = load_clean_boards()
    member = load_member_map()
    ld = scan_table_range("limit_derived", date(2020, 1, 1), date(2026, 7, 22))
    d = add_index_signals(daily)
    d = add_member_signals(d, member, ld)
    d = score_mainlines(d)
    d = label_tier(d)
    # 贴板块名
    name_map = dict(zip(idx_clean["ts_code"].to_list(), idx_clean["name"].to_list()))
    d = d.with_columns(pl.col("ts_code").replace_strict(name_map, default=None).alias("board_name"))
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        keep_cols = [
            "ts_code", "board_name", "trade_date", "close", "ma20", "board_age",
            "board_ret_20d", "mom10", "vol_share", "vol_share_5d", "volshare_slope",
            "board_limitup_n", "board_limitup_ratio", "board_max_consec", "board_size",
            "mainline_score", "mainline_score_clean", "rank_all", "rank_clean", "rank_att",
            "mainline_tier", "board_fwd3", "board_fwd5",
        ]
        d.select([c for c in keep_cols if c in d.columns]).write_parquet(CACHE_DIR / "mainline_panel.parquet")
    return d, idx_clean


def mainlines_on(d: pl.DataFrame, trade_date: date, rank_col: str = "rank_all", top_main: int = 2,
                 top_branch: int = 3) -> pl.DataFrame:
    """某交易日主线/支线输出(带年龄)。"""
    day = d.filter(pl.col("trade_date") == trade_date).sort(rank_col)
    return day.select([
        rank_col, "ts_code", "board_name", "board_age", "board_ret_20d",
        "board_limitup_n", "board_max_consec", "vol_share", "mainline_score",
    ]).head(top_main + top_branch)


if __name__ == "__main__":
    print("[B2] 装配主线识别器面板 2020-2026 ...")
    d, idx_clean = build_mainline_panel()
    print(f"  清洗后概念板块 {idx_clean.height} 个(剔宽基);面板 {d.height} 行,"
          f"{d['trade_date'].min()} ~ {d['trade_date'].max()}")

    # 校准日输出
    cal = date(2026, 7, 22)
    print(f"\n[B2.5] {cal} 主线/支线识别(rank_all,全四信号):")
    print(lab.fmt(mainlines_on(d, cal, "rank_all")))
    print(f"\n[B2.5] {cal} 主线/支线识别(rank_clean,仅②③强信号):")
    print(lab.fmt(mainlines_on(d, cal, "rank_clean")))
