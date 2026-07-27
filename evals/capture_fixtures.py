"""Freeze a slice of a live catalog into fixtures the eval can replay offline.

Stores the MCP server's raw get_entities payloads, not parsed objects, so the
eval exercises the real parser too and the fixtures stay honest about what the
server actually returns. Re-run against a live DataHub to refresh:

    python -m evals.capture_fixtures
"""

import asyncio
import json
import sys
from pathlib import Path

from steward.config import Config
from steward.mcp import connect

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "corpus_snapshot.json"

# One governed copy and one drifted copy of each of four tables, plus the pair
# behind the profiling-tag trap. Chosen because the right answer for each is
# defensible from the data alone.
URNS = [
    # order_details: dbt is governed (PII_Data + Authoritative Source), the rest drift
    "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.ORDER_ENTRY_DB.analytics.order_details,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)",
    # addresses: dbt owned + domained, postgres bare
    "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.addresses,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:postgres,b2fd91.order_entry_db.order_entry.addresses,PROD)",
    # countries: small table, tests that a 4-column match is still convincing
    "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.countries,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.countries,PROD)",
    # promotions: dbt carries "No Sample Values", a per-platform profiling annotation
    "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.promotions,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:postgres,b2fd91.order_entry_db.order_entry.promotions,PROD)",
    # inventories: unrelated table, used to build the impostor-twin case
    "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.inventories,PROD)",
    # orders: the case the judge refused in the field while approving five
    # sibling tables with the same proposal shape — kept so that behaviour is
    # measured on repeat rather than characterised from a single run
    "urn:li:dataset:(urn:li:dataPlatform:dbt,b2fd91.order_entry_db.order_entry.orders,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:postgres,b2fd91.order_entry_db.order_entry.orders,PROD)",
]


async def main() -> int:
    cfg = Config()
    async with connect(cfg, allow_mutations=False) as dh:
        records = await dh.get_entities(URNS)
    if isinstance(records, dict):
        records = [records]

    found = {r.get("urn") for r in records}
    missing = [u for u in URNS if u not in found]
    if missing:
        print(f"WARNING: {len(missing)} URN(s) not returned:", file=sys.stderr)
        for u in missing:
            print(f"  {u}", file=sys.stderr)

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {len(records)} records to {FIXTURE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
