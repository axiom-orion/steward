"""Offline tests — fixtures only, no DataHub, no API keys."""

import json

import pytest

from steward.corpus import (
    Dataset,
    Owner,
    Tag,
    Twin,
    dataset_urns,
    field_overlap,
    find_twins,
    logical_name,
    parse_dataset,
    platform_from_urn,
)
from steward.detectors import (
    detect_missing_description,
    detect_missing_domain,
    detect_missing_owner,
    detect_tag_drift,
    scan_dataset,
)
from steward.ledger import Ledger
from steward.mcp import WRITE_TOOLS
from steward.remedy import propose

DBT = "urn:li:dataset:(urn:li:dataPlatform:dbt,pack.order_entry_db.order_entry.addresses,PROD)"
SNOW = "urn:li:dataset:(urn:li:dataPlatform:snowflake,pack.order_entry_db.order_entry.addresses,PROD)"
OTHER = "urn:li:dataset:(urn:li:dataPlatform:postgres,pack.other_db.other.addresses,PROD)"

FIELDS = ("address_id", "customer_id", "address_line1", "address_line2", "zipcode")


def ds(urn, **kw) -> Dataset:
    base = dict(name=logical_name(urn), platform=platform_from_urn(urn), fields=FIELDS)
    base.update(kw)
    return Dataset(urn=urn, **base)


def governed(urn=DBT) -> Dataset:
    return ds(
        urn,
        description="Shipping and billing addresses.",
        owners=(
            Owner("urn:li:corpGroup:pack.ORG_DATA_PLATFORM", "Data Platform Team",
                  "urn:li:ownershipType:__system__technical_owner", "Technical Owner"),
            Owner("urn:li:corpuser:pack.EMP006", "Ian Chen",
                  "urn:li:ownershipType:__system__business_owner", "Business Owner"),
        ),
        tags=(Tag("urn:li:tag:pack.PII_Data", "PII Data", "Contains personal data."),),
        domain_urn="urn:li:domain:pack.abc",
        domain_name="Data Platform Team",
    )


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

SEARCH_FIXTURE = {
    "searchResults": [
        {"entity": {"urn": DBT, "properties": {"name": "addresses"}}},
        {"entity": {"urn": "urn:li:dataProduct:abc-123", "properties": {"name": "Analytics"}}},
        {"entity": {"urn": SNOW}},
        {"entity": {"urn": DBT}},  # dup
    ]
}


def test_dataset_urns_filters_and_dedupes():
    assert dataset_urns(SEARCH_FIXTURE) == [DBT, SNOW]


def test_parse_dataset_reads_governance_facets():
    record = {
        "urn": DBT,
        "name": "addresses",
        "platform": {"name": "dbt"},
        "properties": {"name": "addresses", "description": "Shipping and billing addresses."},
        "ownership": {"owners": [
            {"owner": {"urn": "urn:li:corpuser:pack.EMP006",
                       "properties": {"displayName": "Ian Chen"}},
             "ownershipType": {"urn": "urn:li:ownershipType:__system__business_owner",
                               "info": {"name": "Business Owner"}}},
            # the same principal listed twice, as real records do
            {"owner": {"urn": "urn:li:corpuser:pack.EMP006",
                       "properties": {"displayName": "Ian Chen"}},
             "ownershipType": {"urn": "urn:li:ownershipType:__system__business_owner",
                               "info": {"name": "Business Owner"}}},
        ]},
        "tags": {"tags": [{"tag": {"urn": "urn:li:tag:pack.PII_Data",
                                   "properties": {"name": "PII Data", "description": "personal"}}}]},
        "domain": {"domain": {"urn": "urn:li:domain:pack.abc", "properties": {"name": "Platform"}}},
        "schemaMetadata": {"fields": [{"fieldPath": f} for f in FIELDS]},
    }
    d = parse_dataset(record)
    assert d.platform == "dbt"
    assert d.description == "Shipping and billing addresses."
    assert [o.urn for o in d.owners] == ["urn:li:corpuser:pack.EMP006"]      # deduped
    assert d.owners[0].ownership_type_name == "Business Owner"
    assert d.domain_urn == "urn:li:domain:pack.abc"
    assert d.fields == FIELDS


def test_parse_dataset_tolerates_missing_and_null_facets():
    """`tags` is absent when untagged; `domain` comes back as an explicit null."""
    d = parse_dataset({"urn": SNOW, "name": "ADDRESSES", "platform": {"name": "snowflake"},
                       "properties": {"name": "ADDRESSES"}, "domain": None,
                       "schemaMetadata": {"fields": [{"fieldPath": "address_id"}]}})
    assert d.owners == () and d.tags == () and d.domain_urn is None
    assert d.description == ""


