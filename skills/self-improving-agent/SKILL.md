---
name: self-improving-agent
version: 3.0.10
description: Captures learnings, errors, and corrections to enable continuous improvement. Log to .learnings/ files for review and promotion to project memory.
author: pskoett
category: agent
platform: any
tags: [learning, self-improvement, errors, corrections, memory]
---

# Self-Improvement Skill

Log learnings and errors to markdown files for continuous improvement. Coding agents can later process these into fixes, and important learnings get promoted to project memory.

## First-Use Initialisation

Before logging anything, ensure the `.learnings/` directory and files exist:

```bash
mkdir -p .learnings
[ -f .learnings/LEARNINGS.md ] || printf "# Learnings\n\nCorrections, insights, and knowledge gaps.\n\n**Categories**: correction | insight | knowledge_gap | best_practice\n\n---\n" > .learnings/LEARNINGS.md
[ -f .learnings/ERRORS.md ] || printf "# Errors\n\nCommand failures and integration errors.\n\n---\n" > .learnings/ERRORS.md
[ -f .learnings/FEATURE_REQUESTS.md ] || printf "# Feature Requests\n\nCapabilities requested by the user.\n\n---\n" > .learnings/FEATURE_REQUESTS.md
```

Never overwrite existing files. **Do not log secrets, tokens, or full source files.**

## Quick Reference

| Situation | Action |
|---|---|
| Command/operation fails | Log to `.learnings/ERRORS.md` |
| User corrects you | Log to `.learnings/LEARNINGS.md` (category: correction) |
| User wants missing feature | Log to `.learnings/FEATURE_REQUESTS.md` |
| API/external tool fails | Log to `.learnings/ERRORS.md` |
| Knowledge was outdated | Log to `.learnings/LEARNINGS.md` (category: knowledge_gap) |
| Found better approach | Log to `.learnings/LEARNINGS.md` (category: best_practice) |
| Broadly applicable learning | Promote to `CLAUDE.md` / `AGENTS.md` |

## Detection Triggers

Automatically log when you notice:

**Corrections** (→ learning, category: correction):
- "No, that's not right..."
- "Actually, it should be..."
- "You're wrong about..."

**Feature Requests** (→ feature request):
- "Can you also..."
- "I wish you could..."

**Knowledge Gaps** (→ learning, category: knowledge_gap):
- User provides info you didn't know
- API behavior differs from your understanding

**Errors** (→ error entry):
- Command returns non-zero exit code
- Exception or stack trace
- Timeout or connection failure

## Logging Format

### Learning Entry — `.learnings/LEARNINGS.md`

```markdown
## [LRN-YYYYMMDD-XXX] category
**Logged**: ISO-8601 timestamp
**Priority**: low | medium | high | critical
**Status**: pending
**Area**: frontend | backend | infra | tests | docs | config

### Summary
One-line description.

### Details
Full context: what happened, what was wrong, what's correct.

### Suggested Action
Specific fix or improvement.

### Metadata
- Source: conversation | error | user_feedback
- Related Files: path/to/file.ext
- Tags: tag1, tag2
- Pattern-Key: simplify.dead_code (optional, for recurring tracking)
- Recurrence-Count: 1
---
```

### Error Entry — `.learnings/ERRORS.md`

```markdown
## [ERR-YYYYMMDD-XXX] skill_or_command_name
**Logged**: ISO-8601 timestamp
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
Brief description of what failed.

### Error
Actual error message (redact secrets).

### Context
- Command attempted
- Environment details

### Suggested Fix
What might resolve this.

### Metadata
- Reproducible: yes | no | unknown
---
```

## Promoting to Project Memory

When a learning is broadly applicable, promote it:

| Learning Type | Promote To | Example |
|---|---|---|
| Behavioral patterns | `SOUL.md` | "Be concise, avoid disclaimers" |
| Workflow improvements | `AGENTS.md` | "Spawn sub-agents for long tasks" |
| Tool gotchas | `TOOLS.md` | "Git push needs auth configured first" |
| Project conventions | `CLAUDE.md` | "Use pnpm, not npm" |

**When to promote:**
- Learning applies across multiple files/features
- Prevents recurring mistakes
- Documents project-specific conventions

**How to promote:**
1. Distill into a concise rule
2. Add to appropriate target file
3. Update original entry: `Status: promoted`, add `Promoted: CLAUDE.md`

## Periodic Review

Review `.learnings/` at natural breakpoints:

```bash
# Count pending items
grep -h "Status\*\*: pending" .learnings/*.md | wc -l

# List high-priority pending items
grep -B5 "Priority\*\*: high" .learnings/*.md | grep "^## \["
```

## Best Practices

- **Log immediately** — context is freshest right after the issue
- **Be specific** — future agents need to understand quickly
- **Include reproduction steps** — especially for errors
- **Suggest concrete fixes** — not just "investigate"
- **Promote aggressively** — if in doubt, add to `CLAUDE.md`
- **Never log secrets** — redact tokens, passwords, API keys
