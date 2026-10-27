"""Build and switch a Neckline backend release.

Local (from Backend/):
    .venv/bin/python scripts/release.py build --release v4.0.0-b113 --commit HEAD --out <dir>

Server (as root, from the uploaded release directory; run with --check-only first):
    /opt/neckline/.venv/bin/python /opt/neckline/releases/<release>/release.py switch \
        --release <release> --previous <current release> [--check-only]

A release directory holds ``release.py`` plus ``backend/`` with the runtime
archive, the wheel and ``release-manifest.json``. ``switch`` snapshots the
database, replaces the runtime files (adding and removing files as the
manifest says), installs the wheel and returns every service and timer to the
state it had before. A failure while the files are being replaced restores the
previous runtime immediately. If the files are already in place but the units
do not return to their previous state, or the readiness check fails, switch
rolls the runtime back and raises. Units are started again only after the old
files, wheel, manifest and runtime check have all succeeded. If that rollback
fails, ``switch-state.json`` records the failed step and, from a live ``systemctl is-enabled`` query,
whether the services are actually masked. The same readiness check against the previous release
(``releaseSet``, manifest version, database and the flags that were true before) runs after every rollback.
The mask command may take effect before it reports failure, so the units are treated as possibly masked from the
moment it is issued: any error from then on is recorded in ``switch-state.json`` and the units are always unmasked
(idempotent) as part of the recovery before it propagates.
The production database is never restored or overwritten.

Database schema version. ``build`` records the commit's ``SCHEMA_VERSION`` in the manifest as ``schemaVersion``
(a manifest without it means 1). Before anything is touched, switch reads the production ``user_version``
(``schemaBefore``, read-only, no DDL) and plans ``none`` / ``upgrade`` / ``downgrade``; ``--check-only`` reports the plan.
An upgrade runs ``init-db`` as the neckline user after the new files, wheel and manifest are in place and verified,
before the units return; a downgrade (only the allow-listed add-tables-only steps, 2→1, 3→2, 3→1, 4→3, 4→2 and 4→1) just rewrites
``user_version`` after the services are stopped and before the files are replaced; tables are kept. Once the old files,
wheel and manifest are back, every rollback puts ``user_version`` back to ``schemaBefore`` (services still stopped) before
the units return and before the old release's readiness check; if that fails the units stay masked. If the old files cannot
be restored at all, the database is left at the version that matches whatever files are in place and ``schemaNow`` in
``switch-state.json`` says what it is. Only ``user_version`` or ``init_schema`` ever touches the database: no table is
dropped, no business data read or written.

Opening the production database as root can leave root-owned ``-wal`` / ``-shm`` files next to it (a read-only
connection to a WAL database creates them when none exist), which would lock the neckline service out. Every
root-side connection is therefore closed explicitly, and right after it the sidecar files (when present) must be
neckline:neckline 0600: root-owned ones are handed back and recorded as ``sidecarFixes`` in ``switch-state.json`` (and in
the output); anything that cannot be put right fails the step (abort before the mask, rollback or manual after it).
``assert_ownership`` checks the sidecar files too. The service owns the directory, so a sidecar may be swapped for a link
at any moment: each one is opened once with ``O_NOFOLLOW | O_NONBLOCK`` and everything (type, link count, owner, the
fix itself via ``fchown`` / ``fchmod``) is done on that descriptor, never by path; links, non-regular files and hard-linked
files are refused. Before any unit is unmasked or started again (``_restore_units``: the normal path and every rollback
path) the production database and its sidecars are checked, and fixed if needed, once more; if that fails the units stay masked.

Health flags: ``kxPool`` / ``kxPicks`` follow the same rule as ``READY_FLAGS`` (true before, still true after) for a release
whose manifest ``schemaVersion`` is at least 2 (the KX releases); a release that predates them (B120 shape, also the
target of a roll-back to it) neither reports nor needs them, and a baseline without them is valid.
"""
from __future__ import annotations

import argparse
import errno
import grp
import gzip
import hashlib
import io
import json
import os
import pwd
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

