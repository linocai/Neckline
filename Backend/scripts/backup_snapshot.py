#!/usr/bin/env python3
"""Fail-closed encrypted Neckline backup to any S3-compatible object store.

The server encrypts each artifact with a random AES-256 key and wraps that key
with the recipient's RSA public key.  It therefore never needs the restore
private key.  A separate restore verifier, explicitly configured by the
operator, is mandatory: an upload alone is not called a successful backup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


CHUNK_BYTES = 1024 * 1024
DAILY_RETENTION = 30
MONTHLY_RETENTION = 12


class BackupConfigurationError(RuntimeError):
    """The backup must stop rather than silently create a local substitute."""


class ObjectStore(Protocol):
    def exists(self, key: str) -> bool: ...
    def upload(self, source: Path, key: str) -> None: ...
    def download(self, key: str, target: Path) -> None: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def delete(self, key: str) -> None: ...


class S3ObjectStore:
    """Thin explicit AWS CLI adapter; the CLI and credentials are prerequisites."""

    def __init__(self, bucket: str, endpoint_url: str | None = None) -> None:
        if shutil.which("aws") is None:
            raise BackupConfigurationError("未安装 aws CLI，拒绝降级为本机备份")
        self.bucket = bucket
        self.endpoint_url = endpoint_url

    def _run(self, args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
        command = ["aws"]
        if self.endpoint_url:
            command += ["--endpoint-url", self.endpoint_url]
        command += args
        return subprocess.run(command, text=True, check=True, capture_output=capture)

    def _uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def exists(self, key: str) -> bool:
        try:
            self._run(["s3api", "head-object", "--bucket", self.bucket, "--key", key])
            return True
        except subprocess.CalledProcessError:
            return False

    def upload(self, source: Path, key: str) -> None:
        self._run(["s3", "cp", str(source), self._uri(key), "--only-show-errors"])

    def download(self, key: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        self._run(["s3", "cp", self._uri(key), str(target), "--only-show-errors"])

    def list_keys(self, prefix: str) -> list[str]:
        out = self._run([
            "s3api", "list-objects-v2", "--bucket", self.bucket, "--prefix", prefix,
            "--output", "json",
        ], capture=True)
        payload = json.loads(out.stdout)
        return sorted(item["Key"] for item in payload.get("Contents", []))

    def delete(self, key: str) -> None:
        self._run(["s3api", "delete-object", "--bucket", self.bucket, "--key", key])


class FilesystemObjectStore:
    """Test-only fake.  Runtime construction never selects this implementation."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def upload(self, source: Path, key: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def download(self, key: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), target)

    def list_keys(self, prefix: str) -> list[str]:
        root = self._path(prefix)
        if not root.exists():
            return []
        return sorted(str(path.relative_to(self.root)) for path in root.rglob("*") if path.is_file())

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


