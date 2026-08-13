"""`scripts/oneoff/retire_legacy_packs.py` 单测(plan §五 V2.2-①「表与契约变更」)。

承阶段 0 教训「改脚本级写库代码先补一层单测」。重点锁四件事:
  ① 演练零写(库逐字节不变);② `--confirm` 只清 `is_active`、追加 deactivate/cli
  事件、**零 DELETE**;③ 幂等(二次跑 no-op);④ 只碰 LEGACY 线,别的线一根汗毛
  不许动。

⚠ 本地 dev 库的 `selection_packs` 是零行 —— 拿它演练是 no-op、证明不了任何事
(orchestrator 核实事实),所以夹具**裸 SQL 造一行 LEGACY 现役行**(activate_pack
已被 engine_api 闸挡死,而这正是生产老库的真实形状:行早已存在、不经激活入口)。
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "oneoff"))

from retire_legacy_packs import RETIRE_NOTE, retire_legacy_packs  # noqa: E402

from neckline.db import connection, init_schema  # noqa: E402
from neckline.selection import pack  # noqa: E402


def _mk_db(tmp_path: Path, *, legacy_active: bool = True, with_skeleton: bool = False) -> Path:
    db = tmp_path / "neckline.db"
    init_schema(db_path=db)
    with connection(db) as conn:
        conn.execute(
            "INSERT INTO selection_packs (pack_version,name,engine_api_version,manifest_json,"
            "config_json,evidence_ref,is_active,created_at,activated_at) "
            "VALUES ('K7-pack-v1','K7 历史包',1,'{}','{}','research/k7_pre_report.md',?, "
            "'2026-08-03T00:00:00+00:00','2026-08-03T00:00:00+00:00')",
            (1 if legacy_active else 0,),
        )
        conn.execute(
            "INSERT INTO selection_pack_activation_log (pack_version,action,via,note,at) "
            "VALUES ('K7-pack-v1','activate','cli','','2026-08-03T00:00:00+00:00')"
        )
    if with_skeleton:
        doc = pack.load_pack_file(
            Path(__file__).resolve().parent.parent / "packs" / "K8-skeleton.json")
        pack.activate_pack(doc["manifest"], doc["config"], via="seed", db_path=db)
    return db


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _rows(db: Path, sql: str):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_dry_run_writes_nothing(tmp_path: Path):
    db = _mk_db(tmp_path)
    before = _md5(db)
    rep = retire_legacy_packs(db, confirm=False)
    assert rep.dry_run is True and len(rep.retired) == 1
    assert _md5(db) == before   # 演练逐字节零写


def test_confirm_clears_is_active_appends_event_and_deletes_nothing(tmp_path: Path):
    db = _mk_db(tmp_path)
    rows_before = _rows(db, "SELECT COUNT(*) FROM selection_packs")[0][0]
    rep = retire_legacy_packs(db, confirm=True)
    assert rep.integrity == "ok" and [r["pack_version"] for r in rep.retired] == ["K7-pack-v1"]

    assert _rows(db, "SELECT COUNT(*) FROM selection_packs")[0][0] == rows_before   # 零 DELETE
    assert _rows(
        db, "SELECT pack_version, line_code, is_active, status, activated_at FROM selection_packs"
    ) == [("K7-pack-v1", "LEGACY", 0, "running", "2026-08-03T00:00:00+00:00")]
    # activated_at 留档不清(历史事实),只有 is_active 变 0。
    assert _rows(
        db, "SELECT pack_version, action, via, note FROM selection_pack_activation_log ORDER BY id"
    ) == [
        ("K7-pack-v1", "activate", "cli", ""),
        ("K7-pack-v1", "deactivate", "cli", RETIRE_NOTE),
    ]


def test_second_run_is_idempotent_noop(tmp_path: Path):
    db = _mk_db(tmp_path)
    retire_legacy_packs(db, confirm=True)
    before = _md5(db)
    rep2 = retire_legacy_packs(db, confirm=True)
    assert rep2.retired == [] and rep2.already_inactive == ["K7-pack-v1"]
    assert _md5(db) == before   # 二次跑逐字节零改动(不重复追加事件)


def test_non_legacy_lines_are_untouched(tmp_path: Path):
    """已激活骨架线的库上跑退役:只动 LEGACY,V 线现役行一根汗毛不许动。"""
    db = _mk_db(tmp_path, with_skeleton=True)
    rep = retire_legacy_packs(db, confirm=True)
    assert [r["pack_version"] for r in rep.retired] == ["K7-pack-v1"]
    assert pack.get_active_skeleton(db).pack_version == "K8-V0.8"
    assert pack.get_active_line("LEGACY", db) is None   # LEGACY 线已无现役
    # 骨架线的事件流里没有多出任何 deactivate。
    log = _rows(db, "SELECT pack_version, action FROM selection_pack_activation_log ORDER BY id")
    assert ("K8-V0.5", "deactivate") not in log


def test_empty_registry_is_a_clean_noop(tmp_path: Path):
    """本地 dev 形状(selection_packs 零行):干净 no-op,不炸、不写。"""
    db = tmp_path / "empty.db"
    init_schema(db_path=db)
    rep = retire_legacy_packs(db, confirm=True)
    assert rep.retired == [] and rep.already_inactive == [] and rep.integrity == "ok"