HOST = "ser657204219523"
ROOT = Path("/opt/neckline")
V4_DB = ROOT / "data/v4.db"
V4_APPLICATION_ID = 1313552704
SERVICES = ["neckline.service"]
TIMERS = ["neckline-v4-morning.timer", "neckline-v4-collect.timer", "neckline-v4-finalize.timer"]
# V4 的出报与采集是一次性任务：切换前必须都不在运行，切换期间 timer 停住，结束后恢复原状态。
V4_JOBS = ["neckline-v4-morning.service", "neckline-v4-collect.service", "neckline-v4-finalize.service"]
RUNTIME_PREFIXES = ("neckline/", "scripts/", "deploy/")
RUNTIME_FILES = (".env.example", "pyproject.toml", "requirements.txt")
SERVICE_USER = "neckline"
SIDECAR_SUFFIXES = ("-wal", "-shm")
KX_FLAGS = ("kxPool", "kxPicks")  # B121 起 health 才带这两项
KX_SCHEMA = 2  # 清单 schemaVersion 达到它的发布才报告、也才要求 KX 项
# 库结构只允许这些「只加了表或列」的降级：旧代码认得它的版本号，按列名读写，多出来的表和列保留不删。
# v3 只给 kx_pool 加了三列，v4 只加 filing_reads 与 evaluations。旧代码的 SELECT / INSERT 都带列名，
# 所以 3→2、3→1、4→3、4→2、4→1 也只改版本号。
ADDITIVE_DOWNGRADES = frozenset({(2, 1), (3, 2), (3, 1), (4, 3), (4, 2), (4, 1)})


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

def schema_version_at(repo: Path, commit: str) -> int:
    """被构建的 commit 里 neckline/morning/db.py 的 SCHEMA_VERSION；只读 git 对象，不 import 任何代码。"""
    try:
        text = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:Backend/neckline/morning/db.py"],
                              check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"{commit[:7]} 里读不到 neckline/morning/db.py: {(exc.stderr or '').strip()[-200:]}") from exc
    found = re.findall(r"^SCHEMA_VERSION\s*=\s*(\d+)\s*(?:#.*)?$", text, flags=re.MULTILINE)
    if len(found) != 1 or int(found[0]) < 1:
        raise SystemExit(f"{commit[:7]} 的 db.py 里找不到唯一的 SCHEMA_VERSION")
    return int(found[0])


