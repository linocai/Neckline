"""Build and switch a Neckline backend release.

Local (from Backend/):
    .venv/bin/python scripts/release.py build --release v3.7.0-b109 --commit HEAD --out <dir>

Server (as root, from the uploaded release directory; run with --check-only first):
    /opt/neckline/.venv/bin/python /opt/neckline/releases/<release>/release.py switch \
        --release <release> --previous <current release> [--check-only]

A release directory holds ``release.py`` plus ``backend/`` with the runtime
archive, the wheel and ``release-manifest.json``. ``switch`` snapshots the
database, replaces the runtime files (adding and removing files as the
manifest says), installs the wheel and returns every service and timer to the
state it had before; any failure restores the previous runtime. The production
database is never restored or overwritten.
"""
from __future__ import annotations

import argparse
import grp
import gzip
import hashlib
import io
import json
import os
import pwd
import shutil
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HOST = "ser657204219523"
ROOT = Path("/opt/neckline")
DB = ROOT / "data/neckline.db"
APPLICATION_ID = 1313552690
SERVICES = ["neckline.service", "neckline-k10-worker.service"]
TIMERS = ["neckline-k10-collection.timer", "neckline-k10-morning.timer", "neckline-k10-evening.timer",
          "neckline-market-update.timer", "neckline-market-retry.timer"]
RUNTIME_PREFIXES = ("neckline/", "scripts/", "deploy/")
RUNTIME_FILES = (".env.example", "pyproject.toml", "requirements.txt")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_runtime_file(name: str) -> bool:
    if "__pycache__" in name.split("/") or name.endswith(".pyc"):
        return False
    return name.startswith(RUNTIME_PREFIXES) or name in RUNTIME_FILES


# ---------------------------------------------------------------- build (local)

def build(*, release: str, commit: str, out: Path) -> dict:
    backend = Path(__file__).resolve().parents[1]
    repo = backend.parent
    version, build_number = release.removeprefix("v").split("-b")
    full_commit = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{commit}^{{commit}}"],
                                 check=True, capture_output=True, text=True).stdout.strip()
    app_py = subprocess.run(["git", "-C", str(repo), "show", f"{full_commit}:Backend/neckline/api/app.py"],
                            check=True, capture_output=True, text=True).stdout
    if f'RELEASE_SET = "{release}"' not in app_py:
        raise SystemExit(f"{full_commit[:7]} 的 RELEASE_SET 不是 {release}")
    target = out / release / "backend"
    target.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as temp:
        archive = subprocess.run(["git", "-C", str(repo), "archive", full_commit, "Backend"],
                                 check=True, capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(temp, filter="data")
        tree = Path(temp) / "Backend"
        files = {path.relative_to(tree).as_posix(): path for path in sorted(tree.rglob("*"))
                 if path.is_file() and is_runtime_file(path.relative_to(tree).as_posix())}
        tar_path = target / f"Neckline-Backend-{release}.tar.gz"
        with tarfile.open(tar_path, "w:gz") as bundle:
            for name, path in files.items():
                data = path.read_bytes()
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), 0o644, 0
                bundle.addfile(info, io.BytesIO(data))
        subprocess.run([str(backend / ".venv/bin/python"), "-m", "pip", "wheel", str(tree), "--no-deps",
                        "--quiet", "-w", str(target)], check=True)
        wheels = list(target.glob("neckline_runtime-*.whl"))
        if len(wheels) != 1:
            raise SystemExit("wheel 产物不唯一")
        manifest = {
            "releaseSet": release, "version": version, "build": int(build_number), "commit": full_commit,
            "schema": 10, "publicReportSchema": 10,
            "backendFiles": {name: file_sha256(path) for name, path in files.items()},
            "archiveSha256": file_sha256(tar_path), "wheel": wheels[0].name,
            "wheelSha256": file_sha256(wheels[0]),
        }
        # Ship the switch script from the same commit as the runtime it installs.
        shutil.copy2(tree / "scripts/release.py", out / release / "release.py")
    (target / "release-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {"release": release, "commit": full_commit, "files": len(manifest["backendFiles"]),
            "directory": str(out / release)}


# ---------------------------------------------------------------- switch (server)

def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=check, capture_output=True, text=True)


def unit_state(unit: str) -> dict:
    return {"active": run("systemctl", "is-active", unit, check=False).stdout.strip(),
            "enabled": run("systemctl", "is-enabled", unit, check=False).stdout.strip()}


def started_attempts() -> int:
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        if con.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise RuntimeError("生产库 application_id 不符")
        return con.execute("SELECT count(*) FROM k10_external_attempts WHERE state='started'").fetchone()[0]


