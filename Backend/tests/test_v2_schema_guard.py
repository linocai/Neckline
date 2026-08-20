"""V2-① 表与共享信息层地基:三律(冻结 / 追加 / 不回写)+ 幂等 + parquet 声明的机器
判据(plan §五 V2-①「三律守门单测(缺一不可)」五条,逐条对应本文件的五个 section)。

背景:本块只建表(20 张新表一次到位:18 张 SQLite + 2 张 parquet),读写业务逻辑留给
后续各块(baskets/basket_cards 的写入在 ⑦,basket_verification 在 ⑧,……)。因此下面
「不回写」一节不调用任何业务模块(它们还不存在)——直接用裸 SQL 模拟"下游表追加一行"
这件事本身,断言它不会牵动上游冻结表,这正是本块阶段该验的东西(schema 与三律本身,
不是尚未出生的业务代码)。

**两种扫描技术,刻意不同**(与 `test_db_isolation_guardrail.py` 同一条纪律):
- 「冻结」一节按 plan 原文用**纯文本 grep**——检查的是"这四个字面短语绝不出现在
  `neckline/` 任何源码里",足够且直接。
- 「追加」一节用 **AST 扫描**:只认「真的调用了 `execute`/`executemany`/`executescript`
  且第一个参数的字符串字面量里含禁止子串」,不误伤 docstring/注释里提到这些表名的
  散文引用(纯文本 grep 会被"三律"讲解性文字本身命中,自我打脸)。
"""

from __future__ import annotations

import ast
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple

import polars as pl
import pytest

from neckline.data import market_data as md
from neckline.db import connection, init_schema

_ROOT = Path(__file__).resolve().parent.parent
_NECKLINE_DIR = _ROOT / "neckline"
# 扫描域 = `neckline/` + `scripts/`(契约线审计 🟡 Y1 第 3 洞,2026-08-03 扩):以前只罩
# `neckline/`,而 `scripts/smoke_basket_verify.py` 里就真长出过一条打冻结表的删除语句
# ——「扫描域外」不是「合法」,冒烟脚本跑的是同一套表、犯的是同一个错。⚠ 没有豁免清单:
# 真需要破例时**先来这里加名单并写清理由**,不要靠"它不在扫描域里"这种沉默默许。
_SCRIPTS_DIR = _ROOT / "scripts"
_NECKLINE_PY_FILES = sorted(_NECKLINE_DIR.rglob("*.py"))
_SCANNED_PY_FILES = sorted(_NECKLINE_DIR.rglob("*.py")) + sorted(_SCRIPTS_DIR.rglob("*.py"))


def test_dirty_legacy_db_does_not_brick_init_schema(tmp_path, caplog):
    """契约线审计 🔵 B3 / 🟡 Y7 的**安全边界**:唯一索引的职责是「防止新的脏数据」,
    不是「让一个已经脏了的库开不了机」。

    `init_schema` 被所有入口调用(API / 哨兵 / 16:35 报告 / CLI),若一条 `CREATE UNIQUE
    INDEX` 在老库上抛 IntegrityError 且没人接,整个产品直接开不了机 —— 而且只给一句
    "UNIQUE constraint failed"。⚠ 这也是这类索引**不能写进 `_SCHEMA`** 的原因:
    `executescript` 一把梭,中途抛异常会连带中断它后面的所有建表语句。"""
    import logging

    db_path = tmp_path / "n.db"
    init_schema(db_path)
    with connection(db_path) as conn:
        # V2.2-① 起唯一现役索引 = per-line 版(`idx_selection_packs_single_active_per_line`);
        # 两行同为 DEFAULT 'LEGACY' 线 → 仍构成同线冲突,场景语义不变。
        conn.execute("DROP INDEX IF EXISTS idx_selection_packs_single_active_per_line")
        for v in ("a", "b"):        # 造两行现役(模拟索引加入之前的手工 SQL 遗留)
            conn.execute(
                "INSERT INTO selection_packs (pack_version, name, engine_api_version, "
                "manifest_json, config_json, is_active, created_at) VALUES (?,?,1,'{}','{}',1,?)",
                (v, v, _now()),
            )

    with caplog.at_level(logging.WARNING):
        init_schema(db_path)        # ⛔ 不许抛
    assert any("唯一索引建不上" in rec.message for rec in caplog.records), "得吵,不许静默跳过"

    with connection(db_path) as conn:
        # 脏行原样保留(⛔ 开机脚本无权替用户"清理"数据),其余表照常建好
        assert conn.execute(
            "SELECT COUNT(*) FROM selection_packs WHERE is_active=1").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 40
        conn.execute("UPDATE selection_packs SET is_active=0 WHERE pack_version='a'")
    init_schema(db_path)            # 人工清理后重跑 → 索引补上
    with connection(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
            "AND name='idx_selection_packs_single_active_per_line'").fetchone()[0] == 1
        # 旧的全表口径索引名必须**不存在**(DROP → 重建为 per-line 是 V2.2-① 的
        # 单向迁移;旧索引若还魂,激活第二条线会被它无差别拦死)。
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
            "AND name='idx_selection_packs_single_active'").fetchone()[0] == 0


