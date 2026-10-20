"""The backend release switch: exact add/replace/remove, rollback, artifact checks."""
import gzip
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from neckline.morning import db as morning_db

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release.py"
spec = importlib.util.spec_from_file_location("neckline_release", _SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
_REAL_STAT = Path.stat
_REAL_CONNECT = sqlite3.connect  # 测试自己读库用它，不经过对 release.py 连接的监视
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


@pytest.fixture(autouse=True)
def _no_leaked_sidecar_notes():
    release._SIDECAR_FIXES.clear()
    yield
    release._SIDECAR_FIXES.clear()


# -wal/-shm 的属主由测试控制：真实文件由 SQLite 或测试创建，stat 报告的属主取自 owner（默认 neckline 10:20 0600），
# os.chown / os.chmod 改的是这份记录；chown_failures 次内 chown 抛 PermissionError。
_SIDECARS = SimpleNamespace(owner={}, chown_failures=0)


def _install_sidecar_owner(monkeypatch, db: Path) -> None:
    _SIDECARS.owner.clear()
    _SIDECARS.chown_failures = 0
    names = {f"{db.name}{suffix}" for suffix in release.SIDECAR_SUFFIXES}
    wrapped_stat = Path.stat
    real_chown, real_chmod = os.chown, os.chmod

    def is_sidecar(path) -> bool:
        return Path(path).parent == db.parent and Path(path).name in names

    def stat(self, *, follow_symlinks=True):
        found = wrapped_stat(self, follow_symlinks=follow_symlinks)  # 文件不存在时照常抛 FileNotFoundError
        if follow_symlinks and is_sidecar(self):
            uid, gid, mode = _SIDECARS.owner.get(self.name, (10, 20, 0o600))
            return SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=mode)
        return found

    def chown(path, uid, gid, **kwargs):
        if not is_sidecar(path):
            return real_chown(path, uid, gid, **kwargs)
        if _SIDECARS.chown_failures > 0:
            _SIDECARS.chown_failures -= 1
            raise PermissionError(1, "Operation not permitted")
        _SIDECARS.owner[Path(path).name] = (uid, gid, _SIDECARS.owner.get(Path(path).name, (0, 0, 0o600))[2])

    def chmod(path, mode, **kwargs):
        if is_sidecar(path):
            uid, gid, _old = _SIDECARS.owner.get(Path(path).name, (10, 20, 0o600))
            _SIDECARS.owner[Path(path).name] = (uid, gid, mode)
        return real_chmod(path, mode, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(os, "chown", chown)
    monkeypatch.setattr(os, "chmod", chmod)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _package(directory: Path, name: str, files: dict[str, bytes], *, schema_version: int | None = None) -> dict:
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
    manifest = {"releaseSet": name, "version": name.removeprefix("v").split("-b")[0], "commit": "c" * 40,
                "backendFiles": {path: _digest(data) for path, data in files.items()},
                "archiveSha256": _digest(tar_path.read_bytes()), "wheelSha256": _digest(wheel.read_bytes())}
    if schema_version is not None:  # B121 之前的清单没有这个字段，按 1 处理
        manifest["schemaVersion"] = schema_version
    (backend / "release-manifest.json").write_text(json.dumps(manifest))
    return manifest


def _v4_database(path: Path, version: int = 1) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA application_id={release.V4_APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version={version}")
        for table in ("documents", "document_versions", "collection_runs", "model_receipts", "reports"):
            conn.execute(f"CREATE TABLE {table}(id INTEGER)")
        conn.execute("INSERT INTO reports VALUES (1)")


def _real_v4_database(path: Path, version: int = 1) -> None:
    """真实结构的 V4 库，带一行文档数据：v1 只有 DDL，v2 再加三张 KX 表。"""
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(morning_db.DDL + (morning_db.KX_DDL if version >= 2 else ""))
        conn.execute("INSERT INTO documents VALUES ('doc-1', 'tushare', 'n-1', 'https://example.test/1', '2026-10-18T00:00:00+00:00')")
        conn.execute(f"PRAGMA application_id={morning_db.APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version={version}")
        conn.commit()


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
    monkeypatch.setattr(release.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=10))
    monkeypatch.setattr(release.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=20))
    _install_sidecar_owner(monkeypatch, database)
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


def _prepare_switch(tmp_path, monkeypatch, *, with_v4: bool, db_version: int = 1, real_db: bool = False,
                    target_schema: int | None = None, previous_schema: int | None = None):
    old_files = {f"neckline/retired/m{i:03d}.py": b"old" for i in range(320)}
    old_files["neckline/api/app.py"] = b"b112"
    new_files = {"neckline/api/app.py": b"b113", "neckline/morning/api.py": b"v4"}
    root = tmp_path / "opt"
    root.mkdir()
    (root / "data").mkdir()
    previous, target = "v4.0.0-b112", "v4.0.0-b113"
    old = _package(root / "releases" / previous, previous, old_files, schema_version=previous_schema)
    _package(root / "releases" / target, target, new_files, schema_version=target_schema)
    monkeypatch.setattr(os, "geteuid", _REAL_EUID)
    release.write_runtime(root, old_files, set())
    (root / "release-manifest.json").write_text(json.dumps(old))
    legacy = root / "data" / ("neckline" + ".db")
    legacy.write_bytes(b"leftover")
    legacy.chmod(0o777)
    v4 = root / "data" / "v4.db"
    if with_v4:
        (_real_v4_database if real_db else _v4_database)(v4, db_version)

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
    _install_sidecar_owner(monkeypatch, v4)
    return root, old, legacy


def test_check_only_removes_the_previous_manifest_with_or_without_v4(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "unit_state", lambda unit: {"active": "inactive", "enabled": "disabled"})
    for present in (False, True):
        work = tmp_path / ("present" if present else "absent")
        work.mkdir()
        root, _old, legacy = _prepare_switch(work, monkeypatch, with_v4=present)
        result = release.switch(release="v4.0.0-b113", previous="v4.0.0-b112", check_only=True)
        assert result["preflight"] == "ok"
        assert (result["schemaBefore"], result["schemaTarget"], result["schemaAction"]) == (1 if present else None, 1, "none")
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


_PREVIOUS = "v4.0.0-b112"
_TARGET = "v4.0.0-b113"
_FLAGS = {"readPage": True, "modelKey": True, "mcp": True, "tushare": True, "mairui": True}


def _health_body(release_set, **overrides):
    body = {"releaseSet": release_set, "version": "v4.0.0", "db": "ready", **_FLAGS}
    body.update(overrides)
    return body


class _Masking:
    """假 systemd 的 mask 状态，供 is-enabled 如实回答。

    fail_after 里的动作（"mask" 或 "unmask"）会先生效、再报错一次：模拟命令超时但副作用已经发生。
    """

    def __init__(self, fail_after=()):
        self.on = False
        self.fail_after = set(fail_after)

    def enabled(self, unit: str) -> str:
        return "masked-runtime\n" if self.on and unit in release.SERVICES else "disabled\n"

    def apply(self, action: str, args) -> None:
        if action in ("mask", "unmask"):
            self.on = action == "mask"
            if action in self.fail_after:
                self.fail_after.discard(action)
                raise subprocess.CalledProcessError(1, list(args), stderr="timed out after taking effect")


def _drive_switch(tmp_path, monkeypatch, *, service_active, after, fail_restore=False):
    """假 systemctl/curl。after 是文件切换完成、服务拉起来之后的 health。"""
    root, old, legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True)
    masking = _Masking()
    monkeypatch.setattr(release.time, "sleep", lambda _seconds: None)
    phase = {"down": False, "cycles": 0}
    installed = []

    def install_wheel(wheel):
        installed.append(Path(wheel))

    monkeypatch.setattr(release, "install_wheel", install_wheel)
    if fail_restore:
        original = release.write_runtime

        def write_runtime(root_path, entries, remove):
            if write_runtime.calls:
                raise RuntimeError("restore failed")
            write_runtime.calls += 1
            return original(root_path, entries, remove)

        write_runtime.calls = 0
        monkeypatch.setattr(release, "write_runtime", write_runtime)

    prior = _health_body(_PREVIOUS)

    def fake_run(*args, check=True):
        stdout = ""
        if args and args[0] == "systemctl":
            action = args[1]
            if action == "is-active":
                unit = args[2]
                stdout = "active\n" if service_active and unit == "neckline.service" and not phase["down"] else "inactive\n"
            elif action == "is-enabled":
                stdout = masking.enabled(args[2])
            elif action in ("mask", "unmask"):
                masking.apply(action, args)
            elif action == "stop":
                phase["down"] = True
            elif action == "start" and phase["down"]:
                phase["cycles"] += 1
                phase["down"] = False
            elif action == "start":
                phase["down"] = False
        elif args and args[0] == "curl":
            if phase["cycles"] >= 2 or (phase["down"] and phase["cycles"] >= 1):
                payload = prior
            elif phase["cycles"] == 1 and not phase["down"]:
                payload = after
            else:
                payload = prior
            stdout = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(list(args), 0, stdout=stdout, stderr="")

    monkeypatch.setattr(release, "run", fake_run)
    return root, old, legacy, installed


