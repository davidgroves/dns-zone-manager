#!/usr/bin/env bash
# Performance harness wrapper (opt-in — never run by CI or pre-commit).
#
# Usage:
#   ./perf.sh zone create [--preset 5m|1m|100k] [--records N] [--zone NAME]
#   ./perf.sh zone destroy [--zone NAME] [--delete-file]
#   ./perf.sh zone generate [--preset 100k] [--output PATH]
#   ./perf.sh run <scenario|all> [--preset 100k]
#   ./perf.sh report [path.json] [--compare A.json B.json]
#   ./perf.sh list
#
# Environment (same names as examples/always-changing/churn.sh):
#   API_BASE   default http://127.0.0.1:8000
#   API_KEY    optional
#   BIND_HOST  default bind
#   BIND_PORT  default 15353
#   TSIG_NAME / TSIG_SECRET / TSIG_ALG
#   RNDC / RNDC_CONF
#   PERF_APP_CONTAINER  for cold-start scenario
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export API_BASE="${API_BASE:-http://127.0.0.1:8000}"
export BIND_HOST="${BIND_HOST:-bind}"
export BIND_PORT="${BIND_PORT:-15353}"

exec uv run python -m perf "$@"
