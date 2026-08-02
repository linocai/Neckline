"""簇内龙头结构预计算表 `leader_structure_daily`(plan §五 V2-④,P0-23:EOD 预
计算落表、在线只读)。

**⚠ RS 口径与 tie-break 已按策略线 K7 交接稿(`archive/交接_K7_系统线需求_
20260802草案.md` 需求 1a)对齐,施工期间收到、非本块原始设计**——原始实现
一度把连板高度当综合序主键,现已改正,以下为定案口径:

**三个原始量,一个派生判断**(DDL 逐列的分工):
    · `rs_rank`      —— **RS20 排名**:簇内按 `ret_20d`(20 日收益率,严格口径,
      窗口不满 20 个交易日观测 → 该票不参与排名,见下)降序排名(`1`=簇内
      RS 头名,K7 需求 1a「leader_clarity = 簇内 RS 头名度」的直接输入,
      未来 K7-pack 排序键 `leader_rs_rank` 读的就是这一列)。
    · `limit_height`  —— 该票自己当日的连板高度(= `limit_cluster_daily.
      consecutive_days`,直接搬运,不重算)。**K7 需求 1a 定死:连板高度不
      进头名主定义**——它的消费场景是未来的"双尾警示标注"(需求 5,连板
      高度越高、次日跌停概率同时越高的双尾放大器),本列继续按 plan 原样
      产出,只是不再参与 `rs_rank`/`role_mech` 的判断。
    · `amount_share`  —— 该票当日成交额占**簇内当日总成交额**的比例(展示/
      审计用的原始量,同样不进 `role_mech`——K7 需求 1a 原文"成交额头名审计
      否决,也不进[头名主定义]",只作 `rs_rank` 并列时的 tie-break,见下)。
    · `role_mech`     —— `leader|core|elastic|unknown` 四选一,NOT NULL。
      **只读 `rs_rank` 一列**(见下),不混入连板高度/成交额占比。

**`rs_rank` 的 tie-break(K7 需求 1a 定死,可复现铁律,不得自行更改)**:
`RS(ret_20d) 降序 → 成交额(元,原始量非占比,组内序不受除法影响)降序 →
ts_code 升序`,排定后再 `rank(method="ordinal")`(`rank(ordinal)` 的并列由
行序打散=不确定性,必须先排定确定性 tie-break 再 ordinal,§五铁律体例)。

**`role_mech` 判据(K7 定死"不得混入连板高度"之外,leader/core/elastic 的
具体切分比例是本块的工程判断,如实登记,非回测拟合值)**:直接从上面已排定
的 `rs_rank` 派生——`rs_rank` 缺失(见下)→ `unknown`;`rs_rank==1` →
`leader`;`rs_rank <= 1 + n_ranked//2`(`n_ranked`=簇内有效排名的成员数,
不含 `unknown`)→ `core`;其余 → `elastic`(比例约"排除头名后的前一半是
中军";`n_ranked=2` 时第二名归 `core` 而非 `elastic`——只有两个有效排名成员
时把较弱者也打成"投机跟风"不太合理)。

**RS20 的严格口径**:窗口内**恰好** `PRICE_WINDOW_DAYS`(=20)个交易日的
累计收益率 `∏(1+ret_1d) - 1`,与"期末收盘/期初收盘-1"代数等价(见 `corr.py`
的收益率窗口口径),**复用 `corr.load_return_window` 同一份数据**(同一批次
内 `corr.py` 已经读过一次,不重复扫 parquet)。**不足 20 个交易日观测(如簇
成员是刚上市 / 因停牌缺数据的次新股)→ `rs_metric=None` → `rs_rank=None` →
`role_mech="unknown"`**(禁写 0/禁近似;K7"RS20"是一个有明确窗口长度的命名
指标,用不足 20 天的部分窗口冒充会歪曲这个名字的含义,不是"更宽容地给个近似
值",同 `corr_matrix_daily.corr` 的"算不出就是 NULL"纪律)。

**依赖顺序**:必须晚于 `cluster.py` 算完当日 `limit_cluster_daily`(读簇成员),
建议晚于/复用 `corr.py` 已加载的窗口(`scripts/scan_layer.py` 按
cluster→corr→leader 顺序调用;单独调用 `refresh_leader_structure` 也能独立
工作,只是会重新读一次价格窗口)。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

import polars as pl

from neckline.data.market_data import get_market_slice
from neckline.db import connection, init_schema
from neckline.scan import cluster
from neckline.scan.corr import PRICE_WINDOW_DAYS, load_return_window, trailing_window_start

logger = logging.getLogger(__name__)

TABLE = "leader_structure_daily"

# RS20 严格口径(K7 需求 1a):必须凑满整个 `PRICE_WINDOW_DAYS`(=20)窗口的观测,
# 少一天都不算——这是一个有明确窗口长度的命名指标("RS20"),不是"能算多少天
# 算多少天"的近似值。等于 `PRICE_WINDOW_DAYS` 而不是另一个独立数字,避免两处
# 各写一份"20"将来漂移。
MIN_OBS_FOR_RS = PRICE_WINDOW_DAYS

_COLUMNS = "trade_date, cluster_key, ts_code, rs_rank, limit_height, amount_share, role_mech, computed_at"
_UPSERT_SQL = f"INSERT OR REPLACE INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)"

_ROW_SCHEMA: Dict[str, pl.DataType] = {
    "trade_date": pl.String,
    "cluster_key": pl.String,
    "ts_code": pl.String,
    "rs_rank": pl.Int64,
    "limit_height": pl.Int64,
    "amount_share": pl.Float64,
    "role_mech": pl.String,
    "computed_at": pl.String,
}

_ROLE_LEADER, _ROLE_CORE, _ROLE_ELASTIC, _ROLE_UNKNOWN = "leader", "core", "elastic", "unknown"

_NEG_INF = float("-inf")


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rs_metric_by_code(price_window: pl.DataFrame, *, min_obs: int = MIN_OBS_FOR_RS) -> pl.DataFrame:
    """`ts_code -> (rs_metric, rs_n_obs)`:窗口内累计收益率 = RS20/`ret_20d`
    严格口径(见模块 docstring「RS20 的严格口径」节)。`price_window` 需含
    `ts_code`/`ret_1d`(`corr.load_return_window` 的输出形状)。观测数
    `< min_obs`(默认 = `PRICE_WINDOW_DAYS`,即窗口不满 20 天)→ `rs_metric=None`。"""
    if price_window.is_empty():
        return pl.DataFrame(schema={"ts_code": pl.String, "rs_metric": pl.Float64, "rs_n_obs": pl.Int64})
    g = price_window.group_by("ts_code").agg(
        (pl.col("ret_1d") + 1.0).product().alias("_gross"),
        pl.len().alias("rs_n_obs"),
    )
    return g.with_columns(
        pl.when(pl.col("rs_n_obs") >= min_obs).then(pl.col("_gross") - 1.0).otherwise(None).alias("rs_metric")
    ).select(["ts_code", "rs_metric", "rs_n_obs"])


def compute_leader_structure_for_day(
    trade_date: date,
    price_window: pl.DataFrame,
    clusters_today: pl.DataFrame,
    amounts_today: pl.DataFrame,
) -> pl.DataFrame:
    """纯函数(无 I/O)。`clusters_today` = `cluster.load_limit_clusters` 的输出;
    `amounts_today` 需含 `ts_code`/`amount`(当日 `daily.amount`,元)。"""
    if clusters_today.is_empty():
        return pl.DataFrame(schema=_ROW_SCHEMA)

    base = clusters_today.select(["cluster_key", "ts_code", "consecutive_days"]).unique()
    base = base.join(amounts_today.select(["ts_code", "amount"]), on="ts_code", how="left")
    base = base.with_columns(
        pl.when(pl.col("amount").sum().over("cluster_key") > 0)
        .then(pl.col("amount") / pl.col("amount").sum().over("cluster_key"))
        .otherwise(None)
        .alias("amount_share")
    )

    rs = rs_metric_by_code(price_window)
    base = base.join(rs.select(["ts_code", "rs_metric"]), on="ts_code", how="left")

    # —— rs_rank:RS20 降序 → 成交额降序 → ts_code 升序,再 ordinal(K7 需求 1a
    # 定死 tie-break,不得自行更改)。`rank(method="ordinal")` 的并列由行序打散
    # = 不确定性,必须先排定确定性 tie-break 再 ordinal(§五铁律/`_day_local_table`
    # 体例)。`amount` 缺失(数据缺口)按 -inf 参与 tie-break(排最后,不当 0——
    # 0 会把缺数据的票错误地排到"成交额最小"档之后的中间位置)。
    has_rs = base.filter(pl.col("rs_metric").is_not_null())
    if not has_rs.is_empty():
        ranked = has_rs.with_columns(
            pl.col("amount").fill_null(_NEG_INF).alias("_amt_tiebreak")
        ).sort(
            ["cluster_key", "rs_metric", "_amt_tiebreak", "ts_code"],
            descending=[False, True, True, False],
        ).with_columns(
            pl.col("rs_metric").rank(method="ordinal", descending=True).over("cluster_key").cast(pl.Int64).alias("rs_rank")
        )
        base = base.join(ranked.select(["cluster_key", "ts_code", "rs_rank"]), on=["cluster_key", "ts_code"], how="left")
    else:
        base = base.with_columns(pl.lit(None, dtype=pl.Int64).alias("rs_rank"))

    base = _attach_role_mech(base)

    return base.select(
        ["cluster_key", "ts_code", "rs_rank", "consecutive_days", "amount_share", "role_mech"]
    ).rename({"consecutive_days": "limit_height"}).with_columns(
        pl.lit(_d(trade_date)).alias("trade_date"), pl.lit(_now()).alias("computed_at")
    ).select(list(_ROW_SCHEMA))


def _attach_role_mech(base: pl.DataFrame) -> pl.DataFrame:
    """`rs_rank` → `role_mech`(K7 需求 1a 定死:只读 `rs_rank`,不混入连板
    高度/成交额占比,见模块 docstring)。`rs_rank` 缺失(RS20 算不出)→
    `unknown`。`n_ranked`(簇内有效排名的成员数,不含 `unknown`)决定
    core/elastic 分界,不含在 `unknown` 里的成员不拉低比例基数。"""
    n_ranked = base.filter(pl.col("rs_rank").is_not_null()).group_by("cluster_key").agg(
        pl.len().alias("_n_ranked")
    )
    out = base.join(n_ranked, on="cluster_key", how="left")
    out = out.with_columns((1 + pl.col("_n_ranked").fill_null(0) // 2).alias("_core_cutoff"))
    out = out.with_columns(
        pl.when(pl.col("rs_rank").is_null())
        .then(pl.lit(_ROLE_UNKNOWN))
        .when(pl.col("rs_rank") == 1)
        .then(pl.lit(_ROLE_LEADER))
        .when(pl.col("rs_rank") <= pl.col("_core_cutoff"))
        .then(pl.lit(_ROLE_CORE))
        .otherwise(pl.lit(_ROLE_ELASTIC))
        .alias("role_mech")
    )
    return out.drop(["_n_ranked", "_core_cutoff"])


def refresh_leader_structure(
    days: Iterable[date],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """批算 + upsert 落表(与 `cluster.py`/`corr.py` 同款"批量与逐日同一实现"分工,
    见两者 docstring)。**依赖当日 `limit_cluster_daily` 已算好**。"""
    init_schema(db_path)
    stats = {"days": 0, "rows": 0}
    for d in sorted(set(days)):
        clusters_today = cluster.load_limit_clusters(d, db_path=db_path)
        stats["days"] += 1
        if clusters_today.is_empty():
            continue
        codes = sorted(clusters_today["ts_code"].unique().to_list())
        window_start = trailing_window_start(d, PRICE_WINDOW_DAYS)
        price_window = load_return_window(window_start, d, parquet_dir=parquet_dir).filter(
            pl.col("ts_code").is_in(codes)
        )
        amounts_today = get_market_slice(d, table="daily", parquet_dir=parquet_dir)
        if amounts_today.is_empty():
            amounts_today = pl.DataFrame(schema={"ts_code": pl.String, "amount": pl.Float64})
        else:
            amounts_today = amounts_today.select(["ts_code", "amount"])
        frame = compute_leader_structure_for_day(d, price_window, clusters_today, amounts_today)
        if frame.is_empty():
            continue
        payload = list(frame.select(list(_ROW_SCHEMA)).iter_rows())
        with connection(db_path) as conn:
            conn.executemany(_UPSERT_SQL, payload)
        stats["rows"] += len(payload)
    return stats


def load_leader_structure(trade_date: date, *, db_path: Optional[Path] = None) -> pl.DataFrame:
    """在线唯一读入口:给定交易日的全部龙头结构行(空 = 当日无簇,合法结果,
    不现算)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE trade_date=?", (_d(trade_date),)).fetchall()
    if not rows:
        return pl.DataFrame(schema=_ROW_SCHEMA)
    return pl.DataFrame(rows, schema=_ROW_SCHEMA, orient="row")


__all__ = [
    "TABLE",
    "MIN_OBS_FOR_RS",
    "rs_metric_by_code",
    "compute_leader_structure_for_day",
    "refresh_leader_structure",
    "load_leader_structure",
]
