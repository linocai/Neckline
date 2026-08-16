from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from neckline.report.basket_daily import card_to_public_dict
from neckline.report.card_plan_repair import (
    CardRepair,
    apply_report_card_repairs,
    build_repair_context,
    json_sha256,
    patch_report_markdown,
    patch_report_snapshot,
    repair_frozen_card,
)
from neckline.report.store import load_report, save_report
from neckline.selection.basket_card import trade_plan_missing_pieces
from neckline.selection.basket_store import load_basket_card, save_basket_card


D0 = date(2026, 8, 14)
REPORT_DATE = date(2026, 8, 16)
BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _old_card():
    return {
        "spec_version": "v5", "version": 1, "basket_key": "event-demo",
        "trade_date": "20260814", "name": "事件驱动篮子", "driver": "公开事件驱动",
        "driver_kind": "event", "why_now": "周末出现公开进展", "tier": 2,
        "rank_in_tier": 1, "rank_mech": 1, "mech_score": 0.7,
        "evidence": [{"claim": "交易所披露进展", "source": "交易所", "date": "2026-08-15"}],
        "evidence_status": "ok", "upside_path": None,
        "upside_path_unavailable_reason": "本次未生成该项",
        "verification_spec": {"all": [{"metric": "close", "op": ">=", "value": 10.0}]},
        "invalidation_spec": {"any": [{"metric": "close", "op": "<", "value": 9.5}]},
        "members": [{
            "ts_code": "600001.SH", "name": "样例股份", "role_llm": "core",
            "role_mech": "core", "role_conflict": 0, "reason": "容量代表", "is_primary": 1,
            "mech": {"close": 10.0, "ma20": 9.4, "limit_up": 11.0,
                     "limit_down": 9.0, "stop_price": 9.5},
            "entry_zone": None, "entry_zone_clamp": "absent",
            "entry_zone_unavailable_reason": "本次未生成该项",
            "max_chase": None, "max_chase_clamp": "absent",
            "max_chase_unavailable_reason": "本次未生成该项",
            "exit_reference": None, "exit_reference_clamp": "absent",
            "exit_reference_unavailable_reason": "本次未生成该项",
            "tags": [], "tags_absent": [],
        }],
        "verification_text": None, "invalidation_text": None, "risks": [],
        "tier_note": None, "narrative": "原篮子叙述", "llm_stage": "ok",
        "generation_source": "deep_reason", "degraded": False, "notes": [],
        "fingerprint": {"stop_pct": 0.05, "take_profit_retrace": None,
                        "charter_version": "v2.3-k8", "pack_version": "test-pack",
                        "engine_api_version": 2},
    }


def _material():
    return {
        "upside_path": "事件验证后先守住中枢，再沿均线逐级抬升",
        "entries": [{"ts_code": "600001.SH", "low": 9.8, "high": 10.2,
                     "max_chase": 10.5, "exit_low": 11.2, "exit_high": 12.0,
                     "why": "回踩当日实体中枢观察"}],
        "verification": "守住支撑并继续放量才算验证",
        "invalidation": "跌破机械失效条件则逻辑失效",
        "risks": ["公开事件进展不及预期"], "tier_note": "维持当前档位",
    }


def _snapshot(card):
    return {
        "tradeDate": "20260814", "basketsAvailable": True,
        "baskets": [{"basketId": 1, "basketKey": "event-demo", "name": "事件驱动篮子",
                     "tier": 2, "memberCodes": ["600001.SH"], "card": card_to_public_dict(card),
                     "cardVersion": 1, "cardUnavailableReason": None, "execHints": {},
                     "scorePercent": 70.0, "scoreContributions": []}],
        "droppedBaskets": [], "droppedBasketsAvailable": True,
        "outCandidates": [], "outCandidatesAvailable": True,
        "reviews": [], "reviewsAvailable": True, "notes": [],
    }


