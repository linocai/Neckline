"""行业强度预计算物化表 `industry_strength_daily` 的读写单一通道(plan §五 v1.4-⑩,
§七 **P0-23**)。

**为什么有这个模块**:v1.4-② 把 `report/industry_strength.py` 立成 A2/B3 判据与排序键①
的唯一源,但它每次调用都对 `daily` 做**全历史 `scan_parquet`**(生产 1591 分区 / 784 万行)。
开发机 Mac <1s,生产(2 vCPU / 1.6G)**700M cap OOM-kill、1400M cap 600s 跑不完** ——
16:35 报告主链 / 信息卡端点(⚠ 当初还有问询台,已随 V2.1-① 整链退役)全部不可用,
且信息卡端点跑在常驻 `neckline.service` 内会把盘中哨兵拖进内存回收死循环
(**卡死不报错**)。用户 2026-07-29 裁定方案②
「**预计算落表**」:日更算一次落 SQLite,在线路径**只读表**。

**单一源不变式(勿破)**:表只是**缓存物化**,判据实现仍只有一份 —— 在
`report/industry_strength.py`(`_day_local_table` / `_attach_persist` / `next_persist_days`
三件套 + 两个常量)。本模块**从后者 import**,后者**不得反向 import 本模块**(依赖方向
store → industry_strength 单向,防循环)。三路等价单测(全量算 ≡ 逐日递推 ≡ 落表读回)
是这条不变式的机器证明。

**写侧铁律**:日更**只读当日那一个分区**(`pl.read_parquet(day_file_path("daily", d))`)。
**⚠ 不许用 `get_market_slice` / `scan_table_range` / `_scan_table`** —— 它们走
`year=*/**.parquet` 全 glob,会打开 1500+ 个 parquet footer,**正是本 P0 的病根之一**。
bootstrap 的 Pass 1 是唯一按年 glob 的地方(单年 ~244 分区),且只在生产旁路目录里跑一次。

**跨日的量只有 `persist_days` 一个**,靠「上一评定日的 `persist_days` + 今日
`is_strength_day` 标记」一步递推(`next_persist_days`)。于是「持续天数要看多远历史」这个
问题**不需要窗口来回答 —— 历史被压缩进了表里的一个整数**;既不截断真实 streak(方案①
被否决的理由),也不用每天重扫 784 万行。

**一个必须知道的语义差(不是 bug)**:表内历史行按**落表当时**的 `stock_basic.industry`
快照冻结,不因日后 `stock_basic` 刷新而回改;现算 `compute_industry_strength` 则一律用
**当前**快照重算全历史。两者在 `stock_basic` 变更后会有细微差异 —— 表侧更接近「当时看到
的世界」。**要重置就重跑 bootstrap(整表重算)**,不要写"自动检测行业变更并回改历史"的
机灵代码。

**口径指纹**:每行带落表当时的 `quantile` / `min_members`。读侧只接受与**当前常量**相等
的行,不等 → 视同缺行(走保险丝)+ WARNING「口径已变更,请重跑 bootstrap」。这条把
「静默混着两种口径的行」变成一次响亮的降级。
"""

from __future__ import annotations

import glob
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import polars as pl

from neckline.db import connection, init_schema
from neckline.report.industry_strength import (
    _MIN_MEMBERS,
    _STRENGTH_QUANTILE,
    IndustryStrength,
    _day_local_table,
    _ret1d_from_daily,
    load_industry_map,
    next_persist_days,
)

logger = logging.getLogger(__name__)

TABLE = "industry_strength_daily"

# `industry_strength_lag_days` 的哨兵值:表里**完全没有**行业强度数据。刻意不用 0
# (0 = 「新鲜」)也不用 None(契约是 int)——「没有」与「没看」必须能分开(§3.8);
# 与 `sectors.SECTOR_LAG_UNKNOWN` 同一惯例、同一取值。
INDUSTRY_STRENGTH_LAG_UNKNOWN = -1

