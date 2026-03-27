"""
Tantra AI — Social Media MCP Server
Exposes LinkedIn + YouTube tools as an MCP-compatible server.

Run standalone:
    python -m tantra.tools.mcp.social_mcp_server

Or from FastAPI:
    Mounted at /mcp/social via the main API router.

MCP protocol: https://modelcontextprotocol.io
Each tool follows the standard { name, description, inputSchema, handler } pattern.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "linkedin_post_text",
        "description": (
            "Publish a text post to LinkedIn on behalf of the authenticated user. "
            "Returns the post URN if successful."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["access_token", "author_urn", "text"],
            "properties": {
                "access_token": {
                    "type": "string",
                    "description": "LinkedIn OAuth 2.0 access token",
                },
                "author_urn": {
                    "type": "string",
                    "description": "LinkedIn URN e.g. urn:li:person:ABC123",
                },
                "text": {
                    "type": "string",
                    "description": "Post body text (max 3000 chars)",
                    "maxLength": 3000,
                },
                "visibility": {
                    "type": "string",
                    "enum": ["PUBLIC", "CONNECTIONS"],
                    "default": "PUBLIC",
                    "description": "Post visibility",
                },
            },
        },
    },
    {
        "name": "linkedin_get_profile",
        "description": "Fetch the authenticated LinkedIn user's basic profile (name, headline, URN).",
        "inputSchema": {
            "type": "object",
            "required": ["access_token"],
            "properties": {
                "access_token": {"type": "string", "description": "LinkedIn OAuth 2.0 access token"},
            },
        },
    },
    {
        "name": "youtube_search",
        "description": "Search YouTube for videos matching a query. Useful for trend research.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "max_results": {
                    "type": "integer",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Maximum number of results",
                },
            },
        },
    },
    {
        "name": "youtube_get_channel_stats",
        "description": "Fetch subscriber count, total views, and video count for a YouTube channel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "YouTube channel ID (optional — uses authenticated user's channel if omitted)",
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def handle_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Dispatch a tool call by name and return the result.
    This is the central handler called by the MCP server loop.
    """
    logger.info(f"MCP tool call: {name}", extra={"args": list(arguments.keys())})

    if name == "linkedin_post_text":
        from tantra.tools.linkedin import linkedin_post_text
        return await linkedin_post_text(
            access_token=arguments["access_token"],
            author_urn=arguments["author_urn"],
            text=arguments["text"],
            visibility=arguments.get("visibility", "PUBLIC"),
        )

    elif name == "linkedin_get_profile":
        from tantra.tools.linkedin import LinkedInClient
        client = LinkedInClient(arguments["access_token"])
        return await client.get_profile()

    elif name == "youtube_search":
        from tantra.tools.youtube import youtube_search
        return youtube_search(
            query=arguments["query"],
            max_results=arguments.get("max_results", 5),
        )

    elif name == "youtube_get_channel_stats":
        from tantra.tools.youtube import YouTubeClient
        client = YouTubeClient.from_api_key()
        return client.get_channel_info(channel_id=arguments.get("channel_id"))

    else:
        return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Minimal HTTP MCP server (FastAPI-based)
# Used when running this module as a standalone microservice
# ---------------------------------------------------------------------------

def create_mcp_app():
    """
    Build a lightweight FastAPI app exposing the MCP HTTP interface.

    Endpoints:
      GET  /tools         → list all tools
      POST /tools/{name}  → call a tool
    """
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    mcp_app = FastAPI(title="Tantra Social MCP Server", version="0.1.0")

    @mcp_app.get("/tools")
    async def list_tools() -> JSONResponse:
        return JSONResponse({"tools": TOOLS})

    @mcp_app.post("/tools/{tool_name}")
    async def call_tool(tool_name: str, body: dict[str, Any] = {}) -> JSONResponse:
        result = await handle_tool(tool_name, body)
        return JSONResponse(result)

    return mcp_app


if __name__ == "__main__":
    import uvicorn
    app = create_mcp_app()
    uvicorn.run(app, host="0.0.0.0", port=9000, log_level="info")
