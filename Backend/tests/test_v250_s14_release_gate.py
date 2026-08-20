"""V2.5.0 **S14 发版门禁**的机器判据(PROJECT_PLAN §9 / §6 S14)。

🔴 **S14 本组只准备、⛔ 一步都没执行**:没有 ssh、没有部署、没有碰生产、没有改
`MemoryMax`、没有动生产库。本文件跑的每一件事都在 **`tmp_path` 临时库**上。

| 组 | 断言 |
|---|---|
| A · 迁移演练 | 拿 **v2.4.2 Build 9 那份 `_SCHEMA`** 造一个真老库 → 塞历史行 → 跑今天的 `init_schema` → **历史行逐表逐列不变**、16 张新表建出来且为空、`integrity_check` 通过 |
| B · 纯新增 | 本版**零 ALTER / 零 DROP 新增**:`_COLUMN_MIGRATIONS` 与 `_migrate_columns` 的写路径与 v2.4.2 逐字相同 |
| C · unit 拓扑 | `deploy/` 恰好 8 个 unit、**零新增**;三段 oneshot 的 `--segments` = 新段序;`StopWhenUnneeded=yes` 还在;三段 service ⛔ 无 `RemainAfterExit` |
| D · 回滚锚点 | 回滚目标 `v2.4.2` / commit `ee12b9b` 真的存在且可取到源码 |
| E · 上线后状态 | 22 项待标定参数一个默认值都没有;`config/k9-params.json` **不在仓库里** |

⚠ **本文件不替代 §9 的人工步骤**:备份两份 + `integrity_check` + 源码锚点 + 明确目标
确认,那几步要在**生产上**做,清单在 `PROJECT_PLAN.md §9.6`。这里只把**能在本地机器
上先跑一遍的那几件**做成机器判据 —— 「演练过了」与「写了一份清单」不是一回事。
"""

from __future__ import annotations

import ast
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from neckline.db import init_schema
from tests import guard_scan

_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY = _ROOT / "deploy"
_DB_PY = _ROOT / "neckline" / "db.py"

#: 🔴 **已验证的回滚目标**(PROJECT_PLAN §9.4)。⛔ 改它之前先确认新目标真的部署过。
ROLLBACK_COMMIT = "ee12b9b"
ROLLBACK_VERSION = "v2.4.2"

#: 本版新增的 16 张表(**纯新增**,零 ALTER / 零 DROP)。
V250_NEW_TABLES: Tuple[str, ...] = (
    "fact_packs", "sw_industry_classify", "sw_industry_daily", "sw_industry_member",
    "k9_runs", "k9_channel_hits", "k9_listing_entries", "k9_reports",
    "k9_coverage_daily", "k9_coverage_misses",
    "k9_checklists", "k9_d1_verdicts", "k9_playbooks",
    "k9_explain_notes", "k9_explain_audit",
    "review_conclusions",
)

#: 本版新增的两个 parquet 目录(回滚只需删这两个)。
V250_NEW_PARQUET_DIRS: Tuple[str, ...] = ("fact_pack", "k9_disposition")


def _baseline_db_py() -> str:
    try:
        return subprocess.run(
            ["git", "show", f"{ROLLBACK_COMMIT}:Backend/neckline/db.py"],
            cwd=_ROOT.parent, capture_output=True, check=True,
        ).stdout.decode("utf-8")
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover
        pytest.skip(f"取不到 {ROLLBACK_COMMIT} 的 db.py(浅克隆 / 无 git)")


#: 建表语句里的表名。🔴 **锁 ASCII 标识符**:Python 的 `\w` 在 Unicode 模式下
#: **匹配中文** —— `db.py` 的注释里有一句「…… 天然幂等」,裸 `\w+` 会把它当成一张表名
#: 报出来(实测踩到)。表名一律 ASCII,判据就该写死成 ASCII。
_CREATE_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS ([A-Za-z_][A-Za-z0-9_]*)")


def _module_literal(src: str, name: str) -> Any:
    """按 AST 取模块级常量的字面量。⛔ 不 exec 那一版模块(会引入一整套旧 import)。"""
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name:
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == name and node.value is not None:
            return ast.literal_eval(node.value)
    raise AssertionError(f"取不到模块级常量 `{name}`")


# ══════════════════════════════════════════════════════════════════════════
# A. 迁移演练:v2.4.2 老库 → 今天的 init_schema
# ══════════════════════════════════════════════════════════════════════════

#: DoD 逐字点名的历史件 + 几张最要紧的 K8 只读留档表(裁定 6)。
_HISTORY_TABLES: Tuple[str, ...] = (
    "baskets", "basket_cards", "basket_members", "tier_history", "gate_evaluations",
    "out_candidates", "reports", "reviews", "positions", "decision_log",
    "industry_strength_daily", "selection_packs",
)


def _dummy_row(conn: sqlite3.Connection, table: str) -> Dict[str, Any]:
    """给一张表造一行「历史数据」:主键 / 自增列交给 SQLite,其余 NOT NULL 列按声明
    类型塞一个**确定性**的值(⛔ 不用随机 —— 失败要能复现)。"""
    values: Dict[str, Any] = {}
    for _cid, name, decl, notnull, dflt, pk in conn.execute(f"PRAGMA table_info({table})"):
        if pk and "INT" in (decl or "").upper():
            continue                                   # AUTOINCREMENT 主键
        if not notnull and dflt is None:
            continue                                   # 可空无默认 → 刻意留空
        upper = (decl or "TEXT").upper()
        if "INT" in upper:
            values[name] = 7
        elif "REAL" in upper or "FLOA" in upper or "DOUB" in upper:
            values[name] = 1.25
        else:
            values[name] = f"v242-{table}-{name}"
    return values


