"""K9-v3 report projection over immutable selection packages."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence

from neckline.facts import readiness
from neckline.report.state import ReportState, headline, resolve_state
from neckline.scorecard import packages
from neckline.scorecard import lifecycle


@dataclass(frozen=True)
class ReportBundle:
    trade_date: date
    report_date: date
    state: ReportState
    headline: str
    gaps: tuple[str, ...]
    strategy: str
    strategy_version: str
    params_package_version: Optional[str]
    params_sha256: Optional[str]
    pack_id: Optional[str]
    pack_version: Optional[str]
    listing_size: Optional[int]
    batch_ids: tuple[str, ...]
    structured: dict[str, Any]
    markdown: str


def build_report(trade_date: date, *, report_date: Optional[date] = None,
                 params_path: Optional[Path] = None, db_path: Optional[Path] = None,
                 parquet_dir: Optional[Path] = None, upstream_gaps: Sequence[str] = (),
                 upstream_failures: Sequence[str] = ()) -> ReportBundle:
    report_date = report_date or trade_date
    gaps = list(upstream_failures) + list(upstream_gaps)
    attempt = lifecycle.latest_attempt(selection_date=report_date, signal_trade_date=trade_date, db_path=db_path)
    # The report is a separate systemd process.  Package rows cannot prove the
    # upstream run completed: require one durable attempt with all stages ok.
    if attempt is None:
        gaps.append("上游生命周期运行身份缺失")
    else:
        failed = [f"{stage}:{value.get('detail') or value.get('status')}" for stage, value in attempt["stages"].items()
                  if value.get("status") != "ok"]
        if attempt.get("status") != "ok" or failed:
            gaps.extend(["上游生命周期未成功", *failed])
    marker = packages.load_selection_run(report_date, db_path=db_path)
    today: list[dict[str, Any]] = []
    count: Optional[int] = None
    frozen_pack_id: Optional[str] = None
    frozen_pack_version: Optional[str] = None
    frozen_params_version: Optional[str] = None
    frozen_params_sha: Optional[str] = None
    upstream_not_ok = attempt is None or attempt.get("status") != "ok" or any(value.get("status") != "ok" for value in (attempt or {}).get("stages", {}).values())
    if marker is None:
        # A report segment can run after a failed/skipped strategy unit.  No
        # package is never evidence of a valid empty selection.
        gaps.append("策略运行身份缺失")
        if params_path is None:
            gaps.append("参数未配置")
        state = ReportState.NOT_RUN
    elif marker["state"] in {"has_list", "empty"}:
        package = packages.load_package(str(marker.get("batch_id") or ""), db_path=db_path)
        if package is None or package["selection_date"] != report_date.strftime("%Y%m%d"):
            gaps.append("策略成绩包缺失")
            state = ReportState.NOT_RUN
        elif package["signal_trade_date"] != trade_date.strftime("%Y%m%d"):
            gaps.append("报告行情日与不可变成绩包不一致")
            state = ReportState.NOT_RUN
        else:
            count = len(package["candidates"])
            if marker["state"] == "empty" and count != 0:
                gaps.append("空清单运行身份与成绩包不一致")
                state = ReportState.NOT_RUN
            elif marker["state"] == "has_list" and count == 0:
                gaps.append("非空运行身份与成绩包不一致")
                state = ReportState.NOT_RUN
            else:
                today = [{"batch_id": package["batch_id"]}]
                frozen_pack_id = str(package["pack_id"])
                frozen_pack_version = str(package["pack_version"])
                frozen_params_version = str(package["params_package_version"])
                frozen_params_sha = str(package["params_sha256"])
                state = ReportState.EMPTY if count == 0 else ReportState.HAS_LIST
    else:
        gaps.append(str(marker.get("reason") or "策略未完成"))
        state = ReportState.NOT_RUN
    if upstream_not_ok:
        state = ReportState.NOT_RUN
        if params_path is None:
            gaps.append("参数未配置")
    if state is ReportState.NOT_RUN:
        count = None
        today = []
    unique_gaps = tuple(dict.fromkeys(gaps))
    text = headline(state, listing_count=count, gaps=unique_gaps)
    structured = {
        "strategyVersion": "K9-v3", "selectionDate": report_date.strftime("%Y%m%d"),
        "signalTradeDate": trade_date.strftime("%Y%m%d"), "state": state.value,
        "headline": text, "gaps": list(unique_gaps),
        "batchIds": [p["batch_id"] for p in today], "listingSize": count,
        "paramsPackageVersion": frozen_params_version, "paramsSha256": frozen_params_sha,
        "packId": frozen_pack_id, "packVersion": frozen_pack_version,
        "labelContractVersion": package["label_contract_version"] if today else None,
    }
    markdown = "# Neckline · K9-v3\n\n" + text
    return ReportBundle(trade_date, report_date, state, text, unique_gaps, "K9", "K9-v3",
                        frozen_params_version, frozen_params_sha,
                        frozen_pack_id, frozen_pack_version, count,
                        tuple(p["batch_id"] for p in today), structured, markdown)


__all__ = ["ReportBundle", "build_report"]