def test_description_ignores_descriptions_on_nested_objects():
    """The regression that made this detector silent: an ownership *type* and a
    glossary term both carry a `description`, and a deep walk finds them."""
    record = {
        "urn": SNOW, "name": "ADDRESSES", "platform": {"name": "snowflake"},
        "properties": {"name": "ADDRESSES"},
        "ownership": {"owners": [{
            "owner": {"urn": "urn:li:corpuser:pack.EMP006"},
            "ownershipType": {"urn": "urn:li:ownershipType:__system__technical_owner",
                              "info": {"name": "Technical Owner",
                                       "description": "Involved in production or maintenance."}},
        }]},
        "glossaryTerms": {"terms": [{"term": {"urn": "urn:li:glossaryTerm:pii",
                                              "properties": {"name": "PII",
                                                             "description": "Personally identifiable..."}}}]},
    }
    d = parse_dataset(record)
    assert d.description == ""
    assert detect_missing_description(d) is not None


# --------------------------------------------------------------------------
# tags: what may travel between platforms
# --------------------------------------------------------------------------

@pytest.mark.parametrize("urn,expected", [
    ("urn:li:tag:pack.PII_Data", True),
    ("urn:li:tag:pack.Authoritative Source", True),
    ("urn:li:tag:pack.__default_large_table", False),     # platform statistic
    ("urn:li:tag:pack.__default_high_queries", False),    # platform statistic
    ("urn:li:tag:Dimension", False),                      # BI column role
    ("urn:li:tag:MEASURE", False),
    ("urn:li:tag:COLUMNFIELD", False),
])
def test_governance_tag_classification(urn, expected):
    assert Tag(urn, "x").is_governance is expected


# --------------------------------------------------------------------------
# twinning
# --------------------------------------------------------------------------

def test_logical_name_strips_pack_db_and_schema():
    assert logical_name(DBT) == "addresses"


def test_twins_match_across_platforms():
    a, b = governed(DBT), ds(SNOW)
    twins = find_twins(b, [a, b])
    assert [t.dataset.urn for t in twins] == [DBT]
    assert twins[0].overlap == 1.0 and twins[0].shared_fields == 5


def test_twins_reject_same_name_different_columns():
    """`addresses` exists in every schema — the name alone proves nothing."""
    unrelated = ds(OTHER, fields=("id", "created_at", "payload", "checksum", "region"))
    assert find_twins(unrelated, [governed(DBT), unrelated]) == []


def test_twins_tolerate_a_wider_view_of_the_same_table():
    """Overlap divides by the smaller column set, so a 5-column table still
    matches the 60-column view built from it."""
    wide = ds(SNOW, fields=FIELDS + tuple(f"extra_{i}" for i in range(55)))
    ratio, shared = field_overlap(ds(DBT), wide)
    assert shared == 5 and ratio == 1.0


def test_twins_normalize_bi_prefixed_column_paths():
    looker = ds(SNOW, fields=tuple(f"addresses.{f}" for f in FIELDS))
    assert find_twins(looker, [governed(DBT), looker])


def test_twin_ordering_is_deterministic():
    weak = ds(OTHER, fields=FIELDS[:4] + ("unrelated",))
    strong = governed(DBT)
    target = ds(SNOW)
    assert [t.dataset.urn for t in find_twins(target, [weak, strong, target])][0] == DBT


# --------------------------------------------------------------------------
# detectors
# --------------------------------------------------------------------------

def test_missing_owner_fires_only_with_a_grounded_candidate():
    bare = ds(SNOW)
    assert detect_missing_owner(bare, []) is None            # no twin -> never guess
    f = detect_missing_owner(bare, find_twins(bare, [governed(), bare]))
    assert f and f.kind == "missing_owner"
    assert [o["urn"] for o in f.evidence["proposed_owners"]] == [
        "urn:li:corpGroup:pack.ORG_DATA_PLATFORM", "urn:li:corpuser:pack.EMP006"]
    assert f.evidence["twins_disagree"] is False


def test_missing_owner_skips_owned_datasets():
    owned = governed(SNOW)
    assert detect_missing_owner(owned, find_twins(owned, [governed(), owned])) is None


def test_missing_owner_flags_disagreeing_twins():
    other = ds(OTHER, fields=FIELDS,
               owners=(Owner("urn:li:corpuser:someone.else", "Someone Else"),))
    bare = ds(SNOW)
    f = detect_missing_owner(bare, find_twins(bare, [governed(), other, bare]))
    assert f.evidence["twins_disagree"] is True
    assert f.evidence["other_twins_with_owners"]


