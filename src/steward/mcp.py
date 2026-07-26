"""Thin wrapper over the official DataHub MCP server (acryldata/mcp-server-datahub).

Steward speaks to DataHub exclusively through this server — reads via its search
and entity tools, writes via its mutation tools (which the server only exposes
when TOOLS_IS_MUTATION_ENABLED=true). That flag is deliberately part of the
story: mutations are off by default, and Steward's judge sits in front of the
explicitly-enabled ones.

Argument names here are load-bearing and non-obvious: the server validates them
with pydantic before DataHub is ever contacted, so a wrong name fails identically
whether the write was right or wrong. They are transcribed from the server's own
tool signatures — `list_schema_fields` takes `urn` while `update_description`
takes `entity_urn`, and `search` caps `num_results` at 50 while silently
accepting larger values.
"""

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import Config

MAX_PAGE = 50   # hard cap inside the server; larger values are clamped, not rejected


class DataHubMCP:
    def __init__(self, session: ClientSession):
        self._session = session

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        result = await self._session.call_tool(tool, args)
        if result.isError:
            text = result.content[0].text if result.content else "unknown MCP error"
            raise RuntimeError(f"MCP tool {tool} failed: {text[:500]}")
        text = result.content[0].text if result.content else ""
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text

    # ---- reads ----

    async def search(
        self, query: str = "*", num_results: int = MAX_PAGE,
        offset: int = 0, filter: str | None = None,
    ) -> dict:
        args: dict[str, Any] = {
            "query": query,
            "num_results": min(num_results, MAX_PAGE),
            "offset": offset,
        }
        if filter:
            args["filter"] = filter
        return await self.call("search", args)

    async def get_entities(self, urns: list[str]) -> Any:
        """Batched: one call for many URNs, and the payload already carries
        ownership, tags, domain and schemaMetadata — no follow-up per dataset."""
        return await self.call("get_entities", {"urns": urns})

    async def get_entity(self, urn: str) -> Any:
        return await self.call("get_entities", {"urns": [urn]})

    async def list_schema_fields(self, urn: str) -> Any:
        return await self.call("list_schema_fields", {"urn": urn})

    # ---- writes (only reachable when the server was started with mutations on) ----

    async def update_description(self, urn: str, description: str) -> Any:
        return await self.call(
            "update_description",
            {"entity_urn": urn, "operation": "replace", "description": description},
        )

    async def add_owners(self, urn: str, owner_urns: list[str], ownership_type: str) -> Any:
        return await self.call(
            "add_owners",
            {"owner_urns": owner_urns, "entity_urns": [urn], "ownership_type": ownership_type},
        )

    async def add_tags(self, urn: str, tag_urns: list[str]) -> Any:
        return await self.call("add_tags", {"tag_urns": tag_urns, "entity_urns": [urn]})

    async def set_domains(self, urn: str, domain_urn: str) -> Any:
        return await self.call("set_domains", {"domain_urn": domain_urn, "entity_urns": [urn]})


# Every write Steward is allowed to make. A proposal naming anything else never
# reaches the server — the allowlist is checked at execution, after judging, so
# a compromised or confused proposer cannot widen its own blast radius.
WRITE_TOOLS = frozenset({"update_description", "add_owners", "add_tags", "set_domains"})


async def execute(dh: DataHubMCP, tool: str, args: dict[str, Any]) -> Any:
    if tool not in WRITE_TOOLS:
        raise RuntimeError(f"refusing to execute non-allowlisted tool {tool!r}")
    return await dh.call(tool, args)


@asynccontextmanager
async def connect(cfg: Config, allow_mutations: bool) -> AsyncIterator[DataHubMCP]:
    params = StdioServerParameters(
        command=cfg.mcp_command,
        env={
            **os.environ,
            "DATAHUB_GMS_URL": cfg.gms_url,
            "TOOLS_IS_MUTATION_ENABLED": "true" if allow_mutations else "false",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield DataHubMCP(session)
