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

def test_v250_adds_no_new_column_migration_and_no_new_alter_or_drop():
    """🔴 **§9.2**:「本版所有 schema 变更是**纯新增**(新表 + 新 parquet 目录),
    ⛔ 不 ALTER、不 DROP、不 UPDATE 任何 K8 表」。

    ⚠ `db.py` 里**确实**有 ALTER / DROP —— 它们是 V2.2 / V2.4.2 就在的**幂等迁移**
    (`_relax_holding_eod_check_notnull` / `_migrate_selection_generation_tables`),
    生产上早就跑过了。本条锁的是「**本版一条都没新增**」:与基线逐条比对,
    ⛔ 不是"扫到 ALTER 就红"(那会逼人删掉已经在生产跑过的迁移)。
    """
    old_src, new_src = _baseline_db_py(), _DB_PY.read_text(encoding="utf-8")

    def ddl_statements(src: str) -> List[str]:
        out = []
        for line in src.splitlines():
            code = line.split("--", 1)[0].split("#", 1)[0]
            for kw in ("ALTER TABLE", "DROP TABLE", "DROP INDEX", "UPDATE "):
                if kw in code:
                    out.append(re.sub(r"\s+", " ", code.strip()))
                    break
        return sorted(out)

    added = sorted(set(ddl_statements(new_src)) - set(ddl_statements(old_src)))
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
    # 示例里**所有数值位**一律是待标定占位符,⛔ 没有任何真数字。
    assert "__TO_BE_CALIBRATED__" in text
    numbers = re.findall(r":\s*(-?\d+(?:\.\d+)?)\s*[,}\n]", text)
    assert not numbers, f"示例配置里出现了真数字:{numbers} —— ⛔ 那等于给了一组默认值"


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
