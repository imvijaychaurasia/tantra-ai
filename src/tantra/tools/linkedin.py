"""
Tantra AI — LinkedIn Tool
Wraps LinkedIn Marketing API v2 for:
  - OAuth 2.0 token exchange
  - Publishing text/image posts
  - Fetching profile analytics
  - Scheduling posts (via Celery)

API reference: https://learn.microsoft.com/en-us/linkedin/marketing/
Scopes needed: r_liteprofile, r_emailaddress, w_member_social
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from tantra.core.config import settings

logger = logging.getLogger(__name__)

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


@dataclass
class LinkedInPost:
    """Structured LinkedIn post payload."""
    author_urn: str              # urn:li:person:{id}  or  urn:li:organization:{id}
    text: str
    visibility: str = "PUBLIC"   # PUBLIC | CONNECTIONS
    image_url: Optional[str] = None
    image_title: Optional[str] = None


class LinkedInClient:
    """
    HTTP client for LinkedIn API.

    Usage:
        client = LinkedInClient(access_token="...")
        post_id = await client.create_post(LinkedInPost(...))
    """

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": "202401",
        }

    # ------------------------------------------------------------------
    # Auth helpers (used in OAuth callback flow)
    # ------------------------------------------------------------------

    @staticmethod
    def build_auth_url(state: str = "tantra") -> str:
        """Build the LinkedIn OAuth 2.0 authorization URL."""
        params = {
            "response_type": "code",
            "client_id": settings.linkedin_client_id,
            "redirect_uri": settings.linkedin_redirect_uri,
            "scope": settings.linkedin_scopes,
            "state": state,
        }
        return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"

    @staticmethod
    async def exchange_code(code: str) -> dict[str, Any]:
        """Exchange authorization code for access + refresh tokens."""
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                LINKEDIN_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.linkedin_redirect_uri,
                    "client_id": settings.linkedin_client_id,
                    "client_secret": settings.linkedin_client_secret.get_secret_value()
                    if settings.linkedin_client_secret
                    else "",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    async def get_profile(self) -> dict[str, Any]:
        """Fetch authenticated user's basic profile."""
        async with httpx.AsyncClient(headers=self._headers) as http:
            resp = await http.get(f"{LINKEDIN_API_BASE}/userinfo")
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    async def create_text_post(
        self,
        author_urn: str,
        text: str,
        visibility: str = "PUBLIC",
    ) -> str:
        """
        Publish a text-only post.
        Returns the post URN (e.g. urn:li:share:...).
        """
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": visibility},
        }

        async with httpx.AsyncClient(headers=self._headers) as http:
            resp = await http.post(f"{LINKEDIN_API_BASE}/ugcPosts", json=payload)
            resp.raise_for_status()
            post_urn = resp.headers.get("x-restli-id", "")
            logger.info(f"LinkedIn post created: {post_urn}")
            return post_urn

    async def create_image_post(
        self,
        author_urn: str,
        text: str,
        image_bytes: bytes,
        image_filename: str = "image.png",
        visibility: str = "PUBLIC",
    ) -> str:
        """
        Upload image and publish a post with it.
        Returns the post URN.
        """
        # Step 1: Register image upload
        register_payload = {
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner": author_urn,
                "serviceRelationships": [{
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }],
            }
        }
        async with httpx.AsyncClient(headers=self._headers) as http:
            reg_resp = await http.post(
                f"{LINKEDIN_API_BASE}/assets?action=registerUpload",
                json=register_payload,
            )
            reg_resp.raise_for_status()
            reg_data = reg_resp.json()

        upload_url = reg_data["value"]["uploadMechanism"][
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
        ]["uploadUrl"]
        asset_urn = reg_data["value"]["asset"]

        # Step 2: Upload binary
        async with httpx.AsyncClient() as http:
            up_resp = await http.put(
                upload_url,
                content=image_bytes,
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            up_resp.raise_for_status()

        # Step 3: Create post with image
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "IMAGE",
                    "media": [{
                        "status": "READY",
                        "description": {"text": text[:200]},
                        "media": asset_urn,
                        "title": {"text": image_filename},
                    }],
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": visibility},
        }
        async with httpx.AsyncClient(headers=self._headers) as http:
            resp = await http.post(f"{LINKEDIN_API_BASE}/ugcPosts", json=payload)
            resp.raise_for_status()
            return resp.headers.get("x-restli-id", "")

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    async def get_post_stats(self, post_urn: str) -> dict[str, Any]:
        """Fetch impressions, clicks, reactions for a post."""
        # Encode URN for query param
        encoded_urn = post_urn.replace(":", "%3A")
        url = (
            f"{LINKEDIN_API_BASE}/organizationalEntityShareStatistics"
            f"?q=organizationalEntity&organizationalEntity={encoded_urn}"
        )
        async with httpx.AsyncClient(headers=self._headers) as http:
            resp = await http.get(url)
            if resp.status_code == 200:
                return resp.json()
            return {}


# ---------------------------------------------------------------------------
# Stub tool functions (called by agents via MCP / CrewAI tools)
# ---------------------------------------------------------------------------

async def linkedin_post_text(
    access_token: str,
    author_urn: str,
    text: str,
    visibility: str = "PUBLIC",
) -> dict[str, Any]:
    """
    MCP-compatible tool function: publish a LinkedIn text post.
    Returns {"success": True, "post_urn": "..."} or {"success": False, "error": "..."}.
    """
    if not access_token:
        return {"success": False, "error": "LinkedIn access_token is required"}
    try:
        client = LinkedInClient(access_token)
        urn = await client.create_text_post(author_urn, text, visibility)
        return {"success": True, "post_urn": urn}
    except httpx.HTTPStatusError as e:
        logger.error(f"LinkedIn post failed: {e.response.text}")
        return {"success": False, "error": e.response.text}
    except Exception as e:
        return {"success": False, "error": str(e)}