def manifest_schema_version(manifest: dict) -> int:
    """清单里的库结构版本；没有该字段的旧清单（B121 之前）都是 v1。"""
    value = manifest.get("schemaVersion", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError(f"{manifest.get('releaseSet')} 清单的 schemaVersion 无效: {value!r}")
    return value


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
    schema_version = schema_version_at(repo, full_commit)
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
            "schema": 10, "publicReportSchema": 10, "schemaVersion": schema_version,
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

def run(*args: str, check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=check, capture_output=True, text=True, cwd=cwd)


def unit_state(unit: str) -> dict:
    return {"active": run("systemctl", "is-active", unit, check=False).stdout.strip(),
            "enabled": run("systemctl", "is-enabled", unit, check=False).stdout.strip()}


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
    if V4_DB.exists():
        report = snapshot_db(directory, V4_DB, V4_APPLICATION_ID, "v4-pre-switch",
                             ("documents", "document_versions", "collection_runs", "model_receipts", "reports"))
    else:
        report = {"present": False, "productionDatabaseRestored": False,
                  "checkedAt": datetime.now(timezone.utc).isoformat()}
    (directory / "snapshot-verification.json").write_text(json.dumps(report, indent=2))
    return report


def snapshot_db(directory: Path, db: Path, application_id: int, name: str, tables: tuple[str, ...]) -> dict:
    recovery = directory / "recovery"
    recovery.mkdir(mode=0o700, exist_ok=True)
    os.chmod(recovery, 0o700)
    copy, check = recovery / f"{name}.db", recovery / f"{name}-restore-check.db"
    archive = recovery / f"{name}.db.gz"

    def counts(path: Path) -> dict:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            con.execute("PRAGMA query_only=ON")
            if con.execute("PRAGMA application_id").fetchone()[0] != application_id:
                raise RuntimeError(f"{path.name} application_id 不符")
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"{path.name} 完整性校验失败")
            return {table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
        finally:
            con.close()

    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # root 打开生产库：显式 close，之后核对伴随文件
    try:
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
        fix_sidecars(db)
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
    return report


def assert_ownership() -> None:
    root_stat = ROOT.stat()
    if (root_stat.st_uid, root_stat.st_gid, root_stat.st_mode & 0o777) != (0, 0, 0o755):
        raise RuntimeError("/opt/neckline 属主或权限异常")
    if not V4_DB.exists():
        return
    owner = (*_service_owner(), 0o600)
    v4_stat = V4_DB.stat()
    if (v4_stat.st_uid, v4_stat.st_gid, v4_stat.st_mode & 0o777) != owner:
        raise RuntimeError("V4 晨报库属主或权限异常")
    for sidecar in _sidecars(V4_DB):
        found = _sidecar_owner(sidecar)
        if found is not None and found != owner:
            raise RuntimeError(f"V4 晨报库伴随文件 {sidecar.name} 属主或权限异常")


# ---------------------------------------------------------------- production database access (root side)

_SIDECAR_FIXES: list[dict] = []  # 本次 switch 里被改回 neckline 的伴随文件，写进 switch-state.json


def _service_owner() -> tuple[int, int]:
    return pwd.getpwnam(SERVICE_USER).pw_uid, grp.getgrnam(SERVICE_USER).gr_gid


def _sidecars(db: Path) -> list[Path]:
    return [Path(f"{db}{suffix}") for suffix in SIDECAR_SUFFIXES]


def _open_sidecar(path: Path) -> int | None:
    """不跟随符号链接地打开伴随文件（只读、非阻塞）；文件不存在返回 None，符号链接直接报错。"""
    try:
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise RuntimeError(f"{path.name} 是符号链接，拒绝处理") from exc
        raise RuntimeError(f"{path.name} 无法安全打开：{exc}") from exc


def _regular_file(path: Path, fd: int) -> os.stat_result | None:
    """对已打开的描述符验明身份：普通文件、链接数为 1。链接数为 0 说明刚被删（服务关库时会删 -wal），当作不存在。"""
    found = os.fstat(fd)
    if not stat.S_ISREG(found.st_mode):
        raise RuntimeError(f"{path.name} 不是普通文件")
    if found.st_nlink == 0:
        return None
    if found.st_nlink != 1:
        raise RuntimeError(f"{path.name} 的链接数是 {found.st_nlink}，不是 1")
    return found


def _sidecar_owner(path: Path) -> tuple[int, int, int] | None:
    """(uid, gid, 权限位)；不存在返回 None。只通过不跟随链接的描述符检查，绝不按路径。"""
    fd = _open_sidecar(path)
    if fd is None:
        return None
    try:
        found = _regular_file(path, fd)
        return None if found is None else (found.st_uid, found.st_gid, found.st_mode & 0o777)
    finally:
        os.close(fd)


def fix_sidecars(db: Path) -> None:
    """root 侧用过库之后：-wal/-shm（存在时）必须是 neckline:neckline 0600。
    root 属主的改回来并记一笔；改不回来或属主是别人就报错（调用方按所处阶段中止或回滚）。
    每个文件只打开一次（O_NOFOLLOW），核验与修改都作用在同一个描述符上，路径中途被换成链接也碰不到别的文件。"""
    uid, gid = _service_owner()
    for path in _sidecars(db):
        fd = _open_sidecar(path)
        if fd is None:
            continue
        try:
            found = _regular_file(path, fd)
            if found is None or (found.st_uid, found.st_gid, found.st_mode & 0o777) == (uid, gid, 0o600):
                continue
            if found.st_uid not in (0, uid):
                raise RuntimeError(f"{path.name} 属主异常（uid={found.st_uid}），既不是 root 也不是 {SERVICE_USER}")
            try:
                os.fchown(fd, uid, gid)
                os.fchmod(fd, 0o600)
            except OSError as exc:
                raise RuntimeError(f"{path.name} 无法改回 {SERVICE_USER}:{SERVICE_USER} 0600：{exc}") from exc
            now = os.fstat(fd)
            if (now.st_uid, now.st_gid, now.st_mode & 0o777) != (uid, gid, 0o600):
                raise RuntimeError(f"{path.name} 改回后仍不是 {SERVICE_USER}:{SERVICE_USER} 0600")
            _SIDECAR_FIXES.append({"file": path.name, "wasUid": found.st_uid, "wasGid": found.st_gid,
                                   "wasMode": oct(found.st_mode & 0o777)})
        finally:
            os.close(fd)


def _check_database_for_service() -> None:
    """单元要重新 unmask / 启动之前：生产库和伴随文件的属主权限必须是服务能用的样子（需要就再改回一次）。"""
    if not V4_DB.exists():
        return
    try:
        fix_sidecars(V4_DB)
        assert_ownership()
    except Exception as exc:
        _raise_step("核验生产库属主权限", exc)


@contextmanager
def _production_db(mode: str, **options) -> Iterator[sqlite3.Connection]:
    """root 侧打开生产库：用完显式 close（不靠 with 语句或 GC），之后立刻核对 -wal/-shm 的属主。"""
    con = sqlite3.connect(f"file:{V4_DB}?mode={mode}", uri=True, timeout=30, **options)
    try:
        yield con
    finally:
        con.close()
        fix_sidecars(V4_DB)


# ---------------------------------------------------------------- database schema version

class SchemaPlan:
    """切换前定下的库结构动作；touched / restored 记录切换过程中对库做过什么。"""

    def __init__(self, before: int | None, target: int, action: str) -> None:
        self.before = before  # 切换前生产库的 user_version；库不存在为 None
        self.target = target  # 目标清单的 schemaVersion
        self.action = action  # none / upgrade / downgrade
        self.touched = False  # 已对库发出过改动命令；命令可能先生效后报错，所以发出即算
        self.restored: dict | None = None  # 回滚时恢复 user_version 的结果，写进 switch-state.json

    def describe(self) -> dict:
        return {"before": self.before, "target": self.target, "action": self.action}


def read_schema_version() -> int | None:
    """生产库当前的 user_version：只读连接、不执行 DDL；库不存在返回 None，application_id 不符就报错。"""
    if not V4_DB.exists():
        return None
    with _production_db("ro") as con:
        con.execute("PRAGMA query_only=ON")
        if con.execute("PRAGMA application_id").fetchone()[0] != V4_APPLICATION_ID:
            raise RuntimeError("V4 晨报库 application_id 不符")
        return con.execute("PRAGMA user_version").fetchone()[0]


def set_schema_version(version: int) -> None:
    """只改 user_version（降级与回滚用）：不建表、不删表、不碰业务数据。"""
    with _production_db("rw", isolation_level=None) as con:
        if con.execute("PRAGMA application_id").fetchone()[0] != V4_APPLICATION_ID:
            raise RuntimeError("V4 晨报库 application_id 不符")
        con.execute(f"PRAGMA user_version={int(version)}")


def plan_schema(new: dict) -> SchemaPlan:
    """切换前决定库结构动作；不被支持的组合在任何改动之前报错。"""
    target = manifest_schema_version(new)
    before = read_schema_version()
    if before is None or before == target:
        return SchemaPlan(before, target, "none")
    if target > before:
        if before < 1:
            raise RuntimeError(f"生产库 user_version={before}，不是可升级的 V4 晨报库，未做任何改动")
        if shutil.which("runuser") is None:
            raise RuntimeError("找不到 runuser，无法以 neckline 用户执行库升级，未做任何改动")
        return SchemaPlan(before, target, "upgrade")
    if (before, target) not in ADDITIVE_DOWNGRADES:
        allowed = ", ".join(f"{a}→{b}" for a, b in sorted(ADDITIVE_DOWNGRADES))
        raise RuntimeError(f"不支持库结构 {before}→{target} 的降级（只允许 {allowed}），未做任何改动")
    return SchemaPlan(before, target, "downgrade")


def _verify_schema(expected: int) -> None:
    actual = read_schema_version()
    if actual != expected:
        raise RuntimeError(f"库 user_version 是 {actual}，应为 {expected}")
    assert_ownership()


def upgrade_schema(plan: SchemaPlan) -> None:
    """新文件、新 wheel、新清单就位并校验通过后，以服务用户执行 init-db（init_schema 在单个事务里升级）。"""
    plan.touched = True
    try:
        run("runuser", "-u", SERVICE_USER, "--", str(ROOT / ".venv/bin/python"), "-m", "neckline.morning.cli",
            "--db", str(V4_DB), "init-db", cwd=ROOT)  # 和服务单元一样以 /opt/neckline 为工作目录
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or "").strip()[-300:] if isinstance(exc.stderr, str) else ""
        raise RuntimeError(f"库升级命令失败（退出码 {exc.returncode}）{tail}") from exc
    _verify_schema(plan.target)


