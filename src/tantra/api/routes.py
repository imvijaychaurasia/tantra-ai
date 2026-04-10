"""
Tantra AI — API Routes
/api/v1/...

Endpoints:
  GET  /health                       System health
  POST /agent/run                    Run an agent task (async)
  GET  /agent/{task_id}              Poll task result

  GET  /auth/linkedin                Initiate LinkedIn OAuth (redirect)
  GET  /auth/linkedin/callback       Handle LinkedIn callback + store token
  GET  /auth/youtube                 Initiate YouTube OAuth (redirect)
  GET  /auth/youtube/callback        Handle YouTube callback

  POST /social/linkedin/post         Publish a LinkedIn post directly
  GET  /social/youtube/search        Search YouTube

  GET  /content/queue                List content queue items (with status filter)
  POST /content/research             Trigger research-and-draft crew (manual)
  POST /content/{item_id}/approve    Approve a draft (n8n calls this)
  POST /content/{item_id}/reject     Reject a draft (n8n calls this)

  POST /memory/save                  Save a memory
  POST /memory/search                Semantic memory search
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tantra.core.config import ModelTier, settings
from tantra.core.database import get_db_dep

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


class ApproveRequest(BaseModel):
    approved_by: Optional[str] = "n8n"


class RejectRequest(BaseModel):
    reason: Optional[str] = None
    rejected_by: Optional[str] = "n8n"


# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------

@router.post("/agent/run", response_model=AgentRunResponse, tags=["agents"])
async def run_agent(
    request: AgentRunRequest,
    background_tasks: BackgroundTasks,
) -> AgentRunResponse:
    """Queue an agent task for async execution. Poll /agent/{task_id} for results."""
    task_id = str(uuid.uuid4())
    return AgentRunResponse(
        task_id=task_id,
        status="queued",
        message=f"Task '{request.task[:50]}...' queued with model={request.model.value}",
    )


@router.get("/agent/{task_id}", tags=["agents"])
async def get_agent_result(task_id: str) -> JSONResponse:
    """Poll for agent task result."""
    return JSONResponse({
        "task_id": task_id,
        "status": "pending",
        "message": "Task is being processed",
    })


# ---------------------------------------------------------------------------
# LinkedIn OAuth — full token storage flow
# ---------------------------------------------------------------------------

@router.get("/auth/linkedin", tags=["auth"])
async def linkedin_auth_start(
    user_id: Optional[str] = Query(None, description="Tantra user UUID to associate the token with"),
) -> RedirectResponse:
    """
    Redirect user to LinkedIn OAuth consent page.

    Pass ?user_id={uuid} to associate the LinkedIn account with a Tantra user.
    The user_id is encoded in the OAuth 'state' parameter (with a nonce for CSRF protection).
    """
    if not settings.linkedin_client_id:
        raise HTTPException(status_code=503, detail="LinkedIn is not configured — set LINKEDIN_CLIENT_ID in .env")

    # Build CSRF-safe state: "{user_id or 'anon'}:{random_nonce}"
    nonce = secrets.token_urlsafe(16)
    uid = user_id or "anon"
    state = f"{uid}:{nonce}"

    from tantra.tools.linkedin import LinkedInClient
    url = LinkedInClient.build_auth_url(state=state)
    return RedirectResponse(url=url)


@router.get("/auth/linkedin/callback", tags=["auth"])
async def linkedin_auth_callback(
    code: str = Query(...),
    state: str = Query("anon:"),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_dep),
) -> HTMLResponse:
    """
    LinkedIn OAuth callback handler.

    Flow:
      1. Exchange authorization code for access token
      2. Fetch profile via /userinfo (OpenID Connect)
      3. Encrypt and store token in social_connections
      4. Return success page
    """
    if error:
        detail = error_description or error
        raise HTTPException(status_code=400, detail=f"LinkedIn OAuth error: {detail}")

    # ── 1. Exchange code for tokens ───────────────────────────────────────────
    from tantra.tools.linkedin import LinkedInClient
    try:
        token_data = await LinkedInClient.exchange_code(code)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LinkedIn token exchange failed: {exc.response.text}",
        )

    access_token = token_data.get("access_token", "")
    expires_in = token_data.get("expires_in", 5183944)  # ~60 days default
    refresh_token = token_data.get("refresh_token")  # Only present if refresh_token scope granted
    scope = token_data.get("scope", settings.linkedin_scopes)

    # ── 2. Fetch OpenID Connect userinfo ─────────────────────────────────────
    client = LinkedInClient(access_token)
    try:
        profile = await client.get_profile()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LinkedIn profile fetch failed: {exc.response.text}",
        )

    # OpenID Connect /userinfo returns: sub, name, given_name, family_name, email, picture
    profile_sub = profile.get("sub", "")
    profile_name = profile.get("name", "")
    profile_email = profile.get("email", "")
    # LinkedIn author URN for posting: urn:li:person:{sub}
    profile_urn = f"urn:li:person:{profile_sub}" if profile_sub else ""

    # ── 3. Encrypt tokens ─────────────────────────────────────────────────────
    from tantra.core.crypto import encrypt_token
    access_enc = encrypt_token(access_token)
    refresh_enc = encrypt_token(refresh_token) if refresh_token else None

    # ── 4. Parse user_id from state ───────────────────────────────────────────
    from tantra.db.social import SocialConnection
    uid_str = state.split(":")[0] if ":" in state else "anon"
    user_uuid: Optional[uuid.UUID] = None
    if uid_str != "anon":
        try:
            user_uuid = uuid.UUID(uid_str)
        except ValueError:
            user_uuid = None

    # ── 5. Upsert SocialConnection ────────────────────────────────────────────
    expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

    # Check if a connection for this sub already exists
    existing = await db.execute(
        select(SocialConnection).where(
            SocialConnection.profile_sub == profile_sub,
            SocialConnection.platform == "linkedin",
        )
    )
    conn = existing.scalar_one_or_none()

    if conn:
        # Update existing connection
        conn.access_token_enc = access_enc
        conn.refresh_token_enc = refresh_enc
        conn.profile_urn = profile_urn
        conn.profile_name = profile_name
        conn.profile_email = profile_email
        conn.expires_at = expires_at
        conn.scopes = scope
        conn.updated_at = datetime.utcnow()
        if user_uuid:
            conn.user_id = user_uuid
        db.add(conn)
    else:
        conn = SocialConnection(
            user_id=user_uuid,
            platform="linkedin",
            access_token_enc=access_enc,
            refresh_token_enc=refresh_enc,
            profile_sub=profile_sub,
            profile_urn=profile_urn,
            profile_name=profile_name,
            profile_email=profile_email,
            expires_at=expires_at,
            scopes=scope,
        )
        db.add(conn)

    await db.commit()

    # ── 6. Return success page ────────────────────────────────────────────────
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>LinkedIn Connected — Tantra AI</title>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #0a0a0a; color: #e0e0e0; display: flex;
               align-items: center; justify-content: center; height: 100vh; margin: 0; }}
        .card {{ background: #1a1a1a; border: 1px solid #333; border-radius: 12px;
                 padding: 40px; text-align: center; max-width: 400px; }}
        .tick {{ font-size: 48px; }}
        h2 {{ color: #00a651; margin: 16px 0 8px; }}
        p {{ color: #888; line-height: 1.5; }}
        .name {{ color: #fff; font-weight: 600; }}
        .urn {{ font-size: 12px; color: #555; font-family: monospace; margin-top: 8px; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="tick">✅</div>
        <h2>LinkedIn Connected</h2>
        <p>Authenticated as <span class="name">{profile_name}</span></p>
        <p class="urn">{profile_urn}</p>
        <p>Your access token is encrypted and stored.<br>
           Tantra AI can now post to LinkedIn on your behalf.</p>
        <p style="margin-top:24px;font-size:12px;color:#555;">
          You can close this window.
        </p>
      </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)


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
    return JSONResponse({
        "success": True,
        "code": code,
        "message": "YouTube OAuth code received. Full token storage coming in Phase 2.",
    })


# ---------------------------------------------------------------------------
# Social media direct actions
# ---------------------------------------------------------------------------

@router.post("/social/linkedin/post", tags=["social"])
async def post_to_linkedin(request: LinkedInPostRequest) -> JSONResponse:
    """Publish a text post to LinkedIn directly (for testing — in production use content queue)."""
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
# Zernio — unified social media accounts (replaces per-platform OAuth flows)
# ---------------------------------------------------------------------------

@router.get("/social/zernio/accounts", tags=["social"])
async def list_zernio_accounts() -> JSONResponse:
    """
    List all social accounts connected to your Zernio profile.

    Use this after connecting accounts at https://zernio.com/dashboard to get
    the account IDs you need for ZERNIO_LINKEDIN_ACCOUNT_ID, ZERNIO_YOUTUBE_ACCOUNT_ID, etc.

    Setup (one-time):
      1. Sign up at https://zernio.com
      2. Connect LinkedIn (+ others) via OAuth in the Zernio dashboard
         — no LinkedIn Developer App required
      3. Copy your API key → add ZERNIO_API_KEY to .env
      4. Run: GET /social/zernio/accounts
      5. Copy the LinkedIn account ID → add ZERNIO_LINKEDIN_ACCOUNT_ID to .env
    """
    if not settings.zernio_enabled:
        return JSONResponse(
            {
                "configured": False,
                "message": (
                    "Zernio is not configured. "
                    "Sign up at https://zernio.com, connect your social accounts, "
                    "then add ZERNIO_API_KEY to your .env file and restart tantra-api."
                ),
                "setup_url": "https://zernio.com/dashboard/api-keys",
            },
            status_code=200,
        )

    from tantra.tools.zernio_client import ZernioClient
    try:
        client = ZernioClient()
        accounts = await client.get_accounts()
        profiles = await client.get_profiles()
        return JSONResponse(jsonable_encoder(
            {
                "configured": True,
                "accounts": accounts,
                "profiles": profiles,
                "hint": (
                    "Copy the 'id' field for each platform into your .env: "
                    "ZERNIO_LINKEDIN_ACCOUNT_ID, ZERNIO_YOUTUBE_ACCOUNT_ID, etc."
                ),
            }
        ))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Zernio API error: {exc}")


class ZernioPostRequest(BaseModel):
    text: str
    platform: str = "linkedin"
    account_id: Optional[str] = None


@router.post("/social/zernio/post", tags=["social"])
async def post_via_zernio(body: ZernioPostRequest) -> JSONResponse:
    """
    Quick-test endpoint: publish a text post via Zernio immediately.
    For production use, create a task and let the scheduled pipeline publish it.

    Body: {"text": "...", "platform": "linkedin", "account_id": "acc_..."}
    """
    if not settings.zernio_enabled:
        raise HTTPException(
            status_code=503,
            detail="Zernio not configured — set ZERNIO_API_KEY in .env",
        )
    from tantra.tools.zernio_client import ZernioClient
    client = ZernioClient()
    result = await client.post_text(body.text, platform=body.platform, account_id=body.account_id)
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error", "Zernio error"))
    return JSONResponse(jsonable_encoder(result))


# ---------------------------------------------------------------------------
# Content Queue — AI-drafted posts for human approval
# ---------------------------------------------------------------------------

@router.get("/content/queue", tags=["content"])
async def list_content_queue(
    status: Optional[str] = Query(None, description="Filter by status: draft|approved|rejected|published|failed"),
    platform: str = Query("linkedin"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_dep),
) -> JSONResponse:
    """
    List content queue items.
    Used by n8n to display pending drafts, and by admin UI.
    """
    from tantra.db.social import ContentQueueItem
    from sqlalchemy import desc

    stmt = select(ContentQueueItem).where(
        ContentQueueItem.platform == platform
    ).order_by(desc(ContentQueueItem.created_at)).limit(limit)

    if status:
        stmt = stmt.where(ContentQueueItem.status == status)

    result = await db.execute(stmt)
    items = result.scalars().all()

    return JSONResponse({
        "items": [
            {
                "id": str(item.id),
                "platform": item.platform,
                "status": item.status,
                "draft_text": item.draft_text,
                "hashtags": item.hashtags,
                "created_at": item.created_at.isoformat(),
                "approved_at": item.approved_at.isoformat() if item.approved_at else None,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "post_urn": item.post_urn,
                "rejection_reason": item.rejection_reason,
            }
            for item in items
        ],
        "total": len(items),
    })


@router.post("/content/research", tags=["content"])
async def trigger_content_research(
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """
    Manually trigger the research-and-draft Celery task.
    The crew will research trending topics, draft 3 LinkedIn posts,
    store them in content_queue, and fire n8n webhooks for approval.
    """
    from tantra.tasks.social_tasks import research_and_draft_posts
    task = research_and_draft_posts.delay()
    return JSONResponse({
        "success": True,
        "celery_task_id": task.id,
        "message": "Research crew dispatched. Check content/queue for drafts in ~2-3 minutes.",
    })


@router.post("/content/{item_id}/approve", tags=["content"])
async def approve_content(
    item_id: str,
    request: ApproveRequest,
    db: AsyncSession = Depends(get_db_dep),
) -> JSONResponse:
    """
    Mark a content queue item as approved.
    Called by n8n after human review. The next Celery beat run will publish it.
    """
    from tantra.db.social import ContentQueueItem

    result = await db.execute(
        select(ContentQueueItem).where(ContentQueueItem.id == uuid.UUID(item_id))
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Content item not found")
    if item.status not in ("draft",):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve item with status='{item.status}'. Only 'draft' items can be approved.",
        )

    item.status = "approved"
    item.approved_at = datetime.utcnow()
    db.add(item)
    await db.commit()

    return JSONResponse({
        "success": True,
        "item_id": item_id,
        "status": "approved",
        "message": "Post approved. Will be published on next scheduled run.",
    })


@router.post("/content/{item_id}/reject", tags=["content"])
async def reject_content(
    item_id: str,
    request: RejectRequest,
    db: AsyncSession = Depends(get_db_dep),
) -> JSONResponse:
    """
    Mark a content queue item as rejected.
    Called by n8n after human review. The item will not be published.
    """
    from tantra.db.social import ContentQueueItem

    result = await db.execute(
        select(ContentQueueItem).where(ContentQueueItem.id == uuid.UUID(item_id))
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Content item not found")
    if item.status not in ("draft", "approved"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reject item with status='{item.status}'",
        )

    item.status = "rejected"
    item.rejection_reason = request.reason or "Rejected via n8n approval workflow"
    db.add(item)
    await db.commit()

    return JSONResponse({
        "success": True,
        "item_id": item_id,
        "status": "rejected",
        "reason": item.rejection_reason,
    })


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


# ---------------------------------------------------------------------------
# Skills + Plugins API  (Phase 2 Milestone 1)
# ---------------------------------------------------------------------------

class SkillInstallRequest(BaseModel):
    slug: str = Field(..., description="Skill slug, 'user/repo', or 'user/repo:path'")
    overwrite: bool = Field(False, description="Overwrite if already installed")


class PluginInstallRequest(BaseModel):
    path: str = Field(..., description="Local path to plugin directory")
    overwrite: bool = Field(False, description="Overwrite if already installed")


@router.get("/skills", tags=["skills"])
async def list_skills(
    category: Optional[str] = Query(None, description="Filter by category"),
    platform: Optional[str] = Query(None, description="Filter by platform"),
    source: str = Query("all", description="all | builtin | installed"),
) -> JSONResponse:
    """List all available skills (built-in + installed)."""
    from tantra.skills.loader import get_loader
    from tantra.skills.registry import SkillRegistry

    loader = get_loader()
    loaded = loader.list(category=category, platform=platform)

    reg = SkillRegistry()
    builtin = reg.list_builtin()
    installed = reg.list_installed()

    if source == "builtin":
        return JSONResponse({"skills": builtin, "total": len(builtin)})
    elif source == "installed":
        return JSONResponse({"skills": installed, "total": len(installed)})

    # Merge: loaded skills (gate-passed) + registry metadata
    result = [s.to_dict() for s in loaded]
    return JSONResponse({"skills": result, "total": len(result)})


@router.get("/skills/{name}", tags=["skills"])
async def get_skill(name: str) -> JSONResponse:
    """Get full details for a skill including its instructions."""
    from tantra.skills.loader import get_loader

    loader = get_loader()
    skill = loader.get(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    data = skill.to_dict()
    data["instructions"] = skill.instructions
    data["prompt_block"] = skill.instructions  # alias
    return JSONResponse(data)


@router.post("/skills/install", tags=["skills"])
async def install_skill(request: SkillInstallRequest) -> JSONResponse:
    """Install a skill from built-in, GitHub, or local path."""
    try:
        from tantra.skills.installer import install
        result = install(request.slug, overwrite=request.overwrite)
        return JSONResponse({"success": True, "skill": result})
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/skills/{name}", tags=["skills"])
async def uninstall_skill(name: str) -> JSONResponse:
    """Uninstall a user-installed skill."""
    from tantra.skills.installer import uninstall
    success = uninstall(name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found in user installs")
    return JSONResponse({"success": True, "uninstalled": name})


@router.get("/skills/{name}/prompt", tags=["skills"])
async def get_skill_prompt(name: str) -> JSONResponse:
    """Get the system prompt block for a skill (for direct injection)."""
    from tantra.skills.loader import get_loader
    loader = get_loader()
    skill = loader.get(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return JSONResponse({"name": name, "prompt": skill.instructions})


@router.get("/plugins", tags=["plugins"])
async def list_plugins() -> JSONResponse:
    """List all available plugins."""
    from tantra.plugins.registry import PluginRegistry
    reg = PluginRegistry()
    plugins = reg.list_all()
    return JSONResponse({"plugins": plugins, "total": len(plugins)})


@router.get("/plugins/{name}", tags=["plugins"])
async def get_plugin(name: str) -> JSONResponse:
    """Get details for a plugin."""
    from tantra.plugins.loader import get_loader
    loader = get_loader()
    plugin = loader.get(name)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    return JSONResponse(plugin.to_dict())


@router.post("/plugins/install", tags=["plugins"])
async def install_plugin(request: PluginInstallRequest) -> JSONResponse:
    """Install a plugin from a local path."""
    from pathlib import Path
    from tantra.plugins.registry import PluginRegistry
    try:
        reg = PluginRegistry()
        result = reg.install_from_path(Path(request.path), overwrite=request.overwrite)
        return JSONResponse({"success": True, "plugin": result})
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hub/index", tags=["hub"])
async def hub_index() -> JSONResponse:
    """
    TantraHub registry index — lists all available skills and plugins.
    This is the discovery endpoint for `tantra skills search`.
    """
    import json
    from pathlib import Path
    index_path = Path(__file__).parents[4] / "tantra-hub" / "index.json"
    if not index_path.exists():
        return JSONResponse({"skills": [], "plugins": []})
    return JSONResponse(json.loads(index_path.read_text()))


# ---------------------------------------------------------------------------
# Director API  (Phase 2)
# ---------------------------------------------------------------------------

@router.get("/director/status", tags=["director"])
async def director_status(db: AsyncSession = Depends(get_db_dep)) -> JSONResponse:
    """
    Get the current Director status: active plan + this week's agent tasks.
    """
    from tantra.db.director import AgentTask, WeeklyPlan

    plan_result = await db.execute(
        select(WeeklyPlan)
        .where(WeeklyPlan.status == "active")
        .order_by(WeeklyPlan.week_start.desc())
        .limit(1)
    )
    plan = plan_result.scalar_one_or_none()

    if not plan:
        return JSONResponse({
            "status": "no_active_plan",
            "message": "No active weekly plan. Run 'tantra task run director_weekly_planning' to create one.",
        })

    tasks_result = await db.execute(
        select(AgentTask)
        .where(AgentTask.plan_id == plan.id)
        .order_by(AgentTask.scheduled_for.asc())
    )
    tasks = tasks_result.scalars().all()

    return JSONResponse({
        "plan": {
            "id": str(plan.id),
            "week_start": str(plan.week_start),
            "week_number": plan.week_number,
            "status": plan.status,
            "goals": plan.goals,
            "director_analysis": plan.director_analysis,
            "activated_at": str(plan.activated_at) if plan.activated_at else None,
        },
        "tasks": [
            {
                "id": str(t.id),
                "task_type": t.task_type,
                "assigned_to": t.assigned_to,
                "priority": t.priority,
                "status": t.status,
                "scheduled_for": str(t.scheduled_for) if t.scheduled_for else None,
                "instructions": t.instructions,
                "completed_at": str(t.completed_at) if t.completed_at else None,
            }
            for t in tasks
        ],
        "task_summary": {
            "total": len(tasks),
            "pending": sum(1 for t in tasks if t.status == "pending"),
            "in_progress": sum(1 for t in tasks if t.status == "in_progress"),
            "completed": sum(1 for t in tasks if t.status == "completed"),
            "failed": sum(1 for t in tasks if t.status == "failed"),
        },
    })


@router.get("/director/plans", tags=["director"])
async def list_director_plans(
    limit: int = Query(default=5, le=20),
    db: AsyncSession = Depends(get_db_dep),
) -> JSONResponse:
    """List recent weekly plans."""
    from tantra.db.director import WeeklyPlan

    result = await db.execute(
        select(WeeklyPlan)
        .order_by(WeeklyPlan.week_start.desc())
        .limit(limit)
    )
    plans = result.scalars().all()
    return JSONResponse({
        "plans": [
            {
                "id": str(p.id),
                "week_start": str(p.week_start),
                "week_number": p.week_number,
                "year": p.year,
                "status": p.status,
                "goals_summary": {
                    "primary_topic": (p.goals or {}).get("primary_topic"),
                    "linkedin_target": (p.goals or {}).get("linkedin_posts_target"),
                    "progress_target": (p.goals or {}).get("progress_posts_target"),
                } if p.goals else None,
                "created_at": str(p.created_at),
            }
            for p in plans
        ]
    })


@router.post("/director/plan/trigger", tags=["director"])
async def trigger_weekly_planning() -> JSONResponse:
    """
    Manually trigger the Director's weekly planning task (for testing).
    Same as `tantra task run director_weekly_planning` from CLI.
    """
    from tantra.tasks.celery_app import director_weekly_planning
    result = director_weekly_planning.delay()
    return JSONResponse({
        "success": True,
        "celery_task_id": result.id,
        "message": "Weekly planning task queued. Check /director/status after ~60s.",
    })


# ---------------------------------------------------------------------------
# YouTube endpoints  (Phase 3a)
# ---------------------------------------------------------------------------

class YouTubeApproveRequest(BaseModel):
    approved_by: Optional[str] = "n8n"
    notes: Optional[str] = None   # Optional reviewer notes


class YouTubeRejectRequest(BaseModel):
    reason: Optional[str] = None
    rejected_by: Optional[str] = "n8n"


@router.get("/youtube/", tags=["youtube"])
async def list_youtube_videos(
    status: Optional[str] = Query(None, description="Filter by status: scripted|approved|producing|produced|uploading|live|rejected|failed"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_dep),
) -> JSONResponse:
    """
    List YouTube videos in the production pipeline.
    Filter by status to find scripted (awaiting approval), live, etc.
    """
    from sqlalchemy import select
    from tantra.db.social import YouTubeVideo

    query = select(YouTubeVideo).order_by(YouTubeVideo.created_at.desc()).limit(limit)
    if status:
        query = query.where(YouTubeVideo.status == status)

    result = await db.execute(query)
    videos = result.scalars().all()

    return JSONResponse({
        "videos": [
            {
                "id": str(v.id),
                "title": v.title,
                "status": v.status,
                "topic_hint": v.topic_hint,
                "thumbnail_concept": v.thumbnail_concept,
                "tags": v.tags or [],
                "youtube_url": v.youtube_url,
                "views": v.views,
                "scene_count": len((v.script or {}).get("scenes", [])),
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "approved_at": v.approved_at.isoformat() if v.approved_at else None,
                "uploaded_at": v.uploaded_at.isoformat() if v.uploaded_at else None,
            }
            for v in videos
        ],
        "total": len(videos),
    })


@router.get("/youtube/{video_id}", tags=["youtube"])
async def get_youtube_video(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_dep),
) -> JSONResponse:
    """
    Get full details for a specific YouTube video, including the complete script.
    Used by n8n approval workflow to display the script for review.
    """
    from tantra.db.social import YouTubeVideo

    video = await db.get(YouTubeVideo, video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"YouTubeVideo {video_id} not found")

    return JSONResponse({
        "id": str(video.id),
        "title": video.title,
        "description": video.description,
        "status": video.status,
        "topic_hint": video.topic_hint,
        "script": video.script,
        "thumbnail_concept": video.thumbnail_concept,
        "tags": video.tags or [],
        "youtube_url": video.youtube_url,
        "youtube_video_id": video.youtube_video_id,
        "views": video.views,
        "likes": video.likes,
        "comments": video.comments,
        "agent_task_id": str(video.agent_task_id) if video.agent_task_id else None,
        "n8n_execution_id": video.n8n_execution_id,
        "rejection_reason": video.rejection_reason,
        "error_message": video.error_message,
        "created_at": video.created_at.isoformat() if video.created_at else None,
        "approved_at": video.approved_at.isoformat() if video.approved_at else None,
        "produced_at": video.produced_at.isoformat() if video.produced_at else None,
        "uploaded_at": video.uploaded_at.isoformat() if video.uploaded_at else None,
    })


@router.post("/youtube/{video_id}/approve", tags=["youtube"])
async def approve_youtube_script(
    video_id: uuid.UUID,
    body: YouTubeApproveRequest,
    db: AsyncSession = Depends(get_db_dep),
) -> JSONResponse:
    """
    n8n calls this endpoint when a human approves a YouTube script.
    Transitions YouTubeVideo from 'scripted' → 'approved'.

    Phase 3b will queue a youtube_produce AgentTask here.
    Phase 3a: just transitions status and returns success.
    """
    from tantra.db.social import YouTubeVideo

    video = await db.get(YouTubeVideo, video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"YouTubeVideo {video_id} not found")

    if video.status not in ("scripted",):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve video in status '{video.status}'. Expected: scripted",
        )

    video.status = "approved"
    video.approved_at = datetime.utcnow()
    await db.commit()

    # Phase 3b: dispatch produce_youtube_video Celery task directly
    import logging
    _logger = logging.getLogger(__name__)

    produce_task_id: str | None = None
    try:
        from tantra.tasks.celery_app import app as celery_app

        # Dispatch production task directly (same pattern as /upload endpoint)
        result = celery_app.send_task(
            "tantra.tasks.youtube.produce_youtube_video",
            args=[str(video_id)],
            queue="default",
        )
        produce_task_id = result.id
        _logger.info(
            "YouTube script approved: video_id=%s title=%r — queued produce task %s",
            str(video_id), video.title, produce_task_id,
        )
    except Exception as exc:
        _logger.error(
            "YouTube approve: failed to queue produce task for video %s: %s",
            str(video_id), exc, exc_info=True,
        )
        # Don't fail the approval — video is approved, production can be retried
        # manually via POST /youtube/{video_id}/produce

    return JSONResponse({
        "success": True,
        "video_id": str(video_id),
        "status": "approved",
        "produce_task_id": produce_task_id,
        "message": (
            "Script approved. Production queued — tantra-media will generate "
            "TTS narration, slide images, and the final MP4. "
            "If produce_task_id is null, retry manually via POST /youtube/{video_id}/produce."
        ),
        "approved_by": body.approved_by,
    })


@router.post("/youtube/{video_id}/reject", tags=["youtube"])
async def reject_youtube_script(
    video_id: uuid.UUID,
    body: YouTubeRejectRequest,
    db: AsyncSession = Depends(get_db_dep),
) -> JSONResponse:
    """
    n8n calls this endpoint when a human rejects a YouTube script.
    Transitions YouTubeVideo from 'scripted' → 'rejected'.
    A new youtube_script AgentTask can be created to try again with adjusted brief.
    """
    from tantra.db.social import YouTubeVideo

    video = await db.get(YouTubeVideo, video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"YouTubeVideo {video_id} not found")

    if video.status not in ("scripted",):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject video in status '{video.status}'. Expected: scripted",
        )

    video.status = "rejected"
    video.rejection_reason = body.reason
    await db.commit()

    return JSONResponse({
        "success": True,
        "video_id": str(video_id),
        "status": "rejected",
        "rejection_reason": body.reason,
        "rejected_by": body.rejected_by,
        "message": "Script rejected. Commission a new youtube_script task via Director chat to retry.",
    })


@router.post("/youtube/{video_id}/produce", tags=["youtube"])
async def trigger_youtube_produce(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_dep),
) -> JSONResponse:
    """
    Manually trigger media production for an approved YouTube script.

    Phase 3b — queues produce_youtube_video Celery task.

    Prerequisites:
      - Video must be in 'approved' status
      - tantra-media service must be running

    The task transitions the video: approved → producing → produced
    Monitor progress via GET /youtube/{video_id} or Flower at :5555.
    """
    import logging as _logging
    from tantra.db.social import YouTubeVideo

    _logger = _logging.getLogger(__name__)

    video = await db.get(YouTubeVideo, video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"YouTubeVideo {video_id} not found")

    if video.status not in ("approved", "failed"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot produce video in status '{video.status}'. "
                "Expected: approved (or failed for retry). "
                "Approve the script first via POST /youtube/{video_id}/approve."
            ),
        )

    celery_task_id: str | None = None
    try:
        from tantra.tasks.celery_app import app as celery_app

        result = celery_app.send_task(
            "tantra.tasks.youtube.produce_youtube_video",
            args=[str(video_id)],
            queue="default",
        )
        celery_task_id = result.id
        _logger.info(
            "YouTube produce queued: video_id=%s title=%r celery_task=%s",
            str(video_id), video.title, celery_task_id,
        )
    except Exception as exc:
        _logger.error(
            "Failed to queue produce task for video %s: %s", str(video_id), exc, exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to queue produce task: {exc}")

    return JSONResponse({
        "success": True,
        "video_id": str(video_id),
        "title": video.title,
        "status": "producing (queued)",
        "celery_task_id": celery_task_id,
        "message": (
            "Produce task queued. The video will be rendered by tantra-media and "
            "status will transition to 'produced' on success. "
            "Monitor at http://localhost:5555 (Flower) or poll GET /youtube/{video_id}."
        ),
    })


@router.post("/youtube/{video_id}/upload", tags=["youtube"])
async def trigger_youtube_upload(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_dep),
) -> JSONResponse:
    """
    Manually trigger upload of a produced YouTube video to the YouTube Data API.

    Phase 3c — queues upload_youtube_video Celery task.

    Prerequisites:
      - Video must be in 'produced' status
      - YOUTUBE_REFRESH_TOKEN must be set in .env
        (run: python scripts/youtube_oauth_setup.py)

    The task transitions the video: produced → uploading → live
    Monitor progress via GET /youtube/{video_id} or Flower at :5555.
    """
    import logging as _logging
    from tantra.db.social import YouTubeVideo

    _logger = _logging.getLogger(__name__)

    video = await db.get(YouTubeVideo, video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"YouTubeVideo {video_id} not found")

    if video.status != "produced":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot upload video in status '{video.status}'. "
                "Expected: produced. "
                "Approve and produce the video first."
            ),
        )

    celery_task_id: str | None = None
    try:
        from tantra.tasks.celery_app import app as celery_app

        # Dispatch upload task directly (no AgentTask wrapper — it's a short terminal operation)
        result = celery_app.send_task(
            "tantra.tasks.youtube.upload_youtube_video",
            args=[str(video_id)],
            queue="default",
        )
        celery_task_id = result.id
        _logger.info(
            "YouTube upload queued: video_id=%s title=%r celery_task=%s",
            str(video_id), video.title, celery_task_id,
        )
    except Exception as exc:
        _logger.error(
            "Failed to queue upload task for video %s: %s", str(video_id), exc, exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to queue upload task: {exc}")

    return JSONResponse({
        "success": True,
        "video_id": str(video_id),
        "title": video.title,
        "status": "uploading (queued)",
        "celery_task_id": celery_task_id,
        "message": (
            "Upload task queued. The video will be uploaded to YouTube and "
            "status will transition to 'live' on success. "
            "Monitor at http://localhost:5555 (Flower) or poll GET /youtube/{video_id}."
        ),
    })


# ---------------------------------------------------------------------------
# Live Monitor — WebSocket + HTML dashboard
# ---------------------------------------------------------------------------

_MONITOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tantra AI — Live Monitor</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d0d0d; color: #c9d1d9; font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 13px; }
  header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 20px; display: flex; align-items: center; gap: 16px; position: sticky; top: 0; z-index: 10; }
  header h1 { font-size: 15px; color: #58a6ff; letter-spacing: 0.05em; }
  #status { font-size: 11px; padding: 3px 10px; border-radius: 20px; background: #21262d; color: #8b949e; }
  #status.connected { background: #1f4a27; color: #3fb950; }
  #status.disconnected { background: #4a1f1f; color: #f85149; }
  .stats { margin-left: auto; display: flex; gap: 20px; font-size: 11px; color: #8b949e; }
  .stat { display: flex; flex-direction: column; align-items: center; gap: 2px; }
  .stat span { font-size: 16px; font-weight: bold; color: #c9d1d9; }
  .filters { background: #161b22; border-bottom: 1px solid #30363d; padding: 8px 20px; display: flex; gap: 12px; align-items: center; }
  .filters label { display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 11px; padding: 4px 10px; border-radius: 4px; border: 1px solid #30363d; transition: background 0.15s; }
  .filters label:hover { background: #21262d; }
  .filter-llm { color: #79c0ff; }
  .filter-agent { color: #d2a8ff; }
  .filter-tool { color: #ffa657; }
  .filter-task { color: #3fb950; }
  .filter-system { color: #8b949e; }
  #log { padding: 8px 0; overflow-y: auto; height: calc(100vh - 100px); }
  .entry { display: flex; gap: 10px; padding: 5px 20px; border-bottom: 1px solid #161b22; animation: fadeIn 0.15s ease; }
  .entry:hover { background: #161b22; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(-3px); } to { opacity: 1; transform: none; } }
  .ts { color: #484f58; flex-shrink: 0; width: 90px; }
  .badge { flex-shrink: 0; width: 110px; text-align: center; border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold; letter-spacing: 0.05em; }
  .badge-llm_start    { background: #1c2d4a; color: #79c0ff; }
  .badge-llm_end      { background: #1c2d4a; color: #79c0ff; }
  .badge-llm_failed   { background: #4a1f1f; color: #f85149; }
  .badge-agent_step   { background: #2d1f4a; color: #d2a8ff; }
  .badge-tool_call    { background: #3d2a1a; color: #ffa657; }
  .badge-task_start   { background: #1a3d1a; color: #3fb950; }
  .badge-task_end     { background: #1a3d1a; color: #3fb950; }
  .badge-task_failed  { background: #4a1f1f; color: #f85149; }
  .badge-system       { background: #21262d; color: #8b949e; }
  .body { flex: 1; overflow: hidden; }
  .body .main { color: #e6edf3; }
  .body .meta { color: #8b949e; font-size: 11px; margin-top: 2px; }
  .body .model { color: #79c0ff; }
  .body .agent { color: #d2a8ff; }
  .body .tool { color: #ffa657; }
  .body .latency { color: #3fb950; }
  .body .error { color: #f85149; }
  #empty { text-align: center; color: #484f58; padding: 60px; }
</style>
</head>
<body>
<header>
  <h1>⬡ तंत्र — Live Monitor</h1>
  <div id="status">Connecting…</div>
  <div class="stats">
    <div class="stat"><span id="s-total">0</span>events</div>
    <div class="stat"><span id="s-llm">0</span>LLM calls</div>
    <div class="stat"><span id="s-tokens">0</span>tokens</div>
    <div class="stat"><span id="s-lat">—</span>avg ms</div>
  </div>
</header>
<div class="filters">
  <span style="color:#8b949e;font-size:11px;margin-right:6px">Filter:</span>
  <label class="filter-llm"><input type="checkbox" id="f-llm" checked> LLM calls</label>
  <label class="filter-agent"><input type="checkbox" id="f-agent" checked> Agent steps</label>
  <label class="filter-tool"><input type="checkbox" id="f-tool" checked> Tool calls</label>
  <label class="filter-task"><input type="checkbox" id="f-task" checked> Tasks</label>
  <label class="filter-system"><input type="checkbox" id="f-system" checked> System</label>
  <button onclick="document.getElementById('log').innerHTML='<div id=empty>Cleared.</div>';stats={total:0,llm:0,tokens:0,latencies:[]};updateStats();" style="margin-left:auto;background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:11px;">Clear</button>
</div>
<div id="log"><div id="empty" style="text-align:center;color:#484f58;padding:60px">Waiting for events…<br><small>Start a task or crew to see live activity</small></div></div>

<script>
const WS_URL = (location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host + '/api/v1/ws/monitor';
let stats = { total: 0, llm: 0, tokens: 0, latencies: [] };
let autoScroll = true;

function updateStats() {
  document.getElementById('s-total').textContent = stats.total;
  document.getElementById('s-llm').textContent = stats.llm;
  document.getElementById('s-tokens').textContent = stats.tokens > 999 ? (stats.tokens/1000).toFixed(1)+'k' : stats.tokens;
  document.getElementById('s-lat').textContent = stats.latencies.length
    ? Math.round(stats.latencies.slice(-50).reduce((a,b)=>a+b,0)/Math.min(50,stats.latencies.length))+'ms' : '—';
}

function fmtTime(ts) {
  try { return new Date(ts).toLocaleTimeString('en-GB',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'}); }
  catch { return ts.slice(11,19) || '??:??:??'; }
}

function escHtml(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderEvent(d) {
  const ev = d.event || 'system';
  const filterMap = {
    llm_start:'f-llm', llm_end:'f-llm', llm_failed:'f-llm',
    agent_step:'f-agent', tool_call:'f-tool',
    task_start:'f-task', task_end:'f-task', task_failed:'f-task',
    system:'f-system'
  };
  const fid = filterMap[ev] || 'f-system';
  if (!document.getElementById(fid)?.checked) return;

  let main = '', meta = '';
  if (ev === 'llm_start') {
    main = `<span class="model">${escHtml(d.model)}</span> — ${escHtml(d.messages_count)} messages`;
    meta = `agent: <span class="agent">${escHtml(d.agent)}</span>  crew: ${escHtml(d.crew||'—')}`;
  } else if (ev === 'llm_end') {
    main = `<span class="model">${escHtml(d.model)}</span> → <span class="latency">${d.latency_ms}ms</span>  ${d.total_tokens||0} tokens`;
    meta = `agent: <span class="agent">${escHtml(d.agent)}</span>  in:${d.prompt_tokens||0} out:${d.completion_tokens||0}`;
    stats.llm++; stats.tokens += d.total_tokens||0;
    if (d.latency_ms > 0) stats.latencies.push(d.latency_ms);
  } else if (ev === 'llm_failed') {
    main = `<span class="model">${escHtml(d.model)}</span> <span class="error">FAILED</span>`;
    meta = `<span class="error">${escHtml(d.error)}</span>`;
  } else if (ev === 'agent_step') {
    main = `<span class="agent">${escHtml(d.agent)}</span> thinking…`;
    meta = escHtml((d.thought_preview||'').substring(0,120));
  } else if (ev === 'tool_call') {
    main = `<span class="agent">${escHtml(d.agent)}</span> → <span class="tool">${escHtml(d.tool)}</span>`;
    meta = escHtml((d.input_preview||'').substring(0,120));
  } else if (ev === 'task_start') {
    main = `▶ <strong>${escHtml(d.task_type)}</strong> started`;
    meta = `id:${escHtml(d.task_id||'?').substring(0,8)}  ${escHtml(d.topic||'')}`;
  } else if (ev === 'task_end') {
    main = `✓ <strong>${escHtml(d.task_type)}</strong> completed`;
    meta = `id:${escHtml(d.task_id||'?').substring(0,8)}  ${escHtml(d.title||'')}`;
  } else if (ev === 'task_failed') {
    main = `✗ <strong>${escHtml(d.task_type)}</strong> <span class="error">FAILED</span>`;
    meta = `<span class="error">${escHtml(d.error||'')}</span>`;
  } else {
    main = escHtml(d.message || JSON.stringify(d));
  }

  stats.total++;
  updateStats();

  const empty = document.getElementById('empty');
  if (empty) empty.remove();

  const log = document.getElementById('log');
  const div = document.createElement('div');
  div.className = 'entry';
  div.innerHTML = `
    <span class="ts">${fmtTime(d.ts)}</span>
    <span class="badge badge-${escHtml(ev)}">${ev.replace('_',' ')}</span>
    <div class="body">
      <div class="main">${main}</div>
      ${meta ? '<div class="meta">'+meta+'</div>' : ''}
    </div>`;
  log.appendChild(div);
  if (autoScroll) log.scrollTop = log.scrollHeight;
}

function connect() {
  const ws = new WebSocket(WS_URL);
  const statusEl = document.getElementById('status');

  ws.onopen = () => {
    statusEl.textContent = 'Connected'; statusEl.className = 'connected';
  };
  ws.onmessage = e => {
    try { renderEvent(JSON.parse(e.data)); } catch {}
  };
  ws.onclose = () => {
    statusEl.textContent = 'Disconnected — reconnecting…'; statusEl.className = 'disconnected';
    setTimeout(connect, 3000);
  };
  ws.onerror = () => ws.close();
}

document.getElementById('log').addEventListener('scroll', function() {
  autoScroll = this.scrollTop + this.clientHeight >= this.scrollHeight - 40;
});

connect();
</script>
</body>
</html>"""


