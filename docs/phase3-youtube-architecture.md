# Phase 3 — YouTube Autonomous Publishing Engine
## Architecture Design

> **Status:** Design complete — implementation not started
> **Platform:** YouTube (first platform of Phase 3 multi-platform earning engine)
> **Prerequisite:** Phase 1 (LinkedIn pipeline) ✅ + Phase 2 (Director layer) ✅

---

## 1. Overview

Phase 3 adds a fully autonomous YouTube content pipeline to Tantra AI. The Director plans YouTube videos as `youtube_script` tasks, a dedicated YouTubeCrew researches and writes scene-by-scene scripts, a new `tantra-media` microservice handles local AI-powered production (TTS + video generation + thumbnails), and the existing `YouTubeClient` uploads the final MP4 to YouTube via the Data API v3.

Unlike LinkedIn posts (text-only, approval → publish in one step), YouTube has a **two-gate approval model**:

1. **Script gate** — human reviews scene script + thumbnail concept before production starts
2. **Upload gate** — (optional) human reviews produced video before it goes live

This prevents wasted GPU time on bad scripts and keeps brand quality high while the production side scales autonomously.

---

## 2. What already exists (Phase 1/2 assets reused)

| Asset | Location | Reuse in Phase 3 |
|-------|----------|-----------------|
| `YouTubeClient` | `tools/youtube.py` | Video upload, analytics, OAuth — fully implemented |
| `ZernioClient` | `tools/zernio_client.py` | YouTube community posts (not video upload) |
| `SocialConnection` | `db/social.py` | Stores encrypted YouTube OAuth tokens |
| `AgentTask` | `db/director.py` | `youtube_script` task_type already documented |
| `dispatch_due_tasks` | `tasks/director_tasks.py` | Picks up youtube tasks automatically |
| `recover_stuck_tasks` | `tasks/director_tasks.py` | Resets stuck youtube production tasks |
| Redis DB3 checkpoint system | `tasks/director_tasks.py` | Checkpoint expensive production jobs |
| n8n approval workflow pattern | `n8n/` | Cloned for YouTube script approval |
| OAuth token infrastructure | `auth/` | Google OAuth already wired |
| Celery beat scheduler | `tasks/celery_app.py` | New youtube tasks added here |

---

## 3. New components

### 3.1 `tantra-media` — Local AI Media Service

A new standalone FastAPI microservice (Docker service, port 8001) that wraps all local generative AI models for media production. Runs independently of the main Tantra API so it can be disabled on lower-RAM machines without affecting the rest of the stack.

**HTTP API:**

```
GET  /health                  → model availability, VRAM status
POST /generate/tts            → Kokoro 82M / CosyVoice2 → MP3 narration
POST /generate/image          → FLUX.1-Schnell → PNG thumbnail / scene image
POST /generate/video          → Wan2.1-T2V-14B → MP4 scene clip (per scene)
POST /generate/assemble       → ffmpeg → final MP4 from clips + narration
POST /transcribe              → Whisper Large V3 Turbo → transcript JSON
GET  /jobs/{job_id}           → poll async job status (pending/running/done/failed)
```

**Request/response example — TTS:**
```json
POST /generate/tts
{
  "text": "30 days ago I started building Tantra AI from scratch...",
  "voice": "kokoro_af_sarah",
  "speed": 1.0,
  "output_format": "mp3"
}
→ {"job_id": "job_tts_abc123", "status": "pending"}

GET /jobs/job_tts_abc123
→ {"status": "done", "file_path": "/data/media/audio/abc123_scene1.mp3", "duration_seconds": 12.4}
```

**Request/response example — video assembly:**
```json
POST /generate/assemble
{
  "clips": [
    {"path": "/data/media/clips/abc123_scene1.mp4", "audio": "/data/media/audio/abc123_scene1.mp3"},
    {"path": "/data/media/clips/abc123_scene2.mp4", "audio": "/data/media/audio/abc123_scene2.mp3"}
  ],
  "thumbnail_path": "/data/media/images/abc123_thumb.png",
  "output_name": "abc123_final.mp4",
  "add_subtitles": true
}
→ {"job_id": "job_asm_xyz789", "status": "pending"}
```

