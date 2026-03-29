"""
Tantra AI — Social Media Celery Tasks (Phase 1)

Tasks:
  research_and_draft_posts        — Run Social Crew → parse 3 LinkedIn drafts
                                    → insert to content_queue → fire n8n webhook
  publish_approved_linkedin_posts — Pull approved posts from queue
                                    → retrieve encrypted token
                                    → post to LinkedIn API
                                    → mark published / failed
  linkedin_engage_feed            — Fetch recent LinkedIn posts → find AI topics
                                    → generate human comment → post reply
  post_tantra_progress            — LLM writes a short human-tone post about
                                    the current Tantra AI build phase → publish

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
    # Sequential crew task order: [0] research, [1] write_linkedin, [2] write_youtube, [3] analyse
    # tasks_output[1] is the content writer's LinkedIn posts.
    linkedin_post_text_raw = ""
    try:
        if hasattr(result, "tasks_output") and len(result.tasks_output) >= 2:
            raw1 = result.tasks_output[1]
            # CrewAI TaskOutput: .raw is the string content, fallback to str()
            linkedin_post_text_raw = getattr(raw1, "raw", None) or str(raw1)
            logger.info(f"Using tasks_output[1] ({len(linkedin_post_text_raw)} chars) for post parsing")
        else:
            linkedin_post_text_raw = crew_output
    except Exception:
        linkedin_post_text_raw = crew_output

    posts = _parse_linkedin_posts(linkedin_post_text_raw)

    # Last-resort fallback: scan entire crew output for POST N labels
    if not posts and linkedin_post_text_raw != crew_output:
        logger.info("Falling back to full crew output for post parsing")
        posts = _parse_linkedin_posts(crew_output)

    if not posts:
        logger.warning("No posts parsed from crew output — storing raw output as single draft")
        # Store whatever the writer produced so it isn't lost; user can edit before approval
        hashtags = ",".join(re.findall(r"#\w+", linkedin_post_text_raw))
        posts = [{"text": linkedin_post_text_raw.strip()[:3000], "hashtags": hashtags}]

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
                # Normalise to common shape — prefer platform_post_id (e.g. urn:li:share:...)
                if post_result.get("success"):
                    post_result["post_urn"] = (
                        post_result.get("platform_post_id")
                        or post_result.get("post_id")
                        or ""
                    )
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


# ---------------------------------------------------------------------------
# Task 3 — LinkedIn Feed Monitor: find AI posts → comment in human tone
# ---------------------------------------------------------------------------

# Human writing style instructions (replaces clawhub human-writing skill)
_HUMAN_COMMENT_STYLE = """
You write LinkedIn comments like a real person — not a marketing bot.

Rules (non-negotiable):
- Under 60 words. Be brief.
- Write exactly like you'd text a smart colleague. No formality.
- No emojis, no bullet points, no "great post!", no "absolutely agree!"
- No AI/business jargon: never use "leverage", "synergy", "space", "ecosystem",
  "paradigm", "utilize", "moving the needle", "thought leadership"