_FLOAT_EPS = 1e-12   # 口径指纹 quantile 是 REAL,比相等一律带容差(同 sentinel/holding.py 体例)


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse_d(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _days_present(conn: sqlite3.Connection, lo: date, hi: date) -> set:
    """`[lo, hi]` 内表里**有行**的 `trade_date`(字符串集合)。`verify` ① 与断口检查共用
    这一个取数原语,判据各自在调用处写明(两者问的不是同一个问题,见 `_coverage_holes`)。"""
    return {
        r[0] for r in conn.execute(
            f"SELECT DISTINCT trade_date FROM {TABLE} WHERE trade_date>=? AND trade_date<=?",
            (_d(lo), _d(hi)),
        )
    }


def _coverage_holes(conn: sqlite3.Connection, lo: date, hi: date) -> List[str]:
    """`[lo, hi]` 与**表内已覆盖范围**的交集里,一行都没有的交易日 = 真正的「断口」(升序)。

    **为什么要跟 `verify` 的第①项分开**(v1.4 review 🟡-2):verify ① 问的是「你点名的
    这段区间该有的交易日都在吗」(显式断言,越界也算缺);本函数问的是「表里**两头都有
    数据**、中间却断了一截吗」—— 只有这种断口才会让 `_prev_persist` 把洞前那天当"昨天"、
    让 streak **桥过缺口**(漏跑日是强度日 → 后续 streak 低报,A2 该拦没拦;漏跑日会断裂
    → 高报误拦)。表尾还没落的今天、表头之前的远古,都**不是**断口:前者由新鲜度 lag
    如实披露、后者由保险丝披露,拿它们当断口只会制造假警报。"""
    bounds = conn.execute(f"SELECT MIN(trade_date), MAX(trade_date) FROM {TABLE}").fetchone()
    if not bounds or bounds[0] is None:
        return []                              # 空表:没有"两头",谈不上断口(bootstrap 领域)
    from neckline.calendar import trading_days_between

    lo = max(lo, _parse_d(bounds[0]))
    hi = min(hi, _parse_d(bounds[1]))
    if lo > hi:
        return []
    have = _days_present(conn, lo, hi)
    return sorted({_d(x) for x in trading_days_between(lo, hi)} - have)


def refresh_command_hint(start: Optional[date] = None, end: Optional[date] = None) -> str:
    """补算命令**原文**(单一源)。所有「表缺行」的 WARNING 都要带上它 —— 让运维看到日志
    就知道下一步敲什么,不用回头翻 plan。"""
    if start is None and end is None:
        return "python scripts/industry_strength.py refresh"
    a = _d(start) if start else _d(end)      # type: ignore[arg-type]
    b = _d(end) if end else _d(start)        # type: ignore[arg-type]
    return f"python scripts/industry_strength.py refresh --from {a} --to {b}"


# —————————————————————————————————————————————————————————————————————————————
# 新鲜度(→ `ReportOut.dataFreshness` 的三个新键,plan §五 v1.4-⑩-F)
# —————————————————————————————————————————————————————————————————————————————

@dataclass
class IndustryStrengthFreshness:
    """行业强度数据新鲜度(→ `ReportOut.dataFreshness` 三键)。

    **与板块数据新鲜度(`sectors.SectorDataFreshness`)是两个独立故障,不许合并成一个
    bool** —— 合并就分不清哪个坏了。故 `dataFreshness` 里既有的 `sectorDataDate` /
    `sectorLagDays` / `stale` 三键语义一个字不改(`stale` 仍**只**表板块数据)。

    `lag_days` = 交易日差(表内最新日 → 报告日);**`lag_days > 0` 即 `stale=True`,不给
    容忍度** —— 与 `ths_daily` 结构性落后 1 日的情况不同:行业强度用**当日** `daily` 算,
    16:05 日更当天就该有。完全无数据 → `lag_days = INDUSTRY_STRENGTH_LAG_UNKNOWN`(-1)
    且 `stale=True`(同 `SectorDataFreshness` 先例:`unavailable` 一定 stale;-1 是哨兵值
    不是「比 0 还新鲜」)。

    **`hole_days`(v1.4 review 🟡-2)**:近期区间里「两头有数据、中间断一截」的交易日数。
    **有断口即 `stale=True`,哪怕 `lag_days == 0`** —— 断口下 streak 是桥过缺口算出来的
    **错数**,而 `MAX(trade_date)` 照样等于今天;旧实现在这里报绿 = 拿错数冒充新鲜。
    刻意**不加第四个公开键**(⑩-F 契约就是三键,客户端已按三键解码):错数不许报绿这件事
    由 `stale` 承担,细节由 `note()` 如实说。"""

    latest_date: str    # 'YYYYMMDD';完全无数据 → ""
    lag_days: int
    stale: bool
    hole_days: int = 0

    @property
    def unavailable(self) -> bool:
        return self.lag_days == INDUSTRY_STRENGTH_LAG_UNKNOWN

    def to_public_dict(self) -> Dict[str, object]:
        # `industryStrengthDate` 表空时发 **null**(不是空串)—— 契约见 plan §五 v1.4-⑩-F。
        return {
            "industryStrengthDate": self.latest_date or None,
            "industryStrengthLagDays": self.lag_days,
            "industryStrengthStale": self.stale,
        }

    def note(self) -> str:
        """脚注文案(单一源,报告 markdown 与客户端横幅共用同一句口径)。新鲜 → 空串。"""
        if self.unavailable:
            return (
                "行业强度数据未就绪(表内无任何数据)——今日排序缺行业维度、"
                "题材持续天数与 A2/B3 本日不可得。"
            )
        if self.lag_days > 0:
            return (
                f"行业强度数据未就绪(最新至 {self.latest_date},落后 {self.lag_days} 个交易日)"
                "——今日排序缺行业维度、题材持续天数与 A2/B3 本日不可得。"
            )
        if self.hole_days:
            # 断口:数据看着"最新",但中间缺了几天 → 题材持续天数是桥过缺口算出来的,
            # 不是"没看"而是"看到的数可能不对"。文案必须说清是哪一种,别混成同一句。
            return (
                f"行业强度数据有断口(最新至 {self.latest_date},近期 {self.hole_days} 个交易日"
                "缺行)——题材持续天数可能桥过缺口失真,A2/B3 与排序行业维度请谨慎参考;"
                f"补算:{refresh_command_hint()}"
            )
        return ""

    def latest_label(self) -> str:
        """给「最新至 {X}」类文案用的短标签(完全无数据 → 「无数据」,不留空)。"""
        return self.latest_date or "无数据"


# 新鲜度顺带查断口的回看窗口(日历日)。**只看近期**:16:05 日更失败造出来的洞就在这
# 几天里,而远古断口早被后续 streak 消化、也超出「今天这份报告可不可信」的问题范围。
# 全历史断口体检是 `verify` 的活(`scripts/industry_strength.py verify`),不在读路径上跑。
_HOLE_LOOKBACK_DAYS = 21


def industry_strength_status(
    report_date: date, *, db_path: Optional[Path] = None
) -> IndustryStrengthFreshness:
    """表内最新行业强度日相对报告日落后几个交易日(plan §五 v1.4-⑩-E)+ 近期断口检查
    (v1.4 review 🟡-2:**有断口不许报绿**,见 `IndustryStrengthFreshness.hole_days`)。"""
    from datetime import timedelta

    from neckline.calendar import trading_days_between

    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(f"SELECT MAX(trade_date) FROM {TABLE}").fetchone()
        newest_s = row[0] if row else None
        if not newest_s:
            return IndustryStrengthFreshness("", INDUSTRY_STRENGTH_LAG_UNKNOWN, True)
        newest = _parse_d(newest_s)
        ref = min(report_date, newest)
        holes = _coverage_holes(conn, ref - timedelta(days=_HOLE_LOOKBACK_DAYS), ref)
    if newest >= report_date:
        return IndustryStrengthFreshness(newest_s, 0, bool(holes), len(holes))
    # 闭区间交易日数 - 1 = 两日之间隔了几个交易日(newest 当天不算落后),同
    # `sectors.compute_sector_freshness` 口径。
    lag = max(len(trading_days_between(newest, report_date)) - 1, 0)
    return IndustryStrengthFreshness(newest_s, lag, lag > 0 or bool(holes), len(holes))


# —————————————————————————————————————————————————————————————————————————————
# 读侧(在线路径**只走这里**)
# —————————————————————————————————————————————————————————————————————————————

def _fingerprint_ok(quantile: Optional[float], min_members: Optional[int]) -> bool:
    return (
        quantile is not None
        and min_members is not None
        and abs(float(quantile) - _STRENGTH_QUANTILE) < _FLOAT_EPS
        and int(min_members) == _MIN_MEMBERS
    )


def load_industry_strength(
    trade_date: date, *, db_path: Optional[Path] = None
) -> List[IndustryStrength]:
    """给定交易日的全行业强度(**在线唯一取数入口**,替代
    `industry_strength.compute_industry_strength`)。

    只返回 `industry_rank IS NOT NULL` 的行(= 当日成员数达 `_MIN_MEMBERS` 的行业,与现算
    返回集**逐位同集**),按 rank 升序。**表缺该日的行 → 空列表**(不崩、也不现算自愈:
    写在 16:05、读在 16:35,职责不混;历史回放更不该顺手写表)。口径指纹不匹配的行视同
    缺行 + WARNING。调用方按保险丝语义处理:**降级方向 = 不拦(放行)**,并如实披露
    「没看」而不是冒充「没有」。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT industry, median_ret, member_count, industry_rank, is_strength_day, "
            f"persist_days, quantile, min_members FROM {TABLE} "
            f"WHERE trade_date=? AND industry_rank IS NOT NULL ORDER BY industry_rank ASC",
            (_d(trade_date),),
        ).fetchall()
    out: List[IndustryStrength] = []
    stale_fingerprints = 0
    for industry, median_ret, member_count, rank, is_str, persist, q, mm in rows:
        if not _fingerprint_ok(q, mm):
            stale_fingerprints += 1
            continue
        out.append(
            IndustryStrength(
                industry=industry,
                median_ret=float(median_ret),
                member_count=int(member_count),
                industry_rank=int(rank),
                is_strength_day=bool(is_str),
                # 达标行的 persist_days 恒非空(与 is_strength_day 同时写);极端脏数据
                # (只可能来自手工改库)按 0 读,不崩 —— 判据侧 0 = 不触发 A2,方向仍是不拦。
                persist_days=int(persist) if persist is not None else 0,
            )
        )
    if stale_fingerprints:
        logger.warning(
            "行业强度口径已变更:%s 有 %d 行的 quantile/min_members 与现行常量(%.2f/%d)不符,"
            "已视同缺行。请重跑 bootstrap 整表重算:%s",
            _d(trade_date), stale_fingerprints, _STRENGTH_QUANTILE, _MIN_MEMBERS,
            "python scripts/industry_strength.py bootstrap",
        )
    return out


def load_industry_median_series(
    industry: str, start: date, end: date, *, db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """给定行业在 `[start, end]` 逐交易日的成员 `ret_1d` 中位数(**在线唯一取数入口**,
    替代 `industry_strength.industry_median_return_series`;④ 信息卡「行业分歧线」合成用)。

    **不过滤 `industry_rank IS NULL`** —— 该口径本来就不受 `_MIN_MEMBERS` 约束(指数合成
    只需要「这个行业当天整体涨跌多少」这一个统计量,不需要判「够不够格参与强度排名」),
    这正是 ⑩-A「落全部行业」的直接兑现。返回 `[{trade_date, median_ret, member_count}]`
    升序;窗口内该行业无行的日子**不出现**(如实反映「算不出中位数」,不补 0 —— 是否把
    缺口当"当日不涨不跌"是调用方的合成策略)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, median_ret, member_count FROM {TABLE} "
            f"WHERE industry=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date ASC",
            (industry, _d(start), _d(end)),
        ).fetchall()
    return [
        {"trade_date": _parse_d(td), "median_ret": float(m), "member_count": int(c)}
        for td, m, c in rows
    ]


