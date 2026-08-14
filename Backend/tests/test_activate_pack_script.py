"""`scripts/activate_pack.py` 四道闸单测(plan §五 V2-③ 立,V2.2-① 扩多版本线:
复刻 `tests/test_charter_v133.py` 对 `activate_charter.py` 的测法:把 `scripts/`
塞进 `sys.path`,直接 `import activate_pack` 当模块用,不经子进程)。

**全部用 `tmp_path` 隔离库**,不碰真实 `data/neckline.db`(`run()`/`main()`
的 `--db`/`db_path` 参数全程显式传)。

V2.2-① 起的格局:正例走四个新包文件(`K8-skeleton.json` / `C1.json` / `Z1.json` /
`Y1.json`,批 1 只演练不激活生产);`K4-pack.json` / `K7-pack.json` 降为**负例守门**
(engine_api 1→2 后必须被闸 2 拒 —— ⛔ 别为了让老测试变绿去改那两个冻结文件)。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import activate_pack as activate_pack_script  # noqa: E402

from neckline.selection import engine_api, pack, primitives  # noqa: E402
from neckline.db import init_schema  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
# ⚠ 两个 LEGACY 包已于 2026-08-11 仓库整理时移进 `archive/packs_retired/`(只做负例守门)。
_K4_PACK_FILE = _REPO_ROOT.parent / "archive" / "packs_retired" / "K4-pack.json"
_K7_PACK_FILE = _REPO_ROOT.parent / "archive" / "packs_retired" / "K7-pack.json"
_K8_SKELETON_FILE = _REPO_ROOT / "packs" / "K8-skeleton.json"
_C1_FILE = _REPO_ROOT / "packs" / "C1.json"
_Z1_FILE = _REPO_ROOT / "packs" / "Z1.json"
_Y1_FILE = _REPO_ROOT / "packs" / "Y1.json"
_NEW_PACK_FILES = (_K8_SKELETON_FILE, _C1_FILE, _Z1_FILE, _Y1_FILE)


def _write_pack(tmp_path: Path, filename: str, pack_version: str, **overrides: Any) -> Path:
    """合成骨架线(V)测试包文件(V2.2-① 起缺省 line_code='V' + 现行 engine_api)。"""
    manifest = {
        "pack_version": pack_version,
        "name": "脚本测试包",
        "date": "2026-08-02",
        "engine_api_version": engine_api.ENGINE_API_VERSION,
        "line_code": "V",
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


def _write_engine_pack(tmp_path: Path, filename: str, line_code: str = "C",
                       pack_version: str = "C-test", **overrides: Any) -> Path:
    """合成引擎线测试包文件(五关齐 + 每叶 provenance,闸 1 正例底座)。"""
    leaf = lambda v: {"value": v, "provenance": {  # noqa: E731
        "source": "engineering_v1", "basis": "测试:从 K8 某句翻译", "calibration": "pending"}}
    manifest = {
        "pack_version": pack_version, "name": "脚本测试引擎包", "date": "2026-08-09",
        "engine_api_version": engine_api.ENGINE_API_VERSION,
        "line_code": line_code, "evidence_ref": [],
    }
    config = {
        "engine": {
            "engine_code": line_code,
            "applies_to": "测试用引擎",
            "gates": {
                "market": {"primary_regimes": leaf(["trend_continuation"])},
                "sector": {"industry_rank_max": leaf(10)},
                # 🔴 裁定 #11:位置关零阈值,只剩定性文本键(⛔ 不走 provenance 闸)。
                "position": {"guidance": "测试用的定性位置准则"},
                # 🔴 裁定 #12:核心关同样零阈值,只剩定性文本键。
                "core": {"guidance": "测试用的定性核心(龙头)准则"},
                "evidence": {"independent_evidence_min": leaf(3)},
            },
            "tier_evidence": {
                "t1": {"max_evidence_degrades": leaf(0)},
                "t2": {"max_evidence_degrades": leaf(1)},
            },
        },
    }
    manifest.update(overrides.get("manifest", {}))
    config.update(overrides.get("config", {}))
    file = tmp_path / filename
    file.write_text(json.dumps({"manifest": manifest, "config": config}, ensure_ascii=False),
                    encoding="utf-8")
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
    """V2.2-① 起旧版号 1 = 不兼容的那一侧(引擎现为 2)。"""
    file = _write_pack(tmp_path, "bad.json", "bad-engine-v1", manifest={"engine_api_version": 1})
    rc = activate_pack_script.run(file, tmp_path / "n.db", confirm=False)
    assert rc == 2


def test_run_rejects_real_k7_pack_file_rollback_anchor_is_dead(tmp_path: Path, capsys):
    """🔴 V2.2-① 反向守门(plan §五 ① 原文):仓库里真的 `archive/packs_retired/K7-pack.json` 走
    闸**必须被拒**——把「回滚锚已作废」钉成机器判据,⛔ 不留一条自己都不信的绳;
    K4 同理。⛔ 修这条红的唯一合法方式是改测试,**不许**去改那两个冻结的历史包
    文件(那正好把守门连档案一起销毁)。"""
    for legacy in (_K7_PACK_FILE, _K4_PACK_FILE):
        rc = activate_pack_script.run(legacy, tmp_path / "n.db", confirm=False)
        assert rc == 2, f"{legacy.name} 竟然过闸了——engine_api 守门被拆?"
        err = capsys.readouterr().err
        assert "engine_api_version 不兼容" in err
    assert pack.get_active_pack(db_path=tmp_path / "n.db") is None   # 一行都没写


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
# V2.2-① 闸 1 扩:引擎线交叉校验 + provenance(正反两例,plan §五 ① 测试清单)
# ══════════════════════════════════════════════════════════════════════════

def test_run_rejects_engine_code_mismatching_line_code(tmp_path: Path, capsys):
    file = _write_engine_pack(tmp_path, "bad.json", line_code="C", pack_version="mismatch-v1")
    doc = json.loads(file.read_text(encoding="utf-8"))
    doc["config"]["engine"]["engine_code"] = "Z"
    file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rc = activate_pack_script.run(file, tmp_path / "n.db", confirm=False)
    assert rc == 2
    assert "逐位相等" in capsys.readouterr().err


def test_run_rejects_engine_pack_with_missing_provenance(tmp_path: Path, capsys):
    file = _write_engine_pack(tmp_path, "bad.json", pack_version="noprov-v1")
    doc = json.loads(file.read_text(encoding="utf-8"))
    doc["config"]["engine"]["gates"]["sector"]["industry_rank_max"] = 10   # 裸值
    file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rc = activate_pack_script.run(file, tmp_path / "n.db", confirm=False)
    assert rc == 2
    assert "provenance" in capsys.readouterr().err


def test_run_accepts_well_formed_engine_pack_dry_run(tmp_path: Path, capsys):
    file = _write_engine_pack(tmp_path, "good.json", pack_version="good-eng-v1")
    rc = activate_pack_script.run(file, tmp_path / "n.db", confirm=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "闸 1 通过" in out and "闸 2 通过" in out and "dry-run" in out


# ══════════════════════════════════════════════════════════════════════════
# 闸 3:演练模式零写库(正例改走 V2.2 新包文件)
# ══════════════════════════════════════════════════════════════════════════

def test_dry_run_writes_nothing_to_db_file(tmp_path: Path):
    db_path = tmp_path / "n.db"
    # 先跑一次让 schema 就位(建表本身是合法的一次性写入,不算"演练模式的写"),
    # 再从这个"已初始化"的基线开始比较 MD5——这样测的是"演练模式本身不额外写",
    # 而不是"从来没有过任何文件"这种更弱的断言。
    init_schema(db_path)
    before = _md5(db_path)

    rc = activate_pack_script.run(_K8_SKELETON_FILE, db_path, confirm=False)
    assert rc == 0
    after = _md5(db_path)
    assert before == after
    assert pack.get_active_pack(db_path=db_path) is None   # 确实什么都没激活


@pytest.mark.parametrize("pack_file", _NEW_PACK_FILES, ids=lambda p: p.name)
def test_dry_run_four_new_packs_all_pass_four_gates(pack_file: Path, tmp_path: Path, capsys):
    """V2.2-① 验收原文:四个新包文件走演练四道闸全绿且**一个都还没激活**。"""
    db_path = tmp_path / "n.db"
    init_schema(db_path)
    before = _md5(db_path)
    rc = activate_pack_script.run(pack_file, db_path, confirm=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "闸 1 通过" in out and "闸 2 通过" in out and "dry-run" in out
    assert _md5(db_path) == before                               # 零写库
    assert pack.list_packs(db_path=db_path) == []                # 一个都没激活


def test_dry_run_diff_against_active_skeleton_shows_changes(tmp_path: Path, capsys):
    """演练模式打印的 diff 必须如实指出改了什么(闸 3 的既有职责,V2.2 场景 =
    换骨架包)。先激活一个参数不同的合成骨架包,再 dry-run 真实 K8-skeleton。"""
    db_path = tmp_path / "n.db"
    old = _write_pack(
        tmp_path, "old.json", "old-skel-v1",
        config={"seeds": {"non_new_stock": {"min_days": 30}},
                "tier": {"weights": {"sector_strength": 1.0}, "dims": ["sector_strength"]}},
    )
    assert activate_pack_script.run(old, db_path, confirm=True) == 0

    rc = activate_pack_script.run(_K8_SKELETON_FILE, db_path, confirm=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[V 线] 现役 old-skel-v1 → 目标 K8-V0.8" in out
    assert "← 改动" in out
    # dry-run 不写库:old-skel-v1 仍是 V 线唯一现役。
    actives = [p.pack_version for p in pack.list_packs(db_path=db_path) if p.is_active]
    assert actives == ["old-skel-v1"]


# ══════════════════════════════════════════════════════════════════════════
# 闸 4:--confirm 才写,per-line is_active 唯一 + 事件日志
# ══════════════════════════════════════════════════════════════════════════

def test_confirm_activates_and_per_line_uniqueness_assertion_holds(tmp_path: Path):
    db_path = tmp_path / "n.db"
    rc = activate_pack_script.run(_K8_SKELETON_FILE, db_path, confirm=True)
    assert rc == 0
    actives = [p.pack_version for p in pack.list_packs(db_path=db_path) if p.is_active]
    assert actives == ["K8-V0.8"]


def test_confirm_four_lines_coexist_each_uniquely_active(tmp_path: Path):
    """四线各自激活后并存,每线唯一现役(V2.2-① 的核心格局;⚠ 本用例只动 tmp 库,
    生产侧批 1 仍**不激活任何新线**)。"""
    db_path = tmp_path / "n.db"
    for f in _NEW_PACK_FILES:
        assert activate_pack_script.run(f, db_path, confirm=True) == 0
    actives = {p.line_code: p.pack_version for p in pack.list_packs(db_path=db_path) if p.is_active}
    assert actives == {"V": "K8-V0.8", "C": "C1", "Z": "Z1", "Y": "Y1"}
    engines = pack.get_active_engines(db_path=db_path)
    assert list(engines) == ["C", "Z", "Y"]


def test_confirm_first_activation_writes_one_log_row(tmp_path: Path):
    db_path = tmp_path / "n.db"
    activate_pack_script.run(_K8_SKELETON_FILE, db_path, confirm=True)
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT pack_version, action FROM selection_pack_activation_log").fetchall()
    finally:
        conn.close()
    assert rows == [("K8-V0.8", "activate")]


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


def test_confirm_engine_activation_leaves_other_lines_untouched(tmp_path: Path):
    """plan 陷阱 #1 的脚本层判据:激活引擎线不产生任何针对骨架线的 deactivate。"""
    db_path = tmp_path / "n.db"
    activate_pack_script.run(_K8_SKELETON_FILE, db_path, confirm=True)
    rc = activate_pack_script.run(_C1_FILE, db_path, confirm=True)
    assert rc == 0

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT pack_version, action FROM selection_pack_activation_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("K8-V0.8", "activate"), ("C1", "activate")]
    assert pack.get_active_skeleton(db_path).pack_version == "K8-V0.8"


def test_confirm_reactivating_already_active_is_noop_and_returns_zero(tmp_path: Path):
    db_path = tmp_path / "n.db"
    activate_pack_script.run(_K8_SKELETON_FILE, db_path, confirm=True)
    rc = activate_pack_script.run(_K8_SKELETON_FILE, db_path, confirm=True)
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

def test_default_pack_file_points_to_repo_k8_skeleton_json():
    """V2.2-① 起默认包 = K8 骨架包(旧默认 K4-pack.json 已被 engine_api 闸作废,
    留一个必被拒的默认值只会让运维以为脚本坏了)。"""
    assert activate_pack_script._DEFAULT_PACK_FILE == _K8_SKELETON_FILE
    assert activate_pack_script._DEFAULT_PACK_FILE.exists()
    assert _K4_PACK_FILE.exists() and _K7_PACK_FILE.exists()   # 历史文件留档不删