def _snapshot(conn: sqlite3.Connection, table: str) -> List[tuple]:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    order = ", ".join(f'"{c}"' for c in cols)
    return list(conn.execute(f"SELECT {order} FROM {table}"))


def test_v242_history_rows_survive_the_v250_migration_bit_for_bit(tmp_path: Path):
    """🔴 **§9.2 的演练**:「迁移后立刻核对:K8 只读表的行数与迁移前**逐表相等**」。

    做法 = 用 `v2.4.2 Build 9` 那份 `_SCHEMA` 造一个**真老库** → 塞历史行 →
    跑今天的 `init_schema`(= 生产升级时会真的发生的事)→ 逐表逐行逐列对拍。
    ⚠ 判据是「**在老库上真跑一遍迁移**」,⛔ 不是「读一眼 `_COLUMN_MIGRATIONS` 说它
    看起来是可空的」。
    """
    baseline = _baseline_db_py()
    old_schema = _module_literal(baseline, "_SCHEMA")
    db = tmp_path / "v242_like.db"

    conn = sqlite3.connect(str(db))
    conn.executescript(old_schema)
    # 🔴 **先把基线自己的 `_COLUMN_MIGRATIONS` 也跑一遍** —— 真实的生产库不是"刚
    # `executescript(_SCHEMA)` 出来的样子":v2.4.2 的 `init_schema` 每次启动都会跑
    # `_migrate_columns` 补列,那些列在生产上**早就补齐了**。
    # ⚠ 少了这一步,演练会把「v2.4.2 自己那几条已经跑过的补列」误报成「v2.5.0 动了历史行」
    # —— 一个**假阳性**,而假阳性会逼着后来者把守门放宽。
    for table, column, ddl in _module_literal(baseline, "_COLUMN_MIGRATIONS"):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.commit()
    seeded: Dict[str, List[tuple]] = {}
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    covered = [t for t in _HISTORY_TABLES if t in existing]
    assert len(covered) >= 8, f"老库里只找到 {covered} —— 基线取错了?"
    for table in covered:
        row = _dummy_row(conn, table)
        if not row:
            continue
        cols = ", ".join(f'"{c}"' for c in row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
    conn.commit()
    for table in covered:
        seeded[table] = _snapshot(conn, table)
    conn.close()

    # —— 这就是升级那一刻会跑的东西 ——
    init_schema(db)

    conn = sqlite3.connect(str(db))
    try:
        # ① 历史行**逐表逐列不变**。
        for table, before in seeded.items():
            after = _snapshot(conn, table)
            assert after == before, (
                f"🔴 `{table}` 的历史行被迁移改动了 —— 裁定 6:K8 表保留、只读、"
                f"不迁移、不回填。\n  before={before}\n  after ={after}")
        # ② 16 张新表建出来了,且**全是空的**(纯新增,⛔ 不回填)。
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in V250_NEW_TABLES:
            assert t in tables, f"本版新表 `{t}` 没建出来"
            assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, (
                f"`{t}` 升级后不该有行 —— 本版**不回填**任何历史数据")
        # ③ 老库的表**一张没少**(⛔ 不 DROP)。
        old_tables = set(_CREATE_TABLE_RE.findall(old_schema))
        assert old_tables <= tables, f"升级后少了这些老表:{sorted(old_tables - tables)}"
        # ④ 库仍然是好的。
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_init_schema_is_idempotent_on_a_fresh_db(tmp_path: Path):
    """连跑两次 `init_schema` 不炸、表集合逐字相同(升级脚本可能重入)。"""
    db = tmp_path / "fresh.db"
    init_schema(db)
    conn = sqlite3.connect(str(db))
    first = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    init_schema(db)
    conn = sqlite3.connect(str(db))
    try:
        assert {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")} == first
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# B. 本版是**纯新增**:零 ALTER / 零 DROP 新增
# ══════════════════════════════════════════════════════════════════════════

#: 🔴 会改动既有数据的 SQL 动词。**大小写不敏感**、`DELETE` 在表里。
#: 复审实测:原来的表只有 4 个**大写**关键字、`DELETE` 根本不在里面 —— 于是
#: `db.py` 里一句小写 `update baskets set …`(CE8)与 `DELETE FROM baskets`(CE9)
#: 双双照绿。裁定 6 的原文是「表不删、**不迁移、不回填**」,`DELETE` 正是「回填」的
#: 反面动作,它不在关键字表里等于这条裁定少了一半。
_MUTATING_KEYWORDS: Tuple[str, ...] = (
    "ALTER TABLE", "DROP TABLE", "DROP INDEX", "UPDATE ", "DELETE FROM",
)


def _ddl_statements(src: str) -> List[str]:
    """源码里所有会改动既有数据的 SQL 语句行(⚠ 先剥掉注释)。"""
    out: List[str] = []
    for line in src.splitlines():
        code = line.split("--", 1)[0].split("#", 1)[0]
        upper = code.upper()          # 🔴 大小写不敏感 —— SQL 关键字本来就是
        for kw in _MUTATING_KEYWORDS:
            if kw in upper:
                out.append(re.sub(r"\s+", " ", code.strip()))
                break
    return sorted(out)


def test_the_ddl_scanner_is_case_insensitive_and_knows_about_delete():
    """扫描器自检:一个看不见小写、也看不见 `DELETE` 的判据等于半个判据。"""
    assert _ddl_statements('conn.execute("update baskets set x=1")')
    assert _ddl_statements('conn.execute("DELETE FROM baskets")')
    assert _ddl_statements('conn.execute("alter table baskets add column z TEXT")')
    # 反向:注释里讲清这条纪律⛔ 不许被判红。
    assert _ddl_statements("# 本版⛔ 不 ALTER TABLE baskets") == []


def test_v250_adds_no_new_column_migration_and_no_new_alter_or_drop():
    """🔴 **§9.2**:「本版所有 schema 变更是**纯新增**(新表 + 新 parquet 目录),
    ⛔ 不 ALTER、不 DROP、不 UPDATE 任何 K8 表」。

    ⚠ `db.py` 里**确实**有 ALTER / DROP —— 它们是 V2.2 / V2.4.2 就在的**幂等迁移**
    (`_relax_holding_eod_check_notnull` / `_migrate_selection_generation_tables`),
    生产上早就跑过了。本条锁的是「**本版一条都没新增**」:与基线逐条比对,
    ⛔ 不是"扫到 ALTER 就红"(那会逼人删掉已经在生产跑过的迁移)。
    """
    old_src, new_src = _baseline_db_py(), _DB_PY.read_text(encoding="utf-8")

    added = sorted(set(_ddl_statements(new_src)) - set(_ddl_statements(old_src)))
    assert not added, (
        f"🔴 本版新增了 ALTER / DROP / UPDATE 语句:{added} —— §9.2 定死本版是**纯新增**。"
        f"若确有必要,先改 PROJECT_PLAN §9.2,再改这条守门。")

    assert _module_literal(new_src, "_COLUMN_MIGRATIONS") == \
        _module_literal(old_src, "_COLUMN_MIGRATIONS"), (
        "`_COLUMN_MIGRATIONS` 与 v2.4.2 不同 —— 本版不该给既有表加列")


def test_v250_new_tables_are_all_actually_new():
    """反向:那 16 张表在 v2.4.2 的 schema 里**一张都没有**(⛔ 别把老表当新表登记)。"""
    old_tables = set(_CREATE_TABLE_RE.findall(
        _module_literal(_baseline_db_py(), "_SCHEMA")))
    overlap = sorted(set(V250_NEW_TABLES) & old_tables)
    assert not overlap, f"这些表 v2.4.2 就有,不是本版新增:{overlap}"
    new_tables = set(_CREATE_TABLE_RE.findall(
        _module_literal(_DB_PY.read_text(encoding="utf-8"), "_SCHEMA")))
    actually_new = sorted(new_tables - old_tables)
    assert actually_new == sorted(V250_NEW_TABLES), (
        f"新表清单与 schema 对不上 —— schema 里实际新增:{actually_new}")


# ══════════════════════════════════════════════════════════════════════════
# B'. 裁定 6 的真牙齿:K8 留档表**按表名**的写保护
# ══════════════════════════════════════════════════════════════════════════
#
# 🔴 复审实测:`ALTER TABLE baskets ADD COLUMN …` 写进 `neckline/k9/store.py`(CE7)、
# `k9/store.py` 里 `DELETE FROM baskets` + `UPDATE positions SET …` —— **全绿**,
# 因为上面那条判据只扫 `db.py` 一个文件。「K8 表只读」这件事在本版之前**只有 DROP
# 一个方向有牙齿**(而 DROP 那条在 `test_v250_s1_retirement_guard.py`)。
#
# 判据形状:⛔ 不是笼统扫关键字(那会把 `db.py` 里 v2.4.2 就在的幂等迁移判红),
# 而是**「写动词 + 留档表名」的组合**。表名清单走 `LEGACY_READONLY_TABLES` 这个
# 现成的单一源,⛔ 不在这里再抄一份。
# ══════════════════════════════════════════════════════════════════════════

_LEGACY_WRITE_RE = re.compile(
    r"\b(ALTER\s+TABLE|DELETE\s+FROM|INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE)"
    r"\s+(?:IF\s+EXISTS\s+)?[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

#: 🔴 **唯一一处有理由写留档表的地方**,连同理由。
#: `sentinel_events`:裁定 7 定死「包没了、**表名留着**」—— 盘中哨兵整包退役,但
#: 当日防重台账 `neckline/dedup.py` 搬家之后**仍然在写这张表**(`test_v250_s1_
#: retirement_guard.py::test_dedup_still_writes_the_sentinel_events_table` 正向锁着
#: 它)。它列在 `LEGACY_READONLY_TABLES` 里是为了「表不许被删」,**不是**「不许被写」。
_LEGACY_WRITE_ALLOW: Dict[str, str] = {
    "neckline/dedup.py::sentinel_events":
        "裁定 7:哨兵包退役、表名留着,当日防重台账仍以它为唯一落点",
}


def _legacy_write_sites(paths: List[Path]) -> List[str]:
    """哪些文件的**字符串常量**里出现了针对 K8 留档表的写语句。

    走 `guard_scan.string_constants()`(AST)而不是按行 grep:
      · 注释与 docstring 天然不进 —— 一条纪律总要写出它禁止的那句 SQL 才解释得清;
      · **跨行拼的 SQL 已被解析器折成一个常量**,按行 grep 只看得见前半句,
        `"INSERT OR IGNORE INTO sentinel_events "` 这种写法的表名就丢在第二行了。
    """
    from tests.test_v250_s1_retirement_guard import (  # noqa: PLC0415
        LEGACY_READONLY_TABLES,
    )

    legacy = {t.lower() for t in LEGACY_READONLY_TABLES}
    hits: List[str] = []
    for path in sorted(paths):
        rel = str(path.relative_to(_ROOT))
        for lineno, text in guard_scan.string_constants(path):
            for verb, table in _LEGACY_WRITE_RE.findall(text):
                if table.lower() not in legacy:
                    continue
                if f"{rel}::{table}" in _LEGACY_WRITE_ALLOW:
                    continue
                verb = re.sub(r"\s+", " ", verb.upper())
                hits.append(f"{rel}:{lineno} {verb} {table}")
    return hits


def test_the_legacy_write_detector_actually_detects(tmp_path: Path):
    """扫描器自检 —— 四种写法都要看得见,注释里的说明⛔ 不许被判红。"""
    bait = tmp_path / "bait.py"
    bait.write_text(
        '"""⛔ 本模块不许 UPDATE positions —— 这句说明不算命中。"""\n'
        "def go(conn):\n"
        '    conn.execute("ALTER TABLE baskets ADD COLUMN z TEXT")\n'
        '    conn.execute("delete from positions where id=1")\n'
        '    conn.execute("INSERT OR REPLACE INTO strategy_versions "\n'
        '                 "(version) VALUES (?)", ("x",))\n'
        '    conn.execute("update  decision_log set note=?", ("x",))\n'
        "    # ⛔ 别写 DELETE FROM baskets(注释不算)\n",
        encoding="utf-8")
    # ⚠ `_legacy_write_sites` 拿 `relative_to(_ROOT)` 作标签,诱饵得放在仓内路径下
    # 才走得通 —— 这里直接调底层正则做自检,判的是**判据形状**。
    found = [
        (v.upper(), t.lower())
        for _ln, s in guard_scan.string_constants(bait)
        for v, t in _LEGACY_WRITE_RE.findall(s)
    ]
    tables = {t for _v, t in found}
    assert tables == {"baskets", "positions", "strategy_versions", "decision_log"}, found
    assert len(found) == 4, "跨行拼接的那条 INSERT 被切碎了?"


def test_the_k8_readonly_archive_has_no_application_layer_writer():
    """🔴 **裁定 6**:K8 历史表「保留、只读、不迁移、不回填」。

    ⚠ 扫描域是 `neckline/**` 减去 `db.py` —— `db.py` 拥有建表与 v2.4.2 就在的幂等
    迁移,它归上面那条「与基线逐条比对」的判据管;应用层则是**一句都不许有**。
    """
    scanned = [p for p in sorted((_ROOT / "neckline").rglob("*.py")) if p != _DB_PY]
    assert len(scanned) > 50, f"扫描域只有 {len(scanned)} 个文件 —— glob 怕是瞎了"
    hits = _legacy_write_sites(scanned)
    assert hits == [], (
        "应用层写了 K8 只读留档表(裁定 6:不迁移、不回填):\n" + "\n".join(hits))


@pytest.mark.xfail(strict=True, reason=(
    "🔴 已知违规,归下一波(PROJECT_PLAN §13.1-B12):`scripts/oneoff/` 里三个 K8 "
    "一次性脚本仍在写裁定 6 的只读留档表 —— bootstrap_k4.py 与 retire_k4_b3.py 写 "
    "`strategy_versions`、fix_position_buy_dates.py 写 `positions`。§4.3 已把章程 / "
    "包激活脚本整块列进退役,但『删掉还是留档』要用户拍板,⛔ 施工侧不自行删脚本、"
    "更⛔ 不为了凑绿把它们从扫描域里摘出去。修好之后请连同这个 xfail 一起删。"))
def test_the_oneoff_scripts_do_not_write_the_k8_readonly_archive():
    """`scripts/**` 这一侧的同一条判据。

    它们是「K8 表已无应用层写入方」这句验收话的现成反例。危害有限(都要人手动跑,
    `fix_position_buy_dates.py` 还默认演练、只有 `--confirm` 才写),但**可见**比
    绿要紧:这条闸红着,下一个人就知道这里还欠着账。
    """
    hits = _legacy_write_sites(sorted((_ROOT / "scripts").rglob("*.py")))
    assert hits == [], (
        "一次性脚本仍在写 K8 只读留档表:\n" + "\n".join(hits))


def test_the_legacy_write_allowlist_stays_justified():
    """白名单里每一条都要有理由,且那条理由指的东西**真的还在**。

    ⛔ 白名单是给「结构上就该这样」的例外用的,不是给「一时改不动」用的。
    """
    assert set(_LEGACY_WRITE_ALLOW) == {"neckline/dedup.py::sentinel_events"}
    for key, reason in _LEGACY_WRITE_ALLOW.items():
        rel, table = key.split("::")
        assert (_ROOT / rel).exists(), f"白名单指向一个不存在的文件:{rel}"
        assert len(reason) > 10, f"{key} 的理由太短,说不清为什么"
    # 正向:`dedup.py` **确实**还在写那张表 —— 例外若已作废,白名单要跟着删。
    assert _LEGACY_WRITE_RE.search(
        "\n".join(s for _ln, s in guard_scan.string_constants(_ROOT / "neckline" / "dedup.py"))
    ), "`dedup.py` 不再写 `sentinel_events` 了 —— 这条白名单可以删了"


# ══════════════════════════════════════════════════════════════════════════
# B''. 回滚边界的那句话:**读取 helper 不执行 DDL**
# ══════════════════════════════════════════════════════════════════════════
#
# 🔴 这条闸**本版之前根本不存在**(复审:`grep -rn "不执行 DDL\|不是迁移触发器\|
# readonly_connection" tests/*.py` 全空)。而这三处白纸黑字断言了它:
#   · `README.md`「API、报告和复盘的读取 helper 不执行 DDL;……任何 GET 或日常读取
#     都不是迁移触发器。」
#   · `PROJECT_PLAN.md §9.2` 与 §9.6 步骤 2:「⛔ API / 报告 / 复盘的读 helper 不执行 DDL。」
#   · `PROJECT_PLAN.md §9.4` 与 §9.6 步骤 6.8:「⛔ 任何 GET / 日常读取都不是迁移触发器
#     —— 回滚边界就是『迁移前备份 + 已验证的 v2.4.2 源码』这两样。」
#
# **回滚边界的论证整个建立在这句话上**:§9.6 步骤 6.6 要求「K8 只读表行数与步骤 1.2
# 备份逐表相等」,操作者若用任何 Neckline 侧工具把 `db_path` 指向那份备份去比对,
# 一次读就往备份里建 16 张表,两份备份 sha256 相等这条前提随之作废。
#
# 复审实测(拿 `ee12b9b` 的 `_SCHEMA` 造 v2.4.2 老库,只调一个纯读 helper):
#   老库 59 表 → `report.store.load_k9_report(...)` 返回 None → **当场 75 表**。
#
# ⚠ 本条现在是 **xfail(strict=True)**:闸先建起来、红着,让欠账**可见**。
# ⛔ 不许为了凑绿把扫描域缩小或把前缀清单改窄。
# ══════════════════════════════════════════════════════════════════════════

#: 读路径的函数名前缀。⚠ 判据是**名字**而不是「有没有写 SQL」—— 一个叫 `load_x`
#: 的函数,调用方就是当它只读来用的,这正是这条纪律要保护的那个预期。
_READ_PREFIXES: Tuple[str, ...] = ("load_", "latest_", "list_", "get_", "read_", "fetch_")


def _read_helpers_reaching_init_schema() -> List[str]:
    hits: List[str] = []
    for path in sorted((_ROOT / "neckline").rglob("*.py")):
        if path == _DB_PY:          # `db.py` 拥有 `init_schema` 自己
            continue
        hits.extend(guard_scan.reaches(
            path, _READ_PREFIXES, "init_schema",
            label=str(path.relative_to(_ROOT))))
    return hits


def test_the_read_path_detector_actually_detects(tmp_path: Path):
    """扫描器自检:间接调用也要看得见 —— 只查直接调用的版本,套一层 helper 就绕过去。"""
    bait = tmp_path / "bait_store.py"
    bait.write_text(
        "def init_schema(p): ...\n"
        "def _conn(p):\n"
        "    init_schema(p)\n"
        "def load_thing(p):\n"
        "    return _conn(p)\n"
        "def compute_thing(p):\n"
        "    return _conn(p)\n",
        encoding="utf-8")
    hits = guard_scan.reaches(bait, _READ_PREFIXES, "init_schema")
    assert len(hits) == 1 and "load_thing" in hits[0], hits


def test_the_readonly_connection_helper_still_exists():
    """正向:`db.py` 为这件事**专门写过**一个 `readonly_connection()`,它是修法本身。

    ⛔ 这条闸不许被理解成「那就把 `init_schema` 从读函数里删掉、让表不存在时炸」——
    正确修法是 `readonly_connection()` + 「表不存在 → 返回文档化的空态」。
    """
    from neckline import db as db_mod  # noqa: PLC0415

    assert hasattr(db_mod, "readonly_connection")
    src = guard_scan.code_without_docstrings(_DB_PY)
    assert "def readonly_connection" in src


def test_no_read_helper_triggers_a_schema_migration():
    """🔴 **回滚边界的机器判据**:读一次⛔ 不许把库迁移掉。

    判据 = AST 调用图闭包:名字以 `load_/latest_/list_/get_/read_/fetch_` 开头的函数,
    经**本文件内**的调用链走不到 `init_schema`。

    ⚠ 这条曾经挂着 `xfail(strict=True)`(§13.1-B13:43 个读 helper 会触发迁移,
    实测一次 `load_k9_report` 就把 v2.4.2 老库从 59 表建成 75 表)。**欠账已还清**
    —— 2026-08-21 修复波:F-B 换掉 `auction/explain/playbook/report/review/dedup/
    settings_store` 的 28 个,F-A 换掉 `facts/k9/scorecard/data.sw_industry` 的 15 个,
    合计 43 → **0**,故按 xfail 原文「修好之后请连同这个 xfail 一起删」把它删了。
    正确修法是 `readonly_tables()` + 「表不存在 → 返回文档化的空态」,
    ⛔ 不是「把 `init_schema` 从读函数里删掉、让表不存在时炸」。
    """
    hits = _read_helpers_reaching_init_schema()
    assert hits == [], (
        f"{len(hits)} 个读 helper 会触发 schema 迁移:\n" + "\n".join(hits))


def test_the_read_path_debt_is_exactly_as_large_as_registered():
    """欠账要有**数**,不能只有一句「还有一些」。

    ⚠ 这条是**账本**,不是闸:它锁的是「这个数没有在没人注意的时候变大」。
    数变了就来改这里 —— 让它成为一次自觉行为(⛔ 别顺手把断言改宽)。

    **账面从 43 变成 0**(2026-08-21 修复波,§13.1-B13 已还清)。当年那 43 =
    40 个直接调 `init_schema` 的 + 3 条隔了一层的:
      · `facts/industry.py:250  load_median_map  → load_day          → init_schema`
      · `report/store.py:226    load_report      → load_report_by_str → init_schema`
      · `settings_store.py:284  list_providers_public → list_providers → init_schema`
    ⚠ 那三条正是「只查直接调用的判据」会漏掉的形状 —— 而 `report.store.load_report`
    恰恰是复审用来实测「59 表 → 75 表」的那两个入口之一。
    ⚠ 钉在 0 之后它与上面那条闸重合,留着是为了让「这笔账**曾经**有多大」在
    发版门禁里还看得见 —— ⛔ 谁都不许把这个数往上调。
    """
    hits = _read_helpers_reaching_init_schema()
    assert len(hits) == 0, (
        f"读路径触发 DDL 的函数从 0 个变成了 {len(hits)} 个:\n" + "\n".join(hits))


# ══════════════════════════════════════════════════════════════════════════
# C. unit 拓扑:**零新增**(§9.3)
# ══════════════════════════════════════════════════════════════════════════

#: `deploy/` 下的 unit 文件 —— **精确集合**(⛔ 不是 `<=`:多一个当场红)。
_EXPECTED_UNITS: Tuple[str, ...] = (
    "neckline.service",
    "neckline-daily.service", "neckline-daily.timer",
    "neckline-scan.service", "neckline-basket.service", "neckline-report.service",
    "neckline-evening.target", "neckline-evening.timer",
)


def test_deploy_has_exactly_eight_units_and_zero_new_ones():
    """🔴 **§9.3 表最后一行:「新增 unit:**无**」**。

    两拍(9:26 核对表 + 10:00 结算)都跑在既有常驻 `neckline.service` 的
    `_morning_loop` 里 —— 多一个 unit 就多一个触发面和一条双跑路径,
    而「当日只跑一次」是记在防重台账里的,双触发会把「今天跑没跑过」变成一道
    要现场推理的题。
    """
    found = tuple(sorted(p.name for p in _DEPLOY.glob("*.service"))) + \
        tuple(sorted(p.name for p in _DEPLOY.glob("*.timer"))) + \
        tuple(sorted(p.name for p in _DEPLOY.glob("*.target")))
    assert sorted(found) == sorted(_EXPECTED_UNITS), (
        f"unit 拓扑变了:{sorted(found)}(§9.3:本版零新增 unit)")


def test_the_three_oneshots_carry_the_new_segment_order():
    """晚间链段序:`verify,scan,basket,review,report` → **`facts,k9,explain,playbook,report`**。

    ⚠ 三个 oneshot **保持原文件名不动**(只改 `ExecStart` 的 `--segments`)——
    避免又动一次 unit 拓扑。
    """
    expected = {
        "neckline-scan.service": "facts",
        "neckline-basket.service": "k9,explain,playbook",
        "neckline-report.service": "report",
    }
    seen: List[str] = []
    for name, segments in expected.items():
        text = (_DEPLOY / name).read_text(encoding="utf-8")
        m = re.search(r"^ExecStart=.*--segments (\S+)", text, re.M)
        assert m, f"{name} 的 ExecStart 里没有 --segments"
        assert m.group(1) == segments, f"{name} 的段名是 {m.group(1)},应为 {segments}"
        seen.extend(m.group(1).split(","))
        # K8 的老段名一个都不许残留。⚠ 按**剥注释后的代码行**判 —— 注释里讲清
        # 「原来是 `--segments review,report`」是必要留痕,把它算进命中会逼人删注释。
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        for dead in ("verify", "scan", "basket", "review"):
            assert f"--segments {dead}" not in code, f"{name} 的代码行里还有老段名 {dead}"
            assert f",{dead}," not in f",{m.group(1)},"
    # 五个新段**不重不漏**覆盖一次。
    assert sorted(seen) == ["explain", "facts", "k9", "playbook", "report"]


def test_the_strategy_segment_gets_the_params_package_path_explicitly():
    """🔴 **参数包⛔ 无默认路径**(§3.2 / 裁定 5):跑策略层那个 unit 必须显式传
    `--k9-params`。⚠ 那个路径下的文件**现在还不存在**(参数待标定)——
    于是那一段每天出「今天没跑成 · 参数未配置」,这是**设计行为**(§9.5)。"""
    text = (_DEPLOY / "neckline-basket.service").read_text(encoding="utf-8")
    m = re.search(r"^ExecStart=.*--k9-params (\S+)", text, re.M)
    assert m, "`neckline-basket.service` 没有显式传 `--k9-params`"
    assert m.group(1).endswith(".json")
    # ⛔ 老的 K8 参数开关不许残留。
    assert "--direction-pipeline-config" not in text


def test_every_unit_flag_and_segment_really_exists_in_the_evening_cli():
    """🔴 **unit 指向的东西真的存在吗** —— S1 之后就出现过 unit 指向已删脚本的先例。

    复审实测:把 `SEG_FACTS` 的值从 `"facts"` 改成 `"factpack"`(unit 不动)、或把
    `--k9-params` 从 argparse 里删掉,本文件原来 **13 条全绿** —— 它比的是 unit 文本
    与写死在本文件里的字面量,两边一起漂就看不出来。万幸 `test_weekend_report_
    schedule.py` 接住了这两条;**但 README 的「发版」一节与 §9.6 头部都把本文件说成
    「能在本地先跑一遍的机器判据」,操作者照着只跑这一个文件。** 判据搬过来一份。

    ⚠ 这条比的是 unit ↔ **真实 argparse / 真实段名常量**,⛔ 不比字面量。
    """
    from neckline.report.evening import CHAIN_SEGMENTS  # noqa: PLC0415

    known_segments = set(CHAIN_SEGMENTS)
    assert known_segments, "`CHAIN_SEGMENTS` 是空的?那这条判据就是空的"

    # `evening.py` 的 argparse 真的接受哪些开关(AST 读 `add_argument` 的字面量,
    # ⛔ 不 import 那个模块去构造 parser —— parser 建在 `main()` 里,取不到)。
    known_flags = {
        arg.value
        for node in ast.walk(ast.parse(
            (_ROOT / "scripts" / "evening.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        and arg.value.startswith("--")
    }
    assert "--segments" in known_flags, "取 argparse 的方式失效了(一个恒空的闸)"

    offenders: List[str] = []
    for path in sorted(_DEPLOY.glob("*.service")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if not line.startswith("ExecStart="):
                continue
            if "evening.py" not in line:
                continue
            for token in line.split():
                if token.startswith("--") and token not in known_flags:
                    offenders.append(f"{path.name}: `{token}` 不在 evening.py 的 argparse 里")
            m = re.search(r"--segments (\S+)", line)
            if m:
                for seg in m.group(1).split(","):
                    if seg not in known_segments:
                        offenders.append(
                            f"{path.name}: 段名 `{seg}` 不在 `CHAIN_SEGMENTS` 里")
    assert offenders == [], "unit 指向了不存在的东西:\n" + "\n".join(offenders)


def test_stop_when_unneeded_is_still_there_and_no_remain_after_exit():
    """🔴 **`StopWhenUnneeded=yes` ⛔ 绝对不许删**(删了 = 只跑第一晚,次晚起静默全哑);
    🔴 三段 service ⛔ **永远不许加** `RemainAfterExit=yes`。"""
    target = (_DEPLOY / "neckline-evening.target").read_text(encoding="utf-8")
    assert re.search(r"^StopWhenUnneeded=yes", target, re.M), (
        "`neckline-evening.target` 的 `StopWhenUnneeded=yes` 没了 —— 删了它,"
        "晚间链只跑第一晚,次晚起**静默全哑**(血泪见该文件头部)。")
    for name in ("neckline-scan.service", "neckline-basket.service", "neckline-report.service"):
        text = (_DEPLOY / name).read_text(encoding="utf-8")
        assert not re.search(r"^RemainAfterExit=", text, re.M), (
            f"{name} 加了 `RemainAfterExit` —— ⛔ 三段 oneshot 永远不许加")


def test_no_unit_mentions_the_two_morning_ticks():
    """两拍**不是** unit:`deploy/` 全部正文里 `checklist` / `settle` / `auction` 零命中。"""
    for p in sorted(_DEPLOY.glob("*.service")) + sorted(_DEPLOY.glob("*.timer")) + \
            sorted(_DEPLOY.glob("*.target")):
        code = "\n".join(ln for ln in p.read_text(encoding="utf-8").splitlines()
                         if not ln.lstrip().startswith("#"))
        for word in ("checklist", "settle", "auction"):
            assert word not in code, (
                f"{p.name} 提到了 `{word}` —— 两拍跑在既有常驻 `neckline.service` 里,"
                f"⛔ 不新增 unit(§5.7.3 / §9.3)")


def test_the_memory_caps_are_untouched_this_group():
    """⚠ **本组一个 `MemoryMax` 都没改**(§13.1-B5 等用户裁定)。

    实测在**开发机 macOS** 上做(策略层真实数据 RSS 峰值 736 MB vs
    `neckline-basket.service` 的 `MemoryMax=900M`,余量 18%);
    拿一个跨平台的数去改生产 cap 是在猜。⛔ 施工侧不自行改、不拆 unit。
    """
    caps = {
        "neckline.service": ("MemoryHigh=420M", "MemoryMax=600M"),
        "neckline-daily.service": ("MemoryMax=900M",),
        "neckline-scan.service": ("MemoryMax=1400M",),
        "neckline-basket.service": ("MemoryMax=900M",),
        "neckline-report.service": ("MemoryMax=1000M",),
    }
    for name, wanted in caps.items():
        text = (_DEPLOY / name).read_text(encoding="utf-8")
        for line in wanted:
            assert re.search(rf"^{re.escape(line)}$", text, re.M), (
                f"{name} 的 `{line}` 变了 —— 改 `MemoryMax` 要用户点头(§13.1-B5),"
                f"⛔ 施工侧不自行改。")


# ══════════════════════════════════════════════════════════════════════════
# D. 回滚锚点
# ══════════════════════════════════════════════════════════════════════════

def test_the_rollback_anchor_really_exists():
    """🔴 **回滚目标 = 已验证的 `v2.4.2` Build 9**(commit `ee12b9b`,§9.4)。

    ⛔ 一份指向取不到的 commit 的回滚方案等于没有回滚方案 —— 这条就是那句话的判据。
    """
    out = subprocess.run(["git", "cat-file", "-t", ROLLBACK_COMMIT],
                         cwd=_ROOT.parent, capture_output=True)
    assert out.returncode == 0 and out.stdout.decode().strip() == "commit", (
        f"回滚锚点 commit `{ROLLBACK_COMMIT}` 取不到")
    # 那一版的服务端版本号确实是 v2.4.2(⛔ 别把锚点指到别的版本上)。
    app_py = subprocess.run(["git", "show", f"{ROLLBACK_COMMIT}:Backend/neckline/api/app.py"],
                            cwd=_ROOT.parent, capture_output=True, check=True).stdout.decode()
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', app_py, re.M)
    assert m and m.group(1) == ROLLBACK_VERSION, (
        f"`{ROLLBACK_COMMIT}` 的 VERSION 是 {m and m.group(1)},不是 {ROLLBACK_VERSION}")


# ══════════════════════════════════════════════════════════════════════════
# E. 上线后的状态:22 项待标定参数一个默认值都没有
# ══════════════════════════════════════════════════════════════════════════

def test_the_params_package_is_not_in_the_repo():
    """🔴 **`config/k9-params.json` ⛔ 不在仓库里**(裁定 5:参数标定归 whynotme,
    用户确认后**由用户放入**)。

    ⚠ 上线后它仍然不在 → 清单段每天出「今天没跑成 · 参数未配置」——
    那是**设计行为**,⛔ 不是故障(§9.5)。
    """
    assert not (_ROOT / "config" / "k9-params.json").exists(), (
        "仓库里出现了 `config/k9-params.json` —— ⛔ 参数包由用户放入,不进版本库")
    example = _ROOT / "config" / "k9-params.example.json"
    assert example.exists(), "示例配置应当在(它是用户照着填的模板)"
    text = example.read_text(encoding="utf-8")
    assert "__TO_BE_CALIBRATED__" in text


#: 🔴 示例配置里**唯一**允许的真值,连同出处。K9 §二 给定项(白酒 L2),
#: ⛔ 不是待标定参数 —— §14 S5 已登记为唯一例外。
_EXAMPLE_LEAF_ALLOW: Dict[str, Any] = {
    "industry.excludedL2Codes[0]": "801125.SI",
}


def _json_leaves(node: Any, path: str = "") -> List[Tuple[str, Any]]:
    """把一份 JSON 摊平成 `(路径, 叶子值)` 清单 —— **数组里的元素也算叶子**。"""
    if isinstance(node, dict):
        out: List[Tuple[str, Any]] = []
        for key, value in node.items():
            out.extend(_json_leaves(value, f"{path}.{key}" if path else str(key)))
        return out
    if isinstance(node, list):
        out = []
        for i, value in enumerate(node):
            out.extend(_json_leaves(value, f"{path}[{i}]"))
        return out
    return [(path, node)]


def _uncalibrated_offenders(doc: Any) -> List[str]:
    from neckline.k9 import params as params_mod  # noqa: PLC0415

    offenders: List[str] = []
    for path, value in _json_leaves(doc):
        if value == params_mod.TO_BE_CALIBRATED:
            continue
        if path in _EXAMPLE_LEAF_ALLOW and value == _EXAMPLE_LEAF_ALLOW[path]:
            continue
        offenders.append(f"{path} = {value!r}")
    return offenders


def test_the_example_leaf_detector_actually_detects():
    r"""扫描器自检 —— **数组里的数**必须看得见。

    复审 CE14:往示例配置里加 `"__probe": [0.4, 0.3, 0.3]`,原来那条正则
    (`re.findall(r":\s*(-?\d+(?:\.\d+)?)\s*[,}\n]")`)**照绿** —— 它只认
    `"k": 0.3` 这一种形状。而 §8 待标定表里恰好有权重类(三成分权重、形态内合成权重
    4 组),天然可能写成数组。
    """
    from neckline.k9 import params as params_mod  # noqa: PLC0415

    tbc = params_mod.TO_BE_CALIBRATED
    assert _uncalibrated_offenders({"a": tbc}) == []
    assert _uncalibrated_offenders({"__probe": [0.4, 0.3, 0.3]}) == [
        "__probe[0] = 0.4", "__probe[1] = 0.3", "__probe[2] = 0.3"]
    assert _uncalibrated_offenders({"deep": {"er": {"k": 10}}}) == ["deep.er.k = 10"]
    assert _uncalibrated_offenders({"flag": False}) == ["flag = False"]


def test_the_example_config_has_no_real_value_at_any_leaf():
    """🔴 **裁定 5:⛔ 一个默认值都没有**。

    判据是 **JSON 语义**而不是正则:`json.load` 之后递归到**每一个叶子**,
    断言它要么是 `__TO_BE_CALIBRATED__`、要么在一份显式白名单里。
    ⚠ 一份「大部分位置是占位符」的示例配置,与一份给了默认值的示例配置,
    对下一个人来说是同一件东西。
    """
    import json  # noqa: PLC0415

    example = _ROOT / "config" / "k9-params.example.json"
    doc = json.loads(example.read_text(encoding="utf-8"))
    offenders = _uncalibrated_offenders(doc)
    assert offenders == [], (
        "示例配置里出现了真值 —— ⛔ 那等于给了一组默认值:\n" + "\n".join(offenders))
    # 正向:白名单里那一条**确实还在**(⛔ 白名单不许留一条指向空气的例外)。
    leaves = dict(_json_leaves(doc))
    for path, value in _EXAMPLE_LEAF_ALLOW.items():
        assert leaves.get(path) == value, f"白名单条目 {path} 已不在示例配置里"
    assert len(leaves) > 40, f"示例配置只摊出 {len(leaves)} 个叶子 —— 递归怕是断了"


def test_every_params_field_still_has_no_default():
    """**结构性保证**:少一个值就**构造不出对象**(⛔ 不是靠 if 判断)。

    ⚠ 这条与 `test_v250_s5_params_guard.py` 重叠是**有意的**:发版门禁要在**发版这一刻**
    再确认一次「一个待标定参数都没被填上默认值」—— 那是本版最容易被"为了让报告好看"
    破掉的一条(§9.5 逐字)。
    """
    import dataclasses  # noqa: PLC0415

    from neckline.k9 import params as params_mod  # noqa: PLC0415

    checked = 0
    for cls in vars(params_mod).values():
        if not (isinstance(cls, type) and dataclasses.is_dataclass(cls)):
            continue
        for f in dataclasses.fields(cls):
            assert f.default is dataclasses.MISSING, f"{cls.__name__}.{f.name} 有默认值"
            assert f.default_factory is dataclasses.MISSING, (
                f"{cls.__name__}.{f.name} 有 default_factory")
            checked += 1
    assert checked >= 40, f"只检查到 {checked} 个字段 —— 扫描域怕是错了"
