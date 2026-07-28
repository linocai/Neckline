"""同花顺概念板块三表的**日更 / 周更落盘**(plan §五 v1.4-①-C,§七 P0-3)。

**背景(P0-3)**:`ths_daily` / `ths_index` / `ths_member` 此前**只有一次性 backfill**
(`scripts/backfill_concept.py`,`END="20260722"` 硬编 + 「已存在则跳过」),16:05 的
`scripts/daily_update.py` 压根不碰它们 → `report/sectors.py::compute_sector_strength`
当日无行 → **返回空列表且不报错**(优雅降级)→ 「当日暴起板块」整路为空、`board_age`
拿不到,而且**因为降级得太安静,从报告上看不出坏了**。本模块补上日更管线。

────────────────────────────────────────────────────────────────────────────
⚠ **`write_table_day` 铁律的唯一登记例外**(§3.8;登记于此 + 项目 `CLAUDE.md`)
────────────────────────────────────────────────────────────────────────────
`ths_daily.parquet` **维持扁平单文件**,不转「一日一分区」。**三条理由**:
  ① 单文件**不存在跨分区 schema 冲突** —— `write_table_day` 铁律要防的失效模式
     (2026-07-27 `moneyflow_dc` 分区类型漂移毒化整表)在这里结构上不成立;
  ② 读侧全走扁平路径(`report/sectors.py::_ths_path` / `board_pool.py` /
     `intel_candidates.py`),转日分区是高回归风险的大改,收益不抵风险;
  ③ 「**按声明 dtype cast** + `.tmp` → `os.replace` 原子替换」等价覆盖了类型漂移与
     半写两个风险(前者由 `THS_DAILY_DTYPES` 兜,后者由原子替换兜)。
**dtype 声明住 `THS_DAILY_DTYPES` 一处命名常量**,照 `market_data.TABLE_FLOAT_COLS`
的精神——**永远向声明看齐,永不向「现有文件」看齐**(向现有文件看齐正是 v1.3.5 那场
事故的病根:基准本身可能是脏的)。守门单测见 `tests/test_concept_data.py`。

**周更(`ths_index` / `ths_member`)的两条硬规矩**:成分变动慢,每周重拉一次即可;
**重拉前保留上一版 `.bak`,拉取失败绝不覆盖旧快照** —— 半份成分比旧成分更糟(会让
「这只票属于哪些板块」凭空少掉一半,而报告不会喊)。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import polars as pl

logger = logging.getLogger(__name__)

THS_DAILY_FILE = "ths_daily.parquet"
THS_INDEX_FILE = "ths_index.parquet"
THS_MEMBER_FILE = "ths_member.parquet"
SNAPSHOT_META_FILE = "ths_snapshot_meta.json"

# —— `ths_daily` 扁平文件的 canonical dtype 声明(**单一事实源**)————————————————
# 来源 = TuShare `ths_daily` 实际返回列(2026-07-28 实测:ts_code/trade_date 为字符串,
# 其余十列 float64)+ backfill 落盘时把 `trade_date` 转成 `pl.Date`(读侧
# `compute_sector_strength` 按 `pl.col("trade_date") <= trade_date` 与 date 对象比较,
# 依赖的就是 Date 而非字符串)。
# ⚠ **追加时一律 cast 到本表声明**,不看现有文件是什么 —— 某日某列全空会让 pandas 落成
# object → polars String,与历史 Float64 冲突;向声明看齐才切得断这条链(守门单测:
# 造一列全空的当日增量,断言追加后该列 dtype 仍 = 声明值)。
THS_DAILY_DTYPES: Dict[str, pl.DataType] = {
    "ts_code": pl.String,
    "trade_date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "pre_close": pl.Float64,
    "avg_price": pl.Float64,
    "change": pl.Float64,
    "pct_change": pl.Float64,
    "vol": pl.Float64,
    "turnover_rate": pl.Float64,
}

# 日更每次重拉的**尾部窗口**(交易日)。为什么不是「只拉当天」:2026-07-28 16:19 实测
# `ths_daily` 当日数据**尚未发布**(`trade_date=20260728` 返 0 行,而 0723/0724/0727 各
# 有 1800~2500 行)—— 16:05 的日更几乎必然拿不到当天。尾窗重拉让**次日自动补上前一日**,
# 且对「当时只发布了一半」的日子有自愈能力(整段替换,不是 skip-if-exists 冻住半份)。
THS_DAILY_TRAILING_DAYS = 5


def ths_path(filename: str, parquet_dir: Optional[Path] = None) -> Path:
    from neckline.config import settings as _s

    return (parquet_dir or _s.parquet_dir) / filename


def align_ths_daily_schema(df: pl.DataFrame) -> pl.DataFrame:
    """把一份 `ths_daily` 数据对齐到 `THS_DAILY_DTYPES` **声明**。

    · 声明里有、df 里没有的列 → 补成该 dtype 的全 null 列(不静默丢列);
    · df 里有、声明里没有的列(TuShare 将来加列)→ **原样保留**,不擅自丢数据;
    · 列序按声明排,声明外的列排在后面(读侧不靠列序,这只是让 diff 好看)。
    `strict=False`:非法值 cast 成 null,不炸整批(同 `market_data._align_to_table_schema`)。
    """
    if df.is_empty() and not df.columns:
        return pl.DataFrame({k: pl.Series([], dtype=v) for k, v in THS_DAILY_DTYPES.items()})
    exprs: List[pl.Expr] = []
    for col, dtype in THS_DAILY_DTYPES.items():
        if col not in df.columns:
            exprs.append(pl.lit(None).cast(dtype).alias(col))
        elif col == "trade_date" and df.schema[col] == pl.String:
            # TuShare 原样返回 'YYYYMMDD' 字符串;直接 cast 到 Date 会失败,先 strptime。
            exprs.append(pl.col(col).str.strptime(pl.Date, "%Y%m%d", strict=False).alias(col))
        elif df.schema[col] != dtype:
            exprs.append(pl.col(col).cast(dtype, strict=False).alias(col))
        else:
            exprs.append(pl.col(col))
    extra = [c for c in df.columns if c not in THS_DAILY_DTYPES]
    return df.with_columns(exprs).select(list(THS_DAILY_DTYPES) + extra)


def _atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    """`.tmp` → `os.replace` 原子替换(同一文件系统内 rename 是原子的)。半写的文件
    永远不会以正式名出现 —— 这是扁平单文件替代日分区后必须自己扛的那一半保证。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)


