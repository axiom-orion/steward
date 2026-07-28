# Steward — Devpost submission copy

Paste-ready. Each `##` maps to a field on the Devpost form.
**Draft-save the form the moment you open it, then edit forever.**

---

## Project name

Steward

## Elevator pitch (one line, ~200 char limit)

The agent that does the data steward's chores — and shows receipts. It finds metadata debt in DataHub, proposes fixes grounded in your own catalog, and puts every write past an adversarial judge.

---

## About the project

### Inspiration

Metadata debt is usually described as an absolute: this dataset has no description, that
one has no owner. Working in DataHub's own showcase catalog, the more useful framing turned
out to be that **debt is relative**. The same logical table lands in the catalog three or
four times — once each for dbt, Snowflake, Postgres, S3, Looker, Power BI — and only one
copy ever gets governed. Owners, business domains, and PII classification stop dead at the
platform boundary.

That reframe changes what an agent is even for. If the catalog already knows the answer on
one copy, an agent should not be asking a language model to invent one for the others. It
should be propagating what is already there — and the hard question stops being "what is
the right value" and becomes "**does this fact describe the table, or only this platform's
copy of it?**"

That question is the whole project.

### What it does

Steward runs a four-stage loop entirely over DataHub's own MCP server:

1. **Scan** — loads the full catalog, matches "twins" (copies of the same logical table) by
   logical name plus ≥0.8 column overlap, and runs four detectors: `missing_description`,
   `missing_owner`, `missing_domain`, `tag_drift`.
2. **Propose** — either *drafted* (Claude writes new prose) or *propagated* (a value copied
   verbatim from a governed twin). Propagated fixes invent nothing: no model is ever asked
   to pick an owner or a domain. Where there is no grounded candidate, Steward reports the
   debt and proposes nothing.
3. **Judge** — a separate Claude call with an opposing prompt. For drafted prose it checks
   invention against evidence. For propagated metadata it checks portability: ownership,
   business domain, and PII classification follow the data; storage statistics, profiling
   annotations, and "this is the authoritative copy" do not.
4. **Apply** — only judge-approved writes, only behind two locks (`--apply` *and*
   `STEWARD_MUTATIONS=true`). Every decision, approved or refused, lands in an append-only
   JSONL ledger.

Against DataHub's showcase pack it found and fixed a real compliance gap: the `PII_Data`
classification existed on the dbt copies of `cust_email` and `phone_number` and was missing
from the Snowflake, Power BI, and two Looker copies of the same 55 columns.

**The refusals are the more interesting half.** Un-prompted and un-designed-for, the judge
declined to propagate an `Authoritative Source` tag, reasoning that the dbt copy being the
authoritative source is *precisely a statement that the Looker copy is not*. It also refused
a per-platform profiling annotation despite a 1.00 column match.

### How I built it

Python, over `acryldata/mcp-server-datahub`. Claude (`claude-opus-5`) via structured outputs
for both the drafter and the judge. The catalog is read through the MCP server's read tools;
writes go through its mutation tools, which only exist when `TOOLS_IS_MUTATION_ENABLED=true`
— a flag Steward sets only for the final apply step of an approved fix.

**The confidence floor is two-sided, and that was the design decision worth the most.**
Verdicts below 0.70 are *held* rather than acted on — in **either** direction. Gating only
approvals is the obvious build and it is wrong: on the labeled eval set, the single case the
judge got wrong was a low-confidence *refusal*, which a one-sided floor waves straight
through as a safe-looking "no" while quietly leaving real debt unfixed. That is the failure
mode nobody notices, because nothing visibly bad happens.

### Challenges I ran into

**The agent was silently blind to most of the catalog.** The MCP server's `search` tool
clamps `num_results` to 50 with no error and no warning, and an unfiltered `*` query burns
that page on dataProducts, containers, and corpusers. Every scan before I caught this was
seeing **19 of 69 datasets** — while confidently reporting "0 findings remaining." Fixed
locally with an entity-type filter and real pagination; the upstream silent truncation
became the OSS contribution below.

**A held decision can carry `approved: true`.** Authorization has to key off the
*disposition*, not the raw verdict — otherwise a held-then-applied write audits perfectly
clean. Caught in the ledger auditor, not in the agent.

**Structured-output calls need token headroom.** `max_tokens=2000` plus adaptive thinking
truncated a long verdict mid-JSON and killed a whole run. The real fix wasn't the token
bump: an unparseable verdict now raises `JudgeError` and fails closed, instead of being
silently read as a refusal the model never issued. *"The gate said no"* and *"the gate never
answered"* must not be the same code path.