- Share ONE specific thought or question. Not a summary of what they said.
- It should sound like it came from a real engineering manager, not a content bot.
- No hashtags in comments.
"""

_AI_TOPIC_KEYWORDS = [
    "artificial intelligence", "machine learning", "llm", "large language model",
    "gpt", "claude", "gemini", "ollama", "ai agent", "automation", "generative ai",
    "deep learning", "neural network", "openai", "anthropic", "copilot", "chatbot",
    "rag", "vector", "embedding", "fine-tuning", "agentic",
]


def _is_ai_post(text: str) -> bool:
    """Return True if post text is likely about AI/ML topics."""
    t = text.lower()
    return any(kw in t for kw in _AI_TOPIC_KEYWORDS)


def _get_redis_client():
    """Get a Redis client for deduplication state."""
    import redis
    from tantra.core.config import settings
    # Redis URL is broker URL but database 0 (broker uses 1)
    redis_url = settings.celery_broker_url.replace("/1", "/3")
    return redis.from_url(redis_url, decode_responses=True)


def _already_commented(post_id: str) -> bool:
    """Check Redis to avoid double-commenting on the same post."""
    try:
        r = _get_redis_client()
        return r.exists(f"tantra:commented:{post_id}") == 1
    except Exception:
        return False


def _mark_commented(post_id: str, ttl_seconds: int = 86400 * 7) -> None:
    """Mark a post as commented in Redis (7-day TTL)."""
    try:
        r = _get_redis_client()
        r.setex(f"tantra:commented:{post_id}", ttl_seconds, "1")
    except Exception as exc:
        logger.warning("Redis mark_commented failed: %s", exc)


def _llm_generate(prompt: str, system: str, max_tokens: int = 200) -> str:
    """Call LiteLLM proxy (worker tier) for a quick text generation."""
    import litellm
    from tantra.core.config import settings, ModelTier

    base_url = f"{settings.litellm_base_url}/v1"
    api_key = settings.litellm_key

    response = litellm.completion(
        model=f"openai/{ModelTier.worker.value}",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        api_base=base_url,
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=0.8,
    )
    return response.choices[0].message.content.strip()


@app.task(bind=True, name="tantra.tasks.social.linkedin_engage_feed", queue="social")
def linkedin_engage_feed(self) -> dict:
    """
    Fetch recent LinkedIn posts from the user's account history,
    find posts about AI topics, and comment on ones not yet engaged.

    Deduplication: Redis key per post_id (7-day TTL) prevents double-comments.
    Rate-safe: only comments on up to 2 posts per run.

    Note on LinkedIn feed access: Zernio is a publishing tool; reading
    OTHER people's feed posts requires LinkedIn's native feed API (restricted).
    This task works with the user's own published posts and their engagement data.
    To engage with others' posts, connect LinkedIn's native feed API when available.
    """
    logger.info("Starting linkedin_engage_feed task")

    from tantra.core.config import settings as _cfg
    from tantra.tools.zernio_client import ZernioClient, _obj_to_dict

    if not _cfg.zernio_enabled:
        return {"success": False, "error": "Zernio not configured"}

    try:
        zernio = ZernioClient()
    except Exception as exc:
        return {"success": False, "error": f"ZernioClient init failed: {exc}"}

    # Step 1: Fetch recent published posts from the account
    try:
        posts_raw = asyncio.run(zernio.list_posts(status="published", limit=20))
    except Exception as exc:
        logger.error("list_posts failed: %s", exc)
        return {"success": False, "error": str(exc)}

    # Step 2: Filter for AI-topic posts not yet engaged
    ai_posts = []
    for p in posts_raw:
        pd = _obj_to_dict(p) if not isinstance(p, dict) else p
        post_id = pd.get("field_id") or pd.get("_id") or pd.get("id", "")
        content = pd.get("content", pd.get("text", ""))
        if not post_id or not content:
            continue
        if _is_ai_post(content) and not _already_commented(post_id):
            ai_posts.append({"id": post_id, "content": content})

    if not ai_posts:
        logger.info("No new AI-topic posts to engage with")
        return {"success": True, "engaged": 0, "message": "No new posts to engage"}

    # Step 3: Generate and post comments (max 2 per run)
    engaged = 0
    errors = []
    for post in ai_posts[:2]:
        try:
            comment_text = _llm_generate(
                prompt=(
                    f"Post content:\n{post['content'][:600]}\n\n"
                    "Write a short, genuine comment to add to this post."
                ),
                system=_HUMAN_COMMENT_STYLE,
                max_tokens=120,
            )
            comment_text = comment_text.strip().strip('"')

            # Attempt to post comment via Zernio comments API (may not be supported)
            try:
                comment_result = asyncio.run(
                    zernio._client.comments.acreate(
                        post_id=post["id"],
                        content=comment_text,
                        account_id=_cfg.zernio_linkedin_account_id,
                    )
                )
                logger.info("Comment posted on post %s: %s", post["id"], comment_text[:80])
                _mark_commented(post["id"])
                engaged += 1
            except AttributeError:
                # Zernio SDK doesn't support comments yet — log for manual review
                logger.info(
                    "COMMENT READY (Zernio comments API not yet available):\n"
                    "Post ID: %s\nComment: %s",
                    post["id"], comment_text,
                )
                _mark_commented(post["id"])  # Mark to avoid regenerating same comment
                engaged += 1
            except Exception as ce:
                logger.warning("Comment failed for post %s: %s", post["id"], ce)
                errors.append(str(ce))

        except Exception as exc:
            logger.error("Comment generation failed: %s", exc)
            errors.append(str(exc))

    return {
        "success": True,
        "engaged": engaged,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Task 4 — Post about Tantra AI progress in human, jargon-free tone
# ---------------------------------------------------------------------------

# Context the LLM uses to write about the build journey
_TANTRA_BUILD_CONTEXT = """
You are Vijay Chaurasia, an engineering manager building a personal AI project called Tantra AI on the side.
Here is the current status:
- Phase 1 is done: you built a pipeline that automatically researches topics, writes LinkedIn posts, and publishes them
- The stack runs locally on your own machine using open-source AI models
- Phase 2 is starting: building a team of AI agents that work together, each with a specific role (like a company structure)
- Recent struggles: AI models kept running out of memory, had to figure out which smaller model to use as a fallback.
  Docker networking tripped you up — inside containers, "localhost" doesn't point where you think it does.
