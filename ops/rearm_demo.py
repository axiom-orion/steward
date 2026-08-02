"""Put the catalog back the way it was, using nothing but the ledger.

Applying the fixes consumes the demo: once PII_Data has been propagated to the
Snowflake copy, the drift that makes the point no longer exists, and the video
cannot be recorded a second time. This reverses a session so the beat can be
re-shot.

It reads only `applied` receipts and inverts them — add_tags becomes
remove_tags — which is worth noticing on its own: the ledger is complete enough
to undo the agent's work without consulting the agent. An audit log you can
replay backwards is a stronger claim than one you can only read.

Only tag writes are reversed. Restoring a description or an owner would need the
prior value, which an `applied` record does not carry; those are additive and
harmless to leave in place. Dry run by default, like everything else here.

    python -m ops.rearm_demo                 # show what would be undone
    STEWARD_MUTATIONS=true python -m ops.rearm_demo --apply
"""

import argparse
import asyncio
import sys
from collections import defaultdict

from steward.config import Config
from steward.ledger import Ledger
from steward.mcp import connect

INVERTIBLE = {"add_tags": "remove_tags"}


def undo_plan(records: list[dict]) -> dict[str, set[str]]:
    """Tag URNs to strip, per entity, newest receipts included."""
    plan: defaultdict[str, set[str]] = defaultdict(set)
    for rec in records:
        if rec.get("event") != "applied":
            continue
        for action in (rec.get("proposal") or {}).get("actions") or []:
            if action.get("tool") not in INVERTIBLE:
                continue
            args = action.get("args") or {}
            for entity in args.get("entity_urns") or []:
                plan[entity].update(args.get("tag_urns") or [])
    return plan


async def main() -> int:
    parser = argparse.ArgumentParser(prog="rearm_demo")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    cfg = Config()
    if args.apply and not cfg.mutations_enabled:
        print("refusing: --apply requires STEWARD_MUTATIONS=true", file=sys.stderr)
        return 2

    plan = undo_plan(Ledger(cfg.ledger_path).read_all())
    if not plan:
        print("nothing to undo — no applied tag writes in the ledger.")
        return 0

    total = sum(len(tags) for tags in plan.values())
    for entity, tags in sorted(plan.items()):
        print(f"{entity}")
        for tag in sorted(tags):
            print(f"    - {tag}")

    if not args.apply:
        print(f"\ndry run: would remove {total} tag(s) from {len(plan)} entity(ies). "
              "Re-run with --apply and STEWARD_MUTATIONS=true.", file=sys.stderr)
        return 0

    removed = failed = 0
    async with connect(cfg, allow_mutations=True) as dh:
        for entity, tags in sorted(plan.items()):
            try:
                await dh.call("remove_tags", {"tag_urns": sorted(tags), "entity_urns": [entity]})
                removed += len(tags)
                print(f"reverted: {entity}")
            except RuntimeError as err:
                failed += 1
                print(f"FAILED: {entity}: {err}", file=sys.stderr)
    print(f"\nremoved {removed} tag(s), {failed} failure(s). The drift is back — reshoot away.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
