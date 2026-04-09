# Tantra AI — Multi-Agent Framework Design

**Status**: Design (pre-implementation)
**Author**: Vijay Chaurasia + Claude
**Date**: 2026-04-09
**Scope**: Replace monolithic agent definitions with a file-per-concern, per-agent config framework

---

## 1. Problem Statement

The current agent architecture is monolithic:

- `director.py` contains 817 lines — soul, skills, policy, prompts, task extraction, and chat logic all in one file
- Agent identity (who it is, what it does, what it won't do) lives as Python string constants that require code changes to modify
- No per-agent reflection, learning, or evaluation loop — agents don't improve over time
- New agents (media/video-agent, media/image-agent, coding, infra) have no established pattern to follow
- No visibility into agent state — no browser-accessible view of what each agent knows, has learned, or decided

---

## 2. Design Goals

1. **Separation of concerns** — identity, capability, policy, and learned behaviour are separate files
2. **File-driven config** — changing an agent's soul or policy requires editing a markdown file, not Python
3. **Living agents** — reflection, learning, and feedback files are auto-updated after each task
4. **Discoverable** — all agent configs are browsable via a dashboard at `/agents`
5. **Non-breaking migration** — existing pipeline (YouTube, LinkedIn) continues to work during migration
6. **Consistent pattern** — every new agent follows the same 9-file structure from day one

---

## 3. Target Directory Structure

```
tantra-ai/
├── agents/                          ← per-agent config directories (NEW)
│   │
│   ├── _framework/                  ← base templates for new agents
│   │   ├── soul.template.md
│   │   ├── skills.template.md
│   │   ├── policy.template.md
│   │   ├── reflection.template.md
│   │   ├── learning.template.md
│   │   ├── feedback.template.md
│   │   └── evaluation.template.md
│   │
│   ├── director/                    ← migrated from director.py constants
│   │   ├── soul.md
│   │   ├── skills.md
│   │   ├── memory.md
│   │   ├── policy.md
│   │   ├── tools.json
│   │   ├── reflection.md            ← auto-written after each director session
│   │   ├── learning.md              ← distilled lessons (auto-updated weekly)
│   │   ├── feedback.md              ← human + system feedback received
│   │   └── evaluation.md            ← performance scores and trends
│   │
│   ├── youtube-crew/                ← migrated from youtube_crew.py constants
│   │   ├── soul.md
│   │   ├── skills.md
│   │   ├── memory.md
│   │   ├── policy.md
│   │   ├── tools.json
│   │   ├── reflection.md            ← per-script quality self-assessment
│   │   ├── learning.md              ← what makes scripts perform well
│   │   ├── feedback.md              ← video performance data fed back in
│   │   └── evaluation.md            ← script quality scores
│   │
│   ├── social-crew/                 ← migrated from social_crew.py constants
│   │   ├── soul.md
│   │   ├── skills.md
│   │   ├── memory.md
│   │   ├── policy.md
│   │   ├── tools.json
│   │   ├── reflection.md
│   │   ├── learning.md
│   │   ├── feedback.md
│   │   └── evaluation.md
│   │
│   ├── media/
│   │   ├── video-agent/             ← Phase 3d: ComfyUI/LTX controller (NEW)
│   │   │   ├── soul.md
│   │   │   ├── skills.md
│   │   │   ├── memory.md
│   │   │   ├── policy.md
│   │   │   ├── tools.json           ← ComfyUI API endpoints
│   │   │   ├── reflection.md
│   │   │   ├── learning.md          ← what prompts produce best visuals
│   │   │   ├── feedback.md
│   │   │   └── evaluation.md        ← visual quality scores
│   │   │
│   │   └── image-agent/             ← Phase 3d: Flux.1-dev controller (NEW)
│   │       ├── soul.md
│   │       ├── skills.md
│   │       ├── memory.md
│   │       ├── policy.md
│   │       ├── tools.json
│   │       ├── reflection.md
│   │       ├── learning.md
│   │       ├── feedback.md
│   │       └── evaluation.md
│   │
│   ├── frontier/                    ← Claude/GPT-4 tier (future)
│   ├── coding/                      ← code generation agent (future)
│   └── infra/                       ← infrastructure agent (future)
│
└── core/
    ├── planner.md                   ← how WeeklyPlan + task dispatch works
    ├── workflow.md                  ← task state machine definition
    ├── context.md                   ← context passing protocol between agents
    └── evaluation.md                ← global evaluation criteria and scoring
```

---

## 4. File Specifications

### 4.1 `soul.md` — Identity (human-authored, static)

The agent's personality, values, voice, and backstory. Loaded into every system prompt.

```markdown
# Soul — [Agent Name]

## Identity
One sentence: who am I and what is my purpose in this system?

## Personality & Voice
- Tone: [direct | warm | technical | creative]
- Communication style: [brief | detailed | narrative]
- What makes me distinctive:

## Values
What I genuinely care about in my work. What drives my decisions when the instructions are ambiguous.

## Backstory
How I came to exist in the Tantra AI system. My history and context.

## Relationship to Other Agents
Who I work with, who I report to, who works for me.
```

---

### 4.2 `skills.md` — Capabilities (human-authored, static)

What the agent can do, what tasks it handles, what it produces.

```markdown
# Skills — [Agent Name]

## Primary Capabilities
What I can do well. My core function.

## Task Types I Handle
| task_type | Description | Output |
|-----------|-------------|--------|
| youtube_script | ... | YouTubeScript JSON |

## Output Formats
What I produce and in what form.

## Escalation
What I hand off to other agents and when.

## Limitations
What I cannot or should not attempt.
```

---

### 4.3 `memory.md` — Memory Configuration (human-authored + auto-updated)

Memory namespace config and a running summary of what the agent has retained.

```markdown
# Memory — [Agent Name]

## Namespace
qdrant_namespace: agent:director
episodic_table: agent_events

## Retention Policy
What I remember permanently vs. what I let expire.

## Current Memory Summary
[Auto-updated weekly by the memory consolidation task]
Last consolidated: YYYY-MM-DD
Key facts retained:
- ...
```

---

### 4.4 `policy.md` — Rules & Constraints (human-authored, static)

Hard rules the agent never violates, soft guidelines, content policy, and escalation triggers.

```markdown
# Policy — [Agent Name]

## Hard Rules (never violate)
1. ...
2. ...

## Soft Guidelines (prefer, can deviate with reasoning)
1. ...

## Content Policy
What topics, formats, or outputs are out of bounds.

## Escalation Rules
When to stop and ask a human before proceeding.

## Inter-agent Trust
Which agents I trust implicitly vs. which I verify.
```

---

### 4.5 `tools.json` — Tool Definitions (machine-readable)

All tools and APIs available to this agent. Used by the loader to inject tool descriptions.

```json
{
  "version": "1.0",
  "agent": "director",
  "tools": [
    {
      "name": "queue_youtube_script",
      "type": "celery_task",
      "task_name": "tantra.tasks.youtube.generate_youtube_script",
      "description": "Commission a YouTube video script on any topic",
      "parameters": {
        "topic_hint": "string",
        "video_type": "slideshow|educational|product_video|visual_video|marketing_video"
      }
    },
    {
      "name": "queue_research_draft",
      "type": "celery_task",
      "task_name": "tantra.tasks.social.research_and_draft_posts",
      "description": "Research a topic and draft LinkedIn posts",
      "parameters": {}
    }
  ]
}
```

---

### 4.6 `reflection.md` — Task Reflections (auto-written by agent)

Written by the agent after each significant task. Append-only log, newest first.
**Never edited by humans** — this is the agent's own voice.

```markdown
# Reflection Log — [Agent Name]

---
## 2026-04-09 | Task: Generate YouTube script — "Artemis II"
**Confidence**: 8/10
**Duration**: 169s

### What went well
The topic drift fix worked. Script stayed on Artemis II throughout. 
Visual descriptions were specific and cinematically useful.

### What could improve
Tags were returned as CSV string instead of array — caused upload failure.
Should have validated output schema before returning.

### Unexpected observations
The crew researcher tried to pivot to "AI on laptops" angle — caught and corrected
by topic enforcement. May need stronger guardrails for space/science topics.

### Decision made
Used label:visual_video routing. Confirmed correct.

---
## 2026-04-08 | Task: Weekly planning for Week 15
...
```

---

### 4.7 `learning.md` — Distilled Lessons (auto-updated by learning consolidation task)

Synthesised from multiple reflections. Updated weekly by a background task that reads
all recent reflections and extracts durable patterns. Highest-confidence lessons are
injected into the agent's system prompt.

```markdown
# Learning — [Agent Name]
Last consolidated: 2026-04-09

## Verified Patterns (high confidence, injected into system prompt)
- YouTube tags must be returned as JSON array, never CSV string
- topic_hint bypasses channel_context — always set it for non-Tantra-AI topics
- visual_description field should be cinematic and specific (lighting, angle, mood)

## Hypotheses (being tested — 2+ observations, not yet verified)
- Longer scripts (>8 scenes) produce higher engagement — testing

## Abandoned Approaches (tried, didn't work)
- edge-tts: blocked from VPS IPs via WebSocket (403)
- kokoro-onnx: model files renamed, 404 on download
- execute_agent_task for produce dispatch: returned null task_id

## Open Questions
- Does gTTS accent setting affect retention?
```

---

### 4.8 `feedback.md` — Received Feedback (auto-written by feedback pipeline)

External signals: human corrections, performance metrics, other agents' assessments.
Structured so the learning consolidation task can parse it.

```markdown
# Feedback — [Agent Name]
Last updated: 2026-04-09

## Human Feedback
| Date | Context | Feedback | Action taken |
|------|---------|----------|--------------|
| 2026-04-09 | Artemis II script | "topic drift — still talking about Tantra AI" | Added MANDATORY topic directive |
| 2026-04-09 | Director content policy | "should create videos on ANY topic not just Tantra AI" | Rewrote content policy section |

## Agent Feedback (from other agents)
| Date | From | Context | Signal |
|------|------|---------|--------|
| 2026-04-09 | youtube-crew | Artemis II script | quality_score: 8/10, topic_adherence: 9/10 |

## System Metrics Feedback
| Date | Metric | Value | Baseline | Delta |
|------|--------|-------|----------|-------|
| 2026-04-09 | upload_success_rate | 80% | 60% | +20% |
| 2026-04-09 | produce_task_null_rate | 0% | 100% | -100% |
```

---

### 4.9 `evaluation.md` — Performance Scores (auto-written by evaluation task)

Scored metrics per agent. Used for weekly review and for steering learning.md updates.

```markdown
# Evaluation — [Agent Name]
Last scored: 2026-04-09

## Scoring Rubric
| Dimension | Weight | Description |
|-----------|--------|-------------|
| task_success_rate | 30% | Tasks completed without failure |
| output_quality | 25% | Human rating of outputs |
| policy_compliance | 20% | Zero hard-rule violations |
| topic_adherence | 15% | Output matches requested topic |
| self_awareness | 10% | Reflection quality and accuracy |

## Current Scores (Week 15/2026)
| Dimension | Score | Notes |
|-----------|-------|-------|
| task_success_rate | 9/10 | 1 failed upload (invalidTags), now fixed |
| output_quality | 7/10 | Text-only slides, visual quality deferred |
| policy_compliance | 10/10 | No hard-rule violations |
| topic_adherence | 8/10 | Artemis II drifted initially, fixed |
| self_awareness | 6/10 | Reflection loop not yet active |
| **Overall** | **8/10** | |

## Trend
Week 14: N/A (first week)
Week 15: 8/10 (baseline set)

## Next improvement targets
1. Output quality: implement ComfyUI visuals (Phase 3d)
2. Self-awareness: activate reflection loop post-task
```

---

## 5. Core Framework Files

### `core/planner.md`
How WeeklyPlan is created, AgentTask rows are generated, and tasks are dispatched via Celery. Reference for all agents that participate in planning.

### `core/workflow.md`
The task state machine: `pending → in_progress → completed | failed`. YouTube-specific: `scripted → approved → producing → produced → uploading → live`. Social: `draft → approved → published`. All valid transitions and what triggers them.

### `core/context.md`
What information flows between agents. How `topic_hint`, `video_type`, `channel_context`, `plan_id` are passed. The contract for inter-agent communication.

### `core/evaluation.md`
Global evaluation criteria. Defines the scoring rubric used across all `evaluation.md` files. The ground truth for what "good" means in this system.

---

## 6. The Loader (`src/tantra/core/agent_loader.py`)

The loader is the only new Python code required. Everything else is just files.

```python
class AgentConfigLoader:
    """
    Reads agent config files and assembles a system prompt.
    
    Usage:
        loader = AgentConfigLoader("director")
        system_prompt = loader.build_system_prompt(inject_learning=True)
        tools = loader.get_tools()
    """
    
    BASE_PATH = Path(__file__).parent.parent.parent.parent / "agents"
    
    # Which files are loaded into the system prompt (in order)
    STATIC_FILES = ["soul.md", "skills.md", "policy.md"]
    
    # Which files are injected as additional context (top N lines only)
    DYNAMIC_FILES = [
        ("learning.md", 50),    # top 50 lines — verified patterns only
        ("evaluation.md", 20),  # recent scores — agent knows how it's doing
    ]
    
    def build_system_prompt(self, inject_learning: bool = True) -> str:
        ...
    
    def get_tools(self) -> list[dict]:
        ...
    
    def append_reflection(self, task: str, reflection: str) -> None:
        """Append a new reflection entry to reflection.md"""
        ...
    
    def append_feedback(self, source: str, context: str, feedback: str) -> None:
        """Append a feedback entry to feedback.md"""
        ...
```

### Migration path for director.py

```python
# BEFORE (current)
DIRECTOR_CHAT_SYSTEM_PROMPT = """
You are the Director of Tantra AI (तंत्र)...
[220 lines of hardcoded identity, capabilities, policies]
"""

# AFTER (migrated)
from tantra.core.agent_loader import AgentConfigLoader

_loader = AgentConfigLoader("director")
DIRECTOR_CHAT_SYSTEM_PROMPT = _loader.build_system_prompt(inject_learning=True)
```

The `director.py` file shrinks dramatically. The 220-line string becomes three readable markdown files. Nothing else in the call chain changes.

---

## 7. Reflection & Learning Loop

This is what makes agents improve over time — the self-reinforcing cycle:

```
Task executes
    ↓
Agent appends to reflection.md
(structured: what went well, what to improve, anomalies, confidence score)
    ↓
Weekly: consolidation task reads ALL reflections since last consolidation
    ↓
LLM synthesises: extracts verified patterns, promotes hypotheses, archives outdated lessons
    ↓
Writes updated learning.md
    ↓
Next task: loader injects learning.md verified patterns into system prompt
    ↓
Agent performs better
```

The reflection writer is triggered at the end of each Celery task (for applicable agents). It's a short LLM call (~200 tokens) that reads the task result and writes a structured entry.

---

## 8. Browser Dashboard — `/agents`

A new page in the Tantra AI web UI that lets you browse and read every agent's config files.

### URL structure
```
/agents                           → list all agents (cards grid)
/agents/director                  → director's file tabs
/agents/director/soul             → soul.md rendered as markdown
/agents/director/reflection       → reflection.md (live, auto-refresh)
/agents/youtube-crew/learning     → learning.md
/agents/media/video-agent/policy  → policy.md
```

### API endpoints (new, added to routes.py)
```
GET  /api/v1/agents/                             → list all agent directories
GET  /api/v1/agents/{agent_path}/files           → list files for agent
GET  /api/v1/agents/{agent_path}/files/{file}    → get file content (raw markdown)
PUT  /api/v1/agents/{agent_path}/files/{file}    → update static file (soul/skills/policy only)
POST /api/v1/agents/{agent_path}/reflect         → manually trigger reflection for an agent
```

### UI design
- Left sidebar: tree of agents (director, youtube-crew, social-crew, media/video-agent, ...)
- Main panel: tab row — Soul | Skills | Policy | Tools | Reflection | Learning | Feedback | Evaluation
- Dynamic tabs (reflection, learning, feedback, evaluation) have a "last updated" timestamp and auto-refresh every 30s
- Static tabs (soul, skills, policy) have an Edit button — inline markdown editor, saves via PUT endpoint
- Evaluation tab shows a visual score card (bar chart per dimension)

---

## 9. Implementation Plan (next session)

### Session 1: Foundation (core loader + director migration)
1. Create `agents/` directory with all subdirectories
2. Write `soul.md`, `skills.md`, `policy.md` for `director` (extract from director.py)
3. Write `tools.json` for `director`
4. Create empty `reflection.md`, `learning.md`, `feedback.md`, `evaluation.md` with headers
5. Implement `src/tantra/core/agent_loader.py` (loader class)
6. Migrate `director.py`: replace hardcoded string with `AgentConfigLoader("director").build_system_prompt()`
7. Test: `tantra director chat` should work identically to before

### Session 2: Remaining existing agents + reflection loop
1. Create config files for `youtube-crew` and `social-crew`
2. Migrate `youtube_crew.py` prompts to files
3. Implement reflection writer (triggered at end of `generate_youtube_script` task)
4. Implement learning consolidation task (weekly Celery beat)

### Session 3: Browser dashboard + media agents
1. Add `/agents` API endpoints to `routes.py`
2. Build `/agents` dashboard HTML (markdown rendering with marked.js)
3. Create `media/video-agent/` and `media/image-agent/` config files (ready for Phase 3d)

### Session 4: Core framework files + evaluation
1. Write `core/planner.md`, `core/workflow.md`, `core/context.md`, `core/evaluation.md`
2. Implement evaluation scoring task (weekly, writes to each agent's `evaluation.md`)
3. Wire feedback.md updates to YouTube video performance data

---

## 10. What Does NOT Change

- All Celery tasks (`youtube_tasks.py`, `social_tasks.py`, etc.) — unchanged
- Database models — unchanged  
- API routes (except new `/agents` ones) — unchanged
- The CrewAI crew definitions (`youtube_crew.py`, etc.) — agent backstories move to files, but crew wiring stays in Python
- n8n workflows — unchanged
- Docker compose — unchanged

The framework is purely additive until the final migration step (replacing hardcoded strings with loader calls).