@dataclass(frozen=True)
class BackupConfig:
    db_path: Path
    fact_pack_root: Path
    recipient_public_key: Path
    s3_bucket: str
    s3_prefix: str
    restore_verify_command: str
    endpoint_url: str | None = None

    @classmethod
    def from_environment(cls) -> "BackupConfig":
        required = (
            "BACKUP_DB_PATH", "BACKUP_FACT_PACK_ROOT", "BACKUP_RECIPIENT_PUBLIC_KEY_PATH",
            "BACKUP_S3_BUCKET", "BACKUP_S3_PREFIX", "BACKUP_RESTORE_VERIFY_COMMAND",
        )
        missing = [name for name in required if not os.environ.get(name, "").strip()]
        if missing:
            raise BackupConfigurationError("备份配置缺失：" + ", ".join(missing))
        if os.environ.get("BACKUP_ENABLE_RETENTION") != "1":
            raise BackupConfigurationError("BACKUP_ENABLE_RETENTION 必须明确设为 1")
        return cls(
            db_path=Path(os.environ["BACKUP_DB_PATH"]),
            fact_pack_root=Path(os.environ["BACKUP_FACT_PACK_ROOT"]),
            recipient_public_key=Path(os.environ["BACKUP_RECIPIENT_PUBLIC_KEY_PATH"]),
            s3_bucket=os.environ["BACKUP_S3_BUCKET"],
            s3_prefix=os.environ["BACKUP_S3_PREFIX"].strip("/"),
            restore_verify_command=os.environ["BACKUP_RESTORE_VERIFY_COMMAND"],
            endpoint_url=os.environ.get("BACKUP_S3_ENDPOINT_URL") or None,
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_sqlite(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise BackupConfigurationError(f"SQLite 不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    with sqlite3.connect(f"file:{destination}?mode=ro", uri=True) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite 在线快照完整性检查失败")


def encrypt_file(source: Path, destination: Path, public_key_path: Path) -> dict[str, str | int]:
    if not public_key_path.is_file():
        raise BackupConfigurationError(f"加密公钥不存在：{public_key_path}")
    public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
    key = os.urandom(32)
    nonce = os.urandom(12)
    wrapped_key = public_key.encrypt(
        key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    if len(wrapped_key) > 65535:
        raise RuntimeError("封装密钥过长")
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    plaintext = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        output_file.write(nonce)
        output_file.write(len(wrapped_key).to_bytes(2, "big"))
        output_file.write(wrapped_key)
        for block in iter(lambda: input_file.read(CHUNK_BYTES), b""):
            plaintext.update(block)
            output_file.write(encryptor.update(block))
        output_file.write(encryptor.finalize())
        output_file.write(encryptor.tag)
    return {
        "plaintextSha256": plaintext.hexdigest(),
        "ciphertextSha256": sha256_file(destination),
        "wrappedKey": wrapped_key.hex(),
        "plaintextBytes": source.stat().st_size,
        "ciphertextBytes": destination.stat().st_size,
    }


def encrypted_header(path: Path) -> tuple[str, str]:
    with path.open("rb") as input_file:
        input_file.read(12)
        wrapped_length = int.from_bytes(input_file.read(2), "big")
        wrapped_key = input_file.read(wrapped_length)
    if not wrapped_key:
        raise RuntimeError("加密对象缺少封装密钥")
    return sha256_file(path), wrapped_key.hex()


def decrypt_file(source: Path, destination: Path, private_key_path: Path, wrapped_key_hex: str) -> None:
    private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    payload_size = source.stat().st_size
    if payload_size < 12 + 2 + 16:
        raise RuntimeError("加密对象长度不合法")
    with source.open("rb") as input_file:
        nonce = input_file.read(12)
        wrapped_length = int.from_bytes(input_file.read(2), "big")
        embedded_wrapped_key = input_file.read(wrapped_length)
        if embedded_wrapped_key.hex() != wrapped_key_hex:
            raise RuntimeError("manifest 与加密对象的封装密钥不一致")
        input_file.seek(payload_size - 16)
        tag = input_file.read(16)
        input_file.seek(14 + wrapped_length)
        decryptor = Cipher(
            algorithms.AES(private_key.decrypt(
                bytes.fromhex(wrapped_key_hex),
                padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
            )),
            modes.GCM(nonce, tag),
        ).decryptor()
        remaining = payload_size - (14 + wrapped_length + 16)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output_file:
            while remaining:
                block = input_file.read(min(CHUNK_BYTES, remaining))
                remaining -= len(block)
                output_file.write(decryptor.update(block))
            output_file.write(decryptor.finalize())


def _artifact_sources(config: BackupConfig, staging: Path) -> list[tuple[str, Path]]:
    sqlite_copy = staging / "neckline.db"
    snapshot_sqlite(config.db_path, sqlite_copy)
    if not config.fact_pack_root.is_dir():
        raise BackupConfigurationError(f"事实包目录不存在：{config.fact_pack_root}")
    artifacts = [("sqlite/neckline.db", sqlite_copy)]
    for source in sorted(config.fact_pack_root.rglob("*.parquet")):
        artifacts.append((f"fact-pack/{source.relative_to(config.fact_pack_root).as_posix()}", source))
    if len(artifacts) == 1:
        raise BackupConfigurationError("事实包目录为空，拒绝生成不完整备份")
    return artifacts


def _object_key(prefix: str, plaintext_sha: str) -> str:
    return f"{prefix}/objects/{plaintext_sha}.enc"


def create_backup(config: BackupConfig, store: ObjectStore, staging: Path, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    snapshot_id = now.strftime("%Y%m%dT%H%M%SZ")
    artifacts: list[dict[str, str | int]] = []
    for name, source in _artifact_sources(config, staging):
        encrypted = staging / "encrypted" / hashlib.sha256(name.encode()).hexdigest()
        crypto = encrypt_file(source, encrypted, config.recipient_public_key)
        object_key = _object_key(config.s3_prefix, crypto["plaintextSha256"])
        if not store.exists(object_key):
            store.upload(encrypted, object_key)
        downloaded = staging / "verify" / encrypted.name
        store.download(object_key, downloaded)
        remote_ciphertext_sha, remote_wrapped_key = encrypted_header(downloaded)
        if store.exists(object_key) and remote_ciphertext_sha != crypto["ciphertextSha256"]:
            crypto["ciphertextSha256"] = remote_ciphertext_sha
            crypto["wrappedKey"] = remote_wrapped_key
        if sha256_file(downloaded) != crypto["ciphertextSha256"]:
            raise RuntimeError(f"远端校验失败：{name}")
        artifacts.append({"name": name, "objectKey": object_key, **crypto})

    manifest = {
        "format": 1,
        "snapshotId": snapshot_id,
        "createdAt": now.isoformat(),
        "artifacts": artifacts,
        "configPresence": {key: bool(os.environ.get(key)) for key in ("API_TOKEN", "TUSHARE_TOKEN", "APNS_KEY_PATH")},
    }
    local_manifest = staging / "manifest.json"
    local_manifest.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    daily_key = f"{config.s3_prefix}/snapshots/daily/{snapshot_id}/manifest.json"
    month_key = f"{config.s3_prefix}/snapshots/monthly/{now.strftime('%Y-%m')}/manifest.json"
    store.upload(local_manifest, daily_key)
    store.upload(local_manifest, month_key)
    for key in (daily_key, month_key):
        verified = staging / "verify" / f"{hashlib.sha256(key.encode()).hexdigest()}.json"
        store.download(key, verified)
        if sha256_file(verified) != sha256_file(local_manifest):
            raise RuntimeError("远端 manifest 校验失败")
    manifest["dailyManifestKey"] = daily_key
    manifest["monthlyManifestKey"] = month_key
    return manifest


def apply_retention(config: BackupConfig, store: ObjectStore) -> None:
    daily_prefix = f"{config.s3_prefix}/snapshots/daily/"
    daily = [key for key in store.list_keys(daily_prefix) if key.endswith("/manifest.json")]
    for key in daily[:-DAILY_RETENTION]:
        store.delete(key)
    month_prefix = f"{config.s3_prefix}/snapshots/monthly/"
    monthly = [key for key in store.list_keys(month_prefix) if key.endswith("/manifest.json")]
    by_month: dict[str, list[str]] = {}
    for key in monthly:
        month = key.removeprefix(month_prefix).split("/", 1)[0]
        by_month.setdefault(month, []).append(key)
    for month in sorted(by_month)[:-MONTHLY_RETENTION]:
        for key in by_month[month]:
            store.delete(key)

    references: set[str] = set()
    retained = [key for key in store.list_keys(daily_prefix) + store.list_keys(month_prefix) if key.endswith("/manifest.json")]
    with tempfile.TemporaryDirectory(prefix="neckline-backup-gc-") as raw:
        scratch = Path(raw)
        for index, key in enumerate(retained):
            target = scratch / f"{index}.json"
            store.download(key, target)
            references.update(entry["objectKey"] for entry in json.loads(target.read_text())["artifacts"])
    for key in store.list_keys(f"{config.s3_prefix}/objects/"):
        if key not in references:
            store.delete(key)


def run_restore_verifier(command: str, manifest_key: str) -> None:
    if not command.strip():
        raise BackupConfigurationError("未配置隔离恢复验证命令")
    rendered = command.replace("{manifest_key}", manifest_key)
    subprocess.run(shlex.split(rendered), check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", default=None, help="临时目录根；运行后自动删除")
    args = parser.parse_args(argv)
    try:
        config = BackupConfig.from_environment()
        with tempfile.TemporaryDirectory(prefix="neckline-backup-", dir=args.staging_root) as raw:
            manifest = create_backup(config, S3ObjectStore(config.s3_bucket, config.endpoint_url), Path(raw))
            run_restore_verifier(config.restore_verify_command, manifest["dailyManifestKey"])
            apply_retention(config, S3ObjectStore(config.s3_bucket, config.endpoint_url))
        print("异机加密备份、远端校验与隔离恢复验证完成")
        return 0
    except (BackupConfigurationError, RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"备份失败（未降级）：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
