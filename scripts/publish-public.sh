#!/usr/bin/env bash
# publish-public.sh — build a scrubbed snapshot of this repo and push it to the
# GitHub remote. It NEVER publishes secrets, the AWS account id / infra
# identifiers, or the internal brain/ notes.
#
# Safe by construction: it exports tracked files from HEAD into a throwaway
# clone of the remote, strips the excluded paths, regenerates the env/.gitignore
# templates with placeholders, applies the .publish-redact map, runs a
# secret-guard scan, and only then commits + pushes. Your local repo and its
# history are never touched.
#
# Usage:
#   bash scripts/publish-public.sh              # build, scan, commit, push
#   bash scripts/publish-public.sh --dry-run    # build + scan only (no push)
#
# Redaction map: create a git-ignored `.publish-redact` file at the repo root
# with one entry per line, either:
#     literal                 -> replaced with REDACTED
#     literal||placeholder    -> replaced with placeholder
# Any listed literal (or a secret pattern) that survives the scrub ABORTS the
# push. Real values live only in this git-ignored file, never in the source.

set -euo pipefail

REMOTE_URL="https://github.com/Joetogo/bedrock-ac.git"
EXCLUDES=(brain)                           # paths (relative to root) never published
COMMIT_MSG="Publish scrubbed source snapshot"

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

REPO_ROOT="$(git rev-parse --show-toplevel)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "-> cloning remote"
git clone --quiet "$REMOTE_URL" "$WORK/repo"
REPO="$WORK/repo"

echo "-> exporting tracked files from HEAD"
git -C "$REPO_ROOT" archive HEAD | tar -x -C "$REPO"

echo "-> stripping internal paths: ${EXCLUDES[*]}"
for p in "${EXCLUDES[@]}"; do rm -rf "${REPO:?}/$p"; done

echo "-> regenerating placeholder templates"
cat > "$REPO/env.example" <<'EOF'
# Copy to .env and fill in. .env is git-ignored.
# Values come from the deploy_gateway.py output + the Cognito app client secret.

MCP_URL=https://<project>-gw-<id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
TOKEN_URL=https://<project>-<account_id>.auth.us-east-1.amazoncognito.com/oauth2/token
CLIENT_ID=REPLACE_CLIENT_ID
CLIENT_SECRET=REPLACE_CLIENT_SECRET

AWS_REGION=us-east-1
OAUTH_SCOPE=neat-graph-bedrock-api/invoke

# Optional. If omitted, the runner auto-picks a current Claude inference profile.
# MODEL_ID=us.anthropic.claude-sonnet-4-6-v1:0
EOF

cat > "$REPO/webapp/frontend/.env.local.example" <<'EOF'
NEXT_PUBLIC_API_BASE=https://REPLACE.execute-api.us-east-1.amazonaws.com
NEXT_PUBLIC_COGNITO_DOMAIN=https://neat-graph-bedrock-<account_id>.auth.us-east-1.amazoncognito.com
NEXT_PUBLIC_CLIENT_ID=REPLACE_APP_CLIENT_ID
NEXT_PUBLIC_REDIRECT_URI=http://localhost:3000/callback
EOF

cat > "$REPO/.gitignore" <<'EOF'
# --- Python ---
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.venv*/
build/

# --- AWS SAM build artifacts ---
.aws-sam/

# --- Secrets & local env (never commit real credentials) ---
# Template files (env.example, *.env.local.example) are intentionally tracked.
.env
.env.*
!.env.example
*.env.runtime
agent/.env.runtime

# --- AgentCore local deploy config (account id / role & runtime ARNs) ---
.bedrock_agentcore.yaml
.bedrock_agentcore/

# --- Node / Next.js (root-level safety net; webapp/frontend has its own) ---
node_modules/
.next/
out/
*.tsbuildinfo

# --- Editor / OS ---
.DS_Store
Thumbs.db
.idea/
.vscode/

# --- Tooling scratch ---
.superpowers/
.publish-redact
EOF

echo "-> placeholdering Makefile runtime ARN"
if [ -f "$REPO/Makefile" ]; then
  sed -i -E 's#(RUNTIME_ARN[[:space:]]*\?=[[:space:]]*).*#\1arn:aws:bedrock-agentcore:us-east-1:<account_id>:runtime/<runtime-id>#' "$REPO/Makefile"
fi

echo "-> applying redaction map + secret guard scan"
python - "$REPO" "$REPO_ROOT/.publish-redact" <<'PY'
import os, re, sys
root, redact_file = sys.argv[1], sys.argv[2]

# Parse .publish-redact: "literal" or "literal||placeholder" per line.
pairs = []
if os.path.exists(redact_file):
    with open(redact_file, encoding="utf-8", errors="ignore") as fh:
        for ln in fh:
            ln = ln.rstrip("\n").rstrip("\r")
            if not ln.strip():
                continue
            if "||" in ln:
                lit, repl = ln.split("||", 1)
            else:
                lit, repl = ln, "REDACTED"
            if lit:
                pairs.append((lit, repl))

patterns = [
    ("AWS access key id",       re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private key block",       re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("populated CLIENT_SECRET", re.compile(r"CLIENT_SECRET\s*=\s*(?!REPLACE|REDACTED)[^\s\"'<]{6,}")),
    ("token/api_key literal",   re.compile(r"(bearer_token|api_key)\"?\s*[:=]\s*\"[^\"]{12,}\"")),
]

changed, hits = 0, []
for dirpath, dirnames, filenames in os.walk(root):
    if ".git" in dirnames:
        dirnames.remove(".git")
    for name in filenames:
        fp = os.path.join(dirpath, name)
        try:
            with open(fp, encoding="utf-8", errors="ignore", newline="") as fh:
                text = fh.read()
        except Exception:
            continue
        orig = text
        for lit, repl in pairs:
            if lit in text:
                text = text.replace(lit, repl)
        if text != orig:
            with open(fp, "w", encoding="utf-8", errors="ignore", newline="") as fh:
                fh.write(text)
            changed += 1
        rel = os.path.relpath(fp, root)
        for label, rx in patterns:
            if rx.search(text):
                hits.append(f"{rel}: {label}")
        for lit, _ in pairs:
            if lit in text:
                hits.append(f"{rel}: unredacted literal")
if hits:
    print("ABORT - sensitive content still present after scrub:", file=sys.stderr)
    for h in sorted(set(hits)):
        print("   " + h, file=sys.stderr)
    sys.exit(1)
print(f"   redacted {changed} file(s); scan clean")
PY

cd "$REPO"
git add -A
if git diff --cached --quiet; then
  echo "-> no changes vs remote; nothing to publish."
  exit 0
fi

echo "-> staged changes:"
git diff --cached --stat | tail -30

if [ "$DRY_RUN" = "1" ]; then
  echo "-> --dry-run: built and scanned clean, not pushing."
  exit 0
fi

git commit --quiet -m "$COMMIT_MSG

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
git push origin HEAD:main
echo "-> published to $REMOTE_URL (main)"
