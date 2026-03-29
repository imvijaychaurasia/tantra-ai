---
name: openclaw-database
version: 0.1.0
description: Local SQLite database management via OpenClaw plugin — schema inspection, raw SQL, transactions, and CRUD helpers. Requires OpenClaw runtime.
author: clawic
category: tools
platform: openclaw
tags: [database, sqlite, sql, openclaw-plugin]
metadata:
  tantra:
    notes: "Native OpenClaw plugin. Install via: openclaw plugins install clawhub:database"
---

# OpenClaw Database Plugin

Adds local SQLite database management to OpenClaw agents. Schema inspection, raw SQL, transactional execution, and full CRUD.

## Install (in OpenClaw)

```bash
openclaw plugins install clawhub:database
openclaw plugins enable database
openclaw gateway restart
```

## Config

```json
{
  "plugins": {
    "entries": {
      "database": {
        "enabled": true,
        "config": {
          "storagePath": "~/.openclaw/state/database/database.sqlite",
          "resultRowLimit": 100,
          "busyTimeoutMs": 5000,
          "enableWal": true
        }
      }
    }
  }
}
```

Default path: `~/.openclaw/state/database/database.sqlite`

## Available Tools

| Tool | Description |
|---|---|
| `database_list_tables` | List all tables |
| `database_describe_table` | Column/index info for a table |
| `database_schema` | Full CREATE statements |
| `database_create_table` | Create a table |
| `database_create_index` | Create an index |
| `database_drop_index` | Drop an index |
| `database_drop_table` | Drop a table |
| `database_query` | Execute a SELECT query |
| `database_execute` | Execute any SQL statement |
| `database_transaction` | Multi-statement transaction (rollback on failure) |
| `database_insert` | Insert rows |
| `database_select` | Structured SELECT helper |
| `database_update` | Update rows |
| `database_delete` | Delete rows |

## Usage Examples

```
Create a table called tasks with id, title, status, and created_at columns.
Show me all tables in the database.
Insert a new task: "Deploy Phase 2" with status "pending".
Select all tasks where status is pending.
```

## Security Notes

- Database is fully local (no external network calls)
- Only accessible within OpenClaw gateway
- Scan is ✅ Benign (high confidence)
- Don't point storagePath at an existing sensitive file
