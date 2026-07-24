"""steward scan | steward fix [--apply] [--limit N]

scan: find metadata debt, print findings. Read-only.
fix:  draft + judge each finding. Dry-run by default; --apply performs the
      judge-approved writes (and requires STEWARD_MUTATIONS=true as the second
      lock). Every decision is a ledger receipt either way.
"""

import argparse
import asyncio
import sys

from .config import Config
from .detectors import Finding, dataset_urns_from_search, detect_missing_description
from .ledger import Ledger
from .mcp import connect


async def collect_findings(cfg: Config, limit: int) -> list[Finding]:
    findings: list[Finding] = []
    async with connect(cfg, allow_mutations=False) as dh:
        search = await dh.search("*", num_results=200)
        urns = dataset_urns_from_search(search)[:limit]
        print(f"scanning {len(urns)} datasets...", file=sys.stderr)
        for urn in urns:
            entity = await dh.get_entity(urn)
            try:
                schema = await dh.list_schema_fields(urn)
            except RuntimeError:
                schema = {}
            f = detect_missing_description(urn, entity, schema)
            if f:
                findings.append(f)
    return findings


async def cmd_scan(cfg: Config, args: argparse.Namespace) -> int:
    findings = await collect_findings(cfg, args.limit)
    for f in findings:
        print(f"[{f.kind}] {f.entity_name}  ({f.urn})")
    print(f"\n{len(findings)} finding(s).", file=sys.stderr)
    return 0


async def cmd_fix(cfg: Config, args: argparse.Namespace) -> int:
    from .judge import draft_fix, judge_fix  # imported here so scan works without an API key

    if args.apply and not cfg.mutations_enabled:
        print("refusing: --apply requires STEWARD_MUTATIONS=true in the environment.", file=sys.stderr)
        return 2

    ledger = Ledger(cfg.ledger_path)
    findings = await collect_findings(cfg, args.limit)
    print(f"{len(findings)} finding(s) to draft + judge.", file=sys.stderr)
    approved: list[tuple[Finding, str]] = []

    for f in findings:
        draft = draft_fix(f, cfg.model)
        verdict = judge_fix(f, draft, cfg.model)
        ledger.write(
            "decided", f,
            proposal=draft.model_dump(),
            verdict=verdict.model_dump(),
            mode="apply" if args.apply else "dry-run",
        )
        mark = "APPROVED" if verdict.approved else "REJECTED"
        print(f"[{mark} {verdict.confidence:.2f}] {f.entity_name}: {draft.description[:100]}")
        if not verdict.approved:
            print(f"    judge: {verdict.rationale[:200]}")
        if verdict.approved:
            approved.append((f, draft.description))

    if not args.apply:
        print(f"\ndry-run: {len(approved)} approved fix(es) NOT applied. Re-run with --apply.", file=sys.stderr)
        return 0

    async with connect(cfg, allow_mutations=True) as dh:
        for f, description in approved:
            try:
                await dh.update_description(f.urn, description)
                ledger.write("applied", f, description=description)
                print(f"applied: {f.entity_name}")
            except RuntimeError as err:
                ledger.write("apply_failed", f, error=str(err)[:500])
                print(f"FAILED: {f.entity_name}: {err}", file=sys.stderr)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="steward", description="Governed metadata remediation for DataHub.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_scan = sub.add_parser("scan", help="find metadata debt (read-only)")
    p_scan.add_argument("--limit", type=int, default=50)
    p_fix = sub.add_parser("fix", help="draft + judge fixes; --apply to write")
    p_fix.add_argument("--limit", type=int, default=50)
    p_fix.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    cfg = Config()
    handler = {"scan": cmd_scan, "fix": cmd_fix}[args.command]
    sys.exit(asyncio.run(handler(cfg, args)))


if __name__ == "__main__":
    main()
