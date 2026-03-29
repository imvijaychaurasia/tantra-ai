---
name: openclaw-git-tools
version: 1.0.0
description: Git repository management in the OpenClaw workspace — repo scanning, pre-push safety checks, clone/pull. Requires OpenClaw runtime + git CLI.
author: marxbiotech
category: tools
platform: openclaw
tags: [git, repositories, devops, openclaw-plugin]
metadata:
  tantra:
    requires:
      bins: [git]
    notes: "Native OpenClaw plugin. Install via: openclaw plugins install clawhub:marxbiotech-git-tools"
---

# OpenClaw Git Tools Plugin

Manages git repositories in the OpenClaw workspace. Supports repo scanning, safety checks before push, and clone/pull operations.

## Install (in OpenClaw)

```bash
openclaw plugins install clawhub:marxbiotech-git-tools
```

Requires `git` CLI installed in the container.

## Available Commands

| Command | Description |
|---|---|
| `/git_check [path]` | Pre-push safety check — sensitive files, diff size, branch name, divergence |
| `/git_sync [url]` | Pull all workspace repos (no args) or clone a new repo by URL |
| `/git_repos` | Scan all workspace repos — branch, dirty status, last commit |

## Usage Examples

```
/git_repos
/git_check
/git_sync https://github.com/imvijaychaurasia/tantra-ai.git
```

## Notes

- Repos are stored under the workspace `repos/` directory (default: `/root/.openclaw/workspace/repos`)
- The plugin sets `git --global pull.rebase = true` inside the container
- Clone/pull operations use any SSH keys or credential helpers present in the container (e.g. `~/.ssh`)
- Only run trusted URLs with `/git_sync`

## Security Notes

- Scan is ✅ Benign (high confidence)
- Sets `pull.rebase = true` globally inside the container — expected side effect
- Uses container SSH keys for authenticated operations — ensure keys are set up via `/ssh_setup`