def test_scan_domain_covers_both_package_and_scripts():
    """扫描范围本身要被看住(同 `test_eighteen_new_tables_is_the_declared_count` 精神):
    删掉 `scripts/` 那一半、或把某个目录悄悄挪走,这条会红。"""
    rel = {str(p.relative_to(_ROOT)) for p in _SCANNED_PY_FILES}
    assert any(p.startswith("neckline/") for p in rel)
    assert any(p.startswith("scripts/") for p in rel)
    assert any(p.startswith("scripts/oneoff/") for p in rel), "oneoff 留档脚本同样在扫描域内"

# 🔴 V2.5.0 S1:这 18 张表的**应用层写路径已全部删除**(K8 退役),但表本身按裁定 6
# **保留、只读、不迁移、不回填**。本清单因此从「新表验收单」变成「只读留档验收单」——
# `init_schema()` 在空库上仍必须把它们建出来,少一张就说明有人顺手 DROP 了。

_NEW_SQLITE_TABLES = (
    "baskets",
    "basket_members",
    "basket_cards",
    "basket_verification",
    "basket_review_daily",
    "tier_history",
    "corr_matrix_daily",
    "limit_cluster_daily",
    "leader_structure_daily",
    "user_actions",
    "entry_snapshots",
    "position_plans",
    "custom_alerts",
    "profile_preference",
    "profile_capability",
    "selection_packs",
    "selection_pack_activation_log",
    "llm_providers",
)


def _now() -> str:
    return "2026-08-02T00:00:00+00:00"


def test_eighteen_new_tables_is_the_declared_count():
    """防止上面这份清单本身漂移(增删表时必须顺手改这里,同 `test_scan_actually_
    covers_the_three_known_guardrail_files` 的"扫描范围本身要被看住"精神)。"""
    assert len(_NEW_SQLITE_TABLES) == 18


def test_all_v2_tables_created_on_empty_db(tmp_path):
    db_path = tmp_path / "n.db"
    init_schema(db_path)
    with connection(db_path) as conn:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in _NEW_SQLITE_TABLES if t not in existing]
    assert not missing, f"缺表:{missing}"


def test_app_settings_gains_llm_routing_columns(tmp_path):
    db_path = tmp_path / "n.db"
    init_schema(db_path)
    with connection(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(app_settings)")}
    assert {"llm_default_provider", "llm_task_routes"} <= cols


# ══════════════════════════════════════════════════════════════════════════
# 1. 冻结(basket_cards / entry_snapshots)
# ══════════════════════════════════════════════════════════════════════════

def test_basket_cards_duplicate_key_raises_integrity_error(tmp_path):
    db_path = tmp_path / "n.db"
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier, "
            "pack_version, engine_api_version, charter_version, created_at) "
            "VALUES ('20260731','deadbeef','示例篮子','示例驱动','theme',1,'K4-pack-v1',1,'v1.3.3',?)",
            (now,),
        )
        basket_id = conn.execute("SELECT id FROM baskets WHERE basket_key='deadbeef'").fetchone()[0]
        conn.execute(
            "INSERT INTO basket_cards (basket_id, version, card_json, created_at) VALUES (?,1,?,?)",
            (basket_id, "{}", now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO basket_cards (basket_id, version, card_json, created_at) VALUES (?,1,?,?)",
                (basket_id, "{}", now),
            )


def test_entry_snapshots_duplicate_key_raises_integrity_error(tmp_path):
    db_path = tmp_path / "n.db"
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, snapshot_json, created_at) "
            "VALUES (1, '600001.SH', '20260731', '{}', ?)",
            (now,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, snapshot_json, created_at) "
                "VALUES (1, '600001.SH', '20260801', '{}', ?)",
                (now,),
            )