**Model loading strategy — lazy load with idle eviction:**
- Models are NOT loaded at startup (too much RAM/VRAM)
- Load on first request for each model type
- Keep in memory for 10 minutes of idle, then unload
- Config flag: `TANTRA_MEDIA_ENABLED=true` — if `false`, the production step is skipped entirely (human handles production manually)

**Hardware requirements per model:**

| Model | VRAM / RAM | Quality | Speed |
|-------|-----------|---------|-------|
| Kokoro 82M | ~600 MB RAM | Good | Fast (real-time on CPU) |
| CosyVoice2-0.5B | ~2 GB RAM | Better | Medium |
| FLUX.1-Schnell (Q4) | ~4 GB VRAM | High | ~30s/image |
| Wan2.1-T2V-14B | ~16 GB VRAM | High | ~2-5 min/scene |
| Whisper Large V3 Turbo | ~3 GB RAM | Excellent | Fast |

> **Apple Silicon note:** Kokoro + FLUX.1-Schnell run well on M2 Pro/Max (unified memory). Wan2.1-T2V-14B requires M2 Ultra (192 GB) or M3 Max (128 GB) at minimum. On M2 Pro, Wan2.1 can be disabled and replaced with slide-based video assembly (Remotion generates video from images + narration — no VRAM required). The architecture handles this gracefully: if `wan21_enabled=false`, the production step uses the Remotion fallback.

**Docker service addition (`docker-compose.yml`):**
```yaml
tantra-media:
  build:
    context: .
    target: media
  ports:
    - "8001:8001"
  volumes:
    - ./data/media:/data/media
  environment:
    - TANTRA_MEDIA_ENABLED=true
    - KOKORO_ENABLED=true
    - FLUX_ENABLED=true
    - WAN21_ENABLED=false  # enable only on high-VRAM machines
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
```

---

### 3.2 `YouTubeVideo` — New DB Model

Lives in `db/social.py` (alongside `ContentQueueItem`). Separate model because YouTube videos have fundamentally different metadata from social posts: scene structure, file paths, multi-stage production lifecycle.

```python
class YouTubeVideo(Base):
    __tablename__ = "youtube_videos"

    id: UUID                          # primary key
    agent_task_id: UUID               # FK → agent_tasks.id (the youtube_script task)
    plan_id: UUID                     # FK → weekly_plans.id (nullable)

    # Script (set by YouTubeCrew)
    title: str
    script: JSON                      # YouTubeScript dict (see schema below)
    thumbnail_concept: str            # Human-readable thumbnail idea
    tags: JSON                        # list[str]
    description: str                  # SEO description

    # Status state machine
    status: str                       # scripted|approved|producing|produced|uploading|live|failed|rejected

    # Production file paths (populated by produce_youtube_video)
    audio_path: str                   # data/media/audio/{id}_narration.mp3
    video_path: str                   # data/media/output/{id}_final.mp4
    thumbnail_path: str               # data/media/images/{id}_thumb.png

    # Upload result (populated by upload_youtube_video)
    youtube_video_id: str             # YouTube's video ID (e.g. "dQw4w9WgXcQ")
    youtube_url: str                  # https://youtu.be/{video_id}

    # n8n tracking
    n8n_execution_id: str             # script approval workflow execution ID

    # Analytics (updated by youtube_analytics_pull)
    views: int
    watch_time_minutes: float
    ctr_percent: float
    avg_view_duration_seconds: float
    likes: int
    comments: int
    analytics_updated_at: datetime

    # Timestamps
    created_at: datetime
    approved_at: datetime
    produced_at: datetime
    uploaded_at: datetime
```

