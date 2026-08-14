from __future__ import annotations

import sqlite3
import inspect
from datetime import date
from types import SimpleNamespace

import pytest

from neckline.selection.run_store import (
    add_direction, add_event, create_run, finish_run, init_selection_run_schema,
    latest_publication_state, record_llm_call, update_direction_disposition,
)


def _config():
    return {"version": "v1", "deep_initial_limit": 20}


def test_run_trace_is_append_only_and_usage_unavailable_is_explicit(tmp_path):
    db = tmp_path / "selection.sqlite3"
    init_selection_run_schema(db)
    run = create_run("20260814", _config(), db_path=db)
    add_direction(run, direction_id="d1", ordinal=0, seed_keys=["s1"], brief={"label": "one"}, db_path=db)
    call = record_llm_call(run, task="triage", batch_no=0, provider="p", model="m", enable_search=False,
                           wall_ms=12, usage=None, db_path=db)
    add_event(run, "triage_retry", direction_id="d1", llm_call_id=call, db_path=db)
    update_direction_disposition(run, "d1", triage_disposition="reserve", db_path=db)
    finish_run(run, selection_state="partial", text="部分完成", published=True, db_path=db)
    assert latest_publication_state("20260814", db_path=db) == {"selectionState": "partial", "selectionStateText": "部分完成"}
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT usage_unavailable FROM selection_llm_calls").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM selection_direction_events").fetchone()[0] == 1
    conn.close()


def test_status_reader_is_read_only_and_safe_before_migration(tmp_path):
    db = tmp_path / "empty.sqlite3"
    assert latest_publication_state("20260814", db_path=db) is None
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='selection_runs'").fetchone()[0] == 0
    conn.close()


def test_publish_snapshot_rolls_back_basket_tier_card_and_run_on_card_failure(tmp_path, monkeypatch):
    """A failed final card write must expose neither staged facts nor published state."""
    from neckline.selection import basket_store
    from neckline.selection.aggregate import AggregateResult

    db = tmp_path / "publish.sqlite3"
    init_selection_run_schema(db)
    run = create_run("20260814", _config(), db_path=db)
    member = SimpleNamespace(
        ts_code="600001.SH", role_llm="core", role_mech=None, role_conflict=False,
        reason="test", is_primary=True,
    )
    basket = SimpleNamespace(
        trade_date="20260814", basket_key="candidate", name="candidate", driver="driver",
        driver_kind="theme", pack_version="test-pack", engine_api_version=1,
        charter_version="v1", evidence_status="ok", engine_code=None,
        engine_version=None, skeleton_version=None, members=(member,),
    )
    result = AggregateResult(trade_date="20260814", baskets=(basket,))
    history = {
        "candidate": {
            "basket_key": "candidate", "tier": 2, "mech_score": 1.0,
            "mech_breakdown": {}, "rank_in_tier": 1, "rank_mech": 1,
            "pack_version": "test-pack",
        }
    }

    def fail_card(_conn, _row):
        raise RuntimeError("injected card failure")

    monkeypatch.setattr(basket_store, "_save_basket_card_on_conn", fail_card)
    with pytest.raises(RuntimeError, match="injected card failure"):
        basket_store.publish_selection_snapshot(
            result, tier_by_basket_key={"candidate": 2}, tier_history_by_basket_key=history,
            cards_by_basket_key={"candidate": {"members": []}}, selection_run_id=run,
            db_path=db,
        )
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM baskets").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM tier_history").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM basket_cards").fetchone()[0] == 0
        assert conn.execute("SELECT publication_state FROM selection_runs WHERE run_id=?", (run,)).fetchone()[0] == "processing"
    finally:
        conn.close()


