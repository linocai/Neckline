"""Public, credential-free B92 collection status DTO."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectionConfigurationOut(_DTO):
    state: Literal["configured", "not_configured"]
    configId: str | None = None
    revision: int | None = None
    missing: list[str] = Field(default_factory=list)


class CollectionControlOut(_DTO):
    state: Literal["open", "closed"]
    reasonCode: str
    changedAt: str | None = None


class CollectionSourceOut(_DTO):
    sourceKey: str
    state: str
    lastSuccessAt: str | None = None
    coverageThrough: str | None = None
    observedStartAt: str | None = None
    observedEndAt: str | None = None
    limitations: list[str] = Field(default_factory=list)
    credentialConfigured: bool


class CollectionRunOut(_DTO):
    taskId: str
    slotAt: str
    status: str
    stage: str | None = None
    startedAt: str | None = None
    completedAt: str | None = None
    sourceOutcomes: list[CollectionSourceOut] = Field(default_factory=list)


class CollectionStatusOut(_DTO):
    schemaVersion: Literal["10"] = "10"
    configuration: CollectionConfigurationOut
    control: CollectionControlOut
    sources: list[CollectionSourceOut] = Field(default_factory=list)
    activeTasks: list[CollectionRunOut] = Field(default_factory=list)
    latestRuns: list[CollectionRunOut] = Field(default_factory=list)


class CollectionControlIn(_DTO):
    state: Literal["open", "closed"]


__all__ = ["CollectionConfigurationOut", "CollectionControlIn",
           "CollectionControlOut", "CollectionRunOut", "CollectionSourceOut",
           "CollectionStatusOut"]