**YouTubeScript JSON schema** (stored in `script` column):

```json
{
  "duration_target_seconds": 480,
  "hook": "What if your AI system could plan and publish content while you sleep?",
  "scenes": [
    {
      "id": 1,
      "type": "hook",
      "duration_seconds": 20,
      "narration": "30 days ago I started building Tantra AI from scratch...",
      "visual_prompt": "Developer at terminal, dark room, green text on screen",
      "b_roll_description": "Terminal showing 'tantra director chat' streaming response",
      "on_screen_text": "Day 1 — Just a Celery task"
    },
    {
      "id": 2,
      "type": "content",
      "duration_seconds": 60,
      "narration": "The core idea: a Director agent that plans the week, dispatches tasks...",
      "visual_prompt": "Architecture diagram animating layer by layer, dark background",
      "b_roll_description": "tantra director status output scrolling in terminal",
      "on_screen_text": null
    }
  ],
  "call_to_action": "Subscribe — I ship weekly updates on what breaks and what works",
  "thumbnail_concept": "Split screen: empty terminal (Day 1) vs Director chat running (Day 30)",
  "thumbnail_prompt": "Futuristic terminal UI, two panes, left shows empty cursor, right shows autonomous AI typing, dark neon aesthetic, text: 'Day 1 vs Day 30'"
}
```

---

### 3.3 `YouTubeCrew` — New CrewAI Crew

Lives in `crews/youtube_crew.py`. Parallel to `SocialCrew` but optimised for long-form video content.

**Agents:**

| Agent | Model tier | Responsibility |
|-------|-----------|----------------|
| `topic_researcher` | `worker` | Tavily: research topic + YouTube trending + competitor gap analysis |
| `script_writer` | `director` | Scene-by-scene script with narration text + visual prompts |
| `seo_optimizer` | `worker` | Title (≤100 chars), description (keyword-rich, 250+ words), tags (15-20) |
| `quality_reviewer` | `fast` | Validates hook strength, retention markers, CTA, brand voice |

**Process:** Sequential (researcher → writer → seo → reviewer → final output)

**Input context injected by `generate_youtube_script`:**
```python
{
  "topic": task.instructions,            # Director's topic guidance
  "platform_context": "YouTube",
  "channel_focus": "Building Tantra AI — local autonomous agents — builder audience",
  "duration_target": "6-10 minutes",
  "brand_voice": "authentic, technical, building-in-public, no hype",
  "active_plan": get_live_context(),     # current week goals from DB
  "recent_videos": list_my_videos()[:5] # recent uploads to avoid repetition
}
```

**Output:** `YouTubeScript` dataclass (maps to `script` JSON column above)

---

### 3.4 New Celery Tasks — `tasks/youtube_tasks.py`

```python
# Task registry (all in "agents" queue):
tantra.tasks.youtube.generate_youtube_script   # CrewAI → script → n8n webhook
tantra.tasks.youtube.produce_youtube_video     # tantra-media API → MP4 production
tantra.tasks.youtube.upload_youtube_video      # YouTube Data API → live
tantra.tasks.youtube.update_youtube_metadata   # SEO update on existing video
```

**`generate_youtube_script(agent_task_id)`:**
1. Load `AgentTask` by ID, mark `in_progress`
2. Run `YouTubeCrew` with topic context + live DB context
3. Parse `YouTubeScript` output (validate scenes, duration, prompts)
4. Create `YouTubeVideo` row (status: `scripted`)
5. Fire n8n webhook: `POST n8n_youtube_script_webhook`  with script + title + thumbnail concept
6. Update `AgentTask.result = {"youtube_video_id": str(video.id)}`
7. Mark `AgentTask` `completed`

**Checkpoint:** Store raw `YouTubeCrew` output in Redis DB3 (`tantra:checkpoint:youtube_script:{agent_task_id}`) with 4h TTL. On restart, skip crew and resume at step 3.

