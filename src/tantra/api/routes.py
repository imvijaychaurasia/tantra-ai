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
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
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
