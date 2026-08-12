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


def _run(weekly, monkeypatch, argv, *, fail=(), degraded=()):
    """跑一次 `main()`,把三步各自换成"记一笔 / 抛"的桩。返回 `(exit_code, 跑过的步)`。

    ⚠ `step_calibration` 的契约是 **`(摘要, 降级段列表)`**(§七 P0-56),与另两步的
    「返回一句摘要」刻意不同 —— 因为 `build_report` 永不抛异常,段炸掉走不到 except,
    只能靠这第二项把真相带上来。`degraded=` 用来构造那种「没抛异常但被掏空」的跑。
    """
    ran = []

    def _mk(name):
        def _fn(*a, **kw):
            ran.append(name)
            if name in fail:
                raise RuntimeError(f"{name} 炸了")
            if name == "calibration":
                return (f"{name} ok", list(degraded))
            return f"{name} ok"
        return _fn

    monkeypatch.setattr(weekly, "step_profile", _mk("profile"))
    monkeypatch.setattr(weekly, "step_trade_clocks", _mk("clocks"))
    monkeypatch.setattr(weekly, "step_calibration", _mk("calibration"))
    # ⚠ 步 4 / 步 5 也换成桩,但**不进 `ran`** —— 上面那些断言锁的是前三步的顺序与
    # 「一步失败另一步照跑」,把新步塞进 `ran` 会让它们全部要改一遍(而它们守的东西
    # 没变)。真正的理由是**测试隔离**:不打桩这两步会去读**真实项目库**
    # (`settings.db_path`,CLAUDE.md「测试隔离」条明写的那类泄漏)。
    monkeypatch.setattr(weekly, "step_out_shadow_review", lambda *a, **kw: "out_review stub")
    monkeypatch.setattr(weekly, "step_auction_eval", lambda *a, **kw: "auction_eval stub")
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

    # ── 🔴 P0-56 回归守门:段被掏空但没抛异常,退出码必须说真话 ────────────────
    def test_degraded_segments_exit_one_even_though_nothing_raised(
            self, weekly, monkeypatch):
        """🔴 **P0-56 的真实形状**:`build_report` 永不抛异常 —— 安慰剂对照臂炸了只记
        note、报告照落盘、`step_calibration` 正常返回。**生产上真的发生过**:
        `v2.2-k8` 激活后判分引擎对现役章程恒抛 `ValueError`,三段全废,而日志末行
        仍是「周度作业完成(全部步骤成功)」、`ExecMainStatus=0`。

        铁律说「验收看 `ExecMainStatus=0` 且本次时间戳」—— 所以那个绿灯**必须**
        因降级而变红,否则铁律本身被架空。⛔ 别把这条改成"只警告不改退出码"。"""
        code, ran = _run(weekly, monkeypatch, [], degraded=("placebo", "strata"))
        assert ran == ["profile", "clocks", "calibration"], "降级不该打断任何一步"
        assert code == 1, "有段没跑成却返 0 —— 绿灯盖在一次被掏空的跑上(P0-56)"

    def test_no_degradation_still_exits_zero(self, weekly, monkeypatch):
        """反向:没有降级段就不许平白变红(⛔ 别把守门做成"永远失败")。"""
        code, _ = _run(weekly, monkeypatch, [], degraded=())
        assert code == 0

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

    def test_quotas_are_measured_not_placeholders(self):
        """✅ **2026-08-11 已生产隔离实测校准**,占位值销案(§七 P0-23)。

        ⚠ 本测试**原先断言的是「待校准那句提示还在」** —— 那是实测前的守门:防止有人
        把提示删掉、让占位值被后人当成量过的结论。实测做完了,守门因此**翻面**:
        现在锁的是「占位值不许回来」+「读数与理由必须留在文件里」。

        ⛔ 别把这条改回去 —— 两个方向的守门是同一个目的的两个阶段,不是重复。"""
        head = _SERVICE.read_text(encoding="utf-8")
        body = _lines(_SERVICE)

        # ① 占位值不许回来(3600 = "实际上没有超时";1400M 比实测需要多 3.5 倍)
        assert "TimeoutStartSec=3600" not in body, "3600 是占位值,一小时超时等于没有超时"
        assert "MemoryMax=1400M" not in body, "1400M 是占位值,真泄漏时兜不住"
        assert "MemoryMax=800M" in body
        # 🔴 **2026-08-11 V2.3.2-③-B 起 `TimeoutStartSec` 不再是 900**:③-B 给本作业
        # 加了一次周度 LLM 调用,而 900 **恰等于** `REVIEW_BUDGET_SECONDS` —— 「预算
        # 耗尽」与「systemd SIGTERM」会落在同一秒。守门因此从"钉住 900"改成钉住那条
        # **不变量**(> 预算上限),数值本身留给实测收敛。
        # ⛔ 别把它改回 `== 900`(那正是要防的那颗雷)。
        # ⚠ 不变量的正面守门在 `tests/test_out_shadow.py::
        #   test_weekly_unit_timeout_is_strictly_above_the_llm_budget`,这里只锁"别退回去"。
        assert "TimeoutStartSec=900" not in body, "900 恰等于 REVIEW_BUDGET_SECONDS,是雷"

        # ② 实测读数与选值理由必须留在文件里(否则下一个人无从判断该不该动它)
        assert "已生产隔离实测校准" in head
        assert "400M 扛住" in head and "256M 被 OOM-kill" in head, "反证读数是选值的唯一依据"
        assert "改这两个数必须重新实测" in head

        # ③ 🔧 **2026-08-11 V2.3.2 批 6 部署实测后,本组守门第二次翻面**。
        #    上一版锁的是「③-B 的欠账句(『1800 …不是重新实测出来的』/『部署时必须』)
        #    还在」—— 那是**实测前**的守门:防止有人把欠账悄悄销掉。⑥ 部署时那次实测
        #    真做了(带步 4 的完整周度作业量了两次,读数见 unit 文件头),欠账因此**合法
        #    销案**,守门随之从「欠账句必须在」翻成「欠账句必须没了 + 新读数必须在」。
        #    ⛔ 别把它改回 `assert "不是重新实测出来的" in head` —— 那会要求文件里同时
        #    写着"已经量过了"和"还没量过",两句话只能有一句是真的。
        assert "不是重新实测出来的" not in head, "③-B 欠账已销案,这句话不该还在"
        assert "TimeoutStartSec=1800" not in body, "1800 是收敛前的保守值,已由实测替换"
        assert "TimeoutStartSec=3000" in body
        # 步 4 那次 LLM 调用的**两次**读数(同输入 1.44 倍差)是选 3000 的直接依据 ——
        # 少了它们,3000 又变回一个"看起来挺大"的拍脑袋数。
        assert "166.2s" in head and "115.2s" in head, "步 4 LLM 段的实测读数是选值依据"
        assert "1.44 倍" in head, "同输入两跑的离散度必须留痕(它解释了为什么留 6 倍余量)"

        # ④ nk 上不许用 root `--scope`(会把行情文件写成 root 属主)—— 这条恒有效
        assert "systemd-run" in head and "--scope" in head
