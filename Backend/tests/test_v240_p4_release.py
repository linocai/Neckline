"""V2.4.0 **P4 发布治理**验收集(plan §五 V2.4.0 P4.1 / P4.4 / P4.5 / P4.6-4 / P4.7)。

五组:
  **A. P4.1 macOS 测试宿主守门**:`App/project.yml` 与生成后的 `pbxproj` 双向断言,
     且 **iOS 那条自动值一字未动**(修 macOS 不许连坐 iOS)。
  **B. P4.4 版本治理**:三处同为 `2.4.0`;🔴 **补上既有守门看不见的那两处**
     —— `project.yml` 顶层 base(守门只比 app target)与 pbxproj **project 级**块
     (`CLAUDE.md` 记着它曾静默停在 `2.0.0` 而守门一直是绿的)。
  **C. P4.6 第 4 步「在生产副本上演练 schema migration」+ 最终 DoD「v2.3.3 历史记录、
     冻结卡片和复盘结果完全不变」**:拿 **`v2.3.3` tag 里那份 `_SCHEMA`** 造一个真正的
     老库,塞进历史行,跑今天的 `init_schema`,断言历史行**逐列逐位不变**、新列一律
     `NULL`。⚠ 判据是「老库上真跑一遍迁移」,⛔ 不是「读一眼 `_COLUMN_MIGRATIONS`
     说它看起来是可空的」。
  **D. 工作区卫生**:`git diff --check`(空白错误)。
  **E. V2.4.2 构建号与 iOS 图标缓存键**:客户端源、生成工程与服务版本一起锁定。
"""

from __future__ import annotations

import ast
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

from neckline.db import init_schema

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_YML = _REPO_ROOT.parent / "App" / "project.yml"
_PBXPROJ = _REPO_ROOT.parent / "App" / "Neckline.xcodeproj" / "project.pbxproj"
_ASSET_CATALOG = _REPO_ROOT.parent / "App" / "Neckline" / "Resources" / "Assets.xcassets"
_APP_PY = _REPO_ROOT / "neckline" / "api" / "app.py"

_MACOS_TEST_HOST_KEY = "TEST_HOST[sdk=macosx*]"
_MACOS_TEST_HOST = "$(BUILT_PRODUCTS_DIR)/Neckline.app/Contents/MacOS/Neckline"
_IOS_TEST_HOST = "$(BUILT_PRODUCTS_DIR)/Neckline.app/Neckline"

def _expected_marketing_version() -> str:
    """🔵 **复审 🔵-10:从 `app.py::VERSION` 反推,⛔ 不在守门里写死版号**。

    写死的后果不是"多改一行",而是**每次升版都得动守门测试** —— 而"为变绿改守门"
    正是 P4.5 明令禁止的习惯;久了没人分得清哪次是升版、哪次是把守门放宽了。
    本条只留**一致性**断言:客户端两处 = pbxproj 四处 = `app.py::VERSION` 去掉 `v`。
    """
    from neckline.api.app import VERSION

    assert VERSION.startswith("v"), f"`app.py::VERSION` 形状变了:{VERSION!r}"
    return VERSION[1:]


_EXPECTED_VERSION = _expected_marketing_version()
_EXPECTED_RC_BUILD = "7"
_EXPECTED_PRIMARY_ICON = "AppIconV242"


# ══════════════════════════════════════════════════════════════════════════
# A. P4.1 macOS 测试宿主
# ══════════════════════════════════════════════════════════════════════════

def test_project_yml_declares_macos_test_host_override():
    """🔴 **必须从源文件 `project.yml` 修**(§3.14-H):手改 pbxproj 会被下一次
    `xcodegen generate` 原样冲掉。"""
    data = yaml.safe_load(_PROJECT_YML.read_text(encoding="utf-8"))
    base = data["targets"]["NecklineTests"]["settings"]["base"]
    assert base.get(_MACOS_TEST_HOST_KEY) == _MACOS_TEST_HOST, (
        "NecklineTests 缺 macOS 测试宿主覆盖 —— macOS `xcodebuild test` 会退回报 "
        "`Could not find test host`(P4.1 的病灶)"
    )


