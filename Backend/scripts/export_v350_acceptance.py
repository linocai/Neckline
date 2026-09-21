#!/usr/bin/env python3
"""Generate B78 native-QA inputs through the real, offline CLI/worker/API path."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--scenario", required=True, choices=("complete", "partial", "materials", "empty", "zero"))
    args = parser.parse_args()
    if not args.release_root.is_absolute():
        raise SystemExit("release-root must be an explicit absolute path")
    release = args.release_root.resolve()
    temporary = release / "temporary" / "integration"
    temporary.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["DB_PATH"] = str(temporary / "unused-default.sqlite")
    os.environ["TMPDIR"] = str(temporary)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _pytest.monkeypatch import MonkeyPatch
    from tests.test_v350_cli_api import generate_acceptance

    with MonkeyPatch.context() as monkeypatch:
        result = generate_acceptance(temporary / args.scenario, monkeypatch, scenario=args.scenario)
    output = release / "evidence" / "api" / args.scenario
    write_json(output / "report.json", result.report)
    if result.materials is not None:
        write_json(output / "materials.json", result.materials)
    write_json(output / "readiness.json", result.readiness)
    write_json(output / "configuration.json", result.configuration)
    write_json(output / "provenance.json", result.provenance)
    token_file = temporary / "loopback.token"
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(32))
        token_file.chmod(0o600)
    print(json.dumps({"scenario": args.scenario, "database": str(result.database),
                      "evidence": str(output), "tokenFile": str(token_file)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
