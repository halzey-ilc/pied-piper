#!/usr/bin/env bash
# Create .env from .env.example and fill the __GENERATE__ placeholders with
# strong random secrets. Existing .env is never overwritten. Secret values are
# never printed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -f .env ]; then
    echo ".env already exists; leaving it untouched." >&2
    exit 0
fi
cp .env.example .env

gen() { python3 -c 'import secrets; print(secrets.token_urlsafe(48))'; }

# Replace each __GENERATE__ with its own fresh secret, in place, without echoing.
while IFS= read -r key; do
    secret="$(gen)"
    # Use a Python helper to avoid sed escaping issues with secret characters.
    KEY="$key" SECRET="$secret" python3 - <<'PY'
import os, pathlib
key, secret = os.environ["KEY"], os.environ["SECRET"]
path = pathlib.Path(".env")
lines = path.read_text().splitlines()
out = [f"{key}={secret}" if line.startswith(f"{key}=") else line for line in lines]
path.write_text("\n".join(out) + "\n")
PY
done < <(grep -oE '^[A-Z_]+=__GENERATE__' .env | cut -d= -f1)

chmod 600 .env
echo "Wrote .env with generated secrets (values not shown). Review non-secret values before use."
