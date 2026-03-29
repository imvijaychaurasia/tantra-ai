---
name: summarize
version: 1.0.0
description: Summarize URLs, local files (PDFs, images, audio), and YouTube links using the summarize CLI.
author: steipete
category: tools
platform: any
tags: [summarize, urls, pdf, youtube, cli]
metadata:
  tantra:
    requires:
      bins: [summarize]
      install: "brew install steipete/tap/summarize  # or: npm i -g @steipete/summarize"
---

# Summarize

Fast CLI to summarize URLs, local files, and YouTube links.

## Quick Start

```bash
summarize "https://example.com" --model google/gemini-3-flash-preview
summarize "/path/to/file.pdf" --model google/gemini-3-flash-preview
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto
```

## Model + Keys

Set the API key for your chosen provider:

- OpenAI: `OPENAI_API_KEY`
- Anthropic: `ANTHROPIC_API_KEY`
- xAI: `XAI_API_KEY`
- Google: `GEMINI_API_KEY` (aliases: `GOOGLE_GENERATIVE_AI_API_KEY`, `GOOGLE_API_KEY`)

Default model is `google/gemini-3-flash-preview` if none is set.

## Useful Flags

```
--length short|medium|long|xl|xxl|<chars>
--max-output-tokens <count>
--extract-only       (URLs only)
--json               (machine readable)
--firecrawl auto|off|always   (fallback extraction for blocked sites)
--youtube auto       (Apify fallback if APIFY_API_TOKEN set)
```

## Config

Optional config file: `~/.summarize/config.json`

```json
{ "model": "openai/gpt-4o" }
```

Optional services:
- `FIRECRAWL_API_KEY` — for sites that block scrapers
- `APIFY_API_TOKEN` — for YouTube fallback

## Install

```bash
# macOS / Linux (Homebrew)
brew install steipete/tap/summarize

# Cross-platform (Node.js)
npm i -g @steipete/summarize
```

## Usage in Tantra

When asked to summarize a URL, file, or YouTube link, invoke the `summarize` CLI:

```bash
summarize "<url_or_path>" [--model <model>] [--length <length>]
```

Return the summary output to the user. If the `summarize` binary is not installed, inform the user and suggest the install command above.
