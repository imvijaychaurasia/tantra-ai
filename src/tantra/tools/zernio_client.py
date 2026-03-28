"""
Tantra AI — Zernio Unified Social Media Client

Zernio handles OAuth for all platforms (LinkedIn, X/Twitter, Instagram,
TikTok, YouTube, etc.) — no per-platform developer apps needed.

Setup (one-time, 5 minutes):
  1. Sign up at https://zernio.com
  2. Connect LinkedIn (and other platforms) via OAuth in the Zernio dashboard
     → no LinkedIn Developer Portal needed at all
  3. Copy your API key from https://zernio.com/dashboard/api-keys
  4. Add to .env:  ZERNIO_API_KEY=sk_...
  5. List connected accounts to get account IDs:
       curl -H "Authorization: Bearer $ZERNIO_API_KEY" https://zernio.com/api/v1/accounts
  6. Add to .env:  ZERNIO_LINKEDIN_ACCOUNT_ID=acc_...

Supported post types (all platforms where applicable):
  text      — plain text post
  image     — post with image URL(s)
  video     — post with video URL
  carousel  — document/PDF carousel (LinkedIn-specific)
  poll      — poll with options (platform support varies)

Usage:
    client = ZernioClient()
    result = await client.post_text("Hello LinkedIn!", platform="linkedin")
    # → {"success": True, "post_id": "post_abc123", "url": "https://..."}
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from tantra.core.config import settings

logger = logging.getLogger(__name__)


def _obj_to_dict(obj: Any) -> dict[str, Any]:
    """
    Normalise a Zernio SDK response object (or plain dict) to a plain dict.

    The SDK may return Pydantic v1 models (.dict()), Pydantic v2 models
    (.model_dump()), dataclasses (vars()), or plain dicts — handle all cases.
    """
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):          # Pydantic v2
        return obj.model_dump()
    if hasattr(obj, "dict"):                # Pydantic v1
        return obj.dict()
    if hasattr(obj, "__dict__"):            # dataclass / generic object
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {}

# Zernio platform → Tantra platform name mapping
PLATFORM_ACCOUNT_MAP: dict[str, str] = {
    "linkedin":  "zernio_linkedin_account_id",
    "twitter":   "zernio_twitter_account_id",
    "instagram": "zernio_instagram_account_id",
    "youtube":   "zernio_youtube_account_id",
}


def _get_account_id(platform: str, account_id_override: Optional[str] = None) -> Optional[str]:
    """Resolve the Zernio account ID for a platform from config or override."""
    if account_id_override:
        return account_id_override
    attr = PLATFORM_ACCOUNT_MAP.get(platform.lower())
    if attr:
        return getattr(settings, attr, None)
    return None


class ZernioClient:
    """
    Async-ready wrapper around the Zernio SDK.

    All public methods return a consistent result dict:
        {"success": True,  "post_id": "post_abc", "url": "...", "platform_post_id": "..."}
        {"success": False, "error": "..."}

    Supports both sync (via asyncio.run) and async usage.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        key = api_key or settings.zernio_key
        if not key:
            raise ValueError(
                "Zernio API key not configured. "
                "Set ZERNIO_API_KEY in .env or pass api_key= explicitly. "
                "Get your key at https://zernio.com/dashboard/api-keys"
            )
        from zernio import Zernio  # lazy import — only if Zernio is configured
        self._client = Zernio(api_key=key, base_url=settings.zernio_base_url)
        self._key = key

    # ------------------------------------------------------------------
    # Account discovery
    # ------------------------------------------------------------------

    async def get_accounts(self) -> list[dict[str, Any]]:
        """
        List all social accounts connected in Zernio.
        Returns list of {id, platform, display_name, username, profile_url}.
        Call this once after connecting accounts in the dashboard to get your account IDs.
        """
        result = await self._client.accounts.alist()
        # SDK may return a wrapper object or a plain list
        raw = getattr(result, "accounts", result) or []
        accounts = raw if isinstance(raw, list) else [raw]
        out = []
        for acc in accounts:
            a = _obj_to_dict(acc)
            # Try every plausible ID field name the SDK might use
            acc_id = (
                a.get("_id")
                or a.get("id")
                or a.get("accountId")
                or a.get("account_id")
                or a.get("socialAccountId")
                or a.get("social_account_id")
                or ""
            )
            out.append({
                "id":           acc_id,
                "platform":     a.get("platform", ""),
                "display_name": a.get("displayName", a.get("display_name", a.get("name", ""))),
                "username":     a.get("username", ""),
                "profile_url":  a.get("profileUrl", a.get("profile_url", "")),
                "_raw_keys":    list(a.keys()),   # DEBUG: remove once ID field confirmed
            })
        return out

    async def get_profiles(self) -> list[dict[str, Any]]:
        """
        List all Zernio profiles (brand containers).
        A profile groups multiple platform accounts (e.g. 'Personal Brand' = LinkedIn + Twitter).
        """
        result = await self._client.profiles.alist()
        raw = getattr(result, "profiles", result) or []
        profiles = raw if isinstance(raw, list) else [raw]
        out = []
        for p in profiles:
            d = _obj_to_dict(p)
            out.append({
                "id":          d.get("_id", d.get("id", "")),
                "name":        d.get("name", ""),
                "description": d.get("description", ""),
                "accounts":    d.get("accounts", []),
            })
        return out

    # ------------------------------------------------------------------
    # Publishing — all post types
    # ------------------------------------------------------------------

    async def post_text(
        self,
        content: str,
        platform: str = "linkedin",
        account_id: Optional[str] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """
        Publish a plain text post.

        Args:
            content:       Post text (up to ~3000 chars for LinkedIn).
            platform:      Target platform (linkedin | twitter | instagram | ...).
            account_id:    Zernio account ID (acc_xxx). Falls back to config if None.
            scheduled_for: If set, schedule the post instead of publishing immediately.

        Returns:
            {"success": True, "post_id": "post_abc", "platform": "linkedin"}
        """
        return await self._publish(
            content=content,
            platform=platform,
            account_id=account_id,
            scheduled_for=scheduled_for,
        )

    async def post_image(
        self,
        content: str,
        image_urls: list[str],
        platform: str = "linkedin",
        account_id: Optional[str] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """
        Publish a post with one or more images.
        Images must be publicly accessible URLs.

        LinkedIn: up to 20 images per post.
        """
        media_items = [{"type": "image", "url": url} for url in image_urls]
        return await self._publish(
            content=content,
            platform=platform,
            account_id=account_id,
            media_items=media_items,
            scheduled_for=scheduled_for,
        )

    async def post_video(
        self,
        content: str,
        video_url: str,
        platform: str = "linkedin",
        account_id: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """
        Publish a post with a video.
        Video must be a publicly accessible URL (MP4 recommended).

        LinkedIn limits: max 5GB, max 10 minutes, formats: MP4/MOV/MKV/AVI.
        """
        media_item: dict[str, Any] = {"type": "video", "url": video_url}
        if thumbnail_url:
            media_item["thumbnailUrl"] = thumbnail_url
        return await self._publish(
            content=content,
            platform=platform,
            account_id=account_id,
            media_items=[media_item],
            scheduled_for=scheduled_for,
        )

    async def post_carousel(
        self,
        content: str,
        document_url: str,
        platform: str = "linkedin",
        account_id: Optional[str] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """
        Publish a document carousel post (LinkedIn-specific).
        Document must be a publicly accessible PDF/PPTX/PPT/DOC/DOCX URL.

        LinkedIn carousels: 3-300 pages, max 100MB PDF.
        These consistently get 3-5× higher engagement than text posts.
        """
        media_items = [{"type": "document", "url": document_url}]
        return await self._publish(
            content=content,
            platform=platform,
            account_id=account_id,
            media_items=media_items,
            scheduled_for=scheduled_for,
        )

    async def post_to_multiple(
        self,
        content: str,
        platforms: list[str],
        account_ids: Optional[dict[str, str]] = None,
        media_items: Optional[list[dict]] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """
        Publish the same post to multiple platforms in a single API call.
        Zernio adapts the format per-platform automatically.

        Args:
            platforms:   ["linkedin", "twitter", "instagram"]
            account_ids: {platform: account_id} overrides. Uses config defaults if omitted.

        Returns:
            {"success": True, "post_id": "...", "platforms": [{platform, status}, ...]}
        """
        platform_list = []
        for p in platforms:
            aid = (account_ids or {}).get(p) or _get_account_id(p)
            if aid:
                platform_list.append({"platform": p, "accountId": aid})
            else:
                logger.warning("No account ID configured for platform=%s, skipping", p)

        if not platform_list:
            return {"success": False, "error": "No valid account IDs found for any platform"}

        return await self._publish_multi(
            content=content,
            platform_list=platform_list,
            media_items=media_items,
            scheduled_for=scheduled_for,
        )

    # ------------------------------------------------------------------
    # Post management
    # ------------------------------------------------------------------

    async def delete_post(self, post_id: str) -> dict[str, Any]:
        """Delete a scheduled or published post."""
        try:
            await self._client.posts.adelete(post_id)
            return {"success": True, "post_id": post_id}
        except Exception as exc:
            logger.error("Zernio delete failed for post %s: %s", post_id, exc)
            return {"success": False, "error": str(exc)}

    async def get_post(self, post_id: str) -> dict[str, Any]:
        """Retrieve a post's current status and details."""
        try:
            result = await self._client.posts.aget(post_id)
            raw = getattr(result, "post", result)
            return {"success": True, "post": _obj_to_dict(raw) if raw is not None else {}}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def get_analytics(self, post_id: str) -> dict[str, Any]:
        """
        Fetch post analytics: impressions, likes, comments, shares, clicks.
        Returns platform-specific metrics where available.
        """
        try:
            result = await self._client.analytics.aget(post_id)
            analytics = getattr(result, "analytics", result)
            return {"success": True, "analytics": analytics}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def list_posts(
        self,
        status: str = "published",
        platform: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        List posts with optional status and platform filters.
        status: published | scheduled | failed | all
        """
        try:
            kwargs: dict[str, Any] = {"status": status, "limit": limit}
            if platform:
                kwargs["platform"] = platform
            result = await self._client.posts.alist(**kwargs)
            posts = getattr(result, "posts", result) or []
            return posts if isinstance(posts, list) else [posts]
        except Exception as exc:
            logger.error("Zernio list_posts failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _publish(
        self,
        content: str,
        platform: str,
        account_id: Optional[str],
        media_items: Optional[list[dict]] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Single-platform publish helper."""
        aid = _get_account_id(platform, account_id)
        if not aid:
            return {
                "success": False,
                "error": (
                    f"No account ID for platform='{platform}'. "
                    f"Set ZERNIO_{platform.upper()}_ACCOUNT_ID in .env "
                    f"or pass account_id= explicitly."
                ),
            }

        return await self._publish_multi(
            content=content,
            platform_list=[{"platform": platform, "accountId": aid}],
            media_items=media_items,
            scheduled_for=scheduled_for,
        )

    async def _publish_multi(
        self,
        content: str,
        platform_list: list[dict],
        media_items: Optional[list[dict]] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Core publish call — shared by all post_* methods."""
        kwargs: dict[str, Any] = {
            "content": content,
            "platforms": platform_list,
        }

        if scheduled_for:
            kwargs["scheduled_for"] = scheduled_for.isoformat()
            kwargs["timezone"] = settings.zernio_default_timezone
        else:
            kwargs["publish_now"] = True

        if media_items:
            kwargs["media_items"] = media_items

        try:
            result = await self._client.posts.acreate(**kwargs)
            raw = getattr(result, "post", result)
            post = _obj_to_dict(raw) if raw is not None else {}
            post_id = post.get("_id", post.get("id", ""))
            platform_results = post.get("platforms", [])

            logger.info("Zernio post created: %s → platforms=%s", post_id, platform_list)
            return {
                "success": True,
                "post_id": post_id,
                "platforms": platform_results,
                "scheduled": scheduled_for is not None,
            }

        except Exception as exc:
            logger.error("Zernio publish failed: %s | platforms=%s", exc, platform_list)
            return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Module-level convenience functions (for CrewAI @tool wrappers)
# ---------------------------------------------------------------------------

def _get_client() -> Optional[ZernioClient]:
    """Get a ZernioClient if configured, else None."""
    if not settings.zernio_enabled:
        return None
    try:
        return ZernioClient()
    except Exception as exc:
        logger.error("Could not initialise ZernioClient: %s", exc)
        return None


async def zernio_post_text(
    content: str,
    platform: str = "linkedin",
    account_id: Optional[str] = None,
    scheduled_for: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    Convenience function: post text via Zernio.
    Returns {"success": True/False, ...}.
    """
    client = _get_client()
    if not client:
        return {"success": False, "error": "Zernio not configured — set ZERNIO_API_KEY in .env"}
    return await client.post_text(content, platform=platform, account_id=account_id,
                                  scheduled_for=scheduled_for)


async def zernio_post_multi(
    content: str,
    platforms: list[str],
    account_ids: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Post the same content to multiple platforms simultaneously."""
    client = _get_client()
    if not client:
        return {"success": False, "error": "Zernio not configured — set ZERNIO_API_KEY in .env"}
    return await client.post_to_multiple(content, platforms=platforms, account_ids=account_ids)
