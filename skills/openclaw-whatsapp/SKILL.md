---
name: openclaw-whatsapp
version: 0.1.1
description: WhatsApp integration via wacli CLI — chat lookup, message search, history backfill, and explicit sends. Requires OpenClaw runtime + wacli installed.
author: clawic
category: tools
platform: openclaw
tags: [whatsapp, messaging, wacli, openclaw-plugin]
metadata:
  tantra:
    requires:
      bins: [wacli]
    notes: "Native OpenClaw plugin. Install via: openclaw plugins install clawhub:whatsapp. Requires wacli auth completed."
---

# OpenClaw WhatsApp Plugin

Provides WhatsApp tools via the local `wacli` CLI. This is an OpenClaw native plugin — it runs inside the OpenClaw gateway runtime.

## Requirements

1. Install wacli:
   ```bash
   brew install steipete/tap/wacli
   ```
2. Authenticate once:
   ```bash
   wacli auth
   ```
3. Install the plugin in OpenClaw:
   ```bash
   openclaw plugins install clawhub:whatsapp
   ```

## Config (openclaw config)

```json
{
  "plugins": {
    "entries": {
      "whatsapp": {
        "enabled": true,
        "config": {
          "wacliPath": "/opt/homebrew/bin/wacli",
          "storePath": "~/.wacli",
          "requireExplicitSendConfirmation": true,
          "defaultChatListLimit": 20,
          "defaultMessageSearchLimit": 20
        }
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|---|---|
| `whatsapp_chats_list` | List recent chats |
| `whatsapp_messages_search` | Search message history |
| `whatsapp_history_backfill` | Backfill older messages for a chat |
| `whatsapp_doctor` | Diagnose sync/auth issues |
| `whatsapp_send_text` | Send a text message (requires confirm=true) |
| `whatsapp_send_file` | Send a file with caption (requires confirm=true) |

## Usage Examples

```
Search my WhatsApp history for "invoice" after 2025-01-01.
Find the JID for the Project Alpha group and backfill older messages.
Send a WhatsApp message to +14155551212 saying the files are ready.
```

⚠️ The send tools require `confirm=true` — the agent cannot send a message without an explicit confirmation step.

## Security Notes

- This plugin only reads/writes your local wacli data store
- No credentials are sent to external servers
- Keep `requireExplicitSendConfirmation: true` always enabled
- Scan is ✅ Benign (high confidence)