def test_usage_flag_remains_unavailable_when_partial_tokens_are_present(tmp_path):
    db = tmp_path / "usage.sqlite3"
    init_selection_run_schema(db)
    run = create_run("20260814", _config(), db_path=db)
    record_llm_call(
        run, task="triage", batch_no=1, provider="p", model="m", enable_search=False,
        wall_ms=1, usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                          "usage_unavailable": True}, db_path=db,
    )
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT usage_unavailable FROM selection_llm_calls").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("stage", ["basket", "tier", "card", "published"])
def test_publish_snapshot_rolls_back_each_final_stage(tmp_path, monkeypatch, stage):
    """Every final write shares one transaction; no stage may leak a half snapshot."""
    from neckline.selection import basket_store, run_store
    from neckline.selection.aggregate import AggregateResult

    db = tmp_path / f"{stage}.sqlite3"
    init_selection_run_schema(db)
    run = create_run("20260814", _config(), db_path=db)
    member = SimpleNamespace(ts_code="600001.SH", role_llm="core", role_mech=None,
                             role_conflict=False, reason="test", is_primary=True)
    basket = SimpleNamespace(
        trade_date="20260814", basket_key="candidate", name="candidate", driver="driver",
        driver_kind="theme", pack_version="test-pack", engine_api_version=1,
        charter_version="v1", evidence_status="ok", engine_code=None,
        engine_version=None, skeleton_version=None, members=(member,),
    )
    result = AggregateResult(trade_date="20260814", baskets=(basket,))
    history = {"candidate": {"basket_key": "candidate", "tier": 2, "mech_score": 1.0,
                             "mech_breakdown": {}, "rank_in_tier": 1, "rank_mech": 1,
                             "pack_version": "test-pack"}}

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"injected {stage} failure")

    target = {
        "basket": "_save_baskets_on_conn",
        "tier": "_save_tier_history_on_conn",
        "card": "_save_basket_card_on_conn",
    }.get(stage)
    if target:
        monkeypatch.setattr(basket_store, target, fail)
    else:
        monkeypatch.setattr(run_store, "finish_run", fail)
    with pytest.raises(RuntimeError, match=f"injected {stage} failure"):
        basket_store.publish_selection_snapshot(
            result, tier_by_basket_key={"candidate": 2}, tier_history_by_basket_key=history,
            cards_by_basket_key={"candidate": {"members": []}}, selection_run_id=run,
            db_path=db,
        )
    conn = sqlite3.connect(db)
    try:
        assert [conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("baskets", "tier_history", "basket_cards")] == [0, 0, 0]
        assert conn.execute("SELECT publication_state FROM selection_runs WHERE run_id=?", (run,)).fetchone()[0] == "processing"
    finally:
        conn.close()


def test_publish_snapshot_finishes_direction_as_frozen_in_same_commit(tmp_path):
    from neckline.selection import basket_store
    from neckline.selection.aggregate import AggregateResult

    db = tmp_path / "frozen.sqlite3"
    init_selection_run_schema(db)
    run = create_run("20260814", _config(), db_path=db)
    add_direction(run, direction_id="d1", ordinal=0, seed_keys=["s1"], brief={"label": "one"}, db_path=db)
    member = SimpleNamespace(ts_code="600001.SH", role_llm="core", role_mech=None,
                             role_conflict=False, reason="test", is_primary=True)
    basket = SimpleNamespace(
        trade_date="20260814", basket_key="candidate", name="candidate", driver="driver",
        driver_kind="theme", pack_version="test-pack", engine_api_version=1,
        charter_version="v1", evidence_status="ok", engine_code=None,
        engine_version=None, skeleton_version=None, members=(member,),
    )
    result = AggregateResult(trade_date="20260814", baskets=(basket,))
    history = {"candidate": {"basket_key": "candidate", "tier": 2, "mech_score": 1.0,
                             "mech_breakdown": {}, "rank_in_tier": 1, "rank_mech": 1,
                             "pack_version": "test-pack"}}
    basket_store.publish_selection_snapshot(
        result, tier_by_basket_key={"candidate": 2}, tier_history_by_basket_key=history,
        cards_by_basket_key={"candidate": {"members": []}},
        direction_id_by_basket_key={"candidate": "d1"}, selection_run_id=run, db_path=db,
    )
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT final_disposition FROM selection_directions WHERE run_id=? AND direction_id='d1'", (run,)).fetchone()[0] == "basket_frozen"
        assert conn.execute("SELECT count(*) FROM selection_direction_events WHERE run_id=? AND transition='basket_frozen'", (run,)).fetchone()[0] == 1
        assert conn.execute("SELECT publication_state FROM selection_runs WHERE run_id=?", (run,)).fetchone()[0] == "complete"
    finally:
        conn.close()