# plan 原文列了三个短语(`UPDATE basket_cards` / `UPDATE entry_snapshots` /
# `DELETE FROM basket_cards`);这里补上第四个对称短语 `DELETE FROM entry_snapshots`
# ——entry_snapshots 与 basket_cards 同享"冻结"三律,没有理由只守一半。
#
# **写法变体成套补齐(契约线审计 🟡 Y1 第 1 洞,2026-08-03)**:原来只有 UPDATE/DELETE
# 四条纯短语,而**绕开 UNIQUE 冻结的第一写法根本不是它们** —— `INSERT OR REPLACE INTO
# basket_cards`(= 先删后插)一个字都不命中,`REPLACE INTO` 同理。仓里现役
# `INSERT OR REPLACE` 手法有十余处合法用途(scan/stage/retreat 等),抄错一个表名
# 守门全程沉默。故对两张冻结表把「任何会覆盖既有行的写法」一并列进来。
# ⚠ 大小写:比对前统一 `upper()`,不再只认全大写字面量。
# ⚠ **V2.2-④ 扩容**:`selection_clock` 是「**结案**」件 —— `INSERT OR IGNORE` +
# `basket_id` UNIQUE,结了就是结了(plan ④-A)。它与 `basket_review_daily` 的
# 「每日复盘可覆盖」**刻意不同**,⛔ 别把后者也塞进这份清单。
_FROZEN_TABLES = ("basket_cards", "entry_snapshots", "selection_clock")
_FROZEN_FORBIDDEN_TEXT = tuple(
    f"{verb} {tbl}" if verb == "UPDATE" else f"{verb} FROM {tbl}"
    for tbl in _FROZEN_TABLES for verb in ("UPDATE", "DELETE")
) + tuple(
    f"{verb} {tbl}"
    for tbl in _FROZEN_TABLES
    for verb in ("INSERT OR REPLACE INTO", "REPLACE INTO", "INSERT OR ABORT INTO",
                 "INSERT OR FAIL INTO", "INSERT OR ROLLBACK INTO")
)


def test_no_forbidden_sql_text_against_frozen_tables():
    """⚠ **纯文本 grep(刻意)**:连注释 / docstring 里的这些字面短语也一并拦。
    「钝但强」是选定的取向 —— 它能抓到 AST 字面量提取抓不到的写法(如 SQL 存在模块级
    常量里再 `execute(_SQL)`),代价是讲解这些写法时得绕开字面量本身。"""
    hits: List[Tuple[str, str]] = []
    for path in _SCANNED_PY_FILES:
        text = path.read_text(encoding="utf-8").upper()
        for forbidden in _FROZEN_FORBIDDEN_TEXT:
            if forbidden.upper() in text:
                hits.append((str(path.relative_to(_ROOT)), forbidden))
    assert not hits, f"冻结表出现禁止字样(neckline/ 与 scripts/ 全域不许出现):{hits}"


def test_frozen_guard_actually_catches_the_or_replace_variant(tmp_path):
    """守门本身可证伪(🟡 Y1 的病根是"看起来在守、其实漏"):造一个含
    `INSERT OR REPLACE` 的假源文件,断言上面那套短语集**确实**命中它。"""
    fake = tmp_path / "sneaky.py"
    fake.write_text(
        'conn.execute("insert or replace into basket_cards (basket_id) values (1)")\n',
        encoding="utf-8",
    )
    text = fake.read_text(encoding="utf-8").upper()
    assert any(f.upper() in text for f in _FROZEN_FORBIDDEN_TEXT)


# ══════════════════════════════════════════════════════════════════════════
# 2. 追加(user_actions / basket_verification / selection_pack_activation_log)
# ══════════════════════════════════════════════════════════════════════════

