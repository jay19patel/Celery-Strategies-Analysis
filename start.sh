#!/usr/bin/env bash
# The dashboard on :3000. It proxies /api to the backend (BACKEND_URL, default http://127.0.0.1:8000).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
[ -d node_modules ] || npm install
exec npm run dev
