"""事实包的**唯一写入口**与只读读取(PROJECT_PLAN §5.3.2)。

🔴 **唯一写入口**:全仓只有本文件调 `write_table_day("fact_pack", ...)`、
只有本文件出现 `INSERT INTO fact_packs`(AST 守门单测 G16 逐文件扫描)。

**四条落地纪律,逐条对应 §5.3.2**:

1. **类型级保证** —— `freeze_pack()` 的签名只接受 `CompletePack`。`IncompletePack`
   没有 rows、没有 freeze,于是「数据未到齐 → 不冻结」是**类型错误**,不是某个人
   记得检查的布尔标志。运行期再补一道 `isinstance` 断言(本仓无 mypy,见 §14 登记)。
2. **写序** —— 先写 parquet 到**同一文件系统内**的临时路径 → `fsync` → 算 sha256 →
   `os.replace` 原子就位 → **再**一个短事务插 `fact_packs` 行。
   进程死在中间只会留一个没有清单的孤儿 parquet(不可见,下次覆盖);
   反序则会留一个**指向空气的清单**——那是审计物,不能允许它说谎。
3. **不许覆盖** —— 用 `INSERT`(⛔ 不是 `INSERT OR REPLACE`)。同一
   `(trade_date, pack_version)` 二次冻结直接抛 `PackAlreadyFrozen`。
   口径变了就发新 `pack_version`,⛔ 没有静默重写这条路。
   🔴 **版本进路径**(`fact_pack/version=<v>/year=YYYY/YYYYMMDD.parquet`,
   2026-08-21 复审 R1-B1 修复):清单有 `UNIQUE(trade_date, pack_version)`,
   同一天允许两版行;路径里**没有**版本时两版共用一个坑位,于是「发新版本」这条
   被指定为正路的路径,恰恰是唯一能把旧版本数据抹掉的那条 —— 旧清单行连同它的
   `content_fingerprint` 原样留着,指向的却已经是新版本的字节。
4. **只读** —— `load_pack()` 返回 `@dataclass(frozen=True)` 的 `FactPack`;
   `rows` 是**每次调用现读 parquet** 的属性,调用方拿到的永远是自己的副本,改不脏别人。

**保留策略**(§3.2):生产滚动保留 `RETENTION_PACKS`(250)个交易日的 parquet;更早的
裁剪,**清单行永远保留**——「那次跑用的哪版包」必须活得比数据久。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import polars as pl

from neckline.calendar import trading_days_between
from neckline.config import settings
from neckline.data.market_data import day_file_path, table_dir, write_table_day
from neckline.db import connection, init_schema, readonly_tables
from neckline.facts import industry as industry_mod
from neckline.facts.pack import (
    MAX_LOOKBACK_PACKS,
    PACK_COLUMNS,
    PACK_VERSION,
    CompletePack,
)

logger = logging.getLogger(__name__)


def columns_for_pack_version(pack_version: str) -> Tuple[str, ...]:
    """返回已发布事实合同的列，不能拿 fp-3 清单读取 fp-4。"""
    if pack_version == "fp-4":
        from neckline.facts.v4 import PACK_COLUMNS as v4_columns
        return v4_columns
    return PACK_COLUMNS

#: parquet 表名(`data/parquet/fact_pack/year=YYYY/YYYYMMDD.parquet`)。
PARQUET_TABLE = "fact_pack"

#: 生产滚动保留的交易日数(§3.2:250 > `MAX_LOOKBACK_PACKS`(120) 有充足余量)。
#: ⚠ 这是**工程容量策略**,不是待标定参数 —— Plan §3.2 已给定值并给了理由。
RETENTION_PACKS = 250

ORIGIN_LIVE = "live"
ORIGIN_BACKFILL = "backfill"
STATE_FROZEN = "frozen"

_INSERT_COLUMNS = (
    "pack_id, trade_date, pack_version, origin, state, content_fingerprint, row_count, "
    "sources_json, market_json, suspend_anomaly_count, frozen_at"
)


class PackAlreadyFrozen(RuntimeError):
    """同一 `(trade_date, pack_version)` 已冻结过。⛔ 不静默覆盖(纪律 3)。"""


class PackNotFrozen(LookupError):
    """该交易日没有冻结过的事实包 —— 「今天没跑成」,⛔ 不是「今天没有」。"""


class FactPackIntegrityError(RuntimeError):
    """冻结清单与其 parquet 载荷不一致，任何消费方都必须 fail-closed。"""


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_verified_rows(path: Path, *, trade_date: date, pack_version: str,
                        fingerprint: str, row_count: int) -> pl.DataFrame:
    """Read only the exact bytes pinned by a frozen ``fact_packs`` ledger row."""
    if not path.exists():
        raise FileNotFoundError(
            f"{trade_date} 的 {pack_version} parquet 缺失({path})，冻结清单不可消费")
    # Read once: hashing a path and reopening it for parquet parsing has a
    # replace-between-operations race.  The verified digest and decoded frame
    # must originate from exactly the same immutable byte buffer.
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise FileNotFoundError(
            f"{trade_date} 的 {pack_version} parquet 无法读取({path}): {exc}") from exc
    actual_fingerprint = hashlib.sha256(payload).hexdigest()
    if actual_fingerprint != fingerprint:
        raise FactPackIntegrityError(
            f"{trade_date} 的 {pack_version} parquet SHA-256 与冻结清单不一致"
            f"(expected={fingerprint}, actual={actual_fingerprint})")
    try:
        rows = pl.read_parquet(io.BytesIO(payload))
    except Exception as exc:  # noqa: BLE001
        raise FactPackIntegrityError(f"{trade_date} 的 {pack_version} parquet 不可读取:{exc}") from exc
    if rows.height != row_count:
        raise FactPackIntegrityError(
            f"{trade_date} 的 {pack_version} parquet 行数与冻结清单不一致"
            f"(expected={row_count}, actual={rows.height})")
    return rows


# ══════════════════════════════════════════════════════════════════════════
# 路径:**一版一个坑位**(纪律 3 的落地,2026-08-21 复审 R1-B1)
# ══════════════════════════════════════════════════════════════════════════

def pack_file_path(
    trade_date: date, pack_version: str, parquet_dir: Optional[Path] = None,
    *, revision: int = 1,
) -> Path:
    """当前布局:`fact_pack/version=<v>/year=YYYY/YYYYMMDD.parquet`。

    🔴 版本进路径是「口径变了就发新 `pack_version`」这条纪律**唯一**能成立的前提。
    路径里没有版本时,同一天的第二版会把第一版的数据原地抹掉,而第一版的清单行
    (连同它的 `content_fingerprint`)还在 —— 那就是一条会说谎的审计记录。
    """
    if revision < 1:
        raise ValueError("revision 必须 >= 1")
    path = day_file_path(PARQUET_TABLE, trade_date, parquet_dir, version=pack_version)
    if revision == 1:
        return path
    return path.parent.parent / f"revision={revision}" / path.parent.name / path.name


@dataclass(frozen=True)
class FactPack:
    """一份**已冻结**的事实包(只读)。

    `rows` 刻意是**属性**而不是字段:每次访问现读 parquet,调用方拿到的永远是自己的
    副本,改不脏别人也改不脏磁盘(纪律 4)。"""

    pack_id: str
    trade_date: date
    pack_version: str
    origin: str
    content_fingerprint: str
    row_count: int
    sources: Tuple[dict, ...]
    market: Dict[str, object]
    suspend_anomaly_count: int
    frozen_at: str
    revision: int = 1
    supersedes_pack_id: Optional[str] = None
    correction_reason: Optional[str] = None
    _parquet_dir: Optional[Path] = field(default=None, repr=False, compare=False)

    @property
    def path(self) -> Path:
        """本包的 parquet 只允许落在版本化路径。"""
        return pack_file_path(
            self.trade_date, self.pack_version, self._parquet_dir, revision=self.revision)

    @property
    def rows(self) -> pl.DataFrame:
        """现读 parquet。文件已被保留策略裁剪 → `FileNotFoundError`(清单行仍在,
        ⛔ 不返回空表冒充「那天没数据」)。"""
        return _read_verified_rows(
            self.path, trade_date=self.trade_date, pack_version=self.pack_version,
            fingerprint=self.content_fingerprint, row_count=self.row_count,
        )

    def field(self, name: str) -> pl.Series:
        """按列取数。列名不在 `PACK_COLUMNS` 里直接抛(⛔ 不静默返回空列)。"""
        if name not in columns_for_pack_version(self.pack_version):
            raise KeyError(f"{name!r} 不是事实包的列;可用列见 facts.pack.PACK_COLUMNS")
        return self.rows[name]


# ══════════════════════════════════════════════════════════════════════════
# 写(唯一入口)
# ══════════════════════════════════════════════════════════════════════════

def _put_in_place(
    tmp_path: Path, final_path: Path, trade_date: date, version: str, *, orphan_there: bool
) -> None:
    """把暂存文件原子就位。**⛔ 不覆盖开工之后才冒出来的文件**(纪律 3)。

    `orphan_there` = 开工前(查完清单那一刻)这个坑位上就已经躺着一个文件。清单里
    这一版没有行,故它只能是上次进程死在中间留下的**孤儿** —— 纪律 2 承诺过
    「下次覆盖」,所以覆盖它。

    ⚠ 反过来,开工时是空的、落地时却已经有人 —— 那是**另一个进程正在冻同一
    `(date, version)`**。过去这里一律 `os.replace`,于是后到者先把先到者的文件盖掉,
    再在 INSERT 处吃 `IntegrityError` 抛 `PackAlreadyFrozen`;先到者的清单行从此
    与磁盘内容对不上(R1-B1「顺带」那一条)。现在后到者在这里就停手,磁盘不动。
    """
    if orphan_there:
        logger.warning(
            "[fact_pack] %s 的 %s 坑位上有一个**没有清单行**的孤儿文件,覆盖它(纪律 2)",
            trade_date, version)
        os.replace(tmp_path, final_path)
        return
    try:
        os.link(tmp_path, final_path)
    except FileExistsError as e:
        raise PackAlreadyFrozen(
            f"{trade_date} 的 {version} parquet 在本次冻结开工之后被别人写上了 "
            f"—— 另一个进程正在冻同一 (trade_date, pack_version)。"
            f"⛔ 已停手,磁盘上那一份原样保留:{final_path}") from e
    except OSError:                       # 文件系统不支持硬链接:退回原子替换
        logger.warning("[fact_pack] %s 不支持 os.link,退回 os.replace", final_path.parent)
        os.replace(tmp_path, final_path)


def freeze_pack(
    pack: CompletePack,
    *,
    origin: str = ORIGIN_LIVE,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> FactPack:
    """冻结一份事实包。**签名只接受 `CompletePack`**(纪律 1)。

    ⛔ 传 `IncompletePack` 是**类型错误**;运行期这里也会当场抛 `TypeError`,
    ⛔ 绝不「尽力而为地冻一半」。
    """
    if not isinstance(pack, CompletePack):
        raise TypeError(
            f"freeze_pack() 只接受 CompletePack,收到 {type(pack).__name__} —— "
            f"数据未到齐时的正确动作是报告「今天没跑成」并逐条列出缺口,⛔ 不是冻一份残包")
    if origin not in (ORIGIN_LIVE, ORIGIN_BACKFILL):
        raise ValueError(f"origin 只能是 {ORIGIN_LIVE!r} / {ORIGIN_BACKFILL!r},收到 {origin!r}")
    expected_columns = columns_for_pack_version(pack.pack_version)
    absent = [c for c in expected_columns if c not in pack.rows.columns]
    if absent:
        raise ValueError(
            f"{pack.pack_version} 不能用旧事实行伪造，缺少字段:{','.join(absent)}")

    init_schema(db_path)
    day_s = _d(pack.trade_date)

    # —— 纪律 3:先看清单。已冻结过就当场停手,⛔ 不碰 parquet ——————————————
    with connection(db_path) as conn:
        dup = conn.execute(
            "SELECT pack_id FROM fact_packs WHERE trade_date=? AND pack_version=?",
            (day_s, pack.pack_version),
        ).fetchone()
    if dup is not None:
        raise PackAlreadyFrozen(
            f"{pack.trade_date} 的 {pack.pack_version} 事实包已冻结(pack_id={dup[0]}) —— "
            f"⛔ 冻结不可覆盖。口径变了请发新 pack_version")

    root = parquet_dir or settings.parquet_dir
    final_path = pack_file_path(pack.trade_date, pack.pack_version, root)
    # 查完清单那一刻坑位上就有东西 = 孤儿(清单里这一版没有行)。⚠ 必须在写暂存
    # **之前**取这个读数:落地时才看,就分不清「孤儿」和「另一个进程刚写的」。
    orphan_there = final_path.exists()

    # —— 纪律 2:临时路径 → fsync → sha256 → 原子就位 ————————————————————————
    # ⚠ 临时目录必须在**同一文件系统内**(`os.replace` / `os.link` 跨设备会抛),故落在
    # `<parquet_dir>/fact_pack/.staging/<uuid>/` 而不是系统 /tmp。
    # `.staging` 不匹配读侧的 `year=*` glob,对任何读路径不可见。
    staging_root = table_dir(PARQUET_TABLE, root) / ".staging"
    staging = staging_root / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    try:
        tmp_path = write_table_day(
            PARQUET_TABLE, pack.trade_date,
            pack.rows.select(list(columns_for_pack_version(pack.pack_version))),
            parquet_dir=staging,
        )
        with tmp_path.open("rb") as fh:
            os.fsync(fh.fileno())
        fingerprint = _sha256(tmp_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        _put_in_place(tmp_path, final_path, pack.trade_date, pack.pack_version,
                      orphan_there=orphan_there)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        # 顺手收掉空壳。⚠ 用 `rmdir` 而不是 `rmtree`:进程被 kill 在中间时
        # `.staging` 里可能躺着**别的进程正在写**的那一份,⛔ 不许连它一起删。
        try:
            staging_root.rmdir()
        except OSError:
            pass

    # 行业事实与大表同一份装配结果,一并落表(重算幂等,见 industry.save_day)。
    industry_mod.save_day(pack.trade_date, list(pack.industry_rows), db_path=db_path)

    pack_id = uuid.uuid4().hex
    payload = (
        pack_id, day_s, pack.pack_version, origin, STATE_FROZEN, fingerprint,
        pack.row_count,
        json.dumps([s.to_dict() for s in pack.sources], ensure_ascii=False, sort_keys=True),
        json.dumps(pack.market, ensure_ascii=False, sort_keys=True),
        pack.suspend_anomaly_count, _now(),
    )
    try:
        with connection(db_path) as conn:
            conn.execute(
                f"INSERT INTO fact_packs ({_INSERT_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                payload,
            )
    except sqlite3.IntegrityError as e:      # 并发两个进程同时冻同一天
        raise PackAlreadyFrozen(
            f"{pack.trade_date} 的 {pack.pack_version} 事实包已冻结(唯一索引拦下):{e}") from e

    logger.info(
        "[fact_pack] 冻结 %s(%s / %s):%d 行,sha256=%s…,停牌异常 %d",
        pack.trade_date, pack.pack_version, origin, pack.row_count,
        fingerprint[:12], pack.suspend_anomaly_count,
    )
    return _row_to_pack(
        (pack_id, day_s, pack.pack_version, origin, fingerprint, pack.row_count,
         payload[7], payload[8], pack.suspend_anomaly_count, payload[10]),
        root if parquet_dir is not None else None,
    )


def freeze_correction(
    pack: CompletePack,
    *,
    expected_superseded_pack_id: str,
    correction_reason: str,
    origin: str = ORIGIN_LIVE,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> FactPack:
    """Append an explicitly authorized correction without rewriting revision 1.

    This is deliberately separate from :func:`freeze_pack`: scheduled jobs
    cannot discover or invoke a correction implicitly.  The caller must pin
    the exact pack being superseded and provide an audit reason.
    """
    if not isinstance(pack, CompletePack):
        raise TypeError("freeze_correction() 只接受 CompletePack")
    reason = correction_reason.strip()
    if not reason:
        raise ValueError("correction_reason 不能为空")
    if origin not in (ORIGIN_LIVE, ORIGIN_BACKFILL):
        raise ValueError(f"未知 origin:{origin}")
    absent = [c for c in columns_for_pack_version(pack.pack_version) if c not in pack.rows.columns]
    if absent:
        raise ValueError(f"{pack.pack_version} 修订缺少字段:{','.join(absent)}")

    init_schema(db_path)
    current = load_pack(
        pack.trade_date, pack_version=pack.pack_version,
        parquet_dir=parquet_dir, db_path=db_path,
    )
    if current.pack_id != expected_superseded_pack_id:
        raise PackAlreadyFrozen(
            f"修订基线已变化(expected={expected_superseded_pack_id}, actual={current.pack_id})")
    revision = current.revision + 1
    root = parquet_dir or settings.parquet_dir
    final_path = pack_file_path(
        pack.trade_date, pack.pack_version, root, revision=revision)
    orphan_there = final_path.exists()
    staging_root = table_dir(PARQUET_TABLE, root) / ".staging"
    staging = staging_root / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    try:
        tmp_path = write_table_day(
            PARQUET_TABLE, pack.trade_date,
            pack.rows.select(list(columns_for_pack_version(pack.pack_version))),
            parquet_dir=staging,
        )
        with tmp_path.open("rb") as fh:
            os.fsync(fh.fileno())
        fingerprint = _sha256(tmp_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        _put_in_place(
            tmp_path, final_path, pack.trade_date,
            f"{pack.pack_version}/revision={revision}", orphan_there=orphan_there)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            staging_root.rmdir()
        except OSError:
            pass

    pack_id = uuid.uuid4().hex
    day_s = _d(pack.trade_date)
    sources_json = json.dumps(
        [s.to_dict() for s in pack.sources], ensure_ascii=False, sort_keys=True)
    market_json = json.dumps(pack.market, ensure_ascii=False, sort_keys=True)
    frozen_at = _now()
    try:
        with connection(db_path) as conn:
            latest = _latest_by_day(_manifest_rows(
                conn, pack_version=pack.pack_version,
                start_day=day_s, end_day=day_s,
            ))
            if not latest or latest[0][0] != expected_superseded_pack_id:
                raise PackAlreadyFrozen("写入修订前事实包基线已被其他进程推进")
            conn.execute(
                "INSERT INTO fact_pack_revisions ("
                "pack_id,trade_date,pack_version,revision,supersedes_pack_id,correction_reason,"
                "origin,state,content_fingerprint,row_count,sources_json,market_json,"
                "suspend_anomaly_count,frozen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pack_id, day_s, pack.pack_version, revision, current.pack_id, reason,
                 origin, STATE_FROZEN, fingerprint, pack.row_count, sources_json,
                 market_json, pack.suspend_anomaly_count, frozen_at),
            )
    except sqlite3.IntegrityError as exc:
        raise PackAlreadyFrozen(
            f"{pack.trade_date} 的 {pack.pack_version} revision={revision} 已存在:{exc}") from exc

    logger.warning(
        "[fact_pack] 追加修订 %s(%s revision=%d):supersedes=%s,reason=%s",
        pack.trade_date, pack.pack_version, revision, current.pack_id, reason,
    )
    return _row_to_pack(
        (pack_id, day_s, pack.pack_version, origin, fingerprint, pack.row_count,
         sources_json, market_json, pack.suspend_anomaly_count, frozen_at,
         revision, current.pack_id, reason),
        root if parquet_dir is not None else None,
    )


# ══════════════════════════════════════════════════════════════════════════
# 读(只读)
# ══════════════════════════════════════════════════════════════════════════

_SELECT_COLUMNS = (
    "pack_id, trade_date, pack_version, origin, content_fingerprint, row_count, "
    "sources_json, market_json, suspend_anomaly_count, frozen_at"
)
_BASE_SELECT_COLUMNS = _SELECT_COLUMNS + ", 1 AS revision, NULL AS supersedes_pack_id, NULL AS correction_reason"
_REVISION_SELECT_COLUMNS = _SELECT_COLUMNS + ", revision, supersedes_pack_id, correction_reason"


def _row_to_pack(row: Sequence, parquet_dir: Optional[Path]) -> FactPack:
    return FactPack(
        pack_id=row[0],
        trade_date=datetime.strptime(row[1], "%Y%m%d").date(),
        pack_version=row[2],
        origin=row[3],
        content_fingerprint=row[4],
        row_count=int(row[5]),
        sources=tuple(json.loads(row[6])),
        market=json.loads(row[7]),
        suspend_anomaly_count=int(row[8]),
        frozen_at=row[9],
        revision=int(row[10]) if len(row) > 10 else 1,
        supersedes_pack_id=row[11] if len(row) > 11 else None,
        correction_reason=row[12] if len(row) > 12 else None,
        _parquet_dir=parquet_dir,
    )


def _has_revision_table(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact_pack_revisions'"
    ).fetchone() is not None


def _manifest_rows(
    conn: sqlite3.Connection,
    *,
    pack_version: str,
    start_day: Optional[str] = None,
    end_day: Optional[str] = None,
) -> list[Sequence]:
    clauses = ["pack_version=?"]
    args: list[object] = [pack_version]
    if start_day is not None:
        clauses.append("trade_date>=?")
        args.append(start_day)
    if end_day is not None:
        clauses.append("trade_date<=?")
        args.append(end_day)
    where = " AND ".join(clauses)
    rows = list(conn.execute(
        f"SELECT {_BASE_SELECT_COLUMNS} FROM fact_packs WHERE {where}", tuple(args)
    ).fetchall())
    if _has_revision_table(conn):
        rows.extend(conn.execute(
            f"SELECT {_REVISION_SELECT_COLUMNS} FROM fact_pack_revisions WHERE {where}",
            tuple(args),
        ).fetchall())
    return rows


def _latest_by_day(rows: Sequence[Sequence]) -> list[Sequence]:
    latest: dict[str, Sequence] = {}
    for row in rows:
        day = str(row[1])
        if day not in latest or int(row[10]) > int(latest[day][10]):
            latest[day] = row
    return [latest[day] for day in sorted(latest)]


def load_pack(
    trade_date: date,
    *,
    pack_version: str = PACK_VERSION,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> FactPack:
    """读一份已冻结的事实包。没有 → `PackNotFrozen`(⛔ 不返回一个空包冒充成功)。

    ⛔ **读函数不执行 DDL**(§7.1 政策 / R3-🔴-2):库还没迁移过(`fact_packs` 不存在)
    与「那天没冻结」在这里是同一个结果 —— `PackNotFrozen`。⛔ 不许顺手 `init_schema`
    迁移只属于启动、显式写命令或确认的发布流程。
    """
    with readonly_tables("fact_packs", db_path=db_path) as conn:
        rows = [] if conn is None else _manifest_rows(
            conn, pack_version=pack_version,
            start_day=_d(trade_date), end_day=_d(trade_date),
        )
        latest = _latest_by_day(rows)
        row = latest[0] if latest else None
    if row is None:
        raise PackNotFrozen(f"{trade_date} 没有 {pack_version} 的冻结事实包")
    return _row_to_pack(row, parquet_dir)


def load_pack_by_id(
    pack_id: str,
    *,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> FactPack:
    """Read one exact immutable revision by id; never substitute the latest."""
    with readonly_tables("fact_packs", db_path=db_path) as conn:
        row = None if conn is None else conn.execute(
            f"SELECT {_BASE_SELECT_COLUMNS} FROM fact_packs WHERE pack_id=?", (pack_id,)
        ).fetchone()
        if row is None and conn is not None and _has_revision_table(conn):
            row = conn.execute(
                f"SELECT {_REVISION_SELECT_COLUMNS} FROM fact_pack_revisions WHERE pack_id=?",
                (pack_id,),
            ).fetchone()
    if row is None:
        raise PackNotFrozen(f"找不到事实包 {pack_id}")
    return _row_to_pack(row, parquet_dir)


def latest_pack(
    *,
    on_or_before: Optional[date] = None,
    pack_version: str = PACK_VERSION,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Optional[FactPack]:
    """最近一份已冻结的事实包(可给上限日)。无 → `None`。

    这是「参数未配置 / 数据未到齐时**保留上一份冻结结果**」的读取入口(裁定 5):
    今天没跑成不会动昨天那份 —— `fact_packs` 是 `INSERT` only,昨天那行谁都改不了。

    ⛔ 读函数不执行 DDL(§7.1):库未迁移 → `None`,与「一份都没冻过」同一个结果。"""
    with readonly_tables("fact_packs", db_path=db_path) as conn:
        rows = [] if conn is None else _manifest_rows(
            conn, pack_version=pack_version,
            end_day=_d(on_or_before) if on_or_before is not None else None,
        )
        latest = _latest_by_day(rows)
        row = latest[-1] if latest else None
    return None if row is None else _row_to_pack(row, parquet_dir)


def load_pack_range(
    start: date,
    end: date,
    *,
    as_of: date,
    columns: Sequence[str],
    pack_version: str = PACK_VERSION,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> pl.DataFrame:
    """读 `[start, end]` 区间的事实包大表(策略层的历史读取入口,§5.4.2)。

    **三个必填关键字,每个都是一道闸**(⛔ 都不给默认值,⛔ 都是抛不是警告):

    ① `as_of` —— 策略契约第三条「读取范围截止到当日」,硬断言 `end <= as_of`。
       调用方必须显式说出「我现在站在哪一天」,⛔ 不给它默认值去猜
       (⚠ Plan §5.4.2 写的是 `load_pack_range(start, end)` 硬断言 `end <= trade_date`,
       但没说 `trade_date` 从哪来 —— 回测里它不是墙钟。已登记进 §14)。

    ② 区间交易日数 `<= MAX_LOOKBACK_PACKS`(120)——**工程容量上限,不是策略参数**。

    ③ 🛑 `columns` —— **列投影是必填的,不是可选优化**(§12 坑 1)。
       2026-08-20 本片实测(120 个交易日 / 659,239 行,开发机):
           · 10 列投影 → frame **53.6 MB**、RSS 峰值 **270 MB**(§3.2 估的 53 MB 对上了)
           · 全 41 列   → frame **185.3 MB**、RSS 峰值 **865 MB**
       生产是 2 vCPU / 1.6 G,历史上 700M cap 就被 OOM-kill 过。把「读全部列」做成
       缺省行为,等于把那条红线设成默认路径。要全部列就**显式**传
       `columns=facts.pack.PACK_COLUMNS` —— 让它是一次自觉行为。

    每个清单内分区都在拼接前复核 SHA-256 与行数；缺文件、篡改、截断都直接抛，
    ⛔ 不许悄悄跳过后返回残缺历史窗口。
    """
    if end > as_of:
        raise ValueError(
            f"读取范围截止到当日(策略契约第三条):end={end} > as_of={as_of}")
    if start > end:
        raise ValueError(f"start({start}) > end({end})")
    calendar_db = db_path if db_path is not None else settings.db_path
    span = len(trading_days_between(start, end, db_path=calendar_db))
    if span > MAX_LOOKBACK_PACKS:
        raise ValueError(
            f"区间 {start}~{end} 含 {span} 个交易日,超过 MAX_LOOKBACK_PACKS={MAX_LOOKBACK_PACKS}"
            f"(工程容量上限,§3.2)")
    expected_days = [_d(day) for day in trading_days_between(start, end, db_path=calendar_db)]

    with readonly_tables("fact_packs", db_path=db_path) as conn:
        rows = [] if conn is None else _manifest_rows(
            conn, pack_version=pack_version, start_day=_d(start), end_day=_d(end))
        manifests = _latest_by_day(rows)
    actual_days = [str(row[1]) for row in manifests]
    if actual_days != expected_days:
        missing = sorted(set(expected_days) - set(actual_days))
        extra = sorted(set(actual_days) - set(expected_days))
        raise FactPackIntegrityError(
            f"{pack_version} 冻结事实窗口日期不完整或含闭市日"
            f"(missing={missing or '-'}, extra={extra or '-'})")
    if not manifests:
        return pl.DataFrame()

    picked = list(columns)
    if not picked:
        raise ValueError("columns 不能为空 —— 列投影是必填的(见 docstring 的实测内存账)")
    contract_columns = columns_for_pack_version(pack_version)
    unknown = [c for c in picked if c not in contract_columns]
    if unknown:
        raise KeyError(f"{unknown} 不是事实包的列;可用列见 facts.pack.PACK_COLUMNS")
    if "trade_date" not in picked:
        picked = ["trade_date", *picked]

    frames: List[pl.DataFrame] = []
    for manifest in manifests:
        day_s, fingerprint, expected_count = manifest[1], manifest[4], manifest[5]
        revision = int(manifest[10])
        d = datetime.strptime(day_s, "%Y%m%d").date()
        p = pack_file_path(d, pack_version, parquet_dir, revision=revision)
        rows = _read_verified_rows(p, trade_date=d, pack_version=pack_version,
                                   fingerprint=str(fingerprint), row_count=int(expected_count))
        absent = [column for column in picked if column not in rows.columns]
        if absent:
            raise FactPackIntegrityError(
                f"{d} 的 {pack_version} parquet 缺少请求列:{','.join(absent)}")
        frames.append(rows.select(picked))
    return pl.concat(frames, how="vertical_relaxed").sort(["trade_date", "ts_code"])


def list_packs(
    *, pack_version: Optional[str] = None, db_path: Optional[Path] = None
) -> List[Tuple[str, str, str, int]]:
    """清单速览 `[(trade_date, pack_version, origin, row_count)]`,升序。

    ⛔ 读函数不执行 DDL(§7.1):库未迁移 → 空列表。"""
    with readonly_tables("fact_packs", db_path=db_path) as conn:
        if conn is None:
            return []
        versions = [pack_version] if pack_version is not None else [
            str(row[0]) for row in conn.execute(
                "SELECT DISTINCT pack_version FROM fact_packs "
                + ("UNION SELECT DISTINCT pack_version FROM fact_pack_revisions"
                   if _has_revision_table(conn) else "")
            ).fetchall()
        ]
        result: list[Tuple[str, str, str, int]] = []
        for version in versions:
            for row in _latest_by_day(_manifest_rows(conn, pack_version=version)):
                result.append((str(row[1]), str(row[2]), str(row[3]), int(row[5])))
        return sorted(result, key=lambda item: (item[0], item[1]))


# ══════════════════════════════════════════════════════════════════════════
# 保留策略(§3.2:parquet 滚动裁剪,清单行永不裁剪)
# ══════════════════════════════════════════════════════════════════════════

def trim_parquet(
    *,
    keep: int = RETENTION_PACKS,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    dry_run: bool = False,
) -> List[date]:
    """删除最近 `keep` 个已冻结交易日**之外**的 `fact_pack` parquet 文件。

    🔴 **只删 parquet,⛔ 绝不删 `fact_packs` 行** —— 审计要活得比数据久:
    「那次跑用的是哪版包、指纹是多少」在数据被裁剪之后仍然查得到。
    返回被删(或 dry_run 下将被删)的交易日列表。"""
    if keep < 1:
        raise ValueError("keep 必须 >= 1")
    rows = list_packs(db_path=db_path)
    frozen_days = sorted({datetime.strptime(r[0], "%Y%m%d").date() for r in rows})
    doomed = set(frozen_days[:-keep] if len(frozen_days) > keep else [])
    # 一天可能有多版(§5.3.2 第 3 条)，每一版都独立裁剪。
    targets: Dict[date, List[Path]] = {}
    with readonly_tables("fact_packs", db_path=db_path) as conn:
        manifests: list[Sequence] = []
        if conn is not None:
            versions = [str(row[0]) for row in conn.execute(
                "SELECT DISTINCT pack_version FROM fact_packs "
                + ("UNION SELECT DISTINCT pack_version FROM fact_pack_revisions"
                   if _has_revision_table(conn) else "")
            ).fetchall()]
            for version in versions:
                manifests.extend(_manifest_rows(conn, pack_version=version))
    for manifest in manifests:
        d = datetime.strptime(str(manifest[1]), "%Y%m%d").date()
        if d in doomed:
            targets.setdefault(d, []).append(pack_file_path(
                d, str(manifest[2]), parquet_dir, revision=int(manifest[10])))
    removed: List[date] = []
    for d in sorted(targets):
        alive = [p for p in targets[d] if p.exists()]
        if not alive:
            continue
        removed.append(d)
        if not dry_run:
            for p in alive:
                p.unlink()
    if removed:
        logger.info(
            "[fact_pack] 保留策略:保留最近 %d 个交易日,%s %d 个更早的 parquet(清单行原样保留)",
            keep, "将删" if dry_run else "已删", len(removed),
        )
    return removed


__all__ = [
    "PARQUET_TABLE",
    "RETENTION_PACKS",
    "ORIGIN_LIVE",
    "ORIGIN_BACKFILL",
    "STATE_FROZEN",
    "PackAlreadyFrozen",
    "PackNotFrozen",
    "FactPackIntegrityError",
    "FactPack",
    "pack_file_path",
    "freeze_pack",
    "freeze_correction",
    "load_pack",
    "load_pack_by_id",
    "latest_pack",
    "load_pack_range",
    "list_packs",
    "trim_parquet",
]