def _published_result(day: str, *keys: str):
    from neckline.selection.aggregate import AggregateResult

    member = SimpleNamespace(ts_code="600001.SH", role_llm="core", role_mech=None,
                             role_conflict=False, reason="test", is_primary=True)
    baskets = tuple(
        SimpleNamespace(
            trade_date=day, basket_key=key, name=f"{key}-name", driver=f"{key}-driver",
            driver_kind="theme", pack_version="test-pack", engine_api_version=1,
            charter_version="v1", evidence_status="ok", engine_code=None,
            engine_version=None, skeleton_version=None, members=(member,),
        )
        for key in keys
    )
    history = {
        key: {"basket_key": key, "tier": 2, "mech_score": 1.0,
              "mech_breakdown": {}, "rank_in_tier": i + 1, "rank_mech": i + 1,
              "pack_version": "test-pack"}
        for i, key in enumerate(keys)
    }
    cards = {key: {"members": [], "generation": key} for key in keys}
    return AggregateResult(trade_date=day, baskets=baskets), history, cards


def _gate_outcome(day: str, result, *keys: str):
    from neckline.selection.gates import BasketGateSummary, GateCheck, GateDayOutcome

    return GateDayOutcome(
        trade_date=day, result=result,
        summaries={
            key: BasketGateSummary(
                basket_key=key,
                checks=(GateCheck(gate="market", verdict="pass", reason=f"{key}-gate"),),
            )
            for key in keys
        },
    )


