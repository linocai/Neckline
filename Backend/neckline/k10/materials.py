"""Reader-safe material projection shared by producers, storage and HTTP.

Only derived presentation rows are isolated here. Frozen research identities
and paid reply validation remain their owning stages' responsibility.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from .delivery import delivery_gap


class MaterialProjectionError(ValueError):
    def __init__(self, reason_code: str = "material_projection_invalid"):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _text(value: Any, *, maximum: int | None = None) -> str:
    # Narrative text keeps the full upstream value, including long Unicode
    # content. Length limits apply only to the established identity fields.
    if (not isinstance(value, str) or not value.strip()
            or (maximum is not None and len(value) > maximum)):
        raise MaterialProjectionError()
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise MaterialProjectionError() from exc
    return value


def _refs(value: Any, source_keys: set[tuple[str, int]] | None) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise MaterialProjectionError()
    result: list[dict[str, Any]] = []
    for ref in value:
        if not isinstance(ref, Mapping) or set(ref) != {"documentId", "revision"}:
            raise MaterialProjectionError()
        document_id = _text(ref["documentId"])
        revision = ref["revision"]
        if (not isinstance(revision, int) or isinstance(revision, bool)
                or not 1 <= revision <= 2**63 - 1):
            raise MaterialProjectionError()
        if source_keys is not None and (document_id, revision) not in source_keys:
            raise MaterialProjectionError("material_source_unavailable")
        result.append({"documentId": document_id, "revision": revision})
    return result


def project_material(item: Any, *, source_keys: set[tuple[str, int]] | None = None) -> dict[str, Any]:
    """Validate one material without rewriting its facts or truncating text.

    ``None`` checks structural shape only so a reader can collect exact source
    identities before looking them up. Public/write callers then pass the
    actual verified document revisions before accepting the result.
    """
    required = {"materialId", "eventId", "eventTitle", "facts", "companyRelations",
                "uncertainties", "sourceRefs", "asOf"}
    if not isinstance(item, Mapping) or set(item) != required:
        raise MaterialProjectionError()
    result = {"materialId": _text(item["materialId"], maximum=160),
              "eventId": _text(item["eventId"], maximum=160),
              "eventTitle": _text(item["eventTitle"]), "asOf": _text(item["asOf"])}
    try:
        stamp = datetime.fromisoformat(result["asOf"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise MaterialProjectionError() from exc
    if stamp.tzinfo is None:
        raise MaterialProjectionError()
    for field in ("facts", "companyRelations", "uncertainties"):
        if not isinstance(item[field], list):
            raise MaterialProjectionError()
    result["facts"] = []
    for fact in item["facts"]:
        if not isinstance(fact, Mapping) or set(fact) != {"text", "sourceRefs"}:
            raise MaterialProjectionError()
        result["facts"].append({"text": _text(fact["text"]),
                                "sourceRefs": _refs(fact["sourceRefs"], source_keys)})
    result["companyRelations"] = []
    for relation in item["companyRelations"]:
        if (not isinstance(relation, Mapping)
                or set(relation) != {"companyCode", "companyName", "relation", "sourceRefs"}):
            raise MaterialProjectionError()
        result["companyRelations"].append({
            "companyCode": _text(relation["companyCode"], maximum=32),
            "companyName": _text(relation["companyName"], maximum=160),
            "relation": _text(relation["relation"]),
            "sourceRefs": _refs(relation["sourceRefs"], source_keys),
        })
    result["uncertainties"] = [_text(value) for value in item["uncertainties"]]
    result["sourceRefs"] = _refs(item["sourceRefs"], source_keys)
    return result


def material_refs(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Exact document identities from an already structurally checked row."""
    return [*item["sourceRefs"],
            *(ref for fact in item["facts"] for ref in fact["sourceRefs"]),
            *(ref for relation in item["companyRelations"] for ref in relation["sourceRefs"])]


def material_gap(*, report_id: str, material_id: str, reason_code: str,
                 stage: str = "materials") -> dict[str, Any]:
    gap = delivery_gap(stage=stage, unit_kind="material", unit_id=material_id,
        reason_code=reason_code,
        message=("一项材料的来源无法核实，已跳过；其他材料仍可阅读。"
                 if reason_code == "material_source_unavailable" else
                 "一项材料的展示内容无法读取，已跳过；其他材料仍可阅读。"),
        company_scope_known=False)
    # Material identities can recur in several reports. Bind the read/write
    # diagnostic to this report without exposing dirty material content.
    from .delivery import digest
    gap["gapId"] = "gap_" + digest({"reportId": report_id, "gap": gap["gapId"]})[:32]
    return gap


def partition_materials(*, report_id: str, materials: Sequence[Any],
                        source_keys: set[tuple[str, int]], stage: str = "materials"
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(materials):
        material_id = item.get("materialId") if isinstance(item, Mapping) else None
        unit_id = (material_id if isinstance(material_id, str) and material_id.strip()
                   and len(material_id) <= 160 else f"material_projection_{index}")
        try:
            projection = project_material(item, source_keys=source_keys)
            if projection["materialId"] in seen:
                raise MaterialProjectionError("material_duplicate_identity")
        except MaterialProjectionError as exc:
            gaps.append(material_gap(report_id=report_id, material_id=unit_id,
                                     reason_code=exc.reason_code, stage=stage))
            continue
        seen.add(projection["materialId"])
        valid.append(projection)
    return valid, gaps