**`produce_youtube_video(youtube_video_id)`:**
1. Load `YouTubeVideo`, assert status is `approved`
2. Mark status `producing`
3. For each scene in `script.scenes`:
   - Call `tantra-media POST /generate/tts` → poll until done → store audio path
   - Call `tantra-media POST /generate/video` (if Wan2.1 enabled) or `generate/image` → store clip/image path
4. Call `tantra-media POST /generate/image` for thumbnail
5. Call `tantra-media POST /generate/assemble` → final MP4
6. Update `YouTubeVideo`: `video_path`, `audio_path`, `thumbnail_path`, status → `produced`
7. Create new `AgentTask(task_type="youtube_publish", ...)` or trigger directly

**Checkpoint:** After each scene's media is generated, save progress to Redis. On restart, skip completed scenes.

**`upload_youtube_video(youtube_video_id)`:**
1. Load `YouTubeVideo`, assert status is `produced`
2. Retrieve YouTube OAuth token from `SocialConnection` via `AuthManager`
3. Build `YouTubeClient` from OAuth credentials
4. Call `YouTubeClient.upload_video(video.video_path, VideoMetadata(...))`
5. Set thumbnail via `thumbnails().set()`
6. Update `YouTubeVideo`: `youtube_video_id`, `youtube_url`, status → `live`
7. Mark parent `AgentTask` `completed`

---

### 3.5 New Director Task Types

Extend `_VALID_TASK_TYPES` in `cli.py`:

```python
_VALID_TASK_TYPES = {
    "research_draft",     # Phase 1 — LinkedIn
    "progress_post",      # Phase 1 — LinkedIn
    "analytics_review",   # Phase 2 — analytics
    "youtube_script",     # Phase 3 — YouTube (already listed)
    "youtube_produce",    # Phase 3 — NEW: trigger production
    "youtube_publish",    # Phase 3 — NEW: trigger upload
}

_VALID_ASSIGNED_TO = {
    "director", "cmo", "cto", "social_crew",
    "youtube_crew",  # NEW
    "media_crew",    # NEW (for produce tasks)
}
```

---

### 3.6 New n8n Workflow — YouTube Script Approval

Cloned from the LinkedIn content approval workflow (`tantra_linkedin_approval_workflow.json`), adapted for YouTube.

**Flow:**
```
Tantra API webhook trigger
  → n8n formats: title + hook + scene breakdown + thumbnail concept
      → Sends to your notification channel (Telegram/email/Slack)
           → You open n8n editor, review full script
                → Click "Approve" or "Reject" or "Request Edit"
                     → n8n calls back: POST /api/v1/youtube/{video_id}/approve
                          → Tantra API: YouTubeVideo.status = approved
                               → New AgentTask(youtube_produce) created
```

**New FastAPI routes needed (`api/routes.py`):**
```python
POST /api/v1/youtube/{video_id}/approve   # n8n calls this after human approval
POST /api/v1/youtube/{video_id}/reject    # n8n calls on rejection
GET  /api/v1/youtube/                     # list all YouTube videos with status
GET  /api/v1/youtube/{video_id}           # get single video details
```

---

### 3.7 New Celery Beat Schedules

Add to `tasks/celery_app.py`:

```python
# YouTube production pipeline
"youtube-script-Monday-8am": {
    "task": "tantra.tasks.director.dispatch_due_tasks",
    "schedule": crontab(hour=8, minute=0, day_of_week="monday"),
},
# Note: youtube_script AgentTasks are created by Director weekly_planning
# dispatch_due_tasks picks them up automatically — no per-task beat schedule needed

# Analytics pull already exists in Phase 1:
# youtube_analytics_pull — daily 8:30 AM
```

---

### 3.8 `tantra director status` — YouTube section

Extend the CLI `director status` command to show YouTube videos:

