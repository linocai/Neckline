"""Neckline V3 API: reads and explicit user commands; heavy work runs in K10 worker."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, status

from neckline import notify_kinds
from neckline.api.deps import require_api_token_ready, require_token
from neckline.api.k10 import create_router as create_k10_router
from neckline.api.schemas import (
    DeviceRegisterIn, OkOut, ProviderCreateIn, ProviderOut, ProviderUpdateIn, ProvidersListOut,
    PushKindOut, PushSettingsOut, SettingsOut, SettingsProviderOut, SettingsPushIn,
    TavilySettingsIn, TavilySettingsOut,
)
from neckline.api.stores import upsert_device
from neckline.config import settings
from neckline.k10.schema import read_connection, require_schema
from neckline.k10.notifications import require_notifications_schema
from neckline.settings_store import (
    create_provider, delete_provider, get_app_settings, get_tavily_api_key, list_providers_public,
    set_push_kinds, set_tavily_api_key, update_provider,
)

VERSION = "v3.1.0"
RELEASE_SET = "v3.1.0-b52"
API_PREFIX = "/api/v1"
_DB_PATH_OVERRIDE: Optional[Path] = None


def _db() -> Path:
    return _DB_PATH_OVERRIDE or settings.db_path


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_api_token_ready()
    # Schema changes belong to the verified offline cutover command. Starting
    # the API never runs a scan, starts a background model or mutates a database.
    with read_connection(_db()) as connection:
        require_schema(connection)
    require_notifications_schema(_db())
    yield


app = FastAPI(title="Neckline", version=VERSION, lifespan=lifespan)
app.include_router(create_k10_router(db_path_provider=_db, require_token_dependency=require_token,
                                     parquet_dir_provider=lambda: settings.parquet_dir,
                                     current_config_binding_provider=lambda: (
                                         settings.k10_config_id,
                                         settings.k10_config_revision,
                                         settings.k10_config_binding_error,
                                     ),
                                     current_execution_config_binding_provider=lambda: (
                                         settings.k10_execution_config_id,
                                         settings.k10_execution_config_revision,
                                         settings.k10_execution_config_binding_error,
                                     )))


@app.get(f"{API_PREFIX}/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "releaseSet": RELEASE_SET}


@app.get(f"{API_PREFIX}/settings", dependencies=[Depends(require_token)])
def get_settings() -> SettingsOut:
    st = get_app_settings(db_path=_db())
    providers = [
        SettingsProviderOut(
            name=p.name, model=p.model, hasWebSearch=p.has_web_search,
            keySet=p.key_set, enabled=p.enabled,
        )
        for p in list_providers_public(db_path=_db())
    ]
    return SettingsOut(
        providers=providers,
        tavily=TavilySettingsOut(keySet=st.tavily_key_set),
        push=PushSettingsOut(kinds=[
            PushKindOut(
                kind=k, level=notify_kinds.level_of(k),
                label=notify_kinds.KIND_LABEL[k], enabled=st.push_kinds[k],
            )
            for k in notify_kinds.ALL_KINDS
        ]),
    )


def _provider_out(rec) -> ProviderOut:
    return ProviderOut(
        name=rec.name, baseUrl=rec.base_url, model=rec.model, hasWebSearch=rec.has_web_search,
        searchEngine=rec.search_engine, notes=rec.notes, enabled=rec.enabled,
        keySet=bool(rec.api_key),
    )


@app.get(f"{API_PREFIX}/settings/providers", dependencies=[Depends(require_token)])
def list_settings_providers() -> ProvidersListOut:
    return ProvidersListOut(items=[
        ProviderOut(
            name=p.name, baseUrl=p.base_url, model=p.model, hasWebSearch=p.has_web_search,
            searchEngine=p.search_engine, notes=p.notes, enabled=p.enabled, keySet=p.key_set,
        )
        for p in list_providers_public(db_path=_db())
    ])


@app.post(f"{API_PREFIX}/settings/providers", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(require_token)])
def create_settings_provider(body: ProviderCreateIn) -> ProviderOut:
    try:
        rec = create_provider(
            body.name, body.baseUrl, body.model, api_key=body.apiKey,
            has_web_search=body.hasWebSearch, search_engine=body.searchEngine,
            notes=body.notes, enabled=body.enabled, db_path=_db(),
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                             detail={"ok": False, "reason": "already_exists"})
    return _provider_out(rec)


@app.put(f"{API_PREFIX}/settings/providers/{{name}}", dependencies=[Depends(require_token)])
def update_settings_provider(name: str, body: ProviderUpdateIn) -> ProviderOut:
    fields = body.model_fields_set
    kwargs: Dict[str, Any] = {}
    if "baseUrl" in fields:
        kwargs["base_url"] = body.baseUrl
    if "model" in fields:
        kwargs["model"] = body.model
    if "apiKey" in fields:
        kwargs["api_key"] = body.apiKey
    if "hasWebSearch" in fields:
        kwargs["has_web_search"] = body.hasWebSearch
    if "searchEngine" in fields:
        kwargs["search_engine"] = body.searchEngine
    if "notes" in fields:
        kwargs["notes"] = body.notes
    if "enabled" in fields:
        kwargs["enabled"] = body.enabled
    rec = update_provider(name, db_path=_db(), **kwargs)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return _provider_out(rec)


@app.delete(f"{API_PREFIX}/settings/providers/{{name}}", dependencies=[Depends(require_token)])
def delete_settings_provider(name: str) -> OkOut:
    if not delete_provider(name, db_path=_db()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return OkOut(ok=True)


@app.put(f"{API_PREFIX}/settings/tavily", dependencies=[Depends(require_token)])
def put_settings_tavily(body: TavilySettingsIn) -> TavilySettingsOut:
    if not body.apiKey.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"ok": False, "reason": "invalid_tavily_key"})
    set_tavily_api_key(body.apiKey, db_path=_db())
    return TavilySettingsOut(keySet=bool(get_tavily_api_key(db_path=_db())))


@app.delete(f"{API_PREFIX}/settings/tavily", dependencies=[Depends(require_token)])
def delete_settings_tavily() -> TavilySettingsOut:
    set_tavily_api_key(None, db_path=_db())
    return TavilySettingsOut(keySet=False)


@app.put(f"{API_PREFIX}/settings/push", dependencies=[Depends(require_token)])
def put_settings_push(body: SettingsPushIn) -> OkOut:
    try:
        set_push_kinds(body.kinds, db_path=_db())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                             detail={"ok": False, "reason": "invalid_push_kinds", "message": str(e)})
    return OkOut(ok=True)


@app.post(f"{API_PREFIX}/devices", dependencies=[Depends(require_token)])
def register_device(body: DeviceRegisterIn) -> OkOut:
    upsert_device(body.token, body.platform, db_path=_db())
    return OkOut(ok=True)


@app.get(f"{API_PREFIX}/usage/summary", dependencies=[Depends(require_token)])
def get_usage_summary(days: int = 5) -> dict:
    from neckline.llm import usage
    return usage.summary(days=days, db_path=_db())
