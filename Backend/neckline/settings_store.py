"""Connection registry, write-only search credentials and K10 notification settings.

Only explicit commands write. Read functions never migrate a database or expose keys.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neckline import notify_kinds
from neckline.db import connection, init_schema, readonly_tables

_UNSET = object()
_PROVIDER_COLUMNS = "id, name, base_url, model, api_key, has_web_search, search_engine, notes, enabled, created_at, updated_at"


@dataclass
class AppSettings:
    push_kinds: Dict[str, bool]
    tavily_key_set: bool
    updated_at: Optional[str]


@dataclass
class ProviderRecord:

    id: int
    name: str
    base_url: str
    model: str
    api_key: Optional[str]
    has_web_search: bool
    search_engine: Optional[str]
    notes: Optional[str]
    enabled: bool
    created_at: str
    updated_at: str


@dataclass
class ProviderPublic:

    name: str
    base_url: str
    model: str
    has_web_search: bool
    search_engine: Optional[str]
    notes: Optional[str]
    enabled: bool
    key_set: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return v or None


def _ensure_row(conn) -> None:
    conn.execute("INSERT OR IGNORE INTO app_settings (id) VALUES (1)")


def _decode_push_kinds(raw: Optional[str]) -> Dict[str, bool]:
    data: Dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except (json.JSONDecodeError, TypeError):
            data = {}
    return {
        k: (bool(data[k]) if k in data else notify_kinds.DEFAULT_ENABLED)
        for k in notify_kinds.ALL_KINDS
    }


def get_push_kinds(db_path: Optional[Path] = None) -> Dict[str, bool]:
    with readonly_tables("app_settings.push_kinds", db_path=db_path) as conn:
        row = None if conn is None else conn.execute(
            "SELECT push_kinds FROM app_settings WHERE id=1").fetchone()
    return _decode_push_kinds(row[0] if row else None)


def set_push_kinds(kinds: Dict[str, bool], db_path: Optional[Path] = None) -> None:
    unknown = sorted(set(kinds) - set(notify_kinds.ALL_KINDS))
    if unknown:
        raise ValueError(f"未登记的通知 kind:{unknown};合法取值见 notify_kinds.ALL_KINDS")
    missing = [k for k in notify_kinds.ALL_KINDS if k not in kinds]
    if missing:
        raise ValueError(f"推送开关必须给全每一个 kind,缺:{missing}")
    payload = {k: (1 if kinds[k] else 0) for k in notify_kinds.ALL_KINDS}
    init_schema(db_path)
    with connection(db_path) as conn:
        _ensure_row(conn)
        conn.execute(
            "UPDATE app_settings SET push_kinds=?, updated_at=? WHERE id=1",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), _now()),
        )


def push_kind_enabled(kind: str, db_path: Optional[Path] = None) -> bool:
    notify_kinds.level_of(kind)   # 未登记 → 抛,不给"未知 kind 静默放行/静默拦截"
    return get_push_kinds(db_path=db_path)[kind]


def get_app_settings(db_path: Optional[Path] = None) -> AppSettings:
    with readonly_tables("app_settings.push_kinds", "app_settings.tavily_api_key", db_path=db_path) as conn:
        row = None if conn is None else conn.execute(
            "SELECT push_kinds,tavily_api_key,updated_at FROM app_settings WHERE id=1"
        ).fetchone()
    return AppSettings(_decode_push_kinds(row[0] if row else None),
                       bool(_clean(row[1])) if row else False, row[2] if row else None)


def get_tavily_api_key(db_path: Optional[Path] = None) -> Optional[str]:
    with readonly_tables("app_settings.tavily_api_key", db_path=db_path) as conn:
        row = None if conn is None else conn.execute(
            "SELECT tavily_api_key FROM app_settings WHERE id=1").fetchone()
    return _clean(row[0]) if row else None


def set_tavily_api_key(api_key: Optional[str], db_path: Optional[Path] = None) -> None:
    init_schema(db_path)
    with connection(db_path) as conn:
        _ensure_row(conn)
        conn.execute(
            "UPDATE app_settings SET tavily_api_key=?, updated_at=? WHERE id=1",
            (_clean(api_key), _now()),
        )


def _row_to_record(row: Tuple[Any, ...]) -> ProviderRecord:
    return ProviderRecord(
        id=row[0], name=row[1], base_url=row[2], model=row[3], api_key=_clean(row[4]),
        has_web_search=bool(row[5]), search_engine=_clean(row[6]), notes=_clean(row[7]),
        enabled=bool(row[8]), created_at=row[9], updated_at=row[10],
    )


def _to_public(rec: ProviderRecord) -> ProviderPublic:
    return ProviderPublic(
        name=rec.name, base_url=rec.base_url, model=rec.model, has_web_search=rec.has_web_search,
        search_engine=rec.search_engine, notes=rec.notes, enabled=rec.enabled,
        key_set=bool(rec.api_key),
    )


def list_providers(db_path: Optional[Path] = None) -> List[ProviderRecord]:
    with readonly_tables("llm_providers", db_path=db_path) as conn:
        if conn is None:
            return []
        rows = conn.execute(
            f"SELECT {_PROVIDER_COLUMNS} FROM llm_providers ORDER BY id ASC"
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def list_providers_public(db_path: Optional[Path] = None) -> List[ProviderPublic]:
    return [_to_public(r) for r in list_providers(db_path=db_path)]


def get_provider_record(name: str, db_path: Optional[Path] = None) -> Optional[ProviderRecord]:
    with readonly_tables("llm_providers", db_path=db_path) as conn:
        row = None if conn is None else conn.execute(
            f"SELECT {_PROVIDER_COLUMNS} FROM llm_providers WHERE name=?", (name,)
        ).fetchone()
    return _row_to_record(row) if row is not None else None


def create_provider(
    name: str,
    base_url: str,
    model: str,
    *,
    api_key: Optional[str] = None,
    has_web_search: bool = False,
    search_engine: Optional[str] = None,
    notes: Optional[str] = None,
    enabled: bool = True,
    db_path: Optional[Path] = None,
) -> ProviderRecord:
    nm = (name or "").strip()
    if not nm:
        raise ValueError("provider name 不可为空")
    bu = (base_url or "").strip()
    if not bu:
        raise ValueError("base_url 不可为空")
    md = (model or "").strip()
    if not md:
        raise ValueError("model 不可为空")
    now = _now()
    init_schema(db_path)
    try:
        with connection(db_path) as conn:
            conn.execute(
                "INSERT INTO llm_providers "
                "(name, base_url, model, api_key, has_web_search, search_engine, notes, enabled, "
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (nm, bu, md, _clean(api_key), 1 if has_web_search else 0, _clean(search_engine),
                 _clean(notes), 1 if enabled else 0, now, now),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"provider 已存在:{nm!r}") from e
    rec = get_provider_record(nm, db_path=db_path)
    assert rec is not None  # 刚插入,必然能读回
    return rec


def update_provider(
    name: str,
    *,
    base_url: Any = _UNSET,
    model: Any = _UNSET,
    api_key: Any = _UNSET,
    has_web_search: Any = _UNSET,
    search_engine: Any = _UNSET,
    notes: Any = _UNSET,
    enabled: Any = _UNSET,
    db_path: Optional[Path] = None,
) -> Optional[ProviderRecord]:
    init_schema(db_path)
    sets: List[str] = []
    vals: List[Any] = []
    if base_url is not _UNSET:
        sets.append("base_url=?")
        vals.append(str(base_url).strip())
    if model is not _UNSET:
        sets.append("model=?")
        vals.append(str(model).strip())
    if api_key is not _UNSET:
        sets.append("api_key=?")
        vals.append(_clean(api_key))
    if has_web_search is not _UNSET:
        sets.append("has_web_search=?")
        vals.append(1 if has_web_search else 0)
    if search_engine is not _UNSET:
        sets.append("search_engine=?")
        vals.append(_clean(search_engine))
    if notes is not _UNSET:
        sets.append("notes=?")
        vals.append(_clean(notes))
    if enabled is not _UNSET:
        sets.append("enabled=?")
        vals.append(1 if enabled else 0)

    if not sets:
        return get_provider_record(name, db_path=db_path)  # 无字段变更,只探是否存在

    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(name)
    with connection(db_path) as conn:
        cur = conn.execute(f"UPDATE llm_providers SET {', '.join(sets)} WHERE name=?", vals)
        if cur.rowcount == 0:
            return None
    return get_provider_record(name, db_path=db_path)


def delete_provider(name: str, db_path: Optional[Path] = None) -> bool:
    init_schema(db_path)
    with connection(db_path) as conn:
        cur = conn.execute("DELETE FROM llm_providers WHERE name=?", (name,))
        if cur.rowcount == 0:
            return False
        return True
