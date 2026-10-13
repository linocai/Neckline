"""The backend release switch: exact add/replace/remove, rollback, artifact checks."""
import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release.py"
spec = importlib.util.spec_from_file_location("neckline_release", _SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


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


def test_runtime_file_set_excludes_tests_and_bytecode():
    assert release.is_runtime_file("neckline/k10/store.py")
    assert release.is_runtime_file("scripts/release.py") and release.is_runtime_file("pyproject.toml")
    assert not release.is_runtime_file("tests/test_release_script.py")
    assert not release.is_runtime_file("neckline/__pycache__/store.cpython-311.pyc")
    assert not release.is_runtime_file(".env")


def test_switch_adds_replaces_removes_and_rolls_back_exactly(tmp_path):
    old_files = {"neckline/a.py": b"old a", "scripts/retired.py": b"retired"}
    new_files = {"neckline/a.py": b"new a", "neckline/k10/added.py": b"added"}
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
    assert not (root / "neckline/k10/added.py").exists()
    assert (root / "neckline/a.py").read_bytes() == b"old a"


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
    import sqlite3
    database = tmp_path / "neckline.db"
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA application_id={release.APPLICATION_ID}")
        for table in ("k10_tasks", "k10_external_attempts", "k10_model_response_receipts",
                      "k10_source_document_versions"):
            conn.execute(f"CREATE TABLE {table}(id INTEGER)")
        conn.execute("INSERT INTO k10_tasks VALUES (1)")
    monkeypatch.setattr(release, "DB", database)
    (tmp_path / "rel").mkdir()
    report = release.snapshot(tmp_path / "rel")
    assert report["roundTripVerified"] and report["counts"]["k10_tasks"] == 1
    assert sorted(p.name for p in (tmp_path / "rel/recovery").iterdir()) == ["pre-switch.db.gz"]


def test_switch_refuses_symlinked_runtime_target(tmp_path):
    root = tmp_path / "root"
    (root / "neckline").mkdir(parents=True)
    (root / "neckline/a.py").symlink_to(tmp_path / "elsewhere.py")
    with pytest.raises(RuntimeError, match="符号链接"):
        release.write_runtime(root, {"neckline/a.py": b"x"}, set())
