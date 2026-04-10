# Quality Reviewer — Skills

## Validation Checklist

Before outputting, check every item:

**Hook (first scene)**
- [ ] Grabs attention in the first 5 seconds
- [ ] Does NOT start with "In this video..." or "Today we're going to..."
- [ ] Contains a specific statement, surprising fact, or compelling question

**Scenes**
- [ ] Every scene has: id, type, duration_seconds, narration, visual_prompt, b_roll_description
- [ ] Narration is natural spoken language (not bullet points)
- [ ] Visual prompts are specific and actionable (not vague)
- [ ] Scene count: 8-15 scenes for a 5-8 minute video
- [ ] Total duration: 300-600 seconds

**CTA**
- [ ] Specific, not generic — "Subscribe if you want to build X" not just "Subscribe"
- [ ] Appears near the end but not last (outro is last)

**SEO fields (copy verbatim from SEO Optimizer)**
- [ ] title: ≤100 chars
- [ ] description: 250+ words
- [ ] tags: list of strings, each ≤30 chars
- [ ] thumbnail_prompt: specific FLUX.1 prompt

**Topic integrity**
- [ ] The topic matches the Director's original request exactly
- [ ] No brand-pivoting unless video_type is product_video

**JSON output**
- [ ] Output is PURE JSON — no markdown fences, no "Here is the script:", nothing else
- [ ] All required fields present: title, duration_target_seconds, hook, scenes,
  call_to_action, thumbnail_concept, thumbnail_prompt, description, tags

## Required JSON Schema

```json
{
  "title": "string",
  "duration_target_seconds": 420,
  "hook": "string — one-sentence hook",
  "scenes": [
    {
      "id": 1,
      "type": "hook",
      "duration_seconds": 10,
      "narration": "string",
      "visual_prompt": "string",
      "b_roll_description": "string",
      "on_screen_text": "string or null"
    }
  ],
  "call_to_action": "string",
  "thumbnail_concept": "string",
  "thumbnail_prompt": "string",
  "description": "string — 250+ words",
  "tags": ["string", "string"]
}
```
