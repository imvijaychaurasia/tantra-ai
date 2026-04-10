# Core — Planner

## Task Planning Rules

1. Every plan starts with the outcome, not the steps. What does "done" look like?
2. Break goals into ≤5 concrete subtasks. If you need more, the goal is too big.
3. Each subtask must map to exactly one agent or Celery task type.
4. Dependencies must be explicit. Parallel tasks run simultaneously by default.
5. Always define success criteria before starting.

## Delegation Hierarchy

```
Director (CAIO)
  └── YouTube Crew
        ├── Researcher (worker tier)
        ├── Script Writer (director tier)
        ├── SEO Optimizer (worker tier)
        └── Quality Reviewer (worker tier)
  └── Social Crew
        ├── Researcher (worker tier)
        └── Drafter (director tier)
  └── Media Agents (Phase 3e+)
        ├── Image Agent (Flux.1-dev)
        └── Video Agent (LTX-Video / HunyuanVideo)
```

## Priority Levels

- `critical`: blocks other work, execute immediately
- `high`: execute this week
- `medium`: execute when capacity available
- `low`: backlog

## Task Lifecycle

`queued` → `running` → `done` | `failed` | `needs_review`
