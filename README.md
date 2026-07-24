# Steward

**The agent that does the data steward's chores — and shows receipts.**

Steward is a governed metadata-remediation agent for [DataHub](https://datahubproject.io).
It finds metadata debt (undocumented datasets, missing owners, tag drift), drafts a fix from
evidence, and puts every proposed write in front of an adversarial judge before anything
touches the catalog. Every decision — approved or rejected — lands in an append-only JSONL
ledger you can audit after the fact.

## How it works

```
DataHub  ──(official MCP server, read tools)──▶  detectors  ──▶  findings + evidence
findings ──▶ drafter (Claude) ──▶ proposal ──▶ judge (Claude, adversarial) ──▶ verdict
verdict  ──▶ ledger receipt  ──▶ (only if approved AND --apply) MCP mutation tool ──▶ DataHub
```

Three properties worth naming:

- **The whole loop runs over DataHub's own MCP server** (`acryldata/mcp-server-datahub`) —
  reads and writes. Mutation tools are only exposed when `TOOLS_IS_MUTATION_ENABLED=true`,
  and Steward only sets that flag for the final apply step of judge-approved fixes.
- **The drafter and the judge are separate calls with opposing prompts.** The drafter must
  ground every claim in evidence; the judge is instructed to reject anything the evidence
  doesn't support. A vague-but-true description beats a specific-but-unsupported one.
- **Receipts, not trust.** The ledger records the finding, the evidence, the proposal, the
  verdict with rationale, and the applied outcome — before and after the write.

## Usage

```bash
export DATAHUB_GMS_URL=http://localhost:8080
steward scan                    # find debt (read-only)
steward fix                     # draft + judge, DRY RUN — nothing is written
STEWARD_MUTATIONS=true steward fix --apply   # perform judge-approved writes
```

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
