"""The backend release switch: exact add/replace/remove, rollback, artifact checks."""
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release.py"
spec = importlib.util.spec_from_file_location("neckline_release", _SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
_REAL_STAT = Path.stat
_REAL_EUID = os.geteuid


def _allow_root_check_once(monkeypatch):
    """switch() 要求 euid 0，但测试进程不能 chown。只放行开头那一次检查。"""
    state = {"used": False}

    def euid():
        if not state["used"]:
            state["used"] = True
            return 0
        return _REAL_EUID()

    monkeypatch.setattr(os, "geteuid", euid)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _package(directory: Path, name: str, files: dict[str, bytes]) -> dict:
    backend = directory / "backend"
    backend.mkdir(parents=True)
    tar_path = backend / f"Neckline-Backend-{name}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as bundle:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    wheel = backend / "neckline_runtime-3.7.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel " + name.encode())
    manifest = {"releaseSet": name, "backendFiles": {path: _digest(data) for path, data in files.items()},
                "archiveSha256": _digest(tar_path.read_bytes()), "wheelSha256": _digest(wheel.read_bytes())}
    (backend / "release-manifest.json").write_text(json.dumps(manifest))
    return manifest


def _v4_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA application_id={release.V4_APPLICATION_ID}")
        for table in ("documents", "document_versions", "collection_runs", "model_receipts", "reports"):
            conn.execute(f"CREATE TABLE {table}(id INTEGER)")
        conn.execute("INSERT INTO reports VALUES (1)")


def test_runtime_file_set_excludes_tests_and_bytecode():
    assert release.is_runtime_file("neckline/morning/api.py")
    assert release.is_runtime_file("scripts/release.py") and release.is_runtime_file("pyproject.toml")
    assert not release.is_runtime_file("tests/test_release_script.py")
    assert not release.is_runtime_file("neckline/__pycache__/store.cpython-311.pyc")
    assert not release.is_runtime_file(".env")
    assert release.SERVICES == ["neckline.service"]
    assert release.TIMERS == [
        "neckline-v4-morning.timer", "neckline-v4-collect.timer", "neckline-v4-finalize.timer",
    ]


def test_switch_adds_replaces_removes_and_rolls_back_exactly(tmp_path):
    old_files = {"neckline/a.py": b"old a", "scripts/retired.py": b"retired"}
    new_files = {"neckline/a.py": b"new a", "neckline/morning/added.py": b"added"}
    old = _package(tmp_path / "old", "v1-b1", old_files)
    new = _package(tmp_path / "new", "v1-b2", new_files)
    root = tmp_path / "root"
    release.write_runtime(root, old_files, set())
    release.verify_runtime(root, old)

    release.write_runtime(root, new_files, set(old["backendFiles"]) - set(new["backendFiles"]))
    release.verify_runtime(root, new)
    assert not (root / "scripts/retired.py").exists()

    release.write_runtime(root, old_files, set(new["backendFiles"]) - set(old["backendFiles"]))
    release.verify_runtime(root, old)
    assert not (root / "neckline/morning/added.py").exists()
    assert (root / "neckline/a.py").read_bytes() == b"old a"


def test_replacement_deletes_every_file_absent_from_the_new_manifest(tmp_path):
    old_files = {f"neckline/retired/m{i:03d}.py": b"x" for i in range(320)}
    old_files["neckline/api/app.py"] = b"old"
    new_files = {"neckline/api/app.py": b"new"}
    root = tmp_path / "root"
    release.write_runtime(root, old_files, set())
    release.write_runtime(root, new_files, set(old_files) - set(new_files))
    assert (root / "neckline/api/app.py").read_bytes() == b"new"
    assert list((root / "neckline/retired").glob("*.py")) == []


def test_load_release_rejects_tampered_artifacts(tmp_path):
    files = {"neckline/a.py": b"content"}
    _package(tmp_path / "rel", "v1-b3", files)
    manifest, entries, wheel = release.load_release(tmp_path / "rel", "v1-b3")
    assert entries == files and wheel.name.startswith("neckline_runtime-")
    with pytest.raises(RuntimeError, match="不是"):
        release.load_release(tmp_path / "rel", "v1-b4")
    wheel.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="哈希"):
        release.load_release(tmp_path / "rel", "v1-b3")


def test_snapshot_keeps_only_the_verified_archive(tmp_path, monkeypatch):
    database = tmp_path / "v4.db"
    _v4_database(database)
    monkeypatch.setattr(release, "V4_DB", database)
    (tmp_path / "rel").mkdir()
    report = release.snapshot(tmp_path / "rel")
    assert report["roundTripVerified"] and report["counts"]["reports"] == 1
    assert sorted(p.name for p in (tmp_path / "rel/recovery").iterdir()) == ["v4-pre-switch.db.gz"]


def test_snapshot_skips_a_missing_v4_database(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "V4_DB", tmp_path / "missing.db")
    (tmp_path / "rel").mkdir()
    report = release.snapshot(tmp_path / "rel")
    assert report["present"] is False and "archive" not in report
    assert not (tmp_path / "rel/recovery").exists()


def test_switch_refuses_symlinked_runtime_target(tmp_path):
    root = tmp_path / "root"
    (root / "neckline").mkdir(parents=True)
    (root / "neckline/a.py").symlink_to(tmp_path / "elsewhere.py")
    with pytest.raises(RuntimeError, match="符号链接"):
        release.write_runtime(root, {"neckline/a.py": b"x"}, set())


