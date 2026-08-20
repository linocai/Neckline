"""申万二级行业当日强度 = **成员涨跌幅中位数**(裁定 2 / 裁定 3,PROJECT_PLAN §5.3)。

**它在哪一层**:架构第一层 · 事实层。只回答「这个二级行业今天的成员涨跌幅中位数是
多少、用了几个成员算」。

🔴 **本模块里没有、也不许有任何门槛常量。**
「成员数不足的行业不参与热度排名」是**策略主张**(它直接决定哪些票拿不到相对强度、
进不了形态召回),门槛值 `params.industry.minMembers` 是 §8.2 第 16 项**待标定参数**,
住 `k9/industry_heat.py`(架构 §二 判据:凡是我会想去调的东西都落在策略层)。
本层对**每一个**有成员的二级行业都老实产出一行中位数,够不够格由下游按参数包判。

⚠ 这是 V2.5.0 S3 对 `report/industry_strength.py::_MIN_MEMBERS = 5` 的处置 ——
那个 5 是硬编码的待标定参数,随该模块整体退役物理删除,⛔ 不许以「事实表用工程常量」
为由留下。那条分工对 `limitmap.MIN_CLUSTER_SIZE`(判「簇存不存在」)成立,对
「行业成员数不足则不产出强度」**不成立**。

**口径(裁定 2 + 🔴 裁定 12 逐字)**:
    相对强度 = 个股当日涨跌幅 − 所属申万**二级**行业当日**全部成员**涨跌幅的
    **中位数**(剔除当日**全天停牌**的成员)
⛔ 不使用申万行业指数涨跌幅(`sw_daily` 本版不落,§3.2),⛔ 不使用概念板块。

**🔴 停牌 = 只剔「全天停牌」(裁定 12,2026-08-20 用户对 S3 的返工)**:
    · **全天停牌**(`suspend_type='S'` 且 `suspend_timing` 为空)——150 个交易日实测
      2001 行,**0 行**出现在 daily。它天然不在 `daily` 里,按 `daily` 算中位数时
      自动已剔除;真出现了就是数据事故 → WARNING + 排除 + 计数(`suspended_excluded`)。
    · **盘中临时停牌**(`suspend_timing` 非空,如 `'9:30-9:40'`)——实测 36 行里
      **35 行**在 daily 里,分布在 25/150 天。这些票**当天正常交易、有完整涨跌幅**,
      🔴 **照常计入中位数**。⛔ 不许把它们当停牌剔掉:那是把 17% 的日子里若干只
      正常交易的票从行业事实里抹掉,而且不会有任何人看见。
    · `suspend_type='R'` = **复牌**,当天**正常交易**(20230103 的 000045.SZ 还涨停
      +10.01%、成交 14639 手)。⛔ **一律不过滤,认 R 会误杀真实交易日。**

⚠ 三类的判别**不在本模块**:`facts/pack.py::_suspend_flag_of` 是唯一实现,产出四值
`suspend_flag`(`none`/`S`/`I`/`R`);本模块只收「哪些代码是全天停牌」这一个集合。

**`ret_1d` 用原始(未复权)`close / pre_close − 1`**,不走 `apply_qfq`:qfq 对同一行的
`close`/`pre_close` 用同一标量缩放,比值精确抵消(见 `data/adjust.py::qfq_expr`),
数值与复权面板上算出的完全相同,却省掉全特征装配的 I/O。

🛑 **生产性能红线(§12 坑 1,2026-07-29 挡过一次上云)**:在线路径⛔ 禁止现算全历史。
本模块的公开计算入口 `compute_day` 是**纯函数**(只吃当日横截面,无 I/O);唯一的 I/O
入口 `refresh_day` **只读当日一个 parquet 分区**(`pl.read_parquet(day_file_path(...))`,
⛔ 不走 `get_market_slice` / `scan_table_range` 的 `year=*` 全 glob)。在线读表走
`load_day` / `load_median_map`。这条纪律原样继承自它取代的那份行业强度实现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import polars as pl

from neckline.db import connection, init_schema

logger = logging.getLogger(__name__)

TABLE = "sw_industry_daily"

_COLUMNS = (
    "trade_date, l2_code, l2_name, member_count, suspended_excluded, median_ret, computed_at"
)

_ROW_SCHEMA: Dict[str, pl.DataType] = {
    "trade_date": pl.String,
    "l2_code": pl.String,
    "l2_name": pl.String,
    "member_count": pl.Int64,
    "suspended_excluded": pl.Int64,
    "median_ret": pl.Float64,
    "computed_at": pl.String,
}


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class IndustryDay:
    """一个 (trade_date, l2_code) 的当日事实。**没有 rank / 强度日 / 持续天数**
    —— 那三个都要 `minMembers` 或分位阈,是策略参数(见模块 docstring)。"""

    l2_code: str
    l2_name: str
    member_count: int
    suspended_excluded: int
    median_ret: float


def ret_1d_expr() -> pl.Expr:
    """`close / pre_close − 1` 的**唯一实现**(原始未复权,见模块 docstring)。
    事实包大表与行业中位数共用这一个表达式,⛔ 不许各写一遍。"""
    return (pl.col("close") / pl.col("pre_close") - 1).alias("ret_1d")


def attach_ret_1d(daily: pl.DataFrame) -> pl.DataFrame:
    """给 `daily` 横截面补 `ret_1d` 列,并滤掉除零 / 空值行。空入参原样返回。"""
    if daily.is_empty():
        return daily
    kept = daily.filter(
        pl.col("close").is_not_null()
        & pl.col("pre_close").is_not_null()
        & (pl.col("pre_close") != 0)
    )
    return kept.with_columns(ret_1d_expr())


def compute_day(
    daily_with_ret: pl.DataFrame,
    l2_of: Dict[str, Tuple[str, str]],
    full_day_halted: Iterable[str] = (),
) -> Tuple[List[IndustryDay], List[str]]:
    """**纯函数,无 I/O**。当日横截面(需含 `ts_code`/`ret_1d`)+ `ts_code → (l2_code,
    l2_name)` 映射 → 每个有成员的二级行业一条中位数。

    🔴 `full_day_halted` 只装**全天停牌**的代码(裁定 12)。盘中临时停牌与复牌
    ⛔ 不许放进来 —— 它们当天正常交易,是中位数的合法成员。

    返回 `(逐行业结果, 异常代码列表)`。第二项非空 = 停牌断言被违反
    (**全天停牌**的票竟然出现在 daily),调用方须打 WARNING 并把数量记进
    `fact_packs.suspend_anomaly_count`。

    ⛔ **不设最小成员数门槛**:成员只有 2 只的「旅游零售Ⅱ」照样产出中位数。
    够不够格参与热度排名由 `k9/industry_heat.py` 按 `params.industry.minMembers` 判。
    """
    if daily_with_ret.is_empty() or not l2_of:
        return [], []
    halted = set(full_day_halted)

    codes = daily_with_ret["ts_code"].to_list()
    anomalies = sorted(c for c in codes if c in halted)

    mapping = pl.DataFrame(
        {
            "ts_code": list(l2_of.keys()),
            "l2_code": [v[0] for v in l2_of.values()],
            "l2_name": [v[1] for v in l2_of.values()],
        }
    )
    panel = daily_with_ret.select(["ts_code", "ret_1d"]).join(mapping, on="ts_code", how="inner")
    if panel.is_empty():
        return [], anomalies

    # 每个行业先记「本该有几个成员」,再剔除异常的全天停牌行 —— 两个数都要,
    # `suspended_excluded` 就是它们的差(⛔ 不掩盖:剔了几个必须看得见)。
    before = panel.group_by("l2_code").agg(pl.len().alias("_before"))
    if anomalies:
        panel = panel.filter(~pl.col("ts_code").is_in(anomalies))
    if panel.is_empty():
        return [], anomalies

    agg = (
        panel.filter(pl.col("ret_1d").is_not_null())
        .group_by(["l2_code", "l2_name"])
        .agg(
            pl.col("ret_1d").median().alias("median_ret"),
            pl.len().alias("member_count"),
        )
        .join(before, on="l2_code", how="left")
        .sort("l2_code")
    )
    out = [
        IndustryDay(
            l2_code=r["l2_code"],
            l2_name=r["l2_name"],
            member_count=int(r["member_count"]),
            suspended_excluded=int(r["_before"]) - int(r["member_count"]),
            median_ret=float(r["median_ret"]),
        )
        for r in agg.iter_rows(named=True)
    ]
    return out, anomalies


def _day_frame(trade_date: date, rows: List[IndustryDay]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=_ROW_SCHEMA)
    now = _now()
    day_s = _d(trade_date)
    return pl.DataFrame(
        [
            {
                "trade_date": day_s,
                "l2_code": r.l2_code,
                "l2_name": r.l2_name,
                "member_count": r.member_count,
                "suspended_excluded": r.suspended_excluded,
                "median_ret": r.median_ret,
                "computed_at": now,
            }
            for r in rows
        ],
        schema=_ROW_SCHEMA,
    )


def save_day(
    trade_date: date, rows: List[IndustryDay], *, db_path: Optional[Path] = None
) -> int:
    """落表(`INSERT OR REPLACE`,同一天重算幂等覆盖)。

    ⚠ 与 `fact_packs` 的「⛔ 不许覆盖」纪律**刻意不同**:那张是**清单**(审计物,
    覆盖等于篡改历史);本表是**当日横截面的物化缓存**,同一天用同一份 `daily`
    重算必然逐位相同,可覆盖是幂等而不是改写。口径真变了走 `pack_version`。
    """
    frame = _day_frame(trade_date, rows)
    if frame.is_empty():
        return 0
    init_schema(db_path)
    payload = [
        (
            r["trade_date"], r["l2_code"], r["l2_name"], r["member_count"],
            r["suspended_excluded"], r["median_ret"], r["computed_at"],
        )
        for r in frame.iter_rows(named=True)
    ]
    with connection(db_path) as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,?)", payload
        )
    return len(payload)


def load_day(trade_date: date, *, db_path: Optional[Path] = None) -> List[IndustryDay]:
    """在线唯一读入口。空 = 当日无行(合法结果,⛔ 不现算兜底 —— 现算就是 §12 坑 1)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT l2_code, l2_name, member_count, suspended_excluded, median_ret "
            f"FROM {TABLE} WHERE trade_date=? ORDER BY l2_code",
            (_d(trade_date),),
        ).fetchall()
    return [
        IndustryDay(
            l2_code=r[0], l2_name=r[1], member_count=int(r[2]),
            suspended_excluded=int(r[3]), median_ret=float(r[4]),
        )
        for r in rows
    ]


def load_median_map(trade_date: date, *, db_path: Optional[Path] = None) -> Dict[str, float]:
    """`l2_code → median_ret`(当日)。供事实包大表贴 `sw_l2_median_ret` 用。"""
    return {r.l2_code: r.median_ret for r in load_day(trade_date, db_path=db_path)}


__all__ = [
    "TABLE",
    "IndustryDay",
    "ret_1d_expr",
    "attach_ret_1d",
    "compute_day",
    "save_day",
    "load_day",
    "load_median_map",
]
