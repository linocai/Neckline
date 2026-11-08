"""V4 晨报库：原件、采集记录、模型回执、晨报、推送设备与推送记录。

只读路径用 mode=ro 打开且不执行 DDL；建表只在显式的 init-db 与写命令里做。时间一律存 UTC ISO（到秒）。
库结构版本 2 在版本 1 的基础上只加三张 KX 表；版本 3 只给 kx_pool 加三列（题材、首次入选日、能否买入）。
版本 4 只加 filing_reads 与 evaluations 两张表。
版本 5 删掉 kx_pool、kx_picks、kx_daily_stats（KX 看板改为只读 KX，不再落库）。
版本 6 只加表：stock_pool、stock_pool_meta（当前股票池、流通市值和最近一次刷新），
sector_concepts、sector_members、sector_concepts_meta（同花顺概念和最近一次刷新），
market_daily（创业板日线）、sector_events（板块线事件）。
版本 7 删掉 market_daily（索引随表删除）。板块线不再用日线位置。
init_schema 把版本 1～6 的库在同一个事务里升到版本 7，升级失败整体回滚。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import unquote

from neckline.news.model import SourceDocument
from neckline.news.tushare import clip_covered_to_lookback

APPLICATION_ID = 1313552704  # 与 v3 库（1313552690）区分
SCHEMA_VERSION = 7
_UPGRADABLE_FROM = (1, 2, 3, 4, 5, 6)  # 这些版本的库可由 init_schema 原地升级到当前版本
# 版本 5 退役的三张表。索引随表一起删掉。
_RETIRED_KX_TABLES = ("kx_daily_stats", "kx_picks", "kx_pool")

DDL = """
CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  source_key TEXT NOT NULL,
  external_id TEXT NOT NULL,
  url TEXT,
  first_seen_at TEXT NOT NULL,
  UNIQUE(source_key, external_id)
);
CREATE TABLE IF NOT EXISTS document_versions (
  document_id TEXT NOT NULL REFERENCES documents(document_id),
  revision INTEGER NOT NULL CHECK(revision >= 1),
  content_sha256 TEXT NOT NULL,
  published_at TEXT,
  published_precision TEXT NOT NULL CHECK(published_precision IN ('exact','date','unknown')),
  fetched_at TEXT NOT NULL,
  title TEXT,
  original_text TEXT,
  excerpt TEXT,
  metadata_json TEXT NOT NULL,
  PRIMARY KEY(document_id, revision),
  UNIQUE(document_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_versions_published ON document_versions(published_at);
CREATE TABLE IF NOT EXISTS collection_runs (
  run_id TEXT PRIMARY KEY,
  slot_at TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK(status IN ('running','completed','partial','failed')),
  sources_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS model_receipts (
  request_sha256 TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK(state IN ('started','succeeded','failed','unknown')),
  report_id TEXT,
  started_at TEXT NOT NULL,
  settled_at TEXT,
  error_code TEXT,
  seconds REAL,
  response_json TEXT
);
CREATE TABLE IF NOT EXISTS reports (
  report_id TEXT PRIMARY KEY,
  trade_date TEXT NOT NULL UNIQUE,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('running','available','failed')),
  config_version TEXT NOT NULL,
  config_sha256 TEXT NOT NULL,
  started_at TEXT NOT NULL,
  available_at TEXT,
  failed_at TEXT,
  failure_reason TEXT,
  gap_summary TEXT,
  stats_json TEXT NOT NULL DEFAULT '{}',
  content_json TEXT
);
CREATE TABLE IF NOT EXISTS devices (
  token TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_log (
  report_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('ready','problem')),
  device_token TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('sent','failed')),
  detail TEXT,
  sent_at TEXT NOT NULL,
  PRIMARY KEY(report_id, kind, device_token)
);
"""

# 版本 4 只加两张表：公告 PDF 读数，以及留给后续评估层的 evaluations。不改已有表。
V4_DDL = """
CREATE TABLE IF NOT EXISTS filing_reads (
  document_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  url TEXT NOT NULL,
  kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('ok','error')),
  error TEXT,
  figures_json TEXT,
  summary TEXT,
  read_at TEXT NOT NULL,
  PRIMARY KEY(document_id, revision)
);
CREATE TABLE IF NOT EXISTS evaluations (
  report_id TEXT NOT NULL,
  company TEXT NOT NULL CHECK(length(company) = 6),
  contract TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('prepared','succeeded','invalid','gap')),
  verdict TEXT CHECK(verdict IN ('推','不推')),
  package_json TEXT NOT NULL,
  output_json TEXT,
  error TEXT,
  usage_json TEXT NOT NULL DEFAULT '{}',
  seconds REAL,
  prepared_at TEXT NOT NULL,
  settled_at TEXT,
  PRIMARY KEY(report_id, company)
);
"""

# 版本 6 只加表：股票池，同花顺概念和成分（只存创业板代码），以及日线。
# 概念刷新在一个事务里整表替换。refreshed_at 只在成功时改；失败只写 error，沿用上一份。
V6_DDL = """
CREATE TABLE IF NOT EXISTS stock_pool (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  fullname TEXT,
  list_date TEXT,
  aliases_json TEXT NOT NULL,
  summary TEXT,
  source TEXT NOT NULL,
  refreshed_at TEXT NOT NULL,
  circ_mv REAL,
  total_mv REAL,
  mv_date TEXT
);
CREATE TABLE IF NOT EXISTS stock_pool_meta (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  refreshed_at TEXT NOT NULL,
  count INTEGER NOT NULL CHECK(count >= 0),
  content_sha256 TEXT NOT NULL,
  source TEXT NOT NULL,
  excluded_json TEXT NOT NULL,
  mv_date TEXT,
  exclude_top_share REAL
);
CREATE TABLE IF NOT EXISTS sector_concepts (
  ts_code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  member_count INTEGER NOT NULL CHECK(member_count >= 0),
  refreshed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sector_members (
  ts_code TEXT NOT NULL,
  code TEXT NOT NULL,
  PRIMARY KEY (ts_code, code)
);
CREATE TABLE IF NOT EXISTS sector_concepts_meta (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  refreshed_at TEXT,
  count INTEGER NOT NULL CHECK(count >= 0),
  content_sha256 TEXT NOT NULL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS sector_events (
  report_id TEXT NOT NULL,
  event_id TEXT NOT NULL CHECK(length(event_id) = 16),
  title TEXT NOT NULL,
  sector TEXT,
  reason TEXT,
  repeat INTEGER NOT NULL CHECK(repeat IN (0, 1)),
  state TEXT NOT NULL CHECK(state IN ('prepared','succeeded','invalid','gap','skipped')),
  verdict TEXT CHECK(verdict IS NULL OR verdict IN ('推','不推')),
  package_json TEXT NOT NULL,
  output_json TEXT,
  error TEXT,
  usage_json TEXT NOT NULL DEFAULT '{}',
  seconds REAL,
  prepared_at TEXT NOT NULL,
  settled_at TEXT,
  PRIMARY KEY (report_id, event_id)
);
"""

# 版本 6 的日线表。init_schema 不再执行它。降到版本 6 时由 release.py 用同一份语句重建空表。
MARKET_DAILY_V6_DDL = """
CREATE TABLE IF NOT EXISTS market_daily (
  code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  pre_close REAL NOT NULL,
  pct_chg REAL NOT NULL,
  amount REAL NOT NULL,
  limit_up INTEGER NOT NULL CHECK(limit_up IN (0, 1)),
  PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_market_daily_trade_date ON market_daily(trade_date);
"""


class NotReady(RuntimeError):
    """库不存在或不是 V4 晨报库。"""


def utc(ts: datetime) -> str:
    if ts.tzinfo is None:
        raise ValueError("时间必须带时区")
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _statements(script: str) -> list[str]:
    """把只含普通语句的 DDL 拆开（本文件的 DDL 里没有字符串或注释里的分号）。"""
    return [part.strip() for part in script.split(";") if part.strip()]


def init_schema(path: Path) -> None:
    """显式建表（init-db 与写命令调用）。

    新库一次建到当前版本，不再建 KX 的三张表，也不建 market_daily。版本 1～6 的库在同一个事务里补上缺的表、
    删掉 kx_pool / kx_picks / kx_daily_stats 和 market_daily，并把版本号升到当前版本。任何一步失败都整体回滚。
    已经是当前版本时什么都不改。只读连接不走这里。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o600)  # 生产库只给服务用户读写；发布脚本会核对这个权限
        os.chmod(path, 0o600)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        app_id = conn.execute("PRAGMA application_id").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if tables and (app_id != APPLICATION_ID or version not in (SCHEMA_VERSION, *_UPGRADABLE_FROM)):
            raise NotReady(f"{path} 不是 V4 晨报库（application_id={app_id}, user_version={version}）")
        conn.execute("PRAGMA journal_mode=WAL")
        if tables and version == SCHEMA_VERSION:
            return
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _statements(DDL) + _statements(V4_DDL) + _statements(V6_DDL):
                conn.execute(statement)
            for name in _RETIRED_KX_TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {name}")
            conn.execute("DROP TABLE IF EXISTS market_daily")
            conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _check(conn: sqlite3.Connection, path: Path) -> None:
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if (app_id, version) != (APPLICATION_ID, SCHEMA_VERSION):
        raise NotReady(f"{path} 不是当前版本的 V4 晨报库（application_id={app_id}, user_version={version}）")


@contextmanager
def read(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.exists():
        raise NotReady(f"{path} 不存在")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        _check(conn, path)
        yield conn
    finally:
        conn.close()


@contextmanager
def write(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.exists():
        raise NotReady(f"{path} 不存在，先运行 init-db")
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        _check(conn, path)
        with conn:
            yield conn
    finally:
        conn.close()


# ------------------------------------------------------------------ 原件

def document_id(source_key: str, external_id: str) -> str:
    return "doc_" + hashlib.sha256(f"{source_key}\x1f{external_id}".encode()).hexdigest()[:32]


def _content_hash(doc: SourceDocument) -> str:
    return hashlib.sha256(_dumps({
        "url": doc.url, "title": doc.title, "text": doc.original_text, "excerpt": doc.excerpt,
        "publishedAt": utc(doc.published_at) if doc.published_at else None,
        "precision": doc.published_precision,
    }).encode()).hexdigest()


def store_documents(conn: sqlite3.Connection, docs: Sequence[SourceDocument]) -> int:
    """追加原件版本；同一文档内容没变就不新增版本。返回新增版本数。"""
    added = 0
    for doc in docs:
        doc_id = document_id(doc.source_key, doc.external_id)
        conn.execute("INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?)",
                     (doc_id, doc.source_key, doc.external_id, doc.url, utc(doc.fetched_at)))
        digest = _content_hash(doc)
        if conn.execute("SELECT 1 FROM document_versions WHERE document_id=? AND content_sha256=?",
                        (doc_id, digest)).fetchone():
            continue
        revision = conn.execute("SELECT coalesce(max(revision),0)+1 FROM document_versions WHERE document_id=?",
                                (doc_id,)).fetchone()[0]
        conn.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?,?,?,?,?)", (
            doc_id, revision, digest, utc(doc.published_at) if doc.published_at else None, doc.published_precision,
            utc(doc.fetched_at), doc.title, doc.original_text, doc.excerpt, _dumps(dict(doc.metadata))))
        added += 1
    return added


_DOC_QUERY = """
SELECT v.document_id, v.revision, d.source_key, d.url, d.first_seen_at, v.published_at, v.fetched_at,
       v.title, v.original_text, v.excerpt, v.metadata_json
FROM document_versions v JOIN documents d USING(document_id)
WHERE v.published_precision='exact' AND v.published_at >= ? AND v.published_at {upper} ? AND v.fetched_at <= ?
  AND v.revision = (SELECT max(revision) FROM document_versions w
                    WHERE w.document_id = v.document_id AND w.fetched_at <= ?)
ORDER BY v.published_at, v.document_id
"""


def iter_documents(conn: sqlite3.Connection, *, published_from: datetime, published_to: datetime,
                   fetched_by: datetime, include_end: bool) -> Iterator[dict]:
    """按发布时间取原件（每个文档取 fetched_by 之前的最新版本），逐行给出，形状与离线导出一致。"""
    sql = _DOC_QUERY.format(upper="<=" if include_end else "<")
    cutoff = utc(fetched_by)
    for row in conn.execute(sql, (utc(published_from), utc(published_to), cutoff, cutoff)):
        meta = json.loads(row["metadata_json"])
        meta["title"] = row["title"]
        yield {"documentId": row["document_id"], "revision": row["revision"], "sourceKey": row["source_key"],
               "url": row["url"], "firstSeenAt": row["first_seen_at"], "publishedAt": row["published_at"],
               "fetchedAt": row["fetched_at"], "text": row["original_text"], "excerpt": row["excerpt"], "meta": meta}


_DOC_REF = re.compile(r"^(doc_[0-9a-f]{32})(?:@([1-9][0-9]{0,17}))?$")
_DOC_COLUMNS = """SELECT v.document_id, v.revision, d.source_key, d.url, d.first_seen_at, v.published_at,
       v.fetched_at, v.title, v.original_text, v.excerpt, v.metadata_json
FROM document_versions v JOIN documents d USING(document_id)"""


def parse_document_ref(ref: str) -> tuple[str, int | None] | None:
    """`doc_<32hex>` 取最新版本，`doc_<32hex>@<正整数>` 取精确版本。格式不对返回 None。

    `%40` 与 `@` 同一写法。版本号只接受 1–18 位数字；更长、前导零、0、负数、大写、长度不对都算找不到。
    """
    if not isinstance(ref, str) or not ref:
        return None
    matched = _DOC_REF.fullmatch(unquote(ref))
    if matched is None:
        return None
    raw = matched.group(2)
    if raw is None:
        return matched.group(1), None
    revision = int(raw)
    if revision > 2**63 - 1:
        return None
    return matched.group(1), revision


def _document_row(row: sqlite3.Row) -> dict:
    return {"documentId": row["document_id"], "revision": row["revision"], "sourceKey": row["source_key"],
            "url": row["url"], "title": row["title"], "text": row["original_text"], "excerpt": row["excerpt"],
            "publishedAt": row["published_at"], "fetchedAt": row["fetched_at"], "firstSeenAt": row["first_seen_at"],
            "meta": json.loads(row["metadata_json"])}


def get_document(conn: sqlite3.Connection, ref: str) -> dict | None:
    parsed = parse_document_ref(ref)
    if parsed is None:
        return None
    doc_id, revision = parsed
    if revision is None:
        row = conn.execute(_DOC_COLUMNS + " WHERE v.document_id=? ORDER BY v.revision DESC LIMIT 1",
                           (doc_id,)).fetchone()
    else:
        row = conn.execute(_DOC_COLUMNS + " WHERE v.document_id=? AND v.revision=?", (doc_id, revision)).fetchone()
    if row is None:
        return None
    return _document_row(row)


# ------------------------------------------------------------------ 采集记录

def begin_collection(conn: sqlite3.Connection, *, run_id: str, slot_at: datetime, started_at: datetime) -> None:
    conn.execute("INSERT INTO collection_runs VALUES (?,?,?,?,?,?)",
                 (run_id, utc(slot_at), utc(started_at), None, "running", "[]"))


def finish_collection(conn: sqlite3.Connection, *, run_id: str, status: str, sources: Sequence[Mapping],
                      finished_at: datetime) -> None:
    conn.execute("UPDATE collection_runs SET status=?, sources_json=?, finished_at=? WHERE run_id=?",
                 (status, _dumps(list(sources)), utc(finished_at), run_id))


def save_collection_sources(conn: sqlite3.Connection, *, run_id: str, sources: Sequence[Mapping]) -> None:
    """一个来源取完就把它的摘要写进这条仍为 running 的记录，终态等全部结束再改。"""
    conn.execute("UPDATE collection_runs SET sources_json=? WHERE run_id=? AND status='running'",
                 (_dumps(list(sources)), run_id))


def source_watermark(conn: sqlite3.Connection, source_key: str) -> datetime | None:
    """该来源最近一次完整覆盖到的时刻；下次采集从这里减去回看时长开始。

    来源取完就和它的文档在同一个事务里提交摘要，所以只要摘要标了 complete，
    无论所在采集记录是 running（后面的来源中断了）、failed、partial 还是 completed，都算数。
    """
    best = None
    for row in conn.execute("SELECT sources_json FROM collection_runs"):
        for source in json.loads(row["sources_json"]):
            if source.get("sourceKey") == source_key and source.get("complete"):
                end = datetime.fromisoformat(source["requestedEnd"])
                best = end if best is None or end > best else best
    return best


def source_attempt_start(conn: sqlite3.Connection, source_key: str) -> datetime | None:
    """最早一次真正取数的 requestedStart。没给起点的空记录不算，有完整覆盖时不用它。"""
    rows = conn.execute("SELECT sources_json FROM collection_runs ORDER BY started_at, rowid")
    for row in rows:
        for source in json.loads(row["sources_json"]):
            if source.get("sourceKey") != source_key or source.get("error") == "bootstrap_start_missing":
                continue
            raw = source.get("requestedStart")
            if isinstance(raw, str):
                return datetime.fromisoformat(raw)
    return None


def _covered_pair(pair: object) -> tuple[datetime, datetime] | None:
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        return None
    if not isinstance(pair[0], str) or not isinstance(pair[1], str):
        return None
    try:
        start, end = datetime.fromisoformat(pair[0]), datetime.fromisoformat(pair[1])
    except ValueError:
        return None
    if start.tzinfo is None or end.tzinfo is None:
        return None
    return start, end


def source_covered(conn: sqlite3.Connection, source_key: str, *,
                   lookback_seconds: int) -> list[tuple[datetime, datetime]]:
    """来源记录里的 covered 切片，中断后停在 running 的记录也算。

    每条记录只保留「requestedEnd − lookback_seconds」之前的部分。没有 covered 的旧记录不贡献区间。
    """
    found: list[tuple[datetime, datetime]] = []
    rows = conn.execute("SELECT sources_json FROM collection_runs ORDER BY started_at, rowid")
    for row in rows:
        for source in json.loads(row["sources_json"]):
            if source.get("sourceKey") != source_key:
                continue
            raw_end = source.get("requestedEnd")
            if not isinstance(raw_end, str):
                continue
            try:
                fetched_at = datetime.fromisoformat(raw_end)
            except ValueError:
                continue
            parsed = [item for pair in source.get("covered") or [] if (item := _covered_pair(pair)) is not None]
            found.extend(clip_covered_to_lookback(parsed, fetched_at=fetched_at, lookback_seconds=lookback_seconds))
    return found


def collections_since(conn: sqlite3.Connection, since: datetime) -> list[dict]:
    rows = conn.execute("SELECT * FROM collection_runs WHERE slot_at >= ? ORDER BY slot_at", (utc(since),))
    return [{**dict(r), "sources": json.loads(r["sources_json"])} for r in rows]


# ------------------------------------------------------------------ 模型回执

class DbReceipts:
    """DeepSeek 回执落在晨报库；每次读写各开一个连接，供多线程并发调用。"""

    def __init__(self, path: Path, report_id: str | None) -> None:
        self.path = path
        self.report_id = report_id

    def lookup(self, digest: str) -> Mapping[str, Any] | None:
        with write(self.path) as conn:
            row = conn.execute("SELECT state, response_json, seconds FROM model_receipts WHERE request_sha256=?",
                               (digest,)).fetchone()
        if row is None:
            return None
        return {"state": row["state"], "seconds": row["seconds"],
                "response": json.loads(row["response_json"]) if row["response_json"] else None}

    def begin(self, digest: str) -> None:
        with write(self.path) as conn:
            conn.execute("""INSERT INTO model_receipts(request_sha256, state, report_id, started_at)
                            VALUES (?, 'started', ?, ?)
                            ON CONFLICT(request_sha256) DO UPDATE SET state='started', started_at=excluded.started_at,
                              settled_at=NULL, error_code=NULL WHERE model_receipts.state='failed'""",
                         (digest, self.report_id, utc(datetime.now(timezone.utc))))

    def _settle(self, digest: str, state: str, *, code: str | None = None, response: Any = None,
                seconds: float | None = None) -> None:
        with write(self.path) as conn:
            conn.execute("""UPDATE model_receipts SET state=?, settled_at=?, error_code=?, seconds=?, response_json=?
                            WHERE request_sha256=? AND state='started'""",
                         (state, utc(datetime.now(timezone.utc)), code, seconds,
                          _dumps(response) if response is not None else None, digest))

    def succeed(self, digest: str, response: Mapping[str, Any], seconds: float) -> None:
        self._settle(digest, "succeeded", response=response, seconds=seconds)

    def fail(self, digest: str, code: str) -> None:
        self._settle(digest, "failed", code=code)

    def unknown(self, digest: str, code: str) -> None:
        self._settle(digest, "unknown", code=code)


# ------------------------------------------------------------------ 晨报

def _report(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    data["stats"] = json.loads(data.pop("stats_json") or "{}")
    content = data.pop("content_json")
    data["content"] = json.loads(content) if content else None
    return data


def get_report(conn: sqlite3.Connection, trade_date: str) -> dict | None:
    return _report(conn.execute("SELECT * FROM reports WHERE trade_date=?", (trade_date,)).fetchone())


def latest_report(conn: sqlite3.Connection) -> dict | None:
    return _report(conn.execute("SELECT * FROM reports WHERE status IN ('available','failed') "
                                "ORDER BY trade_date DESC LIMIT 1").fetchone())


def latest_available_report(conn: sqlite3.Connection) -> dict | None:
    """最新一份已经出完、能拿来对照个股的晨报。失败的那期不算。"""
    return _report(conn.execute(
        "SELECT * FROM reports WHERE status='available' AND content_json IS NOT NULL "
        "ORDER BY trade_date DESC LIMIT 1").fetchone())


def list_reports(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM reports ORDER BY trade_date DESC LIMIT ?", (limit,)).fetchall()
    return [_report(r) for r in rows]


def begin_report(conn: sqlite3.Connection, *, report_id: str, trade_date: str, window_start: datetime,
                 window_end: datetime, config_version: str, config_sha256: str, started_at: datetime) -> None:
    conn.execute("""INSERT INTO reports(report_id, trade_date, window_start, window_end, status, config_version,
                                        config_sha256, started_at)
                    VALUES (?,?,?,?,'running',?,?,?) ON CONFLICT(report_id) DO NOTHING""",
                 (report_id, trade_date, utc(window_start), utc(window_end), config_version, config_sha256,
                  utc(started_at)))


def complete_report(conn: sqlite3.Connection, *, report_id: str, content: Mapping, stats: Mapping,
                    gap_summary: str | None, available_at: datetime) -> int:
    """只把仍在 running 的报告标成 available。返回更新行数；0 表示状态已经被人改过。"""
    cursor = conn.execute("""UPDATE reports SET status='available', content_json=?, stats_json=?, gap_summary=?,
                             available_at=? WHERE report_id=? AND status='running'""",
                          (_dumps(content), _dumps(stats), gap_summary, utc(available_at), report_id))
    return cursor.rowcount


def fail_report(conn: sqlite3.Connection, *, report_id: str, trade_date: str, window_start: datetime,
                window_end: datetime, config_version: str, config_sha256: str, reason: str, at: datetime) -> None:
    begin_report(conn, report_id=report_id, trade_date=trade_date, window_start=window_start, window_end=window_end,
                 config_version=config_version, config_sha256=config_sha256, started_at=at)
    conn.execute("UPDATE reports SET status='failed', failure_reason=?, failed_at=? WHERE report_id=? AND status='running'",
                 (reason, utc(at), report_id))


# ------------------------------------------------------------------ 评估

def _evaluation(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["package"] = json.loads(data.pop("package_json"))
    raw_output = data.pop("output_json")
    if raw_output is None:
        data["output"] = None
    else:
        try:
            data["output"] = json.loads(raw_output)
        except ValueError:
            data["output"] = raw_output
    data["usage"] = json.loads(data.pop("usage_json") or "{}")
    return data


def insert_prepared(conn: sqlite3.Connection, *, report_id: str, company: str, contract: str,
                    package: Mapping, prepared_at: datetime) -> bool:
    """写入 prepared。同一期同一家已经有行时不覆盖，续跑沿用原来的数据包。"""
    if len(company) != 6:
        raise ValueError("公司代码须为六位")
    cursor = conn.execute(
        """INSERT INTO evaluations(report_id, company, contract, state, package_json, prepared_at)
           VALUES (?,?,?,'prepared',?,?)
           ON CONFLICT(report_id, company) DO NOTHING""",
        (report_id, company, contract, _dumps(package), utc(prepared_at)))
    return cursor.rowcount == 1


def get_evaluation(conn: sqlite3.Connection, report_id: str, company: str) -> dict | None:
    row = conn.execute("SELECT * FROM evaluations WHERE report_id=? AND company=?", (report_id, company)).fetchone()
    return None if row is None else _evaluation(row)


def list_evaluations(conn: sqlite3.Connection, report_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM evaluations WHERE report_id=? ORDER BY company", (report_id,)).fetchall()
    return [_evaluation(row) for row in rows]


def settle_evaluation(conn: sqlite3.Connection, *, report_id: str, company: str, state: str, settled_at: datetime,
                      verdict: str | None = None, output: Any = None, error: str | None = None,
                      usage: Mapping | None = None, seconds: float | None = None) -> int:
    """只允许从 prepared 收成 succeeded、invalid 或 gap。返回更新行数。"""
    if state not in ("succeeded", "invalid", "gap"):
        raise ValueError("评估状态只能收成 succeeded、invalid 或 gap")
    if state == "succeeded" and verdict not in ("推", "不推"):
        raise ValueError("verdict 无效")
    if state != "succeeded":
        verdict = None
    if isinstance(output, str):
        output_json = output
    elif output is None:
        output_json = None
    else:
        output_json = _dumps(output)
    cursor = conn.execute(
        """UPDATE evaluations SET state=?, verdict=?, output_json=?, error=?, usage_json=?, seconds=?, settled_at=?
           WHERE report_id=? AND company=? AND state='prepared'""",
        (state, verdict, output_json, error, _dumps(dict(usage or {})), seconds, utc(settled_at), report_id, company))
    return cursor.rowcount


# ------------------------------------------------------------------ 推送

def upsert_device(conn: sqlite3.Connection, *, token: str, platform: str, at: datetime) -> None:
    conn.execute("""INSERT INTO devices VALUES (?,?,?,?)
                    ON CONFLICT(token) DO UPDATE SET platform=excluded.platform, updated_at=excluded.updated_at""",
                 (token, platform, utc(at), utc(at)))


def device_tokens(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT token FROM devices WHERE platform='ios' ORDER BY token")]


def push_done(conn: sqlite3.Connection, *, report_id: str, kind: str, token: str) -> bool:
    return conn.execute("SELECT 1 FROM push_log WHERE report_id=? AND kind=? AND device_token=? AND state='sent'",
                        (report_id, kind, token)).fetchone() is not None


def log_push(conn: sqlite3.Connection, *, report_id: str, kind: str, token: str, state: str, detail: str | None,
             at: datetime) -> None:
    conn.execute("INSERT OR REPLACE INTO push_log VALUES (?,?,?,?,?,?)", (report_id, kind, token, state, detail, utc(at)))


# ------------------------------------------------------------------ 股票池

def read_stock_pool(conn: sqlite3.Connection) -> dict | None:
    """当前股票池。还没有刷新过返回 None。不建表。"""
    meta = conn.execute(
        "SELECT refreshed_at, count, content_sha256, source, excluded_json, mv_date, exclude_top_share "
        "FROM stock_pool_meta WHERE id=1").fetchone()
    if meta is None:
        return None
    rows = []
    for row in conn.execute(
            "SELECT code, name, fullname, list_date, aliases_json, summary, source, refreshed_at, "
            "circ_mv, total_mv, mv_date FROM stock_pool ORDER BY code"):
        rows.append({
            "code": row["code"], "name": row["name"], "fullname": row["fullname"] or "",
            "listDate": row["list_date"] or "", "aliases": json.loads(row["aliases_json"]),
            "summary": row["summary"], "summarySource": row["source"], "refreshedAt": row["refreshed_at"],
            "circMv": row["circ_mv"], "totalMv": row["total_mv"], "mvDate": row["mv_date"],
        })
    excluded = json.loads(meta["excluded_json"]) if meta["excluded_json"] else []
    return {"refreshedAt": meta["refreshed_at"], "count": meta["count"], "sha256": meta["content_sha256"],
            "source": meta["source"], "rows": rows, "excluded": excluded,
            "mvDate": meta["mv_date"], "excludeTopShare": meta["exclude_top_share"]}


def replace_stock_pool(conn: sqlite3.Connection, rows: Sequence[Mapping], *, refreshed_at: str, sha256: str,
                       source: str, excluded: Sequence[Mapping] = (), mv_date: str | None = None,
                       exclude_top_share: float | None = None) -> None:
    """整表替换当前池子和元信息。调用方负责把这一次放进同一个事务。"""
    rows = list(rows)
    conn.execute("DELETE FROM stock_pool")
    conn.execute("DELETE FROM stock_pool_meta")
    for row in rows:
        conn.execute("INSERT INTO stock_pool VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            row["code"], row["name"], row.get("fullname") or "", row.get("listDate") or "",
            _dumps(list(row.get("aliases") or ())), row.get("summary"), row.get("summarySource") or source,
            refreshed_at, row.get("circMv"), row.get("totalMv"), row.get("mvDate")))
    conn.execute("INSERT INTO stock_pool_meta VALUES (1,?,?,?,?,?,?,?)", (
        refreshed_at, len(rows), sha256, source, _dumps(list(excluded)), mv_date, exclude_top_share))


def read_sector_concepts(conn: sqlite3.Connection) -> dict | None:
    """当前概念清单。还没有刷新记录时返回 None。失败过的记录仍返回，error 里是原因。"""
    meta = conn.execute(
        "SELECT refreshed_at, count, content_sha256, error FROM sector_concepts_meta WHERE id=1").fetchone()
    if meta is None:
        return None
    members: dict[str, list[str]] = {}
    for row in conn.execute("SELECT ts_code, code FROM sector_members ORDER BY ts_code, code"):
        members.setdefault(row["ts_code"], []).append(row["code"])
    concepts = []
    for row in conn.execute("SELECT ts_code, name, member_count, refreshed_at FROM sector_concepts ORDER BY ts_code"):
        concepts.append({
            "tsCode": row["ts_code"], "name": row["name"], "memberCount": row["member_count"],
            "members": members.get(row["ts_code"], []), "refreshedAt": row["refreshed_at"],
        })
    return {"refreshedAt": meta["refreshed_at"], "count": meta["count"], "sha256": meta["content_sha256"],
            "error": meta["error"], "concepts": concepts}


def replace_sector_concepts(conn: sqlite3.Connection, concepts: Sequence[Mapping], *, refreshed_at: str,
                            sha256: str) -> None:
    """整表替换概念、成分和成功元信息。调用方负责放进同一个事务。"""
    concepts = list(concepts)
    conn.execute("DELETE FROM sector_members")
    conn.execute("DELETE FROM sector_concepts")
    conn.execute("DELETE FROM sector_concepts_meta")
    for item in concepts:
        conn.execute("INSERT INTO sector_concepts VALUES (?,?,?,?)", (
            item["tsCode"], item["name"], int(item["memberCount"]), refreshed_at))
        for code in item["members"]:
            conn.execute("INSERT INTO sector_members VALUES (?,?)", (item["tsCode"], code))
    conn.execute("INSERT INTO sector_concepts_meta VALUES (1,?,?,?,NULL)",
                 (refreshed_at, len(concepts), sha256))


def set_sector_refresh_error(conn: sqlite3.Connection, error: str) -> None:
    """记下这次失败。不改概念行，也不改上次成功的时间和哈希。"""
    row = conn.execute("SELECT 1 FROM sector_concepts_meta WHERE id=1").fetchone()
    if row is None:
        conn.execute("INSERT INTO sector_concepts_meta VALUES (1,NULL,0,?,?)", ("", error))
        return
    conn.execute("UPDATE sector_concepts_meta SET error=? WHERE id=1", (error,))


# ------------------------------------------------------------------ 板块事件


def _sector_event(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["repeat"] = bool(data["repeat"])
    data["package"] = json.loads(data.pop("package_json"))
    raw_output = data.pop("output_json")
    if raw_output is None:
        data["output"] = None
    else:
        try:
            data["output"] = json.loads(raw_output)
        except ValueError:
            data["output"] = raw_output
    data["usage"] = json.loads(data.pop("usage_json") or "{}")
    return data


def get_sector_event(conn: sqlite3.Connection, report_id: str, event_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM sector_events WHERE report_id=? AND event_id=?",
                       (report_id, event_id)).fetchone()
    return None if row is None else _sector_event(row)


def list_sector_events(conn: sqlite3.Connection, report_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM sector_events WHERE report_id=? ORDER BY event_id", (report_id,)).fetchall()
    return [_sector_event(row) for row in rows]


def insert_sector_event(conn: sqlite3.Connection, *, report_id: str, event_id: str, title: str,
                        sector: str | None, reason: str | None, repeat: bool, state: str,
                        package: Mapping, prepared_at: datetime, verdict: str | None = None,
                        output: Any = None, error: str | None = None, usage: Mapping | None = None,
                        seconds: float | None = None, settled_at: datetime | None = None) -> bool:
    """写入一条事件。同一期同一编号已经有行时不覆盖，续跑沿用原来的数据包和终态。"""
    if len(event_id) != 16:
        raise ValueError("事件编号须为 16 位")
    if state not in ("prepared", "succeeded", "invalid", "gap", "skipped"):
        raise ValueError("板块事件状态无效")
    if state == "prepared" and settled_at is not None:
        raise ValueError("prepared 还没有收口时间")
    if state != "prepared" and settled_at is None:
        raise ValueError("终态必须带收口时间")
    if state == "succeeded" and verdict not in ("推", "不推"):
        raise ValueError("verdict 无效")
    if state != "succeeded":
        verdict = None
    if isinstance(output, str):
        output_json = output
    elif output is None:
        output_json = None
    else:
        output_json = _dumps(output)
    cursor = conn.execute(
        """INSERT INTO sector_events(report_id, event_id, title, sector, reason, repeat, state, verdict,
                                     package_json, output_json, error, usage_json, seconds, prepared_at, settled_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(report_id, event_id) DO NOTHING""",
        (report_id, event_id, title, sector, reason, 1 if repeat else 0, state, verdict, _dumps(package),
         output_json, error, _dumps(dict(usage or {})), seconds, utc(prepared_at),
         None if settled_at is None else utc(settled_at)))
    return cursor.rowcount == 1


def settle_sector_event(conn: sqlite3.Connection, *, report_id: str, event_id: str, state: str,
                        settled_at: datetime, verdict: str | None = None, output: Any = None,
                        error: str | None = None, usage: Mapping | None = None,
                        seconds: float | None = None) -> int:
    """只允许从 prepared 收成 succeeded、invalid 或 gap。返回更新行数。"""
    if state not in ("succeeded", "invalid", "gap"):
        raise ValueError("板块事件只能收成 succeeded、invalid 或 gap")
    if state == "succeeded" and verdict not in ("推", "不推"):
        raise ValueError("verdict 无效")
    if state != "succeeded":
        verdict = None
    if isinstance(output, str):
        output_json = output
    elif output is None:
        output_json = None
    else:
        output_json = _dumps(output)
    cursor = conn.execute(
        """UPDATE sector_events SET state=?, verdict=?, output_json=?, error=?, usage_json=?, seconds=?, settled_at=?
           WHERE report_id=? AND event_id=? AND state='prepared'""",
        (state, verdict, output_json, error, _dumps(dict(usage or {})), seconds, utc(settled_at),
         report_id, event_id))
    return cursor.rowcount


def merge_running_stats(conn: sqlite3.Connection, report_id: str, patch: Mapping) -> None:
    """把开工时就定下来的统计写进仍为 running 的晨报。终态另写完整统计。"""
    row = conn.execute("SELECT stats_json FROM reports WHERE report_id=? AND status='running'", (report_id,)).fetchone()
    if row is None:
        return
    stats = json.loads(row["stats_json"] or "{}")
    stats.update(dict(patch))
    conn.execute("UPDATE reports SET stats_json=? WHERE report_id=? AND status='running'", (_dumps(stats), report_id))
