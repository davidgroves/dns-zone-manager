#!/bin/bash
# clean-data.sh - Reset DNS zones and scheduled changes to their original state
#
# This script removes Docker volumes and resets all dynamic DNS updates.
# Zone files will be re-copied from ./bind/zones/ on next startup, and the
# PostgreSQL database is recreated empty (the API rebuilds its schema).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-examples}"

echo "=== DNS API Data Cleanup ==="
echo ""

# Always stop containers before volume deletion (safe even if nothing running)
echo "Stopping containers..."
docker compose down 2>/dev/null || true
echo ""

# Remove the data volumes, trying both possible names (with and without the
# project prefix)
for name in bind-zones postgres-data; do
    for vol in "${COMPOSE_PROJECT}_${name}" "$name" "examples_${name}"; do
        if docker volume inspect "$vol" >/dev/null 2>&1; then
            echo "Removing volume: $vol"
            docker volume rm "$vol"
            echo ""
        fi
    done
done

echo "=== Cleanup Complete ==="
echo ""
echo "Zone files will be reset from ./bind/zones/ on next startup."
echo "The PostgreSQL database will be recreated empty and the API will"
echo "rebuild its schema automatically."
echo ""
echo "To restart the environment:"
echo "  docker compose up"
echo "Or to restart and rebuild the environment:"
echo "  docker compose up --build"
echo ""