@router.get("/monitor", response_class=HTMLResponse, tags=["monitor"])
async def monitor_page() -> HTMLResponse:
    """Serve the live monitor HTML dashboard."""
    return HTMLResponse(content=_MONITOR_HTML)


@router.websocket("/ws/monitor")
async def monitor_websocket(websocket: WebSocket) -> None:
    """
    WebSocket endpoint that streams live monitor events to the browser.

    Subscribes to the Redis pub/sub channel tantra:monitor:live and
    forwards every JSON event to the connected WebSocket client.
    Multiple clients can connect simultaneously (each gets its own subscriber).
    """
    await websocket.accept()
    try:
        import redis.asyncio as aioredis
        from tantra.core.config import settings
        from tantra.core.monitor import MONITOR_CHANNEL

        r = aioredis.from_url(settings.celery_broker_url, decode_responses=True)
        async with r.pubsub() as ps:
            await ps.subscribe(MONITOR_CHANNEL)
            try:
                async for message in ps.listen():
                    if message and message.get("type") == "message":
                        data = message.get("data", "")
                        if data:
                            await websocket.send_text(data)
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                await ps.unsubscribe(MONITOR_CHANNEL)
        await r.aclose()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Agent Config Dashboard — /agents
# ---------------------------------------------------------------------------

