"""What to do with a verdict — which is not the same as what the verdict says.

The judge returns an opinion and a confidence. Policy decides whether that
opinion is strong enough to act on unsupervised. Keeping the two apart means the
gate's reasoning and the operator's risk tolerance can be tuned separately, and
the ledger records both.

The floor is deliberately **two-sided**. Gating only approvals is the obvious
design and it is the wrong one: on the labeled eval set the single case the
judge got wrong was a low-confidence *refusal* — it declined to propagate a
team-named domain onto a commercial table, at 0.61 against 0.88 everywhere else.
A one-sided floor would have waved that through as a safe "no" and quietly left
real debt unfixed, which is the failure mode nobody notices. An unconfident
refusal is an unresolved question, not a decision.

Measured on the eval set: confidence separates the judge's right answers from
its wrong one (0.88 vs 0.61), which is what makes a floor worth having here
rather than a multi-judge vote — same decision, a third of the cost.
"""

from dataclasses import dataclass

APPLY = "apply"
REFUSE = "refuse"
HOLD = "hold"

DEFAULT_FLOOR = 0.7


@dataclass(frozen=True)
class Disposition:
    action: str          # APPLY | REFUSE | HOLD
    reason: str

    @property
    def held(self) -> bool:
        return self.action == HOLD


def decide(approved: bool, confidence: float, floor: float = DEFAULT_FLOOR) -> Disposition:
    """Turn a verdict into an action.

    A floor of 0 disables holding entirely and restores raw judge behaviour."""
    if floor > 0 and confidence < floor:
        verdict_word = "approval" if approved else "refusal"
        return Disposition(
            HOLD,
            f"{verdict_word} at confidence {confidence:.2f} is below the {floor:.2f} floor; "
            "left for a human",
        )
    if approved:
        return Disposition(APPLY, f"approved at confidence {confidence:.2f}")
    return Disposition(REFUSE, f"refused at confidence {confidence:.2f}")
