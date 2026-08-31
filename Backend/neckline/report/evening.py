"""K9-v3 evening orchestration.

This module only creates a selection package after a complete approved parameter
pack has been supplied.  The safe no-parameter path creates a report and no
candidate, playbook, score, or notification side effect.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SEG_FACTS = "facts"
SEG_DIRECTION = "direction"
SEG_K9 = "k9"
SEG_REPORT = "report"
CHAIN_SEGMENTS: Tuple[str, ...] = (SEG_FACTS, SEG_DIRECTION, SEG_K9, SEG_REPORT)
STATUS_OK, STATUS_FAILED, STATUS_SKIPPED, STATUS_EMPTY = "ok", "failed", "skipped", "empty"

@dataclass
class EveningChainResult:
    trade_date: date
    report_date: date
    status: Dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    bundle: Any = None

    def ok(self, segment: str) -> bool:
        return self.status.get(segment) in {STATUS_OK, STATUS_EMPTY}

def run_evening_chain(trade_date: date, *, report_date: Optional[date] = None,
                      segments: Sequence[str] = CHAIN_SEGMENTS,
                      k9_params_path: Optional[Path] = None,
                      correction_revision: Optional[int] = None,
                      db_path: Optional[Path] = None,
                      parquet_dir: Optional[Path] = None, save: bool = True) -> EveningChainResult:
    from neckline.report import pipeline, store
    from neckline.facts import readiness
    report_date = report_date or trade_date
    unknown = set(segments) - set(CHAIN_SEGMENTS)
    if unknown:
        raise ValueError(f"未知晚间段:{sorted(unknown)}")
    result = EveningChainResult(trade_date, report_date)
    for segment in CHAIN_SEGMENTS:
        result.status[segment] = STATUS_SKIPPED
    if SEG_FACTS in segments:
        ready = readiness.preflight(trade_date, parquet_dir=parquet_dir, db_path=db_path,
                                    pack_version="fp-4")
        result.status[SEG_FACTS] = STATUS_OK if ready.ready else STATUS_EMPTY
        result.stats[SEG_FACTS] = {"gaps": list(ready.gaps), "packId": ready.pack_id}
    if SEG_DIRECTION in segments:
        # Direction is optional factual context, never a strategy input.
        result.status[SEG_DIRECTION] = STATUS_SKIPPED
    if SEG_K9 in segments:
        result.status[SEG_K9], result.stats[SEG_K9] = _run_k9_lifecycle(
            trade_date, report_date=report_date, k9_params_path=k9_params_path,
            correction_revision=correction_revision, db_path=db_path, parquet_dir=parquet_dir)
    if SEG_REPORT in segments:
        if correction_revision is not None and result.status.get(SEG_K9) != STATUS_OK:
            result.status[SEG_REPORT] = STATUS_FAILED
            result.stats[SEG_REPORT] = {
                "state": "not_saved",
                "reason": "correction_d0_not_created",
            }
            return result
        gaps = list(result.stats.get(SEG_K9, {}).get("gaps", []))
        bundle = pipeline.build_report(trade_date, report_date=report_date,
                                       params_path=k9_params_path, db_path=db_path,
                                       parquet_dir=parquet_dir, upstream_gaps=gaps)
        result.bundle = bundle
        result.status[SEG_REPORT] = STATUS_OK
        result.stats[SEG_REPORT] = {"state": bundle.state.value, "listingSize": bundle.listing_size}
        if save:
            store.save_k9_report(trade_date=bundle.trade_date, report_date=bundle.report_date,
                                 state=bundle.state.value, headline=bundle.headline,
                                 gaps=list(bundle.gaps), markdown=bundle.markdown,
                                 structured=bundle.structured, strategy=bundle.strategy,
                                 strategy_version=bundle.strategy_version,
                                 params_package_version=bundle.params_package_version,
                                 pack_id=bundle.pack_id, pack_version=bundle.pack_version,
                                 listing_size=bundle.listing_size, db_path=db_path)
    return result

def _create_d0(trade_date: date, *, report_date: date, k9_params_path: Optional[Path], db_path: Optional[Path],
               parquet_dir: Optional[Path], correction_revision: Optional[int] = None) -> tuple[str, dict[str, Any]]:
    from neckline.calendar import next_trading_day
    from neckline.config import settings
    from neckline.facts import readiness, store as fact_store
    from neckline.k9 import v3_params, v3_run
    from neckline.scorecard import packages
    def record(state: str, reason: str, batch_id: Optional[str] = None) -> tuple[str, dict[str, Any]]:
        # A failed correction is lifecycle evidence, but it must not replace a
        # previously trusted successful D0 marker with not_run/failed.
        if correction_revision is None or state in {"has_list", "empty"}:
            packages.record_selection_run(
                selection_date=report_date, signal_trade_date=trade_date,
                state=state, batch_id=batch_id, reason=reason,
                correction_revision=correction_revision, db_path=db_path)
        outcome = STATUS_OK if state in {"has_list", "empty"} else (STATUS_FAILED if state == "failed" else STATUS_EMPTY)
        return outcome, {
            "reason": reason, "batchId": batch_id, "gaps": [] if state in {"has_list", "empty"} else [reason],
        }
    if k9_params_path is None:
        return record("not_run", "参数未配置")
    try:
        params = v3_params.load(k9_params_path)
    except v3_params.ParamsUnavailable as exc:
        return record("not_run", "参数未配置：" + "；".join(exc.gaps()))
    ready = readiness.preflight(trade_date, parquet_dir=parquet_dir, db_path=db_path,
                                pack_version="fp-4")
    if not ready.ready:
        return record("not_run", "事实未就绪：" + "；".join(ready.gaps))
    pack = fact_store.load_pack(trade_date, pack_version="fp-4", parquet_dir=parquet_dir, db_path=db_path)
    try:
        hits = v3_run.compute(trade_date, selection_date=report_date, params=params,
                              parquet_dir=parquet_dir, db_path=db_path)
    except Exception as exc:
        return record("failed", f"策略计算失败：{exc}")
    try:
        calendar_db = db_path if db_path is not None else settings.db_path
        d1 = next_trading_day(trade_date, db_path=calendar_db)
        d2 = next_trading_day(d1, db_path=calendar_db)
    except Exception as exc:
        return record("not_run", f"交易日历未就绪:{exc}")
    # selection_date is the public report date.  On a Sunday report this is not
    # the Friday fact date; keeping both values is part of the immutable ID.
    try:
        hits = v3_run.bind_d1_price_limits(hits, d1_trade_date=d1)
    except v3_run.PackageCreationError as exc:
        return record("not_run", f"D1 价格边界未就绪：{exc}")
    revision = correction_revision or 1
    batch_id = f"k9-v3-{report_date:%Y%m%d}-r{revision}-{pack.pack_id}"
    # K9-v3 pre-plans are not a parameter-ratio transformer.  The mechanical
    # engine has now frozen the candidate/channel shape; a routed LLM may only
    # fill typed price levels and explanations for that shape.
    from neckline.k9 import v3_playbook
    try:
        playbooks, provenance = v3_playbook.generate(hits, db_path=db_path)
    except v3_playbook.PlaybookUnavailable as exc:
        return record("not_run", f"playbook_not_generated：{exc}")
    try:
        v3_run.create_package(batch_id=batch_id, selection_date=report_date,
                              signal_trade_date=trade_date, d1_trade_date=d1, d2_trade_date=d2,
                              params=params, pack_id=pack.pack_id, hits=hits, playbooks=playbooks,
                              playbook_provenance=provenance, revision=revision,
                              parquet_dir=parquet_dir, db_path=db_path)
    except Exception as exc:
        return record("failed", f"成绩包创建失败：{exc}")
    candidate_count = len({h.ts_code for h in hits})
    return record("empty" if candidate_count == 0 else "has_list", "", batch_id)


def _run_k9_lifecycle(trade_date: date, *, report_date: date, k9_params_path: Optional[Path], db_path: Optional[Path],
                      parquet_dir: Optional[Path], correction_revision: Optional[int] = None) -> tuple[str, dict[str, Any]]:
    """Independently advance D2 → D1 → D0; an earlier failure cannot erase a due package."""
    from neckline.auction.eod import settle_d1_close_for_due, settle_d2_for_due
    from neckline.scorecard.lifecycle import run_nightly, begin_attempt, record_attempt, attempt_is_ok

    d0_result: dict[str, tuple[str, dict[str, Any]]] = {}
    run_identity = (
        f"correction:r{correction_revision}:{report_date:%Y%m%d}:{trade_date:%Y%m%d}"
        if correction_revision is not None
        else f"nightly:{report_date:%Y%m%d}:{trade_date:%Y%m%d}"
    )
    attempt_id = begin_attempt(selection_date=report_date, signal_trade_date=trade_date,
                               run_identity=run_identity, db_path=db_path)
    if attempt_is_ok(attempt_id, db_path=db_path):
        # The same durable run identity already created a trusted D0 marker and
        # package.  Do not re-enter D0 with a different parameter availability.
        return STATUS_OK, {"reason": "already_succeeded", "gaps": [], "outcomes": []}
    # Intraday evidence is a separate local fact source.  Record a durable
    # failed stage even when collection itself raises before run_nightly gets
    # control; otherwise report-only could mistake a crash for an empty run.
    from neckline.auction.readings import collect_d1_eod_readings, collect_d2_eod_readings
    try:
        d1_readings = collect_d1_eod_readings(trade_date, db_path=db_path, parquet_dir=parquet_dir)
        d1_collection_error: Exception | None = None
    except Exception as exc:  # noqa: BLE001
        d1_readings = {}; d1_collection_error = exc
    try:
        d2_readings = collect_d2_eod_readings(trade_date, db_path=db_path, parquet_dir=parquet_dir)
        d2_collection_error: Exception | None = None
    except Exception as exc:  # noqa: BLE001
        d2_readings = {}; d2_collection_error = exc
    def create_d0() -> None:
        d0_result["value"] = _create_d0(trade_date, report_date=report_date, k9_params_path=k9_params_path,
                                          db_path=db_path, parquet_dir=parquet_dir,
                                          correction_revision=correction_revision)
        _raise_if_not_ok(d0_result["value"])
    outcomes = run_nightly(
        settle_d2=lambda: (_raise_collection_error(d2_collection_error) if d2_collection_error else settle_d2_for_due(trade_date=trade_date, readings=d2_readings, db_path=db_path)),
        update_d1=lambda: (_raise_collection_error(d1_collection_error) if d1_collection_error else settle_d1_close_for_due(trade_date=trade_date, readings=d1_readings, db_path=db_path)),
        create_d0=create_d0,
    )
    record_attempt(attempt_id=attempt_id, outcomes=outcomes, db_path=db_path)
    d0 = next((x for x in outcomes if x.stage == "d0"), None)
    failures = [f"{x.stage}:{x.detail}" for x in outcomes if not x.ok]
    status, detail = d0_result.get("value", (STATUS_FAILED, {"reason": "D0 未执行"}))
    if d0 is not None and not d0.ok:
        detail = d0.detail.split(": ", 1)[-1]
        gaps = [detail] if detail else ["K9-v3 创建失败"]
        # A failed settlement remains visible even when D0 is deliberately
        # blocked by the no-parameter/no-playbook safety gate.
        return (STATUS_FAILED if any(x.stage != "d0" and not x.ok for x in outcomes) else STATUS_EMPTY), {
            "reason": "d0_not_created", "gaps": gaps,
            "outcomes": [x.__dict__ for x in outcomes],
        }
    # No-parameter/facts/pre-plan state is a durable D0 marker, not a fake
    # empty package.  D1/D2 can still advance independently before it.
    return (STATUS_FAILED if failures else status), {
        "gaps": detail.get("gaps", []), "reason": detail.get("reason"),
        "outcomes": [x.__dict__ for x in outcomes],
    }


def _raise_if_not_ok(outcome: tuple[str, dict[str, Any]]) -> None:
    status, detail = outcome
    if status == STATUS_OK:
        return
    gaps = detail.get("gaps") or [detail.get("reason", "K9-v3 创建失败")]
    raise RuntimeError("；".join(str(x) for x in gaps))


def _raise_collection_error(error: Exception) -> None:
    raise RuntimeError(f"盘中证据采集异常：{type(error).__name__}: {error}") from error

__all__ = ["SEG_FACTS", "SEG_DIRECTION", "SEG_K9", "SEG_REPORT", "CHAIN_SEGMENTS",
           "STATUS_OK", "STATUS_FAILED", "STATUS_SKIPPED", "STATUS_EMPTY",
           "EveningChainResult", "run_evening_chain"]
