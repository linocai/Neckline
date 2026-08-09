"""V2.2-④-E 周度作业(`scripts/weekly.py` + `deploy/neckline-weekly.{service,timer}`)。

两组:
  ① 脚本语义 —— 三步各自失败时另一步照跑 · **任一步失败 → exit 1** · 幂等重跑;
  ② unit 文件守门 —— 把 §七 **P0-45** 的两条教训钉成机器判据
     (timer **不含** `Unit=…target` · service **永不含** `RemainAfterExit`)。

⚠ ② 那两条是「周期性的东西必须验第二次」的**静态**一半:它们证明形态没写错;
   「跨两次触发的不变量」只能在生产上验(plan ④ 验收第 ③ 条,drop-in + 分钟级
   `OnCalendar` 观测连续两次触发)—— ⛔ 别拿这两条当那一条已经做过了。
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "weekly.py"
_SERVICE = _ROOT / "deploy" / "neckline-weekly.service"
_TIMER = _ROOT / "deploy" / "neckline-weekly.timer"


def _load():
    spec = importlib.util.spec_from_file_location("_weekly_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_weekly_under_test"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def weekly():
    return _load()


def _run(weekly, monkeypatch, argv, *, fail=()):
    """跑一次 `main()`,把三步各自换成"记一笔 / 抛"的桩。返回 `(exit_code, 跑过的步)`。"""
    ran = []

    def _mk(name):
        def _fn(*a, **kw):
            ran.append(name)
            if name in fail:
                raise RuntimeError(f"{name} 炸了")
            return f"{name} ok"
        return _fn

    monkeypatch.setattr(weekly, "step_profile", _mk("profile"))
    monkeypatch.setattr(weekly, "step_trade_clocks", _mk("clocks"))
    monkeypatch.setattr(weekly, "step_calibration", _mk("calibration"))
    monkeypatch.setattr(sys, "argv", ["weekly.py", *argv])
    return weekly.main(), ran


# ══════════════════════════════════════════════════════════════════════════
# ① 脚本语义
# ══════════════════════════════════════════════════════════════════════════

class TestExitSemantics:
    def test_all_three_steps_run_and_exit_zero(self, weekly, monkeypatch):
        code, ran = _run(weekly, monkeypatch, [])
        assert code == 0
        assert ran == ["profile", "clocks", "calibration"]

    @pytest.mark.parametrize("broken", ["profile", "clocks", "calibration"])
    def test_one_step_failing_never_stops_the_others(self, weekly, monkeypatch, broken):
        """承晚间链的保险丝哲学:一步失败,另一步照跑。"""
        code, ran = _run(weekly, monkeypatch, [], fail=(broken,))
        assert ran == ["profile", "clocks", "calibration"]
        assert code == 1

    def test_any_failure_exits_one_so_execmainstatus_tells_the_truth(self, weekly, monkeypatch):
        """🔴 §铁律「timer 跑过 ≠ 任务成功」:验收看 `ExecMainStatus`,
        所以脚本必须在任一步失败时给非零码。"""
        code, _ = _run(weekly, monkeypatch, [], fail=("profile", "calibration"))
        assert code == 1

    def test_skip_flags_skip_without_failing(self, weekly, monkeypatch):
        code, ran = _run(weekly, monkeypatch, ["--skip-profile", "--skip-clocks"])
        assert code == 0 and ran == ["calibration"]

    def test_default_target_week_is_the_previous_full_week(self, weekly):
        """周六 09:00 触发时「本周」的周末还没到 —— 取上一周才是走完的窗口。"""
        import argparse

        anchor = weekly._target_week(argparse.Namespace(week=None))
        assert (date.today() - anchor).days == 7

    def test_explicit_week_wins(self, weekly):
        import argparse

        assert weekly._target_week(argparse.Namespace(week="20260805")) == date(2026, 8, 5)


class TestStepsAreReusedNotRewritten:
    def test_profile_step_reuses_the_existing_modules(self):
        src = _SCRIPT.read_text(encoding="utf-8")
        assert "from neckline.profile import capability" in src
        assert "from neckline.profile import preference" in src

    def test_calibration_step_reuses_build_and_write_report(self):
        src = _SCRIPT.read_text(encoding="utf-8")
        assert "calibration.build_report(" in src and "calibration.write_report(" in src

    def test_script_never_pushes(self):
        """⛔ 不推送(不新增 kind → 不触发「新增推送须用户拍板」纪律)。"""
        from .conftest import source_code_only

        code = source_code_only(_SCRIPT)
        for banned in ("notify", "push_", "apns"):
            assert banned not in code.lower(), f"周度作业碰了推送:{banned}"

    def test_script_never_writes_the_selection_pack(self):
        from .conftest import source_code_only

        code = source_code_only(_SCRIPT).upper()
        for banned in ("SELECTION_PACKS", "ACTIVATE_PACK"):
            assert banned not in code


class TestIdempotentRerun:
    def test_running_twice_on_an_empty_db_is_clean_and_zero(self, isolated_env, tmp_path,
                                                            monkeypatch):
        """真跑两遍(空库):幂等、退出码 0、产物落盘。"""
        weekly = _load()
        monkeypatch.setattr(sys, "argv", [
            "weekly.py", "--week", "20260805", "--no-placebo", "--no-tradable",
            "--out", str(tmp_path), "--db", str(isolated_env.db_path)])
        from .conftest import business_days, insert_trade_cal

        insert_trade_cal(isolated_env, business_days(date(2026, 8, 3), 10))
        assert weekly.main() == 0
        first = sorted(p.name for p in tmp_path.iterdir())
        assert weekly.main() == 0
        assert sorted(p.name for p in tmp_path.iterdir()) == first
        assert any(n.endswith(".md") for n in first)
        assert any(n.endswith(".json") for n in first)


# ══════════════════════════════════════════════════════════════════════════
# ② unit 文件守门(§七 P0-45 的两条教训 → 机器判据)
# ══════════════════════════════════════════════════════════════════════════

def _lines(path: Path):
    """剥掉注释行再看 —— 本项目吃过「配置文件自己的护栏注释把 grep 绊红」的亏
    (CLAUDE.md ⑰:一个对自己的注释报警的闸门等于没有闸门)。"""
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


class TestUnitFiles:
    def test_both_unit_files_exist(self):
        assert _SERVICE.exists() and _TIMER.exists()

    def test_timer_triggers_a_service_never_a_target(self):
        """🔴 §七 **P0-45**:`.target` 不会自己落下 → timer 卡在 `running`、NEXT 永不
        重算 → **首次必成、之后必哑**。周度只有三步,不需要 target 的编排能力。"""
        units = [ln for ln in _lines(_TIMER) if ln.startswith("Unit=")]
        assert units == ["Unit=neckline-weekly.service"]
        assert not any(ln.lower().endswith(".target") for ln in units)

    def test_service_never_remains_after_exit(self):
        """🔴 P0-45 连带铁律:`RemainAfterExit=yes` 会让 `ExecStart` 一行不执行地静默空跑。"""
        assert not any(ln.startswith("RemainAfterExit") for ln in _lines(_SERVICE))

    def test_service_is_oneshot_and_runs_as_the_service_user(self):
        body = _lines(_SERVICE)
        assert "Type=oneshot" in body
        assert "User=neckline" in body and "Group=neckline" in body

    def test_service_execs_the_weekly_script(self):
        assert any(ln.startswith("ExecStart=") and "scripts/weekly.py" in ln
                   for ln in _lines(_SERVICE))

    def test_timer_is_saturday_0900_shanghai_and_persistent(self):
        """`Persistent=true` 与 evening 的 `false` **刻意不同**:周频漏一次就是一周,
        而它幂等、不推送、不写业务判定表,补跑无害。"""
        body = _lines(_TIMER)
        assert "OnCalendar=Sat 09:00 Asia/Shanghai" in body
        assert "Persistent=true" in body
        assert "WantedBy=timers.target" in body      # [Install] 里的这条不是被触发单元

    def test_evening_timer_stays_non_persistent(self):
        """反向对照:evening 会推 APNs,**不许**补跑 —— 两者的差别是"补跑会不会打扰用户"。"""
        assert "Persistent=false" in _lines(_ROOT / "deploy" / "neckline-evening.timer")

    def test_service_declares_timeout_and_memory_caps(self):
        body = _lines(_SERVICE)
        assert any(ln.startswith("TimeoutStartSec=") for ln in body)
        assert any(ln.startswith("MemoryMax=") for ln in body)

    def test_the_pending_calibration_note_is_still_there(self):
        """两个资源上限**待生产隔离实测校准**(§七 P0-23)—— 实测前那句提示不许被删掉,
        否则占位值会被后人当成量过的结论。"""
        head = _SERVICE.read_text(encoding="utf-8")
        assert "待生产隔离实测校准" in head or "待实测校准" in head
        assert "systemd-run" in head and "--scope" in head   # nk 上不许用 root --scope