def test_same_day_published_generation_replaces_whole_visible_snapshot(tmp_path, monkeypatch):
    """A same-D0 replacement never unions A/B facts; failed B leaves A visible."""
    from neckline.selection import basket_store
    from neckline.selection.basket_dropped_handoff import load_dropped_handoff
    from neckline.selection.gates import load_gate_evaluations
    from neckline.selection.run_store import create_run
    from neckline.selection.tier import DroppedBasket

    db = tmp_path / "generations.sqlite3"
    init_selection_run_schema(db)
    day = "20260814"
    run_a = create_run(day, _config(), db_path=db)
    result_a, history_a, cards_a = _published_result(day, "shared", "only_a")
    basket_store.publish_selection_snapshot(
        result_a, tier_by_basket_key={"shared": 2, "only_a": 2},
        tier_history_by_basket_key=history_a, cards_by_basket_key=cards_a,
        gate_outcome=_gate_outcome(day, result_a, "shared", "only_a"),
        out_dropped=(DroppedBasket("only_a", "out_a", 1.0),), out_baskets_by_key={"only_a": result_a.baskets[1]},
        handoff_dropped=(DroppedBasket("only_a", "out_a", 1.0),),
        selection_run_id=run_a, db_path=db,
    )
    first_visible = basket_store.load_baskets_for_date(day, db_path=db)
    assert [b.basket_key for b in first_visible] == ["only_a", "shared"]
    assert {r["candidate_key"] for r in load_gate_evaluations(day, db_path=db)} == {"shared", "only_a"}
    assert [d.basket_key for d in load_dropped_handoff(date(2026, 8, 14), db_path=db)] == ["only_a"]

    run_b = create_run(day, _config(), db_path=db)
    result_b, history_b, cards_b = _published_result(day, "shared", "only_b")
    original = basket_store._save_basket_card_on_conn
    monkeypatch.setattr(basket_store, "_save_basket_card_on_conn", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("B failed")))
    with pytest.raises(RuntimeError, match="B failed"):
        basket_store.publish_selection_snapshot(
            result_b, tier_by_basket_key={"shared": 2, "only_b": 2},
            tier_history_by_basket_key=history_b, cards_by_basket_key=cards_b,
            gate_outcome=_gate_outcome(day, result_b, "shared", "only_b"),
            out_dropped=(DroppedBasket("only_b", "out_b", 1.0),), out_baskets_by_key={"only_b": result_b.baskets[1]},
            handoff_dropped=(DroppedBasket("only_b", "out_b", 1.0),),
            selection_run_id=run_b, db_path=db,
        )
    assert [b.basket_key for b in basket_store.load_baskets_for_date(day, db_path=db)] == ["only_a", "shared"]
    assert {r["candidate_key"] for r in load_gate_evaluations(day, db_path=db)} == {"shared", "only_a"}
    assert [d.basket_key for d in load_dropped_handoff(date(2026, 8, 14), db_path=db)] == ["only_a"]

    monkeypatch.setattr(basket_store, "_save_basket_card_on_conn", original)
    basket_store.publish_selection_snapshot(
        result_b, tier_by_basket_key={"shared": 2, "only_b": 2},
        tier_history_by_basket_key=history_b, cards_by_basket_key=cards_b,
        gate_outcome=_gate_outcome(day, result_b, "shared", "only_b"),
        out_dropped=(DroppedBasket("only_b", "out_b", 1.0),), out_baskets_by_key={"only_b": result_b.baskets[1]},
        handoff_dropped=(DroppedBasket("only_b", "out_b", 1.0),),
        selection_run_id=run_b, db_path=db,
    )
    visible = basket_store.load_baskets_for_date(day, db_path=db)
    assert [b.basket_key for b in visible] == ["only_b", "shared"]
    assert all(b.name.endswith("-name") for b in visible)
    assert basket_store.load_basket_card(visible[0].basket_id, db_path=db)["card"]["generation"] == "only_b"
    assert basket_store.load_basket(first_visible[0].basket_id, db_path=db) is None
    assert basket_store.load_tier_history(first_visible[0].basket_id, db_path=db) is None
    assert {r["candidate_key"] for r in load_gate_evaluations(day, db_path=db)} == {"shared", "only_b"}
    assert [d.basket_key for d in load_dropped_handoff(date(2026, 8, 14), db_path=db)] == ["only_b"]


def test_generation_migration_preserves_legacy_rows_and_repeated_init(tmp_path):
    """Old day-only constraints are rebuilt once; legacy facts retain generation ''."""
    from neckline.db import init_schema

    db = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db)
    try:
        conn.executescript("""
        CREATE TABLE baskets (
          id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL, basket_key TEXT NOT NULL,
          name TEXT NOT NULL, driver TEXT NOT NULL, driver_kind TEXT NOT NULL, tier INTEGER NOT NULL,
          pack_version TEXT NOT NULL, engine_api_version INTEGER NOT NULL, charter_version TEXT NOT NULL,
          via TEXT NOT NULL DEFAULT 'auto', evidence_status TEXT NOT NULL DEFAULT 'ok',
          created_at TEXT NOT NULL, UNIQUE(trade_date, basket_key)
        );
        INSERT INTO baskets(trade_date,basket_key,name,driver,driver_kind,tier,pack_version,engine_api_version,charter_version,via,evidence_status,created_at)
        VALUES('20260814','legacy','legacy','driver','theme',2,'p',1,'v','auto','ok','now');
        CREATE TABLE out_candidates (
          id INTEGER PRIMARY KEY AUTOINCREMENT, d0_date TEXT NOT NULL, basket_key TEXT NOT NULL,
          ts_code TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', role TEXT, engine_code TEXT,
          engine_version TEXT, skeleton_version TEXT, out_gate TEXT, out_reason TEXT NOT NULL,
          out_detail TEXT, created_at TEXT NOT NULL, UNIQUE(d0_date,basket_key,ts_code)
        );
        CREATE TABLE basket_dropped_handoff (
          trade_date TEXT PRIMARY KEY, dropped_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        INSERT INTO basket_dropped_handoff VALUES('20260814','[]','now');
        CREATE TABLE gate_evaluations (
          id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL, candidate_key TEXT NOT NULL,
          ts_code TEXT, gate TEXT NOT NULL, gate_kind TEXT NOT NULL, verdict TEXT NOT NULL,
          score REAL, threshold REAL, engine_code TEXT, engine_version TEXT,
          evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
        );
        INSERT INTO gate_evaluations(trade_date,candidate_key,gate,gate_kind,verdict,evidence_json,created_at)
        VALUES('20260814','legacy','market','mech','pass','{}','now');
        """)
        conn.commit()
    finally:
        conn.close()
    init_schema(db)
    init_schema(db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT selection_run_id FROM baskets WHERE basket_key='legacy'").fetchone()[0] == ""
        assert "UNIQUE(trade_date, basket_key, selection_run_id)" in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='baskets'"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT selection_run_id FROM basket_dropped_handoff WHERE trade_date='20260814'"
        ).fetchone()[0] == ""
    finally:
        conn.close()
    from neckline.selection.gates import load_gate_evaluations
    assert [row["candidate_key"] for row in load_gate_evaluations("20260814", db_path=db)] == ["legacy"]