def load_release(directory: Path, release: str) -> tuple[dict, dict[str, bytes], Path]:
    manifest = json.loads((directory / "backend/release-manifest.json").read_text())
    tar_path = directory / f"backend/Neckline-Backend-{release}.tar.gz"
    # Releases built before this script carry no wheel name; they used 3.7.0.
    wheel = directory / "backend" / manifest.get("wheel", "neckline_runtime-3.7.0-py3-none-any.whl")
    if manifest["releaseSet"] != release:
        raise RuntimeError(f"{directory} 不是 {release}")
    if file_sha256(tar_path) != manifest["archiveSha256"] or file_sha256(wheel) != manifest["wheelSha256"]:
        raise RuntimeError(f"{release} 制品哈希不符")
    with tarfile.open(tar_path, "r:gz") as archive:
        entries = {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
    if any(Path(name).is_absolute() or ".." in Path(name).parts for name in entries):
        raise RuntimeError("制品含不安全路径")
    if {name: sha256(data) for name, data in entries.items()} != manifest["backendFiles"]:
        raise RuntimeError(f"{release} 制品内容与清单不符")
    return manifest, entries, wheel


def write_runtime(root: Path, entries: dict[str, bytes], remove: set[str]) -> None:
    for name, content in sorted(entries.items()):
        target = root / name
        if target.is_symlink():
            raise RuntimeError(f"拒绝覆盖符号链接 {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".release-", delete=False) as temp:
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
            staged = Path(temp.name)
        try:
            if os.geteuid() == 0:
                os.chown(staged, 0, 0)
            os.chmod(staged, 0o644)
            os.replace(staged, target)
        finally:
            staged.unlink(missing_ok=True)
    for name in sorted(remove):
        (root / name).unlink(missing_ok=True)


def verify_runtime(root: Path, manifest: dict) -> None:
    drift = [name for name, digest in manifest["backendFiles"].items()
             if not (root / name).is_file() or file_sha256(root / name) != digest]
    if drift:
        raise RuntimeError(f"运行文件与清单不符: {drift[:3]}")


def install_wheel(wheel: Path) -> None:
    run(str(ROOT / ".venv/bin/python"), "-m", "pip", "install", "--no-index", "--no-deps",
        "--force-reinstall", "--no-compile", "--quiet", str(wheel))
    site = list((ROOT / ".venv/lib").glob("python*/site-packages"))
    if len(site) != 1:
        raise RuntimeError("site-packages 路径不唯一")
    with zipfile.ZipFile(wheel) as package:
        names = [n for n in package.namelist() if n.startswith("neckline/") and not n.endswith("/")]
        if any(file_sha256(site[0] / n) != sha256(package.read(n)) for n in names):
            raise RuntimeError("已安装的包与 wheel 不符")


def snapshot(directory: Path) -> dict:
    recovery = directory / "recovery"
    recovery.mkdir(mode=0o700, exist_ok=True)
    os.chmod(recovery, 0o700)
    tables = ("k10_tasks", "k10_external_attempts", "k10_model_response_receipts", "k10_source_document_versions")
    copy, check = recovery / "pre-switch.db", recovery / "restore-check.db"
    archive = recovery / "pre-switch.db.gz"

    def counts(path: Path) -> dict:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
            con.execute("PRAGMA query_only=ON")
            if con.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise RuntimeError(f"{path.name} application_id 不符")
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"{path.name} 完整性校验失败")
            return {table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}

    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    os.chmod(copy, 0o600)
    expected = counts(copy)
    with copy.open("rb") as raw, gzip.open(archive, "wb", compresslevel=6) as packed:
        shutil.copyfileobj(raw, packed)
    os.chmod(archive, 0o600)
    with gzip.open(archive, "rb") as packed, check.open("wb") as raw:
        shutil.copyfileobj(packed, raw)
    if file_sha256(check) != file_sha256(copy) or counts(check) != expected:
        raise RuntimeError("恢复点还原校验失败")
    report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "counts": expected,
              "databaseBytes": copy.stat().st_size, "archive": str(archive),
              "archiveBytes": archive.stat().st_size, "archiveSha256": file_sha256(archive),
              "roundTripVerified": True, "productionDatabaseRestored": False}
    for path in (copy, check):
        for leftover in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            leftover.unlink(missing_ok=True)
    (directory / "snapshot-verification.json").write_text(json.dumps(report, indent=2))
    return report


def health(release: str) -> None:
    for _ in range(40):
        result = run("curl", "-s", "-m", "3", "http://127.0.0.1:8002/api/v1/health", check=False)
        try:
            if json.loads(result.stdout).get("releaseSet") == release:
                return
        except ValueError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"health 未报告 {release}")