- What surprised you: once the pieces clicked together, watching the system write and post real content on its own felt genuinely surreal.
- You post about this to share the journey, not to be an influencer.
"""

_HUMAN_POST_STYLE = """
Write a short LinkedIn post (under 150 words) about the Tantra AI project.

Rules:
- First person, conversational. Like you're talking to a colleague over coffee.
- Pick ONE specific thing from the context — one moment, one failure, one win. Not a summary.
- Short sentences. Vary their length.
- No emojis, no bullet points, no headers.
- No jargon: never use "leverage", "ecosystem", "synergy", "utilize", "paradigm",
  "deep dive", "thought leadership", "exciting journey", "moving the needle".
- No corporate phrases: "I'm thrilled to share", "proud to announce"
- Be honest. If something was hard or broke, say that.
- End with a plain observation, not a question or CTA.
- Maximum 2 hashtags at the very end, or none.
"""


@app.task(bind=True, name="tantra.tasks.social.post_tantra_progress", queue="social")
def post_tantra_progress(self) -> dict:
    """
    Generate a short, human-tone LinkedIn post about the Tantra AI build journey
    and publish it directly via Zernio (no approval queue — immediate publish).

    Runs every 5 minutes for testing. In production: set to once daily max.
    Rate limit: checks Redis to avoid posting more than once per hour.
    """
    logger.info("Starting post_tantra_progress task")

    from tantra.core.config import settings as _cfg

    if not _cfg.zernio_enabled:
        return {"success": False, "error": "Zernio not configured"}

    # Rate limit: don't post more than once per hour (even if schedule is tighter)
    try:
        r = _get_redis_client()
        if r.exists("tantra:progress_post:cooldown"):
            logger.info("Progress post cooldown active — skipping this run")
            return {"success": True, "skipped": True, "reason": "cooldown active (1h)"}
    except Exception:
        pass  # If Redis is down, proceed anyway

    # Generate the post using LiteLLM worker tier (phi4:14b)
    try:
        post_text = _llm_generate(
            prompt=(
                "Write a LinkedIn post about the Tantra AI project.\n\n"
                "Context:\n" + _TANTRA_BUILD_CONTEXT
            ),
            system=_HUMAN_POST_STYLE,
            max_tokens=250,
        )
        post_text = post_text.strip().strip('"')
        logger.info("Generated progress post (%d chars): %s...", len(post_text), post_text[:100])
    except Exception as exc:
        logger.error("LLM post generation failed: %s", exc)
        return {"success": False, "error": f"LLM generation failed: {exc}"}

    # Publish via Zernio
    from tantra.tools.zernio_client import ZernioClient
    try:
        zernio = ZernioClient()
        result = asyncio.run(
            zernio.post_text(
                content=post_text,
                platform="linkedin",
                account_id=_cfg.zernio_linkedin_account_id or None,
            )
        )
    except Exception as exc:
        return {"success": False, "error": f"Zernio publish failed: {exc}"}

    if result.get("success"):
        # Set 1-hour cooldown in Redis
        try:
            r = _get_redis_client()
            r.setex("tantra:progress_post:cooldown", 3600, "1")
        except Exception:
            pass

        urn = result.get("platform_post_id") or result.get("post_id", "")
        logger.info("Progress post published: %s", urn)
        return {
            "success": True,
            "post_urn": urn,
            "post_text_preview": post_text[:200],
        }
    else:
        return {"success": False, "error": result.get("error", "Unknown Zernio error")}
