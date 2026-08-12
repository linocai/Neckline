"""**四线原子激活**单测(plan §五 V2.4.0 **P4.3**;🔴 高危区:发版脚本)。

三组:
  **A. `activate_pack` 行为逐位不变**(重构成 `_activate_one` 薄壳之后的**正面锁**,
     §3.14-F 原文「单测正面锁死」)—— 这一组**先写再改**,任何一条红都说明抽函数
     抽歪了,⛔ 不许改这一组去迁就实现。
  **B. `activate_pack_set` 原子性**:一个事务 / 全过才切 / **任一失败整批回滚** /
     共享 `batch_id` / 旧新版本集合 / 持仓章程不参与。
  **C. 脚本层四道闸**:🔴 含**非原子必被闸 1 拒**的反证(那是"为什么非要原子"的
     物理理由,不是洁癖),以及 P4.7「只回滚策略包集合」那条绳真的走得通。

**全部用 `tmp_path` 隔离库**,不碰真实 `data/neckline.db`(`db_path` 全程显式传)。
`K8-V0.7` 从 git tag `v2.3.3` 取(那是生产此刻的骨架包),取不到就 skip —— ⛔ 不在
测试里手抄一份旧包。
"""

from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import activate_pack as activate_pack_script  # noqa: E402
import activate_pack_set as activate_set_script  # noqa: E402

from neckline.selection import pack  # noqa: E402
from neckline.strategy import brain  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_K8_FILE = _REPO_ROOT / "packs" / "K8-skeleton.json"
_C2, _Z2, _Y2 = (_REPO_ROOT / "packs" / f"{n}.json" for n in ("C2", "Z2", "Y2"))
_C1, _Z1, _Y1 = (_REPO_ROOT / "packs" / f"{n}.json" for n in ("C1", "Z1", "Y1"))
_NEW_FOUR = (_K8_FILE, _C2, _Z2, _Y2)


def _docs(files) -> List[Dict[str, Any]]:
    return [pack.load_pack_file(f) for f in files]


