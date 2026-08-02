# Steward — demo video script

**Hard limit: under 3:00** (DataHub rule; 3:05 is a violation). Target **2:45**.
Public on YouTube or Vimeo. Must show the project functioning.

Doctrine from the winner-gallery research: overview in the first 20 seconds,
sponsor tech named *on screen*, one quantified metric on screen, native surfaces
over chat. 60/40 explain to demo.

Narration below is ~415 words ≈ 2:45 at a normal speaking pace. Read it slightly
slower than feels right.

---

## Shot list

| # | Time | Shot | On-screen text |
|---|---|---|---|
| 1 | 0:00–0:22 | DataHub UI, dbt `order_details`, tags panel showing **PII_Data** → cut to Snowflake `ORDER_DETAILS`, same columns, no PII tag | `DataHub's own sample catalog` |
| 2 | 0:22–0:40 | Split or quick cuts: the same table on dbt / Snowflake / Postgres / Looker / PowerBI | `One table. Five copies. One of them governed.` |
| 3 | 0:40–1:02 | Terminal: `steward scan`, output scrolling to the summary block | `steward scan` · `DataHub MCP Server` |
| 4 | 1:02–1:52 | Terminal: `steward fix --kinds tag_drift` (dry run). Hold on the two REJECTED lines; highlight the judge rationale | `The judge refuses` |
| 5 | 1:52–2:14 | `--apply` scrolling, then DataHub UI refreshed: Snowflake / PowerBI / Looker now carrying PII_Data | `before → after` |
| 6 | 2:14–2:33 | Terminal: the `[HELD 0.62]` line, then `steward review` | `Not sure ⇒ don't decide` |
| 7 | 2:33–2:50 | Metrics card, then the PR page | `7/8 · 12/12 writes authorized · 0 violations` |

---

## Narration

**[0:00 — shot 1]**
This is DataHub's own sample catalog. The dbt copy of `order_details` is tagged
PII. Here's the Snowflake copy — the same fifty-five columns, the same
`cust_email`, the same `phone_number`. No PII tag. The classification stopped at
the platform boundary.

**[0:22 — shot 2]**
That's what most metadata debt actually looks like. It isn't absolute, it's
relative: the same logical table lands in the catalog four or five times, and
only one copy gets governed. Owners, domains, and classifications don't
propagate.

**[0:40 — shot 3]**
Steward is an agent that finds that drift and closes it. It reads the catalog
through DataHub's own MCP server, matches copies of the same table by name and
column agreement, and treats a governed twin as evidence. One command: sixty-nine
datasets, a hundred and eight findings — twenty-two with no owner, twenty-four
with no domain, thirteen tags that stop at a boundary.

Nothing here is invented. No model is asked to pick an owner. Every proposed
value is copied from a copy of the same table that already carries it.

**[1:02 — shot 4]**
So the judge's job isn't "is this made up" — it's "does this belong to the table,
or only to one platform's copy of it." Watch what it refuses.

`Authoritative Source` — refused. The dbt copy being the system of record is
precisely a statement that the Snowflake copy is *not*. Copying it would claim
two authoritative copies of one table.

`No Sample Values` — refused. That's a profiling annotation about one platform,
not a fact about the data. Identical columns, hundred-percent match, refused
anyway.

**[1:52 — shot 5]**
Approved writes go through the MCP server's mutation tools — which only exist
when mutations are explicitly enabled. Snowflake, PowerBI, and both Looker copies
now carry the PII classification. Real writes, in the UI.

**[2:14 — shot 6]**
And when it isn't sure, it doesn't decide. This refusal came back at
point-six-two confidence, under the floor — so it's held for a human, with the
reasoning attached, instead of quietly becoming a "no".

**[2:33 — shot 7]**
Eight labeled cases, five judgements each: seven of eight correct, unanimous
every time. Twelve writes applied, every one carrying an approving verdict —
provable from the shipped ledger on a fresh clone, with no DataHub and no API
key. Building it also surfaced a bug in the MCP server itself: `search` silently
caps at fifty results. That's an open PR upstream.

---

## Recording notes

**Re-arm the demo first — the beat is one-shot.** Applying the fixes destroys the
"before" state; once PII_Data is on the Snowflake copy the whole opening no
longer exists. Reset with:

```bash
STEWARD_MUTATIONS=true python -m ops.rearm_demo --apply   # dry run without --apply
steward scan --kinds tag_drift                            # expect 13 findings
```

Expected baseline before recording: **13 tag_drift findings**, and Snowflake
`ORDER_DETAILS` showing only `Large Table` / `Most Queried`.

**Stack must be fully up, and it lies about being up.**
- GMS reports `unhealthy` and `/health` 503s for minutes while draining Kafka —
  poll `POST /api/graphql` instead and wait for a real answer.
- OpenSearch has exited on its own twice. `search` fails while entity reads keep
  working, which looks like a Steward bug and isn't. Check
  `curl localhost:9200/_cluster/health` — it must not be `red`; recovery from a
  cold start took several minutes and 180 shards.
- Both outages coincided with the CockroachDB cluster for the other hackathon
  running. Don't record with `roach1-3` up.

**Pacing.** A real `fix` run costs ~8.6s per finding — the tag_drift pass is
about two minutes of wall clock. Speed-ramp the scrolling in the edit; do not
fake output. Hold full speed on the two REJECTED lines, which is the point of the
whole video.

**Trademarks.** The platform names (Snowflake, Looker, PowerBI, Tableau) come
from DataHub's own showcase datapack and appear as catalog metadata, not as
branding. No third-party music. The Vouch trademark lesson applies to *invented*
data — this is the sponsor's sample pack, which is fine.

**Metrics card (shot 7)** — put these on screen, they are all reproducible:

```
eval          7/8 correct · 8/8 unanimous · 4/4 bad writes refused
ledger        12 writes · 12 authorized · 0 violations
tests         44 offline
upstream      acryldata/mcp-server-datahub PR #148, #170
```
