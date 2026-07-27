# Steward

**The agent that does the data steward's chores — and shows receipts.**

Steward is a governed metadata-remediation agent for [DataHub](https://datahubproject.io).
It finds metadata debt (undocumented datasets, missing owners, orphaned datasets, tag drift),
proposes a fix grounded in evidence, and puts every proposed write in front of an adversarial
judge before anything touches the catalog. Every decision — approved or rejected — lands in an
append-only JSONL ledger you can audit after the fact.

## How it works

```
DataHub  ──(official MCP server, read tools)──▶  corpus ──▶ twin matching ──▶ detectors
findings ──▶ proposal ─┬─ drafted (Claude writes new text)
                       └─ propagated (value copied from another copy of the same table)
proposal ──▶ judge (Claude, adversarial) ──▶ verdict ──▶ ledger receipt
verdict  ──▶ (only if approved AND --apply) MCP mutation tool ──▶ DataHub
```

Most metadata debt is *relative*. The same logical table lands in the catalog three or four
times — once per platform — and only one copy gets governed; owners, domain and classification
tags stop at the platform boundary. So Steward loads the whole catalog, matches copies of the
same table by name **and** column agreement, and treats a governed twin as the evidence for
fixing the copies that drift from it.

Four properties worth naming:

- **The whole loop runs over DataHub's own MCP server** (`acryldata/mcp-server-datahub`) —
  reads and writes. Mutation tools are only exposed when `TOOLS_IS_MUTATION_ENABLED=true`,
  and Steward only sets that flag for the final apply step of judge-approved fixes.
- **Propagated fixes invent nothing.** No model is asked to pick an owner or a domain; every
  proposed value is copied verbatim from a copy of the same table that already carries it.
  Where there is no grounded candidate, Steward reports the debt and proposes nothing.
- **The proposer and the judge are separate calls with opposing prompts.** For drafted prose
  the judge checks invention against evidence. For propagated metadata it checks something
  harder: whether the facet describes the *table* or only one platform's *copy* of it.
  Ownership, business domain and PII classification follow the data; storage statistics,
  profiling annotations and "this is the authoritative copy" do not.
- **Receipts, not trust.** The ledger records the finding, the evidence, the proposal, the
  verdict with rationale, and the applied outcome — before and after the write.

## Usage

```bash
export DATAHUB_GMS_URL=http://localhost:8080
steward scan                             # find debt (read-only, no API key needed)
steward scan --kinds tag_drift           # one detector at a time
steward fix                              # propose + judge, DRY RUN — nothing is written
steward fix --kinds missing_owner --max-findings 10
STEWARD_MUTATIONS=true steward fix --apply    # perform judge-approved writes
```

Detectors: `missing_description`, `missing_owner`, `missing_domain`, `tag_drift`.

`fix` needs `ANTHROPIC_API_KEY` (or an `ant auth login` profile). Writes require two locks:
the `--apply` flag *and* `STEWARD_MUTATIONS=true`.

## Measuring the gate

A judge-gated agent is only as good as its gate, so the gate is evaluated
separately from the agent. `evals/` replays frozen payloads from DataHub's own
showcase pack through the real detector and remedy code, then scores the judge
against hand-labeled answers — including proposals it is supposed to refuse.

```bash
python -m evals.run_eval --repeats 5      # needs ANTHROPIC_API_KEY, no DataHub
python -m evals.audit_ledger steward-ledger.jsonl
```

Latest run — 8 cases, 5 judgements each:

| | |
|---|---|
| Correct vs label | 7/8 |
| Unanimous across 5 runs | 8/8 |
| Bad writes refused | 4/4 |
| Good writes allowed | 3/4 |
| Mean confidence when right / wrong | 0.88 / 0.61 |

The four refusals are the interesting half: a tag that designates *which copy is
the system of record* (propagating it would claim two copies are authoritative),
a per-platform profiling annotation, a fabricated twin whose claimed column
overlap is not credible given the column names, and two copies that disagree
about their domain with nothing to break the tie.

The one miss is a real disagreement, not a crash: the judge refuses to propagate
a domain named after a team onto an obviously commercial `orders` table, while
accepting the same domain for a reference table. That is a defensible position
about the *source* catalog, but it is not the question the propagation asks, and
it makes the gate hard to predict across sibling tables. Its confidence on that
verdict is 0.61 against 0.88 elsewhere — low confidence is a usable signal for
routing a decision to a human.

Separately, `audit_ledger` checks the property that must hold regardless of the
judge's opinions: every applied write carries an approving verdict for that exact
set of actions. It is what lets the agent prove after the fact, to someone who
does not trust it, that nothing was written behind the gate's back.

## Development

```bash
uv venv && uv pip install -e . pytest
pytest                          # 36 offline tests, no DataHub or API key needed
```

## Reuse disclosure

Built for the [DataHub Agent Hackathon](https://datahub.devpost.com). The judge-gated-write
pattern is adapted from the author's earlier open-source work (Vouch, a Slack GraphRAG agent
with citation judging); all Steward code in this repository was written during the hackathon
submission period. Dependencies: `acryl-datahub` / `mcp-server-datahub` (Apache-2.0),
`anthropic`, `mcp`, `pydantic`.

## License

Apache-2.0 — see [LICENSE](LICENSE).
