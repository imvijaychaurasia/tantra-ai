#!/usr/bin/env python3
"""
Tantra AI — YouTube OAuth2 Setup
तंत्र  ·  One-time script to obtain a YouTube Data API v3 refresh token

Run this ONCE on the HOST machine (not inside Docker — it needs a browser).
It prints the refresh token to add to your .env file.

Prerequisites:
  1. Google Cloud Console → create an OAuth 2.0 Desktop App credential
     https://console.cloud.google.com/apis/credentials
  2. Enable YouTube Data API v3
     https://console.cloud.google.com/apis/library/youtube.googleapis.com
  3. Under "OAuth consent screen" → add your Google account as a Test User
     (for unverified apps in personal projects this is sufficient)
  4. Download the client secrets JSON, or just note client_id + client_secret

Usage:
  pip install google-api-python-client google-auth google-auth-oauthlib
  python scripts/youtube_oauth_setup.py

  Or if your credentials are already in .env:
  YOUTUBE_CLIENT_ID=... YOUTUBE_CLIENT_SECRET=... python scripts/youtube_oauth_setup.py

The script will:
  - Open a browser (or print the URL if running headless) for Google sign-in
  - Exchange the auth code for access + refresh tokens
  - Print the refresh token and the exact .env line to add
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal dependency check
# ---------------------------------------------------------------------------
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    print(
        "\nERROR: Required packages not installed.\n"
        "Run:  pip install google-api-python-client google-auth google-auth-oauthlib\n"
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Scopes — matches what upload_youtube_video requests
# ---------------------------------------------------------------------------
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

# ---------------------------------------------------------------------------
# Resolve credentials — env vars take priority over prompts
# ---------------------------------------------------------------------------

def _get_env_or_dotenv(key: str) -> str | None:
    """Read from os.environ first, then from .env file in the repo root."""
    val = os.environ.get(key)
    if val:
        return val
    # Try to read from .env in current dir or repo root
    for dotenv_path in [Path(".env"), Path(__file__).parent.parent / ".env"]:
        if dotenv_path.exists():
            for line in dotenv_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    return None


def main() -> None:
    print("\n" + "=" * 70)
    print("  Tantra AI — YouTube OAuth2 Setup (Phase 3c)")
    print("=" * 70)

    # ── Get credentials ───────────────────────────────────────────────────────
    client_id = _get_env_or_dotenv("YOUTUBE_CLIENT_ID")
    client_secret = _get_env_or_dotenv("YOUTUBE_CLIENT_SECRET")

    if not client_id:
        print("\nYOUTUBE_CLIENT_ID not found in env or .env.")
        client_id = input("Enter your OAuth2 client_id: ").strip()

    if not client_secret:
        print("YOUTUBE_CLIENT_SECRET not found in env or .env.")
        client_secret = input("Enter your OAuth2 client_secret: ").strip()

    if not client_id or not client_secret:
        print("ERROR: client_id and client_secret are required.")
        sys.exit(1)

    # ── Build client config dict (equivalent to downloaded client_secrets.json) ──
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    print(f"\nScopes requested:")
    for scope in SCOPES:
        print(f"  • {scope}")

    # ── Run the OAuth flow ────────────────────────────────────────────────────
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)

    # Detect if we have a display available (local machine vs SSH headless)
    import os as _os
    has_display = bool(_os.environ.get("DISPLAY") or _os.environ.get("WAYLAND_DISPLAY") or sys.platform == "darwin")

    creds = None
    if has_display:
        print("\nStarting OAuth flow — a browser window will open...")
        print()
        try:
            creds = flow.run_local_server(
                port=0,
                prompt="consent",
                access_type="offline",
            )
        except Exception as _e:
            print(f"Browser flow failed ({_e}), switching to headless mode...")

    if creds is None:
        # ── Headless / SSH fallback ───────────────────────────────────────────
        # Generate the auth URL, user opens it on any machine (Mac/phone/etc),
        # approves, then pastes the full redirect URL (which contains the code).
        auth_url, _ = flow.authorization_url(
            prompt="consent",
            access_type="offline",
        )
        print("\n" + "─" * 70)
        print("  HEADLESS MODE — no browser detected (running via SSH)")
        print("─" * 70)
        print("\nStep 1: Open this URL on your Mac or any machine with a browser:\n")
        print(f"  {auth_url}\n")
        print("Step 2: Sign in with the Google account that OWNS your YouTube channel.")
        print("        Click 'Allow' on the permissions screen.")
        print()
        print("Step 3: You'll be redirected to http://localhost:... which will fail.")
        print("        That's expected — copy the FULL URL from your browser's address bar.")
        print()
        redirect_response = input("Step 4: Paste the full redirect URL here and press Enter:\n> ").strip()
        print()
        try:
            flow.fetch_token(authorization_response=redirect_response)
            creds = flow.credentials
        except Exception as exc:
            print(f"\nERROR exchanging token: {exc}")
            print("Make sure you pasted the full URL including http://localhost:...?code=...")
            sys.exit(1)

    # ── Extract and display the refresh token ─────────────────────────────────
    refresh_token = creds.refresh_token

    if not refresh_token:
        print(
            "\nERROR: No refresh_token in response.\n"
            "This usually means the app already has offline access for this account.\n"
            "Go to https://myaccount.google.com/permissions, revoke Tantra AI access,\n"
            "then run this script again."
        )
        sys.exit(1)

    # Verify the token works by fetching channel info
    print("\nVerifying token by fetching your YouTube channel info...")
    try:
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        channels = youtube.channels().list(part="snippet", mine=True).execute()
        items = channels.get("items", [])
        if items:
            channel = items[0]["snippet"]
            print(f"\n  Channel: {channel.get('title', '(unknown)')}")
            print(f"  Channel ID: {items[0].get('id', '(unknown)')}")
        else:
            print("  WARNING: No channels found for this account.")
            print("  Make sure your Google account has a YouTube channel.")
    except Exception as exc:
        print(f"  WARNING: Could not verify token: {exc}")
        print("  Token may still be valid — add it to .env and test by running upload.")

    # ── Print instructions ─────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  ✓  Refresh token obtained!")
    print("=" * 70)
    print("\nAdd this line to your .env file:\n")
    print(f"  YOUTUBE_REFRESH_TOKEN={refresh_token}")
    print()
    print("Then rebuild the celery-worker to pick up the new env var:")
    print("  docker compose up -d celery-worker")
    print()
    print("To upload your first video (replace <VIDEO_UUID> with the DB ID):")
    print("  docker compose exec tantra-api python -c \\")
    print("    \"from tantra.tasks.youtube_tasks import upload_youtube_video; \\")
    print("     print(upload_youtube_video('<VIDEO_UUID>'))\"")
    print()
    print("Or approve the video via the API and let the state machine handle it:")
    print("  curl -X POST http://localhost:8000/api/v1/youtube/<VIDEO_UUID>/upload")
    print("=" * 70)


if __name__ == "__main__":
    main()
