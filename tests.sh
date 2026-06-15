#!/usr/bin/env bash
# Run all tests (backend and frontend)
#
# Usage:
#   ./tests.sh              Run unit tests only (backend + frontend)
#   ./tests.sh --all        Run all tests (unit + integration)
#   ./tests.sh --integration  Run only integration tests
#
# Integration tests require:
#   - Docker (for backend BIND container tests)
#   - Running dev server + backend (for frontend E2E Playwright tests)
#
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RUN_INTEGRATION=false
RUN_UNIT=true

for arg in "$@"; do
    case $arg in
        --all)
            RUN_INTEGRATION=true
            ;;
        --integration)
            RUN_INTEGRATION=true
            RUN_UNIT=false
            ;;
    esac
done

if $RUN_UNIT; then
    echo "=== Backend Unit Tests ==="
    uv run pytest tests/unit/ -v

    echo ""
    echo "=== Frontend Unit Tests ==="
    npm run test
fi

if $RUN_INTEGRATION; then
    echo ""
    echo "=== Backend Integration Tests (requires Docker) ==="
    uv run pytest tests/integration/ -v

    echo ""
    echo "=== Frontend E2E Tests (requires dev server + backend) ==="
    
    # Check if Vite dev server is running
    if ! curl -s --max-time 2 http://localhost:5173 > /dev/null 2>&1; then
        echo "ERROR: Vite dev server not running on http://localhost:5173"
        echo "Start it with VS Code task 'Start All (Devcontainer)' or: npm run dev"
        exit 1
    fi
    
    # Check if FastAPI backend is running
    if ! curl -s --max-time 2 http://localhost:8000/v1/zones > /dev/null 2>&1; then
        echo "ERROR: FastAPI backend not running on http://localhost:8000"
        echo "Start it with VS Code task 'Start All (Devcontainer)'"
        exit 1
    fi
    
    echo "Dev servers detected, running E2E tests..."
    npm run test:e2e
fi

echo ""
echo "All tests passed!"