def downgrade_schema(plan: SchemaPlan) -> None:
    """白名单内的降级：服务已停、文件未换时只改 user_version，多出来的表保留。"""
    plan.touched = True
    set_schema_version(plan.target)
    _verify_schema(plan.target)


def restore_schema(plan: SchemaPlan) -> dict:
    """回滚时把 user_version 恢复成 schemaBefore：升级过就按白名单降回去，降级过就写回原值。"""
    current = read_schema_version()
    if current == plan.before:
        return {"status": "unchanged", "version": current}
    if plan.action == "downgrade":
        if current != plan.target:
            raise RuntimeError(f"库 user_version 是 {current}，不是降级目标 {plan.target}，不能写回 {plan.before}")
    elif (current, plan.before) not in ADDITIVE_DOWNGRADES:
        raise RuntimeError(f"库 user_version 是 {current}，无法安全恢复到 {plan.before}")
    set_schema_version(plan.before)
    _verify_schema(plan.before)
    return {"status": "restored", "from": current, "version": plan.before}


READY_FLAGS = ("readPage", "modelKey", "mcp", "tushare", "mairui")


def _read_health() -> dict | None:
    result = run("curl", "-s", "-m", "3", "http://127.0.0.1:8002/api/v4/health", check=False)
    if getattr(result, "returncode", 0) != 0:
        return None
    try:
        body = json.loads(result.stdout)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _version_matches(reported: object, expected: str) -> bool:
    """就绪接口的 version 带 v 前缀，清单里的 version 不带。"""
    if not isinstance(reported, str):
        return False
    return reported.removeprefix("v") == expected.removeprefix("v")