import os as _os
from pathlib import Path as _Path


def _get_agents_root() -> _Path:
    """Resolve agents/ directory — works in Docker (/app/agents) and host dev."""
    for candidate in [_Path("/app/agents"), _Path(__file__).resolve().parents[4] / "agents"]:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("agents/ directory not found")


@router.get("/agents", response_class=HTMLResponse, tags=["agents-dashboard"])
async def agents_dashboard() -> HTMLResponse:
    """Browser UI for browsing and editing all agent config files."""
    return HTMLResponse(_AGENTS_DASHBOARD_HTML)


@router.get("/api/v1/agents/tree", tags=["agents-dashboard"])
async def agents_tree() -> JSONResponse:
    """Return the full agents/ directory tree as JSON."""
    try:
        root = _get_agents_root()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    def _build_tree(path: _Path) -> dict:
        if path.is_file():
            return {"name": path.name, "type": "file", "path": str(path.relative_to(root))}
        children = []
        try:
            for child in sorted(path.iterdir()):
                if child.name.startswith("."):
                    continue
                children.append(_build_tree(child))
        except PermissionError:
            pass
        return {
            "name": path.name,
            "type": "directory",
            "path": str(path.relative_to(root)) if path != root else "",
            "children": children,
        }

    return JSONResponse(_build_tree(root))


