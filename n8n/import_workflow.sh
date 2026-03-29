#!/usr/bin/env bash
# Tantra AI — Import n8n approval workflow via API
# Usage: bash n8n/import_workflow.sh   (run from repo root)
# Requires: N8N_TOKEN env var set before calling
set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
N8N_URL="${N8N_URL:-http://localhost:5678}"
N8N_TOKEN="${N8N_TOKEN:-}"

if [[ -z "$N8N_TOKEN" ]]; then
  echo "ERROR: N8N_TOKEN is not set."
  echo "  1. Go to http://localhost:5678/settings/api"
  echo "  2. Create an API key"
  echo "  3. Export it:  export N8N_TOKEN=<your-key>"
  exit 1
fi

# ── Resolve the JSON source relative to the repo root ────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_JSON="${SCRIPT_DIR}/tantra_linkedin_approval_workflow.json"

if [[ ! -f "$SRC_JSON" ]]; then
  echo "ERROR: workflow JSON not found at $SRC_JSON"
  exit 1
fi

echo "Source: $SRC_JSON"

# ── Build minimal workflow JSON ───────────────────────────────────────────────
# n8n v1 API rejects: id, createdAt, updatedAt, triggerCount, versionId,
# pinData, staticData, and tag objects (only tag name strings are accepted).
python3 - "$SRC_JSON" <<'PYEOF'
import json, sys

src = sys.argv[1]
with open(src) as f:
    wf = json.load(f)

# Strip top-level server-managed / unsupported fields
for key in ["id", "createdAt", "updatedAt", "triggerCount",
            "versionId", "pinData", "staticData"]:
    wf.pop(key, None)

# tags: API accepts only list of name strings
raw_tags = wf.get("tags", [])
wf["tags"] = [
    t["name"] if isinstance(t, dict) else t
    for t in raw_tags
]

# settings: only keep fields the API schema accepts
# callerPolicy, errorWorkflow (empty string) are UI-only and get rejected
ALLOWED_SETTINGS = {
    "executionOrder", "saveManualExecutions", "saveDataErrorExecution",
    "saveDataSuccessExecution", "saveExecutionProgress", "timezone",
}
settings = wf.get("settings", {})
wf["settings"] = {k: v for k, v in settings.items() if k in ALLOWED_SETTINGS}

out = "/tmp/wf_clean.json"
with open(out, "w") as f:
    json.dump(wf, f, indent=2)

print(f"Clean workflow written to {out}")
print("Top-level keys:", list(wf.keys()))
print("Tags:", wf["tags"])
PYEOF

echo ""
echo "── Importing workflow into n8n ──────────────────────────────────────────"
RESULT=$(curl -s -X POST "${N8N_URL}/api/v1/workflows" \
  -H "Content-Type: application/json" \
  -H "X-N8N-API-KEY: ${N8N_TOKEN}" \
  -d @/tmp/wf_clean.json)

echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"

# Check success
WF_ID=$(echo "$RESULT" | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || true)

if [[ -n "$WF_ID" ]]; then
  echo ""
  echo "✅ Workflow imported! ID: $WF_ID"
  echo ""
  echo "Activating workflow..."
  ACTIVATE=$(curl -s -X PATCH "${N8N_URL}/api/v1/workflows/${WF_ID}" \
    -H "Content-Type: application/json" \
    -H "X-N8N-API-KEY: ${N8N_TOKEN}" \
    -d '{"active": true}')
  ACTIVE=$(echo "$ACTIVATE" | python3 -c \
    "import json,sys; d=json.load(sys.stdin); print(d.get('active',''))" 2>/dev/null || true)
  if [[ "$ACTIVE" == "True" ]] || [[ "$ACTIVE" == "true" ]]; then
    echo "✅ Workflow activated! Open: ${N8N_URL}/workflow/${WF_ID}"
  else
    echo "⚠️  Activate manually: ${N8N_URL}/workflow/${WF_ID} → toggle Active"
    echo "   Or run:"
    echo "   curl -s -X PATCH ${N8N_URL}/api/v1/workflows/${WF_ID} \\"
    echo "     -H 'Content-Type: application/json' \\"
    echo "     -H \"X-N8N-API-KEY: \${N8N_TOKEN}\" \\"
    echo "     -d '{\"active\": true}'"
  fi
else
  echo ""
  echo "❌ Import may have failed — check the response above."
fi