_EXEC_METHOD_NAMES = {"execute", "executemany", "executescript"}
# ⚠ **V2.2-④ 扩容**:`trade_clock_events` 是交易时钟的事件流水(含 K8 §十五 的用户
# 主观说明)—— **只追加**。⛔ 注意父表 `trade_clock` **不在**这份清单里:它是一个有
# 生命周期的对象(`running → closed`),`UPDATE trade_clock SET status=…` 是它的正常
# 职责,同 `selection_packs` 版本注册表与其 activation_log 的既有分工。
# 追加表禁 UPDATE/DELETE;**`INSERT OR REPLACE` 也算改写**(= 先删后插),Y1 一并补上
# ——「只 INSERT」这条纪律不是靠动词第一个单词是 INSERT 就算数的。
_APPEND_ONLY_TABLES = ("user_actions", "basket_verification", "selection_pack_activation_log",
                       "trade_clock_events",
                       # V2.3.2 三张新审计表(plan §五 ⑨):阈值影子 / OUT 股票级清单 /
                       # OUT 研究影子对照。⛔ 三张都只增不改 —— 「上一次怎么判的」
                       # 本身就是审计对象,覆盖掉就没了。
                       "threshold_shadow_evals", "out_candidates", "out_shadow_daily",
                       "out_shadow_reviews")

_FORBIDDEN_APPEND_SQL = tuple(
    f"{verb} {tbl}" if verb == "UPDATE" else f"{verb} FROM {tbl}"
    for tbl in _APPEND_ONLY_TABLES
    for verb in ("UPDATE", "DELETE")
) + tuple(
    f"{verb} {tbl}"
    for tbl in _APPEND_ONLY_TABLES
    for verb in ("INSERT OR REPLACE INTO", "REPLACE INTO")
)


def _sql_literal(node: ast.AST) -> Optional[str]:
    """从字符串常量或简单 f-string 里尽力取出 SQL 文本用于子串匹配;取不到(纯变量/
    复杂拼接)时返回 None——保守不报,不做语义分析(与下面调用方的"宁可漏报不许
    误报"取向一致:漏报靠人工代码审查兜底,误报会训练大家忽略这条守门)。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                parts.append("")  # 变量插值位置留空,不影响相邻关键字子串判断
        return "".join(parts)
    return None


def _execute_sql_literals(path: Path) -> List[Tuple[int, str]]:
    """扫一个文件,返回其中「`execute`/`executemany`/`executescript` 调用的第一个
    参数是字符串字面量」的 `(行号, SQL文本)` 列表。**只看真实调用**——`_SCHEMA` 这类
    作为变量传入 `executescript(_SCHEMA)` 的调用,参数是 `ast.Name` 不是字符串常量,
    天然不会被本函数"看见"内容,不会被误伤(也不需要被扫,那里面只有 DDL)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            continue
        if name not in _EXEC_METHOD_NAMES or not node.args:
            continue
        sql = _sql_literal(node.args[0])
        if sql is not None:
            out.append((node.lineno, sql))
    return out


# —— 🟡 Y2(契约线审计 2026-08-03):`occurred_at` 时区口径 ————————————————————
# 病根:DDL 注释承诺「ISO8601 北京时间」,`record()` 却默认落 UTC。⑮ 客户端按北京时间
# 上报 `view` 事件那天,同一列里就会混进两种偏移 —— 而 `since`/`until` 与 `ORDER BY`
# 都是**字符串比较**,时间轴当场错乱且不报错。


# ══════════════════════════════════════════════════════════════════════════
# 3. 不回写(baskets / basket_cards 不受 basket_verification / basket_review_daily 追加影响)
# ══════════════════════════════════════════════════════════════════════════

