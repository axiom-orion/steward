"""Thin wrapper over the official DataHub MCP server (acryldata/mcp-server-datahub).

Steward speaks to DataHub exclusively through this server — reads via its search
and entity tools, writes via its mutation tools (which the server only exposes
when TOOLS_IS_MUTATION_ENABLED=true). That flag is deliberately part of the
story: mutations are off by default, and Steward's judge sits in front of the
explicitly-enabled ones.
"""

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import Config


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

    async def search(self, query: str = "*", num_results: int = 100) -> dict:
        return await self.call("search", {"query": query, "num_results": num_results})

    async def get_entity(self, urn: str) -> Any:
        return await self.call("get_entities", {"urns": [urn]})

    async def list_schema_fields(self, urn: str) -> Any:
        return await self.call("list_schema_fields", {"urn": urn})

    async def update_description(self, urn: str, description: str) -> Any:
        return await self.call(
            "update_description",
            {"entity_urn": urn, "operation": "replace", "description": description},
        )


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
