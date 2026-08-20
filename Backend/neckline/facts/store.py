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
   ⚠ 修复之前落盘的包在**遗留布局**(`fact_pack/year=YYYY/…`)里:读侧
   `resolve_pack_path` 拿指纹核对后仍读得到,写侧 `_relocate_legacy_day` 在下次冻结
   同一天时把它归位。⛔ 不再往遗留位置写任何东西。
4. **只读** —— `load_pack()` 返回 `@dataclass(frozen=True)` 的 `FactPack`;
   `rows` 是**每次调用现读 parquet** 的属性,调用方拿到的永远是自己的副本,改不脏别人。

**保留策略**(§3.2):生产滚动保留 `RETENTION_PACKS`(250)个交易日的 parquet;更早的
裁剪,**清单行永远保留**——「那次跑用的哪版包」必须活得比数据久。
"""

from __future__ import annotations

import hashlib
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


# ══════════════════════════════════════════════════════════════════════════
# 路径:**一版一个坑位**(纪律 3 的落地,2026-08-21 复审 R1-B1)
# ══════════════════════════════════════════════════════════════════════════

def pack_file_path(
    trade_date: date, pack_version: str, parquet_dir: Optional[Path] = None
) -> Path:
    """当前布局:`fact_pack/version=<v>/year=YYYY/YYYYMMDD.parquet`。

    🔴 版本进路径是「口径变了就发新 `pack_version`」这条纪律**唯一**能成立的前提。
    路径里没有版本时,同一天的第二版会把第一版的数据原地抹掉,而第一版的清单行
    (连同它的 `content_fingerprint`)还在 —— 那就是一条会说谎的审计记录。
    """
    return day_file_path(PARQUET_TABLE, trade_date, parquet_dir, version=pack_version)


def legacy_pack_file_path(trade_date: date, parquet_dir: Optional[Path] = None) -> Path:
    """V2.5.0 早期布局(**路径里没有版本**):`fact_pack/year=YYYY/YYYYMMDD.parquet`。

    ⚠ **只读兼容**:R1-B1 修复之前冻结的包(生产 / 开发机上已有的 fp-2)落在这里。
    ⛔ 不再往这个位置写任何东西 —— 写入一律走 `pack_file_path`。
    """
    return day_file_path(PARQUET_TABLE, trade_date, parquet_dir)


def resolve_pack_path(
    trade_date: date,
    pack_version: str,
    fingerprint: str,
    parquet_dir: Optional[Path] = None,
) -> Path:
    """这份清单行**真正**指向哪个文件。

    ① 带版本的路径在 → 就是它;
    ② 否则遗留路径在**且它的 sha256 逐字等于本行的 `content_fingerprint`** → 是它;
    ③ 都不是 → 返回带版本的路径(让「文件不在」的报错指向当前布局)。

    🔴 第 ② 步的 sha256 **不是可省的礼貌检查**:遗留路径是「一天一个坑位」的旧布局,
    同一天若已经存在过第二版,那个文件属于谁在路径上看不出来。拿指纹核一次,
    「读回来的行属于这条清单」就从**约定**变成了**判据** —— 这正是 R1-B1 那条
    「记账物在说谎」要根除的东西。⛔ 不许把这一步改成「文件在就用」。
    """
    versioned = pack_file_path(trade_date, pack_version, parquet_dir)
    if versioned.exists():
        return versioned
    legacy = legacy_pack_file_path(trade_date, parquet_dir)
    if legacy.exists() and _sha256(legacy) == fingerprint:
        return legacy
    return versioned


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
    _parquet_dir: Optional[Path] = field(default=None, repr=False, compare=False)

    @property
    def path(self) -> Path:
        """本包的 parquet 落在哪(⚠ 遗留布局的回落**要过指纹**,见 `resolve_pack_path`)。"""
        return resolve_pack_path(
            self.trade_date, self.pack_version, self.content_fingerprint, self._parquet_dir)

    @property
    def rows(self) -> pl.DataFrame:
        """现读 parquet。文件已被保留策略裁剪 → `FileNotFoundError`(清单行仍在,
        ⛔ 不返回空表冒充「那天没数据」)。"""
        p = self.path
        if not p.exists():
            raise FileNotFoundError(
                f"{self.trade_date} 的事实包 parquet 已不在({p}) —— "
                f"清单行仍在(pack_id={self.pack_id}),数据本身已被保留策略裁剪")
        return pl.read_parquet(p)

    def field(self, name: str) -> pl.Series:
        """按列取数。列名不在 `PACK_COLUMNS` 里直接抛(⛔ 不静默返回空列)。"""
        if name not in PACK_COLUMNS:
            raise KeyError(f"{name!r} 不是事实包的列;可用列见 facts.pack.PACK_COLUMNS")
        return self.rows[name]


# ══════════════════════════════════════════════════════════════════════════
# 写(唯一入口)
# ══════════════════════════════════════════════════════════════════════════

def _relocate_legacy_day(trade_date: date, root: Path, db_path: Optional[Path]) -> None:
    """把这一天还躺在**遗留布局**里的 parquet 挪进它自己那一版的路径下。

    R1-B1 修复之前冻结的包路径里没有版本。要给同一天冻**第二版**之前,必须先弄清楚
    那个文件属于谁并把它归位 —— 否则新版本一落地,旧版本的清单行就又开始说谎了
    (那正是本次要根除的东西)。

    归属由**清单**判定,⛔ 不猜:
      · 该日 0 条清单行 → 它是上次进程死在中间留下的**孤儿**,原样留着(下面会被
        新文件顶掉或就地不管),⛔ 不动;
      · 该日恰好 1 条清单行且指纹对得上 → 归它,`os.replace` 到带版本的路径;
      · 指纹对不上,或该日已有 ≥2 条清单行 → **当场停手**。这份数据已经处于
        「谁是谁说不清」的状态,⛔ 不许再往上叠一版把水搅得更浑 —— 让操作者来判。
    """
    legacy = legacy_pack_file_path(trade_date, root)
    if not legacy.exists():
        return
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT pack_version, content_fingerprint FROM fact_packs WHERE trade_date=?",
            (_d(trade_date),),
        ).fetchall()
    if not rows:
        logger.warning(
            "[fact_pack] %s 的遗留布局文件没有对应清单行(%s)—— 当孤儿留着,⛔ 不猜它属于哪一版",
            trade_date, legacy)
        return
    digest = _sha256(legacy)
    owners = [r[0] for r in rows if r[1] == digest]
    if len(owners) != 1:
        raise PackAlreadyFrozen(
            f"{trade_date} 的遗留布局 parquet({legacy})对不上唯一一条清单行:"
            f"该日清单有 {len(rows)} 条({[r[0] for r in rows]}),指纹能对上的有 {len(owners)} 条。"
            f"⛔ 在弄清这个文件属于哪一版之前不许再冻新版本 —— 先人工核对再手工归位")
    target = pack_file_path(trade_date, owners[0], root)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(legacy, target)
    logger.info(
        "[fact_pack] %s 的遗留布局 parquet 已归位到 %s(属于 %s,指纹 %s…)",
        trade_date, target, owners[0], digest[:12])


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
    # —— 先把这一天可能还留在**遗留布局**里的那份归位,再动手写新版本 ——————
    _relocate_legacy_day(pack.trade_date, root, db_path)

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
            PARQUET_TABLE, pack.trade_date, pack.rows.select(list(PACK_COLUMNS)),
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


# ══════════════════════════════════════════════════════════════════════════
# 读(只读)
# ══════════════════════════════════════════════════════════════════════════

_SELECT_COLUMNS = (
    "pack_id, trade_date, pack_version, origin, content_fingerprint, row_count, "
    "sources_json, market_json, suspend_anomaly_count, frozen_at"
)


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
        _parquet_dir=parquet_dir,
    )


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
    把一个 v2.4.2 老库迁移掉:迁移只属于启动、显式写命令、RC 迁移流程。
    """
    with readonly_tables("fact_packs", db_path=db_path) as conn:
        row = None if conn is None else conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM fact_packs WHERE trade_date=? AND pack_version=?",
            (_d(trade_date), pack_version),
        ).fetchone()
    if row is None:
        raise PackNotFrozen(f"{trade_date} 没有 {pack_version} 的冻结事实包")
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
    sql = f"SELECT {_SELECT_COLUMNS} FROM fact_packs WHERE pack_version=?"
    args: List = [pack_version]
    if on_or_before is not None:
        sql += " AND trade_date<=?"
        args.append(_d(on_or_before))
    sql += " ORDER BY trade_date DESC LIMIT 1"
    with readonly_tables("fact_packs", db_path=db_path) as conn:
        row = None if conn is None else conn.execute(sql, tuple(args)).fetchone()
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

    缺哪天就少哪天的行(⛔ 不抛 ——「那天没冻结」是可被读出来的事实,由调用方对着
    `fact_packs` 自己数)。
    """
    if end > as_of:
        raise ValueError(
            f"读取范围截止到当日(策略契约第三条):end={end} > as_of={as_of}")
    if start > end:
        raise ValueError(f"start({start}) > end({end})")
    span = len(trading_days_between(start, end))
    if span > MAX_LOOKBACK_PACKS:
        raise ValueError(
            f"区间 {start}~{end} 含 {span} 个交易日,超过 MAX_LOOKBACK_PACKS={MAX_LOOKBACK_PACKS}"
            f"(工程容量上限,§3.2)")

    # ⚠ 连 `content_fingerprint` 一起取:遗留布局的回落要拿它核对(`resolve_pack_path`)。
    with readonly_tables("fact_packs", db_path=db_path) as conn:
        days = [] if conn is None else [
            (r[0], r[1]) for r in conn.execute(
                "SELECT trade_date, content_fingerprint FROM fact_packs WHERE pack_version=? "
                "AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
                (pack_version, _d(start), _d(end)),
            ).fetchall()
        ]
    if not days:
        return pl.DataFrame()

    picked = list(columns)
    if not picked:
        raise ValueError("columns 不能为空 —— 列投影是必填的(见 docstring 的实测内存账)")
    unknown = [c for c in picked if c not in PACK_COLUMNS]
    if unknown:
        raise KeyError(f"{unknown} 不是事实包的列;可用列见 facts.pack.PACK_COLUMNS")
    if "trade_date" not in picked:
        picked = ["trade_date", *picked]

    frames: List[pl.DataFrame] = []
    for day_s, fingerprint in days:
        d = datetime.strptime(day_s, "%Y%m%d").date()
        p = resolve_pack_path(d, pack_version, fingerprint, parquet_dir)
        if not p.exists():           # 已被保留策略裁剪:清单在、数据不在
            logger.warning("[fact_pack] %s 的 parquet 已裁剪,区间读跳过该日(%s)", d, p)
            continue
        frames.append(pl.read_parquet(p, columns=picked))
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="vertical_relaxed").sort(["trade_date", "ts_code"])


def list_packs(
    *, pack_version: Optional[str] = None, db_path: Optional[Path] = None
) -> List[Tuple[str, str, str, int]]:
    """清单速览 `[(trade_date, pack_version, origin, row_count)]`,升序。

    ⛔ 读函数不执行 DDL(§7.1):库未迁移 → 空列表。"""
    sql = "SELECT trade_date, pack_version, origin, row_count FROM fact_packs"
    args: Tuple = ()
    if pack_version is not None:
        sql += " WHERE pack_version=?"
        args = (pack_version,)
    sql += " ORDER BY trade_date"
    with readonly_tables("fact_packs", db_path=db_path) as conn:
        if conn is None:
            return []
        return [(r[0], r[1], r[2], int(r[3])) for r in conn.execute(sql, args).fetchall()]


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
    # ⚠ 一天可能有多版(§5.3.2 第 3 条),裁剪要把**每一版**都收掉;遗留布局那一份
    # 也一并收 —— 否则「250 天之外的数据已经不在」这句话在旧文件上不成立。
    targets: Dict[date, List[Path]] = {}
    for day_s, version, _origin, _n in rows:
        d = datetime.strptime(day_s, "%Y%m%d").date()
        if d in doomed:
            targets.setdefault(d, []).append(pack_file_path(d, version, parquet_dir))
    for d in doomed:
        targets.setdefault(d, []).append(legacy_pack_file_path(d, parquet_dir))
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
    "FactPack",
    "pack_file_path",
    "legacy_pack_file_path",
    "resolve_pack_path",
    "freeze_pack",
    "load_pack",
    "latest_pack",
    "load_pack_range",
    "list_packs",
    "trim_parquet",
]
