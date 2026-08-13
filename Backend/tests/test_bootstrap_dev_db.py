"""`scripts/bootstrap_dev_db.py` 单测(plan §五 V2.4.0 **P4.2**:开发库可复现)。

**全部用 `tmp_path`**(P4.2 原文「测试全部使用临时目录」)—— 参考库也是临时造的,
⛔ 一处都不碰真实 `data/neckline.db`。

四组判据:
  A. **护栏**:必须显式 `--db-path`;生产 / 仓库 `data/` / `/opt/neckline` 一律拒。
  B. **只读参考表白名单**:业务表与凭据表一张都不许被拷(⛔ 黑名单反向断言)。
  C. **结果**:四条 pack line = `K8-V0.8`/`C2`/`Z2`/`Y2`,章程 = `v2.3-k8`。
  D. **幂等**:重复跑结果相同、零新增激活事件。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "oneoff"))

import bootstrap_dev_db  # noqa: E402

from neckline.config import settings  # noqa: E402
from neckline.db import connection, init_schema  # noqa: E402
from neckline.selection import pack  # noqa: E402
from neckline.strategy import brain  # noqa: E402
from tests.conftest import TEST_RULE_V1_CONFIG  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def reference_db(tmp_path: Path) -> Path:
    """临时"参考库":只有 K1 章程行 + 几行参考表 + **一张凭据表和一张业务表**
    (后两张是给 B 组当诱饵的 —— 它们必须**没有**被拷过去)。"""
    db = tmp_path / "reference.db"
    init_schema(db)
    brain.save_version("K1", {"config": dict(TEST_RULE_V1_CONFIG)}, "测试:K1 基线",
                       activate=True, db_path=db)
    with connection(db) as conn:
        conn.execute("INSERT OR REPLACE INTO trade_cal (exchange, cal_date, is_open) "
                     "VALUES ('SSE','20260812',1)")
        conn.execute(
            "INSERT OR REPLACE INTO stock_basic "
            "(ts_code, name, industry, list_date, market, list_status) "
            "VALUES ('000001.SZ','平安银行','银行','19910403','主板','L')")
        conn.execute(
            "INSERT OR REPLACE INTO llm_providers "
            "(name, base_url, model, api_key, has_web_search, enabled, created_at, updated_at) "
            "VALUES ('glm','https://x/chat/completions','glm-4','sk-SECRET-DO-NOT-COPY',"
            "0,1,'2026-08-12T00:00:00+00:00','2026-08-12T00:00:00+00:00')")
        conn.execute(
            "INSERT OR REPLACE INTO positions "
            "(ts_code, buy_date, buy_price, qty, status, created_at, updated_at) "
            "VALUES ('600519.SH','2026-08-01',1500.0,100,'open',"
            "'2026-08-12T00:00:00+00:00','2026-08-12T00:00:00+00:00')")
    return db


# ══════════════════════════════════════════════════════════════════════════
# A. 护栏
# ══════════════════════════════════════════════════════════════════════════

class TestGuardrails:
    def test_db_path_is_required_with_no_default(self, monkeypatch):
        """⛔ **没有默认值** —— 一个"默认打哪个库"的 bootstrap 早晚有人不带参数跑一次。"""
        monkeypatch.setattr(sys, "argv", ["bootstrap_dev_db.py"])
        with pytest.raises(SystemExit) as e:
            bootstrap_dev_db.main()
        assert e.value.code != 0

    def test_refuses_the_authoritative_db(self):
        """🔴 **V2.4.0 复审 🟡-2:⛔ 绝不拿真库当实弹靶子**。

        旧写法是 `bootstrap(settings.db_path, None)` —— **真的把 bootstrap 瞄准
        `data/neckline.db`**,靠被测的那道护栏自己拦住。护栏一旦被削弱或调序,
        跑一次 `pytest` 就会在权威库上 `init_schema` + `activate_pack_set(K8-V0.8,
        C2, Z2, Y2)` + 激活章程 `v2.3-k8` = **一次静默的真激活**,正是本次发版明令
        推迟的那件事。「测试自己就是那颗雷」和"改不改代码"无关,越早拆越好。

        ✅ 改成断**纯判据函数** `_is_protected_db`(零副作用、不开文件、不写一个字节),
        再由下面 `test_protected_paths_never_reach_the_writing_path` 用一个
        **不存在但被同一条护栏拦住**的路径去走真正的 `bootstrap()` 分支。
        """
        reason = bootstrap_dev_db._is_protected_db(settings.db_path)
        assert reason is not None and "settings.db_path" in reason

    def test_refuses_paths_under_repo_data_dir(self, capsys):
        rc = bootstrap_dev_db.bootstrap(_REPO_ROOT / "data" / "whatever.db", None)
        assert rc == 2
        assert "仓库 data/ 目录下" in capsys.readouterr().err

    def test_refuses_production_deploy_dir(self, capsys):
        rc = bootstrap_dev_db.bootstrap(Path("/opt/neckline/data/neckline.db"), None)
        assert rc == 2
        assert "/opt/neckline" in capsys.readouterr().err

    def test_protected_paths_never_reach_the_writing_path(self, tmp_path: Path, capsys):
        """`bootstrap()` 的**拒绝分支**照样要被真的走一遍(🟡-2 改法的另一半)——
        但靶子是一个**长得像权威库、却不是它**的路径:把模块里那份 `settings` 换成
        替身(`Settings` 是 frozen dataclass,⛔ 不能 `setattr` 单字段 —— 这是
        `conftest.py` 文件头写明的既有姿势),`db_path` 指到 `tmp_path` 下一个
        **不存在**的文件。护栏判据是路径相等,与文件在不在无关,照样命中;而
        **万一护栏失效,写坏的也只是 tmp**。"""
        fake = tmp_path / "looks-like-prod" / "neckline.db"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(bootstrap_dev_db, "settings", SimpleNamespace(db_path=fake))
            rc = bootstrap_dev_db.bootstrap(fake, None)
        assert rc == 2
        assert "settings.db_path" in capsys.readouterr().err
        assert not fake.exists(), "拒绝必须发生在创建文件之前"

    def test_temp_path_is_allowed(self, tmp_path: Path):
        assert bootstrap_dev_db._is_protected_db(tmp_path / "dev.db") is None


# ══════════════════════════════════════════════════════════════════════════
# B. 只读参考表白名单(⛔ 不复制业务数据与凭据)
# ══════════════════════════════════════════════════════════════════════════

class TestReferenceTableWhitelist:
    def test_whitelist_is_exactly_the_four_read_only_tables(self):
        assert bootstrap_dev_db._REFERENCE_TABLES == (
            "trade_cal", "strategy_versions", "stock_basic", "namechange")

    def test_forbidden_tables_are_disjoint_from_the_whitelist(self):
        assert not (set(bootstrap_dev_db._FORBIDDEN_TABLES)
                    & set(bootstrap_dev_db._REFERENCE_TABLES))

    def test_credentials_and_business_rows_are_not_copied(self, tmp_path: Path, reference_db: Path):
        """🔴 反向断言:参考库里那把假 api_key 与那笔假持仓,**一行都不许出现在开发库里**。"""
        dev = tmp_path / "dev.db"
        assert bootstrap_dev_db.bootstrap(dev, reference_db) == 0
        conn = sqlite3.connect(str(dev))
        try:
            assert conn.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
            leaked = conn.execute(
                "SELECT COUNT(*) FROM llm_providers WHERE api_key LIKE 'sk-SECRET%'").fetchone()[0]
            assert leaked == 0
            # 白名单那两张确实拷过来了(否则上面两条会因为"什么都没拷"而假绿)。
            assert conn.execute("SELECT COUNT(*) FROM trade_cal").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0] == 1
        finally:
            conn.close()

    def test_reference_db_is_opened_read_only(self, tmp_path: Path, reference_db: Path):
        """参考库指向的往往正是权威库 —— bootstrap 跑完它必须**逐字节不变**。"""
        import hashlib

        before = hashlib.md5(reference_db.read_bytes()).hexdigest()
        assert bootstrap_dev_db.bootstrap(tmp_path / "dev.db", reference_db) == 0
        assert hashlib.md5(reference_db.read_bytes()).hexdigest() == before


# ══════════════════════════════════════════════════════════════════════════
# C. 结果:v2.4.0 现役版本集合
# ══════════════════════════════════════════════════════════════════════════

class TestRebuildsTheV240ActiveSet:
    def test_four_pack_lines_and_charter(self, tmp_path: Path, reference_db: Path, capsys):
        dev = tmp_path / "dev.db"
        assert bootstrap_dev_db.bootstrap(dev, reference_db) == 0
        actives = {p.line_code: p.pack_version
                   for p in pack.list_packs(db_path=dev) if p.is_active}
        assert actives == {"V": "K8-V0.8", "C": "C2", "Z": "Z2", "Y": "Y2"}
        assert brain.get_active(db_path=dev).version == "v2.3-k8"
        out = capsys.readouterr().out
        for line in ("V", "C", "Z", "Y"):
            assert f"pack line {line}:" in out           # P4.2 要求输出四条 pack line
        assert "纪律章程:v2.3-k8" in out

    def test_charter_config_is_derived_never_hand_copied(self, tmp_path: Path, reference_db: Path):
        """🔴 章程数值的唯一源仍是 `strategy_versions` 行:`v2.3-k8` 的 config 由既有落行
        脚本从 K1 一路派生而来 —— 脚本本体里**不许出现**那些数字。"""
        dev = tmp_path / "dev.db"
        assert bootstrap_dev_db.bootstrap(dev, reference_db) == 0
        cfg = brain.get_active(db_path=dev).rule["config"]
        assert cfg["loss_warning_pct"] == 0.05 and cfg["loss_warning_action"] == "review"
        assert cfg["stop_pct"] == 0.05 and cfg["take_profit_retrace"] is None
        src = (_REPO_ROOT / "scripts" / "bootstrap_dev_db.py").read_text(encoding="utf-8")
        for forbidden in ("loss_warning_pct", "take_profit_retrace", "single_cap", "0.05"):
            assert forbidden not in src, f"bootstrap 脚本里手抄了章程字段 {forbidden}"

    def test_without_reference_db_packs_land_but_charter_fails_loud(self, tmp_path: Path, capsys):
        """不给 `--reference-db` → 只建空 schema + **四线包照落**(包文件在仓库里,永远
        拿得到);章程没有祖先行可派生 → **非零退出 + 说清怎么办**,⛔ 不静默造一个
        默认章程(那就是给钉死的领域常量造第二份事实源)。"""
        dev = tmp_path / "bare.db"
        rc = bootstrap_dev_db.bootstrap(dev, None)
        assert rc == 1
        assert brain.get_active(db_path=dev) is None
        assert "--reference-db" in capsys.readouterr().err
        actives = {p.line_code: p.pack_version
                   for p in pack.list_packs(db_path=dev) if p.is_active}
        assert actives == {"V": "K8-V0.8", "C": "C2", "Z": "Z2", "Y": "Y2"}

    def test_missing_reference_db_file_fails_loud(self, tmp_path: Path, capsys):
        rc = bootstrap_dev_db.bootstrap(tmp_path / "dev.db", tmp_path / "nope.db")
        assert rc == 2
        assert "参考库不存在" in capsys.readouterr().err


# ══════════════════════════════════════════════════════════════════════════
# D. 幂等
# ══════════════════════════════════════════════════════════════════════════

def test_running_twice_is_idempotent(tmp_path: Path, reference_db: Path):
    dev = tmp_path / "dev.db"
    assert bootstrap_dev_db.bootstrap(dev, reference_db) == 0
    conn = sqlite3.connect(str(dev))
    try:
        n_pack_events = conn.execute(
            "SELECT COUNT(*) FROM selection_pack_activation_log").fetchone()[0]
        n_charter_events = conn.execute(
            "SELECT COUNT(*) FROM strategy_activation_log").fetchone()[0]
    finally:
        conn.close()

    assert bootstrap_dev_db.bootstrap(dev, reference_db) == 0
    conn = sqlite3.connect(str(dev))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM selection_pack_activation_log").fetchone()[0] == n_pack_events
        assert conn.execute(
            "SELECT COUNT(*) FROM strategy_activation_log").fetchone()[0] == n_charter_events
    finally:
        conn.close()
    assert brain.get_active(db_path=dev).version == "v2.3-k8"


def test_running_repeatedly_never_leaves_two_active_charters(tmp_path: Path, reference_db: Path):
    """🔴 **V2.4.0 复审 🟡-1 的机器判据 —— 断的是「裸计数」,⛔ 不是 `get_active()`**。

    病:`_copy_reference_tables` 用 `INSERT OR REPLACE` 把 `strategy_versions` 连
    `is_active` 一起拷过来 → 第二次跑就有**两行现役**(第一次跑激活的 `v2.3-k8` +
    参考库带来的 `K1`);`activate_charter` 见「已是现役」早退,`brain.get_active()`
    的 `ORDER BY created_at DESC LIMIT 1` 把它遮住,`strategy_versions` 又没有
    `selection_packs` 那种部分唯一索引 —— 库层静默接受。
    结果:「今天用的是哪版章程」= 「看 `created_at` 谁大」。

    ⚠ 原守门 `test_running_twice_is_idempotent` 断的是 `get_active().version` 与事件数,
    **恰好是被那个 `LIMIT 1` 遮住的两样**,所以它当时全绿。这条改断裸计数。"""
    dev = tmp_path / "dev.db"
    for run in (1, 2, 3):
        assert bootstrap_dev_db.bootstrap(dev, reference_db) == 0, f"第 {run} 次跑失败"
        conn = sqlite3.connect(str(dev))
        try:
            rows = [r[0] for r in conn.execute(
                "SELECT version FROM strategy_versions WHERE is_active=1 ORDER BY version")]
        finally:
            conn.close()
        assert rows == ["v2.3-k8"], f"第 {run} 次跑之后现役行 = {rows}(必须恰好一行 v2.3-k8)"


def test_self_check_fails_loud_when_two_rows_are_active(tmp_path: Path, reference_db: Path, capsys):
    """**反向探针**:人为造出两行现役 → 那条裸计数自检必须**当场非零退出**。

    没有这条,上面那条绿了也证明不了自检在工作(它可能只是因为归一化恰好生效)。
    ⚠ 探针要把**上游两道自愈**都掐掉才测得到自检本身:① 归一化只在带
    `--reference-db` 时跑 → 这次不带;② `activate_charter.activate` 会
    `activate_version` 顺手把标记收敛掉 → 用桩换成"什么都不做的成功"。
    剩下的唯一一道就是自检 —— 它必须红。"""
    dev = tmp_path / "dev.db"
    assert bootstrap_dev_db.bootstrap(dev, reference_db) == 0
    conn = sqlite3.connect(str(dev))
    try:
        conn.execute("UPDATE strategy_versions SET is_active=1 WHERE version='K1'")
        conn.commit()
    finally:
        conn.close()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bootstrap_dev_db.activate_charter, "activate",
                   lambda *a, **k: 0)      # 掐掉自愈,只留自检
        rc = bootstrap_dev_db.bootstrap(dev, None)
    assert rc == 3
    err = capsys.readouterr().err
    assert "现役行有 2 个" in err and "恰好 1" in err
