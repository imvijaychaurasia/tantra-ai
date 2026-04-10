# Core — Workflow Definitions

## YouTube Video Pipeline (Phase 3 — LIVE)

```
Director chat "approved"
  → extract AgentTask (youtube_script)
  → generate_youtube_script Celery task
      → YouTubeCrew (researcher → script_writer → seo_optimizer → quality_reviewer)
      → store YouTubeVideo (status: pending_approval)
  → n8n webhook → approve script
  → produce_youtube_video Celery task
      → tantra-media: TTS + slide images → MP4
      → store video file
  → upload_youtube_video Celery task (auto-chained)
      → YouTube Data API v3 resumable upload
      → upload thumbnail
      → status: live
```

## LinkedIn Post Pipeline (Phase 1 — LIVE)

```
Director chat "approved"
  → extract AgentTask (research_draft or progress_post)
  → research_draft_post or post_progress_update Celery task
      → SocialCrew (researcher → drafter) or direct write
      → store ContentQueueItem (status: pending_approval)
  → manual approval via monitor UI
  → publish_approved_linkedin_posts Celery task
      → LinkedIn API post
      → status: published
```

## ComfyUI Visual Pipeline (Phase 3e — Planned)

```
youtube_script with label:visual_video
  → YouTubeCrew produces script with rich visual_prompts
  → produce_youtube_video detects video_type: visual_video
  → dispatch to ComfyUI pipeline:
      → image_agent: Flux.1-dev FP8 → scene images
      → video_agent: LTX-Video → motion clips per scene
      → ffmpeg: composite images + clips + TTS audio → MP4
  → upload_youtube_video (same as slideshow path)
```