def _health_matches(body: dict, release: str, version: str | None, prior: dict | None) -> bool:
    if body.get("releaseSet") != release or body.get("db") != "ready":
        return False
    if version is not None and not _version_matches(body.get("version"), version):
        return False
    if prior:
        for flag in READY_FLAGS + KX_FLAGS:
            if prior.get(flag) is True and body.get(flag) is not True:
                return False
    return True


def health(release: str, *, version: str | None = None, prior: dict | None = None) -> None:
    for _ in range(40):
        body = _read_health()
        if body is not None and _health_matches(body, release, version, prior):
            return
        time.sleep(0.5)
    raise RuntimeError(f"health 未报告 {release}")


def _capture_health() -> dict | None:
    for _ in range(40):
        body = _read_health()
        if body is not None:
            return body
        time.sleep(0.5)
    return None


def _expected_prior(prior: dict | None, manifest: dict) -> dict | None:
    """切换后要比对的切换前 health：目标发布不认识 KX（清单 schemaVersion 低于 KX_SCHEMA，如回退到 B120）就不要求 KX 项。
    回滚时比对的是上一版自己的基线，原样使用。"""
    if prior is None or manifest_schema_version(manifest) >= KX_SCHEMA:
        return prior
    return {key: value for key, value in prior.items() if key not in KX_FLAGS}


def _baseline_problem(body: dict, previous: str) -> str | None:
    """切换前 health 基线必须形状完整，否则之后的「就绪」比较没有依据。"""
    if body.get("releaseSet") != previous:
        return f"releaseSet 不是 {previous}"
    if body.get("db") != "ready":
        return "db 未就绪"
    if not isinstance(body.get("version"), str):
        return "version 不是字符串"
    for flag in READY_FLAGS:
        if not isinstance(body.get(flag), bool):
            return f"{flag} 不是布尔值"
    for flag in KX_FLAGS:  # B120 的 health 没有这两项：缺失合法，出现了就必须是布尔值
        if flag in body and not isinstance(body[flag], bool):
            return f"{flag} 不是布尔值"
    return None


def _mask_state() -> bool | str:
    """服务单元现在是否被 mask，以 systemctl is-enabled 的实际输出为准；查不到记 unknown。"""
    try:
        values = [run("systemctl", "is-enabled", unit, check=False).stdout.strip() for unit in SERVICES]
    except Exception:
        return "unknown"
    if not all(values):
        return "unknown"
    return any(value.startswith("masked") for value in values)


def _write_switch_state(directory: Path, record: dict) -> None:
    if _SIDECAR_FIXES:
        record = {**record, "sidecarFixes": list(_SIDECAR_FIXES)}
    (directory / "switch-state.json").write_text(json.dumps(record, indent=2))


def _raise_step(step: str, exc: BaseException) -> None:
    error = RuntimeError(f"{step}：{exc}")
    error.step = step  # type: ignore[attr-defined]
    raise error from exc


def _runtime_file_state(old: dict, new: dict) -> str:
    """旧文件、新文件，或两边都对不上（混合）。清单发布号用来区分两套内容碰巧相同的情况。"""
    def matches(manifest: dict) -> bool:
        try:
            verify_runtime(ROOT, manifest)
        except Exception:
            return False
        return True

    old_ok, new_ok = matches(old), matches(new)
    release_set = None
    try:
        release_set = json.loads((ROOT / "release-manifest.json").read_text()).get("releaseSet")
    except Exception:
        release_set = None
    if old_ok and not new_ok:
        return "old" if release_set in (None, old.get("releaseSet")) else "mixed"
    if new_ok and not old_ok:
        return "new" if release_set in (None, new.get("releaseSet")) else "mixed"
    if old_ok and new_ok:
        if release_set == old.get("releaseSet"):
            return "old"
        if release_set == new.get("releaseSet"):
            return "new"
    return "mixed"


