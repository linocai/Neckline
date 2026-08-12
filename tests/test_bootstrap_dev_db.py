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

    def test_refuses_the_authoritative_db(self, capsys):
        rc = bootstrap_dev_db.bootstrap(settings.db_path, None)
        assert rc == 2
        assert "settings.db_path" in capsys.readouterr().err

    def test_refuses_paths_under_repo_data_dir(self, capsys):
        rc = bootstrap_dev_db.bootstrap(_REPO_ROOT / "data" / "whatever.db", None)
        assert rc == 2
        assert "仓库 data/ 目录下" in capsys.readouterr().err

    def test_refuses_production_deploy_dir(self, capsys):
        rc = bootstrap_dev_db.bootstrap(Path("/opt/neckline/data/neckline.db"), None)
        assert rc == 2
        assert "/opt/neckline" in capsys.readouterr().err

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
