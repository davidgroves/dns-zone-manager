#!/bin/bash
# clean-data.sh - Reset DNS zones to their original state
#
# This script removes Docker volumes and resets all dynamic DNS updates.
# Zone files will be re-copied from ./bind/zones/ on next startup.

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

# Remove the bind-zones volume
VOLUME_NAME="${COMPOSE_PROJECT}_bind-zones"

# Try both possible volume names (with and without project prefix)
for vol in "$VOLUME_NAME" "bind-zones" "examples_bind-zones"; do
    if docker volume inspect "$vol" >/dev/null 2>&1; then
        echo "Removing volume: $vol"
        docker volume rm "$vol"
        echo ""
    fi
done

echo "=== Cleanup Complete ==="
echo ""
echo "Zone files will be reset from ./bind/zones/ on next startup."
echo ""
echo "To restart the environment:"
echo "  docker compose up"
echo "Or to restart and rebuild the environment:"
echo "  docker compose up --build"
echo ""