def test_missing_domain_fires_and_records_conflict():
    bare = ds(SNOW)
    f = detect_missing_domain(bare, find_twins(bare, [governed(), bare]))
    assert f.evidence["proposed_domain_urn"] == "urn:li:domain:pack.abc"
    assert f.evidence["twins_disagree"] is False


def test_tag_drift_one_finding_per_tag_and_skips_artifacts():
    twin = governed(DBT)
    twin = Dataset(**{**twin.__dict__, "tags": twin.tags + (
        Tag("urn:li:tag:pack.__default_large_table", "Large Table", "storage size"),
        Tag("urn:li:tag:pack.No Sample Values", "No Sample Values", "profiling annotation"),
    )})
    bare = ds(SNOW)
    findings = detect_tag_drift(bare, find_twins(bare, [twin, bare]))
    proposed = {f.evidence["proposed_tag_urn"] for f in findings}
    assert proposed == {"urn:li:tag:pack.PII_Data", "urn:li:tag:pack.No Sample Values"}
    assert all(f.kind == "tag_drift" for f in findings)


def test_tag_drift_skips_tags_already_present():
    twin = governed(DBT)
    already = ds(SNOW, tags=(Tag("urn:li:tag:pack.PII_Data", "PII Data"),))
    assert detect_tag_drift(already, find_twins(already, [twin, already])) == []


def test_scan_dataset_honours_kind_filter():
    bare = ds(SNOW)
    twins = find_twins(bare, [governed(), bare])
    kinds = {f.kind for f in scan_dataset(bare, twins)}
    assert kinds == {"missing_description", "missing_owner", "missing_domain", "tag_drift"}
    assert {f.kind for f in scan_dataset(bare, twins, ["missing_owner"])} == {"missing_owner"}


# --------------------------------------------------------------------------
# remedies — argument names are load-bearing; the server validates them
# before DataHub is touched, so a typo fails identically to a bad write
# --------------------------------------------------------------------------

def _propose(kind, target=None, twin_pool=None):
    target = target or ds(SNOW)
    pool = twin_pool or [governed(), target]
    twins = find_twins(target, pool)
    findings = [f for f in scan_dataset(target, twins) if f.kind == kind]
    assert findings, f"no {kind} finding produced"
    return propose(findings[0], model="unused-offline")


def test_owner_proposal_groups_by_ownership_type():
    p = _propose("missing_owner")
    assert {a.tool for a in p.actions} == {"add_owners"}
    assert len(p.actions) == 2, "technical and business owners cannot share one call"
    for a in p.actions:
        assert set(a.args) == {"owner_urns", "entity_urns", "ownership_type"}
        assert a.args["entity_urns"] == [SNOW]
        assert a.args["ownership_type"].startswith("urn:li:ownershipType:")


def test_owner_proposal_discloses_a_defaulted_ownership_type():
    twin = ds(DBT, owners=(Owner("urn:li:corpuser:pack.EMP006", "Ian Chen"),))
    p = _propose("missing_owner", twin_pool=[twin, ds(SNOW)])
    assert p.actions[0].args["ownership_type"] == "TECHNICAL_OWNER"
    assert any("no ownership type" in b for b in p.basis)


def test_domain_and_tag_proposals_use_the_servers_argument_names():
    d = _propose("missing_domain")
    assert d.actions[0].tool == "set_domains"
    assert set(d.actions[0].args) == {"domain_urn", "entity_urns"}

    t = _propose("tag_drift")
    assert t.actions[0].tool == "add_tags"
    assert set(t.actions[0].args) == {"tag_urns", "entity_urns"}


def test_propagated_proposals_need_no_model_and_invent_nothing():
    """No API key is touched for propagation, and every proposed value appears
    on the twin."""
    p = _propose("missing_owner")
    assert p.origin == "propagated"
    twin_owners = {o.urn for o in governed().owners}
    proposed = {u for a in p.actions for u in a.args["owner_urns"]}
    assert proposed <= twin_owners


def test_every_proposed_tool_is_allowlisted():
    for kind in ("missing_owner", "missing_domain", "tag_drift"):
        for action in _propose(kind).actions:
            assert action.tool in WRITE_TOOLS


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------

def test_ledger_roundtrip(tmp_path):
    f = detect_missing_description(ds(SNOW))
    ledger = Ledger(str(tmp_path / "ledger.jsonl"))
    ledger.write("decided", f, proposal={"summary": "x"}, verdict={"approved": False})
    ledger.write("applied", f, description="x")
    records = ledger.read_all()
    assert [r["event"] for r in records] == ["decided", "applied"]
    assert records[0]["finding"]["urn"] == SNOW
    raw = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert all(json.loads(line) for line in raw)