def test_repair_cli_bootstraps_backend_package_when_run_as_a_script():
    result = subprocess.run(
        [sys.executable, "scripts/repair_report_card_plans.py", "--help"],
        cwd=BACKEND_ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert "--expected-run-id" in result.stdout and "--apply" in result.stdout


def test_repair_uses_only_frozen_anchors_and_completes_all_trade_plan_pieces():
    old = _old_card()
    context = build_repair_context(old, trade_date=D0)
    assert "D0收盘=10.0" in context and "次日涨跌停参考价=[9.0,11.0]" in context
    assert "重新选股" in context and "不要改变篮子、成员、角色、档位或证据" in context
    repaired = repair_frozen_card(old, _material(), narrative="补全后的叙述", version=2)
    assert trade_plan_missing_pieces(repaired) == []
    assert repaired["tier"] == old["tier"] and repaired["members"][0]["role_llm"] == "core"
    assert repaired["members"][0]["entry_zone"] == {"low": 9.8, "high": 10.2,
                                                      "why": "回踩当日实体中枢观察"}
    assert repaired["generation_source"] == "targeted_card_repair"


def test_repair_rejects_prices_outside_frozen_mechanical_band():
    payload = _material()
    payload["entries"][0]["high"] = 11.5
    payload["entries"][0]["max_chase"] = 11.5
    with pytest.raises(ValueError, match="rejected_out_of_limit"):
        repair_frozen_card(_old_card(), payload, narrative="", version=2)


def test_apply_appends_card_and_patches_only_report_snapshot_atomically(isolated_env):
    old = _old_card()
    snapshot = _snapshot(old)
    markdown = "# report\n\n## ③ 今日篮子\n\n旧卡\n\n### ③b 今日未定档篮子(档位已满)\n\n无\n"
    save_basket_card(1, old, version=1, db_path=isolated_env.db_path)
    save_report(
        D0, report_date=REPORT_DATE, strategy_version="v2.3-k8", sentiment={}, sectors=[],
        candidates=[], markdown=markdown, basket_daily=snapshot, db_path=isolated_env.db_path,
    )
    before = load_report(D0, db_path=isolated_env.db_path)
    repaired = repair_frozen_card(old, _material(), narrative="补全后的叙述", version=2)
    plan = CardRepair(1, 1, 2, json_sha256(old), repaired)
    new_snapshot = patch_report_snapshot(snapshot, {1: plan})
    new_markdown = patch_report_markdown(markdown, new_snapshot, trade_date=D0)
    apply_report_card_repairs(
        trade_date="20260814", report_date="20260816",
        expected_snapshot_sha256=json_sha256(snapshot), repairs={1: plan},
        snapshot=new_snapshot, markdown=new_markdown, db_path=isolated_env.db_path,
    )
    after = load_report(D0, db_path=isolated_env.db_path)
    assert after["generated_at"] == before["generated_at"]
    assert after["report_date"] == "20260816"
    assert after["basket_daily"]["baskets"][0]["tier"] == 2
    assert after["basket_daily"]["baskets"][0]["cardVersion"] == 2
    assert after["basket_daily"]["baskets"][0]["card"]["members"][0]["entryZone"]["low"] == 9.8
    assert "9.80~10.20" in after["markdown"] and "旧卡" not in after["markdown"]
    assert load_basket_card(1, version=1, db_path=isolated_env.db_path)["card"] == old
    assert load_basket_card(1, db_path=isolated_env.db_path)["version"] == 2


def test_apply_stale_snapshot_rolls_back_without_appending_card(isolated_env):
    old = _old_card()
    snapshot = _snapshot(old)
    save_basket_card(1, old, version=1, db_path=isolated_env.db_path)
    save_report(
        D0, report_date=REPORT_DATE, strategy_version="v2.3-k8", sentiment={}, sectors=[],
        candidates=[], markdown="## ③ 今日篮子\n\nold\n\n### ③b 今日未定档篮子(档位已满)\n",
        basket_daily=snapshot, db_path=isolated_env.db_path,
    )
    repaired = repair_frozen_card(old, _material(), narrative="", version=2)
    plan = CardRepair(1, 1, 2, json_sha256(old), repaired)
    with pytest.raises(ValueError, match="snapshot changed"):
        apply_report_card_repairs(
            trade_date="20260814", report_date="20260816", expected_snapshot_sha256="stale",
            repairs={1: plan}, snapshot=patch_report_snapshot(snapshot, {1: plan}),
            markdown="new", db_path=isolated_env.db_path,
        )
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        assert conn.execute("SELECT count(*) FROM basket_cards").fetchone()[0] == 1
    finally:
        conn.close()