def test_downstream_writes_never_rewrite_basket_or_card(tmp_path):
    db_path = tmp_path / "n.db"
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier, "
            "pack_version, engine_api_version, charter_version, created_at) "
            "VALUES ('20260731','cafebabe','示例篮子','示例驱动','theme',1,'K4-pack-v1',1,'v1.3.3',?)",
            (now,),
        )
        basket_id = conn.execute("SELECT id FROM baskets WHERE basket_key='cafebabe'").fetchone()[0]
        conn.execute(
            "INSERT INTO basket_cards (basket_id, version, card_json, created_at) VALUES (?,1,?,?)",
            (basket_id, '{"card":"D0 原判"}', now),
        )

    with connection(db_path) as conn:
        conn.row_factory = sqlite3.Row
        basket_before = dict(conn.execute("SELECT * FROM baskets WHERE id=?", (basket_id,)).fetchone())
        card_before = dict(
            conn.execute(
                "SELECT * FROM basket_cards WHERE basket_id=? AND version=1", (basket_id,)
            ).fetchone()
        )

    # 模拟"跑一次 D+1 验证与复盘":两张下游表各追加一行,不碰 baskets / basket_cards
    # (此刻 ⑧/⑨ 的业务模块尚不存在,直接用裸 SQL 站在它们将来会插入的同一张表上)。
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO basket_verification (basket_id, trade_date, observed_at, state, source, created_at) "
            "VALUES (?, '20260801', ?, 'verified', 'eod', ?)",
            (basket_id, now, now),
        )
        conn.execute(
            "INSERT INTO basket_review_daily (basket_id, review_date, depth, mech_json, created_at) "
            "VALUES (?, '20260801', 'full', '{}', ?)",
            (basket_id, now),
        )

    with connection(db_path) as conn:
        conn.row_factory = sqlite3.Row
        basket_after = dict(conn.execute("SELECT * FROM baskets WHERE id=?", (basket_id,)).fetchone())
        card_after = dict(
            conn.execute(
                "SELECT * FROM basket_cards WHERE basket_id=? AND version=1", (basket_id,)
            ).fetchone()
        )

    assert basket_after == basket_before
    assert card_after == card_before
    assert card_after["card_json"] == '{"card":"D0 原判"}'  # 逐字节不变,不是"字段相似"


# ══════════════════════════════════════════════════════════════════════════
# 4. 幂等(init_schema 空库 + 真实生产库只读副本各跑两遍)
# ══════════════════════════════════════════════════════════════════════════

def _schema_fingerprint(conn: sqlite3.Connection) -> dict:
    tables = sorted(
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )
    cols = {t: [tuple(r[1:3]) for r in conn.execute(f"PRAGMA table_info({t})")] for t in tables}
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    return {"tables": tables, "cols": cols, "integrity": integrity}


def test_init_schema_idempotent_on_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    init_schema(db_path)
    with connection(db_path) as conn:
        first = _schema_fingerprint(conn)
    init_schema(db_path)
    with connection(db_path) as conn:
        second = _schema_fingerprint(conn)
    assert first == second
    assert first["integrity"] == "ok"


def test_init_schema_idempotent_on_real_production_db_copy(real_db_readonly_copy):
    """`real_db_readonly_copy`(conftest.py,session 级)是真实 `data/neckline.db` 的
    一次性物理副本——本用例只碰副本,原始生产库全程 `mode=ro` 打开(见该夹具实现),
    满足「对真实 neckline.db 只读不写,建表逻辑用隔离库验证」。"""
    init_schema(real_db_readonly_copy)
    with connection(real_db_readonly_copy) as conn:
        first = _schema_fingerprint(conn)
    init_schema(real_db_readonly_copy)
    with connection(real_db_readonly_copy) as conn:
        second = _schema_fingerprint(conn)
    assert first == second
    assert first["integrity"] == "ok"
    missing = [t for t in _NEW_SQLITE_TABLES if t not in first["tables"]]
    assert not missing, f"真实生产库副本迁移后仍缺表:{missing}"


# ══════════════════════════════════════════════════════════════════════════
# 5. parquet 声明(intraday_ticks / auction_snapshots 写读往返 + 全空列不漂 String)
# ══════════════════════════════════════════════════════════════════════════

def _pdf(rows):
    import pandas as pd

    return pd.DataFrame(rows)


def test_intraday_ticks_and_auction_snapshots_are_valid_tables():
    assert "intraday_ticks" in md._VALID_TABLES
    assert "auction_snapshots" in md._VALID_TABLES
    assert md.TABLE_FLOAT_COLS["intraday_ticks"] == ("price", "volume", "amount", "cum_volume", "cum_amount")
    assert md.TABLE_FLOAT_COLS["auction_snapshots"] == (
        "auction_price", "auction_volume", "auction_amount", "pre_close", "gap_pct",
    )


