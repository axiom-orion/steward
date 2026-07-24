"""Drafter + judge — the two-model gate in front of every write.

The drafter proposes a fix from the evidence. The judge — a separate call with
an adversarial prompt — approves only fixes that are fully grounded in that
same evidence. Nothing reaches a mutation tool without an approving verdict,
and every verdict lands in the ledger, approved or not.
"""

from anthropic import Anthropic
from pydantic import BaseModel, Field

from .detectors import Finding

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

JUDGE_SYSTEM = """You are the approval gate for automated writes to a production data catalog. \
You are given the evidence and a proposed description written by a different model. Approve ONLY \
if every statement in the proposal is supported by the evidence. Reject speculation, invented \
business context, or claims about data quality, freshness, or ownership that the evidence does \
not show. A vague-but-true description beats a specific-but-unsupported one. When uncertain, \
reject — an unfixed gap is recoverable, a wrong description that looks authoritative is not."""


def _evidence_text(finding: Finding) -> str:
    fields = ", ".join(finding.evidence.get("schema_fields", [])) or "(no schema fields returned)"
    return (
        f"Dataset name: {finding.entity_name}\n"
        f"URN: {finding.urn}\n"
        f"Platform: {finding.evidence.get('platform', 'unknown')}\n"
        f"Schema fields: {fields}"
    )


def draft_fix(finding: Finding, model: str) -> Draft:
    response = client().messages.parse(
        model=model,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        system=DRAFTER_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Evidence:\n{_evidence_text(finding)}\n\nPropose a description for this dataset.",
        }],
        output_format=Draft,
    )
    return response.parsed_output


def judge_fix(finding: Finding, draft: Draft, model: str) -> Verdict:
    response = client().messages.parse(
        model=model,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        system=JUDGE_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Evidence:\n{_evidence_text(finding)}\n\n"
                f"Proposed description:\n{draft.description}\n\n"
                f"Drafter's claimed basis: {draft.basis}\n\n"
                "Should this write be applied?"
            ),
        }],
        output_format=Verdict,
    )
    return response.parsed_output
