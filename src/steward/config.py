"""Runtime configuration. Everything comes from the environment — no secrets in code."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    gms_url: str = field(default_factory=lambda: os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"))
    model: str = field(default_factory=lambda: os.environ.get("STEWARD_MODEL", "claude-opus-4-8"))
    mcp_command: str = field(
        default_factory=lambda: os.environ.get(
            "STEWARD_MCP_COMMAND", os.path.expanduser("~/stewenv/bin/mcp-server-datahub")
        )
    )
    ledger_path: str = field(default_factory=lambda: os.environ.get("STEWARD_LEDGER", "steward-ledger.jsonl"))
    # Mutations stay off unless explicitly enabled AND --apply is passed. Two locks.
    mutations_enabled: bool = field(
        default_factory=lambda: os.environ.get("STEWARD_MUTATIONS", "") == "true"
    )