@router.get("/api/v1/agents/file", tags=["agents-dashboard"])
async def read_agent_file(path: str = Query(..., description="Relative path inside agents/")) -> JSONResponse:
    """Read a single agent config file."""
    try:
        root = _get_agents_root()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Security: prevent path traversal
    try:
        full_path = (root / path).resolve()
        full_path.relative_to(root.resolve())  # raises ValueError if outside root
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is a directory, not a file")

    content = full_path.read_text(encoding="utf-8")
    return JSONResponse({"path": path, "content": content, "size": len(content)})


class AgentFileWriteRequest(BaseModel):
    path: str = Field(..., description="Relative path inside agents/")
    content: str = Field(..., description="New file content")
    comment: str = Field(default="update", description="Short description of what changed (stored in history)")


@router.post("/api/v1/agents/file", tags=["agents-dashboard"])
async def write_agent_file(request: AgentFileWriteRequest) -> JSONResponse:
    """
    Write a single agent config file with automatic version history.
    Hot-reload: the change takes effect on the NEXT LLM call — zero restart needed.
    Every save creates a versioned snapshot in .history/ with your comment.
    """
    from tantra.core.agent_loader import save_file_with_history

    try:
        root = _get_agents_root()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Security: prevent path traversal
    try:
        full_path = (root / request.path).resolve()
        full_path.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    # Only allow editing .md and .json files
    if full_path.suffix not in (".md", ".json"):
        raise HTTPException(status_code=400, detail="Only .md and .json files can be edited")

    entry = save_file_with_history(full_path, request.content, comment=request.comment, actor="user")
    return JSONResponse({"path": request.path, "saved": True, "size": len(request.content), "history_entry": entry})