def _restore_runtime(*, old: dict, old_entries: dict[str, bytes], old_wheel: Path, old_dir: Path, new: dict) -> None:
    """还原旧文件、旧 wheel 和旧清单，并核对运行文件。失败时不碰服务。"""
    remove = set(new["backendFiles"]) - set(old["backendFiles"])
    try:
        write_runtime(ROOT, old_entries, remove)
    except Exception as exc:
        _raise_step("还原运行文件", exc)
    try:
        install_wheel(old_wheel)
    except Exception as exc:
        _raise_step("安装旧 wheel", exc)
    try:
        shutil.copyfile(old_dir / "backend/release-manifest.json", ROOT / "release-manifest.json")
        os.chmod(ROOT / "release-manifest.json", 0o644)
    except Exception as exc:
        _raise_step("还原清单", exc)
    try:
        verify_runtime(ROOT, old)
    except Exception as exc:
        _raise_step("校验旧运行文件", exc)


def _restore_units(before: dict) -> None:
    _check_database_for_service()  # 任何会让单元回来的路径都先过这一关；不过就保持 mask、不启动
    try:
        run("systemctl", "unmask", "--runtime", *SERVICES)
    except Exception as exc:
        _raise_step("取消 mask", exc)
    try:
        for unit in SERVICES + TIMERS:
            if before[unit]["active"] == "active":
                run("systemctl", "start", unit, check=False)
    except Exception as exc:
        _raise_step("恢复单元", exc)


def _restore_schema_step(plan: SchemaPlan) -> None:
    """服务停着、旧文件已还原时，把库的 user_version 恢复成切换前的值；失败即回滚失败，单元保持 mask。"""
    try:
        plan.restored = restore_schema(plan)
    except Exception as exc:
        plan.restored = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        _raise_step("恢复库结构版本", exc)


def _finish_rollback(*, previous: str, old: dict, before: dict, prior: dict | None, api_running: bool,
                     schema: SchemaPlan | None = None) -> dict:
    """旧文件已还原后的共用收尾：先恢复库结构版本（服务仍停着），再取消 mask 并恢复单元，
    API 切换前在运行时按旧版本做就绪检查，核对单元状态。"""
    if schema is not None and schema.touched:
        _restore_schema_step(schema)
    _restore_units(before)
    if api_running:
        version = old.get("version")
        if not isinstance(version, str) or not version:
            _raise_step("旧版本就绪检查", RuntimeError("旧清单缺少 version"))
        try:
            health(previous, version=version, prior=prior)
        except Exception as exc:
            _raise_step("旧版本就绪检查", exc)
    try:
        after = {unit: unit_state(unit) for unit in SERVICES + TIMERS}
    except Exception as exc:
        _raise_step("回滚后单元状态", exc)
    if after != before:
        _raise_step("回滚后单元状态", RuntimeError("回滚后服务或 timer 状态与切换前不一致"))
    return after


def _mask_and_stop() -> None:
    """mask 服务单元、停 timer 和服务，并确认服务已停。mask 的错误原样抛出（可能已生效），其余带步骤名。"""
    run("systemctl", "mask", "--runtime", *SERVICES)
    try:
        run("systemctl", "stop", *TIMERS)
        run("systemctl", "stop", *SERVICES)
    except Exception as exc:
        _raise_step("停止服务", exc)
    try:
        for _ in range(40):
            if all(unit_state(unit)["active"] == "inactive" for unit in SERVICES):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("服务未停止")
    except Exception as exc:
        _raise_step("确认服务已停", exc)