```
YouTube Videos
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Title                                  ┃ Status       ┃ Views        ┃ YouTube URL                        ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ How I built a local AI in 30 days      │ live         │ 1,240        │ https://youtu.be/dQw4w9WgXcQ       │
│ Tantra AI — Director chat demo         │ producing    │ —            │ —                                  │
│ Local LLMs on Apple Silicon            │ scripted     │ —            │ awaiting approval                  │
└────────────────────────────────────────┴──────────────┴──────────────┴────────────────────────────────────┘
```

---

## 4. Complete data flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Director (qwen3:30b)                                     │
│   weekly_planning OR director chat: "produce a youtube video about X"       │
│   → AgentTask(task_type="youtube_script", assigned_to="youtube_crew")       │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ dispatch_due_tasks (30min beat)
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│   generate_youtube_script (Celery task)                                     │
│   → YouTubeCrew (4 agents: researcher + writer + SEO + reviewer)            │
│   → YouTubeVideo created (status: scripted)                                 │
│   → n8n webhook fired (script + thumbnail concept)                          │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ human reviews in n8n
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│   n8n callback: POST /api/v1/youtube/{id}/approve                           │
│   → YouTubeVideo.status = approved                                          │
│   → AgentTask(task_type="youtube_produce") created                          │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ dispatch_due_tasks
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│   produce_youtube_video (Celery task)                                       │
│   Per scene:                                                                │
│     tantra-media: POST /generate/tts  → audio file                         │
│     tantra-media: POST /generate/video (Wan2.1) OR /image (FLUX.1)          │
│   tantra-media: POST /generate/image  → thumbnail.png                       │
│   tantra-media: POST /generate/assemble → final.mp4                        │
│   → YouTubeVideo.status = produced                                          │
│   → AgentTask(task_type="youtube_publish") created                          │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ dispatch_due_tasks
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│   upload_youtube_video (Celery task)                                        │
│   → YouTubeClient.upload_video(final.mp4, VideoMetadata)                    │
│   → thumbnails().set()                                                      │
│   → YouTubeVideo.status = live, youtube_video_id = "dQw4w9WgXcQ"           │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ daily beat
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│   youtube_analytics_pull (existing Phase 1 task)                            │
│   → YouTubeVideo: views, watch_time, CTR, avg_duration, likes, comments     │
│   → Director weekly_planning reads these on Friday → next plan adapts       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. File system additions

```
tantra-ai/
├── docker-compose.yml          ← add tantra-media service
├── Dockerfile                  ← add 'media' build target
├── docs/
│   └── phase3-youtube-architecture.md   ← this file
├── src/tantra/
│   ├── crews/
│   │   └── youtube_crew.py     ← NEW: YouTubeCrew (4 agents)
│   ├── db/
│   │   └── social.py           ← EXTEND: add YouTubeVideo model
│   ├── tasks/
│   │   ├── celery_app.py       ← EXTEND: new beat schedules
│   │   └── youtube_tasks.py    ← NEW: 4 Celery tasks
│   ├── api/
│   │   └── routes.py           ← EXTEND: /youtube/* endpoints
│   └── cli.py                  ← EXTEND: _VALID_TASK_TYPES, director status
├── tantra_media/               ← NEW: separate FastAPI microservice
│   ├── main.py                 # FastAPI app (port 8001)
│   ├── models/
│   │   ├── tts.py              # Kokoro / CosyVoice2
│   │   ├── image.py            # FLUX.1-Schnell
│   │   ├── video.py            # Wan2.1-T2V-14B
│   │   ├── transcriber.py      # Whisper Large V3 Turbo
│   │   └── assembler.py        # ffmpeg assembly
│   ├── jobs.py                 # Redis-backed async job queue
│   └── config.py               # model enable flags, paths
├── n8n/
│   ├── tantra_linkedin_approval_workflow.json   (existing)
│   └── tantra_youtube_script_approval_workflow.json  ← NEW
└── data/
    └── media/                  ← NEW (gitignored, volume-mounted)
        ├── audio/              # TTS narration MP3s
        ├── images/             # FLUX.1 thumbnails + scene images
        ├── clips/              # Wan2.1 per-scene MP4 clips
        └── output/             # Final assembled MP4s
```

