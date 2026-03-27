"""
Tantra AI — OAuth provider clients
Google, GitHub (Phase 1)
LinkedIn, Microsoft/Azure AD (Phase 2)

fastapi-users handles the OAuth flow; we just configure the providers.
"""
from __future__ import annotations

from typing import Optional

from httpx_oauth.clients.github import GitHubOAuth2
from httpx_oauth.clients.google import GoogleOAuth2

from tantra.core.config import settings


def get_google_oauth_client() -> Optional[GoogleOAuth2]:
    """
    Google OAuth2 client.
    Scopes: openid, email, profile
    Requires GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET in .env
    """
    if not (settings.google_client_id and settings.google_client_secret):
        return None
    return GoogleOAuth2(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
    )


def get_github_oauth_client() -> Optional[GitHubOAuth2]:
    """
    GitHub OAuth2 client.
    Scopes: read:user, user:email
    Requires GITHUB_CLIENT_ID + GITHUB_CLIENT_SECRET in .env
    """
    if not (settings.github_client_id and settings.github_client_secret):
        return None
    return GitHubOAuth2(
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret.get_secret_value(),
    )


# Pre-built clients (None if not configured — routes won't be registered)
google_oauth_client = get_google_oauth_client()
github_oauth_client = get_github_oauth_client()
