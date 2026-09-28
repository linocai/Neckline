"""Small read-only projections; paid replies stay intact in their checkpoint.

SQLite parses the large JSON once per row instead of expanding raw provider
replies into Python objects. Serialize these reads in each process so parallel
status requests cannot multiply that temporary SQLite allocation.
"""
from __future__ import annotations

import json
from threading import RLock

CHECKPOINT_READ_LOCK = RLock()
SOURCE_KEYS = ("tushare-major-news", "jin10-flash", "jin10-news")
SOURCE_FIELDS = ("state", "lastSuccessAt", "coverageThrough", "observedStartAt",
                 "observedEndAt", "limitations", "requestedStartAt", "requestedEndAt", "gaps")
_PATHS = ["$.executionStartedAt"] + [
    f'$.sources."{source}".{field}' for source in SOURCE_KEYS for field in SOURCE_FIELDS
]
COLLECTION_SUMMARY_SQL = (
    "CASE WHEN json_valid(checkpoint_json) THEN json_extract(checkpoint_json,"
    + ",".join("'" + path + "'" for path in _PATHS) + ") ELSE '[]' END"
)
EXECUTION_SUMMARY_SQL = (
    "CASE WHEN json_valid(checkpoint_json) THEN "
    "json_object('executionStartedAt',json_extract(checkpoint_json,'$.executionStartedAt')) "
    "ELSE '{}' END"
)


def collection_summary(raw: str) -> dict:
    values = json.loads(raw)
    if not isinstance(values, list) or len(values) != len(_PATHS):
        return {}
    result = {"executionStartedAt": values[0], "sources": {}}
    for index, source in enumerate(SOURCE_KEYS):
        start = 1 + index * len(SOURCE_FIELDS)
        fields = dict(zip(SOURCE_FIELDS, values[start:start + len(SOURCE_FIELDS)]))
        if any(value is not None for value in fields.values()):
            result["sources"][source] = fields
    return result