---

## 6. Implementation order

### Phase 3a — Script pipeline (no media generation)

Build everything up to and including script approval. Videos are scripted and approved, but production is manual (human records/edits).

| Step | What | Files touched |
|------|------|---------------|
| 1 | `YouTubeVideo` DB model | `db/social.py` |
| 2 | Alembic migration | `alembic/versions/` |
| 3 | `YouTubeCrew` (4 agents) | `crews/youtube_crew.py` |
| 4 | `generate_youtube_script` Celery task | `tasks/youtube_tasks.py` |
| 5 | n8n YouTube script approval workflow | `n8n/` |
| 6 | FastAPI `/youtube/*` routes | `api/routes.py` |
| 7 | Extend `cli.py` — task types + director status | `cli.py` |
| 8 | Extend `celery_app.py` — register new tasks | `tasks/celery_app.py` |
| 9 | `tantra director chat` — test youtube_script commission | manual test |

### Phase 3b — Production pipeline

| Step | What | Files touched |
|------|------|---------------|
| 10 | `tantra-media` service skeleton (health + job queue) | `tantra_media/` |
| 11 | TTS module (Kokoro 82M — runs on CPU/MPS) | `tantra_media/models/tts.py` |
| 12 | Image module (FLUX.1-Schnell — thumbnail gen) | `tantra_media/models/image.py` |
| 13 | Assembler (ffmpeg — images + audio → video, no Wan2.1) | `tantra_media/models/assembler.py` |
| 14 | `produce_youtube_video` Celery task (calls tantra-media) | `tasks/youtube_tasks.py` |
| 15 | Docker: add tantra-media service | `docker-compose.yml`, `Dockerfile` |
| 16 | End-to-end test: script → produce → local MP4 | manual test |

### Phase 3c — Upload + analytics loop

| Step | What | Files touched |
|------|------|---------------|
| 17 | `upload_youtube_video` Celery task | `tasks/youtube_tasks.py` |
| 18 | YouTube OAuth callback route | `api/routes.py` |
| 19 | Extend `youtube_analytics_pull` → updates `YouTubeVideo` | `tasks/social_tasks.py` |
| 20 | Director weekly review reads YouTube analytics | `agents/director.py` |
| 21 | End-to-end: Director chat → approve → produce → upload → live | manual test |

### Phase 3d — Wan2.1 (high-VRAM machines only)

| Step | What | Files touched |
|------|------|---------------|
| 22 | Wan2.1-T2V-14B module | `tantra_media/models/video.py` |
| 23 | `NVIDIA_VISIBLE_DEVICES` config in docker-compose.nvidia.yml | `docker-compose.nvidia.yml` |
| 24 | Config flag `WAN21_ENABLED` → assembler picks video vs image pipeline | `tantra_media/config.py` |

---

## 7. Key design decisions

### D1 — Direct YouTube API for uploads, Zernio for community posts

`ZernioClient.post_video()` is for posting video *links* or short-form video to social feeds. For actual YouTube video uploads (up to 128 GB, resumable, with thumbnail setting), the YouTube Data API v3 is required. `YouTubeClient.upload_video()` already implements this with `MediaFileUpload(resumable=True)`.

Zernio is still used for YouTube *community posts* (text announcements about new videos) via `post_to_multiple()`.

### D2 — Two-service architecture (tantra-api + tantra-media)

The main Celery worker calls `tantra-media` via HTTP rather than running models in-process. This means:
- Media service can be restarted independently without killing in-flight tasks
- Media generation is gated by `TANTRA_MEDIA_ENABLED` — systems without GPU just skip it
- Memory pressure from model loading doesn't affect the main API or worker
- Future: tantra-media can run on a separate GPU machine via `TANTRA_MEDIA_BASE_URL`

