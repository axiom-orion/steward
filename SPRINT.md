# Steward — DataHub Agent Hackathon sprint (Aug 10, 5:00pm ET)

**Working name:** Steward — the agent that does the data steward's chores and shows receipts.
(kassi-doctrine: mythic-adjacent, memorable, names the job. Rename cheap until Devpost draft.)

**Started:** 2026-07-24 (~2am). **18 days to deadline.** Verified tonight: NO OSS PR filed,
no prior workspace — this sprint starts from zero. Env bootstrapped tonight (see log below).

## The build (verdict from the 7/11 war room, unchanged)

Governed **metadata-remediation agent**, category 1 "Agents That Do Real Work":
- **Read** the metadata context graph via DataHub's MCP server / SDK
- **Detect** metadata debt with ER-style patterns: orphaned datasets, missing owners/domains,
  stale or contradictory descriptions, near-duplicate glossary terms, tag drift
- **Judge-gate** every proposed fix (LLM judge scores the remediation against evidence —
  the Vouch pattern, reused honestly) — no unreviewed writes
- **Write back** approved fixes via the SDK; every change carries provenance
- **Visible before/after in the DataHub UI** — the demo IS the product
- NOT a chat agent (Analytics Agent exists; reimplementation is penalized)

Differentiator (house habit): one quantified metric in the video — e.g. "X% of seeded debt
remediated with 0 unapproved writes; every write traceable to its evidence."

## Scoring reality

- OSS contribution to datahub-project = **1/6 of score**. Original target Jul 20 — MISSED,
  still fully earnable: find a real bug/gap while building (CLI, docs, MCP server, SDK edges),
  file a clean PR by ~Aug 4 so it has review time before submission.
- Apache-2.0 LICENSE top-level, public repo. **Repo org: vorionsys (public rule) — create at
  push time, not before** (push gate).
- Devpost: **register + DRAFT-SAVE the form the first day anything exists** (Slack lesson,
  non-negotiable). RYAN: register on datahub.devpost.com now.

## Timeline (fits around CRDB Jul 25 spike + its Aug 18 tail)

```
Jul 24      Env: WSL Ubuntu + Docker + datahub CLI (DONE tonight) → quickstart stack up,
            sample metadata ingested, poke the UI + MCP server
Jul 25      (CRDB de-risk spike day — Steward pauses)
Jul 26-28   Skeleton: MCP/SDK read path → debt detectors on seeded metadata → first
            judge-gated write-back visible in UI (end-to-end thin slice)
Jul 29-Aug 3 Detectors deepen; seeded debt corpus w/ ground truth; eval harness (P/R on
            debt found, % remediated, 0-unapproved-writes invariant); OSS PR candidate found
Aug 4       OSS PR filed. Feature-complete gate (war-room target Aug 3 — one day slack)
Aug 5-6     Video (<3:00, overview in first 20s, sponsor tech on screen, metric on screen);
            DRAFT-SAVED Devpost submission goes FINAL-quality
Aug 7-10    Buffer. Submit by NOON Aug 10, never 4:59.
```

## Env log (7/24)

- Ubuntu 26.04 WSL: OOBE bypassed via `wsl -d Ubuntu -u root`; user `ryan` created
  (sudo NOPASSWD, default via /etc/wsl.conf)
- Python: system 3.14 too new → uv-managed **3.11 venv at `~/dhenv`**;
  `~/dhenv/bin/datahub` = **CLI 1.6.0.15** (acryl-datahub)
- `~/datahub` = shallow clone of datahub-project/datahub (344M) for OSS-contribution work
- Docker Desktop: was stopped + Ubuntu WSL integration OFF → enabled via
  settings-store.json `IntegratedWslDistros:["Ubuntu"]` + restart
- GOTCHA: Windows PATH interop breaks `export PATH=...:$PATH` in `wsl bash -c` one-liners
  (space-laden /mnt/c entries) — use full paths (`~/.local/bin/uv`) or quote carefully
- **Docker meltdown solved**: force-kill left docker_data.vhdx stale-attached → engine 500s
  AND integration never provisions (one root cause, two faces). Recipe in memory
  [[docker-desktop-wsl-gotchas]]. Also: integration wants Ubuntu as DEFAULT distro.
- **Quickstart UP (v1.5.0.6 stack)**: first run "failed" — mysql first-init outran the CLI's
  healthcheck window and everything cascaded, but mysql/kafka/opensearch all went healthy
  minutes later; waited for system-update to exit, re-ran quickstart (idempotent) → 
  "✔ DataHub is now running". GOTCHA: quickstart's failure banner lies on slow machines —
  check `docker ps` health before believing it.
- UI http://localhost:9002 (datahub/datahub), GMS :8080. Sample datapack
  `showcase-ecommerce` LOADED (54 events) + VERIFIED searchable via GMS GraphQL
  `searchAcrossEntities` (18 entities: dbt datasets, dataProducts, corpGroups). Note:
  search indexing is async (Kafka→OpenSearch) — a fresh load shows total=0 briefly.
- **ENV MILESTONE COMPLETE 7/24 ~3am.** Jul 25 = CRDB spike; Jul 26 = Steward thin slice
  (install acryldata/mcp-server-datahub against this stack, first detector, first
  judge-gated write visible in UI).
- Windows-side gotchas for scripts: `MSYS_NO_PATHCONV=1` before `wsl … bash /mnt/c/...`
  (Git Bash mangles /mnt paths); bash `UID` readonly.

## Open questions (answer while building)

1. ~~MCP read-only?~~ **ANSWERED 7/24: official `acryldata/mcp-server-datahub` has BOTH.**
   Read: search, get_entities, get_lineage(+paths_between), get_dataset_queries,
   list_schema_fields, search/grep_documents. **Write (gated behind
   `TOOLS_IS_MUTATION_ENABLED=true`): add/remove tags, terms, owners; set/remove domains;
   update_description; structured properties; save_document.** → The ENTIRE loop runs over
   DataHub's own MCP server, and the mutation flag IS the governance beat: mutations off by
   default, Steward's judge gate sits in front of explicitly-enabled mutations. Detectors
   map 1:1 to write tools (missing owner→add_owners, tag drift→add/remove_tags, stale
   description→update_description, orphan→set_domains, dup terms→add/remove_terms).
2. Judge = Claude (house trio rule: Claude/Gemini/Grok, NO OpenAI). Multi-judge like Vouch
   or single-judge to keep latency down? Decide after the thin slice.
3. Seeded debt corpus: derive from DataHub's own sample ingestion + synthetic mutations
   (fictional company names only — trademark rule from Vouch applies).