def test_pbxproj_carries_macos_test_host_in_both_configs():
    """生成后的 pbxproj 里 Debug + Release 各一处(= `xcodegen generate` 真跑过)。"""
    text = _PBXPROJ.read_text(encoding="utf-8")
    hits = re.findall(r'"TEST_HOST\[sdk=macosx\*\]" = "([^"]+)";', text)
    assert hits == [_MACOS_TEST_HOST, _MACOS_TEST_HOST], (
        f"pbxproj 里的 macOS TEST_HOST 覆盖不是预期的两处:{hits} —— "
        "改完 project.yml 忘了跑 `xcodegen generate`?"
    )


def test_ios_test_host_is_untouched():
    """**反向断言**:iOS 那条自动值一字没动(iOS bundle 布局本来就是对的)。"""
    text = _PBXPROJ.read_text(encoding="utf-8")
    ios = re.findall(r'\n\s+TEST_HOST = "([^"]+)";', text)
    assert ios == [_IOS_TEST_HOST, _IOS_TEST_HOST], f"iOS TEST_HOST 被改动了:{ios}"


# ══════════════════════════════════════════════════════════════════════════
# B. P4.4 版本治理(补既有守门的两处盲点)
# ══════════════════════════════════════════════════════════════════════════

def test_server_version_is_v240():
    m = re.search(r'^VERSION\s*=\s*"(v[\d.]+)"', _APP_PY.read_text(encoding="utf-8"), re.MULTILINE)
    assert m and m.group(1) == f"v{_EXPECTED_VERSION}"


def test_project_yml_has_both_marketing_versions_and_they_agree():
    """🔴 `project.yml` 里 `MARKETING_VERSION` **刻意重复两处**(顶层 base + app target),
    而 `test_client_version_governance.py` **只比 app target** —— 顶层那处漂了没人看得见。
    本条把两处一起钉死。"""
    data = yaml.safe_load(_PROJECT_YML.read_text(encoding="utf-8"))
    top = str(data["settings"]["base"]["MARKETING_VERSION"])
    target = str(data["targets"]["Neckline"]["settings"]["base"]["MARKETING_VERSION"])
    assert top == target == _EXPECTED_VERSION, (
        f"project.yml 两处 MARKETING_VERSION 不一致或不是 {_EXPECTED_VERSION}:"
        f"顶层={top} / app target={target}"
    )


def test_every_marketing_version_in_pbxproj_is_the_same():
    """🔴 **含 project 级块**:`CLAUDE.md` 记着 project 级曾静默停在 `2.0.0` 而 app target
    已是 `2.2.0` —— 既有守门刻意排除了 project 级块,**那处漂移一直是绿的**。
    `xcodegen generate` 会顺手修好它,本条负责让"忘了重跑生成器"当场红。"""
    versions = re.findall(r"MARKETING_VERSION = ([\d.]+);", _PBXPROJ.read_text(encoding="utf-8"))
    assert set(versions) == {_EXPECTED_VERSION}, (
        f"pbxproj 里出现了不止一种 MARKETING_VERSION:{sorted(set(versions))}"
    )
    # 🔵 **复审 🔵-11:只断集合会漏掉「整处丢失」** —— 某次生成把 project 级那处
    # 删掉,`set()` 仍是单元素、断言照绿,而那**正是 P4.4 要堵的盲点换了个形状**。
    # 四处 = project 级 Debug/Release + app target Debug/Release。
    assert len(versions) == 4, (
        f"pbxproj 的 MARKETING_VERSION 应恰好 4 处(project 级 2 + app target 2),"
        f"实得 {len(versions)} 处:{versions} —— 少一处 = 有一块没被版本治理覆盖")


def test_v242_build_number_is_synced_into_the_generated_project():
    """V2.4.2 后端篮子链快修为 Build 7；源文件和生成工程不能各自漂移。"""
    data = yaml.safe_load(_PROJECT_YML.read_text(encoding="utf-8"))
    source_build = str(data["settings"]["base"]["CURRENT_PROJECT_VERSION"])
    generated_builds = re.findall(
        r"CURRENT_PROJECT_VERSION = ([0-9]+);", _PBXPROJ.read_text(encoding="utf-8"))
    assert source_build == _EXPECTED_RC_BUILD
    assert generated_builds == [_EXPECTED_RC_BUILD, _EXPECTED_RC_BUILD], (
        "pbxproj 的 Debug/Release 构建号应由 project.yml 生成并同为 Build 7:"
        f"{generated_builds}")