def _events(db: Path) -> List[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT pack_version, action, via, batch_id FROM selection_pack_activation_log "
            "ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _actives(db: Path) -> Dict[str, str]:
    return {p.line_code: p.pack_version for p in pack.list_packs(db_path=db) if p.is_active}


@pytest.fixture()
def old_skeleton_file(tmp_path: Path) -> Path:
    """生产此刻的骨架包 `K8-V0.7`(从 `v2.3.3` tag 取出,⛔ 不手抄)。"""
    try:
        blob = subprocess.run(
            ["git", "show", "v2.3.3:packs/K8-skeleton.json"],
            cwd=_REPO_ROOT, capture_output=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover
        pytest.skip("取不到 v2.3.3 tag 的 K8-skeleton.json(浅克隆/无 git)")
    f = tmp_path / "K8-V0.7.json"
    f.write_bytes(blob)
    assert json.loads(blob.decode())["manifest"]["pack_version"] == "K8-V0.7"
    return f


@pytest.fixture()
def prod_like_db(tmp_path: Path, old_skeleton_file: Path) -> Path:
    """与生产同构的现役集合:`K8-V0.7` + `C1`/`Z1`/`Y1`(逐包走单包激活入口建起来)。"""
    db = tmp_path / "prod_like.db"
    for f in (old_skeleton_file, _C1, _Z1, _Y1):
        doc = pack.load_pack_file(f)
        pack.activate_pack(doc["manifest"], doc["config"], via="cli", db_path=db)
    assert _actives(db) == {"V": "K8-V0.7", "C": "C1", "Z": "Z1", "Y": "Y1"}
    return db


# ══════════════════════════════════════════════════════════════════════════
# A. `activate_pack` 行为逐位不变(重构正面锁)
# ══════════════════════════════════════════════════════════════════════════

class TestActivatePackUnchanged:
    def test_first_activation_writes_exactly_one_event_with_null_batch_id(self, tmp_path: Path):
        """首次激活:一条 `activate` 事件,**`batch_id` 为 NULL**(单包激活不属于任何
        原子批次 —— ⛔ 不编一个假批次号,P4.3)。"""
        db = tmp_path / "n.db"
        doc = pack.load_pack_file(_C1)
        pack.activate_pack(doc["manifest"], doc["config"], via="cli", db_path=db)
        assert _events(db) == [("C1", "activate", "cli", None)]

    def test_switching_writes_deactivate_then_activate(self, tmp_path: Path):
        db = tmp_path / "n.db"
        for f in (_C1, _C2):
            doc = pack.load_pack_file(f)
            pack.activate_pack(doc["manifest"], doc["config"], via="cli", db_path=db)
        assert _events(db) == [
            ("C1", "activate", "cli", None),
            ("C1", "deactivate", "cli", None),
            ("C2", "activate", "cli", None),
        ]
        assert _actives(db) == {"C": "C2"}

    def test_reactivating_same_version_is_idempotent_noop(self, tmp_path: Path):
        db = tmp_path / "n.db"
        doc = pack.load_pack_file(_C1)
        for _ in range(3):
            pack.activate_pack(doc["manifest"], doc["config"], via="cli", db_path=db)
        assert len(_events(db)) == 1

    def test_tampered_same_version_still_raises_and_does_not_overwrite(self, tmp_path: Path):
        db = tmp_path / "n.db"
        doc = pack.load_pack_file(_C1)
        pack.activate_pack(doc["manifest"], doc["config"], via="cli", db_path=db)
        bad = copy.deepcopy(doc)
        bad["manifest"]["name"] = "TAMPERED"
        with pytest.raises(ValueError, match="已存在但内容不同"):
            pack.activate_pack(bad["manifest"], bad["config"], via="cli", db_path=db)
        assert pack.get_pack("C1", db_path=db).name == doc["manifest"]["name"]

    def test_invalid_doc_raises_before_touching_the_db_file(self, tmp_path: Path):
        """schema 不合法 → 在 `init_schema` **之前**就抛(重构后仍然:库文件不会被创建)。"""
        db = tmp_path / "never_created.db"
        with pytest.raises(ValueError, match="schema 校验未通过"):
            pack.activate_pack({"pack_version": "x"}, {}, via="cli", db_path=db)
        assert not db.exists()

    def test_activating_one_line_leaves_other_lines_untouched(self, tmp_path: Path):
        db = tmp_path / "n.db"
        for f in (_K8_FILE, _C1):
            doc = pack.load_pack_file(f)
            pack.activate_pack(doc["manifest"], doc["config"], via="cli", db_path=db)
        assert [e[:2] for e in _events(db)] == [("K8-V0.8", "activate"), ("C1", "activate")]
        assert pack.get_active_skeleton(db_path=db).pack_version == "K8-V0.8"


# ══════════════════════════════════════════════════════════════════════════
# B. `activate_pack_set` 原子性
# ══════════════════════════════════════════════════════════════════════════

class TestActivatePackSet:
    def test_four_lines_switch_together_sharing_one_batch_id(self, prod_like_db: Path):
        result = pack.activate_pack_set(_docs(_NEW_FOUR), db_path=prod_like_db)
        assert result.before == {"V": "K8-V0.7", "C": "C1", "Z": "Z1", "Y": "Y1"}
        assert result.after == {"V": "K8-V0.8", "C": "C2", "Z": "Z2", "Y": "Y2"}
        new_events = [e for e in _events(prod_like_db) if e[3] is not None]
        assert len(new_events) == 8                       # 四条 deactivate + 四条 activate
        assert {e[3] for e in new_events} == {result.batch_id}     # **共享一个批次号**
        assert {e[2] for e in new_events} == {"cli-set"}

    def test_line_switch_order_is_deterministic_v_c_z_y(self, prod_like_db: Path):
        """落库顺序按 `_LINE_CODES` 钉死,**与调用方给的顺序无关** —— 事件流 id 次序
        随调用方漂的话,审计时对不上。"""
        shuffled = _docs((_Y2, _C2, _K8_FILE, _Z2))
        result = pack.activate_pack_set(shuffled, db_path=prod_like_db)
        assert result.activated == ("K8-V0.8", "C2", "Z2", "Y2")

    def test_third_line_failure_rolls_the_whole_batch_back(self, prod_like_db: Path):
        """🔴 **P4.3 的核心判据**:第 3 条线(Z)在事务中途失败 → 四线**全部维持原值**,
        零新增事件,**前两包的 INSERT 也一并回滚**(⛔ 不留半激活状态)。

        造法:库里先登记一个**同版本号、内容不同**的 `Z2`(模拟有人早前用同一个版本号
        登记过别的内容)→ `_activate_one` 走 append-only 硬错。"""
        db = prod_like_db
        zdoc = pack.load_pack_file(_Z2)
        tampered = copy.deepcopy(zdoc)
        tampered["manifest"]["name"] = "被篡改的 Z2"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "INSERT INTO selection_packs (pack_version,name,engine_api_version,manifest_json,"
                "config_json,evidence_ref,is_active,created_at,activated_at,line_code) "
                "VALUES (?,?,?,?,?,?,0,?,NULL,?)",
                ("Z2", "被篡改的 Z2", 2,
                 json.dumps(tampered["manifest"], ensure_ascii=False, sort_keys=True),
                 json.dumps(tampered["config"], ensure_ascii=False, sort_keys=True),
                 "", "2026-08-12T00:00:00+00:00", "Z"),
            )
            conn.commit()
        finally:
            conn.close()

        before_actives, before_events = _actives(db), _events(db)
        with pytest.raises(ValueError, match="已存在但内容不同"):
            pack.activate_pack_set(_docs(_NEW_FOUR), db_path=db)

        assert _actives(db) == before_actives            # 四线全部维持原值
        assert _events(db) == before_events              # 零新增事件
        # 排在 Z 前面的 V / C 两包,连"登记新行"这一步都被回滚掉了。
        assert pack.get_pack("K8-V0.8", db_path=db) is None
        assert pack.get_pack("C2", db_path=db) is None

    def test_failure_leaves_no_half_activated_state_even_for_the_last_line(
        self, prod_like_db: Path, monkeypatch,
    ):
        """第 4 条线(最后一条)失败同样整批回滚 —— 前三条**已经写进事务**的切换全部撤销。"""
        db = prod_like_db
        real = pack._activate_one

        def boom(conn, manifest, config, *, via, batch_id=None):
            if pack.manifest_line_code(manifest) == "Y":
                raise sqlite3.OperationalError("模拟:落最后一包时数据库出错")
            return real(conn, manifest, config, via=via, batch_id=batch_id)

        monkeypatch.setattr(pack, "_activate_one", boom)
        before_actives, before_events = _actives(db), _events(db)
        with pytest.raises(sqlite3.OperationalError):
            pack.activate_pack_set(_docs(_NEW_FOUR), db_path=db)
        assert _actives(db) == before_actives
        assert _events(db) == before_events

    def test_already_active_set_is_idempotent_zero_events(self, prod_like_db: Path):
        pack.activate_pack_set(_docs(_NEW_FOUR), db_path=prod_like_db)
        n = len(_events(prod_like_db))
        again = pack.activate_pack_set(_docs(_NEW_FOUR), db_path=prod_like_db)
        assert again.before == again.after
        assert len(_events(prod_like_db)) == n           # 一条都没多写

    def test_same_line_twice_in_one_batch_is_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="出现两次"):
            pack.activate_pack_set(_docs((_C1, _C2)), db_path=tmp_path / "n.db")

    def test_empty_docs_fails_loud(self, tmp_path: Path):
        with pytest.raises(ValueError, match="docs 为空"):
            pack.activate_pack_set([], db_path=tmp_path / "n.db")

    def test_any_invalid_doc_blocks_the_whole_batch_before_writing(self, prod_like_db: Path):
        """「**四包全部通过校验后才切换**」:一个坏包让整批过不去,且**零写库**。"""
        docs = _docs(_NEW_FOUR)
        docs[2]["manifest"].pop("name")
        before_actives, before_events = _actives(prod_like_db), _events(prod_like_db)
        with pytest.raises(ValueError, match="四包全部通过才切换"):
            pack.activate_pack_set(docs, db_path=prod_like_db)
        assert _actives(prod_like_db) == before_actives and _events(prod_like_db) == before_events

    def test_charter_is_not_part_of_this_transaction(self, prod_like_db: Path):
        """🔴 **持仓章程不参与本事务**(P4.3 末条):批量激活前后 `strategy_versions`
        一行不动。结构性保证 = `pack.py` 全程不 import `strategy.brain`(见下一条)。"""
        db = prod_like_db
        brain.save_version("v-test", {"config": {"stop_pct": 0.05}}, "测试章程",
                           activate=True, db_path=db)
        before = brain.get_active(db_path=db)
        pack.activate_pack_set(_docs(_NEW_FOUR), db_path=db)
        after = brain.get_active(db_path=db)
        assert (after.version, after.rule, after.activated_at) == (
            before.version, before.rule, before.activated_at)

    def test_pack_module_never_imports_brain(self):
        """插槽边界的机器判据(既有纪律,本版沿用):`selection/pack.py` **不 import**
        `strategy.brain`,**SQL 里不出现** `strategy_versions`。

        ⚠ 判据刻意走 **AST + SQL 字面量**,⛔ 不是裸 `grep`:本模块的 docstring 里
        正大光明地写着「本模块全程不 import `neckline.strategy.brain`」——按词扫会把
        **这句自我说明**算成违规,逼下一个人把注释写得绕开自己要说的名字
        (`CLAUDE.md`「对自己的注释报警的闸门等于没有闸门」)。"""
        import ast

        path = _REPO_ROOT / "neckline" / "selection" / "pack.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: List[str] = []
        sqls: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else (
                    f.id if isinstance(f, ast.Name) else "")
                if name in {"execute", "executemany", "executescript"} and node.args:
                    a0 = node.args[0]
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        sqls.append(a0.value)
        assert not [m for m in imported if "strategy" in m], f"pack.py 竟然 import 了 {imported}"
        assert not [s for s in sqls if "strategy_versions" in s], "pack.py 的 SQL 碰了章程表"

    def test_batch_id_column_is_nullable_and_old_rows_stay_null(self, tmp_path: Path):
        """新增列是**增量兼容列**(P4.7:⛔ 不需要删列回滚):可空、无默认;
        单包激活写进去的行 `batch_id IS NULL`。"""
        db = tmp_path / "n.db"
        doc = pack.load_pack_file(_C1)
        pack.activate_pack(doc["manifest"], doc["config"], via="cli", db_path=db)
        conn = sqlite3.connect(str(db))
        try:
            info = {r[1]: r for r in conn.execute(
                "PRAGMA table_info(selection_pack_activation_log)")}
            assert "batch_id" in info
            _cid, _name, _type, notnull, dflt, _pk = info["batch_id"]
            assert notnull == 0 and dflt is None
            assert conn.execute(
                "SELECT COUNT(*) FROM selection_pack_activation_log WHERE batch_id IS NULL"
            ).fetchone()[0] == 1
        finally:
            conn.close()