# —————————————————————————————————————————————————————————————————————————————
# 写侧:16:05 日更增量(只读当日一个分区)
# —————————————————————————————————————————————————————————————————————————————

_UPSERT_SQL = (
    f"INSERT OR REPLACE INTO {TABLE} "
    "(trade_date, industry, median_ret, member_count, industry_rank, is_strength_day, "
    "persist_days, quantile, min_members, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
)


def _prev_persist(conn: sqlite3.Connection, industry: str, day: str) -> Optional[int]:
    """该行业**最近一条 `trade_date < day` 且 `is_strength_day IS NOT NULL`** 的
    `persist_days`。走 `(industry, trade_date)` 索引的 `ORDER BY ... DESC LIMIT 1`,
    **不扫全表**。查不到(该行业史上从未评定过)→ `None`。

    **严格早于 `day`** 是幂等的根:同日重跑时本日已有的行不参与自己的 `prev`,不双计。
    未评定日(`is_strength_day IS NULL`)被条件排除 = 「按不存在处理」,`prev` 天然原样
    往后传、不清零(与 `_attach_persist` 逐位等价)。"""
    row = conn.execute(
        f"SELECT persist_days FROM {TABLE} "
        f"WHERE industry=? AND trade_date<? AND is_strength_day IS NOT NULL "
        f"ORDER BY trade_date DESC LIMIT 1",
        (industry, day),
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def _load_day_panel(
    d: date, industry_of: Dict[str, str], parquet_dir: Optional[Path]
) -> Optional[pl.DataFrame]:
    """**只读当日那一个 parquet 分区** → 加 `ret_1d` → join 行业映射。分区文件不存在 →
    `None`(非交易日 / 数据未落地是正常态,调用方计入 `missing`,不报错)。"""
    from neckline.data.market_data import day_file_path

    p = day_file_path("daily", d, parquet_dir)
    if not p.exists():
        return None
    df = pl.read_parquet(p, columns=["ts_code", "trade_date", "close", "pre_close"])
    if df.is_empty():
        return None
    if df.schema.get("trade_date") != pl.Date:
        df = df.with_columns(pl.col("trade_date").cast(pl.Date))
    ret = _ret1d_from_daily(df)
    if ret.is_empty():
        return None
    ind_map = pl.DataFrame(
        {"ts_code": list(industry_of.keys()), "industry": list(industry_of.values())}
    )
    panel = ret.join(ind_map, on="ts_code", how="inner")
    return None if panel.is_empty() else panel


def _resolve_targets(
    days: Sequence[date], conn_days_max: Optional[str]
) -> List[date]:
    """补跑规则(定死,**两个方向都不许静默只补一天**):

      ① **向后延**:补算历史日 D 时若库内存在 > D 的行,那些行的 `persist_days` 会失真
         → 自动把处理区间延到库内最大交易日。
      ② **向前补洞(v1.4 review 🟡-2)**:目标日与库内最大日之间若隔着交易日(16:05 日更
         失败过一天以上就是这个形状),把缺口那几天**并进处理区间**。不补的话
         `_prev_persist` 会拿洞前那天当"昨天",streak 直接**桥过缺口**,而
         `MAX(trade_date)` 照样等于今天 → 新鲜度看板全绿 = **未披露的错数**(与保险丝
         「显式披露后不拦」的设计相反)。补不动时由 `refresh_industry_strength` 响亮报错,
         见那边的断口检查。

    每日成本 = 1 个分区,受控。"""
    targets = sorted(set(days))
    if not targets or not conn_days_max:
        return targets
    tbl_max = _parse_d(conn_days_max)
    from neckline.calendar import trading_days_between

    if tbl_max > targets[0]:                                   # ① 向后延到库内最大日
        span = trading_days_between(targets[0], tbl_max)
        return sorted(set(targets) | set(span)) if span else targets
    gap = [d for d in trading_days_between(tbl_max, targets[0]) if tbl_max < d < targets[0]]
    return sorted(set(targets) | set(gap)) if gap else targets  # ② 向前补洞


def refresh_industry_strength(
    days: Iterable[date],
    *,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """按**升序**逐日算行业强度并 upsert 落表(16:05 日更增量 + 修补 CLI 的共用实现)。

    每日:①**只读当日那一个分区**(缺文件 → 跳过并计入 `missing`);②`_ret1d_from_daily`
    → join `load_industry_map` → `_day_local_table`;③逐行业查 `prev`(严格早于当日的最近
    一条已评定行)→ `next_persist_days` 递推;④`INSERT OR REPLACE` + 口径指纹两列 +
    `computed_at`。**每天一个事务**,不拿一个大事务锁住库(生产库同时被常驻
    `neckline.service` 持有,WAL 下读不阻塞但长写锁会挡其它写)。

    **幂等**:同日重跑逐位相同(`prev` 查的是严格早于当日的行,不受本日已有行影响)。
    **补跑自动向后延 / 向前补洞**:见 `_resolve_targets`。

    **断口硬检查(v1.4 review 🟡-2)**:跑完回查一次 `_coverage_holes` —— 若表里仍留着
    「两头有数据、中间断一截」的交易日(补洞用的那几天连 `daily` 分区都没有,补不动),
    打 **ERROR** + 补算命令原文,并把洞的日期列表放进返回值 `holes`;CLI 据此 **exit 1**。
    **绝不静默桥接**:桥过去的 streak 会让 A2 该拦没拦 / 不该拦乱拦,而看板照样报绿。

    返回 `{"days": 处理天数, "rows": 落行数, "missing": 缺分区天数, "holes": [洞日…]}`。"""
    init_schema(db_path)
    given_days = sorted(set(days))          # 先物化(入参可能是生成器,下面要用两次)
    with connection(db_path) as conn:
        row = conn.execute(f"SELECT MAX(trade_date) FROM {TABLE}").fetchone()
    tbl_max_s = row[0] if row else None
    targets = _resolve_targets(given_days, tbl_max_s)
    if not targets:
        return {"days": 0, "rows": 0, "missing": 0, "holes": []}

    industry_of = load_industry_map(db_path)
    if not industry_of:
        logger.error(
            "行业强度日更:`stock_basic.industry` 为空(无行业映射),本次不落任何行。"
            "先跑 `python scripts/backfill.py`(bootstrap_metadata)补 stock_basic。"
        )
        return {"days": 0, "rows": 0, "missing": len(targets), "holes": []}

    extra = [d for d in targets if d not in set(given_days)]
    if extra:
        logger.info(
            "行业强度日更:因补算 %s 顺带重算至 %s(共 %d 个交易日,其中 %d 天是自动补的"
            "%s,每日只读 1 个分区)",
            _d(targets[0]), _d(targets[-1]), len(targets), len(extra),
            "缺口(表内最新 %s 与目标日之间的洞)" % tbl_max_s
            if tbl_max_s and _parse_d(tbl_max_s) < given_days[0] else "后续日(重算失真的 streak)",
        )

    done = rows_written = missing = 0
    now = _now()
    for d in targets:
        panel = _load_day_panel(d, industry_of, parquet_dir)
        if panel is None:
            missing += 1
            continue
        day_local = _day_local_table(panel, _STRENGTH_QUANTILE)
        if day_local.is_empty():
            missing += 1
            continue
        day_key = _d(d)
        with connection(db_path) as conn:      # 每天一个事务(见 docstring)
            payload = []
            for r in day_local.iter_rows(named=True):
                industry = r["industry"]
                is_str = r["is_strength_day"]
                persist = next_persist_days(_prev_persist(conn, industry, day_key), is_str)
                payload.append((
                    day_key, industry, float(r["median_ret"]), int(r["member_count"]),
                    None if r["industry_rank"] is None else int(r["industry_rank"]),
                    None if is_str is None else int(bool(is_str)),
                    None if persist is None else int(persist),
                    _STRENGTH_QUANTILE, _MIN_MEMBERS, now,
                ))
            conn.executemany(_UPSERT_SQL, payload)
        done += 1
        rows_written += len(payload)

    # —— 断口硬检查(v1.4 review 🟡-2):补完还留着洞就响亮报错,绝不静默桥接 ——
    lo = min(_parse_d(tbl_max_s), targets[0]) if tbl_max_s else targets[0]
    with connection(db_path) as conn:
        holes = _coverage_holes(conn, lo, targets[-1])
    if holes:
        logger.error(
            "行业强度日更:表内仍有 %d 个交易日**断口**(%s%s)—— 这些天两头都有数据、"
            "中间没有,streak 会直接桥过去(A2 该拦没拦 / 不该拦乱拦),而 `MAX(trade_date)` "
            "照样是最新日、新鲜度看板不会变红。多半是这几天的 `daily` 分区没落地:先补数据"
            "再跑 `%s`。",
            len(holes), ",".join(holes[:20]), "…" if len(holes) > 20 else "",
            refresh_command_hint(_parse_d(holes[0]), _parse_d(holes[-1])),
        )
    return {"days": done, "rows": rows_written, "missing": missing, "holes": holes}


# —————————————————————————————————————————————————————————————————————————————
# bootstrap 历史回填(**生产机分块两遍法**,plan §五 v1.4-⑩-D)
# —————————————————————————————————————————————————————————————————————————————

def available_years(parquet_dir: Optional[Path] = None) -> List[int]:
    """`daily` 表已落盘的年份(升序),供 bootstrap 逐年串行。"""
    from neckline.data.market_data import table_dir

    d = table_dir("daily", parquet_dir)
    if not d.exists():
        return []
    years = []
    for p in d.glob("year=*"):
        try:
            years.append(int(p.name.split("=", 1)[1]))
        except (IndexError, ValueError):
            continue
    return sorted(years)


def bootstrap_pass1_year(
    year: int, *, parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None
) -> Dict[str, int]:
    """**Pass 1(当日量,按年分块)**:`scan_parquet` **只 glob 该年**(单年 ~244 分区 /
    ~135 万行 × 4 列)→ `_ret1d_from_daily` → join 行业 → `_day_local_table` → upsert
    (`persist_days` 先留 **NULL**,由 Pass 2 统一算)。

    **为什么不在这里顺手算 streak**:streak 是跨年的量,按年块算会在年边界断裂。Pass 2
    纯表内递推,内存与年份数无关(~17.5 万行、几 MB)。"""
    from neckline.data.market_data import table_dir

    init_schema(db_path)
    d = table_dir("daily", parquet_dir)
    pattern = str(d / f"year={year}" / "*.parquet")
    if not glob.glob(pattern):
        logger.warning("bootstrap Pass1:year=%d 无任何分区,跳过", year)
        return {"days": 0, "rows": 0}
    industry_of = load_industry_map(db_path)
    if not industry_of:
        raise RuntimeError("`stock_basic.industry` 为空,无法 bootstrap 行业强度(先补 stock_basic)")

    ret = _ret1d_from_daily(
        pl.scan_parquet(pattern).select(["ts_code", "trade_date", "close", "pre_close"]).collect()
    )
    if ret.is_empty():
        return {"days": 0, "rows": 0}
    ind_map = pl.DataFrame(
        {"ts_code": list(industry_of.keys()), "industry": list(industry_of.values())}
    )
    panel = ret.join(ind_map, on="ts_code", how="inner")
    if panel.is_empty():
        return {"days": 0, "rows": 0}
    day_local = _day_local_table(panel, _STRENGTH_QUANTILE).sort(["trade_date", "industry"])
    now = _now()
    payload = [
        (
            _d(r["trade_date"]), r["industry"], float(r["median_ret"]), int(r["member_count"]),
            None if r["industry_rank"] is None else int(r["industry_rank"]),
            None if r["is_strength_day"] is None else int(bool(r["is_strength_day"])),
            None,                       # persist_days 留给 Pass 2
            _STRENGTH_QUANTILE, _MIN_MEMBERS, now,
        )
        for r in day_local.iter_rows(named=True)
    ]
    n_days = day_local["trade_date"].n_unique()
    with connection(db_path) as conn:
        conn.executemany(_UPSERT_SQL, payload)
    return {"days": int(n_days), "rows": len(payload)}


def bootstrap_pass2_streak(*, db_path: Optional[Path] = None, batch: int = 5000) -> Dict[str, int]:
    """**Pass 2(streak,纯表内)**:按 `(industry, trade_date)` 升序读回全量 → 逐行业用
    `next_persist_days` 递推 → 回写 `persist_days`。**这一遍完全不碰 parquet**,内存与年份
    数无关;分批提交(默认每 ≤5000 行一批),不拿一个大事务锁住库。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT industry, trade_date, is_strength_day, persist_days FROM {TABLE} "
            f"ORDER BY industry ASC, trade_date ASC"
        ).fetchall()

    updates: List[Tuple[Optional[int], str, str]] = []
    cur_industry: Optional[str] = None
    prev: Optional[int] = None
    for industry, td, is_str, stored in rows:
        if industry != cur_industry:
            cur_industry, prev = industry, None
        val = next_persist_days(prev, None if is_str is None else bool(is_str))
        if val is not None:
            prev = val          # 未评定日:`val is None`,`prev` **原样往后传**、不清零
        if val != (None if stored is None else int(stored)):
            updates.append((val, td, industry))

    written = 0
    for i in range(0, len(updates), batch):
        chunk = updates[i:i + batch]
        with connection(db_path) as conn:
            conn.executemany(
                f"UPDATE {TABLE} SET persist_days=? WHERE trade_date=? AND industry=?", chunk
            )
        written += len(chunk)
    return {"rows": len(rows), "updated": written}


def bootstrap_industry_strength(
    years: Optional[Sequence[int]] = None,
    *,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    pass2: bool = True,
) -> Dict[str, Any]:
    """两遍法全跑(本地演练 / 小库用;**生产机上一律逐年串行调 `bootstrap_pass1_year`**,
    每块之间看一次 `free -m` 与 `load`,见 plan §五 v1.4-⑩-D 的探针纪律)。"""
    ys = list(years) if years is not None else available_years(parquet_dir)
    per_year: Dict[int, Dict[str, int]] = {}
    for y in ys:
        per_year[y] = bootstrap_pass1_year(y, parquet_dir=parquet_dir, db_path=db_path)
    out: Dict[str, Any] = {
        "years": ys,
        "per_year": per_year,
        "days": sum(v["days"] for v in per_year.values()),
        "rows": sum(v["rows"] for v in per_year.values()),
    }
    if pass2:
        out["pass2"] = bootstrap_pass2_streak(db_path=db_path)
    return out


def bootstrap_recent_days(
    end: date, n: int = 250, *, parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None
) -> Dict[str, int]:
    """**⑩-D 退路**(写死,免临场拍脑袋):若首块(2020)峰值 > 500M 或耗时 > 10 分钟 →
    放弃全历史,**只回填最近 N=250 个交易日**。走逐日路径(每日只读 1 个分区,内存恒定),
    `persist_days` 由递推一次算到位,不需要 Pass 2。

    **代价必须显式记账**:早于起点的历史回放按「表缺行 → 降级 + 标注」处理(⑩-E 保险丝),
    **不许静默按 0**;退路一旦生效要记进 §九 + `~/hz_info.md` + §七 挂一条「历史回放早于
    X 日不可用」。"""
    from neckline.calendar import trading_days_between

    span = trading_days_between(date(end.year - 3, 1, 1), end)
    return refresh_industry_strength(span[-n:], parquet_dir=parquet_dir, db_path=db_path)


# —————————————————————————————————————————————————————————————————————————————
# 自检(CLI `verify` 与单测共用同一实现,plan §五 v1.4-⑩-D 验证判据 2)
# —————————————————————————————————————————————————————————————————————————————

def verify_industry_strength(
    start: Optional[date] = None, end: Optional[date] = None, *, db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """三项自检(**CLI 与单测共用同一实现**,不各写一遍):

      ① **交易日无洞** —— 表内 `trade_date` 集合 == `trade_cal` 在该区间的交易日集合;
      ② **streak 自洽** —— 由 `is_strength_day` 序列重算的连续天数 == 库内 `persist_days`,
         逐行相等(**从各行业全部历史起算**,不是从窗口起算 —— 窗口首日的 streak 依赖更早
         的历史,只看窗口会误判);
      ③ **口径指纹一致** —— 全表 `quantile` / `min_members` 唯一且等于现行常量。

    返回 `{"ok":bool, ...}`;`ok=False` 时各项明细里有逐条问题(CLI 打印 + exit 1)。"""
    from neckline.calendar import trading_days_between

    init_schema(db_path)
    with connection(db_path) as conn:
        bounds = conn.execute(f"SELECT MIN(trade_date), MAX(trade_date) FROM {TABLE}").fetchone()
        if not bounds or bounds[0] is None:
            return {
                "ok": False, "rows": 0, "reason": "表为空(未 bootstrap / 未日更)",
                "missing_days": [], "extra_days": [], "streak_mismatches": [], "fingerprints": [],
            }
        lo = start or _parse_d(bounds[0])
        hi = end or _parse_d(bounds[1])
        lo_s, hi_s = _d(lo), _d(hi)

        # ⚠ 本项的判据是「你点名的这段区间该有的交易日都在吗」(越界也算缺),与读路径上
        # 的断口检查 `_coverage_holes`(只认"两头有数据、中间断一截")刻意不同,见后者注释。
        have_days = _days_present(conn, lo, hi)
        fingerprints = conn.execute(
            f"SELECT DISTINCT quantile, min_members FROM {TABLE}"
        ).fetchall()
        n_rows = conn.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE trade_date>=? AND trade_date<=?", (lo_s, hi_s)
        ).fetchone()[0]
        streak_rows = conn.execute(
            f"SELECT industry, trade_date, is_strength_day, persist_days FROM {TABLE} "
            f"WHERE trade_date<=? ORDER BY industry ASC, trade_date ASC",
            (hi_s,),
        ).fetchall()

    cal_days = {_d(x) for x in trading_days_between(lo, hi)}
    missing_days = sorted(cal_days - have_days)
    extra_days = sorted(have_days - cal_days)

    mismatches: List[Dict[str, Any]] = []
    cur_industry: Optional[str] = None
    prev: Optional[int] = None
    for industry, td, is_str, stored in streak_rows:
        if industry != cur_industry:
            cur_industry, prev = industry, None
        val = next_persist_days(prev, None if is_str is None else bool(is_str))
        if val is not None:
            prev = val
        if td < lo_s:
            continue          # 窗口之前的行只用来把 streak 推到窗口首日,不参与断言
        if val != (None if stored is None else int(stored)):
            mismatches.append({"industry": industry, "trade_date": td, "stored": stored, "expected": val})

    bad_fp = [
        {"quantile": q, "min_members": mm}
        for q, mm in fingerprints if not _fingerprint_ok(q, mm)
    ]
    ok = not missing_days and not extra_days and not mismatches and not bad_fp and len(fingerprints) == 1
    return {
        "ok": ok,
        "range": [lo_s, hi_s],
        "rows": int(n_rows),
        "days": len(have_days),
        "missing_days": missing_days,
        "extra_days": extra_days,
        "streak_mismatches": mismatches,
        "fingerprints": [{"quantile": q, "min_members": mm} for q, mm in fingerprints],
        "bad_fingerprints": bad_fp,
    }


__all__ = [
    "TABLE",
    "INDUSTRY_STRENGTH_LAG_UNKNOWN",
    "IndustryStrengthFreshness",
    "industry_strength_status",
    "load_industry_strength",
    "load_industry_median_series",
    "refresh_industry_strength",
    "refresh_command_hint",
    "available_years",
    "bootstrap_pass1_year",
    "bootstrap_pass2_streak",
    "bootstrap_industry_strength",
    "bootstrap_recent_days",
    "verify_industry_strength",
]