def _roll_back_to(*, previous: str, old: dict, old_entries: dict[str, bytes], old_wheel: Path, old_dir: Path,
                  new: dict, before: dict, prior: dict | None, api_running: bool,
                  schema: SchemaPlan | None = None) -> None:
    """停服并还原上一版。只有文件、wheel、清单都还原且校验通过、库结构版本回到切换前之后才恢复单元。
    生产库只改 user_version，绝不还原或覆盖。"""
    _mask_and_stop()
    _restore_runtime(old=old, old_entries=old_entries, old_wheel=old_wheel, old_dir=old_dir, new=new)
    _finish_rollback(previous=previous, old=old, before=before, prior=prior, api_running=api_running,
                     schema=schema)


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
    manifest_schema_version(old)  # 回滚时要用，无效就在动手前报错
    _SIDECAR_FIXES.clear()
    verify_runtime(ROOT, old)
    assert_ownership()
    running = [unit for unit in V4_JOBS if unit_state(unit)["active"] in ("active", "activating", "reloading")]
    if running:
        raise RuntimeError(f"V4 任务正在运行（{', '.join(running)}），稍后再切换")
    plan = plan_schema(new)  # 库结构动作在任何改动之前定下；不被支持的组合到这里就中止

    before = {unit: unit_state(unit) for unit in SERVICES + TIMERS}
    if check_only:
        report = {"release": release, "previous": previous, "preflight": "ok",
                  "schemaBefore": plan.before, "schemaTarget": plan.target, "schemaAction": plan.action,
                  "addedFiles": sorted(set(new["backendFiles"]) - set(old["backendFiles"])),
                  "removedFiles": sorted(set(old["backendFiles"]) - set(new["backendFiles"])),
                  "changedFiles": sorted(name for name in set(new["backendFiles"]) & set(old["backendFiles"])
                                         if new["backendFiles"][name] != old["backendFiles"][name]),
                  "units": before}
        if _SIDECAR_FIXES:
            report["sidecarFixes"] = list(_SIDECAR_FIXES)
        return report
    api_running = before["neckline.service"]["active"] == "active"
    if api_running:
        prior_health = _capture_health()
        if prior_health is None:
            raise RuntimeError("切换前读不到 health，未做任何改动")
        problem = _baseline_problem(prior_health, previous)
        if problem is not None:
            raise RuntimeError(f"切换前 health 基线无效，未做任何改动（{problem}）")
    else:
        prior_health = None
    record: dict = {"release": release, "previous": previous, "startedAt": datetime.now(timezone.utc).isoformat(),
                    "unitsBefore": before, "apiRunningBefore": api_running, "priorHealth": prior_health,
                    "schemaBefore": plan.before, "schemaTarget": plan.target, "schemaAction": plan.action}
    switched = False
    settled = False

    def outcome() -> dict:
        result = {"release": release, "commit": new["commit"], "switched": switched,
                  "runtimeFiles": len(new["backendFiles"]),
                  "removedFiles": sorted(set(old["backendFiles"]) - set(new["backendFiles"])),
                  "recoveryPoint": record["snapshot"].get("archive"), "units": record["unitsAfter"],
                  "schema": plan.describe()}
        if _SIDECAR_FIXES:
            result["sidecarFixes"] = list(_SIDECAR_FIXES)
        return result

    def note_schema() -> None:
        if plan.restored is not None:
            record["schemaRollback"] = plan.restored

    def fail_manual(step: str, exc: BaseException) -> None:
        nonlocal settled
        origin = exc.__cause__ if exc.__cause__ is not None else exc
        record["failure"] = record.get("failure") or f"{type(origin).__name__}: {origin}"
        record["switched"] = switched
        try:
            file_state = _runtime_file_state(old, new)
        except Exception as info_exc:
            file_state = f"unknown:{type(info_exc).__name__}"
        try:
            units_now = {unit: unit_state(unit) for unit in SERVICES + TIMERS}
        except Exception as info_exc:
            units_now = {"error": f"{type(info_exc).__name__}: {info_exc}"}
        record["unitsAfter"] = units_now
        try:
            schema_now: object = read_schema_version()
        except Exception as info_exc:
            schema_now = f"unknown:{type(info_exc).__name__}"
        note_schema()
        record["rollback"] = {
            "status": "failed", "release": previous, "failedStep": step,
            "error": f"{type(origin).__name__}: {origin}", "fileState": file_state,
            "recoveryPoint": (record.get("snapshot") or {}).get("archive"), "masked": _mask_state(),
            "schemaNow": schema_now,
        }
        record["finishedAt"] = datetime.now(timezone.utc).isoformat()
        try:
            _write_switch_state(new_dir, record)
        except Exception as write_exc:
            settled = True
            raise RuntimeError("回滚失败，需人工处理") from write_exc
        settled = True
        raise RuntimeError("回滚失败，需人工处理") from exc

    try:
        # mask 命令可能先生效、后报错：从发出命令起就当作可能已 mask，任何错误都走统一恢复与记录。
        run("systemctl", "mask", "--runtime", *SERVICES)
        run("systemctl", "stop", *TIMERS)
        run("systemctl", "stop", *SERVICES)
        for _ in range(40):
            if all(unit_state(unit)["active"] == "inactive" for unit in SERVICES):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("服务未停止")
        if any(unit_state(unit)["active"] in ("active", "activating", "reloading") for unit in V4_JOBS):
            raise RuntimeError("停 timer 后仍有 V4 任务在运行")
        record["snapshot"] = snapshot(new_dir)
        try:
            if plan.action == "downgrade":
                downgrade_schema(plan)
                record["schemaAfter"] = plan.target
            write_runtime(ROOT, new_entries, set(old["backendFiles"]) - set(new["backendFiles"]))
            install_wheel(new_wheel)
            shutil.copyfile(new_dir / "backend/release-manifest.json", ROOT / "release-manifest.json")
            os.chmod(ROOT / "release-manifest.json", 0o644)
            verify_runtime(ROOT, new)
            if plan.action == "upgrade":
                upgrade_schema(plan)
                record["schemaAfter"] = plan.target
            switched = True
        except Exception as swap_exc:
            record["failure"] = f"{type(swap_exc).__name__}: {swap_exc}"
            try:
                _restore_runtime(old=old, old_entries=old_entries, old_wheel=old_wheel, old_dir=old_dir, new=new)
            except Exception as restore_exc:
                fail_manual(getattr(restore_exc, "step", "还原旧版本"), restore_exc)
            try:
                after_units = _finish_rollback(previous=previous, old=old, before=before, prior=prior_health,
                                               api_running=api_running, schema=plan)
            except Exception as unit_exc:
                fail_manual(getattr(unit_exc, "step", "恢复单元"), unit_exc)
            note_schema()
            record["rollback"] = {"status": "rolled_back", "release": previous}
            record["switched"] = False
            record["unitsAfter"] = after_units
            record["finishedAt"] = datetime.now(timezone.utc).isoformat()
            settled = True
            _write_switch_state(new_dir, record)
            raise
        try:
            _restore_units(before)
        except Exception as unit_exc:
            step = getattr(unit_exc, "step", "取消 mask")
            if step == "取消 mask":
                # unmask 可能已经生效：先重新 mask 并确认服务已停，再动文件和库版本；之后一路保持 mask。
                root_cause = unit_exc.__cause__ if unit_exc.__cause__ is not None else unit_exc
                record["failure"] = record.get("failure") or f"{type(root_cause).__name__}: {root_cause}"
                try:
                    _mask_and_stop()
                except Exception as stop_exc:
                    fail_manual(getattr(stop_exc, "step", "重新 mask"), stop_exc)
                try:
                    _restore_runtime(old=old, old_entries=old_entries, old_wheel=old_wheel, old_dir=old_dir, new=new)
                    switched = False
                    if plan.touched:
                        _restore_schema_step(plan)
                except Exception as restore_exc:
                    fail_manual(getattr(restore_exc, "step", "还原旧版本"), restore_exc)
                fail_manual(step, unit_exc)
            raise
        record["unitsAfter"] = {unit: unit_state(unit) for unit in SERVICES + TIMERS}
        record["switched"] = True
        record["finishedAt"] = datetime.now(timezone.utc).isoformat()
        _write_switch_state(new_dir, record)
        if record["unitsAfter"] != before:
            raise RuntimeError("服务或 timer 状态未恢复到切换前")
        if not api_running:
            note = "切换前 API 未运行，已跳过就绪检查"
            record["readiness"] = note
            _write_switch_state(new_dir, record)
            return {**outcome(), "readiness": note}
        version = new.get("version")
        if not isinstance(version, str) or not version:
            raise RuntimeError("新清单缺少 version")
        health(release, version=version, prior=_expected_prior(prior_health, new))
        return outcome()
    except Exception as exc:
        if settled:
            raise
        record["failure"] = record.get("failure") or f"{type(exc).__name__}: {exc}"
        try:
            if switched:
                _roll_back_to(previous=previous, old=old, old_entries=old_entries, old_wheel=old_wheel,
                              old_dir=old_dir, new=new, before=before, prior=prior_health, api_running=api_running,
                              schema=plan)
            else:
                _finish_rollback(previous=previous, old=old, before=before, prior=prior_health,
                                 api_running=api_running, schema=plan)
        except Exception as rollback_exc:
            fail_manual(getattr(rollback_exc, "step", "回滚"), rollback_exc)
        note_schema()
        record["rollback"] = {"status": "rolled_back", "release": previous}
        record["finishedAt"] = datetime.now(timezone.utc).isoformat()
        try:
            record["unitsAfter"] = {unit: unit_state(unit) for unit in SERVICES + TIMERS}
        except Exception:
            pass
        _write_switch_state(new_dir, record)
        raise RuntimeError(f"已回滚到 {previous}") from exc


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