@router.get("/api/v1/agents/history", tags=["agents-dashboard"])
async def get_file_history(path: str = Query(..., description="Relative path inside agents/")) -> JSONResponse:
    """Return the version history for a file (newest first). Each entry has ts, comment, actor, version_file, size."""
    from tantra.core.agent_loader import list_file_history

    try:
        root = _get_agents_root()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        full_path = (root / path).resolve()
        full_path.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    history = list_file_history(full_path)
    return JSONResponse({"path": path, "versions": history})


class AgentFileVersionRequest(BaseModel):
    path: str = Field(..., description="Relative path inside agents/")
    version_file: str = Field(..., description="Version filename from CHANGELOG (e.g. '20260409_143000_initial.md')")


@router.get("/api/v1/agents/version", tags=["agents-dashboard"])
async def read_file_version(
    path: str = Query(...),
    version_file: str = Query(..., description="Version filename from CHANGELOG"),
) -> JSONResponse:
    """Read the content of a specific historical version of a file."""
    from tantra.core.agent_loader import read_file_version as _read_version

    try:
        root = _get_agents_root()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        full_path = (root / path).resolve()
        full_path.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    content = _read_version(full_path, version_file)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Version not found: {version_file}")
    return JSONResponse({"path": path, "version_file": version_file, "content": content})


