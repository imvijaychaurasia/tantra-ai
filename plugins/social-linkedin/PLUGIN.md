---
name: social-linkedin
version: 1.0.0
description: LinkedIn content pipeline — research, draft, approve, publish, engage
author: vijaychaurasia
enabled: true
homepage: https://github.com/imvijaychaurasia/tantra-ai
capabilities: {"celery_tasks": ["research_and_draft_posts", "publish_approved_linkedin_posts", "linkedin_engage_feed", "post_tantra_progress"], "api_routes": ["/api/v1/content", "/api/v1/social"], "tools": ["ZernioClient"], "skills": ["linkedin-human-post", "linkedin-human-comment", "social-researcher", "tantra-build-context", "post-approver"]}
dependencies: ["zernio-sdk>=0.1.0", "crewai>=0.130.0", "litellm>=1.0.0"]
metadata: {"tantra": {"requires": {"env": ["ZERNIO_API_KEY"]}}}
---

# social-linkedin Plugin

The core Phase 1 LinkedIn content pipeline for Tantra AI.

## What it does

Automates the full LinkedIn content lifecycle:

1. **Research** — Social Crew (CrewAI) researches topics and produces 3 drafts
2. **Approve** — n8n webhook workflow sends drafts for human review
3. **Publish** — Zernio SDK posts approved content to LinkedIn
4. **Engage** — Scans published posts, generates AI-topic comments
5. **Progress** — Daily human-tone post about the Tantra AI build journey

## Celery tasks registered

- `tantra.tasks.social.research_and_draft_posts` — Mon/Wed/Fri 7 AM
- `tantra.tasks.social.publish_approved_linkedin_posts` — Weekdays 9 AM
- `tantra.tasks.social.linkedin_engage_feed` — Every 4 hours
- `tantra.tasks.social.post_tantra_progress` — Weekdays 9:30 AM

## Skills bundled

- `linkedin-human-post` — writing style for progress posts
- `linkedin-human-comment` — writing style for engagement comments
- `social-researcher` — research output format for the crew
- `tantra-build-context` — project context injected into prompts
- `post-approver` — quality gate for draft review

## Configuration

Required env vars:
- `ZERNIO_API_KEY` — Zernio account API key
- `ZERNIO_LINKEDIN_ACCOUNT_ID` — LinkedIn account ID from Zernio

Optional:
- `GROQ_API_KEY` — enables Groq cloud inference for director-tier tasks
