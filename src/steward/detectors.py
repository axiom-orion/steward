"""Debt detectors — pure functions over metadata payloads, so they test offline.

A Finding is a claim that a specific entity has a specific, fixable defect,
carrying the evidence that supports the claim. Detectors never decide the fix;
they only surface debt. The drafter proposes, the judge disposes.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Finding:
    kind: str            # e.g. "missing_description"
    urn: str
    entity_name: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _first(entity: Any) -> dict:
    """get_entities returns a list-ish payload; normalize to the first record."""
    if isinstance(entity, list):
        return entity[0] if entity else {}
    if isinstance(entity, dict):
        for key in ("entities", "results", "data"):
            inner = entity.get(key)
            if isinstance(inner, list):
                return inner[0] if inner else {}
        return entity
    return {}


def _walk_strings(payload: Any, key: str) -> list[str]:
    """Collect every string value stored under `key` anywhere in the payload."""
    out: list[str] = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k == key and isinstance(v, str):
                out.append(v)
            else:
                out.extend(_walk_strings(v, key))
    elif isinstance(payload, list):
        for item in payload:
            out.extend(_walk_strings(item, key))
    return out


def platform_from_urn(urn: str) -> str:
    """urn:li:dataset:(urn:li:dataPlatform:dbt,shop.orders,PROD) -> dbt"""
    marker = "dataPlatform:"
    if marker in urn:
        return urn.split(marker, 1)[1].split(",", 1)[0].rstrip(")")
    return "unknown"


def dataset_urns_from_search(search_payload: dict) -> list[str]:
    """Pull dataset URNs out of a search response, ignoring other entity types."""
    urns = _walk_strings(search_payload, "urn")
    return [u for u in dict.fromkeys(urns) if u.startswith("urn:li:dataset:")]


def entity_display_name(entity_payload: Any, urn: str) -> str:
    record = _first(entity_payload)
    names = _walk_strings(record, "name")
    return names[0] if names else urn.rsplit(",", 2)[-2] if "," in urn else urn


def detect_missing_description(urn: str, entity_payload: Any, schema_payload: Any) -> Finding | None:
    """A dataset with no (or whitespace-only) description is documentation debt.

    Only the dataset's own description slots count — a deep walk would see the
    description on an owner's ownershipType (or a glossary term) and wrongly
    mark the dataset as documented."""
    record = _first(entity_payload)
    descriptions = []
    for slot in ("properties", "editableProperties"):
        value = record.get(slot) or {}
        if isinstance(value, dict):
            descriptions.append(str(value.get("description") or ""))
    descriptions.append(str(record.get("description") or ""))
    if any(d.strip() for d in descriptions):
        return None
    field_names = _walk_strings(schema_payload, "fieldPath") or _walk_strings(schema_payload, "name")
    return Finding(
        kind="missing_description",
        urn=urn,
        entity_name=entity_display_name(entity_payload, urn),
        evidence={
            "platform": platform_from_urn(urn),
            "schema_fields": field_names[:40],
        },
    )