def test_selection_readers_never_call_schema_initialization(tmp_path, monkeypatch):
    """A report/API read is not a migration entry point, even on a fresh DB."""
    from neckline.db import init_schema
    from neckline.selection import basket_dropped_handoff, basket_stage_handoff, basket_store, gates

    db = tmp_path / "guard.sqlite3"
    init_schema(db)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("production reader attempted init_schema")

    for module in (basket_store, gates, basket_dropped_handoff, basket_stage_handoff):
        monkeypatch.setattr(module, "init_schema", forbidden)

    assert basket_store.load_baskets_for_date("20260814", db_path=db) == []
    assert basket_store.load_basket(1, db_path=db) is None
    assert basket_store.load_basket_card(1, db_path=db) is None
    assert basket_store.load_tier_history(1, db_path=db) is None
    assert basket_store.load_out_candidates("20260814", db_path=db) == []
    assert gates.load_gate_evaluations("20260814", db_path=db) == []
    assert basket_dropped_handoff.load_dropped_handoff(date(2026, 8, 14), db_path=db) is None
    assert basket_stage_handoff.load_stage_verdict(date(2026, 8, 14), db_path=db) is None

    readers = (
        basket_store.next_card_version, basket_store.load_basket_card,
        basket_store.load_baskets_for_date, basket_store.load_basket,
        basket_store.load_tier_history, basket_store.load_out_candidates,
        gates.load_gate_evaluations, basket_dropped_handoff.load_dropped_handoff,
        basket_stage_handoff.load_stage_verdict,
    )
    assert all("init_schema(" not in inspect.getsource(reader) for reader in readers)


