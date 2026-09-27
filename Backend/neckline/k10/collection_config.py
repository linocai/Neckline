"""Explicit configuration contract for independently scheduled news collection."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .config import ConfigurationStatus
from .jin10_mcp import ENDPOINT, PROTOCOL_VERSION


COLLECTION_CONTRACT = "k10-collection-3.6.1-b92"
SOURCE_KEYS = ("tushare-major-news", "jin10-flash", "jin10-news")


def _time(value: object) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        return False
    return parsed.tzinfo is not None


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_collection_config(value: Mapping[str, Any] | None) -> ConfigurationStatus:
    required = ("collectionVersion", "timezone", "slots", "sources", "mcp")
    if not isinstance(value, Mapping):
        return ConfigurationStatus("not_configured", required, ("采集配置缺失",))
    missing = tuple(key for key in required if key not in value)
    errors: list[str] = []
    if set(value) != set(required):
        errors.append("采集配置字段不完整或包含未知字段")
    if value.get("collectionVersion") != COLLECTION_CONTRACT:
        errors.append("collectionVersion 无效")
    if value.get("timezone") != "Asia/Shanghai" or value.get("slots") != ["08:00", "20:00"]:
        errors.append("采集时区及每日 08:00/20:00 槽必须明确")
    sources = value.get("sources")
    if not isinstance(sources, list) or [item.get("sourceKey") if isinstance(item, Mapping) else None for item in sources] != list(SOURCE_KEYS):
        errors.append("采集来源必须依次声明 TuShare、金十快讯、金十文章")
    else:
        for source in sources:
            if set(source) != {"sourceKey", "bootstrapStartAt", "lookbackSeconds", "maxPages",
                               "timeoutSeconds", "maxAttempts", "credentialEnv"}:
                errors.append(f"{source['sourceKey']} 来源字段无效")
                continue
            if not _time(source["bootstrapStartAt"]) or not isinstance(source["lookbackSeconds"], int) or source["lookbackSeconds"] < 0:
                errors.append(f"{source['sourceKey']} 起始边界无效")
            for key in ("maxPages", "timeoutSeconds", "maxAttempts"):
                if not _positive(source[key]):
                    errors.append(f"{source['sourceKey']}.{key} 必须为正整数")
            expected_env = "TUSHARE_TOKEN" if source["sourceKey"] == "tushare-major-news" else "JIN10_MCP_TOKEN"
            if source["credentialEnv"] != expected_env:
                errors.append(f"{source['sourceKey']} 凭据引用无效")
    mcp = value.get("mcp")
    if not isinstance(mcp, Mapping) or set(mcp) != {"endpoint", "protocolVersion"} or mcp.get("endpoint") != ENDPOINT or mcp.get("protocolVersion") != PROTOCOL_VERSION:
        errors.append("金十 MCP endpoint/protocolVersion 必须精确绑定已测值")
    return ConfigurationStatus("configured", (), ()) if not missing and not errors else ConfigurationStatus("not_configured", missing, tuple(errors))


__all__ = ["COLLECTION_CONTRACT", "SOURCE_KEYS", "validate_collection_config"]
