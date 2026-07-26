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

## Development

```bash
uv venv && uv pip install -e . pytest
pytest                          # offline tests, no DataHub or API key needed
```

## Reuse disclosure

Built for the [DataHub Agent Hackathon](https://datahub.devpost.com). The judge-gated-write
pattern is adapted from the author's earlier open-source work (Vouch, a Slack GraphRAG agent
with citation judging); all Steward code in this repository was written during the hackathon
submission period. Dependencies: `acryl-datahub` / `mcp-server-datahub` (Apache-2.0),
`anthropic`, `mcp`, `pydantic`.

## License

Apache-2.0 — see [LICENSE](LICENSE).
