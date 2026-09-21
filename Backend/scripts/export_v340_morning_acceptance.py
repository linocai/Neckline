#!/usr/bin/env python3
"""Export a morning report from the actual FastAPI router for B76 evidence."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from typing import Any


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def export_morning_report(*, database: Path, output: Path, provenance: Path | None = None) -> dict[str, Any]:
    """Read a preserved isolated DB via the production API router and write it."""
    if not database.is_file():
        raise ValueError(f"acceptance database does not exist: {database}")
    backend = Path(__file__).resolve().parents[1]
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from neckline.api.k10 import create_router

    with sqlite3.connect(database) as connection:
        config = connection.execute(
            "SELECT config_id,revision FROM k10_run_config_revisions "
            "ORDER BY created_at DESC,revision DESC LIMIT 1"
        ).fetchone()
        execution = connection.execute(
            "SELECT config_id,revision FROM k10_execution_config_revisions "
            "ORDER BY created_at DESC,revision DESC LIMIT 1"
        ).fetchone()
    if config is None or execution is None:
        raise ValueError("acceptance database lacks active configuration bindings")

    app = FastAPI()
    app.include_router(create_router(
        lambda: database,
        lambda: None,
        lambda: database.parent / "parquet",
        current_config_binding_provider=lambda: (str(config[0]), int(config[1]), None),
        current_execution_config_binding_provider=lambda: (str(execution[0]), int(execution[1]), None),
    ))
    with TestClient(app) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=morning")
    response.raise_for_status()
    payload = response.json()
    report = payload.get("report")
    if (payload.get("state") != "available" or not isinstance(report, dict)
            or report.get("windowKind") != "morning"):
        raise ValueError("actual FastAPI response has no available morning report")
    _write_json(output, payload)
    if provenance is not None:
        _write_json(provenance, {
            "synthetic": True,
            "database": str(database),
            "apiRoute": "/api/v1/k10/v2/reports/latest?window=morning",
            "apiEnvelopeState": payload["state"],
            "reportId": report["reportId"],
            "windowKind": report["windowKind"],
            "status": report["status"],
            "deliveryOutcome": (report.get("delivery") or {}).get("outcome"),
            "network": "test fixture socket denied; model/search use httpx.MockTransport",
        })
    return payload


def _resolve_generation_roots(*, release_root: Path, work_root: Path) -> tuple[Path, Path]:
    """Limit explicit regeneration to the release's owned temporary root."""
    release = release_root.expanduser()
    work = work_root.expanduser()
    if not release.is_absolute() or not work.is_absolute():
        raise ValueError("--release-root and --work-root must be absolute paths")
    release = release.resolve()
    work = work.resolve()
    temporary = release / "temporary"
    try:
        work.relative_to(temporary)
    except ValueError as exc:
        raise ValueError("--work-root must be under --release-root/temporary") from exc
    return release, work


def generate_morning_acceptance(*, release_root: Path, work_root: Path) -> int:
    """Run the actual offline morning flow and export its two FastAPI DTOs.

    The test itself owns the deterministic transports and pytest's socket/DNS
    guard.  It writes to staging first; only a successful complete run replaces
    the release evidence.  Passing the roots only to this child keeps an
    ordinary test run fully isolated under ``tmp_path``.
    """
    release, work = _resolve_generation_roots(release_root=release_root, work_root=work_root)
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = work / "tmp"
    tmp.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = work / "generated-artifacts"
    if staging.exists():
        shutil.rmtree(staging)
    backend = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.update({
        "NECKLINE_V340_MORNING_GENERATION_ROOT": str(work),
        "NECKLINE_V340_MORNING_ARTIFACT_ROOT": str(staging),
        "TMPDIR": str(tmp),
    })
    command = [
        sys.executable, "-m", "pytest", "-q",
        "tests/test_v340_morning_end_to_end.py::test_b76_actual_morning_partial_contains_discovery_and_review_gaps",
        "--basetemp", str(work / "pytest"),
        "-o", "tmp_path_retention_policy=failed",
        "-o", f"cache_dir={work / 'pytest-cache'}",
    ]
    result = subprocess.run(command, cwd=backend, env=environment, check=False)
    if result.returncode != 0:
        return result.returncode
    artifacts = (
        Path("evidence/api/b76-morning-partial-report.json"),
        Path("evidence/api/b76-morning-review-partial-report.json"),
        Path("evidence/acceptance/b76-morning-partial-provenance.json"),
        Path("evidence/acceptance/b76-morning-review-partial-provenance.json"),
    )
    sources = [staging / relative for relative in artifacts]
    missing = [str(source) for source in sources if not source.is_file()]
    if missing:
        raise RuntimeError(f"successful acceptance omitted generated artifacts: {', '.join(missing)}")
    replacements: list[tuple[Path, Path]] = []
    for source, relative in zip(sources, artifacts, strict=True):
        destination = release / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        replacement = destination.with_name(f".{destination.name}.new")
        shutil.copyfile(source, replacement)
        replacement.chmod(0o600)
        replacements.append((replacement, destination))
    for replacement, destination in replacements:
        os.replace(replacement, destination)
    shutil.rmtree(staging)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--generate", action="store_true",
                        help="explicitly run the offline CLI/worker/API morning acceptance export")
    parser.add_argument("--release-root", type=Path,
                        help="B76 release root that owns evidence/api output (required with --generate)")
    parser.add_argument("--work-root", type=Path,
                        help="owned release temporary subdirectory for generated acceptance databases")
    args = parser.parse_args()
    if args.generate:
        if args.database is not None or args.output is not None or args.provenance is not None:
            parser.error("--generate does not accept --database, --output, or --provenance")
        if args.release_root is None or args.work_root is None:
            parser.error("--generate requires --release-root and --work-root")
        try:
            return generate_morning_acceptance(release_root=args.release_root, work_root=args.work_root)
        except ValueError as exc:
            parser.error(str(exc))
    if args.release_root is not None or args.work_root is not None:
        parser.error("--release-root and --work-root require --generate")
    if args.database is None or args.output is None:
        parser.error("--database and --output are required unless --generate is used")
    export_morning_report(database=args.database.resolve(), output=args.output.resolve(),
                          provenance=None if args.provenance is None else args.provenance.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
