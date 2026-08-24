#!/usr/bin/env python3
"""在用户 Mac 的隔离目录恢复一个 Neckline 加密备份（此机须持有私钥）。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from backup_snapshot import BackupConfigurationError, ObjectStore, S3ObjectStore, decrypt_file, sha256_file


_SHA256_LENGTH = 64


@dataclass(frozen=True)
class ValidatedArtifact:
    name: str
    target_relative: PurePosixPath
    object_key: str
    plaintext_sha256: str
    ciphertext_sha256: str
    plaintext_bytes: int
    ciphertext_bytes: int
    wrapped_key: str

    @property
    def download_name(self) -> str:
        """由完整 artifact 身份生成，不能让同 basename 覆盖临时密文。"""
        return hashlib.sha256(self.name.encode("utf-8")).hexdigest() + ".enc"


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != _SHA256_LENGTH:
        raise RuntimeError(f"manifest {field} 非法")
    try:
        int(value, 16)
    except ValueError as exc:
        raise RuntimeError(f"manifest {field} 非法") from exc
    return value.lower()


def _size(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"manifest {field} 非法")
    return value


def _relative_artifact_name(value: Any) -> PurePosixPath:
    """只接受当前备份器会生成的相对 POSIX artifact 路径。"""
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise RuntimeError("manifest artifact name 非法")
    if value.startswith("/"):
        raise RuntimeError("manifest artifact name 必须是相对路径")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise RuntimeError("manifest artifact name 含非法路径段")
    name = PurePosixPath(value)
    if name.as_posix() != value:
        raise RuntimeError("manifest artifact name 非规范")
    if value == "sqlite/neckline.db":
        return name
    if (len(parts) >= 2 and parts[0] == "fact-pack" and parts[-1].endswith(".parquet")):
        return name
    raise RuntimeError("manifest artifact 不在允许恢复范围")


def validate_manifest(manifest: Any, destination: Path) -> tuple[ValidatedArtifact, ...]:
    """在任何下载、解密或写盘前完成完整 manifest 验证。"""
    if not isinstance(manifest, dict) or manifest.get("format") != 1:
        raise RuntimeError("不支持的备份格式")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("manifest artifacts 非法")
    destination_root = destination.resolve()
    seen: set[str] = set()
    validated: list[ValidatedArtifact] = []
    for entry in artifacts:
        if not isinstance(entry, dict):
            raise RuntimeError("manifest artifact 非法")
        relative = _relative_artifact_name(entry.get("name"))
        name = relative.as_posix()
        if name in seen:
            raise RuntimeError("manifest artifact 目标重复")
        seen.add(name)
        target = (destination_root / Path(*relative.parts)).resolve()
        try:
            target.relative_to(destination_root)
        except ValueError as exc:
            raise RuntimeError("manifest artifact 越出恢复目录") from exc
        object_key = entry.get("objectKey")
        if not isinstance(object_key, str) or not object_key or "\x00" in object_key:
            raise RuntimeError("manifest objectKey 非法")
        wrapped_key = entry.get("wrappedKey")
        if (not isinstance(wrapped_key, str) or not wrapped_key or len(wrapped_key) % 2
                or any(ch not in "0123456789abcdefABCDEF" for ch in wrapped_key)):
            raise RuntimeError("manifest wrappedKey 非法")
        validated.append(ValidatedArtifact(
            name=name, target_relative=relative, object_key=object_key,
            plaintext_sha256=_sha256(entry.get("plaintextSha256"), "plaintextSha256"),
            ciphertext_sha256=_sha256(entry.get("ciphertextSha256"), "ciphertextSha256"),
            plaintext_bytes=_size(entry.get("plaintextBytes"), "plaintextBytes"),
            ciphertext_bytes=_size(entry.get("ciphertextBytes"), "ciphertextBytes"),
            wrapped_key=wrapped_key.lower(),
        ))
    if "sqlite/neckline.db" not in seen or not any(item.name.startswith("fact-pack/") for item in validated):
        raise RuntimeError("manifest 缺少必需恢复产物")
    return tuple(validated)


def restore(manifest_path: Path, private_key: Path, destination: Path, store: ObjectStore) -> None:
    if not private_key.is_file():
        raise BackupConfigurationError("恢复私钥不存在")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = validate_manifest(manifest, destination)
    if destination.exists():
        raise RuntimeError("恢复目标已存在")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 所有 I/O 都限定在同级临时目录；失败时 destination 不会留下半份恢复结果。
    with tempfile.TemporaryDirectory(prefix=".neckline-restore-", dir=destination.parent) as raw:
        staging = Path(raw) / "restored"
        staging.mkdir()
        for entry in artifacts:
            encrypted = staging / ".download" / entry.download_name
            store.download(entry.object_key, encrypted)
            if encrypted.stat().st_size != entry.ciphertext_bytes:
                raise RuntimeError(f"密文大小校验失败：{entry.name}")
            if sha256_file(encrypted) != entry.ciphertext_sha256:
                raise RuntimeError(f"密文校验失败：{entry.name}")
            plaintext = staging / Path(*entry.target_relative.parts)
            decrypt_file(encrypted, plaintext, private_key, entry.wrapped_key)
            if plaintext.stat().st_size != entry.plaintext_bytes:
                raise RuntimeError(f"明文大小校验失败：{entry.name}")
            if sha256_file(plaintext) != entry.plaintext_sha256:
                raise RuntimeError(f"明文校验失败：{entry.name}")
        with sqlite3.connect(f"file:{staging / 'sqlite/neckline.db'}?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("恢复 SQLite 完整性检查失败")
        staging.replace(destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--endpoint-url")
    args = parser.parse_args(argv)
    try:
        restore(args.manifest, args.private_key, args.destination,
                S3ObjectStore(args.bucket, args.endpoint_url))
        print("隔离恢复与完整性校验完成")
        return 0
    except (BackupConfigurationError, RuntimeError, OSError, ValueError) as exc:
        print(f"恢复失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
