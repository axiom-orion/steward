"""Labeled cases for the judge eval.

Each case runs the real pipeline — parse -> find_twins -> detect -> propose —
over frozen payloads from DataHub's showcase-ecommerce pack, then hands the
result to the judge. Only the label is authored by hand.

Labels are defensible from the data alone, not from taste:

  approve  the value describes the table, and the twin is unmistakably the same
           table (identical column sets, identical database/schema/table path).
  reject   the value describes one platform's copy (a profiling annotation, a
           system-of-record designation), or the twin claim does not hold up.

Two cases are synthetic, marked `synthetic=True`: an impostor twin and a domain
conflict. Both are built by editing real records, because the showcase pack has
no naturally occurring example and a gate that is never shown a bad proposal has
not been measured.
"""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from steward.corpus import Dataset, find_twins, parse_dataset
from steward.detectors import Finding, scan_dataset
from steward.remedy import Proposal, propose

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "corpus_snapshot.json"

DBT_ORDER_DETAILS = "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.ORDER_ENTRY_DB.analytics.order_details,PROD)"
SNOW_ORDER_DETAILS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)"
DBT_ADDRESSES = "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.addresses,PROD)"
PG_ADDRESSES = "urn:li:dataset:(urn:li:dataPlatform:postgres,b2fd91.order_entry_db.order_entry.addresses,PROD)"
DBT_COUNTRIES = "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.countries,PROD)"
SNOW_COUNTRIES = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.countries,PROD)"
DBT_PROMOTIONS = "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.promotions,PROD)"
PG_PROMOTIONS = "urn:li:dataset:(urn:li:dataPlatform:postgres,b2fd91.order_entry_db.order_entry.promotions,PROD)"
DBT_INVENTORIES = "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.inventories,PROD)"
DBT_ORDERS = "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.orders,PROD)"
PG_ORDERS = "urn:li:dataset:(urn:li:dataPlatform:postgres,b2fd91.order_entry_db.order_entry.orders,PROD)"


def load_snapshot() -> dict[str, Dataset]:
    records = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return {r["urn"]: parse_dataset(r) for r in records}


@dataclass
class EvalCase:
    name: str
    expected_approved: bool
    why: str                      # why that label is the right answer
    build: Callable[[dict[str, Dataset]], tuple[Finding, Proposal]]
    synthetic: bool = False


def _one(target: Dataset, pool: list[Dataset], kind: str, tag_name: str | None = None):
    """Run the real detector + remedy path and pick the finding under test.

    Matches on the proposed tag by name rather than by searching the serialized
    evidence: a substring search also hits `current_tags`, which silently
    selects a different finding than the one the case is about."""
    findings = scan_dataset(target, find_twins(target, pool), [kind])
    if tag_name:
        findings = [f for f in findings if f.evidence.get("proposed_tag_name") == tag_name]
    if not findings:
        raise AssertionError(
            f"fixture drift: no {kind} finding"
            f"{' proposing ' + tag_name if tag_name else ''} for {target.urn}"
        )
    if len(findings) > 1:
        raise AssertionError(f"ambiguous case: {len(findings)} {kind} findings for {target.urn}")
    finding = findings[0]
    return finding, propose(finding, model="unused-for-propagation")


def _undo_applied_tags(ds: Dataset, *tag_leaves: str) -> Dataset:
    """Restore a dataset to its pre-remediation state.

    The snapshot was captured after Steward had already propagated PII_Data and
    ecommerce onto this copy, so the drift these cases are about no longer
    appears in the fixture. Dropping those tags reconstructs the catalog as it
    stood before the fix, which is the state the eval is meant to score."""
    kept = tuple(t for t in ds.tags if t.leaf not in tag_leaves)
    if len(kept) == len(ds.tags):
        raise AssertionError(f"expected {tag_leaves} on {ds.urn}; fixture may have been recaptured")
    return replace(ds, tags=kept)


# ---------------------------------------------------------------- real cases

def _pii_to_snowflake(s):
    target = _undo_applied_tags(s[SNOW_ORDER_DETAILS], "PII_Data", "ecommerce")
    return _one(target, [s[DBT_ORDER_DETAILS], target], "tag_drift", "PII_Data")


def _authoritative_source(s):
    return _one(s[SNOW_ORDER_DETAILS], [s[DBT_ORDER_DETAILS], s[SNOW_ORDER_DETAILS]],
                "tag_drift", "Authoritative Source")


