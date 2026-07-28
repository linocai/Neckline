"""v1.3.3 拆墙章程落库 + 切换器单测(🔴 碰纪律章程 + 大脑版本表)。

锁死:①`charter_v1_3_3` 从 **`v1.3` 行**(按版本名读,不读 get_active)复制、**只改
`forbid_high_elasticity` 一个字段**、其余逐字段 = v1.3、落 `activate=False`(现役不动);
②来源行 v1.3 逐字节不被碰;③来源核心值被改坏 → 拒绝落行;④墙已被拆(源 False)→ 拒绝落行
(要拆的东西不在了);⑤风险登记四条原样入 changelog(不精简);⑥切换器白名单含 v1.3.3 且
`_CORE_EXPECTATIONS` 同步(含 `forbid_high_elasticity=False` 这一判据);⑦切换器激活 v1.3.3
后现役唯一;⑧有 open 持仓仍硬拒(staged 时机铁律不因拆墙放松);⑨v1.3 仍是合法回退目标。
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
from neckline.sentinel import positions as pos_store  # noqa: E402
from neckline.strategy import brain  # noqa: E402
from tests.conftest import TEST_RULE_V1_CONFIG

pytestmark = pytest.mark.usefixtures("isolated_env")

# 除拆墙字段外,v1.3.3 必须与 v1.3 逐字段相同——这里点名锁死几个"绝不能被顺手改掉"的
# (三条禁买过滤 P4/P5/P6 在测试 fixture 里本就缺省不写,故只在源里存在时才断言,见下)。
_V13_FIELDS_UNCHANGED = (
    "stop_pct", "take_profit_retrace", "max_hold_days", "max_hold_days_profit",
    "time_exit_only_if_unprofitable", "max_positions", "single_cap", "max_exposure_frac",
    "strength", "buypoint", "cooldown_days", "week_halving",
    "forbid_green_bigdown", "forbid_far_from_high", "forbid_new_days",
)


def _seed_k1_and_v13(db: Path) -> None:
    """把 K1 落成现役 + 跑真 `charter_v1_3` 落出 v1.3 行(不手抄 v1.3 config)。"""
    brain.save_version("K1", {"config": dict(TEST_RULE_V1_CONFIG)}, "测试:K1 基线",
                       activate=True, db_path=db)
    assert charter_v1_3.land_charter(db) == 0


class TestCharter133Landing:
    def test_lands_v133_only_wall_field_changed(self, isolated_env):
        db = isolated_env.db_path
        _seed_k1_and_v13(db)
        v13_cfg_before = dict(brain.get_version("v1.3", db_path=db).rule["config"])

        assert charter_v1_3_3.land_charter(db) == 0

        cfg = brain.get_version("v1.3.3", db_path=db).rule["config"]
        assert cfg["forbid_high_elasticity"] is False          # 唯一改动:墙拆了
        for f in _V13_FIELDS_UNCHANGED:                        # 点名字段逐个 = v1.3
            if f in v13_cfg_before:
                assert cfg[f] == v13_cfg_before[f], f
        assert set(cfg) == set(v13_cfg_before)                 # 不多不少同一批键
        # 兜底:除拆墙字段外,**一个键都不许有差**(不依赖上面的点名清单是否写全)
        assert {k: v for k, v in cfg.items() if k != "forbid_high_elasticity"} == \
               {k: v for k, v in v13_cfg_before.items() if k != "forbid_high_elasticity"}
        assert brain.get_version("v1.3.3", db_path=db).rule["lineage"] == "K1"

    def test_source_v13_row_untouched(self, isolated_env):
        """本脚本只写新行:v1.3 的 rule/changelog/is_active 逐字段不变。"""
        db = isolated_env.db_path
        _seed_k1_and_v13(db)
        before = brain.get_version("v1.3", db_path=db)
        charter_v1_3_3.land_charter(db)
        after = brain.get_version("v1.3", db_path=db)
        assert after.rule == before.rule
        assert after.changelog == before.changelog
        assert after.is_active == before.is_active
        assert after.created_at == before.created_at

    def test_lands_inactive_active_row_unchanged(self, isolated_env):
        """`activate=False`:落行前后现役版本不变、v1.3.3 自身不激活。"""
        db = isolated_env.db_path
        _seed_k1_and_v13(db)
        charter_v1_3_3.land_charter(db)
        assert brain.get_active(db_path=db).version == "K1"
        assert not brain.get_version("v1.3.3", db_path=db).is_active

    def test_lands_from_v13_row_even_when_v13_is_active(self, isolated_env):
        """生产形态:v1.3 已是现役。仍按版本名读 v1.3 → 结果与 v1.3 inert 时一致。"""
        db = isolated_env.db_path
        _seed_k1_and_v13(db)
        brain.activate_version("v1.3", db_path=db)
        assert charter_v1_3_3.land_charter(db) == 0
        assert brain.get_active(db_path=db).version == "v1.3"        # 现役未被落行动过
        assert brain.get_version("v1.3.3", db_path=db).rule["config"]["forbid_high_elasticity"] is False

    def test_reject_missing_source(self, isolated_env):
        db = isolated_env.db_path
        brain.save_version("K1", {"config": dict(TEST_RULE_V1_CONFIG)}, "只有 K1",
                           activate=True, db_path=db)
        assert charter_v1_3_3.land_charter(db) == 1                  # 无 v1.3 行 → 拒绝

    def test_reject_broken_source_core_values(self, isolated_env):
        """来源 v1.3 行的退出/仓位核心值被改坏 → 拒绝复制(防从错误来源生一份新章程)。"""
        db = isolated_env.db_path
        _seed_k1_and_v13(db)
        bad = dict(brain.get_version("v1.3", db_path=db).rule["config"])
        bad["take_profit_retrace"] = 0.05                            # 退回 K1 旧值 = 行被改坏
        brain.save_version("v1.3", {"config": bad, "lineage": "K1"}, "被改坏的 v1.3",
                           activate=False, db_path=db)
        assert charter_v1_3_3.land_charter(db) == 2
        assert brain.get_version("v1.3.3", db_path=db) is None

    def test_reject_when_wall_already_down(self, isolated_env):
        """来源 v1.3 的 `forbid_high_elasticity` 已是 False → 拒绝(要拆的墙已经不在了)。"""
        db = isolated_env.db_path
        _seed_k1_and_v13(db)
        cfg = dict(brain.get_version("v1.3", db_path=db).rule["config"])
        cfg["forbid_high_elasticity"] = False
        brain.save_version("v1.3", {"config": cfg, "lineage": "K1"}, "墙已被人拆过的 v1.3",
                           activate=False, db_path=db)
        assert charter_v1_3_3.land_charter(db) == 2
        assert brain.get_version("v1.3.3", db_path=db) is None

    def test_risk_registration_verbatim_in_changelog(self, isolated_env):
        """风险登记四条 + 证据链原样在(不精简、不许删)。"""
        db = isolated_env.db_path
        _seed_k1_and_v13(db)
        charter_v1_3_3.land_charter(db)
        cl = brain.get_version("v1.3.3", db_path=db).changelog
        assert "用户 2026-07-27 拍板拆墙" in cl                          # ①
        assert "本 config 与任何回测基线都不同" in cl                     # ②
        assert "N=1288" in cl and "−20.53%" in cl and "不适用于本行" in cl
        assert "§1.3 第二死因" in cl
        assert "-5% 止损" in cl and "K4 安检" in cl and "不再由「不许碰」承担" in cl   # ③
        assert "2026-07-25" in cl and "§2.3 候选语义变更" in cl           # ④
        assert "research/stage1_report.md" in cl


class TestActivator133:
    def _prep(self, db: Path) -> None:
        _seed_k1_and_v13(db)
        assert charter_v1_3_3.land_charter(db) == 0

    def test_whitelist_and_core_expectations_stay_in_sync(self):
        assert set(activate_charter._ALLOWED_TARGETS) <= set(activate_charter._CORE_EXPECTATIONS)

    def test_v133_in_whitelist_with_wall_down_expectation(self):
        """加白名单必须同时加核对表,且 v1.3.3 的判据里 `forbid_high_elasticity=False`
        (= "激活到了拆墙那一行"的唯一语义判据);v1.3 那条则期望 True(墙还在)。"""
        assert "v1.3.3" in activate_charter._ALLOWED_TARGETS
        assert activate_charter._CORE_EXPECTATIONS["v1.3.3"]["forbid_high_elasticity"] is False
        assert activate_charter._CORE_EXPECTATIONS["v1.3"]["forbid_high_elasticity"] is True
        assert activate_charter._TARGET_VERSION == "v1.3.3"           # 默认目标已切

    def test_confirm_activates_v133(self, isolated_env):
        db = isolated_env.db_path
        self._prep(db)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 0
        act = brain.get_active(db_path=db)
        assert act.version == "v1.3.3"
        assert act.rule["config"]["forbid_high_elasticity"] is False
        assert act.rule["config"]["stop_pct"] == 0.05                 # 止损没被带歪
        assert [v.version for v in brain.list_versions(db_path=db) if v.is_active] == ["v1.3.3"]

    def test_dry_run_no_write(self, isolated_env):
        db = isolated_env.db_path
        self._prep(db)
        assert activate_charter.activate(db, "v1.3.3", confirm=False) == 0
        assert brain.get_active(db_path=db).version == "K1"

    def test_reject_with_open_positions(self, isolated_env):
        """拆墙不放松 staged 时机铁律:有 open 持仓照样拒绝激活。"""
        db = isolated_env.db_path
        self._prep(db)
        pos_store.open_position("300001.SZ", 10.0, 1000, date(2026, 7, 20), db_path=db)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 1
        assert brain.get_active(db_path=db).version == "K1"

    def test_reject_v133_row_with_wall_still_up(self, isolated_env):
        """目标 v1.3.3 但那行的 `forbid_high_elasticity` 仍是 True(错误/未改的行)→ 拒绝。"""
        db = isolated_env.db_path
        _seed_k1_and_v13(db)
        bad = dict(brain.get_version("v1.3", db_path=db).rule["config"])   # 墙仍在
        brain.save_version("v1.3.3", {"config": bad, "lineage": "K1"}, "未改对的 v1.3.3",
                           activate=False, db_path=db)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 2
        assert brain.get_active(db_path=db).version == "K1"

    def test_v13_still_allowed_as_rollback_target(self, isolated_env):
        """拆墙后 v1.3 仍在白名单 = 唯一合法回退目标(紧急退回主板 only 口径)。"""
        db = isolated_env.db_path
        self._prep(db)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 0
        assert activate_charter.activate(db, "v1.3", confirm=True) == 0
        act = brain.get_active(db_path=db)
        assert act.version == "v1.3" and act.rule["config"]["forbid_high_elasticity"] is True

    @pytest.mark.parametrize("target", ["K1", "K2", "K4", "v1.2", "v1.3.4", "V1.3.3", "v133", ""])
    def test_whitelist_still_rejects_everything_else(self, isolated_env, target):
        db = isolated_env.db_path
        self._prep(db)
        brain.save_version("K2", {"config": dict(TEST_RULE_V1_CONFIG)}, "废弃研究臂",
                           activate=False, db_path=db)
        brain.save_version("K4", {"config": dict(TEST_RULE_V1_CONFIG)}, "参考档",
                           activate=False, db_path=db)
        assert activate_charter.activate(db, target, confirm=True) != 0
        assert [v.version for v in brain.list_versions(db_path=db) if v.is_active] == ["K1"]


class TestGate2NarrowExemption:
    """闸 2 窄豁免(2026-07-27 用户授权):**当且仅当** diff ⊆ 入场侧白名单 **且** 退出/仓位
    八项逐字段相同,才允许带未平持仓激活。生产真实场景 = v1.3(现役)→ v1.3.3,用户手上
    3 笔 open 持仓。"""

    def _prep_v13_active(self, db: Path) -> None:
        """做成生产形态:v1.3 现役、v1.3.3 已落行待激活。"""
        _seed_k1_and_v13(db)
        assert charter_v1_3_3.land_charter(db) == 0
        brain.activate_version("v1.3", db_path=db)

    def _hold(self, db: Path, code: str = "300759.SZ") -> None:
        pos_store.open_position(code, 39.42, 500, date(2026, 7, 27), db_path=db)

    def test_pure_entry_side_diff_activates_with_open_positions(self, isolated_env):
        """差异只含 `forbid_high_elasticity` → 带持仓可激活(这就是生产要走的那条路)。"""
        db = isolated_env.db_path
        self._prep_v13_active(db)
        self._hold(db)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 0
        act = brain.get_active(db_path=db)
        assert act.version == "v1.3.3"
        assert act.rule["config"]["forbid_high_elasticity"] is False
        # 在途持仓仍在,且其退出参数逐字段未变
        assert len(pos_store.load_open_positions(db_path=db)) == 1
        for k in activate_charter._HOLD_INVARIANT_KEYS:
            assert act.rule["config"][k] == brain.get_version("v1.3", db_path=db).rule["config"][k]

    def test_exemption_writes_audit_trail(self, isolated_env):
        """豁免必留痕:审计日志文件被追加写入,含理由 / diff / 持仓清单。"""
        db = isolated_env.db_path
        self._prep_v13_active(db)
        self._hold(db)
        log = Path(db).resolve().parent / activate_charter._AUDIT_LOG_NAME
        assert not log.exists()
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 0
        text = log.read_text(encoding="utf-8")
        assert "闸 2 窄豁免激活:v1.3 → v1.3.3" in text
        assert "forbid_high_elasticity" in text                       # diff 全文
        assert "300759.SZ" in text and "@¥39.42 × 500 股" in text      # 持仓清单
        assert "在途仓位行为不变量" in text                             # 判定理由 (b)

    def test_audit_write_failure_blocks_activation(self, isolated_env, monkeypatch):
        """留痕写不成 → **拒绝激活**(不许静默豁免),现役不动。"""
        db = isolated_env.db_path
        self._prep_v13_active(db)
        self._hold(db)
        monkeypatch.setattr(activate_charter, "_write_audit", lambda *_a, **_k: False)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 4
        assert brain.get_active(db_path=db).version == "v1.3"          # 未激活

    def test_dry_run_with_exemption_writes_nothing(self, isolated_env):
        """dry-run 下豁免只是预演判定:不写审计日志、不激活。"""
        db = isolated_env.db_path
        self._prep_v13_active(db)
        self._hold(db)
        assert activate_charter.activate(db, "v1.3.3", confirm=False) == 0
        assert not (Path(db).resolve().parent / activate_charter._AUDIT_LOG_NAME).exists()
        assert brain.get_active(db_path=db).version == "v1.3"

    @pytest.mark.parametrize("field,value", [
        ("stop_pct", 0.06), ("take_profit_retrace", 0.05), ("max_hold_days", 6),
        ("max_hold_days_profit", 20), ("time_exit_only_if_unprofitable", False),
        ("single_cap", 20000.0), ("max_positions", 5), ("max_exposure_frac", 0.6),
    ])
    def test_any_exit_or_position_field_change_still_hard_rejects(self, isolated_env, field, value):
        """差异含**任何**退出/仓位字段 → 闸 2 仍硬拒(豁免只给纯入场侧 diff)。"""
        db = isolated_env.db_path
        self._prep_v13_active(db)
        self._hold(db)
        cfg = dict(brain.get_version("v1.3.3", db_path=db).rule["config"])
        cfg[field] = value
        brain.save_version("v1.3.3", {"config": cfg, "lineage": "K1"}, "掺了退出/仓位改动",
                           activate=False, db_path=db)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 1
        assert brain.get_active(db_path=db).version == "v1.3"
        assert not (Path(db).resolve().parent / activate_charter._AUDIT_LOG_NAME).exists()

    def test_entry_side_field_outside_whitelist_still_hard_rejects(self, isolated_env):
        """差异含白名单**之外**的入场字段(如 `forbid_new_days`)→ 仍硬拒:
        白名单是穷举的,不是"入场侧就放行"。"""
        db = isolated_env.db_path
        self._prep_v13_active(db)
        self._hold(db)
        cfg = dict(brain.get_version("v1.3.3", db_path=db).rule["config"])
        cfg["forbid_new_days"] = 120                # 入场侧,但不在豁免白名单里
        brain.save_version("v1.3.3", {"config": cfg, "lineage": "K1"}, "掺了白名单外入场字段",
                           activate=False, db_path=db)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 1
        assert brain.get_active(db_path=db).version == "v1.3"

    def test_new_unknown_field_counts_as_diff(self, isolated_env):
        """`<缺>` 参与比较:目标行多出一个陌生字段也算 diff → 不在白名单 → 硬拒
        (豁免绝不对"多出来的字段"视而不见)。"""
        db = isolated_env.db_path
        self._prep_v13_active(db)
        self._hold(db)
        cfg = dict(brain.get_version("v1.3.3", db_path=db).rule["config"])
        cfg["some_future_knob"] = 1
        brain.save_version("v1.3.3", {"config": cfg, "lineage": "K1"}, "多了陌生字段",
                           activate=False, db_path=db)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 1

    def test_no_open_positions_behaviour_unchanged(self, isolated_env):
        """无持仓时行为与从前逐行相同:不进豁免分支、不写审计日志。"""
        db = isolated_env.db_path
        self._prep_v13_active(db)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 0
        assert brain.get_active(db_path=db).version == "v1.3.3"
        assert not (Path(db).resolve().parent / activate_charter._AUDIT_LOG_NAME).exists()

    def test_k1_to_v133_with_positions_still_rejected(self, isolated_env):
        """K1 → v1.3.3 是**大 diff**(退出三 + 仓位三 + 拆墙),带持仓必须仍被硬拒
        —— 豁免不是"只要目标是 v1.3.3 就放行"。"""
        db = isolated_env.db_path
        _seed_k1_and_v13(db)
        assert charter_v1_3_3.land_charter(db) == 0     # 现役仍 K1
        self._hold(db)
        assert activate_charter.activate(db, "v1.3.3", confirm=True) == 1
        assert brain.get_active(db_path=db).version == "K1"

    def test_exemption_whitelist_is_narrow(self):
        """结构性:入场侧豁免白名单当前**只有一个字段**;不变量清单覆盖退出四 + 仓位四。"""
        assert activate_charter._ENTRY_SIDE_EXEMPT_KEYS == frozenset({"forbid_high_elasticity"})
        assert set(activate_charter._HOLD_INVARIANT_KEYS) == {
            "stop_pct", "take_profit_retrace", "max_hold_days", "max_hold_days_profit",
            "time_exit_only_if_unprofitable", "single_cap", "max_positions", "max_exposure_frac",
        }
        # 两个集合不许相交(入场侧字段绝不能同时被当成在途不变量,反之亦然)
        assert not (activate_charter._ENTRY_SIDE_EXEMPT_KEYS
                    & set(activate_charter._HOLD_INVARIANT_KEYS))
