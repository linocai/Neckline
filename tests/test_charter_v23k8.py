"""`v2.3-k8` 退出字段语义换血 + 三条守门(V2.3.2-⑤,🔴🔴 碰纪律章程 + 大脑版本表)。

体例照 `tests/test_charter_v22k8.py`(那一版是"退出侧四个字段退役",这一版是"对外语义
两个字段新增")。锁死的东西分五组:

  **A. 落行**:`seed_charter_v23k8` 从 **`v2.2-k8` 行**按版本名读、复制、**只加两个字段**、
     其余逐字段相同、`activate=False`(现役不动);默认演练不写库;来源被改坏 / 来源里
     已经有这两个字段 → 拒绝落行;🔴 **风险登记原样入 changelog(不精简、不软化)**。
  **B. 切换器四道闸**:白名单含 `v2.3-k8` + `_CORE_EXPECTATIONS` 同步(两个新值是判据);
     🔴 **闸 2 的「纯入场侧 diff」窄豁免对 `v2.2-k8 → v2.3-k8` 必须返 `False`**(§五 ⑤-C
     明写「窄豁免对它不成立」—— **正面钉死**);有 open 持仓硬拒;回滚目标 `v2.2-k8` 仍可激活。
  **C. `stop_is_advisory` 三级判据**:config 优先 → `db_path` 显式给才查库 → 版本白名单
     (**给老行用**)。`v2.3-k8` 走 config 路径为 `True`,`v1.3.3` 仍 `False`。
  **D. 三条守门(§五 ⑤-B 第 5 条)**:
     ① `stop_pct` 消费方**白名单**,白名单外新增消费方即红;
     ② 全仓**零自动卖出路径**(AST 扫下单 / 委托类调用 + 交易 SDK import);
     ③ `v2.3-k8` 的 config 与 `v2.2-k8` **只差这两个字段**(逐字段对拍)。
  **E. 契约只加不删**:卡的口径指纹同时有 `stop_pct` 与 `loss_warning_*`,且 `stop_pct`
     仍是 0.05;客户端 DTO 两键都在。

🔴 **`stop_pct` 的值与唯一源地位一字不动**(K8.md §十九:「兼容字段 `stop_pct` 只保留
历史读取能力,执行器不得用其触发自动卖出」)—— 本文件多处正面断言它。
"""

from __future__ import annotations

import ast
import re
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
import seed_charter_v23k8  # noqa: E402
from neckline.sentinel import positions as pos_store  # noqa: E402
from neckline.strategy import brain  # noqa: E402
from tests.conftest import TEST_RULE_V1_CONFIG

pytestmark = pytest.mark.usefixtures("isolated_env")

_ROOT = Path(__file__).resolve().parent.parent

# 本版**唯一**允许与 v2.2-k8 不同的两个键(⛔ 第三个键出现就是回归)。
_NEW_KEYS = {"loss_warning_pct", "loss_warning_action"}

# 「一字不动」的两组,点名正面锁死(§五 ⑤-B / 〇b 红线 7)。
_MUST_NOT_MOVE = ("stop_pct", "take_profit_retrace", "max_hold_days", "max_hold_days_profit",
                  "time_exit_only_if_unprofitable", "single_cap", "max_positions",
                  "max_exposure_frac", "forbid_high_elasticity")


def _seed_through_v22k8(db: Path) -> None:
    """K1 现役 → 真 `charter_v1_3` → 真 `charter_v1_3_3` → 真 `seed_charter_v22k8`
    (不手抄任何一版 config)。"""
    brain.save_version("K1", {"config": dict(TEST_RULE_V1_CONFIG)}, "测试:K1 基线",
                       activate=True, db_path=db)
    assert charter_v1_3.land_charter(db) == 0
    assert charter_v1_3_3.land_charter(db) == 0
    assert seed_charter_v22k8.land_charter(db, confirm=True) == 0


# ======================================================================
#  A. 落行
# ======================================================================

