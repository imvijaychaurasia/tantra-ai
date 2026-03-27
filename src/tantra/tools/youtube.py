"""
Tantra AI — YouTube Tool
Wraps Google YouTube Data API v3 for:
  - OAuth 2.0 authentication
  - Uploading videos
  - Fetching channel analytics
  - Managing playlists and descriptions
  - Posting comments

API reference: https://developers.google.com/youtube/v3
Scopes needed: https://www.googleapis.com/auth/youtube.upload
               https://www.googleapis.com/auth/youtube.readonly
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from tantra.core.config import settings

logger = logging.getLogger(__name__)

YOUTUBE_API_SERVICE = "youtube"
YOUTUBE_API_VERSION = "v3"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtubepartner",
]


@dataclass
class VideoMetadata:
    """Metadata for a YouTube video upload."""
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    category_id: str = "22"     # 22 = People & Blogs
    privacy_status: str = "public"   # public | private | unlisted
    made_for_kids: bool = False
    default_language: str = "en"
    thumbnail_path: Optional[str] = None


class YouTubeClient:
    """
    YouTube API client using google-api-python-client.

    Usage:
        # With API key (read-only)
        client = YouTubeClient.from_api_key()
        videos = await client.search_videos("agentic AI 2025")

        # With OAuth (read + write)
        client = YouTubeClient.from_credentials(credentials)
        video_id = await client.upload_video("video.mp4", metadata)
    """

    def __init__(self, service: Any) -> None:
        """Direct constructor — use class methods below instead."""
        self._service = service

    @classmethod
    def from_api_key(cls, api_key: Optional[str] = None) -> "YouTubeClient":
        """Build a read-only client using a plain API key."""
        from googleapiclient.discovery import build as google_build

        key = api_key or (
            settings.youtube_api_key.get_secret_value() if settings.youtube_api_key else None
        )
        if not key:
            raise ValueError("YOUTUBE_API_KEY is not configured")

        service = google_build(YOUTUBE_API_SERVICE, YOUTUBE_API_VERSION, developerKey=key)
        return cls(service)

    @classmethod
    def from_credentials(cls, credentials: Any) -> "YouTubeClient":
        """Build a full (read+write) client from OAuth2 credentials object."""
        from googleapiclient.discovery import build as google_build

        service = google_build(YOUTUBE_API_SERVICE, YOUTUBE_API_VERSION, credentials=credentials)
        return cls(service)

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    @staticmethod
    def build_auth_url(state: str = "tantra") -> str:
        """Build Google OAuth2 authorization URL."""
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.youtube_client_id,
                    "client_secret": settings.youtube_client_secret.get_secret_value()
                    if settings.youtube_client_secret
                    else "",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [settings.youtube_redirect_uri],
                }
            },
            scopes=YOUTUBE_SCOPES,
            redirect_uri=settings.youtube_redirect_uri,
        )
        auth_url, _ = flow.authorization_url(state=state, access_type="offline", prompt="consent")
        return auth_url

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_channel_info(self, channel_id: Optional[str] = None) -> dict[str, Any]:
        """Fetch channel stats (subscribers, views, video count)."""
        params: dict[str, Any] = {
            "part": "snippet,contentDetails,statistics",
        }
        if channel_id:
            params["id"] = channel_id
        else:
            params["mine"] = True

        response = self._service.channels().list(**params).execute()
        items = response.get("items", [])
        return items[0] if items else {}

    def search_videos(
        self,
        query: str,
        max_results: int = 10,
        order: str = "relevance",
    ) -> list[dict[str, Any]]:
        """Search YouTube for videos matching a query."""
        response = (
            self._service.search()
            .list(
                q=query,
                part="id,snippet",
                maxResults=max_results,
                type="video",
                order=order,
            )
            .execute()
        )
        return response.get("items", [])

    def get_video_stats(self, video_id: str) -> dict[str, Any]:
        """Fetch views, likes, comments for a video."""
        response = (
            self._service.videos()
            .list(part="statistics,snippet", id=video_id)
            .execute()
        )
        items = response.get("items", [])
        return items[0] if items else {}

    def list_my_videos(self, max_results: int = 50) -> list[dict[str, Any]]:
        """List authenticated user's uploaded videos."""
        # First get the uploads playlist ID
        channel = self.get_channel_info()
        uploads_id = (
            channel.get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads", "")
        )
        if not uploads_id:
            return []

        response = (
            self._service.playlistItems()
            .list(part="snippet,contentDetails", playlistId=uploads_id, maxResults=max_results)
            .execute()
        )
        return response.get("items", [])

    # ------------------------------------------------------------------
    # Write operations (require OAuth)
    # ------------------------------------------------------------------

    def upload_video(self, file_path: str, metadata: VideoMetadata) -> str:
        """
        Upload a video file to YouTube.
        Returns the video ID on success.

        Note: Large files use resumable upload automatically.
        """
        from googleapiclient.http import MediaFileUpload

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Video file not found: {file_path}")

        body = {
            "snippet": {
                "title": metadata.title,
                "description": metadata.description,
                "tags": metadata.tags,
                "categoryId": metadata.category_id,
                "defaultLanguage": metadata.default_language,
            },
            "status": {
                "privacyStatus": metadata.privacy_status,
                "madeForKids": metadata.made_for_kids,
            },
        }

        media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
        request = self._service.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            _, response = request.next_chunk()

        video_id = response.get("id", "")
        logger.info(f"YouTube video uploaded: https://youtu.be/{video_id}")

        # Optionally set thumbnail
        if metadata.thumbnail_path and os.path.exists(metadata.thumbnail_path):
            from googleapiclient.http import MediaFileUpload as MFU
            self._service.thumbnails().set(
                videoId=video_id,
                media_body=MFU(metadata.thumbnail_path),
            ).execute()

        return video_id

    def update_video(self, video_id: str, **updates: Any) -> dict[str, Any]:
        """Update title, description, tags of an existing video."""
        current = self.get_video_stats(video_id)
        snippet = current.get("snippet", {})
        snippet.update(updates)
        response = (
            self._service.videos()
            .update(part="snippet", body={"id": video_id, "snippet": snippet})
            .execute()
        )
        return response

    def add_comment(self, video_id: str, text: str) -> dict[str, Any]:
        """Post a top-level comment on a video."""
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        }
        return self._service.commentThreads().insert(part="snippet", body=body).execute()


# ---------------------------------------------------------------------------
# Stub tool functions (called by agents)
# ---------------------------------------------------------------------------

def youtube_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """
    Agent-callable tool: search YouTube for relevant videos.
    Returns {"results": [...]} or {"error": "..."}.
    """
    try:
        client = YouTubeClient.from_api_key()
        results = client.search_videos(query, max_results=max_results)
        simplified = [
            {
                "video_id": item["id"].get("videoId", ""),
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "published_at": item["snippet"]["publishedAt"],
            }
            for item in results
        ]
        return {"results": simplified}
    except Exception as e:
        logger.error(f"YouTube search failed: {e}")
        return {"error": str(e)}
