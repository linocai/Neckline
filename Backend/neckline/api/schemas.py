"""Shared settings and device HTTP contracts. Credentials are write-only."""
from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class OkOut(BaseModel):
    ok: bool = True


class PushKindOut(BaseModel):
    kind: str
    level: str
    label: str
    enabled: bool


class PushSettingsOut(BaseModel):
    kinds: List[PushKindOut] = Field(default_factory=list)


class SettingsProviderOut(BaseModel):
    name: str
    model: str
    hasWebSearch: bool
    keySet: bool
    enabled: bool


class TavilySettingsOut(BaseModel):
    keySet: bool = False


class SettingsOut(BaseModel):
    providers: List[SettingsProviderOut] = Field(default_factory=list)
    tavily: TavilySettingsOut = Field(default_factory=TavilySettingsOut)
    push: PushSettingsOut


class ProviderOut(BaseModel):
    name: str
    baseUrl: str
    model: str
    hasWebSearch: bool
    searchEngine: Optional[str] = None
    notes: Optional[str] = None
    enabled: bool
    keySet: bool


class ProvidersListOut(BaseModel):
    items: List[ProviderOut] = Field(default_factory=list)


class ProviderCreateIn(BaseModel):
    name: str = Field(min_length=1)
    baseUrl: str = Field(min_length=1)
    model: str = Field(min_length=1)
    apiKey: Optional[str] = None
    hasWebSearch: bool = False
    searchEngine: Optional[str] = None
    notes: Optional[str] = None
    enabled: bool = True


class ProviderUpdateIn(BaseModel):
    baseUrl: Optional[str] = None
    model: Optional[str] = None
    apiKey: Optional[str] = None
    hasWebSearch: Optional[bool] = None
    searchEngine: Optional[str] = None
    notes: Optional[str] = None
    enabled: Optional[bool] = None


class TavilySettingsIn(BaseModel):
    apiKey: str = Field(min_length=1)


class SettingsPushIn(BaseModel):
    kinds: Dict[str, bool] = Field(default_factory=dict)


class DeviceRegisterIn(BaseModel):
    token: str
    platform: str = "ios"