def _assert_database_untouched(root, legacy):
    assert legacy.read_bytes() == b"leftover"
    with sqlite3.connect(root / "data" / "v4.db") as conn:
        assert conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 1


def _switch_state(root):
    return json.loads((root / "releases" / _TARGET / "switch-state.json").read_text())


def test_switch_rolls_back_when_health_never_becomes_ready(tmp_path, monkeypatch):
    root, old, legacy, installed = _drive_switch(tmp_path, monkeypatch, service_active=True,
                                                 after=_health_body("v0.0.0-b0"))
    with pytest.raises(RuntimeError, match=f"已回滚到 {_PREVIOUS}") as caught:
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "health" in str(caught.value.__cause__)
    release.verify_runtime(root, old)
    assert json.loads((root / "release-manifest.json").read_text())["releaseSet"] == _PREVIOUS
    assert _PREVIOUS in installed[1].parts and _TARGET in installed[0].parts
    state = _switch_state(root)
    assert state["rollback"] == {"status": "rolled_back", "release": _PREVIOUS}
    _assert_database_untouched(root, legacy)


def test_switch_rolls_back_when_db_is_not_ready(tmp_path, monkeypatch):
    root, old, legacy, installed = _drive_switch(tmp_path, monkeypatch, service_active=True,
                                                 after=_health_body(_TARGET, db="db_not_ready"))
    with pytest.raises(RuntimeError, match=f"已回滚到 {_PREVIOUS}"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    release.verify_runtime(root, old)
    assert json.loads((root / "release-manifest.json").read_text())["releaseSet"] == _PREVIOUS
    assert _PREVIOUS in installed[1].parts
    assert _switch_state(root)["rollback"]["status"] == "rolled_back"
    _assert_database_untouched(root, legacy)


def test_switch_rolls_back_when_previously_true_mcp_becomes_false(tmp_path, monkeypatch):
    root, old, legacy, installed = _drive_switch(tmp_path, monkeypatch, service_active=True,
                                                 after=_health_body(_TARGET, mcp=False))
    with pytest.raises(RuntimeError, match=f"已回滚到 {_PREVIOUS}"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    release.verify_runtime(root, old)
    assert json.loads((root / "release-manifest.json").read_text())["releaseSet"] == _PREVIOUS
    assert _PREVIOUS in installed[1].parts
    assert _switch_state(root)["rollback"]["release"] == _PREVIOUS
    _assert_database_untouched(root, legacy)


def test_switch_reports_manual_recovery_when_rollback_fails(tmp_path, monkeypatch):
    root, _old, legacy, _installed = _drive_switch(tmp_path, monkeypatch, service_active=True,
                                                   after=_health_body("v0.0.0-b0"), fail_restore=True)
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理") as caught:
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "已回滚" not in str(caught.value)
    assert (root / "neckline/morning/api.py").read_bytes() == b"v4"
    assert not (root / "neckline/retired/m000.py").exists()
    assert json.loads((root / "release-manifest.json").read_text())["releaseSet"] == _TARGET
    state = _switch_state(root)
    assert state["rollback"]["status"] == "failed"
    assert "restore failed" in state["rollback"]["error"]
    _assert_database_untouched(root, legacy)


def test_switch_skips_readiness_when_the_api_was_not_running(tmp_path, monkeypatch):
    root, _old, legacy, installed = _drive_switch(tmp_path, monkeypatch, service_active=False, after="not-json")
    result = release.switch(release=_TARGET, previous=_PREVIOUS)
    assert result["readiness"] == "切换前 API 未运行，已跳过就绪检查"
    assert set(result) == {"release", "commit", "switched", "runtimeFiles", "removedFiles",
                           "recoveryPoint", "units", "schema", "readiness"}
    assert result["schema"] == {"before": 1, "target": 1, "action": "none"}
    assert result["switched"] is True and result["release"] == _TARGET and result["commit"]
    assert (root / "neckline/api/app.py").read_bytes() == b"b113"
    assert len(installed) == 1 and _TARGET in installed[0].parts
    _assert_database_untouched(root, legacy)


def _drive_scripted(tmp_path, monkeypatch, *, service_active, extra_active=(), curl=None, install=None,
                    masking=None):
    """和 _drive_switch 同一套阶段，但记下每条命令，并允许替换 curl / wheel 安装 / mask 行为。"""
    root, old, legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True)
    masking = masking or _Masking()
    monkeypatch.setattr(release.time, "sleep", lambda _seconds: None)
    phase = {"down": False, "cycles": 0}
    calls = []
    monkeypatch.setattr(release, "install_wheel", install or (lambda _wheel: None))
    prior = _health_body(_PREVIOUS)
    active = {"neckline.service"} if service_active else set()
    active.update(extra_active)

    def fake_run(*args, check=True):
        calls.append(args)
        if args and args[0] == "curl" and curl is not None:
            return curl()
        stdout = ""
        code = 0
        if args and args[0] == "systemctl":
            action = args[1]
            unit = args[2] if len(args) > 2 else ""
            if action == "is-active":
                if phase["down"] or unit not in active:
                    stdout = "inactive\n"
                elif unit == "neckline-v4-morning.timer" and phase["cycles"] >= 2:
                    stdout = "inactive\n"
                else:
                    stdout = "active\n"
            elif action == "is-enabled":
                stdout = masking.enabled(unit)
            elif action in ("mask", "unmask"):
                masking.apply(action, args)
            elif action == "stop":
                phase["down"] = True
            elif action == "start" and phase["down"]:
                phase["cycles"] += 1
                phase["down"] = False
            elif action == "start":
                phase["down"] = False
        elif args and args[0] == "curl":
            if phase["cycles"] >= 2 or (phase["down"] and phase["cycles"] >= 1):
                payload = prior
            elif phase["cycles"] == 1 and not phase["down"]:
                payload = _health_body("v0.0.0-b0")
            else:
                payload = prior
            stdout = json.dumps(payload)
        return subprocess.CompletedProcess(list(args), code, stdout=stdout, stderr="")

    monkeypatch.setattr(release, "run", fake_run)
    return root, old, legacy, calls


def _actions(calls):
    return [args[1] for args in calls if args and args[0] == "systemctl"]


def test_rollback_failure_keeps_mask_and_records_the_scene(tmp_path, monkeypatch):
    root, _old, legacy, calls = _drive_scripted(tmp_path, monkeypatch, service_active=True)
    original = release.write_runtime

    def write_runtime(root_path, entries, remove):
        if write_runtime.calls:
            raise RuntimeError("restore failed")
        write_runtime.calls += 1
        return original(root_path, entries, remove)

    write_runtime.calls = 0
    monkeypatch.setattr(release, "write_runtime", write_runtime)
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理") as caught:
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "已回滚" not in str(caught.value)
    assert _actions(calls).count("unmask") == 1
    assert _actions(calls).count("start") == 1
    state = _switch_state(root)
    assert state["rollback"]["status"] == "failed"
    assert state["rollback"]["failedStep"] == "还原运行文件"
    assert "restore failed" in state["rollback"]["error"]
    assert state["rollback"]["fileState"] == "new"
    assert state["rollback"]["masked"] is True
    assert state["rollback"]["recoveryPoint"]
    assert state["failure"]
    assert (root / "neckline/morning/api.py").read_bytes() == b"v4"
    _assert_database_untouched(root, legacy)


def test_unmask_failure_after_mask_is_recorded(tmp_path, monkeypatch):
    root, old, legacy, calls = _drive_scripted(tmp_path, monkeypatch, service_active=True)
    real_run = release.run

    def fake_run(*args, check=True):
        if args and args[0] == "systemctl" and args[1] == "unmask":
            calls.append(args)
            raise RuntimeError("unmask failed")
        return real_run(*args, check=check)

    monkeypatch.setattr(release, "run", fake_run)
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理") as caught:
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "已回滚" not in str(caught.value)
    assert "unmask failed" in str(caught.value.__cause__)
    assert "start" not in _actions(calls)
    state = _switch_state(root)
    assert state["failure"] and state["rollback"]["status"] == "failed"
    assert state["rollback"]["failedStep"] == "取消 mask"
    assert state["rollback"]["masked"] is True
    assert "unmask failed" in state["rollback"]["error"]
    assert state["rollback"]["fileState"] == "old"
    release.verify_runtime(root, old)
    _assert_database_untouched(root, legacy)


def test_old_wheel_restore_failure_is_recorded(tmp_path, monkeypatch):
    root, old, legacy, calls = _drive_scripted(tmp_path, monkeypatch, service_active=False)
    calls_box = {"n": 0}

    def install(_wheel):
        calls_box["n"] += 1
        if calls_box["n"] == 1:
            raise RuntimeError("new wheel failed")
        raise RuntimeError("old wheel failed")

    monkeypatch.setattr(release, "install_wheel", install)
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理") as caught:
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "old wheel failed" in str(caught.value.__cause__)
    assert "unmask" not in _actions(calls) and "start" not in _actions(calls)
    state = _switch_state(root)
    assert state["failure"] and state["rollback"]["status"] == "failed"
    assert state["rollback"]["failedStep"] == "安装旧 wheel"
    assert "old wheel failed" in state["rollback"]["error"]
    assert state["rollback"]["masked"] is True
    release.verify_runtime(root, old)
    _assert_database_untouched(root, legacy)


def test_rollback_fails_when_previous_timer_does_not_come_back(tmp_path, monkeypatch):
    root, old, legacy, _calls = _drive_scripted(
        tmp_path, monkeypatch, service_active=True, extra_active={"neckline-v4-morning.timer"})
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理") as caught:
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "已回滚" not in str(caught.value)
    state = _switch_state(root)
    assert state["rollback"]["status"] == "failed"
    assert state["rollback"]["failedStep"] == "回滚后单元状态"
    assert state["unitsBefore"]["neckline-v4-morning.timer"]["active"] == "active"
    assert state["rollback"]["fileState"] == "old"
    release.verify_runtime(root, old)
    assert json.loads((root / "release-manifest.json").read_text())["releaseSet"] == _PREVIOUS
    _assert_database_untouched(root, legacy)


@pytest.mark.parametrize("stdout,code", [("", 28), ("not-json", 0), ("[]", 0), ("null", 0)])
def test_switch_aborts_before_changes_when_prior_health_is_unusable(tmp_path, monkeypatch, stdout, code):
    root, _old, legacy, calls = _drive_scripted(tmp_path, monkeypatch, service_active=True, curl=lambda: (
        subprocess.CompletedProcess(["curl"], code, stdout=stdout, stderr="")))
    with pytest.raises(RuntimeError, match="切换前读不到 health，未做任何改动"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "mask" not in _actions(calls) and "stop" not in _actions(calls) and "start" not in _actions(calls)
    assert (root / "neckline/api/app.py").read_bytes() == b"b112"
    assert not (root / "releases" / _TARGET / "switch-state.json").exists()
    _assert_database_untouched(root, legacy)


def test_successful_switch_keeps_the_previous_return_keys(tmp_path, monkeypatch):
    root, _old, _legacy, installed = _drive_switch(tmp_path, monkeypatch, service_active=True,
                                                   after=_health_body(_TARGET))
    result = release.switch(release=_TARGET, previous=_PREVIOUS)
    assert set(result) == {"release", "commit", "switched", "runtimeFiles", "removedFiles", "recoveryPoint", "units",
                           "schema"}
    assert result["schema"] == {"before": 1, "target": 1, "action": "none"}
    assert result["switched"] is True and result["release"] == _TARGET
    assert (root / "neckline/api/app.py").read_bytes() == b"b113"
    assert json.loads((root / "release-manifest.json").read_text())["releaseSet"] == _TARGET
    assert len(installed) == 1 and _TARGET in installed[0].parts


def test_mask_that_takes_effect_before_failing_is_recovered_and_recorded(tmp_path, monkeypatch):
    masking = _Masking(fail_after={"mask"})
    root, old, legacy, calls = _drive_scripted(tmp_path, monkeypatch, service_active=True, masking=masking)
    with pytest.raises(RuntimeError, match=f"已回滚到 {_PREVIOUS}") as caught:
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert isinstance(caught.value.__cause__, subprocess.CalledProcessError)
    actions = _actions(calls)
    assert actions.index("mask") < actions.index("unmask")
    assert "stop" not in actions and actions.count("start") == 1
    assert masking.on is False and release._mask_state() is False
    state = _switch_state(root)
    assert state["rollback"] == {"status": "rolled_back", "release": _PREVIOUS}
    assert "CalledProcessError" in state["failure"] and state["unitsAfter"] == state["unitsBefore"]
    release.verify_runtime(root, old)
    _assert_database_untouched(root, legacy)


def test_unmask_that_takes_effect_before_failing_records_the_actual_mask_state(tmp_path, monkeypatch):
    masking = _Masking(fail_after={"unmask"})
    root, old, legacy, calls = _drive_scripted(tmp_path, monkeypatch, service_active=True, masking=masking)
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理") as caught:
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "已回滚" not in str(caught.value)
    assert "取消 mask" in str(caught.value.__cause__)
    assert isinstance(caught.value.__cause__.__cause__, subprocess.CalledProcessError)
    assert "start" not in _actions(calls)
    state = _switch_state(root)
    assert state["rollback"]["status"] == "failed" and state["rollback"]["failedStep"] == "取消 mask"
    # unmask 已经生效，所以先重新 mask（初始 mask 之外又一次）再还原文件；记录以 systemctl is-enabled 的查询为准。
    assert _actions(calls).count("mask") == 2 and masking.on is True and state["rollback"]["masked"] is True
    assert state["rollback"]["fileState"] == "old"
    release.verify_runtime(root, old)
    _assert_database_untouched(root, legacy)


def test_mask_state_comes_from_systemctl_and_is_unknown_when_it_cannot_be_read(monkeypatch):
    answers = {"value": "masked-runtime\n"}
    monkeypatch.setattr(release, "run", lambda *args, check=True: SimpleNamespace(stdout=answers["value"]))
    assert release._mask_state() is True
    answers["value"] = "enabled\n"
    assert release._mask_state() is False
    answers["value"] = ""
    assert release._mask_state() == "unknown"

    def boom(*_args, check=True):
        raise OSError("systemctl missing")

    monkeypatch.setattr(release, "run", boom)
    assert release._mask_state() == "unknown"


def _curl_after_baseline(*, ready_after: bool):
    """第一次 curl 是切换前基线（正常），之后按 ready_after 回答旧版本是否就绪。"""
    seen = {"curl": 0}

    def curl():
        seen["curl"] += 1
        if seen["curl"] == 1 or ready_after:
            body = _health_body(_PREVIOUS)
        else:
            body = _health_body(_PREVIOUS, db="db_not_ready")
        return subprocess.CompletedProcess(["curl"], 0, stdout=json.dumps(body), stderr="")

    return curl, seen


def _wheel_fails_once():
    calls = {"n": 0}

    def install(_wheel):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("new wheel failed")

    return install


def test_wheel_failure_with_unready_old_api_is_a_failed_rollback(tmp_path, monkeypatch):
    curl, seen = _curl_after_baseline(ready_after=False)
    root, old, legacy, calls = _drive_scripted(tmp_path, monkeypatch, service_active=True, curl=curl,
                                               install=_wheel_fails_once())
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理") as caught:
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "已回滚" not in str(caught.value)
    assert "health" in str(caught.value.__cause__)
    assert seen["curl"] > 1  # 基线之后，旧版本的就绪检查确实跑过
    state = _switch_state(root)
    assert state["rollback"]["status"] == "failed" and state["rollback"]["failedStep"] == "旧版本就绪检查"
    assert state["rollback"]["fileState"] == "old"
    assert state["rollback"]["masked"] is False  # 要检查旧 API 就绪必须先取消 mask 并拉起，记录以实际查询为准
    assert "new wheel failed" in state["failure"] and state["switched"] is False
    assert json.loads((root / "release-manifest.json").read_text())["releaseSet"] == _PREVIOUS
    release.verify_runtime(root, old)
    _assert_database_untouched(root, legacy)


def test_wheel_failure_rolls_back_only_after_the_old_api_is_ready(tmp_path, monkeypatch):
    curl, seen = _curl_after_baseline(ready_after=True)
    root, old, legacy, _calls = _drive_scripted(tmp_path, monkeypatch, service_active=True, curl=curl,
                                                install=_wheel_fails_once())
    with pytest.raises(RuntimeError, match="new wheel failed"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert seen["curl"] == 2  # 基线一次，旧版本就绪检查一次
    state = _switch_state(root)
    assert state["rollback"] == {"status": "rolled_back", "release": _PREVIOUS}
    assert state["unitsAfter"] == state["unitsBefore"]
    release.verify_runtime(root, old)
    _assert_database_untouched(root, legacy)


@pytest.mark.parametrize("body", [
    {},
    {"detail": "Service Unavailable"},
    _health_body("v9.9.9-b9"),
    _health_body(_PREVIOUS, db="db_not_ready"),
    _health_body(_PREVIOUS, version=None),
    _health_body(_PREVIOUS, version=4),
    _health_body(_PREVIOUS, mcp="true"),
    _health_body(_PREVIOUS, tushare=None),
])
def test_switch_aborts_before_the_mask_when_the_prior_health_baseline_is_malformed(tmp_path, monkeypatch, body):
    root, _old, legacy, calls = _drive_scripted(tmp_path, monkeypatch, service_active=True, curl=lambda: (
        subprocess.CompletedProcess(["curl"], 0, stdout=json.dumps(body), stderr="")))
    with pytest.raises(RuntimeError, match="切换前 health 基线无效，未做任何改动"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert not {"mask", "unmask", "stop", "start"} & set(_actions(calls))
    assert (root / "neckline/api/app.py").read_bytes() == b"b112"
    assert not (root / "releases" / _TARGET / "switch-state.json").exists()
    _assert_database_untouched(root, legacy)


def test_a_flag_that_was_false_before_is_still_a_valid_baseline():
    # 布尔值本身（含 False）合法；切换后只要求切换前为 True 的项仍为 True。
    assert release._baseline_problem(_health_body(_PREVIOUS, mcp=False, mairui=False), _PREVIOUS) is None
    assert release._baseline_problem(_health_body(_PREVIOUS, mcp=0), _PREVIOUS) == "mcp 不是布尔值"


# ------------------------------------------------------------------ 库结构版本：manifest、升级、降级、回滚

_KX_TABLES = {"kx_pool", "kx_picks", "kx_daily_stats"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def _fake_repo(tmp_path: Path, db_text: str) -> Path:
    """只含构建所需文件的临时 git 仓库：app.py（RELEASE_SET）、db.py（SCHEMA_VERSION）和一份 release.py。"""
    repo = tmp_path / "repo"
    files = {"Backend/neckline/api/app.py": 'RELEASE_SET = "v9.9.9-b9"\n',
             "Backend/neckline/morning/db.py": db_text, "Backend/pyproject.toml": "[project]\nname='x'\n"}
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    (repo / "Backend/scripts").mkdir(parents=True)
    shutil.copy(_SCRIPT, repo / "Backend/scripts/release.py")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def _load_copy(repo: Path):
    spec_copy = importlib.util.spec_from_file_location("neckline_release_copy", repo / "Backend/scripts/release.py")
    module = importlib.util.module_from_spec(spec_copy)
    spec_copy.loader.exec_module(module)
    return module


def test_build_records_the_schema_version_of_the_built_commit(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path, 'APPLICATION_ID = 1\nSCHEMA_VERSION = 7  # 当前库结构\n')
    # 工作区里的改动不能影响产物：版本号只读被构建的 commit。
    (repo / "Backend/neckline/morning/db.py").write_text("SCHEMA_VERSION = 99\n")
    module = _load_copy(repo)
    real_run = subprocess.run

    def fake_run(argv, *args, **kwargs):
        if "pip" in argv and "wheel" in argv:  # 离线：不真的打 wheel
            (Path(argv[argv.index("-w") + 1]) / "neckline_runtime-9.9.9-py3-none-any.whl").write_bytes(b"wheel")
            return subprocess.CompletedProcess(argv, 0)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = module.build(release="v9.9.9-b9", commit="HEAD", out=tmp_path / "out")
    manifest = json.loads((tmp_path / "out/v9.9.9-b9/backend/release-manifest.json").read_text())
    assert manifest["schemaVersion"] == 7 and manifest["releaseSet"] == "v9.9.9-b9"
    assert manifest["commit"] == result["commit"] and "neckline/morning/db.py" in manifest["backendFiles"]


def test_build_refuses_a_commit_without_exactly_one_schema_version(tmp_path):
    cases = ("APPLICATION_ID = 1\n", "SCHEMA_VERSION = 1\nSCHEMA_VERSION = 2\n", "SCHEMA_VERSION = 0\n")
    for index, text in enumerate(cases):
        repo = _fake_repo(tmp_path / f"case{index}", text)
        with pytest.raises(SystemExit, match="SCHEMA_VERSION"):
            _load_copy(repo).schema_version_at(repo, "HEAD")


def test_a_manifest_without_schema_version_means_one():
    assert release.manifest_schema_version({"releaseSet": "v4.0.0-b120"}) == 1
    assert release.manifest_schema_version({"schemaVersion": 2}) == 2
    for bad in (0, True, "2", None, 2.0):
        with pytest.raises(RuntimeError, match="schemaVersion"):
            release.manifest_schema_version({"releaseSet": "x", "schemaVersion": bad})


@pytest.mark.parametrize("db_version,target_schema,action", [(1, None, "none"), (1, 2, "upgrade"), (2, None, "downgrade")])
def test_check_only_reports_the_schema_plan_and_changes_nothing(tmp_path, monkeypatch, db_version, target_schema, action):
    monkeypatch.setattr(release, "unit_state", lambda unit: {"active": "inactive", "enabled": "disabled"})
    monkeypatch.setattr(release.shutil, "which", lambda name: f"/usr/sbin/{name}")
    root, _old, _legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True, db_version=db_version, real_db=True,
                                          target_schema=target_schema, previous_schema=db_version)
    result = release.switch(release=_TARGET, previous=_PREVIOUS, check_only=True)
    assert result["preflight"] == "ok"
    assert (result["schemaBefore"], result["schemaTarget"], result["schemaAction"]) == (
        db_version, target_schema or 1, action)
    assert _db_facts(root / "data/v4.db")["version"] == db_version
    assert (root / "neckline/api/app.py").read_bytes() == b"b112"
    assert not (root / "releases" / _TARGET / "switch-state.json").exists()


def _db_facts(db_file: Path) -> dict:
    with closing(_REAL_CONNECT(f"file:{db_file}?mode=ro", uri=True)) as con:
        return {"version": con.execute("PRAGMA user_version").fetchone()[0],
                "tables": {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}}


def _documents(db_file: Path) -> list:
    with closing(_REAL_CONNECT(f"file:{db_file}?mode=ro", uri=True)) as con:
        return con.execute("SELECT document_id, source_key, external_id FROM documents").fetchall()


def _real_migration(args):
    """假 runuser 的默认实现：核对命令形状后，用真实的 init_schema 迁移临时库。"""
    assert args[1:4] == ("-u", "neckline", "--"), args
    assert args[4] == str(release.ROOT / ".venv/bin/python"), args
    assert args[5:] == ("-m", "neckline.morning.cli", "--db", str(release.V4_DB), "init-db"), args
    assert morning_db.SCHEMA_VERSION == 2, "库结构版本变了：更新发布脚本的升级测试"
    morning_db.init_schema(release.V4_DB)


def _drive_schema(tmp_path, monkeypatch, *, db_version, target_schema=None, previous_schema=None, real_db=True,
                  migrate=None, health_after=None, install=None, service_active=True, previous_kx=False,
                  target_kx=False):
    """假 systemctl / curl / runuser；health 如实反映「已装清单要求的库结构版本」和库的 user_version 是否一致，
    所以没把版本号回到位的回滚过不了旧版本的就绪检查。calls 里 db-set / write_runtime 是内部步骤的顺序标记。"""
    root, old, legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True, db_version=db_version, real_db=real_db,
                                        target_schema=target_schema, previous_schema=previous_schema)
    db_file = root / "data/v4.db"
    masking = _Masking()
    active = {"neckline.service"} if service_active else set()
    calls: list = []
    cwds: list = []
    monkeypatch.setattr(release.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(release.shutil, "which", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(release, "install_wheel", install or (lambda _wheel: None))
    original_write, original_set = release.write_runtime, release.set_schema_version

    def write_runtime(root_path, entries, remove):
        calls.append(("write_runtime",))
        return original_write(root_path, entries, remove)

    def set_schema_version(version):
        calls.append(("db-set", version))
        return original_set(version)

    monkeypatch.setattr(release, "write_runtime", write_runtime)
    monkeypatch.setattr(release, "set_schema_version", set_schema_version)

    def health_body():
        installed = json.loads((root / "release-manifest.json").read_text())
        ready = _db_facts(db_file)["version"] == installed.get("schemaVersion", 1)
        body = _health_body(installed["releaseSet"], db="ready" if ready else "db_not_ready")
        if (installed["releaseSet"] == _PREVIOUS and previous_kx) or (installed["releaseSet"] == _TARGET and target_kx):
            body.update({"kxPool": True, "kxPicks": True})  # B120 的 health 没有这两项
        if installed["releaseSet"] == _TARGET and health_after:
            body.update(health_after)
        return body

    def fake_run(*args, check=True, cwd=None):
        calls.append(args)
        stdout, code = "", 0
        if args[0] == "systemctl":
            action = args[1]
            unit = args[2] if len(args) > 2 else ""
            if action == "is-active":
                stdout = "active\n" if unit in active else "inactive\n"
            elif action == "is-enabled":
                stdout = masking.enabled(unit)
            elif action in ("mask", "unmask"):
                masking.apply(action, args)
            elif action == "stop":
                active.difference_update(args[2:])
            elif action == "start":
                if masking.on and unit in release.SERVICES:
                    code = 1  # systemd 不会启动被 mask 的单元
                else:
                    active.add(unit)
        elif args[0] == "curl":
            if "neckline.service" in active:
                stdout = json.dumps(health_body())
            else:
                code = 7
        elif args[0] == "runuser":
            cwds.append(cwd)
            (migrate or _real_migration)(args)
        else:
            raise AssertionError(f"unexpected command {args}")
        if code and check:
            raise subprocess.CalledProcessError(code, list(args))
        return subprocess.CompletedProcess(list(args), code, stdout=stdout, stderr="")

    monkeypatch.setattr(release, "run", fake_run)
    return SimpleNamespace(root=root, old=old, legacy=legacy, calls=calls, masking=masking, active=active,
                           db=db_file, cwds=cwds)


def _flow(calls) -> list:
    """只留改变状态的步骤，按发生顺序。"""
    steps = []
    for args in calls:
        if args[0] == "systemctl" and args[1] in ("mask", "unmask", "stop", "start"):
            steps.append(args[1])
        elif args[0] in ("runuser", "write_runtime"):
            steps.append(args[0])
        elif args[0] == "db-set":
            steps.append(f"db-set:{args[1]}")
    return steps


def _command_failure(code=1, stderr="boom"):
    def migrate(args):
        raise subprocess.CalledProcessError(code, list(args), stderr=stderr)

    return migrate


def test_upgrade_moves_a_v1_database_to_v2_after_the_new_files_are_in_place(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2)
    result = release.switch(release=_TARGET, previous=_PREVIOUS)
    assert result["switched"] is True and result["schema"] == {"before": 1, "target": 2, "action": "upgrade"}
    facts = _db_facts(driven.db)
    assert facts["version"] == 2 and _KX_TABLES <= facts["tables"]
    assert _documents(driven.db) == [("doc-1", "tushare", "n-1")]
    # 停服 → 恢复点 → 换文件 → 迁移 → 恢复单元；迁移不早于文件就位，也不晚于单元恢复。
    assert _flow(driven.calls) == ["mask", "stop", "stop", "write_runtime", "runuser", "unmask", "start"]
    assert driven.cwds == [driven.root]  # init-db 的工作目录是 /opt/neckline，和服务单元一致
    state = _switch_state(driven.root)
    assert (state["schemaBefore"], state["schemaTarget"], state["schemaAction"], state["schemaAfter"]) == (
        1, 2, "upgrade", 2)
    assert "schemaRollback" not in state and state["switched"] is True
    assert json.loads((driven.root / "release-manifest.json").read_text())["releaseSet"] == _TARGET
    # 恢复点是迁移之前做的：里面还是 v1。
    archive = driven.root / "releases" / _TARGET / "recovery/v4-pre-switch.db.gz"
    restored = tmp_path / "recovery-check.db"
    with gzip.open(archive, "rb") as packed, restored.open("wb") as raw:
        shutil.copyfileobj(packed, raw)
    assert _db_facts(restored)["version"] == 1 and not _KX_TABLES & _db_facts(restored)["tables"]


def test_upgrade_whose_readiness_check_fails_rolls_back_to_v1_and_keeps_the_data(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2, health_after={"mcp": False})
    with pytest.raises(RuntimeError, match=f"已回滚到 {_PREVIOUS}"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    # 回滚：停服 → 还原文件 → 库版本回 1 → 恢复单元，旧版本就绪检查（要求 db 为 ready）才过得去。
    assert _flow(driven.calls) == ["mask", "stop", "stop", "write_runtime", "runuser", "unmask", "start",
                                   "mask", "stop", "stop", "write_runtime", "db-set:1", "unmask", "start"]
    facts = _db_facts(driven.db)
    assert facts["version"] == 1 and _KX_TABLES <= facts["tables"]  # 版本回去，表不删
    assert _documents(driven.db) == [("doc-1", "tushare", "n-1")]
    release.verify_runtime(driven.root, driven.old)
    assert json.loads((driven.root / "release-manifest.json").read_text())["releaseSet"] == _PREVIOUS
    state = _switch_state(driven.root)
    assert state["rollback"] == {"status": "rolled_back", "release": _PREVIOUS}
    assert state["schemaBefore"] == 1 and state["schemaAfter"] == 2
    assert state["schemaRollback"] == {"status": "restored", "from": 2, "version": 1}
    assert state["unitsAfter"] == state["unitsBefore"]


def test_failed_migration_command_rolls_back_and_the_database_stays_v1(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2, migrate=_command_failure())
    with pytest.raises(RuntimeError, match="库升级命令失败") as caught:  # 换包阶段失败：回滚后原样抛出原因
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "boom" in str(caught.value) and "已回滚" not in str(caught.value)
    assert _flow(driven.calls) == ["mask", "stop", "stop", "write_runtime", "runuser", "write_runtime", "unmask", "start"]
    facts = _db_facts(driven.db)
    assert facts["version"] == 1 and not _KX_TABLES & facts["tables"]
    assert _documents(driven.db) == [("doc-1", "tushare", "n-1")]
    release.verify_runtime(driven.root, driven.old)
    state = _switch_state(driven.root)
    assert state["rollback"]["status"] == "rolled_back" and "schemaAfter" not in state
    assert state["schemaRollback"] == {"status": "unchanged", "version": 1}  # 回滚前核对过：仍是 schemaBefore
    assert state["switched"] is False and state["unitsAfter"] == state["unitsBefore"]


def test_migration_that_took_effect_before_reporting_failure_is_put_back_to_v1(tmp_path, monkeypatch):
    def migrate(args):
        _real_migration(args)
        raise subprocess.CalledProcessError(1, list(args), stderr="timed out after taking effect")

    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2, migrate=migrate)
    with pytest.raises(RuntimeError, match="库升级命令失败"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert _db_facts(driven.db)["version"] == 1
    assert _switch_state(driven.root)["schemaRollback"] == {"status": "restored", "from": 2, "version": 1}
    assert _flow(driven.calls)[-4:] == ["write_runtime", "db-set:1", "unmask", "start"]


def test_migration_that_leaves_the_version_unchanged_is_rejected_and_rolled_back(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2, migrate=lambda _args: None)
    with pytest.raises(RuntimeError, match="user_version 是 1，应为 2"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert _db_facts(driven.db)["version"] == 1
    release.verify_runtime(driven.root, driven.old)


def test_wheel_failure_before_an_upgrade_never_touches_the_database(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2, install=_wheel_fails_once())
    with pytest.raises(RuntimeError, match="new wheel failed"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "runuser" not in _flow(driven.calls) and not [s for s in _flow(driven.calls) if s.startswith("db-set")]
    assert _db_facts(driven.db) == {"version": 1, "tables": _db_facts(driven.db)["tables"]}
    assert not _KX_TABLES & _db_facts(driven.db)["tables"]
    state = _switch_state(driven.root)
    assert state["rollback"]["status"] == "rolled_back" and "schemaRollback" not in state


def test_downgrade_rewrites_only_the_version_and_keeps_every_table(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=2, target_schema=None, previous_schema=2)
    seen = {}
    original = release.set_schema_version

    def set_schema_version(version):
        seen["active"] = set(driven.active)  # 降级时服务必须已经停了
        seen["masked"] = driven.masking.on
        seen["files"] = (driven.root / "neckline/api/app.py").read_bytes()  # 文件还没换
        return original(version)

    monkeypatch.setattr(release, "set_schema_version", set_schema_version)
    result = release.switch(release=_TARGET, previous=_PREVIOUS)
    assert result["schema"] == {"before": 2, "target": 1, "action": "downgrade"}
    assert seen == {"active": set(), "masked": True, "files": b"b112"}
    facts = _db_facts(driven.db)
    assert facts["version"] == 1 and _KX_TABLES <= facts["tables"]
    assert _documents(driven.db) == [("doc-1", "tushare", "n-1")]
    assert "runuser" not in _flow(driven.calls)
    assert _flow(driven.calls) == ["mask", "stop", "stop", "db-set:1", "write_runtime", "unmask", "start"]
    state = _switch_state(driven.root)
    assert (state["schemaBefore"], state["schemaTarget"], state["schemaAction"], state["schemaAfter"]) == (
        2, 1, "downgrade", 1)
    assert (driven.root / "neckline/api/app.py").read_bytes() == b"b113"


def test_failed_downgrade_swap_puts_the_original_version_back_before_the_old_api_starts(tmp_path, monkeypatch):
    calls = {"n": 0}

    def install(_wheel):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("new wheel failed")

    driven = _drive_schema(tmp_path, monkeypatch, db_version=2, target_schema=None, previous_schema=2, install=install)
    with pytest.raises(RuntimeError, match="new wheel failed"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert _flow(driven.calls) == ["mask", "stop", "stop", "db-set:1", "write_runtime", "write_runtime", "db-set:2",
                                   "unmask", "start"]
    facts = _db_facts(driven.db)
    assert facts["version"] == 2 and _KX_TABLES <= facts["tables"]
    release.verify_runtime(driven.root, driven.old)
    assert _switch_state(driven.root)["schemaRollback"] == {"status": "restored", "from": 1, "version": 2}


def test_downgrade_followed_by_a_failed_readiness_check_restores_the_original_version(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=2, target_schema=None, previous_schema=2,
                           health_after={"mcp": False})
    with pytest.raises(RuntimeError, match=f"已回滚到 {_PREVIOUS}"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert _db_facts(driven.db)["version"] == 2
    assert _flow(driven.calls)[-4:] == ["write_runtime", "db-set:2", "unmask", "start"]
    assert _switch_state(driven.root)["schemaRollback"] == {"status": "restored", "from": 1, "version": 2}
    release.verify_runtime(driven.root, driven.old)


def test_downgrade_outside_the_allow_list_aborts_before_the_mask(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=3, target_schema=None, previous_schema=3, real_db=False)
    for check_only in (False, True):
        _allow_root_check_once(monkeypatch)
        with pytest.raises(RuntimeError, match="不支持库结构 3→1 的降级"):
            release.switch(release=_TARGET, previous=_PREVIOUS, check_only=check_only)
    assert _flow(driven.calls) == [] and not {"mask", "unmask", "stop", "start"} & set(_actions(driven.calls))
    assert not (driven.root / "releases" / _TARGET / "switch-state.json").exists()
    assert not (driven.root / "releases" / _TARGET / "recovery").exists()
    assert (driven.root / "neckline/api/app.py").read_bytes() == b"b112"
    with closing(sqlite3.connect(driven.db)) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 3
        assert con.execute("SELECT count(*) FROM reports").fetchone()[0] == 1


def test_upgrade_from_a_database_that_is_not_a_versioned_v4_database_aborts_before_the_mask(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=0, target_schema=2, real_db=False)
    with pytest.raises(RuntimeError, match="不是可升级的 V4 晨报库"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert not {"mask", "stop", "start"} & set(_actions(driven.calls)) and _flow(driven.calls) == []


def test_upgrade_without_runuser_aborts_before_the_mask(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2)
    monkeypatch.setattr(release.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="runuser"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert _flow(driven.calls) == [] and _db_facts(driven.db)["version"] == 1


def test_rollback_that_cannot_restore_the_version_keeps_the_mask_and_records_the_scene(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2, health_after={"mcp": False})
    original = release.set_schema_version

    def flaky(version):
        if version == 1:
            driven.calls.append(("db-set", version))
            raise OSError("disk I/O error")
        return original(version)

    monkeypatch.setattr(release, "set_schema_version", flaky)
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理") as caught:
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert "已回滚" not in str(caught.value) and "disk I/O error" in str(caught.value.__cause__)
    # 恢复版本号失败后不能取消 mask、不能拉起任何服务。
    assert _flow(driven.calls) == ["mask", "stop", "stop", "write_runtime", "runuser", "unmask", "start",
                                   "mask", "stop", "stop", "write_runtime", "db-set:1"]
    assert driven.masking.on is True and release._mask_state() is True
    assert "neckline.service" not in driven.active
    state = _switch_state(driven.root)
    assert state["rollback"]["status"] == "failed" and state["rollback"]["failedStep"] == "恢复库结构版本"
    assert state["rollback"]["masked"] is True and state["rollback"]["schemaNow"] == 2
    assert state["rollback"]["fileState"] == "old"
    assert state["schemaBefore"] == 1 and state["schemaRollback"]["status"] == "failed"
    assert "disk I/O error" in state["schemaRollback"]["error"]
    facts = _db_facts(driven.db)  # 没丢任何东西：版本留在 2，表和数据都在
    assert facts["version"] == 2 and _KX_TABLES <= facts["tables"]
    assert _documents(driven.db) == [("doc-1", "tushare", "n-1")]


def test_restore_refuses_a_version_it_cannot_explain(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=3, target_schema=None, previous_schema=3, real_db=False)
    upgraded = release.SchemaPlan(1, 2, "upgrade")  # 升级过：现值 3 不在白名单里，不能凭空降回 1
    with pytest.raises(RuntimeError, match="无法安全恢复到 1"):
        release.restore_schema(upgraded)
    downgraded = release.SchemaPlan(2, 1, "downgrade")  # 降级过：现值必须正是降级目标
    with pytest.raises(RuntimeError, match="不是降级目标 1"):
        release.restore_schema(downgraded)
    assert _db_facts(driven.db)["version"] == 3 and not [s for s in _flow(driven.calls) if s.startswith("db-set")]


def test_unmask_failure_after_an_upgrade_still_restores_the_version_and_keeps_the_mask(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2)
    real_run = release.run

    def run_with_failing_unmask(*args, check=True, cwd=None):
        if args[0] == "systemctl" and args[1] == "unmask":
            driven.calls.append(args)
            raise RuntimeError("unmask failed")
        return real_run(*args, check=check, cwd=cwd)

    monkeypatch.setattr(release, "run", run_with_failing_unmask)
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    # unmask 失败后先重新 mask 并停服，才还原文件和库版本。
    assert _flow(driven.calls) == ["mask", "stop", "stop", "write_runtime", "runuser", "unmask", "mask", "stop", "stop",
                                   "write_runtime", "db-set:1"]
    assert _db_facts(driven.db)["version"] == 1 and "start" not in _actions(driven.calls)
    state = _switch_state(driven.root)
    assert state["rollback"]["failedStep"] == "取消 mask" and state["rollback"]["masked"] is True
    assert state["rollback"]["fileState"] == "old" and state["rollback"]["schemaNow"] == 1
    assert state["schemaRollback"] == {"status": "restored", "from": 2, "version": 1}
    release.verify_runtime(driven.root, driven.old)


def test_upgrade_that_changes_the_database_ownership_is_rolled_back(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2)
    original = release.assert_ownership
    state = {"raised": False}

    def assert_ownership():
        if any(args[0] == "runuser" for args in driven.calls) and not state["raised"]:
            state["raised"] = True
            raise RuntimeError("V4 晨报库属主或权限异常")
        return original()

    monkeypatch.setattr(release, "assert_ownership", assert_ownership)
    with pytest.raises(RuntimeError, match="V4 晨报库属主或权限异常"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert state["raised"] and _db_facts(driven.db)["version"] == 1
    assert _switch_state(driven.root)["schemaRollback"] == {"status": "restored", "from": 2, "version": 1}
    release.verify_runtime(driven.root, driven.old)


# ------------------------------------------------------------------ root 侧开库：显式 close 与 -wal/-shm 属主

class _ClosingSpy:
    """生产库连接的代理：记录 close，并在 close 时执行 on_close（模拟 root 的连接在旁边留下伴随文件）。"""

    def __init__(self, real, events, on_close=None):
        self._real, self._events, self._on_close = real, events, on_close
        self.closed = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        self.closed = True
        self._events.append("close")
        self._real.close()
        if self._on_close:
            self._on_close()


def _watch_production_connections(monkeypatch, db: Path, *, on_close=None) -> SimpleNamespace:
    """只代理对生产库（路径含 db）的连接；events 里按顺序有 close 和 fix（fix_sidecars 被调用）。"""
    real_connect, real_fix = _REAL_CONNECT, release.fix_sidecars
    watch = SimpleNamespace(spies=[], events=[])

    def connect(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        if str(db) in str(database):
            spy = _ClosingSpy(connection, watch.events, on_close)
            watch.spies.append(spy)
            return spy
        return connection

    def fix(path):
        watch.events.append("fix")
        return real_fix(path)

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(release, "fix_sidecars", fix)
    return watch


def _root_leaves_sidecars(db: Path, armed=lambda: True):
    """模拟 root 的连接关闭后留下 root 属主的 -wal/-shm（文件真实存在，属主按记录报告）。"""
    def leave():
        if not armed():
            return
        for suffix in release.SIDECAR_SUFFIXES:
            (db.parent / f"{db.name}{suffix}").touch()
            _SIDECARS.owner[f"{db.name}{suffix}"] = (0, 0, 0o600)

    return leave


def test_every_root_side_connection_to_the_production_database_is_closed_before_the_sidecars_are_checked(
        tmp_path, monkeypatch):
    root, _old, _legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True, real_db=True)
    db = root / "data/v4.db"
    watch = _watch_production_connections(monkeypatch, db)
    assert release.read_schema_version() == 1
    release.set_schema_version(1)
    release.snapshot(root / "releases" / _TARGET)
    # 读、写、恢复点各一条生产库连接，都是先显式 close、再核对伴随文件。
    assert watch.events == ["close", "fix"] * 3 and len(watch.spies) == 3
    assert all(spy.closed for spy in watch.spies)


def test_the_connection_is_closed_and_the_sidecars_are_checked_even_when_the_body_fails(tmp_path, monkeypatch):
    root, _old, _legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True, real_db=True)
    db = root / "data/v4.db"
    with sqlite3.connect(db) as con:
        con.execute("PRAGMA application_id=123")
    watch = _watch_production_connections(monkeypatch, db)
    with pytest.raises(RuntimeError, match="application_id"):
        release.read_schema_version()
    with pytest.raises(RuntimeError, match="application_id"):
        release.set_schema_version(1)
    assert watch.events == ["close", "fix"] * 2 and all(spy.closed for spy in watch.spies)


def test_root_owned_sidecars_are_handed_back_to_neckline_and_recorded(tmp_path, monkeypatch):
    root, _old, _legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True, real_db=True)
    db = root / "data/v4.db"
    (root / "data/v4.db-wal").touch()
    (root / "data/v4.db-shm").touch()
    _SIDECARS.owner.update({"v4.db-wal": (0, 0, 0o644), "v4.db-shm": (0, 0, 0o600)})
    assert release.read_schema_version() == 1
    assert _SIDECARS.owner == {"v4.db-wal": (10, 20, 0o600), "v4.db-shm": (10, 20, 0o600)}
    assert release._SIDECAR_FIXES == [
        {"file": "v4.db-wal", "wasUid": 0, "wasGid": 0, "wasMode": "0o644"},
        {"file": "v4.db-shm", "wasUid": 0, "wasGid": 0, "wasMode": "0o600"}]
    release.assert_ownership()
    assert db.exists()


def test_sidecar_check_leaves_good_files_alone_and_refuses_what_it_cannot_explain(tmp_path, monkeypatch):
    root, _old, _legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True, real_db=True)
    wal = root / "data/v4.db-wal"
    wal.touch()
    release.fix_sidecars(release.V4_DB)  # 默认就是 neckline 0600：什么都不动
    assert release._SIDECAR_FIXES == []
    _SIDECARS.owner["v4.db-wal"] = (1234, 20, 0o600)  # 既不是 root 也不是 neckline
    with pytest.raises(RuntimeError, match="属主异常"):
        release.fix_sidecars(release.V4_DB)
    wal.unlink()
    wal.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(RuntimeError, match="符号链接"):
        release.fix_sidecars(release.V4_DB)
    wal.unlink()
    release.fix_sidecars(release.V4_DB)  # 文件不存在：没什么可核对


def test_ownership_check_covers_the_sidecar_files_when_they_exist(tmp_path, monkeypatch):
    root, _old, _legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True, real_db=True)
    release.assert_ownership()  # 没有伴随文件
    for name in ("v4.db-wal", "v4.db-shm"):
        (root / "data" / name).touch()
        release.assert_ownership()
        for bad in ((0, 0, 0o600), (10, 20, 0o644)):
            _SIDECARS.owner[name] = bad
            with pytest.raises(RuntimeError, match=f"伴随文件 {name}"):
                release.assert_ownership()
        _SIDECARS.owner.pop(name)
        release.assert_ownership()


def test_switch_records_and_reports_the_sidecars_it_handed_back(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=None)
    _watch_production_connections(monkeypatch, driven.db, on_close=_root_leaves_sidecars(driven.db))
    result = release.switch(release=_TARGET, previous=_PREVIOUS)
    assert result["switched"] is True and result["sidecarFixes"]
    state = _switch_state(driven.root)
    assert state["sidecarFixes"] == result["sidecarFixes"]
    assert {fix["file"] for fix in state["sidecarFixes"]} == {"v4.db-wal", "v4.db-shm"}
    assert all(fix["wasUid"] == 0 for fix in state["sidecarFixes"])
    assert all(owner == (10, 20, 0o600) for owner in _SIDECARS.owner.values())  # 最终都是 neckline


def test_check_only_hands_the_sidecars_back_and_reports_it(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "unit_state", lambda unit: {"active": "inactive", "enabled": "disabled"})
    root, _old, _legacy = _prepare_switch(tmp_path, monkeypatch, with_v4=True, real_db=True)
    _watch_production_connections(monkeypatch, root / "data/v4.db", on_close=_root_leaves_sidecars(root / "data/v4.db"))
    result = release.switch(release=_TARGET, previous=_PREVIOUS, check_only=True)
    assert result["schemaAction"] == "none" and {fix["file"] for fix in result["sidecarFixes"]} == {
        "v4.db-wal", "v4.db-shm"}
    assert all(owner == (10, 20, 0o600) for owner in _SIDECARS.owner.values())


def test_sidecars_that_cannot_be_handed_back_abort_before_the_mask(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=None)
    _watch_production_connections(monkeypatch, driven.db, on_close=_root_leaves_sidecars(driven.db))
    for check_only in (False, True):
        _SIDECARS.owner.clear()  # 上一轮失败后留下的 root 属主记录清掉，各轮独立
        _SIDECARS.chown_failures = 99
        _allow_root_check_once(monkeypatch)
        with pytest.raises(RuntimeError, match="无法改回 neckline:neckline 0600"):
            release.switch(release=_TARGET, previous=_PREVIOUS, check_only=check_only)
    assert _flow(driven.calls) == [] and not {"mask", "stop", "start"} & set(_actions(driven.calls))
    assert not (driven.root / "releases" / _TARGET / "switch-state.json").exists()
    assert (driven.root / "neckline/api/app.py").read_bytes() == b"b112"


def _arm_after_the_recovery_point(driven, monkeypatch) -> dict:
    """恢复点做完（服务已停、已 mask）之后，root 的生产库连接才开始留下 root 属主的伴随文件。"""
    state = {"armed": False}
    original = release.snapshot

    def snapshot(directory):
        result = original(directory)
        state["armed"] = True
        return result

    monkeypatch.setattr(release, "snapshot", snapshot)
    _watch_production_connections(monkeypatch, driven.db,
                                  on_close=_root_leaves_sidecars(driven.db, lambda: state["armed"]))
    return state


def test_sidecar_failure_after_the_mask_rolls_back_and_puts_the_version_back(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=2, target_schema=None, previous_schema=2)
    _arm_after_the_recovery_point(driven, monkeypatch)
    _SIDECARS.chown_failures = 1  # 降级写完版本号后第一次改属主失败，回滚时可以
    with pytest.raises(RuntimeError, match="无法改回 neckline:neckline 0600"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert _db_facts(driven.db)["version"] == 2  # 降级已写入又被写回
    state = _switch_state(driven.root)
    assert state["rollback"]["status"] == "rolled_back"
    assert state["schemaRollback"] == {"status": "restored", "from": 1, "version": 2}
    assert state["sidecarFixes"] and all(owner == (10, 20, 0o600) for owner in _SIDECARS.owner.values())
    release.verify_runtime(driven.root, driven.old)
    assert _flow(driven.calls)[-4:] == ["write_runtime", "db-set:2", "unmask", "start"]


def test_sidecars_that_stay_wrong_during_the_rollback_keep_the_mask_for_manual_recovery(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=2, target_schema=None, previous_schema=2)
    _arm_after_the_recovery_point(driven, monkeypatch)
    _SIDECARS.chown_failures = 99
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    state = _switch_state(driven.root)
    assert state["rollback"]["status"] == "failed" and state["rollback"]["failedStep"] == "恢复库结构版本"
    assert state["rollback"]["masked"] is True and driven.masking.on is True
    assert str(state["rollback"]["schemaNow"]).startswith("unknown:")  # 连读版本号都过不了伴随文件这一关
    assert "start" not in _actions(driven.calls)
    assert _db_facts(driven.db)["version"] == 1  # 版本号停在降级后的值，表和数据都在
    assert _KX_TABLES <= _db_facts(driven.db)["tables"]


# ------------------------------------------------------------------ unmask 已生效后的恢复

def test_unmask_that_took_effect_then_a_failed_version_restore_ends_masked(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2)
    driven.masking.fail_after.add("unmask")  # unmask 生效后才报错
    original = release.set_schema_version

    def flaky(version):
        if version == 1:
            driven.calls.append(("db-set", version))
            raise OSError("disk I/O error")
        return original(version)

    monkeypatch.setattr(release, "set_schema_version", flaky)
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    # unmask 生效 → 先重新 mask 并停服 → 还原文件 → 恢复版本失败；之后没有任何 unmask / start。
    assert _flow(driven.calls) == ["mask", "stop", "stop", "write_runtime", "runuser", "unmask", "mask", "stop", "stop",
                                   "write_runtime", "db-set:1"]
    assert driven.masking.on is True and release._mask_state() is True and not driven.active
    state = _switch_state(driven.root)
    assert state["rollback"]["failedStep"] == "恢复库结构版本" and state["rollback"]["masked"] is True
    assert state["rollback"]["schemaNow"] == 2 and state["rollback"]["fileState"] == "old"
    assert "CalledProcessError" in state["failure"] and "unmask" in state["failure"]  # 记下的是最初的 unmask 故障


def test_unmask_that_took_effect_then_a_failed_remask_leaves_files_and_database_alone(tmp_path, monkeypatch):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2)
    driven.masking.fail_after.add("unmask")
    real_run = release.run
    masks = {"n": 0}

    def run_with_failing_remask(*args, check=True, cwd=None):
        if args[0] == "systemctl" and args[1] == "mask":
            masks["n"] += 1
            if masks["n"] == 2:
                driven.calls.append(args)
                raise RuntimeError("mask failed")
        return real_run(*args, check=check, cwd=cwd)

    monkeypatch.setattr(release, "run", run_with_failing_remask)
    with pytest.raises(RuntimeError, match="回滚失败，需人工处理"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    assert _flow(driven.calls) == ["mask", "stop", "stop", "write_runtime", "runuser", "unmask", "mask"]
    state = _switch_state(driven.root)
    assert state["rollback"]["failedStep"] == "重新 mask" and state["rollback"]["fileState"] == "new"
    assert state["rollback"]["schemaNow"] == 2 and _db_facts(driven.db)["version"] == 2  # 没动文件，也没动库
    assert state["rollback"]["masked"] is False and driven.masking.on is False  # 如实记录：此刻没有 mask


# ------------------------------------------------------------------ KX 就绪项

_KX = {"kxPool": True, "kxPicks": True}


@pytest.mark.parametrize("flag", ["kxPool", "kxPicks"])
def test_kx_flags_that_were_true_must_stay_true_after_the_switch(tmp_path, monkeypatch, flag):
    driven = _drive_schema(tmp_path, monkeypatch, db_version=2, target_schema=2, previous_schema=2,
                           previous_kx=True, target_kx=True, health_after={flag: False})
    with pytest.raises(RuntimeError, match=f"已回滚到 {_PREVIOUS}"):
        release.switch(release=_TARGET, previous=_PREVIOUS)
    release.verify_runtime(driven.root, driven.old)
    assert _switch_state(driven.root)["rollback"] == {"status": "rolled_back", "release": _PREVIOUS}


def test_b120_shaped_baseline_without_kx_flags_is_valid_and_not_required_afterwards(tmp_path, monkeypatch):
    # B120 的 health 没有 kxPool / kxPicks：基线照常通过，B121 上线后它们是 false 也不拦。
    driven = _drive_schema(tmp_path, monkeypatch, db_version=1, target_schema=2, previous_kx=False,
                           target_kx=True, health_after={"kxPool": False, "kxPicks": False})
    result = release.switch(release=_TARGET, previous=_PREVIOUS)
    assert result["switched"] is True and result["schema"]["action"] == "upgrade"
    assert _db_facts(driven.db)["version"] == 2


@pytest.mark.parametrize("fail_readiness", [False, True])
def test_switching_back_to_the_b120_shape_does_not_need_kx_flags(tmp_path, monkeypatch, fail_readiness):
    # 回退用 B121 目录里的新脚本：目标是 B120 形状（清单没有 schemaVersion，health 没有 KX 项），线上基线却带 KX 项。
    driven = _drive_schema(tmp_path, monkeypatch, db_version=2, target_schema=None, previous_schema=2,
                           previous_kx=True, target_kx=False, health_after={"mcp": False} if fail_readiness else None)
    if fail_readiness:
        with pytest.raises(RuntimeError, match=f"已回滚到 {_PREVIOUS}"):
            release.switch(release=_TARGET, previous=_PREVIOUS)
        # 回到 B121：它的就绪检查仍然要求 KX 项，而且过得去。
        assert _db_facts(driven.db)["version"] == 2
        assert _switch_state(driven.root)["schemaRollback"] == {"status": "restored", "from": 1, "version": 2}
        release.verify_runtime(driven.root, driven.old)
    else:
        result = release.switch(release=_TARGET, previous=_PREVIOUS)
        assert result["switched"] is True and result["schema"] == {"before": 2, "target": 1, "action": "downgrade"}
        assert _db_facts(driven.db)["version"] == 1 and _KX_TABLES <= _db_facts(driven.db)["tables"]
        assert "kxPool" not in json.loads((driven.root / "release-manifest.json").read_text())


def test_kx_flag_rules_in_the_baseline_and_the_readiness_comparison():
    assert release._baseline_problem(_health_body(_PREVIOUS), _PREVIOUS) is None  # B120 形状：没有 KX 项
    assert release._baseline_problem(_health_body(_PREVIOUS, kxPool=False, kxPicks=True), _PREVIOUS) is None
    assert release._baseline_problem(_health_body(_PREVIOUS, kxPool="yes"), _PREVIOUS) == "kxPool 不是布尔值"
    assert release._baseline_problem(_health_body(_PREVIOUS, kxPicks=1), _PREVIOUS) == "kxPicks 不是布尔值"
    before = _health_body(_PREVIOUS, **_KX)
    assert release._health_matches(_health_body(_TARGET, **_KX), _TARGET, "4.0.0", before)
    for flag in _KX:
        assert not release._health_matches(_health_body(_TARGET, **{**_KX, flag: False}), _TARGET, "4.0.0", before)
        assert not release._health_matches(_health_body(_TARGET), _TARGET, "4.0.0", before)  # 项消失也算丢
    assert release._health_matches(_health_body(_TARGET, kxPool=False, kxPicks=False), _TARGET, "4.0.0",
                                   _health_body(_PREVIOUS, kxPool=False, kxPicks=False))
    assert release._health_matches(_health_body(_TARGET), _TARGET, "4.0.0", _health_body(_PREVIOUS))
    assert release._expected_prior(before, {"schemaVersion": 2}) == before
    assert release._expected_prior(before, {"releaseSet": "b120"}) == _health_body(_PREVIOUS)
    assert release._expected_prior(None, {"releaseSet": "b120"}) is None
