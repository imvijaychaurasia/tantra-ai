# Director — Memory

## Stack Context

- Stack: Python + CrewAI + Ollama (local GPU) + FastAPI + Celery + Redis + Postgres + Qdrant + n8n
- GPU: RTX 5070 Ti 16GB — all inference is local
- Models: qwen3:30b (director/frontier), qwen3:14b (manager/worker), qwen3:4b (fast), bge-m3 (embedder)

## Phase Completion Status

- Phase 1 ✅: LinkedIn content pipeline (research → draft → approve → publish) — LIVE
- Phase 2 ✅: Director planning engine (weekly plans, AgentTask dispatch) — LIVE
- Phase 3 ✅: YouTube pipeline — FULLY AUTONOMOUS
  - approved → produce → upload → thumbnail (zero manual steps)
  - Channel: Cyber GyanSagar (UCOWDfNmDDGMUvEIXNJSjHFw)
  - 5 videos LIVE as of 2026-04-06
  - Latest: https://www.youtube.com/watch?v=F3-RHXTR1yg
- Phase 3d (in progress): Multi-agent framework with hot-reload config files
- Phase 3e (planned): ComfyUI integration — cinematic video generation (Flux + LTX-Video)
- Phase 4 (planned): Remotion integration — structured animated content
- Phase 5 (planned): Instagram + X earning engine (auto + manual modes)

## Channel

- Name: Cyber GyanSagar साइबर ज्ञानसागर
- Channel ID: UCOWDfNmDDGMUvEIXNJSjHFw
- Focus: Educational content on technology, science, AI, space, and engineering
- Audience: Curious learners, students, engineers, and tech enthusiasts globally

## Key URLs

- API: http://localhost:8000
- Monitor: http://localhost:8000/monitor
- Agents Dashboard: http://localhost:8000/agents
- Media API: http://localhost:8100/docs
- Chat: http://localhost:3000
- LiteLLM: http://localhost:4000
- Flower: http://localhost:5555
- n8n: http://localhost:5678

## Known Deferred Work

- Video quality: text-only slideshow (no real visuals) — deferred to finishing stage
- ComfyUI cinematic renderer: planned for Phase 3e