class TestSeedLanding:
    def test_dry_run_does_not_write(self, isolated_env):
        """默认演练:打印 diff、**不落行**(一次性脚本体例:默认演练 / --confirm 才写)。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert seed_charter_v23k8.land_charter(db, confirm=False) == 0
        assert brain.get_version("v2.3-k8", db_path=db) is None

    def test_lands_only_the_two_new_fields(self, isolated_env):
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        src = dict(brain.get_version("v2.2-k8", db_path=db).rule["config"])

        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0

        cfg = brain.get_version("v2.3-k8", db_path=db).rule["config"]
        assert cfg["loss_warning_pct"] == 0.05            # K8.md §十九 逐字给的数
        assert cfg["loss_warning_action"] == "review"     # K8.md §十九 逐字给的值
        assert set(cfg) - set(src) == _NEW_KEYS           # **只多这两个键**
        assert not set(src) - set(cfg)                    # 一个键都没少
        # 兜底:除那两个新键外,**一个键都不许有差**
        assert {k: v for k, v in cfg.items() if k not in _NEW_KEYS} == src
        assert brain.get_version("v2.3-k8", db_path=db).rule["lineage"] == "K1"

    def test_stop_pct_and_everything_else_are_untouched(self, isolated_env):
        """🔴 §五 ⑤ 的「一字不动」:`stop_pct=0.05`(值与唯一源地位)+ 退出侧四档 + 仓位三件。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        src = dict(brain.get_version("v2.2-k8", db_path=db).rule["config"])
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        cfg = brain.get_version("v2.3-k8", db_path=db).rule["config"]
        for k in _MUST_NOT_MOVE:
            assert cfg[k] == src[k], f"{k} 被改了 —— §五 ⑤ 明写它一字不动"
        assert cfg["stop_pct"] == 0.05

    def test_does_not_activate_and_leaves_source_untouched(self, isolated_env):
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        src_before = brain.get_version("v2.2-k8", db_path=db)
        active_before = brain.get_active(db_path=db).version
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        assert brain.get_version("v2.3-k8", db_path=db).is_active is False
        assert brain.get_active(db_path=db).version == active_before
        src_after = brain.get_version("v2.2-k8", db_path=db)
        assert src_after.rule == src_before.rule and src_after.changelog == src_before.changelog

    def test_refuses_when_source_core_values_broken(self, isolated_env):
        """来源 v2.2-k8 被改坏(有人把 stop_pct 动了)→ 拒绝落行(防从错行复制)。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        bad = dict(brain.get_version("v2.2-k8", db_path=db).rule["config"])
        bad["stop_pct"] = 0.07
        brain.save_version("v2.2-k8", {"config": bad, "lineage": "K1"}, "测试:改坏",
                           activate=False, db_path=db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 2
        assert brain.get_version("v2.3-k8", db_path=db) is None

    def test_refuses_when_source_already_has_the_new_fields(self, isolated_env):
        """来源里已经有 `loss_warning_*` → 拒绝落行(⛔ 只加不覆盖:那说明来源被人动过)。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        tampered = dict(brain.get_version("v2.2-k8", db_path=db).rule["config"])
        tampered["loss_warning_action"] = "auto_sell"     # 有人手塞了个别的口径
        brain.save_version("v2.2-k8", {"config": tampered, "lineage": "K1"}, "测试:被塞过",
                           activate=False, db_path=db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 2
        assert brain.get_version("v2.3-k8", db_path=db) is None

    def test_refuses_when_source_missing(self, isolated_env):
        db = isolated_env.db_path
        brain.save_version("K1", {"config": dict(TEST_RULE_V1_CONFIG)}, "测试",
                           activate=True, db_path=db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 1

    def test_risk_register_is_verbatim_in_changelog(self, isolated_env):
        """🔴 **风险登记原样入 changelog,⛔ 不得删、不得摘要**(照 v1.3 / v2.2-k8 先例)。
        逐条按其**不可替代的判据数字/结论**扫,防"改写成一句概括"。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        log = brain.get_version("v2.3-k8", db_path=db).changelog
        for needle in (
            "loss_warning_pct = 0.05",     # K8.md §十九 原文
            "loss_warning_action = review",
            "执行器不得用其触发自动卖出",     # `stop_pct` 降为兼容只读的原文依据
            "亏损警戒不触发",               # K8.md §十三 原文
            "85%",                         # §1.3 第一死因的真金比例(风险登记之一)
            "兼容只读",                     # ⛔ 不等于"这个字段可以删了"
            "越线采纳",                     # 与 v1.3 / v2.2-k8 同一先例的定性
            "95361.4988",                  # 冻结基线不适用
            "staged",                      # 生效前提
        ):
            assert needle in log, f"风险登记里少了「{needle}」—— ⛔ 不得删、不得摘要"


# ======================================================================
#  B. 切换器四道闸
# ======================================================================

class TestSwitcherGates:
    def test_whitelist_and_core_expectations_are_in_sync(self):
        """闸 1 白名单里每个版本都必须有闸 3 核对项(结构性护栏,加白名单不许忘核对表)。"""
        assert "v2.3-k8" in activate_charter._ALLOWED_TARGETS
        assert set(activate_charter._ALLOWED_TARGETS) <= set(activate_charter._CORE_EXPECTATIONS)

    def test_core_expectations_pin_the_two_new_values_and_the_untouched(self):
        """闸 3 核对项:两个新值是本版判据;`stop_pct=0.05`、四个 None、仓位三件逐字重复。"""
        exp = activate_charter._CORE_EXPECTATIONS["v2.3-k8"]
        assert exp["loss_warning_pct"] == 0.05
        assert exp["loss_warning_action"] == "review"
        assert exp["stop_pct"] == 0.05
        assert exp["take_profit_retrace"] is None and exp["max_hold_days"] is None
        assert exp["max_hold_days_profit"] is None
        assert exp["time_exit_only_if_unprofitable"] is False
        assert exp["single_cap"] == 40000.0 and exp["max_positions"] == 3

    def test_v22k8_expectations_are_untouched(self):
        """⛔ 不改 `v2.2-k8` 既有断言(§五 ⑦:照体例扩,不动老条目)。"""
        exp = activate_charter._CORE_EXPECTATIONS["v2.2-k8"]
        assert set(exp) == {"take_profit_retrace", "max_hold_days", "max_hold_days_profit",
                            "time_exit_only_if_unprofitable", "stop_pct", "single_cap",
                            "max_positions", "forbid_high_elasticity"}

    def test_gate2_entry_side_exemption_is_false_for_this_diff(self, isolated_env):
        """🔴🔴 **§五 ⑤-C「窄豁免对它不成立」那一条**:`v2.2-k8 → v2.3-k8` 改的是**退出侧
        语义**,正是闸 2 当初要防的那一类 → 窄豁免**必须返 `False`**。

        ⚠ 与 v2.2-k8 那次**不同**:那次 (a)(b) 两条同时不成立;这次只有 (a) 不成立
        (两个新字段不在入场侧白名单里),(b) 的八项在途仓位不变量**确实逐字段相同** ——
        这正是"只加语义字段"的物理事实。**⛔ 别因此就以为可以带仓激活**:一条不成立即
        豁免不成立,这里把两侧各自的判定都断言到,免得将来有人只看 (b) 绿就放行。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        old_cfg = dict(brain.get_version("v2.2-k8", db_path=db).rule["config"])
        new_cfg = dict(brain.get_version("v2.3-k8", db_path=db).rule["config"])
        changed = activate_charter._diff_keys(old_cfg, new_cfg)
        assert set(changed) == _NEW_KEYS

        exempt, reasons = activate_charter._exemption_verdict(old_cfg, new_cfg, changed)
        assert exempt is False, "闸 2 窄豁免竟对一个退出侧语义 diff 成立 —— 那道闸等于没了"
        joined = " ".join(reasons)
        assert "(a) diff 含入场侧白名单**之外**的字段" in joined        # 条件 (a) 不成立
        assert "(b) 在途仓位行为不变量" in joined and "逐字段相同 ✓" in joined  # (b) 成立,如实说

    def test_gate2_rejects_with_open_positions(self, isolated_env):
        """闸 2 硬校验:有 open 持仓 → 拒绝激活(带不带 --confirm 都过不去)。
        ⚠ 这正是「⑤ 只做到演练全绿、就位待激活」的物理原因(§五 ⑩-1 用户手动清单)。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        assert activate_charter.activate(db, "v2.2-k8", confirm=True) == 0   # 现役挪到 v2.2-k8
        pos_store.open_position("600519.SH", 10.0, 100, date(2026, 7, 20), db_path=db)
        assert activate_charter.activate(db, "v2.3-k8", confirm=True) == 1
        assert brain.get_active(db_path=db).version == "v2.2-k8"             # 现役未动

    def test_dry_run_all_four_gates_green_then_activates(self, isolated_env):
        """无持仓时:先演练(不写库、现役不动)→ 再 --confirm 激活 → 现役唯一。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        assert activate_charter.activate(db, "v2.2-k8", confirm=True) == 0
        assert activate_charter.activate(db, "v2.3-k8", confirm=False) == 0  # 演练
        assert brain.get_active(db_path=db).version == "v2.2-k8"
        assert activate_charter.activate(db, "v2.3-k8", confirm=True) == 0
        assert brain.get_active(db_path=db).version == "v2.3-k8"
        assert [v.version for v in brain.list_versions(db_path=db) if v.is_active] == ["v2.3-k8"]

    def test_rollback_target_v22k8_still_allowed(self, isolated_env):
        """🔴 回滚绳:`activate_charter.py --target v2.2-k8 --confirm` 仍走四道闸、仍能过。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        assert activate_charter.activate(db, "v2.3-k8", confirm=True) == 0
        assert activate_charter.activate(db, "v2.2-k8", confirm=True) == 0
        assert brain.get_active(db_path=db).version == "v2.2-k8"

    def test_default_target_is_still_v133_not_the_high_risk_one(self):
        """高危目标必须显式 `--target` 打出来,⛔ 不许手滑默认过去。"""
        assert activate_charter._TARGET_VERSION == "v1.3.3"


# ======================================================================
#  C. `stop_is_advisory` 三级判据(V2.3.2-⑤ 改判)
# ======================================================================

class TestStopAdvisoryPredicate:
    def test_config_path_wins_and_v23k8_is_advisory(self, isolated_env):
        """🔴 §五 ⑤ 验收③:`stop_is_advisory('v2.3-k8')` **走 config 路径**为 True。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        cfg = brain.get_version("v2.3-k8", db_path=db).rule["config"]
        assert brain.stop_is_advisory("v2.3-k8", cfg) is True
        # 显式 db_path 那条支路同结论(调用方手上没有 config 时的逃生口)
        assert brain.stop_is_advisory("v2.3-k8", db_path=db) is True

    def test_v133_is_still_mandatory_under_every_path(self, isolated_env):
        """🔴 §五 ⑤ 验收③ 的另一半:`v1.3.3` 仍 False(新章程**不许洗白**旧口径)。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        cfg = brain.get_version("v1.3.3", db_path=db).rule["config"]
        assert brain.stop_is_advisory("v1.3.3", cfg) is False
        assert brain.stop_is_advisory("v1.3.3", db_path=db) is False
        assert brain.stop_is_advisory("v1.3.3") is False

    def test_whitelist_still_covers_the_old_row(self, isolated_env):
        """⚠ 白名单**给老行用**:`v2.2-k8` 的 config 里没有这两个字段 → 回退名单才判得对。
        ⛔ 名单不许删,也⛔ 不许把 `v2.3-k8` 加进去(那是给同一件事留两个事实源)。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        cfg = brain.get_version("v2.2-k8", db_path=db).rule["config"]
        assert "loss_warning_action" not in cfg
        assert brain.stop_is_advisory("v2.2-k8", cfg) is True     # 走白名单
        assert brain.stop_is_advisory("v2.2-k8") is True
        assert "v2.3-k8" not in brain.STOP_ADVISORY_CHARTERS

    def test_explicit_non_review_action_beats_the_whitelist(self):
        """config 说了话就以 config 为准 —— 哪怕版本名在白名单里。
        (防的是"名单写死了口径、章程改了却改不动"这种两个事实源的病。)"""
        assert brain.stop_is_advisory("v2.2-k8", {"loss_warning_action": "hard_stop"}) is False

    def test_unknown_version_defaults_to_mandatory(self):
        """默认方向 = **更严**(破线未走照记违纪)。漏登记的代价是"多记一条"(吵),
        不是"少记一条"(静默漏审)—— 方向刻意选前者。"""
        assert brain.stop_is_advisory("v9.9-nobody-registered") is False
        assert brain.stop_is_advisory(None) is False
        assert brain.stop_is_advisory("v9.9-nobody-registered", {}) is False

    def test_no_db_read_when_db_path_is_absent(self, isolated_env, monkeypatch):
        """⚠ **`db_path=None` 时刻意不读库**:`neckline/db.py` 有它自己那份未被测试夹具
        重写的 `settings`,`None` 会静默连到**真实项目库**(CLAUDE.md「测试隔离」条)。
        这条把"不读"钉死 —— 否则一次手滑就让全套单测去查生产库。"""
        called = []
        monkeypatch.setattr(brain, "get_version",
                            lambda *a, **k: called.append(a) or None)
        assert brain.stop_is_advisory("v2.3-k8") is False   # 名单外 + 不查库 → 保守
        assert called == [], "db_path 缺省时竟去读库了 —— 那会连到真实项目库"

    def test_active_variant_reads_the_active_row_config(self, isolated_env):
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        assert activate_charter.activate(db, "v2.3-k8", confirm=True) == 0
        assert brain.active_stop_is_advisory(db_path=db) is True
        brain.activate_version("v1.3.3", db_path=db)
        assert brain.active_stop_is_advisory(db_path=db) is False


# ======================================================================
#  D. 三条守门(§五 ⑤-B 第 5 条)
# ======================================================================

#: 守门① `stop_pct` 消费方白名单 —— **文件级**,凡在 `neckline/` 或 `scripts/` 里
#: 提到这个字段名的 `.py` 都必须在册。
#:
#: ⚠ **§五 ⑤-B 原文点名的是四类语义消费方**(判分 `eval/exit_sim.py` · 展示派生
#: `api/app.py::_stop_line` · 篮子失效条件 `selection/basket_card.py` · 哨兵警戒
#: `sentinel/holding.py`);**实际取数点比那四处多**(还有连续止损熔断、集合竞价、
#: 周复盘统计、回测引擎……)。本白名单按**实际**登记 —— 一个只列四条、却对另外二十条
#: 视而不见的白名单等于没有白名单。四类语义消费方**全部在册**(下面另有一条正面断言)。
_STOP_PCT_CONSUMERS = {
    # —— 判分口径(回测 / 事后评价;`_CHARTER_TO_SIM_KW` 把它翻成 `_sim_one` 的 `stop`)——
    "neckline/eval/exit_sim.py",
    "neckline/strategy/momentum.py",          # 回测引擎 `_exit_reason` 的止损分支
    # —— 展示派生(端点 / 契约 / 报告)——
    "neckline/api/app.py",                    # `_stop_line`(§五 ⑤-B 点名)
    "neckline/api/schemas.py",                # 契约字段注释
    "neckline/report/basket_daily.py",        # 冻结卡指纹 → camelCase 转换表
    "neckline/report/evening.py",             # 卡落库时的口径指纹 meta
    "neckline/report/holding_k4_check.py",    # 16:35 体检读现役 config
    # —— 篮子失效条件 / 卡上止损价(§五 ⑤-B 点名)——
    "neckline/selection/basket_card.py",
    "neckline/selection/basket_store.py",     # 指纹落列
    "neckline/selection/verification_rules.py",
    "neckline/selection/__init__.py",         # 再导出
    "neckline/sentinel/basket_verify.py",     # 读卡上冻结的那份,⛔ 不读"当前现役"
    # —— 哨兵警戒(§五 ⑤-B 点名)——
    "neckline/sentinel/holding.py",
    "neckline/sentinel/engine.py",            # 取现役 config 传给 holding
    "neckline/sentinel/precall.py",           # 集合竞价警戒
    "neckline/sentinel/circuit.py",           # 连续止损链判据
    # —— 周复盘止损纪律统计(锚卖出时刻 governing 的那版)——
    "neckline/review/reconcile.py",
    # —— 单一源本身 / 建表注释 ——
    "neckline/strategy/brain.py",
    "neckline/db.py",
    # V2.4.0 P3.1:持仓语义文案单一源。**不读** `stop_pct`(docstring 提到它只是为了
    # 讲清"数值口径一字不动,本模块只产文案"这条边界)——文件级白名单按字面出现登记,
    # 不因为"只是注释"就豁免(与上面 `api/schemas.py`「契约字段注释」同一条纪律)。
    "neckline/strategy/charter_copy.py",
    # —— 章程落行与切换脚本(核对表里逐字重复 0.05,正是"防改坏"的闸)——
    "scripts/activate_charter.py",
    "scripts/oneoff/charter_v1_3.py",
    "scripts/oneoff/charter_v1_3_3.py",
    "scripts/oneoff/seed_charter_v22k8.py",
    "scripts/oneoff/seed_charter_v23k8.py",
    # —— 冒烟脚本(只读现役值出图 / 出报告)——
    "scripts/smoke_basket_review.py",
    "scripts/smoke_basket_verify.py",
    "scripts/smoke_profile.py",
    # V2.3.3-④ 竞价层冒烟:D0 零篮子时**往临时 DB 副本**合成一张卡,`stop_pct` 只是
    # 喂给 `build_invalidation_spec` 造那张合成卡的失效位。**不触发任何卖出动作、
    # 不碰真实库**(整份复制到 tmp 再写),与上面三条冒烟同类。
    "scripts/smoke_auction.py",
}

#: §五 ⑤-B 原文点名的四类语义消费方(**必须全部在册**,防有人"精简"白名单时把它们删掉)。
_STOP_PCT_NAMED_FOUR = (
    "neckline/eval/exit_sim.py",
    "neckline/api/app.py",
    "neckline/selection/basket_card.py",
    "neckline/sentinel/holding.py",
)

#: 守门② 下单 / 委托类调用名(正则)。⚠ 只认**调用**,不认字符串与注释。
_ORDER_CALL_PAT = re.compile(
    r"^(place|submit|send|create|cancel|insert|execute)_(order|trade)s?$"
    r"|^order_(insert|action|send|submit)$"
    r"|^(buy|sell)(_stock|_order|_market|_limit|_at_market)?$"
    r"|^(auto_)?(sell|buy)_(all|position|out)$"
    r"|^(place|submit)_(buy|sell)$"
)

#: 守门② 交易(**下单**)SDK —— 行情 / 数据源 SDK 不在此列(那些是本项目的正常依赖)。
_TRADING_SDKS = frozenset({
    "easytrader", "vnpy", "tqsdk", "ths_trader", "ib_insync", "ccxt",
    "alpaca_trade_api", "futu", "xtquant", "gm",
})


def _iter_source_files():
    for d in ("neckline", "scripts"):
        for p in sorted((_ROOT / d).rglob("*.py")):
            yield p


def _scan_order_calls(tree: ast.AST):
    """返回该 AST 里所有"下单 / 委托"嫌疑点 `(lineno, 名字)`。"""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            if name and _ORDER_CALL_PAT.match(name):
                hits.append((node.lineno, name))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in _TRADING_SDKS:
                    hits.append((node.lineno, f"import {a.name}"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in _TRADING_SDKS:
                hits.append((node.lineno, f"from {node.module}"))
    return hits


class TestThreeGuards:
    # —— 守门 ① ——————————————————————————————————————————————————————
    def test_stop_pct_consumers_are_whitelisted(self):
        """🔴 白名单外**新增** `stop_pct` 消费方即红。

        为什么这道闸值钱:K8.md §十九 把 `stop_pct` 降为**兼容只读**(「执行器不得用其
        触发自动卖出」)。降级这件事在代码里没有任何物理标记 —— 唯一防线就是"再有人拿它
        去接一条新链路时,有人看见"。红了**先问那处该不该存在**,别顺手把文件名加进来。"""
        actual = {str(p.relative_to(_ROOT)) for p in _iter_source_files()
                  if "stop_pct" in p.read_text(encoding="utf-8")}
        added = actual - _STOP_PCT_CONSUMERS
        removed = _STOP_PCT_CONSUMERS - actual
        assert not added, (
            f"这些文件新提到了 `stop_pct`,但不在消费方白名单里:{sorted(added)}。"
            f"🔴 先确认它该不该读这个字段(K8.md §十九:兼容只读,⛔ 执行器不得据它自动卖出),"
            f"确认无误再登记进 `_STOP_PCT_CONSUMERS` 并写清它属于哪一类。"
        )
        assert not removed, (
            f"白名单里这些文件已经不提 `stop_pct` 了:{sorted(removed)} —— 顺手把它们从名单删掉,"
            f"别让白名单变成一份没人维护的旧地图。"
        )

    def test_the_four_named_semantic_consumers_are_all_registered(self):
        """§五 ⑤-B 原文点名的四类语义消费方,一个都不许从白名单里掉出去。"""
        for f in _STOP_PCT_NAMED_FOUR:
            assert f in _STOP_PCT_CONSUMERS, f"§五 ⑤-B 点名的消费方 {f} 不在白名单里"
            assert (_ROOT / f).exists(), f"{f} 不存在了 —— 白名单该跟着改"

    # —— 守门 ② ——————————————————————————————————————————————————————
    def test_no_auto_sell_path_anywhere(self):
        """🔴 全仓**零自动卖出路径**(K8.md §十三:「亏损警戒不触发系统自动卖出」)。

        ⚠ 这条**本来就成立**,本版把它钉死 —— 它防的是将来某天有人"顺手"给亏损警戒接一个
        自动委托。⛔ 红了不许改测试,先问那条链路凭什么存在。

        ⚠ 回测的 `Order` / `Broker`(`neckline/backtest/`)是**纯内存模拟盘**,不在扫描的
        调用名词表里(它们叫 `Order(...)` / `broker.fill(...)`,不是 `place_order`)——
        真出现 `submit_order` 之类,不管在哪个包里都会红。"""
        offenders = []
        for p in _iter_source_files():
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:                        # 语法坏了是另一条测试的事
                continue
            for lineno, name in _scan_order_calls(tree):
                offenders.append(f"{p.relative_to(_ROOT)}:{lineno} → {name}")
        assert not offenders, (
            "🔴 出现疑似下单 / 委托调用 —— 系统永不代下单、永不自动卖出"
            f"(K8.md §十三):{offenders}"
        )

    def test_the_auto_sell_detector_actually_fires(self):
        """🔴 **把守门本身测一遍**(防"空转的守门"):喂一段真会下单的代码,必须被逮到。
        没有这条,上面那个 `assert not offenders` 在正则写错时会**永远绿**。"""
        for snippet, expect in (
            ("broker.place_order(code, 100)", "place_order"),
            ("api.submit_order(x)", "submit_order"),
            ("import easytrader", "import easytrader"),
            ("from vnpy.trader import x", "from vnpy.trader"),
            ("trader.sell(code, qty)", "sell"),
        ):
            hits = _scan_order_calls(ast.parse(snippet))
            assert hits, f"守门②漏掉了 `{snippet}` —— 那它就是个空转的闸"
            assert any(expect in h[1] for h in hits), (snippet, hits)
        # 反向:本项目的合法写法**不许**被误伤(回测模拟盘 / 台账记账)
        for benign in (
            "orders.append(Order(ts_code=code, side='buy', target_value=b, reason='母战法建仓'))",
            "pos_store.close_position(pid, price, qty)",
            "pos_store.open_position(code, price, qty, day)",
        ):
            assert not _scan_order_calls(ast.parse(benign)), f"守门②误伤了合法写法:{benign}"

    # —— 守门 ③ ——————————————————————————————————————————————————————
    def test_v23k8_config_differs_from_v22k8_by_exactly_the_two_fields(self, isolated_env):
        """🔴 逐字段对拍:`v2.3-k8` 与 `v2.2-k8` **只差** `loss_warning_pct` /
        `loss_warning_action` 两个键,**且新增值恰是 K8.md §十九 给的那两个**。
        ⛔ 第三个差异出现即红 —— 那说明有人借这次改动夹带了别的东西。"""
        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        old = dict(brain.get_version("v2.2-k8", db_path=db).rule["config"])
        new = dict(brain.get_version("v2.3-k8", db_path=db).rule["config"])
        assert set(new) - set(old) == _NEW_KEYS
        assert set(old) - set(new) == set()
        for k in old:
            assert new[k] == old[k], f"{k} 在 v2.3-k8 里变了 —— 本版只许加两个键"
        assert new["loss_warning_pct"] == 0.05 and new["loss_warning_action"] == "review"


# ======================================================================
#  E. 契约只加不删(卡指纹 + 端点)
# ======================================================================

class TestContractOnlyAdds:
    def test_card_fingerprint_carries_both_old_and_new(self, isolated_env):
        """卡的口径指纹同时有 `stop_pct` 与 `loss_warning_*`,且 `stop_pct` 仍是 0.05
        (⛔ 本版只加不删 —— 服务端删 `stopPct` 键是下一版的事,CLAUDE.md 两步淘汰)。"""
        from neckline.selection import basket_card as bc

        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert seed_charter_v23k8.land_charter(db, confirm=True) == 0
        assert activate_charter.activate(db, "v2.3-k8", confirm=True) == 0

        assert bc.resolve_charter_pcts(db)[0] == 0.05          # 值一字不动
        assert bc.resolve_loss_warning(db) == (0.05, "review")

        card = bc.BasketCard(
            version=1, basket_key="B", trade_date="20260811", name="篮", driver="d",
            driver_kind="policy", why_now="", evidence=(), evidence_status="ok",
            members=(), verification_spec={}, invalidation_spec={},
            stop_pct=0.05, loss_warning_pct=0.05, loss_warning_action="review",
        )
        fp = card.to_card_json()["fingerprint"]
        assert fp["stop_pct"] == 0.05
        assert fp["loss_warning_pct"] == 0.05 and fp["loss_warning_action"] == "review"

    def test_old_charter_leaves_both_new_keys_null(self, isolated_env):
        """老章程(`v2.2-k8`)现役时两位都是 `None` = **该章程没有声明过这个语义**,
        ⛔ 不是"读失败",更⛔ 不许拿 `stop_pct` 顶上去。"""
        from neckline.selection import basket_card as bc

        db = isolated_env.db_path
        _seed_through_v22k8(db)
        assert activate_charter.activate(db, "v2.2-k8", confirm=True) == 0
        assert bc.resolve_loss_warning(db) == (None, None)
        assert bc.resolve_charter_pcts(db)[0] == 0.05          # 止损比例照旧读得出

    def test_camel_map_carries_the_two_new_keys(self):
        """`card_json` → 契约 camelCase 的**唯一转换点**必须带上这两键。"""
        from neckline.report.basket_daily import _CARD_FINGERPRINT_KEYS

        m = dict(_CARD_FINGERPRINT_KEYS)
        assert m["stop_pct"] == "stopPct"                       # ⛔ 老键不删
        assert m["loss_warning_pct"] == "lossWarningPct"
        assert m["loss_warning_action"] == "lossWarningAction"

    def test_client_dto_decodes_both_new_keys(self):
        """客户端 `BasketFingerprint` / `Position` 都必须 `decodeIfPresent` 这两键
        (老卡 / 老服务端缺键 → nil,⛔ 不炸、⛔ 不当"配置丢了")。"""
        from tests.client_sources import models_text
        src = models_text()   # V2.4.0 P3.7:DTO 已拆六份,统一入口读
        for owner in ("struct BasketFingerprint", "struct Position:"):
            block = src.split(owner, 1)[1].split("\n}\n", 1)[0]
            # ⚠ 按**解码那一行**钉,不是"这个词在块里出现过" —— 后者在有人把它改成
            # `try c.decode(...)`(缺键即炸)时会照样绿。
            assert "decodeIfPresent(Double.self, forKey: .lossWarningPct)" in block, owner
            assert "decodeIfPresent(String.self, forKey: .lossWarningAction)" in block, owner
            cases = " ".join(ln for ln in block.splitlines() if ln.strip().startswith("case "))
            for key in ("lossWarningPct", "lossWarningAction"):
                assert key in cases, f"{owner} 的 CodingKeys 里少了 {key}(手写枚举必须同步补)"
        assert "var stopPct: Double? = nil" in src, "⛔ 本版不许删 `stopPct`(两步淘汰第一步)"