def switch(*, release: str, previous: str, check_only: bool = False) -> dict:
    if socket.gethostname() != HOST:
        raise RuntimeError("目标主机不符")
    if os.geteuid() != 0:
        raise RuntimeError("需要 root 运行")
    current = json.loads((ROOT / "release-manifest.json").read_text())
    if current["releaseSet"] != previous:
        raise RuntimeError(f"线上是 {current['releaseSet']}，不是 {previous}")
    new_dir, old_dir = ROOT / "releases" / release, ROOT / "releases" / previous
    new, new_entries, new_wheel = load_release(new_dir, release)
    old, old_entries, old_wheel = load_release(old_dir, previous)
    verify_runtime(ROOT, old)
    root_stat, db_stat = ROOT.stat(), DB.stat()
    if (root_stat.st_uid, root_stat.st_gid, root_stat.st_mode & 0o777) != (0, 0, 0o755):
        raise RuntimeError("/opt/neckline 属主或权限异常")
    owner = (pwd.getpwnam("neckline").pw_uid, grp.getgrnam("neckline").gr_gid, 0o600)
    if (db_stat.st_uid, db_stat.st_gid, db_stat.st_mode & 0o777) != owner:
        raise RuntimeError("生产库属主或权限异常")
    if started_attempts():
        raise RuntimeError("有进行中的外呼，稍后再切换")

    before = {unit: unit_state(unit) for unit in SERVICES + TIMERS}
    if check_only:
        return {"release": release, "previous": previous, "preflight": "ok",
                "addedFiles": sorted(set(new["backendFiles"]) - set(old["backendFiles"])),
                "removedFiles": sorted(set(old["backendFiles"]) - set(new["backendFiles"])),
                "changedFiles": sorted(name for name in set(new["backendFiles"]) & set(old["backendFiles"])
                                       if new["backendFiles"][name] != old["backendFiles"][name]),
                "units": before}
    record: dict = {"release": release, "previous": previous, "startedAt": datetime.now(timezone.utc).isoformat(),
                    "unitsBefore": before}
    masked = switched = False
    try:
        run("systemctl", "mask", "--runtime", *SERVICES)
        masked = True
        run("systemctl", "stop", *TIMERS)
        run("systemctl", "stop", *SERVICES)
        for _ in range(40):
            if all(unit_state(unit)["active"] == "inactive" for unit in SERVICES):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("服务未停止")
        if started_attempts():
            raise RuntimeError("停服后仍有进行中的外呼")
        record["snapshot"] = snapshot(new_dir)
        try:
            write_runtime(ROOT, new_entries, set(old["backendFiles"]) - set(new["backendFiles"]))
            install_wheel(new_wheel)
            verify_runtime(ROOT, new)
            shutil.copyfile(new_dir / "backend/release-manifest.json", ROOT / "release-manifest.json")
            os.chmod(ROOT / "release-manifest.json", 0o644)
            switched = True
        except Exception:
            write_runtime(ROOT, old_entries, set(new["backendFiles"]) - set(old["backendFiles"]))
            install_wheel(old_wheel)
            verify_runtime(ROOT, old)
            shutil.copyfile(old_dir / "backend/release-manifest.json", ROOT / "release-manifest.json")
            raise
    finally:
        if masked:
            run("systemctl", "unmask", "--runtime", *SERVICES)
        for unit in SERVICES + TIMERS:
            if before[unit]["active"] == "active":
                run("systemctl", "start", unit, check=False)
        record["unitsAfter"] = {unit: unit_state(unit) for unit in SERVICES + TIMERS}
        record["switched"] = switched
        record["finishedAt"] = datetime.now(timezone.utc).isoformat()
        (new_dir / "switch-state.json").write_text(json.dumps(record, indent=2))
    if record["unitsAfter"] != before:
        raise RuntimeError("服务或 timer 状态未恢复到切换前")
    health(release)
    return {"release": release, "commit": new["commit"], "switched": switched,
            "runtimeFiles": len(new["backendFiles"]),
            "removedFiles": sorted(set(old["backendFiles"]) - set(new["backendFiles"])),
            "recoveryPoint": record["snapshot"]["archive"], "units": record["unitsAfter"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    build_args = commands.add_parser("build")
    build_args.add_argument("--release", required=True)
    build_args.add_argument("--commit", default="HEAD")
    build_args.add_argument("--out", type=Path, required=True)
    switch_args = commands.add_parser("switch")
    switch_args.add_argument("--release", required=True)
    switch_args.add_argument("--previous", required=True)
    switch_args.add_argument("--check-only", action="store_true", help="只跑预检，不停服、不改文件")
    args = parser.parse_args()
    if args.command == "build":
        result = build(release=args.release, commit=args.commit, out=args.out)
    else:
        result = switch(release=args.release, previous=args.previous, check_only=args.check_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
