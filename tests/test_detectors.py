"""Offline tests — fixtures only, no DataHub, no API keys."""

import json

from steward.detectors import (
    dataset_urns_from_search,
    detect_missing_description,
    entity_display_name,
)
from steward.ledger import Ledger

SEARCH_FIXTURE = {
    "searchResults": [
        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.orders,PROD)", "properties": {"name": "orders"}}},
        {"entity": {"urn": "urn:li:dataProduct:abc-123", "properties": {"name": "Analytics"}}},
        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.items,PROD)"}},
        {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.orders,PROD)"}},  # dup
    ]
}


def test_dataset_urns_filters_and_dedupes():
    urns = dataset_urns_from_search(SEARCH_FIXTURE)
    assert urns == [
        "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.orders,PROD)",
        "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.items,PROD)",
    ]


def test_missing_description_fires_on_empty():
    urn = "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.orders,PROD)"
    entity = {"entities": [{"urn": urn, "properties": {"name": "orders", "description": "  "}}]}
    schema = {"fields": [{"fieldPath": "order_id"}, {"fieldPath": "amount"}]}
    f = detect_missing_description(urn, entity, schema)
    assert f is not None
    assert f.kind == "missing_description"
    assert f.evidence["schema_fields"] == ["order_id", "amount"]
    assert f.evidence["platform"] == "dbt"


def test_missing_description_skips_documented():
    urn = "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.orders,PROD)"
    entity = {"entities": [{"urn": urn, "properties": {"description": "Order fact table."}}]}
    assert detect_missing_description(urn, entity, {}) is None


def test_display_name_fallback():
    urn = "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.orders,PROD)"
    assert entity_display_name({}, urn) == "shop.orders"


def test_ledger_roundtrip(tmp_path):
    urn = "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.orders,PROD)"
    entity = {"entities": [{"urn": urn, "properties": {"name": "orders"}}]}
    f = detect_missing_description(urn, entity, {})
    ledger = Ledger(str(tmp_path / "ledger.jsonl"))
    ledger.write("decided", f, proposal={"description": "x"}, verdict={"approved": False})
    ledger.write("applied", f, description="x")
    records = ledger.read_all()
    assert [r["event"] for r in records] == ["decided", "applied"]
    assert records[0]["finding"]["urn"] == urn
    # every line is independently parseable JSON
    raw = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert all(json.loads(line) for line in raw)
