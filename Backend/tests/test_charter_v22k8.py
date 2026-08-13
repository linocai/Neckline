"""`v2.2-k8` 章程落库 + 切换器 + 退出侧全链守门(V2.2-⑤-A,🔴🔴 碰纪律章程 + 大脑版本表)。

体例照 `tests/test_charter_v133.py`。锁死的东西分四组:

  **A. 落行**:`seed_charter_v22k8` 从 **`v1.3.3` 行**按版本名读、复制、**只改退出侧四个
     字段**、其余逐字段相同、`activate=False`(现役不动);默认演练不写库;来源被改坏 →
     拒绝落行;🔴 **风险登记五条原样入 changelog(不精简、不软化)**。
  **B. 切换器四道闸**:白名单含 `v2.2-k8` + `_CORE_EXPECTATIONS` 同步(四个 `None` 是判据);
     🔴 **闸 2 的「纯入场侧 diff」窄豁免对 `v1.3.3 → v2.2-k8` 必须返 `False`**(§五 ⑤ 明写
     「本版改的全是退出侧,正是闸 2 当初要防的那一类」—— **正面钉死**);有 open 持仓硬拒。
  **C. `Optional[int]` 放宽的护栏**:`MomentumConfig.max_hold_days` 默认值仍是 3、旧 config
     加载吃默认、K1 退出行为逐位不变(⚠ 六年真回测那一层已随策略档案迁出本仓、恒 skip,
     见 §七 **P4-54**,故逻辑层护栏是本仓唯一还跑得动的那道)。
  **D. `None` 语义全链**:回落止盈与时间退出在 `None` 下**恒不触发**(回测侧 + 哨兵侧双侧),
     且**不炸**(`d >= None` 那类 TypeError 是本次最现实的翻车方式)。

⚠ `stop_pct=0.05` 的**值与唯一源地位一字不动**(§五 ⑤ 工程细节 2),本文件多处正面断言它。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "oneoff"))

import activate_charter  # noqa: E402
import charter_v1_3  # noqa: E402
import charter_v1_3_3  # noqa: E402
import seed_charter_v22k8  # noqa: E402
from neckline.sentinel import positions as pos_store  # noqa: E402
from neckline.strategy import brain  # noqa: E402
from tests.conftest import TEST_RULE_V1_CONFIG

pytestmark = pytest.mark.usefixtures("isolated_env")

# 本版**唯一**允许与 v1.3.3 不同的四个键(⛔ 第五个键出现就是回归)。
_CHANGED_KEYS = {
    "take_profit_retrace", "max_hold_days", "max_hold_days_profit",
    "time_exit_only_if_unprofitable",
}

# 「一字不动」的两组,点名正面锁死(§五 ⑤ config 逐字段改动表最后三行)。
_MUST_NOT_MOVE = ("stop_pct", "single_cap", "max_positions", "max_exposure_frac",
                  "forbid_high_elasticity")


def _seed_through_v133(db: Path) -> None:
    """K1 现役 → 跑真 `charter_v1_3` → 跑真 `charter_v1_3_3`(不手抄任何一版 config)。"""
    brain.save_version("K1", {"config": dict(TEST_RULE_V1_CONFIG)}, "测试:K1 基线",
                       activate=True, db_path=db)
    assert charter_v1_3.land_charter(db) == 0
    assert charter_v1_3_3.land_charter(db) == 0


# ======================================================================
#  A. 落行
# ======================================================================

class TestSeedLanding:
    def test_dry_run_does_not_write(self, isolated_env):
        """默认演练:打印 diff、**不落行**(一次性脚本体例:默认演练 / --confirm 才写)。"""
        db = isolated_env.db_path
        _seed_through_v133(db)
        assert seed_charter_v22k8.land_charter(db, confirm=False) == 0
        assert brain.get_version("v2.2-k8", db_path=db) is None

    def test_lands_only_four_exit_fields_changed(self, isolated_env):
        db = isolated_env.db_path
        _seed_through_v133(db)
        src = dict(brain.get_version("v1.3.3", db_path=db).rule["config"])

        assert seed_charter_v22k8.land_charter(db, confirm=True) == 0

        cfg = brain.get_version("v2.2-k8", db_path=db).rule["config"]
        assert cfg["take_profit_retrace"] is None      # 回落止盈 8% 退役
        assert cfg["max_hold_days"] is None            # 时间退出档退役
        assert cfg["max_hold_days_profit"] is None     # 浮盈硬上限退役
        assert cfg["time_exit_only_if_unprofitable"] is False   # 回落 K1 默认值,不留假旋钮
        assert set(cfg) == set(src)                    # 不多不少同一批键
        # 兜底:除那四个字段外,**一个键都不许有差**
        assert {k: v for k, v in cfg.items() if k not in _CHANGED_KEYS} == \
               {k: v for k, v in src.items() if k not in _CHANGED_KEYS}
        assert brain.get_version("v2.2-k8", db_path=db).rule["lineage"] == "K1"

    def test_stop_pct_and_position_discipline_are_untouched(self, isolated_env):
        """🔴 §五 ⑤ 的两条「一字不动」:`stop_pct=0.05`(值与唯一源地位)+ 仓位三仓制。"""
        db = isolated_env.db_path
        _seed_through_v133(db)
        src = dict(brain.get_version("v1.3.3", db_path=db).rule["config"])
        assert seed_charter_v22k8.land_charter(db, confirm=True) == 0
        cfg = brain.get_version("v2.2-k8", db_path=db).rule["config"]
        for k in _MUST_NOT_MOVE:
            assert cfg[k] == src[k], f"{k} 被改了 —— §五 ⑤ 明写它一字不动"
        assert cfg["stop_pct"] == 0.05

    def test_does_not_activate_and_leaves_source_untouched(self, isolated_env):
        db = isolated_env.db_path
        _seed_through_v133(db)
        src_before = brain.get_version("v1.3.3", db_path=db)
        active_before = brain.get_active(db_path=db).version
        assert seed_charter_v22k8.land_charter(db, confirm=True) == 0
        assert brain.get_version("v2.2-k8", db_path=db).is_active is False
        assert brain.get_active(db_path=db).version == active_before
        src_after = brain.get_version("v1.3.3", db_path=db)
        assert src_after.rule == src_before.rule and src_after.changelog == src_before.changelog

    def test_refuses_when_source_core_values_broken(self, isolated_env):
        """来源 v1.3.3 被改坏(有人把 stop_pct 动了)→ 拒绝落行(防从错行复制)。"""
        db = isolated_env.db_path
        _seed_through_v133(db)
        bad = dict(brain.get_version("v1.3.3", db_path=db).rule["config"])
        bad["stop_pct"] = 0.07
        brain.save_version("v1.3.3", {"config": bad, "lineage": "K1"}, "测试:改坏",
                           activate=False, db_path=db)
        assert seed_charter_v22k8.land_charter(db, confirm=True) == 2
        assert brain.get_version("v2.2-k8", db_path=db) is None

    def test_refuses_when_source_missing(self, isolated_env):
        db = isolated_env.db_path
        brain.save_version("K1", {"config": dict(TEST_RULE_V1_CONFIG)}, "测试", activate=True, db_path=db)
        assert seed_charter_v22k8.land_charter(db, confirm=True) == 1

    def test_risk_register_is_verbatim_in_changelog(self, isolated_env):
        """🔴 **风险登记五条原样入 changelog,⛔ 不得删、不得摘要**(裁定 #5「照 v1.3 先例
        全文风险登记不得删」)。逐条按其**不可替代的判据数字/结论**扫,防"改写成一句概括"。"""
        db = isolated_env.db_path
        _seed_through_v133(db)
        assert seed_charter_v22k8.land_charter(db, confirm=True) == 0
        log = brain.get_version("v2.2-k8", db_path=db).changelog
        for needle in (
            "13 笔破线未止损",          # §1.3 第一死因的真金数字
            "85%",                      # 占已实现亏损的比例
            "4–7 自然日",               # 唯一打平的持有桶
            "h9_exit_reform",           # 回落止盈 8% 的网格证据
            "winners_anatomy",          # 大赢家 88.6% 由 hold=5 了结
            "88.6%",
            "从未被回测验证",            # 新退出规则无背书
            "越线采纳",                  # 与 v1.3 同一先例的定性
            "95361.4988",               # 冻结基线不适用
            "staged",                   # 生效前提
        ):
            assert needle in log, f"风险登记里少了「{needle}」—— ⛔ 不得删、不得摘要"


# ======================================================================
#  B. 切换器四道闸
# ======================================================================

class TestSwitcherGates:
    def test_whitelist_and_core_expectations_are_in_sync(self):
        """闸 1 白名单里每个版本都必须有闸 3 核对项(结构性护栏,加白名单不许忘核对表)。"""
        assert "v2.2-k8" in activate_charter._ALLOWED_TARGETS
        assert set(activate_charter._ALLOWED_TARGETS) <= set(activate_charter._CORE_EXPECTATIONS)

    def test_core_expectations_pin_the_four_nones_and_the_untouched(self):
        """闸 3 核对项:四个 `None` 是本版判据;`stop_pct=0.05` 与仓位三件逐字重复(刻意)。"""
        exp = activate_charter._CORE_EXPECTATIONS["v2.2-k8"]
        assert exp["take_profit_retrace"] is None
        assert exp["max_hold_days"] is None
        assert exp["max_hold_days_profit"] is None
        assert exp["time_exit_only_if_unprofitable"] is False
        assert exp["stop_pct"] == 0.05
        assert exp["single_cap"] == 40000.0 and exp["max_positions"] == 3

    def test_eq_does_not_confuse_none_with_zero_or_false(self):
        """🔴 `_eq` 必须把 `None` 与 `0`/`False` 分开 —— 本版的核心判据就是那四个 `None`。"""
        assert activate_charter._eq(None, None) is True
        assert activate_charter._eq(None, 0) is False
        assert activate_charter._eq(None, False) is False
        assert activate_charter._eq(False, 0) is False      # 原实现会误判成相等
        assert activate_charter._eq(0.05, 0.05) is True

    def test_gate2_entry_side_exemption_is_false_for_this_diff(self, isolated_env):
        """🔴🔴 **§五 ⑤「守门单测正面钉死」那一条**:`v1.3.3 → v2.2-k8` 的 diff **全在退出侧**,
        正是闸 2 当初要防的那一类 → 窄豁免**必须返 `False`**。

        ⚠ plan 原文写的函数名是 `_is_entry_side_only_diff()`,仓库里的实名是
        `_exemption_verdict()`(a/b 两条件合判)—— 按**实名**钉,并把 (a)(b) 两侧各自的
        判定都断言到,免得将来只有一侧成立还以为闸还在。"""
        db = isolated_env.db_path
        _seed_through_v133(db)
        assert seed_charter_v22k8.land_charter(db, confirm=True) == 0
        old_cfg = dict(brain.get_version("v1.3.3", db_path=db).rule["config"])
        new_cfg = dict(brain.get_version("v2.2-k8", db_path=db).rule["config"])
        changed = activate_charter._diff_keys(old_cfg, new_cfg)
        assert set(changed) == _CHANGED_KEYS

        exempt, reasons = activate_charter._exemption_verdict(old_cfg, new_cfg, changed)
        assert exempt is False, "闸 2 窄豁免竟对一个纯退出侧 diff 成立 —— 那道闸等于没了"
        joined = " ".join(reasons)
        assert "✗" in joined
        assert "(a) diff 含入场侧白名单**之外**的字段" in joined     # 条件 (a) 不成立
        assert "(b) 在途仓位行为不变量被改动" in joined              # 条件 (b) 也不成立

    def test_gate2_rejects_with_open_positions(self, isolated_env):
        """闸 2 硬校验:有 open 持仓 → 拒绝激活(带不带 --confirm 都过不去)。
        ⚠ 这正是「⑤ 只做到演练全绿、就位待激活」的物理原因(§八 第 19 项)。"""
        db = isolated_env.db_path
        _seed_through_v133(db)
        assert seed_charter_v22k8.land_charter(db, confirm=True) == 0
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 0   # 先把现役挪到 v1.3.3
        pos_store.open_position("600519.SH", 10.0, 100, date(2026, 7, 20), db_path=db)
        assert activate_charter.activate(db, "v2.2-k8", confirm=True) == 1
        assert brain.get_active(db_path=db).version == "v1.3.3"             # 现役未动

    def test_dry_run_all_four_gates_green_then_activates(self, isolated_env):
        """无持仓时:先演练(不写库、现役不动)→ 再 --confirm 激活 → 现役唯一。"""
        db = isolated_env.db_path
        _seed_through_v133(db)
        assert seed_charter_v22k8.land_charter(db, confirm=True) == 0
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 0
        assert activate_charter.activate(db, "v2.2-k8", confirm=False) == 0  # 演练
        assert brain.get_active(db_path=db).version == "v1.3.3"
        assert activate_charter.activate(db, "v2.2-k8", confirm=True) == 0
        assert brain.get_active(db_path=db).version == "v2.2-k8"
        assert [v.version for v in brain.list_versions(db_path=db) if v.is_active] == ["v2.2-k8"]

    def test_rollback_target_v133_still_allowed(self, isolated_env):
        """回滚目标 = `v1.3.3`,仍在白名单、仍走四道闸(SOP:archive/交接与日志/SOP_章程回滚_20260730.md)。"""
        db = isolated_env.db_path
        _seed_through_v133(db)
        assert seed_charter_v22k8.land_charter(db, confirm=True) == 0
        assert activate_charter.activate(db, "v2.2-k8", confirm=True) == 0
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 0
        assert brain.get_active(db_path=db).version == "v1.3.3"

    def test_default_target_is_still_v133_not_the_high_risk_one(self):
        """高危目标必须显式 `--target` 打出来,⛔ 不许手滑默认过去。"""
        assert activate_charter._TARGET_VERSION == "v1.3.3"


# ======================================================================
#  C. `Optional[int]` 放宽的护栏(§3.11-E)
# ======================================================================

class TestMaxHoldDaysWidening:
    def test_default_is_still_three_and_type_is_optional(self):
        """**默认值一字未动** → 旧 config 加载吃默认 3,K1 回测逐位不变(§3.11-E 硬要求)。"""
        import typing

        from neckline.strategy.momentum_config import MomentumConfig

        assert MomentumConfig().max_hold_days == 3
        hints = typing.get_type_hints(MomentumConfig)
        assert hints["max_hold_days"] == typing.Optional[int]

    def test_no_sentinel_value_anywhere(self):
        """⛔ **否决用哨兵位 `9999`**(§3.11-E)—— 全仓不许出现它当"不设时间退出"用。"""
        root = Path(__file__).resolve().parent.parent
        hits = []
        for d in ("neckline", "scripts"):
            for p in (root / d).rglob("*.py"):
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if "9999" in line and "max_hold" in line:
                        hits.append((str(p.relative_to(root)), i))
        assert not hits, f"出现疑似时间退出哨兵位:{hits}(§3.11-E 已否决 9999)"

# ======================================================================
#  D. `None` 语义全链:恒不触发、且不炸
# ======================================================================

def _k8_cfg(**over):
    from neckline.strategy.momentum_config import MomentumConfig

    base = dict(stop_pct=0.05, take_profit_retrace=None, max_hold_days=None,
                max_hold_days_profit=None, time_exit_only_if_unprofitable=False)
    base.update(over)
    return MomentumConfig(**base)


class TestNoTimeExitNeverFires:
    @pytest.mark.parametrize("d", [0, 1, 5, 15, 99])
    def test_sentinel_classify_and_resolve_return_holding(self, d):
        """哨兵侧两个判定器恒 `(HOLDING, None)`,**且不抛** —— `d >= None` 是最现实的翻车点。"""
        from neckline.sentinel import precall

        assert precall.classify_time_exit(d, _k8_cfg(), net_float=1000.0) == (precall.HOLDING, None)
        assert precall.resolve_time_exit(d, _k8_cfg()) == (precall.HOLDING, None)
        # 第五态(停牌挂起)也不许冒出来:没有判定点就无所谓"挂起"
        assert precall.resolve_time_exit(d, _k8_cfg(), None, data_unavailable=True) == \
            (precall.HOLDING, None)

    def test_has_time_exit_clause_predicate(self):
        from neckline.sentinel import precall

        assert precall.has_time_exit_clause(_k8_cfg()) is False
        assert precall.is_two_tier_time_exit(_k8_cfg(max_hold_days_profit=15,
                                                     time_exit_only_if_unprofitable=True)) is False
        assert precall.has_time_exit_clause(
            _k8_cfg(max_hold_days=5, max_hold_days_profit=15,
                    time_exit_only_if_unprofitable=True)) is True

    def test_scan_time_exits_returns_empty(self, isolated_env):
        """扫描器恒返空表 → 零看板事件、零 D5 推送(⑤ 验收「当晚哨兵零时间退出判定」)。"""
        from neckline.sentinel import precall

        pid = pos_store.open_position("600519.SH", 10.0, 100, date(2026, 7, 20),
                                      db_path=isolated_env.db_path)
        positions = pos_store.load_open_positions(db_path=isolated_env.db_path)
        assert positions and positions[0].id == pid
        assert precall.scan_time_exits(positions, date(2026, 8, 10), _k8_cfg()) == []
        assert precall.scan_d5_exits(positions, date(2026, 8, 10), None) == []

    def test_take_profit_never_fires_when_none(self):
        """回落止盈在 `None` 下恒不触发(哨兵侧;既有 `Optional` 判空路径,代码零改动)。"""
        from neckline.sentinel.holding import check_take_profit
        from neckline.sentinel.quotes import Quote

        q = Quote(code="X", name="", price=5.0, pre_close=0.0, open=0.0, high=0.0,
                  low=0.0, volume=0.0, amount=0.0, ts="", source="t")
        pos = pos_store.Position(id=1, ts_code="X", buy_price=10.0, qty=100,
                                 buy_date="20260720", status="open",
                                 sell_price=None, sell_date=None, note=None)
        assert check_take_profit(pos, q, 20.0, None) is None

# ======================================================================
#  E. 16:35 EOD 落库:`max_hold_effective=None` 不炸、如实落 NULL(计划外补口,见完工报告)
# ======================================================================

class _Item:
    """duck-typed `HoldingK4Item`(只喂 `save_holding_eod_checks` 需要的字段)。"""
    def __init__(self, pid: int, eff):
        self.position_id, self.d_count, self.net_float = pid, 3, -10.0
        self.time_exit_state, self.max_hold_effective = "holding", eff
        self.has_strong = self.scenario_review = False
        self.has_data = True
        self.time_exit_locked_state = self.time_exit_locked_date = None
        self.time_exit_locked_net_float = None

    def hits_public(self):
        return []


class TestEodCheckRowUnderK8:
    """🔴 **计划外补口**:`holding_eod_check.max_hold_effective` 原是 `NOT NULL DEFAULT 5`,
    `v2.2-k8` 下 16:35 体检算出的有效硬上限是 `None` → 写库 `IntegrityError` → **整份报告
    崩掉**(§五 ⑤ 涉及文件清单里没有这一处,planner 未预见)。处置见 `db.py::
    _relax_holding_eod_check_notnull` 与 `holding_store.save_holding_eod_checks`。"""

    def test_new_db_column_is_nullable(self, isolated_env):
        from neckline.db import connection, init_schema

        init_schema(isolated_env.db_path)
        with connection(isolated_env.db_path) as conn:
            info = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(holding_eod_check)")}
        assert info["max_hold_effective"] == 0, "新库该列应可空(V2.2-⑤)"

    def test_none_lands_as_null_not_five(self, isolated_env):
        """⛔ 不拿 5 顶上:`('holding', 5)` 在 K1 的 D2 与 K8 的任何一天下长得一模一样。"""
        from neckline.db import connection
        from neckline.report.holding_store import save_holding_eod_checks

        save_holding_eod_checks(date(2026, 8, 10), [_Item(1, None)], db_path=isolated_env.db_path)
        with connection(isolated_env.db_path) as conn:
            row = conn.execute(
                "SELECT max_hold_effective, time_exit_state FROM holding_eod_check"
            ).fetchone()
        assert row == (None, "holding")

    def test_legacy_not_null_table_is_migrated_in_place(self, isolated_env):
        """老库(列仍 `NOT NULL`)→ `init_schema` **原子放宽**,历史行逐字段保留。"""
        from neckline.db import connection, init_schema
        from neckline.report.holding_store import save_holding_eod_checks

        db = isolated_env.db_path
        init_schema(db)
        with connection(db) as conn:                      # 造一张 V2.2 之前形状的老表
            conn.execute("DROP TABLE holding_eod_check")
            conn.execute("""
                CREATE TABLE holding_eod_check (
                    position_id INTEGER NOT NULL, trade_date TEXT NOT NULL,
                    d_count INTEGER NOT NULL DEFAULT 1, net_float REAL,
                    time_exit_state TEXT NOT NULL DEFAULT 'holding',
                    max_hold_effective INTEGER NOT NULL DEFAULT 5,
                    k4_hits_json TEXT NOT NULL DEFAULT '[]',
                    has_strong INTEGER NOT NULL DEFAULT 0,
                    scenario_review INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, time_exit_locked_state TEXT,
                    time_exit_locked_date TEXT, time_exit_locked_net_float REAL,
                    data_unavailable INTEGER,
                    PRIMARY KEY (position_id, trade_date))
            """)
            conn.execute(
                "INSERT INTO holding_eod_check (position_id, trade_date, d_count, "
                "time_exit_state, max_hold_effective, created_at) VALUES (7,'20260807',5,"
                "'time_exit_next_day',5,'2026-08-07T08:00:00+00:00')")

        init_schema(db)                                    # ← 迁移在这里发生

        with connection(db) as conn:
            info = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(holding_eod_check)")}
            hist = conn.execute(
                "SELECT position_id, trade_date, d_count, time_exit_state, max_hold_effective "
                "FROM holding_eod_check").fetchall()
        assert info["max_hold_effective"] == 0             # 已放宽
        assert hist == [(7, "20260807", 5, "time_exit_next_day", 5)]   # 历史行逐字段保留

        save_holding_eod_checks(date(2026, 8, 10), [_Item(8, None)], db_path=db)
        with connection(db) as conn:
            assert conn.execute(
                "SELECT max_hold_effective FROM holding_eod_check WHERE position_id=8"
            ).fetchone() == (None,)

    def test_migration_is_idempotent(self, isolated_env):
        """跑几次都一样(`init_schema` 被所有入口调用,不许每次重建表)。"""
        from neckline.db import connection, init_schema
        from neckline.report.holding_store import save_holding_eod_checks

        db = isolated_env.db_path
        save_holding_eod_checks(date(2026, 8, 10), [_Item(1, None)], db_path=db)
        for _ in range(3):
            init_schema(db)
        with connection(db) as conn:
            assert conn.execute("SELECT count(*) FROM holding_eod_check").fetchone()[0] == 1
            assert not conn.execute(
                "SELECT name FROM sqlite_master WHERE name='holding_eod_check__v22'").fetchall()
