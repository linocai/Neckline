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
    manifest = {"releaseSet": name, "version": name.removeprefix("v").split("-b")[0], "commit": "c" * 40,
                "backendFiles": {path: _digest(data) for path, data in files.items()},
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
                           "recoveryPoint", "units", "readiness"}
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
    assert set(result) == {"release", "commit", "switched", "runtimeFiles", "removedFiles", "recoveryPoint", "units"}
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
    # unmask 已经生效，记录以 systemctl is-enabled 的查询为准，而不是凭「失败了所以还在 mask」推断。
    assert masking.on is False and state["rollback"]["masked"] is False
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