def load_ths_daily(parquet_dir: Optional[Path] = None) -> pl.DataFrame:
    p = ths_path(THS_DAILY_FILE, parquet_dir)
    if not p.exists():
        return pl.DataFrame()
    return pl.read_parquet(p)


def max_ths_daily_date(parquet_dir: Optional[Path] = None) -> Optional[date]:
    """扁平文件里最大的 `trade_date`(= 板块数据最新到哪天)。空/缺文件 → None。
    「板块数据过期」告警的取数入口(单一源,不许各处自己 read_parquet 求 max)。"""
    p = ths_path(THS_DAILY_FILE, parquet_dir)
    if not p.exists():
        return None
    try:
        df = pl.scan_parquet(p).select(pl.col("trade_date").max()).collect()
    except Exception:  # noqa: BLE001  板块数据读不动不该掀翻报告
        logger.warning("读 %s 的最大 trade_date 失败", p, exc_info=True)
        return None
    if df.is_empty() or df[0, 0] is None:
        return None
    val = df[0, 0]
    return val if isinstance(val, date) else None


def upsert_ths_daily(new_rows: pl.DataFrame, parquet_dir: Optional[Path] = None) -> int:
    """把 `new_rows` **整段替换**进扁平文件(同 `trade_date` 的旧行先删后写),返回写入行数。

    幂等:同一批数据反复灌不产生重复行、结果逐行相同。**先删同日旧行再追加**(而不是
    「已存在就跳过」)是刻意的——当日板块数据可能分批发布,skip-if-exists 会把半份数据
    冻成永久事实,而没有任何东西会喊。空 `new_rows` → 不动文件,返回 0。
    """
    if new_rows is None or new_rows.is_empty():
        return 0
    aligned = align_ths_daily_schema(new_rows)
    dates = set(aligned["trade_date"].drop_nulls().to_list())
    existing = load_ths_daily(parquet_dir)
    if existing.is_empty():
        merged = aligned
    else:
        existing = align_ths_daily_schema(existing)
        kept = existing.filter(~pl.col("trade_date").is_in(list(dates)))
        merged = pl.concat([kept, aligned], how="diagonal_relaxed")
    merged = align_ths_daily_schema(merged).sort(["ts_code", "trade_date"])
    _atomic_write_parquet(merged, ths_path(THS_DAILY_FILE, parquet_dir))
    return aligned.height