def test_legacy_selection_reads_do_not_mutate_schema_before_explicit_migration(tmp_path):
    """Missing V2.4.2 tables/columns remain a readable legacy snapshot, not DDL."""
    from neckline.db import init_schema
    from neckline.selection.basket_dropped_handoff import load_dropped_handoff
    from neckline.selection.basket_stage_handoff import load_stage_verdict
    from neckline.selection.basket_store import (
        load_basket_card, load_baskets_for_date, load_out_candidates, load_tier_history,
    )
    from neckline.selection.gates import load_gate_evaluations

    db = tmp_path / "legacy_read.sqlite3"
    conn = sqlite3.connect(db)
    try:
        conn.executescript("""
        CREATE TABLE baskets (
          id INTEGER PRIMARY KEY, trade_date TEXT, basket_key TEXT, name TEXT, driver TEXT,
          driver_kind TEXT, tier INTEGER, pack_version TEXT, engine_api_version INTEGER,
          charter_version TEXT, via TEXT, evidence_status TEXT, created_at TEXT
        );
        INSERT INTO baskets VALUES (1,'20260814','legacy','Legacy','d','theme',2,'p',1,'v','auto','ok','now');
        CREATE TABLE basket_cards (
          id INTEGER PRIMARY KEY, basket_id INTEGER, version INTEGER, card_json TEXT,
          stop_pct REAL, take_profit_retrace REAL, charter_version TEXT, pack_version TEXT,
          engine_api_version INTEGER, created_at TEXT
        );
        INSERT INTO basket_cards VALUES (1,1,1,'{"members":[]}',NULL,NULL,'v','p',1,'now');
        CREATE TABLE tier_history (
          id INTEGER PRIMARY KEY, trade_date TEXT, basket_id INTEGER, tier INTEGER, mech_score REAL,
          mech_breakdown_json TEXT, rank_in_tier INTEGER, rank_mech INTEGER, llm_rank_delta INTEGER,
          llm_reason TEXT, pack_version TEXT, created_at TEXT
        );
        INSERT INTO tier_history VALUES (1,'20260814',1,2,1.0,'{}',1,1,0,NULL,'p','now');
        CREATE TABLE out_candidates (
          id INTEGER PRIMARY KEY, d0_date TEXT, basket_key TEXT, ts_code TEXT, name TEXT, role TEXT,
          engine_code TEXT, engine_version TEXT, skeleton_version TEXT, out_gate TEXT,
          out_reason TEXT, out_detail TEXT, created_at TEXT
        );
        INSERT INTO out_candidates VALUES (1,'20260814','legacy','600001.SH','Legacy',NULL,NULL,NULL,NULL,NULL,'out',NULL,'now');
        CREATE TABLE gate_evaluations (
          id INTEGER PRIMARY KEY, trade_date TEXT, candidate_key TEXT, ts_code TEXT, gate TEXT,
          gate_kind TEXT, verdict TEXT, score REAL, threshold REAL, engine_code TEXT,
          engine_version TEXT, evidence_json TEXT, created_at TEXT
        );
        INSERT INTO gate_evaluations VALUES (1,'20260814','legacy',NULL,'market','mech','pass',NULL,NULL,NULL,NULL,'{}','now');
        CREATE TABLE basket_dropped_handoff (trade_date TEXT PRIMARY KEY, dropped_json TEXT, created_at TEXT);
        INSERT INTO basket_dropped_handoff VALUES ('20260814','[]','now');
        CREATE TABLE basket_stage_handoff (trade_date TEXT PRIMARY KEY, search_stage TEXT, reason_stage TEXT, basket_count INTEGER, notes_json TEXT, created_at TEXT);
        INSERT INTO basket_stage_handoff VALUES ('20260814','ok','ok',1,'[]','now');
        """)
        conn.commit()
        before_schema = conn.execute("PRAGMA schema_version").fetchone()[0]
        before_master = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    finally:
        conn.close()
    before_size = db.stat().st_size

    assert [b.basket_key for b in load_baskets_for_date("20260814", db_path=db)] == ["legacy"]
    assert load_basket_card(1, db_path=db)["card"] == {"members": []}
    assert load_tier_history(1, db_path=db)["tier"] == 2
    assert load_out_candidates("20260814", db_path=db)[0]["basket_key"] == "legacy"
    assert load_gate_evaluations("20260814", db_path=db)[0]["candidate_key"] == "legacy"
    assert load_dropped_handoff(date(2026, 8, 14), db_path=db) == []
    assert load_stage_verdict(date(2026, 8, 14), db_path=db) is not None

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA schema_version").fetchone()[0] == before_schema
        assert conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall() == before_master
    finally:
        conn.close()
    assert db.stat().st_size == before_size

    init_schema(db)
    conn = sqlite3.connect(db)
    try:
        assert "selection_run_id" in {
            row[1] for row in conn.execute("PRAGMA table_info(baskets)")
        }
    finally:
        conn.close()
