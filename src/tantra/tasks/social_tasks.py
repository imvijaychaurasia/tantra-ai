"""
Tantra AI — Social Media Celery Tasks (Phase 1)

Tasks:
  research_and_draft_posts        — Run Social Crew → parse 3 LinkedIn drafts
                                    → insert to content_queue → fire n8n webhook
  publish_approved_linkedin_posts — Pull approved posts from queue
                                    → retrieve encrypted token
                                    → post to LinkedIn API
                                    → mark published / failed

Both tasks use sync DB access via SQLAlchemy (sync engine + asyncio bridge).
Celery workers are synchronous, so we run async helpers in asyncio.run().
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Optional

import httpx

from tantra.tasks.celery_app import app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — sync DB operations (used inside Celery tasks)
# ---------------------------------------------------------------------------

def _get_sync_session():
    """
    Build a synchronous SQLAlchemy session.
    Used inside Celery workers where async is not available natively.
    We use the sync database_url from settings (postgresql:// not asyncpg).
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from tantra.core.config import settings

    engine = create_engine(settings.database_sync_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    return Session()


def _insert_content_queue_item(
    draft_text: str,
    hashtags: str,
    research_context: str,
    platform: str = "linkedin",
    user_id: Optional[uuid.UUID] = None,
) -> str:
    """Insert a new ContentQueueItem and return the item UUID as string."""
    from tantra.db.social import ContentQueueItem

    session = _get_sync_session()
    try:
        item = ContentQueueItem(
            user_id=user_id,
            platform=platform,
            draft_text=draft_text,
            hashtags=hashtags,
            research_context=research_context[:2000] if research_context else None,
            status="draft",
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        item_id = str(item.id)
        logger.info(f"ContentQueueItem created: {item_id}")
        return item_id
    finally:
        session.close()


def _get_approved_items(platform: str = "linkedin") -> list[dict]:
    """Return all approved content items for the given platform."""
    from tantra.db.social import ContentQueueItem

    session = _get_sync_session()
    try:
        items = (
            session.query(ContentQueueItem)
            .filter(
                ContentQueueItem.status == "approved",
                ContentQueueItem.platform == platform,
            )
            .order_by(ContentQueueItem.approved_at)
            .all()
        )
        return [
            {
                "id": str(item.id),
                "user_id": str(item.user_id) if item.user_id else None,
                "draft_text": item.draft_text,
                "hashtags": item.hashtags or "",
            }
            for item in items
        ]
    finally:
        session.close()


def _get_linkedin_token(user_id: Optional[str]) -> Optional[dict]:
    """
    Retrieve and decrypt the LinkedIn token for a user.
    Returns dict with access_token and profile_urn, or None if not found.
    Falls back to the most recently updated connection if user_id is None.
    """
    from tantra.db.social import SocialConnection
    from tantra.core.crypto import decrypt_token

    session = _get_sync_session()
    try:
        if user_id:
            conn = (
                session.query(SocialConnection)
                .filter(
                    SocialConnection.platform == "linkedin",
                    SocialConnection.user_id == uuid.UUID(user_id),
                )
                .order_by(SocialConnection.updated_at.desc())
                .first()
            )
        else:
            # Single-user mode: get the most recently updated LinkedIn connection
            conn = (
                session.query(SocialConnection)
                .filter(SocialConnection.platform == "linkedin")
                .order_by(SocialConnection.updated_at.desc())
                .first()
            )

        if not conn:
            return None

        # Check expiry
        if conn.expires_at and conn.expires_at < datetime.utcnow():
            logger.warning("LinkedIn token expired for sub=%s", conn.profile_sub)
            return None

        return {
            "access_token": decrypt_token(conn.access_token_enc),
            "profile_urn": conn.profile_urn or "",
            "profile_name": conn.profile_name or "",
        }
    finally:
        session.close()


def _mark_item_published(item_id: str, post_urn: str) -> None:
    """Update a ContentQueueItem to status='published'."""
    from tantra.db.social import ContentQueueItem

    session = _get_sync_session()
    try:
        item = session.query(ContentQueueItem).filter(
            ContentQueueItem.id == uuid.UUID(item_id)
        ).first()
        if item:
            item.status = "published"
            item.post_urn = post_urn
            item.published_at = datetime.utcnow()
            session.commit()
    finally:
        session.close()


def _mark_item_failed(item_id: str, error: str) -> None:
    """Update a ContentQueueItem to status='failed'."""
    from tantra.db.social import ContentQueueItem

    session = _get_sync_session()
    try:
        item = session.query(ContentQueueItem).filter(
            ContentQueueItem.id == uuid.UUID(item_id)
        ).first()
        if item:
            item.status = "failed"
            item.publish_error = error[:500]
            session.commit()
    finally:
        session.close()


def _update_n8n_execution_id(item_id: str, execution_id: str) -> None:
    """Store the n8n execution ID on a ContentQueueItem."""
    from tantra.db.social import ContentQueueItem

    session = _get_sync_session()
    try:
        item = session.query(ContentQueueItem).filter(
            ContentQueueItem.id == uuid.UUID(item_id)
        ).first()
        if item:
            item.n8n_execution_id = execution_id
            session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Post parsing — extract individual posts from crew output
# ---------------------------------------------------------------------------

def _parse_linkedin_posts(crew_output: str) -> list[dict]:
    """
    Parse the Content Writer's output into individual posts.

    Expected format (CrewAI task output):
        POST 1
        <text>

        POST 2
        <text>

        POST 3
        <text>

    Returns list of dicts: [{text, hashtags}, ...]
    """
    posts = []
    # Split on POST N labels (case-insensitive, handles POST 1 / POST 2 / POST 3)
    sections = re.split(r"(?i)\bPOST\s+\d+\b", crew_output)
    # First element is anything before POST 1 (usually empty or a preamble)
    for section in sections[1:]:
        text = section.strip()
        if not text:
            continue

        # Extract hashtags from the post body
        hashtag_matches = re.findall(r"#\w+", text)
        hashtags = ",".join(hashtag_matches)

        posts.append({"text": text, "hashtags": hashtags})

    # Fallback: if no POST N labels found, treat entire output as one post
    if not posts and len(crew_output.strip()) > 50:
        hashtags = ",".join(re.findall(r"#\w+", crew_output))
        posts.append({"text": crew_output.strip(), "hashtags": hashtags})

    return posts[:3]  # Cap at 3 posts per run


# ---------------------------------------------------------------------------
# n8n webhook trigger
# ---------------------------------------------------------------------------

def _trigger_n8n_approval(item_id: str, draft_text: str, hashtags: str) -> Optional[str]:
    """
    POST a content draft to n8n for human approval.
    Returns the n8n execution ID if successful.
    """
    from tantra.core.config import settings

    payload = {
        "item_id": item_id,
        "draft_text": draft_text,
        "hashtags": hashtags,
        "platform": "linkedin",
        "approve_url": f"{settings.n8n_approval_callback_base}/{item_id}/approve",
        "reject_url": f"{settings.n8n_approval_callback_base}/{item_id}/reject",
        "created_at": datetime.utcnow().isoformat(),
    }

    try:
        resp = httpx.post(
            settings.n8n_content_draft_webhook,
            json=payload,
            timeout=10.0,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            execution_id = data.get("executionId", data.get("id", ""))
            logger.info(f"n8n approval triggered for item {item_id}: execution={execution_id}")
            return str(execution_id) if execution_id else None
        else:
            logger.warning(
                f"n8n webhook returned {resp.status_code} for item {item_id}: {resp.text[:200]}"
            )
    except httpx.ConnectError:
        logger.warning(
            "n8n is not reachable at %s — skipping webhook trigger for item %s",
            settings.n8n_content_draft_webhook, item_id,
        )
    except Exception as exc:
        logger.error(f"n8n webhook failed for item {item_id}: {exc}")

    return None


# ---------------------------------------------------------------------------
# Task 1 — Research → Draft → Queue → n8n
# ---------------------------------------------------------------------------

@app.task(bind=True, name="tantra.tasks.social.research_and_draft_posts", queue="agents")
def research_and_draft_posts(self, platform: str = "linkedin") -> dict:
    """
    Full content pipeline:
      1. Run the Social Media CrewAI crew (Researcher → ContentWriter)
      2. Parse 3 LinkedIn post drafts from the crew output
      3. Insert each draft into content_queue (status=draft)
      4. Fire n8n webhook for each draft → human approval
      5. Return summary of created items

    This task is CPU/IO heavy (~2-5 min with local models).
    Runs on the 'agents' queue.
    """
    logger.info("Starting research_and_draft_posts task")

    # ── Step 1: Run the Social Crew ──────────────────────────────────────────
    from tantra.crews.social_crew import build_social_media_crew

    try:
        crew = build_social_media_crew(verbose=True)
        result = crew.kickoff()
        # CrewAI returns a CrewOutput object; get the string representation
        crew_output = str(result) if result else ""
        logger.info(f"Social crew completed. Output length: {len(crew_output)} chars")
    except Exception as exc:
        logger.error(f"Social crew failed: {exc}", exc_info=True)
        return {"success": False, "error": str(exc), "items_created": 0}

    # ── Step 2: Find the LinkedIn posts in the crew output ───────────────────
    # The crew runs tasks in order: research → linkedin posts → youtube script → analysis
    # The linkedin post output is the second task result.
    # CrewAI stores individual task outputs in result.tasks_output
    linkedin_post_text_raw = ""
    try:
        if hasattr(result, "tasks_output") and len(result.tasks_output) >= 2:
            linkedin_post_text_raw = str(result.tasks_output[1])
        else:
            linkedin_post_text_raw = crew_output
    except Exception:
        linkedin_post_text_raw = crew_output

    posts = _parse_linkedin_posts(linkedin_post_text_raw)
    if not posts:
        logger.warning("No posts parsed from crew output")
        return {"success": False, "error": "No posts parsed from crew output", "items_created": 0}

    # ── Step 3 + 4: Insert each draft + trigger n8n ──────────────────────────
    created_items = []
    for post in posts:
        try:
            item_id = _insert_content_queue_item(
                draft_text=post["text"],
                hashtags=post["hashtags"],
                research_context=crew_output[:2000],
                platform=platform,
            )
            # Fire n8n approval webhook
            exec_id = _trigger_n8n_approval(
                item_id=item_id,
                draft_text=post["text"],
                hashtags=post["hashtags"],
            )
            if exec_id:
                _update_n8n_execution_id(item_id, exec_id)

            created_items.append({"item_id": item_id, "n8n_execution_id": exec_id})
            logger.info(f"Draft created: item_id={item_id}")
        except Exception as exc:
            logger.error(f"Failed to create content item: {exc}", exc_info=True)

    return {
        "success": True,
        "items_created": len(created_items),
        "items": created_items,
    }


# ---------------------------------------------------------------------------
# Task 2 — Publish approved posts from content_queue
# ---------------------------------------------------------------------------

@app.task(bind=True, name="tantra.tasks.social.publish_approved_linkedin_posts", queue="social")
def publish_approved_linkedin_posts(self) -> dict:
    """
    Publish all approved LinkedIn posts from the content queue.

    Called by Celery beat every weekday at 9 AM.
    For each approved item:
      1. Retrieve and decrypt the LinkedIn OAuth token
      2. Call LinkedIn UGC Posts API
      3. Update item to 'published' or 'failed'

    In practice this will publish one post per morning — the CMO agent only
    approves the best draft for each cycle.
    """
    logger.info("Starting publish_approved_linkedin_posts task")

    approved = _get_approved_items(platform="linkedin")
    if not approved:
        logger.info("No approved LinkedIn posts in queue")
        return {"success": True, "published": 0, "message": "No approved posts to publish"}

    published = 0
    failed = 0
    results = []

    for item in approved:
        item_id = item["id"]
        user_id = item.get("user_id")

        # ── Resolve credentials (Zernio-first, direct OAuth fallback) ───────────
        from tantra.core.config import settings as _cfg

        access_token = ""
        author_urn = ""

        if not _cfg.zernio_enabled:
            # Only retrieve the direct LinkedIn token when Zernio is not configured
            token_info = _get_linkedin_token(user_id)
            if not token_info:
                err = (
                    f"No valid LinkedIn token found (user_id={user_id}). "
                    "Either configure Zernio (ZERNIO_API_KEY) or connect LinkedIn "
                    "directly via /auth/linkedin."
                )
                logger.error(err)
                _mark_item_failed(item_id, err)
                failed += 1
                results.append({"item_id": item_id, "status": "failed", "error": err})
                continue
            access_token = token_info["access_token"]
            author_urn = token_info["profile_urn"]
            if not author_urn:
                err = "LinkedIn author URN is missing — reconnect LinkedIn OAuth"
                logger.error(err)
                _mark_item_failed(item_id, err)
                failed += 1
                results.append({"item_id": item_id, "status": "failed", "error": err})
                continue

        # ── Build post text (body + hashtags) ─────────────────────────────────
        draft_text = item["draft_text"]
        hashtags = item["hashtags"]
        if hashtags and not any(tag in draft_text for tag in hashtags.split(",")):
            # Append hashtags if not already embedded in the draft
            post_text = f"{draft_text}\n\n{hashtags.replace(',', ' ')}"
        else:
            post_text = draft_text

        # ── Publish: Zernio (primary) → direct LinkedIn API (fallback) ──────────
        try:
            if _cfg.zernio_enabled:
                # ── Zernio path (preferred — no OAuth token management) ────────
                from tantra.tools.zernio_client import ZernioClient
                zernio = ZernioClient()
                post_result = asyncio.run(
                    zernio.post_text(
                        content=post_text,
                        platform="linkedin",
                        account_id=_cfg.zernio_linkedin_account_id or None,
                    )
                )
                # Normalise to common shape
                if post_result.get("success"):
                    post_result["post_urn"] = post_result.get("post_id", "")
            else:
                # ── Direct LinkedIn API fallback (requires SocialConnection token) ─
                if not access_token or not author_urn:
                    post_result = {
                        "success": False,
                        "error": (
                            "Zernio not configured (ZERNIO_API_KEY missing) and no "
                            "direct LinkedIn token found. Connect LinkedIn via Zernio "
                            "at https://zernio.com/dashboard, or set LINKEDIN_CLIENT_ID "
                            "and complete the OAuth flow at /auth/linkedin."
                        ),
                    }
                else:
                    from tantra.tools.linkedin import linkedin_post_text as _publish
                    post_result = asyncio.run(
                        _publish(
                            access_token=access_token,
                            author_urn=author_urn,
                            text=post_text,
                            visibility="PUBLIC",
                        )
                    )

            if post_result.get("success"):
                post_urn = post_result.get("post_urn", "")
                _mark_item_published(item_id, post_urn)
                published += 1
                logger.info(f"Published LinkedIn post: {post_urn}")
                results.append({"item_id": item_id, "status": "published", "post_urn": post_urn})
            else:
                err = post_result.get("error", "Unknown LinkedIn API error")
                _mark_item_failed(item_id, err)
                failed += 1
                logger.error(f"LinkedIn publish failed for item {item_id}: {err}")
                results.append({"item_id": item_id, "status": "failed", "error": err})

        except Exception as exc:
            err = str(exc)
            _mark_item_failed(item_id, err)
            failed += 1
            logger.error(f"Unexpected error publishing item {item_id}: {exc}", exc_info=True)
            results.append({"item_id": item_id, "status": "failed", "error": err})

    return {
        "success": True,
        "published": published,
        "failed": failed,
        "results": results,
    }
