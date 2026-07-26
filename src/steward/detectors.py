"""Debt detectors — pure functions over the corpus, so they test offline.

A Finding is a claim that a specific entity has a specific, fixable defect,
carrying the evidence that supports the claim. Detectors never decide the fix;
they only surface debt, and they attach the evidence a remedy would have to
rest on. The remedy proposes, the judge disposes.

Two shapes of detector live here:

  * *absence* — the dataset is missing something outright (no description).
  * *drift*   — another copy of the same table has something this one lacks.
                The twin is the evidence, and it is what keeps the remedy
                honest: propagation can only ever copy a value that a copy of
                this same table already carries.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable

from .corpus import Dataset, Twin, twin_evidence

KINDS = ("missing_description", "missing_owner", "missing_domain", "tag_drift")


@dataclass
class Finding:
    kind: str            # e.g. "missing_description"
    urn: str
    entity_name: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _base_evidence(ds: Dataset) -> dict:
    return {
        "platform": ds.platform,
        "dataset_name": ds.name,
        "schema_fields": list(ds.fields[:40]),
        "field_count": len(ds.fields),
    }


def detect_missing_description(ds: Dataset, twins: Iterable[Twin] = ()) -> Finding | None:
    """A dataset with no description is documentation debt."""
    if ds.description.strip():
        return None
    return Finding(
        kind="missing_description",
        urn=ds.urn,
        entity_name=ds.name,
        evidence=_base_evidence(ds),
    )


def detect_missing_owner(ds: Dataset, twins: Iterable[Twin]) -> Finding | None:
    """Nobody owns this dataset, but somebody owns another copy of the same table.

    Un-owned data is the debt every catalog has most of; the fix is only safe
    when the catalog itself already says who owns this table somewhere else."""
    if ds.owners:
        return None
    with_owners = [t for t in twins if t.dataset.owners]
    if not with_owners:
        return None   # no grounded candidate — Steward will not guess an owner

    best = with_owners[0]
    owner_sets = {frozenset(o.urn for o in t.dataset.owners) for t in with_owners}
    evidence = {
        **_base_evidence(ds),
        **twin_evidence(best),
        "proposed_owners": [
            {
                "urn": o.urn,
                "display": o.display,
                "ownership_type_urn": o.ownership_type_urn,
                "ownership_type_name": o.ownership_type_name,
            }
            for o in best.dataset.owners
        ],
        "other_twins_with_owners": [
            twin_evidence(t) | {"owners": [o.urn for o in t.dataset.owners]}
            for t in with_owners[1:]
        ],
        "twins_disagree": len(owner_sets) > 1,
    }
    return Finding(kind="missing_owner", urn=ds.urn, entity_name=ds.name, evidence=evidence)


def detect_missing_domain(ds: Dataset, twins: Iterable[Twin]) -> Finding | None:
    """This copy sits in no domain while another copy of the same table does.

    Note for the remedy: set_domains replaces rather than merges, so this only
    ever fires on a dataset with no domain at all — nothing to overwrite."""
    if ds.domain_urn:
        return None
    with_domain = [t for t in twins if t.dataset.domain_urn]
    if not with_domain:
        return None

    best = with_domain[0]
    distinct = {t.dataset.domain_urn for t in with_domain}
    evidence = {
        **_base_evidence(ds),
        **twin_evidence(best),
        "proposed_domain_urn": best.dataset.domain_urn,
        "proposed_domain_name": best.dataset.domain_name,
        "other_twin_domains": sorted(d for d in distinct if d != best.dataset.domain_urn),
        "twins_disagree": len(distinct) > 1,
    }
    return Finding(kind="missing_domain", urn=ds.urn, entity_name=ds.name, evidence=evidence)


def detect_tag_drift(ds: Dataset, twins: Iterable[Twin]) -> list[Finding]:
    """Governance tags that stop at a platform boundary.

    One finding per missing tag, deliberately: a classification like PII and a
    profiling annotation like "No Sample Values" can sit side by side on the
    same twin, and only one of them describes the *table* rather than one
    platform's copy of it. Judged separately, they can be decided differently.

    Tags DataHub's own ingestion generates (platform statistics, BI column
    roles) are filtered out upstream in Dataset.governance_tags — they are
    facts about one system, and propagating them would be fabrication."""
    mine = {t.urn for t in ds.tags}
    findings: list[Finding] = []
    seen: set[str] = set()

    for twin in twins:
        for tag in twin.dataset.governance_tags:
            if tag.urn in mine or tag.urn in seen:
                continue
            seen.add(tag.urn)
            findings.append(Finding(
                kind="tag_drift",
                urn=ds.urn,
                entity_name=ds.name,
                evidence={
                    **_base_evidence(ds),
                    **twin_evidence(twin),
                    "proposed_tag_urn": tag.urn,
                    "proposed_tag_name": tag.name,
                    "proposed_tag_description": tag.description[:600],
                    "current_tags": [t.name for t in ds.tags],
                },
            ))
    return findings


_SINGLE = {
    "missing_description": detect_missing_description,
    "missing_owner": detect_missing_owner,
    "missing_domain": detect_missing_domain,
}


def scan_dataset(ds: Dataset, twins: list[Twin], kinds: Iterable[str] = KINDS) -> list[Finding]:
    kinds = set(kinds)
    findings: list[Finding] = []
    for kind, detector in _SINGLE.items():
        if kind in kinds:
            found = detector(ds, twins)
            if found:
                findings.append(found)
    if "tag_drift" in kinds:
        findings.extend(detect_tag_drift(ds, twins))
    return findings