def test_intraday_ticks_round_trip_dtypes(tmp_path):
    trade_date = date(2026, 7, 31)
    rows = [
        {"ts_code": "600001.SH", "trade_date": "20260731", "ts": "09:30:00", "price": 10.0,
         "volume": 100.0, "amount": 1000.0, "cum_volume": 100.0, "cum_amount": 1000.0, "source": "sina"},
        {"ts_code": "600002.SH", "trade_date": "20260731", "ts": "09:30:00", "price": 20.0,
         "volume": 200.0, "amount": 4000.0, "cum_volume": 200.0, "cum_amount": 4000.0, "source": "sina"},
    ]
    md.write_table_day("intraday_ticks", trade_date, pl.DataFrame(rows), parquet_dir=tmp_path)
    got = pl.read_parquet(md.day_file_path("intraday_ticks", trade_date, tmp_path))
    for col in md.TABLE_FLOAT_COLS["intraday_ticks"]:
        assert got.schema[col] == md.CANONICAL_FLOAT, f"{col} 漂了:{got.schema[col]}"


def test_auction_snapshots_round_trip_dtypes(tmp_path):
    trade_date = date(2026, 7, 31)
    rows = [
        {"ts_code": "600001.SH", "trade_date": "20260731", "auction_price": 10.1, "auction_volume": 5000.0,
         "auction_amount": 50500.0, "pre_close": 10.0, "gap_pct": 1.0, "captured_at": "2026-07-31T09:25:05+08:00"},
    ]
    md.write_table_day("auction_snapshots", trade_date, pl.DataFrame(rows), parquet_dir=tmp_path)
    got = pl.read_parquet(md.day_file_path("auction_snapshots", trade_date, tmp_path))
    for col in md.TABLE_FLOAT_COLS["auction_snapshots"]:
        assert got.schema[col] == md.CANONICAL_FLOAT, f"{col} 漂了:{got.schema[col]}"


def test_intraday_ticks_all_null_column_does_not_drift_to_string(tmp_path):
    """照 `test_concept_data.py::test_all_empty_column_does_not_drift_to_string` 体例:
    某数值列当日整列 None → pandas object → polars String,写盘后必须仍是声明的
    Float64(v1.3.5 血训:向"第一个文件"看齐会被脏基准带偏,必须向声明看齐)。"""
    rows = [
        {"ts_code": "600001.SH", "trade_date": "20260731", "ts": "09:30:00", "price": 10.0,
         "volume": 100.0, "amount": 1000.0, "cum_volume": None, "cum_amount": 1000.0, "source": "sina"},
        {"ts_code": "600002.SH", "trade_date": "20260731", "ts": "09:30:00", "price": 20.0,
         "volume": 200.0, "amount": 4000.0, "cum_volume": None, "cum_amount": 4000.0, "source": "sina"},
    ]
    df = pl.from_pandas(_pdf(rows))
    assert df.schema["cum_volume"] != md.CANONICAL_FLOAT  # 前提成立:全空列确实不是 Float64

    trade_date = date(2026, 7, 31)
    md.write_table_day("intraday_ticks", trade_date, df, parquet_dir=tmp_path)
    got = pl.read_parquet(md.day_file_path("intraday_ticks", trade_date, tmp_path))
    for col in md.TABLE_FLOAT_COLS["intraday_ticks"]:
        assert got.schema[col] == md.CANONICAL_FLOAT, f"{col} 漂了:{got.schema[col]}"
    assert got["cum_volume"].null_count() == 2


def test_auction_snapshots_all_null_column_does_not_drift_to_string(tmp_path):
    rows = [
        {"ts_code": "600001.SH", "trade_date": "20260731", "auction_price": 10.1, "auction_volume": 5000.0,
         "auction_amount": 50500.0, "pre_close": 10.0, "gap_pct": None, "captured_at": "2026-07-31T09:25:05+08:00"},
        {"ts_code": "600002.SH", "trade_date": "20260731", "auction_price": 20.2, "auction_volume": 3000.0,
         "auction_amount": 60600.0, "pre_close": 20.0, "gap_pct": None, "captured_at": "2026-07-31T09:25:05+08:00"},
    ]
    df = pl.from_pandas(_pdf(rows))
    assert df.schema["gap_pct"] != md.CANONICAL_FLOAT

    trade_date = date(2026, 7, 31)
    md.write_table_day("auction_snapshots", trade_date, df, parquet_dir=tmp_path)
    got = pl.read_parquet(md.day_file_path("auction_snapshots", trade_date, tmp_path))
    for col in md.TABLE_FLOAT_COLS["auction_snapshots"]:
        assert got.schema[col] == md.CANONICAL_FLOAT, f"{col} 漂了:{got.schema[col]}"
    assert got["gap_pct"].null_count() == 2
