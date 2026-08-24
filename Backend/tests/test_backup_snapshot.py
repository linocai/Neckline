from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


backup = load_script("backup_snapshot")
restore_script = load_script("restore_backup")


def write_key_pair(root: Path) -> tuple[Path, Path]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path = root / "restore-private.pem"
    public_path = root / "backup-public.pem"
    private_path.write_bytes(private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    public_path.write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    return private_path, public_path


def make_config(tmp_path: Path) -> tuple[object, Path]:
    db_path = tmp_path / "production.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO proof(body) VALUES ('sealed')")
    facts = tmp_path / "fact_pack" / "version=fp-2" / "year=2026"
    facts.mkdir(parents=True)
    (facts / "part.parquet").write_bytes(b"frozen-facts")
    private, public = write_key_pair(tmp_path)
    return backup.BackupConfig(
        db_path=db_path,
        fact_pack_root=tmp_path / "fact_pack",
        recipient_public_key=public,
        s3_bucket="test-only",
        s3_prefix="neckline-test",
        restore_verify_command="/usr/bin/true {manifest_key}",
    ), private


def test_backup_encrypts_remote_verifies_and_restores_in_isolation(tmp_path: Path):
    config, private_key = make_config(tmp_path)
    store = backup.FilesystemObjectStore(tmp_path / "s3")
    manifest = backup.create_backup(config, store, tmp_path / "stage", now=datetime(2026, 8, 23, tzinfo=UTC))
    assert all("sealed" not in str(item) for item in manifest["artifacts"])
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    destination = tmp_path / "restored"
    restore_script.restore(manifest_path, private_key, destination, store)
    with sqlite3.connect(destination / "sqlite/neckline.db") as conn:
        assert conn.execute("SELECT body FROM proof").fetchone()[0] == "sealed"
    assert (destination / "fact-pack/version=fp-2/year=2026/part.parquet").read_bytes() == b"frozen-facts"


def test_backup_deduplicates_payload_but_keeps_recoverable_wrapped_key(tmp_path: Path):
    config, private_key = make_config(tmp_path)
    store = backup.FilesystemObjectStore(tmp_path / "s3")
    first = backup.create_backup(config, store, tmp_path / "first", now=datetime(2026, 8, 1, tzinfo=UTC))
    second = backup.create_backup(config, store, tmp_path / "second", now=datetime(2026, 8, 2, tzinfo=UTC))
    assert {item["objectKey"] for item in first["artifacts"]} == {item["objectKey"] for item in second["artifacts"]}
    manifest_path = tmp_path / "second.json"
    manifest_path.write_text(json.dumps(second), encoding="utf-8")
    restore_script.restore(manifest_path, private_key, tmp_path / "restored", store)


class CountingStore(backup.FilesystemObjectStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.downloads = 0

    def download(self, key: str, target: Path) -> None:
        self.downloads += 1
        super().download(key, target)


def _manifest_for_restore_validation(tmp_path: Path) -> tuple[dict, Path, CountingStore]:
    config, private_key = make_config(tmp_path)
    store = CountingStore(tmp_path / "s3")
    manifest = backup.create_backup(config, store, tmp_path / "stage", now=datetime(2026, 8, 23, tzinfo=UTC))
    store.downloads = 0
    return manifest, private_key, store


def _assert_invalid_manifest_has_no_restore_output(tmp_path: Path, manifest: dict, private_key: Path,
                                                    store: CountingStore) -> None:
    manifest_path = tmp_path / "bad-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    destination = tmp_path / "restored"
    with pytest.raises(RuntimeError):
        restore_script.restore(manifest_path, private_key, destination, store)
    assert not destination.exists()
    assert store.downloads == 0
    assert not list(tmp_path.glob(".neckline-restore-*"))


@pytest.mark.parametrize("bad_name", [
    "../escape", "/tmp/escape", "sqlite//neckline.db", "sqlite/./neckline.db",
    "sqlite/../neckline.db", r"sqlite\neckline.db", "sqlite/neckline\x00.db".replace("\\x00", "\x00"),
])
def test_restore_rejects_path_traversal_and_platform_bypasses_before_io(
    tmp_path: Path, bad_name: str,
):
    manifest, private_key, store = _manifest_for_restore_validation(tmp_path)
    manifest["artifacts"][0]["name"] = bad_name
    _assert_invalid_manifest_has_no_restore_output(tmp_path, manifest, private_key, store)


def test_restore_rejects_duplicate_target_and_non_whitelisted_artifact_before_io(tmp_path: Path):
    manifest, private_key, store = _manifest_for_restore_validation(tmp_path)
    manifest["artifacts"].append(dict(manifest["artifacts"][0]))
    _assert_invalid_manifest_has_no_restore_output(tmp_path, manifest, private_key, store)

    second = tmp_path / "second"
    second.mkdir()
    manifest, private_key, store = _manifest_for_restore_validation(second)
    manifest["artifacts"][1]["name"] = "fact-pack/notes.txt"
    _assert_invalid_manifest_has_no_restore_output(second, manifest, private_key, store)


@pytest.mark.parametrize("field, value", [
    ("plaintextSha256", "not-a-sha"),
    ("ciphertextSha256", "g" * 64),
    ("plaintextBytes", -1),
    ("ciphertextBytes", True),
    ("wrappedKey", "xyz"),
])
def test_restore_rejects_illegal_integrity_fields_before_io(
    tmp_path: Path, field: str, value: object,
):
    manifest, private_key, store = _manifest_for_restore_validation(tmp_path)
    manifest["artifacts"][0][field] = value
    _assert_invalid_manifest_has_no_restore_output(tmp_path, manifest, private_key, store)


def test_retention_keeps_30_daily_and_12_monthly_manifests(tmp_path: Path):
    config, _ = make_config(tmp_path)
    store = backup.FilesystemObjectStore(tmp_path / "s3")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for offset in range(32):
        backup.create_backup(config, store, tmp_path / f"stage-{offset}", now=start + timedelta(days=offset))
    for month in range(13):
        backup.create_backup(config, store, tmp_path / f"month-{month}",
                             now=datetime(2025 + (month + 1) // 12, (month % 12) + 1, 15, tzinfo=UTC))
    backup.apply_retention(config, store)
    assert len([key for key in store.list_keys("neckline-test/snapshots/daily/") if key.endswith("manifest.json")]) == 30
    assert len([key for key in store.list_keys("neckline-test/snapshots/monthly/") if key.endswith("manifest.json")]) == 12


def test_missing_backup_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch):
    for key in ("BACKUP_DB_PATH", "BACKUP_FACT_PACK_ROOT", "BACKUP_RECIPIENT_PUBLIC_KEY_PATH",
                "BACKUP_S3_BUCKET", "BACKUP_S3_PREFIX", "BACKUP_RESTORE_VERIFY_COMMAND",
                "BACKUP_ENABLE_RETENTION"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(backup.BackupConfigurationError):
        backup.BackupConfig.from_environment()
