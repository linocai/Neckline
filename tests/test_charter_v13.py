"""v1.3 章程落库 + 切换器单测(§五 v1.3-①-E,🔴 碰纪律章程 + 大脑版本表)。

锁死:①charter_v1_3 从现役 K1 config 复制、只改六字段(仓位三 + 退出三)、其余逐字段 = K1、
落 activate=False(K1 仍现役);②风险登记原样入 changelog(不精简);③过时 v1.2 行保留不激活;
④切换器 --target v1.3 校验 take_profit_retrace=0.08 后激活;⑤切换器硬拒绝误选 v1.2;
⑥有 open 持仓拒绝激活(staged 时机铁律);⑦dry-run 不写库。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import activate_charter  # noqa: E402
import charter_v1_3  # noqa: E402
from neckline.sentinel import positions as pos_store  # noqa: E402
from neckline.strategy import brain  # noqa: E402
from tests.conftest import TEST_RULE_V1_CONFIG

pytestmark = pytest.mark.usefixtures("isolated_env")

_K1_FIELDS_UNCHANGED = ("strength", "buypoint", "forbid_high_elasticity", "stop_pct",
                        "cooldown_days", "week_halving", "max_hold_days")


def _seed_k1(db_path: Path) -> None:
    """把 K1 基线 config 落成现役版本 `K1`(charter/切换器 guard 与生产同名)。"""
    brain.save_version("K1", {"config": dict(TEST_RULE_V1_CONFIG)}, "测试:K1 基线",
                       activate=True, db_path=db_path)


def _seed_stale_v12(db_path: Path) -> None:
    """落一行过时 v1.2 章程行(回落 5%/hold=5,不激活)——验证保留不删不激活。"""
    cfg = dict(TEST_RULE_V1_CONFIG)
    cfg.update(max_positions=3, single_cap=40000.0, max_exposure_frac=1.0)   # 三仓制,退出仍旧 5%
    brain.save_version("v1.2", {"config": cfg, "lineage": "K1"}, "过时 v1.2 行(回落5%)",
                       activate=False, db_path=db_path)


class TestCharterLanding:
    def test_lands_v13_only_six_fields_changed(self, isolated_env):
        db = isolated_env.db_path
        _seed_k1(db)
        assert charter_v1_3.land_charter(db) == 0
        v13 = brain.get_version("v1.3", db_path=db)
        cfg = v13.rule["config"]
        # 六字段改动
        assert cfg["max_positions"] == 3
        assert cfg["single_cap"] == 40000.0
        assert cfg["max_exposure_frac"] == 1.0
        assert cfg["take_profit_retrace"] == 0.08
        assert cfg["time_exit_only_if_unprofitable"] is True
        assert cfg["max_hold_days_profit"] == 15
        # 其余逐字段 = K1(尤其 max_hold_days=5 非浮盈档 / stop_pct=0.05 未动)
        for f in _K1_FIELDS_UNCHANGED:
            assert cfg[f] == TEST_RULE_V1_CONFIG[f]
        assert v13.rule["lineage"] == "K1"
        # K1 仍现役,v1.3 不激活
        assert brain.get_active(db_path=db).version == "K1"
        assert not v13.is_active

    def test_risk_registration_verbatim_in_changelog(self, isolated_env):
        db = isolated_env.db_path
        _seed_k1(db)
        charter_v1_3.land_charter(db)
        cl = brain.get_version("v1.3", db_path=db).changelog
        # 风险登记三条 + 证据链两文件 + 「勿激活 v1.2」原样在(不精简)
        assert "回落 8% 系 H9 V0 网格观察免测采纳" in cl
        assert "未整体回测" in cl and "H9-V3 差 724 元" in cl
        assert "用户知情行使决策权越线采纳(2026-07-25)" in cl
        assert "research/h9_exit_reform.md" in cl and "research/winners_anatomy.md" in cl
        assert "勿激活 v1.2" in cl

    def test_reject_non_k1_source(self, isolated_env):
        """现役非 K1 基线(如已是三仓制)→ 拒绝落行(防从错误来源复制)。"""
        db = isolated_env.db_path
        bad = dict(TEST_RULE_V1_CONFIG); bad["single_cap"] = 40000.0
        brain.save_version("K1", {"config": bad}, "被污染的现役", activate=True, db_path=db)
        assert charter_v1_3.land_charter(db) == 2

    def test_stale_v12_kept_inactive(self, isolated_env):
        db = isolated_env.db_path
        _seed_k1(db)
        _seed_stale_v12(db)
        assert charter_v1_3.land_charter(db) == 0
        assert not brain.get_version("v1.2", db_path=db).is_active   # 保留不激活
        assert brain.get_active(db_path=db).version == "K1"


class TestActivator:
    def _prep(self, db: Path) -> None:
        _seed_k1(db)
        _seed_stale_v12(db)
        charter_v1_3.land_charter(db)

    def test_reject_deprecated_v12(self, isolated_env):
        db = isolated_env.db_path
        self._prep(db)
        assert activate_charter.activate(db, "v1.2", confirm=True) == 2   # 硬拒绝
        assert brain.get_active(db_path=db).version == "K1"              # 未激活任何

    @pytest.mark.parametrize("target", ["K1", "K2", "K3", "K4", "v1.2", "v1.4", "V1.3", "", "k2"])
    def test_whitelist_rejects_everything_but_v13(self, isolated_env, target):
        """审计 🟡-2:目标闸是**白名单**。清仓后(闸 2 天然放行)对任何非白名单目标都必须
        非零退出且不动 `is_active`——审计员实测旧黑名单下 `--target K2 --confirm` 真能把废弃
        研究臂激活成现役章程(exit=0、is_active 变 K2)。"""
        db = isolated_env.db_path
        self._prep(db)
        # 先把 K2/K4 也落成真实存在的行(证明「行存在」也拦得住,不是靠「版本不存在」侥幸)
        brain.save_version("K2", {"config": dict(TEST_RULE_V1_CONFIG)}, "废弃研究臂",
                           activate=False, db_path=db)
        brain.save_version("K4", {"config": dict(TEST_RULE_V1_CONFIG)}, "参考档",
                           activate=False, db_path=db)
        assert activate_charter.activate(db, target, confirm=True) != 0
        assert brain.get_active(db_path=db).version == "K1"
        assert [v.version for v in brain.list_versions(db_path=db) if v.is_active] == ["K1"]

    def test_whitelist_allows_v13(self, isolated_env):
        """白名单阳性方向:v1.3 仍能正常激活(闸没有把正路一起堵死)。"""
        db = isolated_env.db_path
        self._prep(db)
        assert activate_charter.activate(db, "v1.3", confirm=True) == 0
        assert brain.get_active(db_path=db).version == "v1.3"

    def test_core_values_checked_for_every_activation(self, isolated_env):
        """审计 🟡-2 后半:**凡激活必核对**核心值。此处把 v1.3 行的 single_cap 改成 K1 旧值
        (2 万),其余退出三字段都对——旧实现只核对 retrace/退出档会放行,新实现必须拒绝。"""
        db = isolated_env.db_path
        _seed_k1(db)
        bad = dict(TEST_RULE_V1_CONFIG)
        bad.update(max_positions=3, max_exposure_frac=1.0, take_profit_retrace=0.08,
                   time_exit_only_if_unprofitable=True, max_hold_days_profit=15)  # single_cap 仍 20000
        brain.save_version("v1.3", {"config": bad, "lineage": "K1"}, "仓位字段未改的 v1.3",
                           activate=False, db_path=db)
        assert activate_charter.activate(db, "v1.3", confirm=True) == 2
        assert brain.get_active(db_path=db).version == "K1"

    def test_whitelist_and_core_expectations_stay_in_sync(self):
        """结构性护栏:白名单里的每个版本都必须有一条核心值核对表(加白名单不许忘加核对)。"""
        assert set(activate_charter._ALLOWED_TARGETS) <= set(activate_charter._CORE_EXPECTATIONS)

    def test_dry_run_no_write(self, isolated_env):
        db = isolated_env.db_path
        self._prep(db)
        assert activate_charter.activate(db, "v1.3", confirm=False) == 0
        assert brain.get_active(db_path=db).version == "K1"              # dry-run 不写库

    def test_confirm_activates_v13(self, isolated_env):
        db = isolated_env.db_path
        self._prep(db)
        assert activate_charter.activate(db, "v1.3", confirm=True) == 0
        act = brain.get_active(db_path=db)
        assert act.version == "v1.3"
        assert act.rule["config"]["take_profit_retrace"] == 0.08
        # 唯一现役
        assert [v.version for v in brain.list_versions(db_path=db) if v.is_active] == ["v1.3"]

    def test_reject_with_open_positions(self, isolated_env):
        db = isolated_env.db_path
        self._prep(db)
        pos_store.open_position("600001.SH", 10.0, 1000, date(2026, 7, 20), db_path=db)
        assert activate_charter.activate(db, "v1.3", confirm=True) == 1   # 有持仓拒绝
        assert brain.get_active(db_path=db).version == "K1"

    def test_reject_v13_wrong_retrace(self, isolated_env):
        """目标 v1.3 但 take_profit_retrace 非 0.08(错误/未改行)→ 拒绝激活。"""
        db = isolated_env.db_path
        _seed_k1(db)
        bad = dict(TEST_RULE_V1_CONFIG)
        bad.update(max_positions=3, single_cap=40000.0, max_exposure_frac=1.0,
                   time_exit_only_if_unprofitable=True, max_hold_days_profit=15)  # 但 retrace 仍 0.05
        brain.save_version("v1.3", {"config": bad, "lineage": "K1"}, "错误 v1.3", activate=False, db_path=db)
        assert activate_charter.activate(db, "v1.3", confirm=True) == 2
        assert brain.get_active(db_path=db).version == "K1"