# ══════════════════════════════════════════════════════════════════════════
# C. 脚本层(四道闸 + 非原子反证 + 回滚绳)
# ══════════════════════════════════════════════════════════════════════════

class TestActivatePackSetScript:
    def test_single_pack_activation_of_new_skeleton_is_rejected_by_gate1(
        self, prod_like_db: Path, capsys,
    ):
        """🔴 **「为什么必须原子」的物理理由,钉成机器判据**:`C1/Z1/Y1` 仍现役时
        单独激活 `K8-V0.8`,闸 1 的关口闸门模式对账**当场拒**(骨架包里那 11 条键写的
        是 `C2/Z2/Y2`)。⛔ 这不是 bug,别去"修"它。"""
        rc = activate_pack_script.run(_K8_FILE, prod_like_db, confirm=True)
        assert rc == 2
        err = capsys.readouterr().err
        assert "关口闸门模式对账表与现役引擎包不一致" in err
        assert _actives(prod_like_db) == {"V": "K8-V0.7", "C": "C1", "Z": "Z1", "Y": "Y1"}

    def test_dry_run_passes_all_gates_and_writes_nothing(self, prod_like_db: Path, capsys):
        import hashlib

        before = hashlib.md5(prod_like_db.read_bytes()).hexdigest()
        rc = activate_set_script.run(list(_NEW_FOUR), prod_like_db, confirm=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "闸 1 通过(前半)" in out and "闸 1 对账" in out and "闸 2 通过" in out
        assert "旧版本集合(现役):{V=K8-V0.7, C=C1, Z=Z1, Y=Y1}" in out
        assert "新版本集合(本批后):{V=K8-V0.8, C=C2, Z=Z2, Y=Y2}" in out
        assert "[dry-run]" in out
        assert hashlib.md5(prod_like_db.read_bytes()).hexdigest() == before

    def test_confirm_activates_all_four_atomically(self, prod_like_db: Path, capsys):
        rc = activate_set_script.run(list(_NEW_FOUR), prod_like_db, confirm=True)
        assert rc == 0
        assert "现役断言通过" in capsys.readouterr().out
        assert _actives(prod_like_db) == {"V": "K8-V0.8", "C": "C2", "Z": "Z2", "Y": "Y2"}

    def test_rollback_to_the_old_four_pack_set_works(
        self, prod_like_db: Path, old_skeleton_file: Path,
    ):
        """🔴 **P4.7「只回滚策略包集合」那条绳真的走得通**:新集合 → 旧集合再走一次
        原子激活即可(⛔ 不删任何已生成的审计记录,回滚也留痕)。"""
        assert activate_set_script.run(list(_NEW_FOUR), prod_like_db, confirm=True) == 0
        n_after_forward = len(_events(prod_like_db))
        rc = activate_set_script.run(
            [old_skeleton_file, _C1, _Z1, _Y1], prod_like_db, confirm=True)
        assert rc == 0
        assert _actives(prod_like_db) == {"V": "K8-V0.7", "C": "C1", "Z": "Z1", "Y": "Y1"}
        # 回滚是**追加事件**,不是抹掉历史。
        assert len(_events(prod_like_db)) == n_after_forward + 8

    def test_engine_api_incompatible_pack_is_rejected(self, tmp_path: Path, capsys):
        """闸 2 逐包照跑:批里混进一个 `engine_api_version=1` 的包 → 整批拒。"""
        bad = tmp_path / "legacy.json"
        doc = pack.load_pack_file(_C2)
        doc["manifest"]["engine_api_version"] = 1
        doc["manifest"]["pack_version"] = "C-legacy"
        bad.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        rc = activate_set_script.run([_K8_FILE, bad], tmp_path / "n.db", confirm=True)
        assert rc == 2
        assert "engine_api_version 不兼容" in capsys.readouterr().err

    def test_default_file_set_is_the_v240_four_lines(self):
        names = [f.name for f in activate_set_script._DEFAULT_PACK_FILES]
        assert names == ["K8-skeleton.json", "C2.json", "Z2.json", "Y2.json"]
        assert all(f.exists() for f in activate_set_script._DEFAULT_PACK_FILES)
