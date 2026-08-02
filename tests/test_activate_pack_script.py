"""`scripts/activate_pack.py` 四道闸单测(plan §五 V2-③,复刻
`tests/test_charter_v133.py` 对 `activate_charter.py` 的测法:把 `scripts/`
塞进 `sys.path`,直接 `import activate_pack` 当模块用,不经子进程)。

**全部用 `tmp_path` 隔离库**,不碰真实 `data/neckline.db`(`run()`/`main()`
的 `--db`/`db_path` 参数全程显式传)。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import activate_pack as activate_pack_script  # noqa: E402

from neckline.selection import pack, primitives  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_K4_PACK_FILE = _REPO_ROOT / "packs" / "K4-pack.json"
_K7_PACK_FILE = _REPO_ROOT / "packs" / "K7-pack.json"


def _write_pack(tmp_path: Path, filename: str, pack_version: str, **overrides: Any) -> Path:
    manifest = {
        "pack_version": pack_version,
        "name": "脚本测试包",
        "date": "2026-08-02",
        "engine_api_version": 1,
        "evidence_ref": [],
    }
    config = {
        "seeds": {"non_new_stock": {"min_days": 60}},
        "tier": {"weights": {"sector_strength": 1.0}, "dims": ["sector_strength"]},
    }
    manifest.update(overrides.get("manifest", {}))
    config.update(overrides.get("config", {}))
    file = tmp_path / filename
    file.write_text(json.dumps({"manifest": manifest, "config": config}), encoding="utf-8")
    return file


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


# ══════════════════════════════════════════════════════════════════════════
# 闸 1+2:schema / 原语白名单 / engine_api_version 拒绝路径
# ══════════════════════════════════════════════════════════════════════════

def test_run_rejects_missing_file(tmp_path: Path):
    rc = activate_pack_script.run(tmp_path / "nope.json", tmp_path / "n.db", confirm=False)
    assert rc == 2


def test_run_rejects_malformed_json(tmp_path: Path):
    file = tmp_path / "bad.json"
    file.write_text("{not json", encoding="utf-8")
    rc = activate_pack_script.run(file, tmp_path / "n.db", confirm=False)
    assert rc == 2


def test_run_rejects_missing_manifest_fields(tmp_path: Path):
    file = tmp_path / "bad.json"
    file.write_text(json.dumps({"manifest": {}, "config": {}}), encoding="utf-8")
    rc = activate_pack_script.run(file, tmp_path / "n.db", confirm=False)
    assert rc == 2


def test_run_rejects_unregistered_primitive_reference(tmp_path: Path):
    file = _write_pack(
        tmp_path, "bad.json", "bad-prim-v1",
        config={"seeds": {"nonexistent_primitive": {}}},
    )
    rc = activate_pack_script.run(file, tmp_path / "n.db", confirm=False)
    assert rc == 2


def test_run_rejects_incompatible_engine_api_version(tmp_path: Path):
    file = _write_pack(tmp_path, "bad.json", "bad-engine-v1", manifest={"engine_api_version": 2})
    rc = activate_pack_script.run(file, tmp_path / "n.db", confirm=False)
    assert rc == 2


def test_run_gate2_defense_in_depth_catches_whitelist_violation_in_registry(tmp_path: Path, monkeypatch):
    """闸 2 的防御性复核:即便(假设性地)注册表混入了引用白名单外特征的原语,
    脚本仍独立拦下——不完全依赖 `primitives.py` 模块加载时的构造期校验(那道
    校验测的是"能不能造出坏对象",这道测的是"脚本真的会去问注册表要答案")。"""
    evil = SimpleNamespace(name="evil", kind="filter", inputs=("llm_judgments.verdict",))
    monkeypatch.setitem(primitives.PRIMITIVES, "evil", evil)
    file = _write_pack(tmp_path, "p.json", "gate2-v1")
    rc = activate_pack_script.run(file, tmp_path / "n.db", confirm=False)
    assert rc == 2


# ══════════════════════════════════════════════════════════════════════════
# 闸 3:演练模式零写库
# ══════════════════════════════════════════════════════════════════════════

def test_dry_run_writes_nothing_to_db_file(tmp_path: Path):
    db_path = tmp_path / "n.db"
    # 先跑一次让 schema 就位(建表本身是合法的一次性写入,不算"演练模式的写"),
    # 再从这个"已初始化"的基线开始比较 MD5——这样测的是"演练模式本身不额外写",
    # 而不是"从来没有过任何文件"这种更弱的断言。
    pack.get_active_pack(db_path=db_path)
    before = _md5(db_path)

    rc = activate_pack_script.run(_K4_PACK_FILE, db_path, confirm=False)
    assert rc == 0
    after = _md5(db_path)
    assert before == after
    assert pack.get_active_pack(db_path=db_path) is None   # 确实什么都没激活


def test_dry_run_prints_diff_without_error_for_real_k4_pack(tmp_path: Path, capsys):
    db_path = tmp_path / "n.db"
    rc = activate_pack_script.run(_K4_PACK_FILE, db_path, confirm=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "闸 1 通过" in out
    assert "闸 2 通过" in out
    assert "dry-run" in out
    assert "K4-pack-v1" in out


# ══════════════════════════════════════════════════════════════════════════
# V2-③-K7:`packs/K7-pack.json` 过四道闸演练(**只 dry-run,不 --confirm**——
# ③-K7-E 明文"本子项只产出文件 + 演练通过,不激活",激活时机排在 ⑯-E)。
# ══════════════════════════════════════════════════════════════════════════

def test_dry_run_writes_nothing_to_db_file_for_k7_pack(tmp_path: Path):
    db_path = tmp_path / "n.db"
    pack.get_active_pack(db_path=db_path)   # 先让 schema 就位(同 K4 那条测试体例)
    before = _md5(db_path)

    rc = activate_pack_script.run(_K7_PACK_FILE, db_path, confirm=False)
    assert rc == 0
    after = _md5(db_path)
    assert before == after
    assert pack.get_active_pack(db_path=db_path) is None   # 确实什么都没激活


def test_dry_run_prints_diff_without_error_for_real_k7_pack(tmp_path: Path, capsys):
    db_path = tmp_path / "n.db"
    rc = activate_pack_script.run(_K7_PACK_FILE, db_path, confirm=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "闸 1 通过" in out
    assert "闸 2 通过" in out
    assert "dry-run" in out
    assert "K7-pack-v1" in out


def test_dry_run_diff_against_active_k4_pack_shows_ranking_dims_changed(tmp_path: Path, capsys):
    """演练模式打印的 diff 必须如实指出排序键(`intel_rank_priority`)与
    `tier.weights`/`tier.dims` 都变了——运维读日志就能一眼看到 K7-pack 到底
    改了什么(闸 3 的既有职责,K4→K7 是本项目第一次有意义的真实换包场景)。"""
    db_path = tmp_path / "n.db"
    rc_activate = activate_pack_script.run(_K4_PACK_FILE, db_path, confirm=True)
    assert rc_activate == 0

    rc = activate_pack_script.run(_K7_PACK_FILE, db_path, confirm=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "现役 K4-pack-v1 → 目标 K7-pack-v1" in out
    assert "intel_rank_priority" in out and "← 改动" in out
    # dry-run 不写库:K4-pack-v1 仍是唯一现役。
    actives = [p.pack_version for p in pack.list_packs(db_path=db_path) if p.is_active]
    assert actives == ["K4-pack-v1"]


# ══════════════════════════════════════════════════════════════════════════
# 闸 4:--confirm 才写,is_active 唯一 + 事件日志
# ══════════════════════════════════════════════════════════════════════════

def test_confirm_activates_and_uniqueness_assertion_holds(tmp_path: Path):
    db_path = tmp_path / "n.db"
    rc = activate_pack_script.run(_K4_PACK_FILE, db_path, confirm=True)
    assert rc == 0
    actives = [p.pack_version for p in pack.list_packs(db_path=db_path) if p.is_active]
    assert actives == ["K4-pack-v1"]


def test_confirm_first_activation_writes_one_log_row(tmp_path: Path):
    db_path = tmp_path / "n.db"
    activate_pack_script.run(_K4_PACK_FILE, db_path, confirm=True)
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT pack_version, action FROM selection_pack_activation_log").fetchall()
    finally:
        conn.close()
    assert rows == [("K4-pack-v1", "activate")]


def test_confirm_switching_packs_writes_two_log_rows(tmp_path: Path):
    db_path = tmp_path / "n.db"
    file_a = _write_pack(tmp_path, "a.json", "switch-a")
    file_b = _write_pack(tmp_path, "b.json", "switch-b")
    activate_pack_script.run(file_a, db_path, confirm=True)
    rc = activate_pack_script.run(file_b, db_path, confirm=True)
    assert rc == 0

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT pack_version, action FROM selection_pack_activation_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [
        ("switch-a", "activate"),
        ("switch-a", "deactivate"),
        ("switch-b", "activate"),
    ]


def test_confirm_reactivating_already_active_is_noop_and_returns_zero(tmp_path: Path):
    db_path = tmp_path / "n.db"
    activate_pack_script.run(_K4_PACK_FILE, db_path, confirm=True)
    rc = activate_pack_script.run(_K4_PACK_FILE, db_path, confirm=True)
    assert rc == 0

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        n = conn.execute("SELECT count(*) FROM selection_pack_activation_log").fetchone()[0]
    finally:
        conn.close()
    assert n == 1   # 第二次调用没有多写任何事件


def test_confirm_rejects_tampered_content_even_when_currently_active(tmp_path: Path):
    """回归测试(2026-08-02 手工演练发现的顺序漏洞):"已现役,无需激活"的快捷
    退出**不得**发生在"同版本号内容是否一致"的完整性核对之前——否则「文件被
    篡改但版本号没改、且该版本恰好当前现役」会被快捷退出直接放过,完整性问题
    被静默掩盖。"""
    db_path = tmp_path / "n.db"
    file_v1 = _write_pack(tmp_path, "v1.json", "tamper-v1")
    activate_pack_script.run(file_v1, db_path, confirm=True)

    tampered = _write_pack(tmp_path, "tampered.json", "tamper-v1", manifest={"name": "TAMPERED"})
    rc = activate_pack_script.run(tampered, db_path, confirm=True)
    assert rc != 0

    stored = pack.get_pack("tamper-v1", db_path=db_path)
    assert stored.name == "脚本测试包"   # 原内容未被覆盖


def test_confirm_rejects_tampered_content_when_not_currently_active(tmp_path: Path):
    db_path = tmp_path / "n.db"
    file_v1 = _write_pack(tmp_path, "v1.json", "tamper-v2")
    file_v2 = _write_pack(tmp_path, "v2.json", "tamper-v2-other")
    activate_pack_script.run(file_v1, db_path, confirm=True)
    activate_pack_script.run(file_v2, db_path, confirm=True)   # 切走,tamper-v2 不再现役

    tampered = _write_pack(tmp_path, "tampered.json", "tamper-v2", manifest={"name": "TAMPERED"})
    rc = activate_pack_script.run(tampered, db_path, confirm=True)
    assert rc != 0


# ══════════════════════════════════════════════════════════════════════════
# 默认参数 / 收尾细节
# ══════════════════════════════════════════════════════════════════════════

def test_default_pack_file_points_to_repo_k4_pack_json():
    assert activate_pack_script._DEFAULT_PACK_FILE == _K4_PACK_FILE
    assert activate_pack_script._DEFAULT_PACK_FILE.exists()
