"""
Tantra AI — Core unit tests
Fast tests that do NOT require running Docker services.
"""
from __future__ import annotations

import pytest

from tantra.core.config import ModelTier, Settings, get_settings


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestSettings:
    def test_defaults_load(self):
        """Settings load without errors using defaults."""
        s = Settings()
        assert s.environment.value in ("development", "staging", "production")
        assert s.litellm_base_url.startswith("http")

    def test_model_tiers_defined(self):
        """All expected model tiers exist."""
        tiers = {t.value for t in ModelTier}
        assert "frontier" in tiers
        assert "director" in tiers
        assert "manager" in tiers
        assert "worker" in tiers
        assert "coder" in tiers
        assert "fast" in tiers
        assert "embedder" in tiers

    def test_async_db_url_correction(self):
        """Settings corrects postgresql:// → postgresql+asyncpg://."""
        s = Settings(database_url="postgresql://user:pass@localhost:5432/db")
        assert s.database_url.startswith("postgresql+asyncpg://")

    def test_qdrant_url_property(self):
        s = Settings(qdrant_host="qdrant", qdrant_port=6333)
        assert s.qdrant_url == "http://qdrant:6333"


# ---------------------------------------------------------------------------
# Agent base tests
# ---------------------------------------------------------------------------

class TestAgentBase:
    def test_worker_agent_init(self):
        from tantra.agents.worker import WorkerAgent
        agent = WorkerAgent(
            name="TestWorker",
            role="Test Role",
            goal="Test goal",
            model_tier=ModelTier.worker,
        )
        assert agent.name == "TestWorker"
        assert agent.model_tier == ModelTier.worker
        assert agent.agent_id is not None
        assert "TestWorker".lower() in agent.memory_namespace

    def test_leader_agent_init(self):
        from tantra.agents.leader import LeaderAgent
        leader = LeaderAgent(
            name="CMO",
            role="Chief Marketing Officer",
            goal="Grow LinkedIn following",
            model_tier=ModelTier.director,
            worker_roles=["research", "write", "publish"],
        )
        assert leader.name == "CMO"
        assert leader.worker_roles == ["research", "write", "publish"]

    def test_agent_repr(self):
        from tantra.agents.worker import WorkerAgent
        agent = WorkerAgent(
            name="Analyst", role="Analyst", goal="Analyse data",
            model_tier=ModelTier.worker,
        )
        assert "Analyst" in repr(agent)

    def test_worker_presets(self):
        from tantra.agents.worker import (
            make_analyst,
            make_content_writer,
            make_publisher,
            make_research_worker,
        )
        researcher = make_research_worker()
        writer = make_content_writer()
        publisher = make_publisher()
        analyst = make_analyst()

        assert researcher.model_tier == ModelTier.manager
        assert writer.model_tier == ModelTier.worker
        assert "linkedin_post" in writer.skills
        assert analyst.model_tier == ModelTier.worker


# ---------------------------------------------------------------------------
# LinkedIn tool tests (no live API calls)
# ---------------------------------------------------------------------------

class TestLinkedInTool:
    def test_build_auth_url_requires_client_id(self):
        """Auth URL building requires LINKEDIN_CLIENT_ID to be set."""
        from tantra.tools.linkedin import LinkedInClient
        from tantra.core.config import settings

        if not settings.linkedin_client_id:
            pytest.skip("LINKEDIN_CLIENT_ID not configured")

        url = LinkedInClient.build_auth_url()
        assert "linkedin.com/oauth" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_post_without_token_returns_error(self):
        """Posting without a token returns error dict (no exception)."""
        from tantra.tools.linkedin import linkedin_post_text
        result = await linkedin_post_text(
            access_token="",
            author_urn="urn:li:person:test",
            text="Hello LinkedIn",
        )
        assert result["success"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# YouTube tool tests (no live API calls)
# ---------------------------------------------------------------------------

class TestYouTubeTool:
    def test_search_without_key_returns_error(self):
        """YouTube search without API key returns error dict (no exception)."""
        from tantra.tools.youtube import youtube_search
        from tantra.core.config import settings

        if settings.youtube_api_key:
            pytest.skip("YouTube API key is configured — skip no-key test")

        result = youtube_search("AI agents 2025")
        assert "error" in result


# ---------------------------------------------------------------------------
# MCP server tests
# ---------------------------------------------------------------------------

class TestMCPServer:
    def test_tools_list_non_empty(self):
        from tantra.tools.mcp.social_mcp_server import TOOLS
        assert len(TOOLS) >= 4
        names = {t["name"] for t in TOOLS}
        assert "linkedin_post_text" in names
        assert "youtube_search" in names

    def test_tool_schemas_valid(self):
        from tantra.tools.mcp.social_mcp_server import TOOLS
        for tool in TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert "properties" in schema
