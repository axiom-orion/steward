"""Drafter + judge — the gate in front of every write.

The drafter writes new text where text has to be invented (descriptions). The
judge — always a separate call, with an adversarial prompt — approves only what
the evidence already supports. Nothing reaches a mutation tool without an
approving verdict, and every verdict lands in the ledger, approved or not.

The judge is asked a different question depending on where the proposal came
from. For drafted prose the risk is invention, so it checks each claim against
the evidence. For propagated metadata nothing is invented — the risk is that the
value belongs to one platform's copy rather than to the table, so it checks
whether the twin is really the same table and whether the facet travels with it.
That second question is where a catalog gets quietly corrupted: a storage-size
statistic or a profiling annotation copied across platforms looks like diligent
governance and is simply false.
"""

from anthropic import Anthropic
from pydantic import BaseModel, Field

from .detectors import Finding
from .remedy import Proposal

_client: Anthropic | None = None


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


class Draft(BaseModel):
    description: str = Field(description="Proposed dataset description, 1-3 sentences.")
    basis: list[str] = Field(description="Which pieces of evidence each claim rests on.")


class Verdict(BaseModel):
    approved: bool
    rationale: str = Field(description="Why the fix is or is not safe to apply.")
    confidence: float = Field(ge=0, le=1)
    unsupported_claims: list[str] = Field(
        default_factory=list,
        description="Statements in the proposal not supported by the evidence.",
    )


DRAFTER_SYSTEM = """You draft metadata fixes for a data catalog. You are given evidence about \
a dataset (its name, platform, and schema fields) and must propose a description. HARD RULE: \
every claim must be derivable from the evidence given. Describe what the dataset contains based \
on its name and fields; never invent business context, owners, update frequency, or lineage you \
were not shown. Plain prose, no markdown."""

DRAFT_JUDGE_SYSTEM = """You are the approval gate for automated writes to a production data catalog. \
You are given the evidence and a proposed description written by a different model. Approve ONLY \
if every statement in the proposal is supported by the evidence. Reject speculation, invented \
business context, or claims about data quality, freshness, or ownership that the evidence does \
not show. A vague-but-true description beats a specific-but-unsupported one. When uncertain, \
reject — an unfixed gap is recoverable, a wrong description that looks authoritative is not."""

PROPAGATION_JUDGE_SYSTEM = """You are the approval gate for automated writes to a production data \
catalog. The proposal copies metadata from one dataset to another because both appear to be copies \
of the same logical table on different platforms. Nothing has been invented — the value already \
exists on the other copy — so judge exactly two things.

FIRST: is the twin really the same logical table? You are given the shared column count and the \
overlap ratio. Matching names with unrelated columns are not the same table. Generic names \
(events, data, temp) matching on generic columns (id, created_at) are weak evidence.

SECOND, and more important: does this metadata describe the TABLE, or only that one platform's \
COPY of it? Propagate things that are true of the data itself — who owns it, what business domain \
it belongs to, classifications such as PII or sensitivity that follow the columns. Do NOT propagate \
things that are measurements of one system: storage size, query volume, row counts, sampling or \
profiling annotations, freshness or SLA state, platform-specific lifecycle status. Those are facts \
about a copy, and asserting them about a different platform's copy is a fabrication that will look \
like diligent governance to everyone who reads it afterwards.

Also reject when the copies disagree with each other and the evidence gives you no principled way \
to choose. When uncertain, reject — an unfixed gap is recoverable; confidently wrong governance \
metadata is not."""


def _render_evidence(finding: Finding) -> str:
    ev = finding.evidence
    lines = [
        f"Dataset: {finding.entity_name}",
        f"URN: {finding.urn}",
        f"Platform: {ev.get('platform', 'unknown')}",
    ]
    fields = ev.get("schema_fields") or []
    shown = ", ".join(fields[:25]) or "(no schema fields returned)"
    more = f" (+{ev['field_count'] - 25} more)" if ev.get("field_count", 0) > 25 else ""
    lines.append(f"Columns ({ev.get('field_count', len(fields))}): {shown}{more}")

    if ev.get("twin_urn"):
        lines += [
            "",
            f"Twin considered: {ev['twin_name']} on {ev['twin_platform']}",
            f"  urn: {ev['twin_urn']}",
            f"  shares {ev['shared_fields']} columns, overlap ratio {ev['field_overlap']}",
        ]
    for key, label in (
        ("proposed_owners", "Owners on the twin"),
        ("proposed_domain_name", "Domain on the twin"),
        ("proposed_tag_name", "Tag on the twin"),
        ("proposed_tag_description", "Tag definition"),
        ("current_tags", "Tags already on this dataset"),
        ("other_twins_with_owners", "Other copies with owners"),
        ("other_twin_domains", "Domains on other copies"),
    ):
        if ev.get(key):
            lines.append(f"{label}: {ev[key]}")
    if ev.get("twins_disagree"):
        lines.append("WARNING: copies of this table disagree on this facet.")
    return "\n".join(lines)


def draft_description(finding: Finding, model: str) -> Draft:
    response = client().messages.parse(
        model=model,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        system=DRAFTER_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Evidence:\n{_render_evidence(finding)}\n\nPropose a description for this dataset.",
        }],
        output_format=Draft,
    )
    return response.parsed_output


def judge(finding: Finding, proposal: Proposal, model: str) -> Verdict:
    system = DRAFT_JUDGE_SYSTEM if proposal.origin == "drafted" else PROPAGATION_JUDGE_SYSTEM
    writes = "\n".join(f"  {a.tool}({a.args})" for a in proposal.actions)
    response = client().messages.parse(
        model=model,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{
            "role": "user",
            "content": (
                f"Finding: {finding.kind}\n\n"
                f"Evidence:\n{_render_evidence(finding)}\n\n"
                f"Proposal: {proposal.summary}\n"
                f"Stated basis: {proposal.basis}\n\n"
                f"Exact writes that will be executed if you approve:\n{writes}\n\n"
                "Should these writes be applied?"
            ),
        }],
        output_format=Verdict,
    )
    return response.parsed_output