### Accomplishments I'm proud of

A judge-gated agent is only as good as its gate, so the gate is measured separately from the
agent. `evals/` replays frozen catalog payloads through the *real* detector and remedy code
and scores the judge against hand labels — including proposals it is supposed to refuse.
Latest run, 8 cases × 5 judgements each:

| | |
|---|---|
| Correct vs. label | 7/8 |
| Unanimous across 5 runs | 8/8 |
| Bad writes refused | 4/4 |
| Good writes allowed | 3/4 |
| Mean confidence when right / wrong | 0.88 / 0.61 |

And the property that has to hold regardless of the judge's opinions — verified by
`evals/audit_ledger.py` against the live ledger:

> **12 writes, every one carrying an approving verdict for that exact set of actions.
> 0 violations.**

That is what lets the agent prove, after the fact, to someone who does not trust it, that
nothing was written behind the gate's back.

**And you can check all of it without credentials.** The tests, the eval harness, and the
ledger audit run with **no DataHub instance and no API key**:

```bash
pytest                                             # 44 offline tests — no DataHub, no API key
python -m evals.audit_ledger steward-ledger.jsonl  # replays the shipped ledger, checks the invariant
```

The ledger is committed to the repo — 72 real decision records — so that second command
reproduces the invariant check on a fresh clone, against the same data this writeup cites.

`steward scan` is read-only and needs no key either. Only `fix` calls a model, and only
`--apply` **and** `STEWARD_MUTATIONS=true` together can write anything to a catalog.

For a project whose entire thesis is *receipts, not trust*, that seemed like the minimum bar:
a judge should not have to take this writeup's word for any of it.

### What I learned

**The judge audits your evidence, not just your proposal.** It objected once that a write's
domain URN could not be matched to the stated basis — and it was right about the evidence,
even though the URNs actually agreed, because only display names were being rendered.
Rendering URNs alongside names then *stabilised* a case that had been flip-flopping across
runs. The gate was not being difficult; the evidence was genuinely ambiguous.

**Confidence turned out to be a better routing signal than a second opinion.** The eval
showed confidence already separates right from wrong (0.88 vs 0.61), so a floor does the
work of a multi-judge vote at a third of the cost.

**Capture eval fixtures before applying fixes.** I snapshotted after remediation, so the
drift under test no longer existed and a substring matcher quietly picked the wrong finding.

### What's next

More detectors (lineage gaps, stale ownership), a human review queue UI on top of
`steward review`, and pushing the two-sided-floor pattern back upstream as a reusable
governance layer for other DataHub agents.

---

## Built with

`python` · `datahub` · `mcp` · `anthropic` · `claude` · `pydantic` · `docker` · `apache-2.0`

## Try it out

- **Repo:** https://github.com/axiom-orion/steward (Apache-2.0, LICENSE at top level)
- **OSS contribution:** https://github.com/acryldata/mcp-server-datahub/pull/148

**Judges: you can verify this without standing up DataHub and without an API key.**

```bash
uv venv && uv pip install -e . pytest
pytest                                             # 44 offline tests
python -m evals.audit_ledger steward-ledger.jsonl  # ledger invariant, against the shipped ledger
```

Add `DATAHUB_GMS_URL` and run `steward scan` for a read-only pass over a live catalog;
`ANTHROPIC_API_KEY` is only needed for `steward fix`.

---

## Hackathon-required fields

| Field | Value |
|---|---|
| **Category / track** | Category 1 — *Agents That Do Real Work* |
| **Public repo** | Required, Apache-2.0, LICENSE at top level ✅ |
| **Demo video** | Strictly under 3:00, public, sponsor tech named on screen |
| **OSS contribution** | acryldata/mcp-server-datahub **PR #148** — search tool silently clamps `num_results` to 50 |

### On PR #148 (worth its own sentence in the form)

The fix applies an in-repo convention rather than inventing one: `search_documents`
**already** emits `_hybridSearchLimitReached` when it hits its own cap. The `search` tool
does not. That is the whole argument.

---

## Pre-submission checklist

- [ ] Devpost form **draft-saved** (do this first, empty if necessary)
- [x] Repo public with Apache-2.0 LICENSE at top level — https://github.com/axiom-orion/steward
- [x] Test count verified: **44 passing**, offline, in the `~/dhenv` WSL venv (README's "36" was stale and is corrected)
- [x] Ledger committed so `audit_ledger` reproduces on a fresh clone
- [ ] Video recorded, under 3:00, public link
- [ ] Live demo reachable by judges for the full judging window
- [ ] Submitted well before **Aug 10, 5:00 PM ET** — not at 4:59
