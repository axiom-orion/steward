"""steward scan | steward fix [--apply] [--kinds ...] [--max-findings N]

scan: find metadata debt, print findings. Read-only, no API key needed.
fix:  propose + judge each finding. Dry-run by default; --apply performs the
      judge-approved writes (and requires STEWARD_MUTATIONS=true as the second
      lock). Every decision is a ledger receipt either way.
"""

import argparse
import asyncio
import sys
from collections import Counter

from .config import Config
from .corpus import fetch_corpus, find_twins
from .detectors import KINDS, Finding, scan_dataset
from .ledger import Ledger
from .mcp import connect, execute
from .remedy import Proposal, propose


def _note(message: str) -> None:
    print(message, file=sys.stderr)


async def collect_findings(cfg: Config, limit: int, kinds: list[str]) -> list[Finding]:
    async with connect(cfg, allow_mutations=False) as dh:
        corpus = await fetch_corpus(dh, limit=limit, progress=_note)

    findings: list[Finding] = []
    for ds in corpus:
        twins = find_twins(ds, corpus)
        findings.extend(scan_dataset(ds, twins, kinds))

    twinned = sum(1 for ds in corpus if find_twins(ds, corpus))
    _note(f"corpus: {len(corpus)} datasets, {twinned} with at least one twin")
    return findings


def _summarize(findings: list[Finding]) -> None:
    counts = Counter(f.kind for f in findings)
    for kind in KINDS:
        if counts.get(kind):
            _note(f"  {kind:20s} {counts[kind]}")
    _note(f"  {'total':20s} {len(findings)}")


async def cmd_scan(cfg: Config, args: argparse.Namespace) -> int:
    findings = await collect_findings(cfg, args.limit, args.kinds)
    for f in sorted(findings, key=lambda f: (f.kind, f.urn)):
        detail = (
            f.evidence.get("proposed_tag_name")
            or f.evidence.get("proposed_domain_name")
            or (", ".join(o["display"] for o in f.evidence.get("proposed_owners", [])) or None)
            or ""
        )
        twin = f" <- {f.evidence['twin_platform']}" if f.evidence.get("twin_platform") else ""
        print(f"[{f.kind}] {f.evidence.get('platform','?')}:{f.entity_name}"
              + (f"  {detail}{twin}" if detail else ""))
    _note("")
    _summarize(findings)
    return 0


async def cmd_fix(cfg: Config, args: argparse.Namespace) -> int:
    if args.apply and not cfg.mutations_enabled:
        _note("refusing: --apply requires STEWARD_MUTATIONS=true in the environment.")
        return 2

    ledger = Ledger(cfg.ledger_path)
    findings = await collect_findings(cfg, args.limit, args.kinds)
    findings.sort(key=lambda f: (f.kind, f.urn))
    if args.max_findings:
        findings = findings[:args.max_findings]

    _note(f"{len(findings)} finding(s) to propose + judge.")
    approved: list[tuple[Finding, Proposal]] = []
    tally: Counter = Counter()

    for f in findings:
        try:
            proposal = propose(f, cfg.model)
        except Exception as err:                        # a proposer failure is not a write
            ledger.write("propose_failed", f, error=str(err)[:500])
            _note(f"PROPOSE-FAILED {f.entity_name}: {err}")
            tally[f"{f.kind}:error"] += 1
            continue

        from .judge import JudgeError, judge            # late import: scan needs no API key

        try:
            verdict = judge(f, proposal, cfg.model)
        except JudgeError as err:
            # No verdict means no write. Recorded distinctly from a refusal so
            # an auditor can tell "the gate said no" from "the gate never ran".
            ledger.write("judge_failed", f, proposal=proposal.to_dict(), error=str(err)[:500])
            _note(f"JUDGE-FAILED {f.entity_name}: {err}")
            tally[f"{f.kind}:judge_failed"] += 1
            continue
        ledger.write(
            "decided", f,
            proposal=proposal.to_dict(),
            verdict=verdict.model_dump(),
            mode="apply" if args.apply else "dry-run",
        )
        mark = "APPROVED" if verdict.approved else "REJECTED"
        tally[f"{f.kind}:{'approved' if verdict.approved else 'rejected'}"] += 1
        print(f"[{mark} {verdict.confidence:.2f}] {f.kind} {f.entity_name}: {proposal.summary[:110]}")
        if not verdict.approved:
            print(f"    judge: {verdict.rationale[:220]}")
        else:
            approved.append((f, proposal))

    _note("")
    for key in sorted(tally):
        _note(f"  {key:34s} {tally[key]}")

    if not args.apply:
        _note(f"\ndry-run: {len(approved)} approved fix(es) NOT applied. Re-run with --apply.")
        return 0

    applied = failed = 0
    async with connect(cfg, allow_mutations=True) as dh:
        for f, proposal in approved:
            try:
                for action in proposal.actions:
                    await execute(dh, action.tool, action.args)
                ledger.write("applied", f, proposal=proposal.to_dict())
                applied += 1
                print(f"applied: {f.kind} {f.entity_name}")
            except RuntimeError as err:
                ledger.write("apply_failed", f, error=str(err)[:500])
                failed += 1
                _note(f"FAILED: {f.kind} {f.entity_name}: {err}")
    _note(f"\napplied {applied}, failed {failed}, of {len(approved)} approved.")
    return 0


def _kinds(value: str) -> list[str]:
    chosen = [k.strip() for k in value.split(",") if k.strip()]
    unknown = [k for k in chosen if k not in KINDS]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown kind(s) {unknown}; choose from {list(KINDS)}")
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(prog="steward", description="Governed metadata remediation for DataHub.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("scan", "find metadata debt (read-only)"),
                            ("fix", "propose + judge fixes; --apply to write")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--limit", type=int, default=500, help="max datasets to load")
        p.add_argument("--kinds", type=_kinds, default=list(KINDS),
                       help=f"comma-separated subset of {list(KINDS)}")
        if name == "fix":
            p.add_argument("--max-findings", type=int, default=0, help="cap findings judged (0 = all)")
            p.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    cfg = Config()
    handler = {"scan": cmd_scan, "fix": cmd_fix}[args.command]
    sys.exit(asyncio.run(handler(cfg, args)))


if __name__ == "__main__":
    main()
