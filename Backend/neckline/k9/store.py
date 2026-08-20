"""K9 策略层的落库(PROJECT_PLAN §5.4.7 第 8 步 / §5.4.8 / §5.8.2)。

| 载体 | 装什么 | 覆盖语义 |
|---|---|---|
| SQLite `k9_runs` | 一次运行的账:参数包版本 / 事实包版本 / 档位 / 逐形态计数 / 边界计数 | 同 `(trade_date, strategy)` **幂等重写**(同包同参必然算出同一行) |
| SQLite `k9_channel_hits` | 逐条召回记录(跨日接力分的数据源) | **append-only** |
| SQLite `k9_listing_entries` | **定稿**的清单 + **冻结的申万绑定** | 同 `(trade_date, ts_code)` 幂等重写 |
| parquet `k9_disposition` | 全市场逐票的处置(覆盖率归因的原料) | 同日重写 |

🔴 **`k9_listing_entries` 落库 = 清单定稿**(§5.5)。定稿本应发生在**解释层之后**
(消息面剔除 + 后备补位),但解释层是 S9 的产物 —— 现在还不存在。故本片把
「**是谁定的稿**」做成 `k9_runs.listing_finalized_by` 这一列(`'k9'` / `'explain'`),
让「这份清单还没过消息面」成为一个**查得到的事实**,而不是一句注释。
S9 接入后由编排器改传 `'explain'`,⛔ 不许把这一列删掉或恒填 `'explain'`。

🔴 **申万归属在写入时即冻结**(架构 §5.1 / §5.8.2):`sw_l2_code` / `sw_l2_name` 随行
写死,事后申万调整**不回改** —— 「行业分与选票分能拆开」的唯一依据就是这个冻结。

🛑 **本文件⛔ 不 import `neckline.data.market_data`**(守门 G3:取数唯一来源是事实包)。
`k9_disposition` 的 parquet 因此由本文件**直接**用 polars 写,并自带一张显式 schema
(`_DISPOSITION_SCHEMA`)—— 这比 §12 坑 2 说的「在 `TABLE_FLOAT_COLS` 里声明」更强:
那条坑讲的是「向既有分区看齐」可能被脏基准带偏,而这里根本不看齐任何分区,每次都
按同一张 schema 造。⚠ 目录布局仍必须与全仓一致(`year=YYYY/YYYYMMDD.parquet`),
守门单测拿 `market_data.day_file_path` 对拍(测试可以 import,生产路径不行)。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import polars as pl

from neckline.config import settings
from neckline.db import connection, init_schema
from neckline.k9.contract import Pattern, SeatKind, Shortlist, Tier
from neckline.k9.ranking import RelayRecord

logger = logging.getLogger(__name__)

RUNS_TABLE = "k9_runs"
HITS_TABLE = "k9_channel_hits"
LISTING_TABLE = "k9_listing_entries"
PARQUET_TABLE = "k9_disposition"

#: 谁定的稿(见模块 docstring)。⛔ 闭合两值,不许现编。
FINALIZED_BY_K9 = "k9"            # 解释层尚未接入,策略层的席位直接定稿
FINALIZED_BY_EXPLAIN = "explain"  # S9 起:消息面剔除 + 后备补位之后定稿

_DISPOSITION_SCHEMA: Dict[str, pl.DataType] = {
    "trade_date": pl.Date,
    "ts_code": pl.String,
    "excluded_by": pl.String,          # 9 条排除项之一;null = 没被边界排除
    "recalled_patterns_json": pl.String,
    "tier": pl.String,                 # null = 没被召回
    "score": pl.Float64,
    "rank": pl.Int64,
    "seated": pl.Int64,                # 0/1
    "seat_kind": pl.String,            # floor|free|null
    "news_excluded": pl.Int64,         # 0/1(S9 之前恒 0 = 还没有人查过公告)
}


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def disposition_path(trade_date: date, parquet_dir: Optional[Path] = None) -> Path:
    """`<root>/k9_disposition/year=YYYY/YYYYMMDD.parquet`。

    ⚠ 布局与全仓其它 parquet 日分区表**逐字相同**(守门单测拿
    `market_data.day_file_path` 对拍)。这里自己拼而不 import 那个模块,是为了不破
    G3 那条「策略层不 import 取数模块」的边界。
    """
    root = parquet_dir or settings.parquet_dir
    return Path(root) / PARQUET_TABLE / f"year={trade_date.year}" / f"{_d(trade_date)}.parquet"


# ══════════════════════════════════════════════════════════════════════════
# 写
# ══════════════════════════════════════════════════════════════════════════

def new_run_id() -> str:
    return uuid.uuid4().hex


def save_run(
    *,
    run_id: str,
    shortlist: Shortlist,
    boundary_counts: Mapping[str, int],
    over_strict: bool,
    relaxed_streak: int,
    listing_finalized_by: str,
    db_path: Optional[Path] = None,
) -> None:
    """一次运行的账(幂等重写)。

    ⚠ **每一项都要能事后回答「那天为什么是这样」**:用了哪版参数包、哪版事实包、
    走的哪一档、每个形态严格 / 放宽各几只、9 条边界各排掉多少只。
    """
    if listing_finalized_by not in (FINALIZED_BY_K9, FINALIZED_BY_EXPLAIN):
        raise ValueError(
            f"listing_finalized_by 只能是 {FINALIZED_BY_K9!r} / {FINALIZED_BY_EXPLAIN!r},"
            f"收到 {listing_finalized_by!r}")
    init_schema(db_path)
    payload = (
        run_id, _d(shortlist.trade_date), shortlist.strategy,
        shortlist.params_version, shortlist.pack_id, shortlist.pack_version,
        shortlist.tier_used.value, shortlist.strict_candidates, shortlist.relaxed_candidates,
        shortlist.size, int(shortlist.capacity_short), int(over_strict), relaxed_streak,
        json.dumps(shortlist.channel_counts, ensure_ascii=False, sort_keys=True),
        json.dumps(dict(boundary_counts), ensure_ascii=False, sort_keys=True),
        json.dumps([p.value for p in shortlist.absent_patterns], ensure_ascii=False),
        json.dumps(list(shortlist.dropped_by_heat_absent), ensure_ascii=False),
        listing_finalized_by, _now(),
    )
    with connection(db_path) as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO {RUNS_TABLE} "
            "(run_id, trade_date, strategy, params_package_version, pack_id, pack_version, "
            " tier_used, strict_candidates, relaxed_candidates, seated_count, capacity_short, "
            " over_strict, relaxed_streak, channel_counts_json, boundary_counts_json, "
            " absent_patterns_json, dropped_heat_absent_json, listing_finalized_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            payload,
        )


def save_channel_hits(
    *,
    run_id: str,
    trade_date: date,
    hits: Sequence,
    seated_codes: Sequence[str],
    db_path: Optional[Path] = None,
) -> int:
    """逐条召回记录,**append-only**(§5.4.6:跨日接力分的数据源)。

    ⚠ 同一天重跑会再落一份 —— 这是刻意的(append-only 台账不改历史)。
    跨日接力分按 `(trade_date, pattern)` 去重,重复行⛔ 不会把分算高
    (见 `ranking.relay_counts`)。
    """
    init_schema(db_path)
    seated = set(seated_codes)
    now = _now()
    rows = [
        (run_id, _d(trade_date), h.ts_code, h.pattern.value, h.tier.value,
         int(h.ts_code in seated),
         json.dumps(dict(h.strength), ensure_ascii=False, sort_keys=True), now)
        for h in hits
    ]
    if not rows:
        return 0
    with connection(db_path) as conn:
        conn.executemany(
            f"INSERT INTO {HITS_TABLE} "
            "(run_id, trade_date, ts_code, pattern, tier, seated, strength_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def save_listing(
    *, run_id: str, shortlist: Shortlist, db_path: Optional[Path] = None
) -> int:
    """**清单定稿**(§5.5)。同 `(trade_date, ts_code)` 幂等重写。

    🔴 `sw_l2_code` / `sw_l2_name` 在这里**冻结**:事后申万调整不回改(架构 §5.1)。
    """
    init_schema(db_path)
    now = _now()
    rows = [
        (_d(shortlist.trade_date), e.ts_code, run_id, shortlist.strategy, e.name,
         e.sw_l2_code, e.sw_l2_name,
         json.dumps([p.value for p in e.patterns], ensure_ascii=False),
         e.primary_pattern.value, e.tier.value,
         None if e.seat_kind is None else e.seat_kind.value,
         e.rank, e.score, e.industry_heat_score, e.pattern_strength_score, e.relay_score,
         now)
        for e in shortlist.entries
    ]
    with connection(db_path) as conn:
        conn.execute(
            f"DELETE FROM {LISTING_TABLE} WHERE trade_date=? AND strategy=?",
            (_d(shortlist.trade_date), shortlist.strategy),
        )
        if rows:
            conn.executemany(
                f"INSERT INTO {LISTING_TABLE} "
                "(trade_date, ts_code, run_id, strategy, name, sw_l2_code, sw_l2_name, "
                " patterns_json, primary_pattern, tier, seat_kind, rank, score, "
                " industry_heat_score, pattern_strength_score, relay_score, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
    return len(rows)


def mark_listing_finalized_by(
    trade_date: date, *, finalized_by: str, seated_count: int,
    strategy: str = "K9", db_path: Optional[Path] = None,
) -> bool:
    """把「**是谁定的稿**」改成 `'explain'`(S9 接入后由编排器调)。

    🔴 只动这两列 —— ⛔ 不重写整行运行账:那一行记的是**策略层**那次运行
    (参数包版本 / 事实包版本 / 逐形态计数 / 边界计数),解释层没有资格改它。
    `seated_count` 要跟着动,因为剔除 + 补位之后清单上的只数可能变了。

    返回 `True` = 真的改到了那一行(`False` = 那天根本没有运行账,⛔ 不静默造一行)。
    """
    if finalized_by not in (FINALIZED_BY_K9, FINALIZED_BY_EXPLAIN):
        raise ValueError(
            f"listing_finalized_by 只能是 {FINALIZED_BY_K9!r} / {FINALIZED_BY_EXPLAIN!r},"
            f"收到 {finalized_by!r}")
    init_schema(db_path)
    with connection(db_path) as conn:
        cur = conn.execute(
            f"UPDATE {RUNS_TABLE} SET listing_finalized_by=?, seated_count=? "
            "WHERE trade_date=? AND strategy=?",
            (finalized_by, int(seated_count), _d(trade_date), strategy),
        )
        return bool(cur.rowcount)


def save_disposition(
    trade_date: date, rows: Sequence[Mapping[str, object]],
    *, parquet_dir: Optional[Path] = None,
) -> Path:
    """全市场逐票处置 → parquet 日分区(§5.4.8)。

    5500 行/天,小;它让「昨天为什么没选中这只涨停票」变成一次查表而不是一次考古。
    """
    frame = pl.DataFrame(list(rows), schema=_DISPOSITION_SCHEMA)
    path = disposition_path(trade_date, parquet_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.sort("ts_code").write_parquet(path)
    return path


# ══════════════════════════════════════════════════════════════════════════
# 读
# ══════════════════════════════════════════════════════════════════════════

def load_disposition(
    trade_date: date, *, parquet_dir: Optional[Path] = None
) -> pl.DataFrame:
    """某日的全市场处置。文件不在 → 空表(⛔ 不抛:那天没跑过是可读出来的事实)。"""
    path = disposition_path(trade_date, parquet_dir)
    if not path.exists():
        return pl.DataFrame(schema=_DISPOSITION_SCHEMA)
    return pl.read_parquet(path)


def load_listing_codes(
    trade_date: date, *, strategy: str = "K9", db_path: Optional[Path] = None
) -> List[str]:
    """某日清单上的全部 `ts_code`(升序)。空 = 那天没有清单。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        return [
            r[0] for r in conn.execute(
                f"SELECT ts_code FROM {LISTING_TABLE} "
                "WHERE trade_date=? AND strategy=? ORDER BY ts_code",
                (_d(trade_date), strategy),
            ).fetchall()
        ]


