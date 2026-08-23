"""🔴 **双日期契约的唯一机器守门**(LRN-20260816-001,PROJECT_PLAN §12 坑 9)。

> `report_date` 管**标题 / 推送 / 可见身份**;`trade_date` 管 **EOD 读数 / 清单 /
> 预案 / 审计键**。周日报告:`report_date=周日`、`trade_date=紧邻上一周五`。

混同这两个日期会让标题 / 推送 / 可见身份**全错**,而底层计算看起来是对的 ——
**最难发现的一类错**。这条契约是用户 2026-08-16 的反馈固化下来的,⛔ 不许退化。

⚠ **本文件在 S1 随 `scripts/evening.py` 一并被删,S7 从 `eac2823` 取回并按 K9 报告链
改写**(S1 登记 ④ 点名要求)。改写只动了三处:段名(`verify,scan,basket,review,report`
→ `facts,k9,explain,playbook,report`)、防重查的表(`reports` → `k9_reports`)、
参数包开关(`--direction-pipeline-config` → `--k9-params`)。**四条契约断言逐字保留。**

四条契约:
| # | 断言 |
|---|---|
| 1 | 周日槽绑定**紧邻的上一个周五**,周一至周四槽绑定当天 |
| 2 | 周日槽传下去的是 `report_date=周日` / `trade_date=周五`(**两个日期都要**) |
| 3 | 该周五休市 → **安全跳过**,⛔ 不回退到周四重发一份旧报告 |
| 4 | 同日已生成 → **整链跳过**,⛔ 零重复 APNs |
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import evening as evening_script

BACKEND_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════════
# 契约 1 · 定时槽绑定哪一个交易日
# ══════════════════════════════════════════════════════════════════════════

def test_sunday_slot_binds_exactly_to_the_immediately_preceding_friday():
    assert evening_script._scheduled_trade_date(date(2026, 8, 16)) == date(2026, 8, 14)


def test_weekday_slot_stays_on_that_calendar_day():
    assert evening_script._scheduled_trade_date(date(2026, 8, 17)) == date(2026, 8, 17)
    assert evening_script._scheduled_trade_date(date(2026, 8, 20)) == date(2026, 8, 20)


# ══════════════════════════════════════════════════════════════════════════
# 契约 2 · 两个日期都要传下去
# ══════════════════════════════════════════════════════════════════════════

def _fake_chain(captured):
    def chain(trade_date, **kwargs):
        captured["trade_date"] = trade_date
        captured["report_date"] = kwargs["report_date"]
        captured["k9_params_path"] = kwargs.get("k9_params_path")
        return SimpleNamespace(
            status={segment: "ok" for segment in evening_script.CHAIN_SEGMENTS},
            stats={}, notes=[], bundle=None,
        )
    return chain


def test_sunday_slot_passes_sunday_report_date_but_friday_trade_date(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(evening_script, "_today", lambda: date(2026, 8, 16))
    monkeypatch.setattr(evening_script, "is_trading_day", lambda value: True)
    monkeypatch.setattr(evening_script, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(evening_script, "_report_generated_on_local_day",
                        lambda *a, **k: False)
    monkeypatch.setattr(evening_script, "run_evening_chain", _fake_chain(captured))
    monkeypatch.setattr(sys, "argv", [
        "evening.py", "--scheduled", "--no-save", "--db", str(tmp_path / "x.db")])

    assert evening_script.main() == 0
    assert captured["trade_date"] == date(2026, 8, 14)
    assert captured["report_date"] == date(2026, 8, 16)


def test_manual_backfill_can_name_the_publication_date_explicitly(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(evening_script, "is_trading_day", lambda value: True)
    monkeypatch.setattr(evening_script, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(evening_script, "run_evening_chain", _fake_chain(captured))
    monkeypatch.setattr(sys, "argv", [
        "evening.py", "20260814", "--report-date", "20260816", "--no-save",
        "--db", str(tmp_path / "x.db"),
    ])

    assert evening_script.main() == 0
    assert captured["trade_date"] == date(2026, 8, 14)
    assert captured["report_date"] == date(2026, 8, 16)


# ══════════════════════════════════════════════════════════════════════════
# 契约 3 · 休市安全跳过(⛔ 不回退重跑旧报告)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("scheduled_day", [date(2026, 8, 17), date(2026, 8, 16)])
def test_scheduled_holiday_is_clean_noop_and_never_falls_back(
    monkeypatch, scheduled_day,
):
    """周一至周四的节假日，以及周日前一个周五休市，都必须整链跳过。"""
    monkeypatch.setattr(evening_script, "_today", lambda: scheduled_day)
    monkeypatch.setattr(evening_script, "is_trading_day", lambda value: False)
    monkeypatch.setattr(evening_script, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(
        evening_script,
        "run_evening_chain",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    monkeypatch.setattr(sys, "argv", ["evening.py", "--scheduled"])
    assert evening_script.main() == 0


# ══════════════════════════════════════════════════════════════════════════
# 契约 4 · 同日已生成 → 整链跳过(⛔ 零重复 APNs)
# ══════════════════════════════════════════════════════════════════════════

def test_sunday_slot_skips_when_friday_report_was_already_generated_that_day(
    tmp_path, monkeypatch,
):
    db_path = tmp_path / "scheduled.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"CREATE TABLE {evening_script.K9_TABLE} "
            "(trade_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL)")
        conn.execute(
            f"INSERT INTO {evening_script.K9_TABLE} VALUES (?, ?)",
            ("20260814", "2026-08-16T10:30:00+00:00"),
        )

    assert evening_script._report_generated_on_local_day(
        date(2026, 8, 14), date(2026, 8, 16), db_path,
    )
    assert not evening_script._report_generated_on_local_day(
        date(2026, 8, 14), date(2026, 8, 15), db_path,
    )

    monkeypatch.setattr(evening_script, "_today", lambda: date(2026, 8, 16))
    monkeypatch.setattr(evening_script, "is_trading_day", lambda value: True)
    monkeypatch.setattr(evening_script, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(
        evening_script,
        "run_evening_chain",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    monkeypatch.setattr(sys, "argv", ["evening.py", "--scheduled", "--db", str(db_path)])
    assert evening_script.main() == 0


def test_a_report_row_that_was_never_generated_does_not_block_the_slot(tmp_path):
    """空库 / 没有那张表 → **不跳过**(⛔ 别把「查不到」读成「已经跑过」)。"""
    assert not evening_script._report_generated_on_local_day(
        date(2026, 8, 14), date(2026, 8, 16), tmp_path / "missing.db")


# ══════════════════════════════════════════════════════════════════════════
# 排程与三个 oneshot 单元共享同一份契约
# ══════════════════════════════════════════════════════════════════════════

def test_timer_and_all_three_services_share_the_scheduled_date_contract():
    timer = (BACKEND_ROOT / "deploy" / "neckline-evening.timer").read_text(encoding="utf-8")
    calendars = [line for line in timer.splitlines() if line.startswith("OnCalendar=")]
    assert calendars == [
        "OnCalendar=Mon-Thu 19:00 Asia/Shanghai",
        "OnCalendar=Sun 19:00 Asia/Shanghai",
    ]

    service_names = ("neckline-facts.service", "neckline-strategy.service",
                     "neckline-report.service")
    for service_name in service_names:
        unit = (BACKEND_ROOT / "deploy" / service_name).read_text(encoding="utf-8")
        exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
        assert "scripts/evening.py --scheduled " in exec_start


def test_the_three_oneshots_cover_the_new_segment_order_exactly_once():
    """晚间段序 `facts → direction → k9 → explain → playbook → report` 被三个单元
    **不重不漏**地分完。漏一段 = 那一层每晚静默不跑,而 timer 看起来一切正常。"""
    covered: list = []
    for name in ("neckline-facts.service", "neckline-strategy.service",
                 "neckline-report.service"):
        unit = (BACKEND_ROOT / "deploy" / name).read_text(encoding="utf-8")
        exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
        parts = exec_start.split()
        covered.extend(parts[parts.index("--segments") + 1].split(","))
    assert covered == list(evening_script.CHAIN_SEGMENTS), covered


def test_the_strategy_unit_passes_a_parameter_package_explicitly():
    """⛔ **无默认参数路径**(裁定 5):跑策略层的那个单元必须显式传 `--k9-params`。"""
    unit = (BACKEND_ROOT / "deploy" / "neckline-strategy.service").read_text(encoding="utf-8")
    exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert "--k9-params " in exec_start
    assert "direction-pipeline" not in exec_start, "K8 时代的方向流水线配置已退役"


def test_retired_k8_evening_unit_names_and_config_are_absent():
    """现役拓扑不再借用 scan/basket 名字；Git 历史已经承担旧链追溯。"""
    deploy = BACKEND_ROOT / "deploy"
    assert not (deploy / "neckline-scan.service").exists()
    assert not (deploy / "neckline-basket.service").exists()
    assert not (BACKEND_ROOT / "config" / "direction-pipeline.v2.4.2-balanced.json").exists()
    target = (deploy / "neckline-evening.target").read_text(encoding="utf-8")
    assert "neckline-facts.service" in target
    assert "neckline-strategy.service" in target
    live_lines = "\n".join(line for line in target.splitlines()
                           if line.startswith(("Wants=", "After=")))
    assert "neckline-scan.service" not in live_lines
    assert "neckline-basket.service" not in live_lines
