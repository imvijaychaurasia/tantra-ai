#!/usr/bin/env bash
# Tantra AI — Import n8n approval workflow via API
# Usage: bash n8n/import_workflow.sh
# Requires: N8N_TOKEN env var (or set inline below)
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

# ── Build minimal workflow JSON ───────────────────────────────────────────────
# n8n v1 API rejects: id, createdAt, updatedAt, triggerCount, versionId,
# pinData, staticData, and tag objects (only tag name strings are accepted).
# Node-level 'notes' IS valid — keep it.

python3 - <<'PYEOF'
import json, os, sys

src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "tantra_linkedin_approval_workflow.json")
with open(src) as f:
    wf = json.load(f)

# Strip top-level server-managed / unsupported fields
for key in ["id", "createdAt", "updatedAt", "triggerCount",
            "versionId", "pinData", "staticData"]:
    wf.pop(key, None)

# tags: API accepts only list of name strings (it creates/links them)
raw_tags = wf.get("tags", [])
wf["tags"] = [
    t["name"] if isinstance(t, dict) else t
    for t in raw_tags
]

# settings: strip empty errorWorkflow (causes schema validation error in some versions)
settings = wf.get("settings", {})
if settings.get("errorWorkflow") == "":
    settings.pop("errorWorkflow")
wf["settings"] = settings

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
WF_ID=$(echo "$RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || true)
if [[ -n "$WF_ID" ]]; then
  echo ""
  echo "✅ Workflow imported! ID: $WF_ID"
  echo "   Activate it: curl -s -X PATCH ${N8N_URL}/api/v1/workflows/${WF_ID} \\"
  echo "     -H 'Content-Type: application/json' \\"
  echo "     -H 'X-N8N-API-KEY: \${N8N_TOKEN}' \\"
  echo "     -d '{\"active\": true}' | python3 -m json.tool"
else
  echo ""
  echo "❌ Import may have failed — check the response above."
fi