class AgentRestoreRequest(BaseModel):
    path: str = Field(..., description="Relative path inside agents/")
    version_file: str = Field(..., description="Version filename to restore")
    comment: str = Field(default="", description="Optional comment for the restore action")


@router.post("/api/v1/agents/restore", tags=["agents-dashboard"])
async def restore_file_version(request: AgentRestoreRequest) -> JSONResponse:
    """
    Restore a historical version as the new live content.
    Creates a new history entry recording the restore. Hot-reload applies immediately.
    """
    from tantra.core.agent_loader import restore_file_version as _restore

    try:
        root = _get_agents_root()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        full_path = (root / request.path).resolve()
        full_path.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    comment = request.comment or f"restored from {request.version_file}"
    entry = _restore(full_path, request.version_file, comment=comment)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Version not found: {request.version_file}")
    return JSONResponse({"path": request.path, "restored": True, "history_entry": entry})


# ---------------------------------------------------------------------------
# Dashboard HTML (single-file SPA — no external dependencies)
# ---------------------------------------------------------------------------

_AGENTS_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tantra AI — Agent Config Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f0f13; color: #e0e0e0; display: flex; height: 100vh; overflow: hidden; }

/* ── Sidebar ── */
#sidebar { width: 260px; min-width: 180px; background: #16161e; border-right: 1px solid #2a2a3a;
           overflow-y: auto; flex-shrink: 0; display: flex; flex-direction: column; }
