"""滚动相关预计算表 `corr_matrix_daily`(plan §五 V2-④,P0-23:EOD 预计算落表、
在线只读)。

**候选对集合 = `limit_cluster_daily` 的簇成员 ∪ 概念成分**(plan 原文,禁止全市场
N²)——本模块只在**已经是"同一簇"或"同一概念"**的股票之间算相关,不做任何
"两两比较全市场"的操作。两类 scope 各自的 `scope_key`:
    · 簇内对:`scope_key = cluster_key`(来自 `cluster.py` 当日输出,直接复用,
      不重新发明);
    · 概念内对:`scope_key = index_code`(概念板块代码本身已是稳定自然键,
      不需要再套一层 crc32)。

**规模上限(`MAX_SCOPE_MEMBERS_FOR_CORR`,工程常量,2026-08-02 本地真实数据
校准)**:板块池卫生线(`cluster.concept_membership_map`)剔除了"融资融券"
一类宽基标签,但仍会放行成分上千只的**合法**大主题板块(机器人概念 1213 只、
人工智能 1081 只等,见 `report/board_pool.py` docstring 校准记录)——对着
上千只票两两算相关是 `C(1000,2)≈50万` 对/板块的组合爆炸,既不是"共振"该有的
量级,也违背 P0-23"避免组合爆炸"的精神(虽然技术上仍是"板内",但板"内"已经
近似全市场)。超限 scope 直接跳过、不产出任何行(不是"该簇不存在",只是"这个
维度太宽,相关性在这个规模下没有共振意义"——同一票仍可能通过其它更窄的簇/
概念参与相关性计算)。

**上限定为 30(不是最初拟定的 60)的实测依据**:用 2026-07-22~24 三个真实交易日
冒烟(见 §五 V2-④ 完工记录),`MAX_SCOPE_MEMBERS_FOR_CORR=60` 时单日
`corr_matrix_daily` 达 ~9.7 万行(272 个 scope,数十个概念紧贴 60 人上限,
`C(60,2)=1770` 对/板块),3 天批算耗时 34s——量级不算离谱但明显是"是否真的
需要多算这些大板块"这个问题该有答案的时候。按同一批真实数据重算:上限 30 可把
总对数砍掉一半(~4.7 万行/日),上限 20 可砍掉约四分之三(~2.4 万行/日),而
"30 只股票仍同向"已经是相当明确的共振信号(远超最终篮子 1-3 只的量级),不需要
为了多观察几十只边际贡献有限的板块尾部成分而承担二次方增长的成本。**这不是
性能门禁下的强制修正**(生产机实测是 ⑯ 的事),是拿到真实数据后顺手做的合理
调参,如实记账;`_pairwise_corr` 逐对三次 polars 小调用的固定开销仍在,真要
push 到生产级吞吐量还需要按 scope 批量算相关矩阵(未来如需要再优化,不在本块
范围)。

**收益率窗口口径**:用**未复权** `daily.close/pre_close` 算 `ret_1d`(同
`report/industry_strength.py` 的既有论证——qfq 对同一行 `close`/`pre_close`
用同一标量缩放,比值精确抵消,窗口内若无除权事件两者数值相同;有除权事件时
`pre_close` 本身已包含除权调整,连续性不受影响)。**只读 `[window_start,
trade_date]` 这一段窗口**(`scan_table_range` 年份裁剪的 glob,不是
`industry_strength.py` 里那两个被禁止在线调用的"全历史 scan_parquet"入口)。

`corr=NULL` 语义(**禁写 0**,DDL 已注明):
    · 样本不足(`n_obs < MIN_OBS_FOR_CORR`)——窗口内两票重叠的非空交易日太少;
    · 常数序列(某票窗口内收益率标准差为 0,如极端地新股仅 1 个有效交易日,或
      连续停牌后仅剩单点)——相关系数在数学上未定义,不是"不相关"。
"""

from __future__ import annotations

import itertools
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import polars as pl

from neckline.data.market_data import scan_table_range
from neckline.db import connection, init_schema
from neckline.report.board_pool import apply_hygiene, count_members
from neckline.report.sectors import load_index_names, load_member_map
from neckline.facts import limitmap as cluster

logger = logging.getLogger(__name__)

TABLE = "corr_matrix_daily"

PRICE_WINDOW_DAYS = 20          # 滚动窗口(交易日),DDL `window` 列的取值来源,单一源
MIN_OBS_FOR_CORR = 10           # 少于此重叠观测数 → corr=NULL(样本不足)
MAX_SCOPE_MEMBERS_FOR_CORR = 30  # 单 scope 成员数上限,见模块 docstring「规模上限」实测校准

