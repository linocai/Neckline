"""OAuth 状态放在独立 sqlite。令牌只按哈希索引，不写进值里。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, key TEXT, value TEXT, expiry REAL, PRIMARY KEY(kind,key))")
        path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def key(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    def put(self, kind, raw_key, value, expiry=0) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM records WHERE expiry > 0 AND expiry < ?", (time.time(),))
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?)", (kind, self.key(raw_key), json.dumps(value), expiry))

    def get(self, kind, raw_key, consume=False):
        with self.connect() as db:
            if consume:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value, expiry FROM records WHERE kind=? AND key=?", (kind, self.key(raw_key))).fetchone()
            if consume:
                db.execute("DELETE FROM records WHERE kind=? AND key=?", (kind, self.key(raw_key)))
        if not row or (row[1] and row[1] < time.time()):
            return None
        return json.loads(row[0])

    def count(self, kind) -> int:
        with self.connect() as db:
            return db.execute("SELECT count(*) FROM records WHERE kind=? AND (expiry=0 OR expiry>=?)", (kind, time.time())).fetchone()[0]

    def count_provisional(self, kind) -> int:
        """未过期、且还不是永久（expiry 为 0）的记录。注册上限只数这一类。"""
        with self.connect() as db:
            return db.execute(
                "SELECT count(*) FROM records WHERE kind=? AND expiry>0 AND expiry>=?",
                (kind, time.time()),
            ).fetchone()[0]

    def revoke_family(self, family) -> None:
        with self.connect() as db:
            rows = db.execute("SELECT kind,key,value FROM records WHERE kind IN ('access','refresh')").fetchall()
            for kind, key, value in rows:
                if json.loads(value).get("family") == family:
                    db.execute("DELETE FROM records WHERE kind=? AND key=?", (kind, key))