#sidebar-header { padding: 14px 16px; border-bottom: 1px solid #2a2a3a; }
#sidebar-header h1 { font-size: 13px; font-weight: 600; color: #a78bfa; }
#sidebar-header p { font-size: 10px; color: #555; margin-top: 3px; }
#tree { flex: 1; overflow-y: auto; padding: 6px 0; }
.tree-item { cursor: pointer; user-select: none; }
.tree-dir { padding: 4px 8px 4px 6px; display: flex; align-items: center; gap: 5px;
            font-size: 11px; color: #94a3b8; font-weight: 500; }
.tree-dir:hover { color: #e0e0e0; }
.tree-dir .icon { font-size: 10px; width: 10px; }
.tree-children { margin-left: 14px; border-left: 1px solid #222230; }
.tree-file { padding: 3px 8px; display: flex; align-items: center; gap: 7px;
             font-size: 11px; color: #55607a; cursor: pointer; border-radius: 3px; margin: 1px 4px; }
.tree-file:hover { background: #1e1e2e; color: #a0aec0; }
.tree-file.active { background: #2d1f56; color: #a78bfa; }
.tree-file .dot { width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0; }
.dot-static { background: #4ade80; }
.dot-dynamic { background: #f59e0b; }
.dot-config { background: #60a5fa; }

/* ── Main area ── */
#main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
#topbar { background: #16161e; border-bottom: 1px solid #2a2a3a; padding: 8px 14px;
          display: flex; align-items: center; gap: 10px; min-height: 44px; flex-wrap: wrap; }
#file-path { font-size: 11px; color: #6366f1; font-family: monospace; flex: 1; min-width: 0;
             white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
button.tb-btn { padding: 5px 12px; border-radius: 5px; font-size: 11px; font-weight: 500;
                cursor: pointer; border: none; white-space: nowrap; }
#edit-btn  { background: #2d2d44; color: #a78bfa; }
#edit-btn:hover { background: #3a3a58; }
#hist-btn  { background: #1e2a1e; color: #4ade80; }
#hist-btn:hover { background: #243224; }
#save-btn  { background: #7c3aed; color: white; display: none; }
#save-btn:hover { background: #6d28d9; }
#cancel-btn { background: #2d2d44; color: #94a3b8; display: none; }
#cancel-btn:hover { background: #3a3a58; }
.badge { font-size: 10px; padding: 2px 6px; border-radius: 8px; font-weight: 600; }
.badge-static  { background: #14532d; color: #4ade80; }
.badge-dynamic { background: #451a03; color: #f59e0b; }
.badge-config  { background: #1e3a5f; color: #60a5fa; }

/* ── Content split: viewer/editor + history panel ── */
#content-area { flex: 1; display: flex; overflow: hidden; }
#center-pane  { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

/* ── Viewer ── */
#viewer { flex: 1; overflow-y: auto; padding: 20px 24px; font-size: 13px; line-height: 1.75; }
#viewer pre { background: #1a1a24; border: 1px solid #2a2a3a; border-radius: 5px; padding: 14px;
              overflow-x: auto; font-size: 11.5px; }
#viewer code { font-family: 'JetBrains Mono', 'Fira Code', monospace; }
#viewer h1 { color: #a78bfa; font-size: 17px; margin-bottom: 10px; padding-bottom: 7px;
             border-bottom: 1px solid #2a2a3a; }
#viewer h2 { color: #818cf8; font-size: 14px; margin: 18px 0 7px; }
#viewer h3 { color: #94a3b8; font-size: 12px; margin: 14px 0 5px; }
#viewer p  { color: #c0c0d0; margin-bottom: 9px; }
#viewer li { color: #c0c0d0; margin-left: 18px; margin-bottom: 4px; }
#viewer table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 11px; }
#viewer th { background: #1e1e2e; color: #94a3b8; padding: 5px 10px; text-align: left;
             border: 1px solid #2a2a3a; }
#viewer td { padding: 4px 10px; border: 1px solid #2a2a3a; color: #c0c0d0; }
#viewer a  { color: #60a5fa; }

/* ── Editor ── */
#editor { flex: 1; display: none; flex-direction: column; }
#edit-comment-bar { background: #12121a; border-bottom: 1px solid #2a2a3a;
                    padding: 6px 14px; display: flex; align-items: center; gap: 8px; }
#edit-comment-bar label { font-size: 11px; color: #64748b; white-space: nowrap; }
#edit-comment { flex: 1; background: #1e1e2e; border: 1px solid #2a2a3a; border-radius: 4px;
                color: #e0e0e0; font-size: 11px; padding: 4px 8px; outline: none; }
#edit-comment:focus { border-color: #4c1d95; }
#editor textarea { flex: 1; background: #0f0f13; color: #e0e0e0; border: none; outline: none;
                   padding: 20px 24px; font-family: 'JetBrains Mono', 'Fira Code', monospace;
                   font-size: 12.5px; line-height: 1.65; resize: none; tab-size: 2; }

/* ── History panel ── */
#history-panel { width: 320px; background: #12121a; border-left: 1px solid #2a2a3a;
                 display: none; flex-direction: column; flex-shrink: 0; overflow: hidden; }
#history-panel.open { display: flex; }
#history-header { padding: 10px 14px; border-bottom: 1px solid #2a2a3a; display: flex;
                  align-items: center; gap: 8px; }
#history-header h3 { font-size: 12px; color: #a78bfa; flex: 1; }
#history-close { background: none; border: none; color: #555; cursor: pointer; font-size: 16px; }
#history-close:hover { color: #e0e0e0; }
#history-list { flex: 1; overflow-y: auto; }
.hist-entry { padding: 10px 14px; border-bottom: 1px solid #1e1e2e; cursor: pointer; }
.hist-entry:hover { background: #1a1a2e; }
.hist-entry.hist-selected { background: #2d1f56; }
.hist-ts { font-size: 10px; color: #555; font-family: monospace; }
.hist-comment { font-size: 11px; color: #a0aec0; margin-top: 2px; font-weight: 500; }
.hist-meta { font-size: 10px; color: #445; margin-top: 2px; }
#history-preview { height: 220px; border-top: 1px solid #2a2a3a; display: flex;
                   flex-direction: column; overflow: hidden; }
#history-preview-header { padding: 6px 14px; background: #0f0f13; display: flex;
                           align-items: center; gap: 8px; border-bottom: 1px solid #1e1e2e; }
#history-preview-header span { font-size: 10px; color: #555; flex: 1; font-family: monospace; }
#restore-btn { padding: 4px 10px; background: #7c3aed; color: white; border: none;
               border-radius: 4px; font-size: 10px; cursor: pointer; }
#restore-btn:hover { background: #6d28d9; }
#restore-btn:disabled { background: #2d2d44; color: #555; cursor: default; }
#history-preview-content { flex: 1; overflow-y: auto; padding: 10px 14px;
                            font-family: monospace; font-size: 10.5px; color: #6a7090;
                            white-space: pre-wrap; line-height: 1.5; }

/* ── Welcome + status ── */
#welcome { flex: 1; display: flex; align-items: center; justify-content: center;
           flex-direction: column; gap: 8px; }
#welcome h2 { font-size: 18px; color: #3a3a5a; }
#welcome p  { font-size: 12px; color: #2a2a40; }
#statusbar { padding: 3px 14px; background: #0a0a10; font-size: 10px; color: #3a3a5a;
             border-top: 1px solid #1a1a24; }
</style>
</head>
<body>

<!-- Sidebar -->
<div id="sidebar">
  <div id="sidebar-header">
    <h1>🧠 Agent Configs</h1>
    <p>Hot-reload · Version history · Restore</p>
  </div>
  <div id="tree">Loading...</div>
</div>

<!-- Main -->
<div id="main">
  <div id="topbar">
    <span id="file-path">Select a file to view</span>
    <span id="file-badge" class="badge" style="display:none"></span>
    <button class="tb-btn" id="hist-btn" onclick="toggleHistory()" style="display:none">🕐 History</button>
    <button class="tb-btn" id="edit-btn" onclick="startEdit()" style="display:none">✏️ Edit</button>
    <button class="tb-btn" id="save-btn" onclick="saveFile()">💾 Save</button>
    <button class="tb-btn" id="cancel-btn" onclick="cancelEdit()">✕ Cancel</button>
  </div>

  <div id="content-area">
    <div id="center-pane">
      <div id="welcome">
        <h2>Tantra AI — Agent Config Dashboard</h2>
        <p>Select a file from the sidebar to view, edit, or browse its history.</p>
        <p style="margin-top:6px;color:#222235;font-size:11px">Hot-reload · All changes apply instantly · Full version history with restore</p>
      </div>
      <div id="viewer" style="display:none"></div>
      <div id="editor" style="display:none">
        <div id="edit-comment-bar">
          <label for="edit-comment">Change description:</label>
          <input id="edit-comment" type="text" placeholder="e.g. improved hook tone, added space mission policy" maxlength="120">
        </div>
        <textarea id="editor-textarea" spellcheck="false"></textarea>
      </div>
    </div>

    <!-- History panel -->
    <div id="history-panel">
      <div id="history-header">
        <h3>🕐 Version History</h3>
        <button id="history-close" onclick="closeHistory()">✕</button>
      </div>
      <div id="history-list"></div>
      <div id="history-preview">
        <div id="history-preview-header">
          <span id="preview-version-label">Select a version to preview</span>
          <button id="restore-btn" onclick="restoreVersion()" disabled>↩ Restore</button>
        </div>
        <div id="history-preview-content"></div>
      </div>
    </div>
  </div>

  <div id="statusbar">Ready</div>
</div>

<script>
const FILE_TYPES = {
  'soul.md':       { label: 'Soul',       cls: 'static',  dot: 'static' },
  'skills.md':     { label: 'Skills',     cls: 'static',  dot: 'static' },
  'policy.md':     { label: 'Policy',     cls: 'static',  dot: 'static' },
  'memory.md':     { label: 'Memory',     cls: 'static',  dot: 'static' },
  'tools.json':    { label: 'Tools',      cls: 'config',  dot: 'config' },
  'reflection.md': { label: 'Reflection', cls: 'dynamic', dot: 'dynamic' },
  'learning.md':   { label: 'Learning',   cls: 'dynamic', dot: 'dynamic' },
  'feedback.md':   { label: 'Feedback',   cls: 'dynamic', dot: 'dynamic' },
  'evaluation.md': { label: 'Evaluation', cls: 'dynamic', dot: 'dynamic' },
};
const EDITABLE = ['.md', '.json'];
const ORDER = ['soul.md','skills.md','policy.md','memory.md','tools.json',
               'reflection.md','learning.md','feedback.md','evaluation.md'];

let currentPath = null;
let currentContent = null;
let editing = false;
let selectedVersion = null;

// ── Markdown renderer ─────────────────────────────────────────────────────
function renderMd(md) {
  let h = md
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/```[a-z]*\\n([\\s\\S]*?)```/g,(_,c)=>`<pre><code>${c}</code></pre>`)
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/^### (.+)$/gm,'<h3>$1</h3>').replace(/^## (.+)$/gm,'<h2>$1</h2>').replace(/^# (.+)$/gm,'<h1>$1</h1>')
    .replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>').replace(/\\*([^*]+)\\*/g,'<em>$1</em>')
    .replace(/^- \\[x\\] (.+)$/gm,'<li>☑ $1</li>').replace(/^- \\[ \\] (.+)$/gm,'<li>☐ $1</li>')
    .replace(/^- (.+)$/gm,'<li>$1</li>').replace(/^\\d+\\. (.+)$/gm,'<li>$1</li>')
    .replace(/(<li>[\\s\\S]*?<\\/li>\\n?)+/g,m=>`<ul>${m}</ul>`)
    .replace(/^\\| (.+) \\|$/gm,(_,row)=>'<tr>'+row.split(' | ').map(c=>`<td>${c.trim()}</td>`).join('')+'</tr>')
    .replace(/(<tr>[\\s\\S]*?<\\/tr>\\n?)+/g,m=>`<table><tr><th>${m.replace(/<td>/g,'<th>').replace(/<\\/td>/g,'<\\/th>').replace(/<tr>/,'').replace(/<\\/tr>\\n?/,'')}</th></tr>${m}</table>`)
    .replace(/^---$/gm,'<hr style="border-color:#2a2a3a;margin:14px 0">')
    .replace(/^(?!<[h|t|u|l|p|h])(.+)$/gm,'<p>$1</p>');
  return h;
}

// ── Tree ──────────────────────────────────────────────────────────────────
function renderTree(node, container) {
  if (node.type === 'file') {
    if (node.name === 'CHANGELOG.json') return;
    const info = FILE_TYPES[node.name] || { cls: 'static', dot: 'static' };
    const el = document.createElement('div');
    el.className = 'tree-file tree-item';
    el.dataset.path = node.path;
    el.innerHTML = `<span class="dot dot-${info.dot}"></span><span>${node.name}</span>`;
    el.addEventListener('click', () => openFile(node.path, el));
    container.appendChild(el);
  } else {
    if (node.name === '_framework' || node.name === '.history') return;
    const wrap = document.createElement('div');
    const lbl = document.createElement('div');
    lbl.className = 'tree-dir tree-item';
    const ico = document.createElement('span'); ico.className = 'icon'; ico.textContent = '▾';
    const nm = document.createElement('span'); nm.className = 'name'; nm.textContent = node.name || 'agents';
    lbl.appendChild(ico); lbl.appendChild(nm);
    const kids = document.createElement('div'); kids.className = 'tree-children';
    const dirs  = (node.children||[]).filter(c=>c.type==='directory' && c.name!=='.history');
    const files = (node.children||[]).filter(c=>c.type==='file' && c.name!=='CHANGELOG.json');
    files.sort((a,b)=>{
      const ai=ORDER.indexOf(a.name), bi=ORDER.indexOf(b.name);
      if(ai===-1&&bi===-1)return a.name.localeCompare(b.name);
      return ai===-1?1:bi===-1?-1:ai-bi;
    });
    [...dirs,...files].forEach(child=>renderTree(child,kids));
    lbl.addEventListener('click',()=>{
      const open=kids.style.display!=='none';
      kids.style.display=open?'none':'block';
      ico.textContent=open?'▸':'▾';
    });
    wrap.appendChild(lbl); wrap.appendChild(kids);
    container.appendChild(wrap);
  }
}

// ── File open ─────────────────────────────────────────────────────────────
async function openFile(path, el) {
  if (editing && !confirm('Discard unsaved changes?')) return;
  cancelEdit();
  document.querySelectorAll('.tree-file').forEach(e=>e.classList.remove('active'));
  el.classList.add('active');
  currentPath = path;
  const name = path.split('/').pop();
  document.getElementById('file-path').textContent = 'agents/' + path;
  const badge = document.getElementById('file-badge');
  const info = FILE_TYPES[name] || { label: name.replace(/\\..+/,''), cls: 'static' };
  badge.textContent = info.label || name;
  badge.className = 'badge badge-' + info.cls;
  badge.style.display = 'inline-block';
  const editable = EDITABLE.some(s=>name.endsWith(s));
  document.getElementById('edit-btn').style.display = editable ? 'block' : 'none';
  document.getElementById('hist-btn').style.display = editable ? 'block' : 'none';
  setStatus('Loading...');
  try {
    const res = await fetch('/api/v1/agents/file?path='+encodeURIComponent(path));
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    currentContent = data.content;
    showViewer(name, data.content);
    setStatus('agents/' + path + ' · ' + data.size + ' bytes');
  } catch(e) { setStatus('Error: '+e.message); }
}

function showViewer(name, content) {
  document.getElementById('welcome').style.display = 'none';
  document.getElementById('viewer').style.display = 'block';
  document.getElementById('editor').style.display = 'none';
  document.getElementById('save-btn').style.display = 'none';
  document.getElementById('cancel-btn').style.display = 'none';
  editing = false;
  if (name.endsWith('.json')) {
    try {
      document.getElementById('viewer').innerHTML = '<pre><code>' +
        JSON.stringify(JSON.parse(content),null,2).replace(/</g,'&lt;') + '</code></pre>';
    } catch { document.getElementById('viewer').textContent = content; }
  } else {
    document.getElementById('viewer').innerHTML = renderMd(content);
  }
}

function startEdit() {
  document.getElementById('viewer').style.display = 'none';
  document.getElementById('editor').style.display = 'flex';
  document.getElementById('edit-btn').style.display = 'none';
  document.getElementById('hist-btn').style.display = 'none';
  document.getElementById('save-btn').style.display = 'block';
  document.getElementById('cancel-btn').style.display = 'block';
  document.getElementById('editor-textarea').value = currentContent;
  document.getElementById('edit-comment').value = '';
  document.getElementById('editor-textarea').focus();
  editing = true;
  setStatus('Editing agents/' + currentPath + ' — add a description then Save');
}

function cancelEdit() {
  if (!currentPath) return;
  const name = currentPath.split('/').pop();
  const editable = EDITABLE.some(s=>name.endsWith(s));
  showViewer(name, currentContent);
  document.getElementById('edit-btn').style.display = editable ? 'block' : 'none';
  document.getElementById('hist-btn').style.display = editable ? 'block' : 'none';
  editing = false;
}

async function saveFile() {
  const content = document.getElementById('editor-textarea').value;
  const comment = document.getElementById('edit-comment').value.trim() || 'update';
  setStatus('Saving...');
  try {
    const res = await fetch('/api/v1/agents/file', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ path: currentPath, content, comment }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    currentContent = content;
    const name = currentPath.split('/').pop();
    showViewer(name, content);
    document.getElementById('edit-btn').style.display = 'block';
    document.getElementById('hist-btn').style.display = 'block';
    const entry = data.history_entry || {};
    setStatus('✓ Saved · "' + (entry.comment||comment) + '" · v:' + (entry.version_file||'') + ' · hot-reloaded');
    // Refresh history panel if open
    if (document.getElementById('history-panel').classList.contains('open')) loadHistory();
  } catch(e) { setStatus('❌ Save failed: ' + e.message); }
}

// ── History panel ─────────────────────────────────────────────────────────
async function toggleHistory() {
  const panel = document.getElementById('history-panel');
  if (panel.classList.contains('open')) { closeHistory(); return; }
  panel.classList.add('open');
  await loadHistory();
}

function closeHistory() {
  document.getElementById('history-panel').classList.remove('open');
  selectedVersion = null;
}

async function loadHistory() {
  if (!currentPath) return;
  const list = document.getElementById('history-list');
  list.innerHTML = '<div style="padding:14px;font-size:11px;color:#444">Loading...</div>';
  selectedVersion = null;
  document.getElementById('restore-btn').disabled = true;
  document.getElementById('history-preview-content').textContent = '';
  document.getElementById('preview-version-label').textContent = 'Select a version to preview';
  try {
    const res = await fetch('/api/v1/agents/history?path='+encodeURIComponent(currentPath));
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const versions = data.versions || [];
    if (!versions.length) {
      list.innerHTML = '<div style="padding:14px;font-size:11px;color:#444">No history yet.<br>Save a change to create the first version.</div>';
      return;
    }
    list.innerHTML = '';
    versions.forEach((v, i) => {
      const el = document.createElement('div');
      el.className = 'hist-entry';
      const date = v.ts ? new Date(v.ts).toLocaleString() : v.ts;
      const actor = v.actor === 'agent' ? '🤖' : v.actor === 'system' ? '⚙️' : '👤';
      el.innerHTML = `
        <div class="hist-ts">${date} ${actor}</div>
        <div class="hist-comment">${escHtml(v.comment||'update')}</div>
        <div class="hist-meta">${(v.size||0).toLocaleString()} bytes · ${escHtml(v.version_file||'')}</div>`;
      el.addEventListener('click', () => previewVersion(v, el));
      if (i === 0) el.innerHTML += '<div style="font-size:9px;color:#4ade80;margin-top:2px">◆ current</div>';
      list.appendChild(el);
    });
  } catch(e) {
    list.innerHTML = `<div style="padding:14px;font-size:11px;color:#f87171">Error: ${e.message}</div>`;
  }
}

async function previewVersion(v, el) {
  document.querySelectorAll('.hist-entry').forEach(e=>e.classList.remove('hist-selected'));
  el.classList.add('hist-selected');
  selectedVersion = v;
  document.getElementById('preview-version-label').textContent = v.version_file || '';
  document.getElementById('restore-btn').disabled = false;
  document.getElementById('history-preview-content').textContent = 'Loading...';
  try {
    const res = await fetch('/api/v1/agents/version?path='+encodeURIComponent(currentPath)
                           +'&version_file='+encodeURIComponent(v.version_file));
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    document.getElementById('history-preview-content').textContent = data.content;
  } catch(e) {
    document.getElementById('history-preview-content').textContent = 'Error: ' + e.message;
  }
}

async function restoreVersion() {
  if (!selectedVersion) return;
  const comment = prompt('Restore comment (optional):', 'restored from ' + (selectedVersion.ts||'').slice(0,10));
  if (comment === null) return; // cancelled
  setStatus('Restoring...');
  try {
    const res = await fetch('/api/v1/agents/restore', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ path: currentPath, version_file: selectedVersion.version_file, comment: comment||'' }),
    });
    if (!res.ok) throw new Error(await res.text());
    // Reload live file
    const fileRes = await fetch('/api/v1/agents/file?path='+encodeURIComponent(currentPath));
    const fileData = await fileRes.json();
    currentContent = fileData.content;
    showViewer(currentPath.split('/').pop(), currentContent);
    await loadHistory();
    setStatus('✓ Restored — "' + (comment||'restored') + '" · hot-reloaded on next LLM call');
  } catch(e) { setStatus('❌ Restore failed: ' + e.message); }
}

function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function setStatus(msg) { document.getElementById('statusbar').textContent = msg; }

// ── Init ──────────────────────────────────────────────────────────────────
async function init() {
  try {
    const res = await fetch('/api/v1/agents/tree');
    if (!res.ok) throw new Error('tree load failed');
    const tree = await res.json();
    const c = document.getElementById('tree');
    c.innerHTML = '';
    renderTree(tree, c);
  } catch(e) {
    document.getElementById('tree').textContent = 'Error: ' + e.message;
  }
}

window.addEventListener('beforeunload', e => { if(editing){e.preventDefault();e.returnValue='';} });
init();
</script>
</body>
</html>"""
