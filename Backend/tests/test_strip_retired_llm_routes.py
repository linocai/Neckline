"""`scripts/oneoff/strip_retired_llm_routes.py` 单测(V2.1-①,问询台整链退役
两件套之二:清生产库 `app_settings.llm_task_routes` 里的 `inquiry` 死键)。

承阶段 0 教训「改脚本级写库代码先补一层单测」。重点锁四件事:
  ① 纯函数 `strip_keys` 的删/留语义;② 幂等(目标键已不在 → 0 改动,--confirm 也不写);
  ③ 双备份真的落盘 + 单事务写 + 写后复核;④ **只碰 `llm_task_routes` 一列**——
  `llm_default_provider`/其它列/其它表零改动。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "oneoff"))

from strip_retired_llm_routes import (  # noqa: E402
    RETIRED_TASK_KEYS,
    apply_strip,
    strip_keys,
)

from neckline.db import init_schema  # noqa: E402


def _seed_app_settings(
    db_path: Path, routes: dict, default_provider: Optional[str] = None,
) -> None:
    """直接裸写 `app_settings.llm_task_routes`(绕过 `settings_store.set_llm_routes`
    的 `ALL_TASKS` 校验——本脚本要修的正是"已退役任务名躺在库里"这个校验层挡不住的
    历史残留场景,不能用会拒绝写入的 API 去造这个场景)。"""
    init_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (id, push_report, push_retreat, review_col_map) "
            "VALUES (1, 1, 1, '{}')"
        )
        conn.execute(
            "UPDATE app_settings SET llm_task_routes=?, llm_default_provider=? WHERE id=1",
            (json.dumps(routes, ensure_ascii=False), default_provider),
        )
        conn.commit()
    finally:
        conn.close()


def _read_raw(db_path: Path) -> tuple:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT llm_task_routes, llm_default_provider FROM app_settings WHERE id=1"
        ).fetchone()
    finally:
        conn.close()
    return row


# ————————————————————————————————————————————————————————————————
# 纯函数 strip_keys
# ————————————————————————————————————————————————————————————————

def test_strip_keys_removes_present_and_preserves_others():
    routes, removed = strip_keys({"inquiry": "glm", "review": "glm", "script": "deepseek"}, ["inquiry"])
    assert routes == {"review": "glm", "script": "deepseek"}
    assert removed == {"inquiry": "glm"}


def test_strip_keys_absent_key_is_noop():
    routes, removed = strip_keys({"review": "glm"}, ["inquiry"])
    assert routes == {"review": "glm"}
    assert removed == {}


def test_default_retired_keys_is_exactly_inquiry():
    """plan §五 V2.1-①原文只点名 `inquiry` 一个键——锁死默认值,防止有人"顺手"
    把这个脚本改成清别的键的通用工具。"""
    assert RETIRED_TASK_KEYS == ("inquiry",)


# ————————————————————————————————————————————————————————————————
# apply_strip:dry-run / confirm / 幂等 / 只碰一列
# ————————————————————————————————————————————————————————————————

def test_dry_run_reports_diff_but_does_not_write(tmp_path):
    db = tmp_path / "n.db"
    _seed_app_settings(db, {"inquiry": "glm", "review": "glm"}, default_provider="deepseek")

    rep = apply_strip(db, ["inquiry"], confirm=False)

    assert rep.dry_run is True
    assert rep.removed == {"inquiry": "glm"}
    assert rep.after == {"review": "glm"}          # 报告里算出的"将会"是这样
    # 但库里必须原封不动
    raw_routes, raw_default = _read_raw(db)
    assert json.loads(raw_routes) == {"inquiry": "glm", "review": "glm"}
    assert raw_default == "deepseek"
    assert not list(db.parent.glob(f"{db.name}.bak-*"))   # 演练不许产生备份文件


def test_confirm_strips_key_and_preserves_everything_else(tmp_path):
    db = tmp_path / "n.db"
    _seed_app_settings(db, {"inquiry": "glm", "review": "glm", "script": "deepseek"},
                       default_provider="deepseek")

    rep = apply_strip(db, ["inquiry"], confirm=True)

    assert rep.dry_run is False
    assert rep.removed == {"inquiry": "glm"}
    assert rep.integrity == "ok"
    raw_routes, raw_default = _read_raw(db)
    assert json.loads(raw_routes) == {"review": "glm", "script": "deepseek"}
    # 🔴 只碰 llm_task_routes 一列——llm_default_provider 必须逐字节不变
    assert raw_default == "deepseek"
    # 双备份真的落盘
    baks = list(db.parent.glob(f"{db.name}.bak-*"))
    cpbaks = list(db.parent.glob(f"{db.name}.cpbak-*"))
    assert len(baks) == 1 and len(cpbaks) == 1
    assert baks[0].stat().st_size > 0 and cpbaks[0].stat().st_size > 0


def test_confirm_is_idempotent_second_run_is_zero_change_and_no_new_backup(tmp_path):
    db = tmp_path / "n.db"
    _seed_app_settings(db, {"inquiry": "glm"}, default_provider="glm")

    first = apply_strip(db, ["inquiry"], confirm=True)
    assert first.removed == {"inquiry": "glm"}
    n_backups_after_first = len(list(db.parent.glob(f"{db.name}.bak-*")))
    assert n_backups_after_first == 1

    second = apply_strip(db, ["inquiry"], confirm=True)
    assert second.removed == {}                    # 幂等:已经不在了
    assert second.after == {}
    raw_routes, _ = _read_raw(db)
    assert json.loads(raw_routes) == {}
    # 0 改动不该再产生一次备份(同 retire_k4_b3.py 的"已退役 = 不写不备份"体例)
    assert len(list(db.parent.glob(f"{db.name}.bak-*"))) == n_backups_after_first


def test_key_never_present_from_the_start_is_zero_change(tmp_path):
    db = tmp_path / "n.db"
    _seed_app_settings(db, {"review": "glm"}, default_provider="glm")

    rep = apply_strip(db, ["inquiry"], confirm=True)
    assert rep.removed == {}
    raw_routes, _ = _read_raw(db)
    assert json.loads(raw_routes) == {"review": "glm"}
    assert not list(db.parent.glob(f"{db.name}.bak-*"))


def test_multiple_keys_only_removes_named_ones(tmp_path):
    db = tmp_path / "n.db"
    _seed_app_settings(db, {"inquiry": "glm", "ghost_task": "kimi", "review": "glm"})

    rep = apply_strip(db, ["inquiry", "ghost_task"], confirm=True)
    assert rep.removed == {"inquiry": "glm", "ghost_task": "kimi"}
    raw_routes, _ = _read_raw(db)
    assert json.loads(raw_routes) == {"review": "glm"}


# ————————————————————————————————————————————————————————————————
# 防呆:非预期库结构 / 非法 JSON
# ————————————————————————————————————————————————————————————————

def test_missing_app_settings_row_raises(tmp_path):
    """`init_schema` 建表但没人调过任何 setter → id=1 行不存在,不是本脚本能安全
    处理的场景,拒绝而不是静默当作"空路由表"处理。"""
    db = tmp_path / "n.db"
    init_schema(db)

    with pytest.raises(ValueError, match="app_settings 无 id=1 行"):
        apply_strip(db, ["inquiry"], confirm=False)


def test_malformed_json_raises_and_does_not_touch_db(tmp_path):
    db = tmp_path / "n.db"
    init_schema(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO app_settings (id, push_report, push_retreat, review_col_map, llm_task_routes) "
            "VALUES (1, 1, 1, '{}', ?)", ("{not valid json",),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match="不是合法 JSON"):
        apply_strip(db, ["inquiry"], confirm=True)

    # 拒绝解析就该在写库之前止步——原始坏字符串必须原封不动,零备份文件。
    raw_routes, _ = _read_raw(db)
    assert raw_routes == "{not valid json"
    assert not list(db.parent.glob(f"{db.name}.bak-*"))