### D3 — Remotion/ffmpeg as Wan2.1 fallback

On machines without enough VRAM for Wan2.1:
- Per-scene *images* are generated by FLUX.1-Schnell instead of video clips
- ffmpeg + Remotion assembles: image + audio + Ken Burns pan → video clip
- Same final output (MP4), no VRAM needed for video generation
- Config: `WAN21_ENABLED=false` in tantra-media docker service

### D4 — Script JSON is the single source of truth

The `script` JSON column in `YouTubeVideo` drives every downstream step:
- TTS narration: `scene.narration` for each scene
- Video/image gen: `scene.visual_prompt` for each scene
- Assembly: `scene.duration_seconds` for clip timing
- Thumbnail: `script.thumbnail_prompt`
- YouTube description: built from `script.scenes[].b_roll_description`

Storing the full script in Postgres (not just a summary) means any step can be re-run independently from the script, without re-running the crew.

### D5 — Production checkpointing via Redis

Same pattern as `research_draft` crew checkpointing. After each scene's media is generated, progress is saved to Redis DB3:
```
tantra:checkpoint:youtube_produce:{youtube_video_id}:scene_1 → {audio: "...", video: "..."}
tantra:checkpoint:youtube_produce:{youtube_video_id}:scene_2 → {audio: "...", video: "..."}
```
On `produce_youtube_video` restart, completed scenes are skipped. This is critical because Wan2.1 takes 2-5 minutes per scene — a 10-scene video takes 20-50 minutes to produce.

### D6 — Director weekly_planning learns from YouTube analytics

After Phase 3c, `weekly_planning` reads YouTube analytics alongside LinkedIn analytics when building the next week's plan:
- Top-performing topics → suggest similar themes
- Low CTR → Director recommends different thumbnail concepts
- Watch time drop-off → Director adjusts recommended script length
- Analytics are stored on `YouTubeVideo` rows, read by `DirectorAgent.get_live_context()`

---

## 8. Environment variables to add

```env
# tantra-media service
TANTRA_MEDIA_BASE_URL=http://tantra-media:8001
TANTRA_MEDIA_ENABLED=true

# YouTube n8n webhook (new workflow)
N8N_YOUTUBE_SCRIPT_WEBHOOK=http://n8n:5678/webhook/tantra-youtube-script

# YouTube OAuth (already in Settings — confirm scopes)
# YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_API_KEY already configured

# tantra-media model flags (set in docker-compose.yml, not .env)
# KOKORO_ENABLED=true
# FLUX_ENABLED=true
# WAN21_ENABLED=false
# WHISPER_ENABLED=true
```

---

## 9. Extending to Instagram and X (after YouTube)

Phase 3 is designed so Instagram and X are additive modules with minimal new infrastructure:

| Component | LinkedIn | YouTube | Instagram | X |
|-----------|----------|---------|-----------|---|
| Publishing | Zernio | YouTube API + Zernio community | Zernio | Zernio |
| Crew | SocialCrew | YouTubeCrew | InstagramCrew (new) | XCrew (new) |
| DB model | ContentQueueItem | YouTubeVideo | ContentQueueItem (extend) | ContentQueueItem (extend) |
| Content format | Text + image | Long-form video | Reels script + carousel | Thread |
| Approval | n8n (existing) | n8n (new workflow) | n8n (clone) | n8n (clone) |
| Media needs | Optional image | TTS + video + thumbnail | Reels video + image | None |

Instagram and X reuse `ZernioClient.post_to_multiple()` — the infrastructure is already wired. Their Celery tasks follow the same `generate → approve → publish` pattern. `_VALID_TASK_TYPES` gains `instagram_reel`, `x_thread`, etc. The Director treats them identically to `research_draft` and `youtube_script`.

---

*"तंत्र is not magic. It is a system. Build the system."*