# —— 周更:ths_index / ths_member 快照重拉 ————————————————————————————————

def _read_snapshot_meta(parquet_dir: Optional[Path] = None) -> Dict[str, str]:
    p = ths_path(SNAPSHOT_META_FILE, parquet_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_snapshot_meta(meta: Dict[str, str], parquet_dir: Optional[Path] = None) -> None:
    p = ths_path(SNAPSHOT_META_FILE, parquet_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot_due(kind: str, today: date, every_days: int = 7,
                 parquet_dir: Optional[Path] = None) -> bool:
    """某个快照(`ths_index` / `ths_member`)是否该重拉了(默认每 7 天)。无记录 → True。"""
    raw = _read_snapshot_meta(parquet_dir).get(kind)
    if not raw:
        return True
    try:
        last = datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return True
    return (today - last).days >= every_days


def replace_snapshot(
    kind: str,
    filename: str,
    df: Optional[pl.DataFrame],
    today: date,
    parquet_dir: Optional[Path] = None,
) -> bool:
    """用新快照替换旧快照,**旧版先留 `.bak`**;`df` 为空/None → **绝不覆盖**,返 False。

    「半份成分比旧成分更糟」:拉取失败或只拉回一部分时,保留旧快照是唯一安全选项——
    旧成分至少是一致的,半份成分会让「这只票属于哪些板块」凭空少掉一半而无人喊。
    """
    if df is None or df.is_empty():
        logger.warning("%s 快照拉取为空,**保留旧快照不覆盖**(半份成分比旧成分更糟)", kind)
        return False
    path = ths_path(filename, parquet_dir)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    _atomic_write_parquet(df, path)
    meta = _read_snapshot_meta(parquet_dir)
    meta[kind] = today.strftime("%Y%m%d")
    _write_snapshot_meta(meta, parquet_dir)
    logger.info("%s 快照已更新(%d 行,旧版留 .bak)", kind, df.height)
    return True


# —— 编排(供 scripts/daily_update.py 调用;fetcher 可注入,单测免联网)————————————

DailyFetcher = Callable[[str], "object"]          # (trade_date) -> TushareResult
PlainFetcher = Callable[[], "object"]             # () -> TushareResult


def _to_pl(res) -> pl.DataFrame:
    data = getattr(res, "data", None)
    if not getattr(res, "ok", False) or data is None or not len(data):
        return pl.DataFrame()
    return pl.from_pandas(data)


def concept_index_codes(parquet_dir: Optional[Path] = None) -> Optional[set]:
    """`ths_index.parquet` 里的板块代码集合(= **概念指数** type='N');缺文件 → None。"""
    p = ths_path(THS_INDEX_FILE, parquet_dir)
    if not p.exists():
        return None
    try:
        return set(pl.read_parquet(p, columns=["ts_code"])["ts_code"].to_list())
    except Exception:  # noqa: BLE001
        logger.warning("读 %s 失败,本次不做概念板块过滤", p, exc_info=True)
        return None


def update_ths_daily(
    trading_days: Sequence[date],
    *,
    fetch: Optional[DailyFetcher] = None,
    parquet_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """按日拉 `ths_daily` 尾部窗口并 upsert。返回 `{'days':n,'rows':m,'empty':k,'failed':f}`。

    **一次调用取全板块**(实测 `trade_date=20260722` 一次返 2499 行)——不必按 `ts_code`
    分批 400 次,配额消耗 = 窗口天数(默认 5 次/日),与 §七 P4-20 的配额账一起算。
    某日返 0 行不是失败(当天数据尚未发布很常见,见 `THS_DAILY_TRAILING_DAYS` 注释),
    但要如实计数,别把「没发布」和「拉失败」混成一个数。

    ⚠ **必须按 `ths_index` 过滤成概念板块**(2026-07-28 实测踩到):`ths_daily` 不带
    `ts_code` 时返回的是**全部同花顺板块指数**(概念 N + 行业 I + 地域 R + 风格…,2499
    行/日),而 backfill 建的这个文件一直是**只含概念指数**(逐个 `ts_code` 拉 type='N',
    ~394 行/日)。不过滤就会把三倍的非概念指数灌进来:① `compute_sector_strength` 按
    20 日动量取 top10,榜单会被行业/地域指数挤占,**语义从「强势概念板块」悄悄变成
    「强势任意板块」**;② 这些代码不在 `ths_index` 里,`load_index_names` 查不到名字 →
    报告上显示成裸代码 `700005.TI`。故此处按当前 `ths_index` 快照过滤,保持读侧语义
    **逐位不变**。`ths_index` 缺失(全新环境)→ 不过滤 + WARNING(有数据好过没数据,
    但要喊一声)。
    """
    if fetch is None:
        from neckline.data.tushare_client import _call

        def fetch(td: str):  # type: ignore[misc]
            return _call("ths_daily", trade_date=td)

    keep = concept_index_codes(parquet_dir)
    if keep is None:
        logger.warning("ths_index 快照缺失,本次 ths_daily 不做概念板块过滤"
                       "(可能混入行业/地域指数,榜单语义会变宽)")

    stats = {"days": 0, "rows": 0, "empty": 0, "failed": 0}
    for d in trading_days:
        stats["days"] += 1
        res = fetch(d.strftime("%Y%m%d"))
        if not getattr(res, "ok", False):
            stats["failed"] += 1
            logger.warning("ths_daily %s 拉取失败:%s(留待次日尾窗重试)",
                           d, getattr(res, "reason", "?"))
            continue
        df = _to_pl(res)
        if keep is not None and not df.is_empty() and "ts_code" in df.columns:
            df = df.filter(pl.col("ts_code").is_in(list(keep)))
        if df.is_empty():
            stats["empty"] += 1
            continue
        stats["rows"] += upsert_ths_daily(df, parquet_dir)
    return stats


def update_ths_snapshots(
    today: date,
    *,
    fetch_index: Optional[PlainFetcher] = None,
    fetch_member: Optional[Callable[[str], "object"]] = None,
    every_days: int = 7,
    force: bool = False,
    parquet_dir: Optional[Path] = None,
) -> Dict[str, bool]:
    """周更 `ths_index` / `ths_member`(未到期 → 直接跳过,不耗配额)。

    `ths_member` 需按板块逐个拉(~400 次),故与 `ths_index` 同频、每周一次。**任一环节
    没拿全就不覆盖**:index 拉空 → 两者都不动(没有板块列表就谈不上成分);member 只拿回
    一部分 → 由 `replace_snapshot` 的「空则不覆盖」+ 本函数的「失败率过半则不覆盖」两道
    闸拦下(半份成分比旧成分更糟)。
    """
    out = {"ths_index": False, "ths_member": False}
    if not force and not snapshot_due("ths_index", today, every_days, parquet_dir):
        logger.info("ths_index/ths_member 快照未到重拉周期(每 %d 天),跳过", every_days)
        return out
    if fetch_index is None:
        from neckline.data.tushare_client import ts_ths_index

        fetch_index = lambda: ts_ths_index(exchange="A", type_="N")  # noqa: E731
    if fetch_member is None:
        from neckline.data.tushare_client import ts_ths_member

        fetch_member = ts_ths_member

    idx = _to_pl(fetch_index())
    if idx.is_empty() or "ts_code" not in idx.columns:
        logger.warning("ths_index 拉取为空,本周快照重拉整体放弃(旧快照原样保留)")
        return out
    out["ths_index"] = replace_snapshot("ths_index", THS_INDEX_FILE, idx, today, parquet_dir)

    codes = idx["ts_code"].to_list()
    frames, failed = [], 0
    for code in codes:
        df = _to_pl(fetch_member(code))
        if df.is_empty():
            failed += 1
            continue
        if "ts_code" in df.columns:
            df = df.rename({"ts_code": "index_code"})
        elif "index_code" not in df.columns:
            df = df.with_columns(pl.lit(code).alias("index_code"))
        frames.append(df)
    if not frames or failed > len(codes) / 2:
        logger.warning(
            "ths_member 只拉回 %d/%d 个板块的成分,**保留旧快照不覆盖**(半份成分比旧成分更糟)",
            len(frames), len(codes),
        )
        return out
    member = pl.concat(frames, how="diagonal_relaxed")
    out["ths_member"] = replace_snapshot("ths_member", THS_MEMBER_FILE, member, today, parquet_dir)
    return out


__all__ = [
    "THS_DAILY_DTYPES",
    "THS_DAILY_TRAILING_DAYS",
    "align_ths_daily_schema",
    "load_ths_daily",
    "max_ths_daily_date",
    "upsert_ths_daily",
    "snapshot_due",
    "replace_snapshot",
    "update_ths_daily",
    "update_ths_snapshots",
]