def load_listing_membership(
    start: date, end: date, codes: Sequence[str], *, strategy: str = "K9",
    db_path: Optional[Path] = None,
) -> Dict[Tuple[str, str], Dict[str, object]]:
    """`[start, end]` × `codes` 里**上过清单**的那些:`(trade_date, ts_code) → {rank,
    patterns, primary_pattern, tier, seat_kind}`。键不存在 = 那天这只票不在清单上。

    🔴 **一次查询**取全,⛔ 别按日循环 —— `init_schema()` 每次都要重跑整份 schema
    脚本(S11 复盘装订的 40 天窗口会踩这个)。

    ⚠ 它是**只读材料通道**:S11 的交割单分析台拿它回答「我买的这只,系统那天选没选」。
    ⛔ 反方向不成立 —— `scorecard/**` 绝不许 import `neckline.review`(架构 §五:
    三条成绩线互不进入对方的分子分母,守门单测锁死)。
    """
    wanted = [c for c in dict.fromkeys(codes) if c]
    if not wanted or start > end:
        return {}
    init_schema(db_path)
    placeholders = ",".join("?" for _ in wanted)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, ts_code, rank, patterns_json, primary_pattern, tier, seat_kind "
            f"FROM {LISTING_TABLE} WHERE trade_date>=? AND trade_date<=? AND strategy=? "
            f"AND ts_code IN ({placeholders}) ORDER BY trade_date, rank",
            (_d(start), _d(end), strategy, *wanted),
        ).fetchall()
    return {
        (r[0], r[1]): {
            "rank": int(r[2]), "patterns": json.loads(r[3]),
            "primary_pattern": r[4], "tier": r[5], "seat_kind": r[6],
        }
        for r in rows
    }


