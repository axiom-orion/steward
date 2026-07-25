"""Runtime configuration. Everything comes from the environment — no secrets in code."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv() -> None:
    """Load KEY=value lines from a .env next to the repo root into os.environ.
    Existing environment variables win. No dependency needed for this much."""
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()


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