_COLUMNS = "trade_date, scope_key, code_a, code_b, window, corr, n_obs, computed_at"
_UPSERT_SQL = f"INSERT OR REPLACE INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)"

_ROW_SCHEMA: Dict[str, pl.DataType] = {
    "trade_date": pl.String,
    "scope_key": pl.String,
    "code_a": pl.String,
    "code_b": pl.String,
    "window": pl.Int64,
    "corr": pl.Float64,
    "n_obs": pl.Int64,
    "computed_at": pl.String,
}


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def trailing_window_start(trade_date: date, n: int = PRICE_WINDOW_DAYS) -> date:
    """`trade_date` 往前数 `n` 个交易日(含端点算法与 `data/limit_derived.py` 的
    尾部窗口惯例同源)。自然日缓冲 `n*3+15` 足够跨越春节等长假(20 个交易日
    ≈ 28 个自然日,3 倍缓冲留足冗余)。交易日历覆盖不足 `n` 天(如账套起点
    附近)时退回历史最早一天,不报错——窗口越界的后果由 `MIN_OBS_FOR_CORR`
    的样本量门槛自然兜住,不需要在这里特殊处理。"""
    from neckline.calendar import trading_days_between

    lo = trade_date - timedelta(days=n * 3 + 15)
    days = trading_days_between(lo, trade_date)
    if len(days) >= n:
        return days[-n]
    return days[0] if days else trade_date


def load_return_window(start: date, end: date, parquet_dir: Optional[Path] = None) -> pl.DataFrame:
    """`[start, end]` 全市场 `ts_code/trade_date/ret_1d`(未复权,见模块 docstring)。
    `scan_table_range` 已按年份裁剪 glob(P1-26/P0-23 纪律),不整表扫描。"""
    df = scan_table_range("daily", start, end, parquet_dir=parquet_dir)
    if df.is_empty():
        return pl.DataFrame(schema={"ts_code": pl.String, "trade_date": pl.Date, "ret_1d": pl.Float64})
    df = df.select(["ts_code", "trade_date", "close", "pre_close"]).filter(
        pl.col("close").is_not_null() & pl.col("pre_close").is_not_null() & (pl.col("pre_close") != 0)
    )
    return df.with_columns((pl.col("close") / pl.col("pre_close") - 1.0).alias("ret_1d")).select(
        ["ts_code", "trade_date", "ret_1d"]
    )


def build_scope_membership(
    trade_date: date, *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None
) -> Dict[str, List[str]]:
    """今日候选对集合(plan 原文:`limit_cluster_daily` 簇成员 ∪ 概念成分)。返回
    `{scope_key: sorted(codes)}`,已应用板块池卫生线与规模上限,`len(codes)<2`
    的 scope 不出现(单票没有"对")。"""
    scopes: Dict[str, List[str]] = {}

    clusters_today = cluster.load_limit_clusters(trade_date, db_path=db_path)
    if not clusters_today.is_empty():
        for (key,), sub in clusters_today.group_by(["cluster_key"]):
            codes = sorted(sub["ts_code"].unique().to_list())
            if 2 <= len(codes) <= MAX_SCOPE_MEMBERS_FOR_CORR:
                scopes[key] = codes

    member_map = load_member_map(parquet_dir=parquet_dir)   # con_code -> [index_code,...]
    if member_map:
        index_names = load_index_names(parquet_dir=parquet_dir)
        hygiene = apply_hygiene(index_names, count_members(member_map))
        inv: Dict[str, List[str]] = {}
        for con_code, idx_codes in member_map.items():
            for idx in idx_codes:
                if idx in hygiene.kept:
                    inv.setdefault(idx, []).append(con_code)
        for idx_code, codes in inv.items():
            uniq = sorted(set(codes))
            if 2 <= len(uniq) <= MAX_SCOPE_MEMBERS_FOR_CORR:
                scopes[idx_code] = uniq

    return scopes


def _pairwise_corr(wide: pl.DataFrame, code_a: str, code_b: str, min_obs: int) -> Tuple[Optional[float], int]:
    """成对相关(pairwise-complete,不是 listwise)——两票各自的缺失日互不牵连,
    只要求这一对自己重叠的非空观测数达标。纯 polars 实现(不引入 numpy 作为新的
    直接依赖,§3.1 钉死依赖清单原则——`requirements.txt` 里 numpy 目前只是
    polars/pandas 的传递依赖)。常数序列(std=0)→ `(None, n_obs)`,相关系数数学
    上未定义,禁写 0(见模块 docstring)。"""
    pair = wide.select([code_a, code_b]).drop_nulls()
    n_obs = pair.height
    if n_obs < min_obs:
        return None, n_obs
    std_a, std_b = pair[code_a].std(), pair[code_b].std()
    if not std_a or not std_b:
        return None, n_obs
    corr = pair.select(pl.corr(code_a, code_b)).item()
    return (float(corr) if corr is not None else None), n_obs


