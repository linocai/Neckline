"""Serialization for the K9-v3 package report read model."""
from __future__ import annotations
from typing import Any

def structured(bundle: Any) -> dict[str, Any]:
    return dict(bundle.structured)

def markdown(bundle: Any, payload: dict[str, Any] | None = None) -> str:
    return bundle.markdown

__all__ = ["structured", "markdown"]