def _prepare_switch(tmp_path, monkeypatch, *, with_v4: bool):
    old_files = {f"neckline/retired/m{i:03d}.py": b"old" for i in range(320)}
    old_files["neckline/api/app.py"] = b"b112"
    new_files = {"neckline/api/app.py": b"b113", "neckline/morning/api.py": b"v4"}
    root = tmp_path / "opt"
    root.mkdir()
    (root / "data").mkdir()
    previous, target = "v4.0.0-b112", "v4.0.0-b113"
    old = _package(root / "releases" / previous, previous, old_files)
    _package(root / "releases" / target, target, new_files)
    monkeypatch.setattr(os, "geteuid", _REAL_EUID)
    release.write_runtime(root, old_files, set())
    (root / "release-manifest.json").write_text(json.dumps(old))
    legacy = root / "data" / ("neckline" + ".db")
    legacy.write_bytes(b"leftover")
    legacy.chmod(0o777)
    v4 = root / "data" / "v4.db"
    if with_v4:
        _v4_database(v4)

    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.setattr(release, "V4_DB", v4)
    monkeypatch.setattr(release.socket, "gethostname", lambda: release.HOST)
    _allow_root_check_once(monkeypatch)
    monkeypatch.setattr(release.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=10))
    monkeypatch.setattr(release.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=20))
    def fake_stat(self, *, follow_symlinks=True):
        if self == legacy:
            raise AssertionError("switch inspected the leftover database")
        if self == root:
            return SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o755)
        if with_v4 and self == v4:
            return SimpleNamespace(st_uid=10, st_gid=20, st_mode=0o600)
        return _REAL_STAT(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", fake_stat)
    return root, old, legacy


def test_check_only_removes_the_previous_manifest_with_or_without_v4(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "unit_state", lambda unit: {"active": "inactive", "enabled": "disabled"})
    for present in (False, True):
        work = tmp_path / ("present" if present else "absent")
        work.mkdir()
        root, _old, legacy = _prepare_switch(work, monkeypatch, with_v4=present)
        result = release.switch(release="v4.0.0-b113", previous="v4.0.0-b112", check_only=True)
        assert result["preflight"] == "ok"
        assert len(result["removedFiles"]) == 320
        assert all(name.startswith("neckline/retired/") for name in result["removedFiles"])
        assert (root / "neckline/api/app.py").read_bytes() == b"b112"
        assert os.stat(legacy).st_mode & 0o777 == 0o777


def test_failed_switch_restores_the_previous_runtime(tmp_path, monkeypatch):
    root, old, legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True)
    calls = {"n": 0}

    def install_wheel(wheel):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("wheel failed")

    def fake_run(*args, check=True):
        return subprocess.CompletedProcess(list(args), 0, stdout="inactive\n", stderr="")

    monkeypatch.setattr(release, "install_wheel", install_wheel)
    monkeypatch.setattr(release, "run", fake_run)
    with pytest.raises(RuntimeError, match="wheel failed"):
        release.switch(release="v4.0.0-b113", previous="v4.0.0-b112")
    monkeypatch.undo()
    release.verify_runtime(root, old)
    assert (root / "neckline/retired/m000.py").read_bytes() == b"old"
    assert not (root / "neckline/morning/api.py").exists()
    assert legacy.read_bytes() == b"leftover"


def test_health_reads_the_v4_release_set(monkeypatch):
    seen = []

    def fake_run(*args, check=True):
        seen.append(args)
        return SimpleNamespace(stdout=json.dumps({"releaseSet": "v4.0.0-b113", "db": "ready"}))

    monkeypatch.setattr(release, "run", fake_run)
    release.health("v4.0.0-b113")
    assert seen[0][4] == "http://127.0.0.1:8002/api/v4/health"


def test_health_rejects_a_body_without_the_release_set(monkeypatch):
    monkeypatch.setattr(release.time, "sleep", lambda _seconds: None)

    def fake_run(*args, check=True):
        return SimpleNamespace(stdout=json.dumps({"db": "ready"}))

    monkeypatch.setattr(release, "run", fake_run)
    with pytest.raises(RuntimeError, match="health"):
        release.health("v4.0.0-b113")


def test_ownership_ignores_a_missing_v4_database_and_checks_a_present_one(tmp_path, monkeypatch):
    root = tmp_path / "opt"
    root.mkdir()
    v4 = tmp_path / "v4.db"
    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.setattr(release, "V4_DB", v4)
    monkeypatch.setattr(release.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=10))
    monkeypatch.setattr(release.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=20))
    created = {"yes": False}

    def fake_stat(self, *, follow_symlinks=True):
        if self == root:
            return SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o755)
        if created["yes"] and self == v4:
            return SimpleNamespace(st_uid=10, st_gid=20, st_mode=0o600)
        return _REAL_STAT(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", fake_stat)
    release.assert_ownership()
    created["yes"] = True
    v4.write_bytes(b"")
    release.assert_ownership()
    monkeypatch.setattr(Path, "stat", lambda self, *, follow_symlinks=True: SimpleNamespace(
        st_uid=0 if self == root else 10, st_gid=0 if self == root else 20,
        st_mode=0o755 if self == root else 0o644,
    ))
    with pytest.raises(RuntimeError, match="V4"):
        release.assert_ownership()
