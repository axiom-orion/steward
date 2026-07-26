"""From a Finding to a concrete, reviewable write.

A Proposal is the exact set of MCP tool calls Steward would make, decided
*before* any judging and never mutated afterwards — what the judge reads is
what the writer executes.

Two origins, and the difference matters for how much the judge has to trust:

  * **drafted** — a model wrote new text (descriptions). The judge is checking
    invention against evidence.
  * **propagated** — the value is copied verbatim from another copy of the same
    table. Nothing is invented, so the judge is checking one thing only: is the
    twin really the same table, and does this facet belong to the table rather
    than to one platform's copy of it?

Propagated proposals are built deterministically here, not by a model. Asking a
model to pick an owner invites it to invent one; the catalog already knows.
"""

from dataclasses import dataclass, field
from typing import Any

from .detectors import Finding

DEFAULT_OWNERSHIP_TYPE = "TECHNICAL_OWNER"


@dataclass
class Action:
    """One MCP tool call, exactly as it will be made."""
    tool: str
    args: dict[str, Any]


@dataclass
class Proposal:
    summary: str
    actions: list[Action]
    basis: list[str] = field(default_factory=list)
    origin: str = "propagated"      # "propagated" | "drafted"

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "origin": self.origin,
            "basis": self.basis,
            "actions": [{"tool": a.tool, "args": a.args} for a in self.actions],
        }


def _owner_proposal(f: Finding) -> Proposal:
    """add_owners takes a single ownership type per call, so owners are grouped
    by the type their twin recorded — a Technical Owner does not silently become
    a Business Owner in transit."""
    ev = f.evidence
    by_type: dict[str, list[dict]] = {}
    defaulted = False
    for owner in ev.get("proposed_owners", []):
        type_urn = owner.get("ownership_type_urn")
        if not type_urn:
            type_urn = DEFAULT_OWNERSHIP_TYPE
            defaulted = True
        by_type.setdefault(type_urn, []).append(owner)

    actions = [
        Action(tool="add_owners", args={
            "owner_urns": [o["urn"] for o in owners],
            "entity_urns": [f.urn],
            "ownership_type": type_urn,
        })
        for type_urn, owners in sorted(by_type.items())
    ]

    names = ", ".join(
        f"{o['display']} ({o.get('ownership_type_name') or 'Technical Owner'})"
        for o in ev.get("proposed_owners", [])
    )
    basis = [
        f"{ev['twin_platform']} copy {ev['twin_urn']} shares {ev['shared_fields']} of "
        f"this dataset's {ev['field_count']} columns (overlap {ev['field_overlap']}) and is owned by these principals.",
    ]
    if defaulted:
        basis.append(
            "At least one owner carries no ownership type on the twin; those are proposed "
            f"as {DEFAULT_OWNERSHIP_TYPE}."
        )
    if ev.get("twins_disagree"):
        basis.append("Other copies of this table list a different owner set — the twins disagree.")

    return Proposal(
        summary=f"Assign owners from the {ev['twin_platform']} copy: {names}",
        actions=actions,
        basis=basis,
    )


def _domain_proposal(f: Finding) -> Proposal:
    ev = f.evidence
    basis = [
        f"{ev['twin_platform']} copy {ev['twin_urn']} shares {ev['shared_fields']} columns "
        f"(overlap {ev['field_overlap']}) and belongs to domain "
        f"{ev.get('proposed_domain_name') or ev['proposed_domain_urn']}.",
        "This dataset currently has no domain, so nothing is overwritten.",
    ]
    if ev.get("twins_disagree"):
        basis.append(f"Other copies sit in different domains: {ev.get('other_twin_domains')}.")
    return Proposal(
        summary=f"Set domain to {ev.get('proposed_domain_name') or ev['proposed_domain_urn']}",
        actions=[Action(tool="set_domains", args={
            "domain_urn": ev["proposed_domain_urn"],
            "entity_urns": [f.urn],
        })],
        basis=basis,
    )


def _tag_proposal(f: Finding) -> Proposal:
    ev = f.evidence
    return Proposal(
        summary=f"Apply tag {ev['proposed_tag_name']} (carried by the {ev['twin_platform']} copy)",
        actions=[Action(tool="add_tags", args={
            "tag_urns": [ev["proposed_tag_urn"]],
            "entity_urns": [f.urn],
        })],
        basis=[
            f"{ev['twin_platform']} copy {ev['twin_urn']} shares {ev['shared_fields']} columns "
            f"(overlap {ev['field_overlap']}) and carries this tag.",
            f"Tag definition: {ev.get('proposed_tag_description') or '(no description)'}",
            f"Tags already on this dataset: {ev.get('current_tags') or 'none'}",
        ],
    )


def _description_proposal(f: Finding, model: str) -> Proposal:
    from .judge import draft_description      # imported late so scan needs no API key

    draft = draft_description(f, model)
    return Proposal(
        summary=draft.description,
        actions=[Action(tool="update_description", args={
            "entity_urn": f.urn,
            "operation": "replace",
            "description": draft.description,
        })],
        basis=draft.basis,
        origin="drafted",
    )


def propose(f: Finding, model: str) -> Proposal:
    if f.kind == "missing_description":
        return _description_proposal(f, model)
    if f.kind == "missing_owner":
        return _owner_proposal(f)
    if f.kind == "missing_domain":
        return _domain_proposal(f)
    if f.kind == "tag_drift":
        return _tag_proposal(f)
    raise ValueError(f"no remedy for finding kind {f.kind!r}")