def load_listing(
    trade_date: date, *, strategy: str = "K9", db_path: Optional[Path] = None
) -> List[Dict[str, object]]:
    """某日清单的全部行(按名次升序),供报告层渲染。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT ts_code, name, sw_l2_code, sw_l2_name, patterns_json, primary_pattern, "
            f"tier, seat_kind, rank, score, industry_heat_score, pattern_strength_score, "
            f"relay_score FROM {LISTING_TABLE} WHERE trade_date=? AND strategy=? ORDER BY rank",
            (_d(trade_date), strategy),
        ).fetchall()
    return [
        {
            "ts_code": r[0], "name": r[1], "sw_l2_code": r[2], "sw_l2_name": r[3],
            "patterns": json.loads(r[4]), "primary_pattern": r[5], "tier": r[6],
            "seat_kind": r[7], "rank": int(r[8]), "score": float(r[9]),
            "industry_heat_score": r[10], "pattern_strength_score": r[11],
            "relay_score": r[12],
        }
        for r in rows
    ]


def load_run(
    trade_date: date, *, strategy: str = "K9", db_path: Optional[Path] = None
) -> Optional[Dict[str, object]]:
    """某日的运行账。`None` = 那天策略层没跑过(⛔ 不是「跑了没结果」)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        r = conn.execute(
            f"SELECT run_id, params_package_version, pack_id, pack_version, tier_used, "
            f"strict_candidates, relaxed_candidates, seated_count, capacity_short, "
            f"over_strict, relaxed_streak, channel_counts_json, boundary_counts_json, "
            f"absent_patterns_json, dropped_heat_absent_json, listing_finalized_by, created_at "
            f"FROM {RUNS_TABLE} WHERE trade_date=? AND strategy=?",
            (_d(trade_date), strategy),
        ).fetchone()
    if r is None:
        return None
    return {
        "run_id": r[0], "params_package_version": r[1], "pack_id": r[2], "pack_version": r[3],
        "tier_used": r[4], "strict_candidates": int(r[5]), "relaxed_candidates": int(r[6]),
        "seated_count": int(r[7]), "capacity_short": bool(r[8]), "over_strict": bool(r[9]),
        "relaxed_streak": int(r[10]), "channel_counts": json.loads(r[11]),
        "boundary_counts": json.loads(r[12]), "absent_patterns": json.loads(r[13]),
        "dropped_heat_absent": json.loads(r[14]), "listing_finalized_by": r[15],
        "created_at": r[16],
    }


