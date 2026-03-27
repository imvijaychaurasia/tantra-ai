"""
Tantra AI — API Routes
/api/v1/...

Endpoints:
  GET  /health              System health
  POST /agent/run           Run an agent task (async)
  GET  /agent/{task_id}     Poll task result
  POST /auth/linkedin       Initiate LinkedIn OAuth
  GET  /auth/linkedin/callback  Handle LinkedIn callback
  POST /auth/youtube        Initiate YouTube OAuth
  GET  /auth/youtube/callback   Handle YouTube callback
  POST /social/linkedin/post    Publish a LinkedIn post
  GET  /social/youtube/search   Search YouTube
  POST /memory/save         Save a memory
  GET  /memory/search       Semantic memory search
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from tantra.core.config import ModelTier, settings

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AgentRunRequest(BaseModel):
    task: str = Field(..., min_length=5, description="Task description for the agent")
    model: ModelTier = ModelTier.director
    agent_name: Optional[str] = "Tantra"
    context: Optional[str] = None
    stream: bool = False


class AgentRunResponse(BaseModel):
    task_id: str
    status: str = "queued"
    message: str = "Task queued for execution"


class LinkedInPostRequest(BaseModel):
    access_token: str
    author_urn: str
    text: str = Field(..., min_length=1, max_length=3000)
    visibility: str = "PUBLIC"


class MemorySaveRequest(BaseModel):
    namespace: str = "default"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchRequest(BaseModel):
    namespace: str = "default"
    query: str
    top_k: int = 5


# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------

@router.post("/agent/run", response_model=AgentRunResponse, tags=["agents"])
async def run_agent(
    request: AgentRunRequest,
    background_tasks: BackgroundTasks,
) -> AgentRunResponse:
    """
    Queue an agent task for async execution.
    Poll /agent/{task_id} for results.
    """
    task_id = str(uuid.uuid4())

    # In full implementation: dispatch to Celery queue
    # celery_task = run_agent_task.delay(task_id, request.dict())
    # For now: store in Redis with "queued" status

    return AgentRunResponse(
        task_id=task_id,
        status="queued",
        message=f"Task '{request.task[:50]}...' queued with model={request.model.value}",
    )


@router.get("/agent/{task_id}", tags=["agents"])
async def get_agent_result(task_id: str) -> JSONResponse:
    """Poll for agent task result."""
    # In full implementation: check Redis/DB for result
    return JSONResponse({
        "task_id": task_id,
        "status": "pending",
        "message": "Task is being processed",
    })


# ---------------------------------------------------------------------------
# LinkedIn OAuth
# ---------------------------------------------------------------------------

@router.get("/auth/linkedin", tags=["auth"])
async def linkedin_auth_start() -> RedirectResponse:
    """Redirect user to LinkedIn OAuth consent page."""
    if not settings.linkedin_client_id:
        raise HTTPException(status_code=503, detail="LinkedIn is not configured")

    from tantra.tools.linkedin import LinkedInClient
    url = LinkedInClient.build_auth_url(state="tantra")
    return RedirectResponse(url=url)


@router.get("/auth/linkedin/callback", tags=["auth"])
async def linkedin_auth_callback(
    code: str = Query(...),
    state: str = Query("tantra"),
    error: Optional[str] = Query(None),
) -> JSONResponse:
    """Handle LinkedIn OAuth callback — exchange code for tokens."""
    if error:
        raise HTTPException(status_code=400, detail=f"LinkedIn OAuth error: {error}")

    from tantra.tools.linkedin import LinkedInClient
    tokens = await LinkedInClient.exchange_code(code)

    # In full impl: store tokens in DB / encrypted key store
    return JSONResponse({
        "success": True,
        "access_token": tokens.get("access_token"),
        "expires_in": tokens.get("expires_in"),
        "message": "LinkedIn connected. Store this access_token securely.",
    })


# ---------------------------------------------------------------------------
# YouTube OAuth
# ---------------------------------------------------------------------------

@router.get("/auth/youtube", tags=["auth"])
async def youtube_auth_start() -> RedirectResponse:
    """Redirect user to Google OAuth consent page."""
    if not settings.youtube_client_id:
        raise HTTPException(status_code=503, detail="YouTube OAuth is not configured")

    from tantra.tools.youtube import YouTubeClient
    url = YouTubeClient.build_auth_url()
    return RedirectResponse(url=url)


@router.get("/auth/youtube/callback", tags=["auth"])
async def youtube_auth_callback(
    code: str = Query(...),
    state: str = Query("tantra"),
    error: Optional[str] = Query(None),
) -> JSONResponse:
    """Handle YouTube OAuth callback."""
    if error:
        raise HTTPException(status_code=400, detail=f"YouTube OAuth error: {error}")

    # In full impl: complete token exchange and store credentials
    return JSONResponse({
        "success": True,
        "code": code,
        "message": "YouTube OAuth code received. Token exchange pending full implementation.",
    })


# ---------------------------------------------------------------------------
# Social media actions
# ---------------------------------------------------------------------------

@router.post("/social/linkedin/post", tags=["social"])
async def post_to_linkedin(request: LinkedInPostRequest) -> JSONResponse:
    """Publish a text post to LinkedIn."""
    from tantra.tools.linkedin import linkedin_post_text

    result = await linkedin_post_text(
        access_token=request.access_token,
        author_urn=request.author_urn,
        text=request.text,
        visibility=request.visibility,
    )
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error", "LinkedIn API error"))
    return JSONResponse(result)


@router.get("/social/youtube/search", tags=["social"])
async def search_youtube(
    q: str = Query(..., description="Search query"),
    max_results: int = Query(5, ge=1, le=20),
) -> JSONResponse:
    """Search YouTube for relevant videos."""
    from tantra.tools.youtube import youtube_search

    result = youtube_search(q, max_results=max_results)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Memory endpoints
# ---------------------------------------------------------------------------

@router.post("/memory/save", tags=["memory"])
async def save_memory(request: MemorySaveRequest) -> JSONResponse:
    """Save a fact or observation to agent memory."""
    from tantra.memory.manager import MemoryManager

    mem = MemoryManager(namespace=request.namespace)
    await mem.init()
    point_id = await mem.save(content=request.content, metadata=request.metadata)
    return JSONResponse({"success": True, "point_id": point_id})


@router.post("/memory/search", tags=["memory"])
async def search_memory(request: MemorySearchRequest) -> JSONResponse:
    """Semantic search over stored memories."""
    from tantra.memory.manager import MemoryManager

    mem = MemoryManager(namespace=request.namespace)
    await mem.init()
    results = await mem.search(query=request.query, top_k=request.top_k)
    return JSONResponse({"results": results})