def _no_sample_values(s):
    return _one(s[PG_PROMOTIONS], [s[DBT_PROMOTIONS], s[PG_PROMOTIONS]],
                "tag_drift", "No Sample Values")


def _owners_addresses(s):
    return _one(s[PG_ADDRESSES], [s[DBT_ADDRESSES], s[PG_ADDRESSES]], "missing_owner")


def _domain_countries(s):
    return _one(s[SNOW_COUNTRIES], [s[DBT_COUNTRIES], s[SNOW_COUNTRIES]], "missing_domain")


def _domain_orders(s):
    """The one the judge refused in the field, kept as a labeled case.

    Same proposal shape as `domain_countries`, on a table whose columns are
    obviously commercial. The judge approved the sibling tables and refused this
    one, arguing "Data Platform Team" names a team rather than a business domain
    — a fair observation about the source catalog, but not the question being
    asked. The propagation only claims this copy belongs wherever the governed
    copy already sits; whether that domain is well named is a separate defect
    that a consistency fix should not relitigate, and refusing on those grounds
    would block propagation in any catalog whose domains are team-named."""
    return _one(s[PG_ORDERS], [s[DBT_ORDERS], s[PG_ORDERS]], "missing_domain")


# ----------------------------------------------------------- synthetic cases

def _impostor_twin(s):
    """A twin claim that is arithmetically stated but substantively false.

    `promotions` is told it is the same table as `inventories`, with a high
    overlap asserted in the evidence. The detector would never emit this — the
    column sets share nothing — so it is fabricated to ask a narrower question:
    if the twin matcher were ever fooled, is the judge a second line of defence
    or does it take the overlap number on trust?"""
    finding, proposal = _one(s[PG_ADDRESSES], [s[DBT_ADDRESSES], s[PG_ADDRESSES]], "missing_owner")
    inventories = s[DBT_INVENTORIES]
    promotions = s[PG_PROMOTIONS]
    finding = Finding(
        kind="missing_owner",
        urn=promotions.urn,
        entity_name=promotions.name,
        evidence={
            **finding.evidence,
            "dataset_name": promotions.name,
            "platform": promotions.platform,
            "schema_fields": list(promotions.fields),
            "field_count": len(promotions.fields),
            "twin_urn": inventories.urn,
            "twin_platform": inventories.platform,
            "twin_name": inventories.name,
            "field_overlap": 0.95,
            "shared_fields": 5,
        },
    )
    return finding, propose(finding, model="unused-for-propagation")


def _domain_conflict(s):
    """Two copies of the same table sit in different domains; a third has none.

    There is no principled way to pick, so the correct move is to refuse and
    leave it for a human."""
    dbt = s[DBT_COUNTRIES]
    rival = replace(
        s[DBT_COUNTRIES],
        urn=s[DBT_COUNTRIES].urn.replace("dbt,", "s3,"),
        platform="s3",
        domain_urn="urn:li:domain:b2fd91.d4f24004-fb54-4e3c-8dea-2b7e209230b0",
        domain_name="Sales Analytics",
    )
    target = s[SNOW_COUNTRIES]
    return _one(target, [dbt, rival, target], "missing_domain")


CASES: list[EvalCase] = [
    EvalCase(
        "pii_to_snowflake", True,
        "Identical 55-column set including cust_email and phone_number; PII "
        "classification follows the data, not the platform.",
        _pii_to_snowflake),
    EvalCase(
        "owners_addresses", True,
        "Identical database.schema.table path and column set; ownership is a "
        "property of the table.",
        _owners_addresses),
    EvalCase(
        "domain_countries", True,
        "Same reference table, 4/4 columns; business domain follows the data.",
        _domain_countries),
    EvalCase(
        "domain_orders", True,
        "Identical table path and 15/15 columns. Propagation claims only that "
        "this copy belongs where the governed copy sits; whether the domain is "
        "well named is a separate defect.",
        _domain_orders),
    EvalCase(
        "authoritative_source", False,
        "Designates which copy is the system of record. Applying it to a second "
        "copy asserts both are authoritative.",
        _authoritative_source),
    EvalCase(
        "no_sample_values", False,
        "A profiling/display annotation about one platform's copy, not a fact "
        "about the data.",
        _no_sample_values),
    EvalCase(
        "impostor_twin", False,
        "promotions and inventories are unrelated tables; the asserted overlap "
        "is not supported by the column names shown.",
        _impostor_twin, synthetic=True),
    EvalCase(
        "domain_conflict", False,
        "Copies disagree on the domain and nothing in the evidence resolves it.",
        _domain_conflict, synthetic=True),
]
