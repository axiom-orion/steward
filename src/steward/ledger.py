"""Receipts. One JSONL line per decision — finding, proposal, verdict, action.

The ledger is append-only and written before-and-after the mutation: `decided`
records the judge's verdict, `applied`/`apply_failed` records what actually
happened. An auditor can replay the whole session from this file alone.
"""

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .detectors import Finding


class Ledger:
    def __init__(self, path: str):
        self.path = Path(path)

    def write(self, event: str, finding: Finding, **extra: Any) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            "finding": asdict(finding),
            **extra,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