def test_v242_ios_primary_icon_uses_the_cache_busted_asset_name():
    """覆盖安装后通知栏不能继续命中旧的 `AppIcon` 系统缓存键。"""
    data = yaml.safe_load(_PROJECT_YML.read_text(encoding="utf-8"))
    target = data["targets"]["Neckline"]["settings"]["base"]
    assert target["ASSETCATALOG_COMPILER_APPICON_NAME"] == _EXPECTED_PRIMARY_ICON
    icon_set = _ASSET_CATALOG / f"{_EXPECTED_PRIMARY_ICON}.appiconset"
    assert icon_set.is_dir()
    contents = yaml.safe_load((icon_set / "Contents.json").read_text(encoding="utf-8"))
    ios_icons = [
        item for item in contents["images"]
        if item.get("platform") == "ios" and item.get("size") == "1024x1024"
    ]
    assert ios_icons == [{
        "idiom": "universal", "platform": "ios", "size": "1024x1024",
        "filename": "icon_1024.png",
    }]
    generated = _PBXPROJ.read_text(encoding="utf-8")
    assert generated.count(
        f"ASSETCATALOG_COMPILER_APPICON_NAME = {_EXPECTED_PRIMARY_ICON};"
    ) == 2


# ══════════════════════════════════════════════════════════════════════════
# C. 老库迁移演练:历史行逐位不变
# ══════════════════════════════════════════════════════════════════════════

_NEW_COLUMNS_V240: Tuple[Tuple[str, str], ...] = (
    ("auction_reports", "quote_quality_json"),
    ("auction_verdicts", "critical_data_quality"),
    ("auction_verdicts", "context_data_quality"),
    ("auction_verdicts", "quality_detail_json"),
    ("basket_stage_handoff", "seed_count"),
    ("basket_stage_handoff", "seed_summary"),
    ("selection_pack_activation_log", "batch_id"),
    # 🔴 2026-08-12 用户裁定 ①:竞价独立观察池的账 + 观察范围自述(**第 8 个可空列**)。
    ("auction_reports", "observation_json"),
)
# DoD 逐字点名的三类历史件 + 上面那些被加了列的表 —— 一起塞行、一起验。
_HISTORY_TABLES: Tuple[str, ...] = (
    "basket_cards", "reviews", "baskets", "auction_reports", "auction_verdicts",
    "basket_stage_handoff", "selection_pack_activation_log", "reports",
)