def compute_corr_for_day(
    trade_date: date,
    price_window: pl.DataFrame,
    scopes: Dict[str, List[str]],
    *,
    window: int = PRICE_WINDOW_DAYS,
    min_obs: int = MIN_OBS_FOR_CORR,
) -> pl.DataFrame:
    """纯函数(无 I/O):给定窗口内的收益率长表(`ts_code/trade_date/ret_1d`)与
    候选对 scope,算每对 `code_a<code_b` 的相关系数。`price_window` 只需覆盖
    `trailing_window_start(trade_date)..trade_date`(调用方负责截好)。"""
    if not scopes:
        return pl.DataFrame(schema=_ROW_SCHEMA)
    day_s = _d(trade_date)
    now = _now()
    rows: List[Tuple[str, str, str, str, int, Optional[float], int, str]] = []
    for scope_key, codes in scopes.items():
        sub = price_window.filter(pl.col("ts_code").is_in(codes))
        if sub.is_empty():
            continue
        wide = sub.pivot(on="ts_code", index="trade_date", values="ret_1d").sort("trade_date")
        present = [c for c in codes if c in wide.columns]
        for code_a, code_b in itertools.combinations(sorted(present), 2):
            corr, n_obs = _pairwise_corr(wide, code_a, code_b, min_obs)
            rows.append((day_s, scope_key, code_a, code_b, window, corr, n_obs, now))
    if not rows:
        return pl.DataFrame(schema=_ROW_SCHEMA)
    return pl.DataFrame(rows, schema=_ROW_SCHEMA, orient="row")


def refresh_corr_matrix(
    days: Iterable[date],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """批算 + upsert 落表。**依赖 `cluster.py` 已先算好当日 `limit_cluster_daily`**
    (`scripts/scan_layer.py` 按 cluster→corr→leader 的顺序调用)。同一份实现服务
    单日 refresh 与多日 bootstrap(无跨日递推状态,批量与逐日等价,见
    `cluster.refresh_limit_clusters` 同款分工说明)。"""
    init_schema(db_path)
    stats = {"days": 0, "rows": 0, "scopes": 0, "scopes_skipped_no_price": 0}
    for d in sorted(set(days)):
        scopes = build_scope_membership(d, db_path=db_path, parquet_dir=parquet_dir)
        stats["days"] += 1
        if not scopes:
            continue
        stats["scopes"] += len(scopes)
        all_codes = sorted({c for codes in scopes.values() for c in codes})
        window_start = trailing_window_start(d)
        price_window = load_return_window(window_start, d, parquet_dir=parquet_dir)
        price_window = price_window.filter(pl.col("ts_code").is_in(all_codes))
        frame = compute_corr_for_day(d, price_window, scopes)
        # scope 完全没有价格数据(如极端数据缺口)时 `compute_corr_for_day` 会
        # 悄悄跳过它(见该函数 `sub.is_empty()` 分支)——在此处对账记数,不让这类
        # scope 无声无息地消失(`verify_scan_layer` 的跨表覆盖检查会再抓一次)。
        covered = set(frame["scope_key"].unique().to_list()) if not frame.is_empty() else set()
        stats["scopes_skipped_no_price"] += len(set(scopes) - covered)
        if frame.is_empty():
            continue
        payload = list(frame.iter_rows())
        with connection(db_path) as conn:
            conn.executemany(_UPSERT_SQL, payload)
        stats["rows"] += len(payload)
    return stats


def load_corr_matrix(trade_date: date, *, db_path: Optional[Path] = None) -> pl.DataFrame:
    """在线唯一读入口:给定交易日的全部相关行(空 = 当日无候选对或全部样本不足,
    合法结果,不现算)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE trade_date=?", (_d(trade_date),)).fetchall()
    if not rows:
        return pl.DataFrame(schema=_ROW_SCHEMA)
    return pl.DataFrame(rows, schema=_ROW_SCHEMA, orient="row")


__all__ = [
    "TABLE",
    "PRICE_WINDOW_DAYS",
    "MIN_OBS_FOR_CORR",
    "MAX_SCOPE_MEMBERS_FOR_CORR",
    "trailing_window_start",
    "load_return_window",
    "build_scope_membership",
    "compute_corr_for_day",
    "refresh_corr_matrix",
    "load_corr_matrix",
]
