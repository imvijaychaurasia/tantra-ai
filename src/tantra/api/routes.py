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

    # Phase 3b: queue produce task
    # For now, log that production is pending Phase 3b
    import logging
    _logger = logging.getLogger(__name__)
    _logger.info(
        "YouTube script approved: video_id=%s title=%r approved_by=%s. "
        "Production pipeline (Phase 3b) not yet implemented — video stays in 'approved'.",
        str(video_id), video.title, body.approved_by,
    )

    return JSONResponse({
        "success": True,
        "video_id": str(video_id),
        "status": "approved",
        "message": (
            "Script approved. Video queued for production when Phase 3b "
            "tantra-media service is deployed."
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