def _v233_schema_and_migrations() -> Tuple[str, List[Tuple[str, str, str]]]:
    """从 `v2.3.3` tag 的 `neckline/db.py` 里**按 AST 取出**建表脚本与迁移列清单。

    ⛔ 不 exec 那一版模块(那会引入一整套旧 import);只取两个模块级常量的字面量。"""
    try:
        src = subprocess.run(
            ["git", "show", "v2.3.3:neckline/db.py"],
            cwd=_REPO_ROOT, capture_output=True, check=True,
        ).stdout.decode("utf-8")
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover
        pytest.skip("取不到 v2.3.3 tag 的 neckline/db.py(浅克隆/无 git)")
    tree = ast.parse(src)
    schema: str = ""
    migrations: List[Tuple[str, str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name == "_SCHEMA":
            schema = ast.literal_eval(node.value)
        elif name == "_COLUMN_MIGRATIONS":
            migrations = [tuple(x) for x in ast.literal_eval(node.value)]
    assert schema and migrations, "v2.3.3 的 db.py 里没取到 _SCHEMA / _COLUMN_MIGRATIONS"
    return schema, migrations


def _dummy_row(conn: sqlite3.Connection, table: str) -> Dict[str, Any]:
    """给一张表造一行「历史数据」:主键/自增列交给 SQLite,其余 NOT NULL 列按声明
    类型塞一个确定性的值(⛔ 不用随机,失败要能复现)。"""
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
            values[name] = f"v233-{table}-{name}"
    return values


def test_v233_history_rows_survive_the_v240_migration_bit_for_bit(tmp_path: Path):
    """🔴 **最终 DoD**:「v2.3.3 历史记录、冻结卡片和复盘结果完全不变」。

    做法 = **P4.6 第 4 步的演练**:用 `v2.3.3` 那份 `_SCHEMA` 造一个真老库 → 塞历史行 →
    跑今天的 `init_schema`(= 生产升级时会发生的事)→ 逐表逐行逐列对拍。"""
    schema, old_migrations = _v233_schema_and_migrations()
    db = tmp_path / "v233_like.db"

    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(schema)
        for table, column, ddl in old_migrations:                 # 老库的历史迁移列
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        # 反向自检:v2.4.0 的新列此刻**必须还不存在**,否则这份"老库"是假的。
        for table, column in _NEW_COLUMNS_V240:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert column not in cols, f"{table}.{column} 在 v2.3.3 老库里就有了?这份老库是假的"

        before: Dict[str, List[tuple]] = {}
        cols_before: Dict[str, List[str]] = {}
        for table in _HISTORY_TABLES:
            row = _dummy_row(conn, table)
            conn.execute(
                f"INSERT INTO {table} ({', '.join(row)}) "
                f"VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
            cols_before[table] = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            before[table] = conn.execute(
                f"SELECT {', '.join(cols_before[table])} FROM {table} ORDER BY rowid").fetchall()
            assert before[table], f"{table} 没塞进历史行,这条测试会假绿"
        conn.commit()
    finally:
        conn.close()

    init_schema(db)                                               # ← v2.4.0 迁移真跑一遍

    conn = sqlite3.connect(str(db))
    try:
        for table in _HISTORY_TABLES:
            after = conn.execute(
                f"SELECT {', '.join(cols_before[table])} FROM {table} ORDER BY rowid").fetchall()
            assert after == before[table], f"{table} 的历史行被迁移改动了(DoD:历史只读)"
        for table, column in _NEW_COLUMNS_V240:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert column in cols, f"迁移没给 {table} 补上 {column}"
            if table in _HISTORY_TABLES:
                nulls = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL").fetchone()[0]
                assert nulls == 0, (
                    f"{table}.{column} 给老行填了值 —— 老行必须是 NULL"
                    "(NULL = 「这一版还没有这个概念」,⛔ 不是「正常」)")
    finally:
        conn.close()


def test_v240_new_columns_are_all_nullable_without_default():
    """**增量兼容列**(P4.7:「新增 SQLite 列为增量兼容列,不需要通过删列回滚」)——
    可空、无默认,回滚时留着它们不影响 v2.3.3 代码。"""
    from neckline import db as db_module

    declared = {(t, c): ddl for t, c, ddl in db_module._COLUMN_MIGRATIONS}
    for table, column in _NEW_COLUMNS_V240:
        ddl = declared.get((table, column))
        assert ddl is not None, f"{table}.{column} 没登记进 _COLUMN_MIGRATIONS"
        assert "NOT NULL" not in ddl.upper() and "DEFAULT" not in ddl.upper(), (
            f"{table}.{column} 的 DDL 带了 NOT NULL / DEFAULT:{ddl!r} —— "
            "老行会被填上一个不是它当时事实的值")


# ══════════════════════════════════════════════════════════════════════════
# D. 工作区卫生
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("extra", [[], ["--cached"]])
def test_git_diff_check_reports_no_whitespace_errors(extra):
    """P4.5 验收集里那条 `git diff --check`(行尾空白 / 冲突标记)。

    🔵 **复审 🔵-12:补 `--cached`** —— 裸 `git diff --check` 只看**工作区**,
    `git add` 过之后那份改动就从它眼皮底下消失了(而 commit 前的最后一刻,
    改动恰恰都在暂存区)。两种都跑一遍。"""
    try:
        proc = subprocess.run(["git", "diff", "--check", *extra], cwd=_REPO_ROOT,
                              capture_output=True, text=True)
    except FileNotFoundError:  # pragma: no cover
        pytest.skip("无 git")
    assert proc.returncode == 0, (
        f"git diff --check {' '.join(extra)} 有问题:\n{proc.stdout}")


def test_release_scripts_exist_and_are_top_level():
    """P4.2 / P4.3 的两个新脚本是**现役脚本**,放 `scripts/` 顶层(⛔ 不进 `oneoff/`)。"""
    for name in ("bootstrap_dev_db.py", "activate_pack_set.py"):
        assert (_REPO_ROOT / "scripts" / name).exists()
        assert not (_REPO_ROOT / "scripts" / "oneoff" / name).exists()