def relaxed_streak_before(
    trade_date: date, *, strategy: str = "K9", db_path: Optional[Path] = None
) -> int:
    """截至(不含)`trade_date` 为止,连续多少天是靠放宽档跑的(K9 §五-8)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT tier_used FROM {RUNS_TABLE} WHERE trade_date<? AND strategy=? "
            "ORDER BY trade_date DESC",
            (_d(trade_date), strategy),
        ).fetchall()
    streak = 0
    for (tier_used,) in rows:
        if tier_used != Tier.RELAXED.value:
            break
        streak += 1
    return streak


#: 上方机械空间在 `k9_channel_hits.strength_json` 里的两个落点与各自的符号。
#:
#: 🔴 **唯一源仍是 `k9/upside_room.py`**:形态 1 存的是 `score_room_far(pct)` = **原值**,
#: 形态 3 存的是 `score_room_near(pct)` = **负值**(反向打分,裁定 1 / K9 §3.4)。
#: 这张表只负责把那个符号**反过来读回原值**,⛔ 不在这里重算任何东西。
#: ⚠ 形态 2 / 4 **根本不看这一项**(K9 §3.3 / §3.5 的强度性里没有它)—— 只被 p2/p4
#: 召回的票**没有**上方机械空间,那是「本形态不看它」而不是「值是 0」。
_UPSIDE_ROOM_SOURCES: Tuple[Tuple[str, str, float], ...] = (
    ("p1", "upsideRoomFar", 1.0),
    ("p3", "upsideRoomNear", -1.0),
)


def load_upside_room_mech(
    trade_date: date, *, db_path: Optional[Path] = None
) -> Dict[str, float]:
    """某日逐票的**上方机械空间**原值(`upside_room_mech_pct`),从召回记录反读。

    键不存在 = 这只票当天没有被 p1 / p3 召回过 → **本形态不看这一项**
    (⛔ 调用方不许补 0:「不看」与「没有空间」是两件事)。

    ⚠ **反读而不是新开一列**:`k9_channel_hits.strength_json` 已经原样冻着这个读数
    (p1 正向、p3 取负,见 `_UPSIDE_ROOM_SOURCES`),再给 `k9_listing_entries` 加一列
    等于让同一个数有两处落点、两处都可能漂。
    ⚠ **命名铁律(裁定 1)**:这里读回来的是**上方机械空间**(机械、排序用),
    与预案层的**第一压力位**(LLM 判断)是两个量,⛔ 永不互相顶替。
    """
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT ts_code, pattern, strength_json FROM {HITS_TABLE} WHERE trade_date=?",
            (_d(trade_date),),
        ).fetchall()
    out: Dict[str, float] = {}
    for ts_code, pattern, raw in rows:
        for want_pattern, key, sign in _UPSIDE_ROOM_SOURCES:
            if pattern != want_pattern:
                continue
            try:
                value = json.loads(raw).get(key)
            except (TypeError, ValueError):
                logger.warning("[k9] %s %s 的 strength_json 解不出,跳过", trade_date, ts_code)
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[ts_code] = float(value) * sign
    return out


def load_relay_records(
    *, start: date, end: date, source_table: str, strategy: str = "K9",
    db_path: Optional[Path] = None,
) -> List[RelayRecord]:
    """跨日接力分的原料:`[start, end]` 里「哪只票在哪天被哪个形态选中过」。

    `source_table` 由 `ranking.RELAY_TABLE_OF[params.ranking.relaySource]` 给出
    —— **全映射**,⛔ 本函数不认识第三张表(传进来就抛)。
    """
    if source_table == HITS_TABLE:
        sql = (f"SELECT trade_date, ts_code, pattern FROM {HITS_TABLE} "
               "WHERE trade_date>=? AND trade_date<=?")
        args: Tuple = (_d(start), _d(end))
    elif source_table == LISTING_TABLE:
        sql = (f"SELECT trade_date, ts_code, primary_pattern FROM {LISTING_TABLE} "
               "WHERE trade_date>=? AND trade_date<=? AND strategy=?")
        args = (_d(start), _d(end), strategy)
    else:
        raise ValueError(
            f"跨日接力分只认 {HITS_TABLE!r} / {LISTING_TABLE!r} 两张表,收到 {source_table!r}")
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [RelayRecord(trade_date=r[0], ts_code=r[1], pattern=Pattern(r[2])) for r in rows]


__all__ = [
    "RUNS_TABLE", "HITS_TABLE", "LISTING_TABLE", "PARQUET_TABLE",
    "FINALIZED_BY_K9", "FINALIZED_BY_EXPLAIN",
    "disposition_path", "new_run_id",
    "save_run", "save_channel_hits", "save_listing", "save_disposition",
    "mark_listing_finalized_by",
    "load_disposition", "load_listing_codes", "load_listing",
    "load_listing_membership", "load_run", "load_upside_room_mech",
    "relaxed_streak_before", "load_relay_records",
]
