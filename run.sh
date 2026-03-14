#!/bin/bash
# Development server with hot reload
# Usage: ./run.sh [--docker]

set -e
cd "$(dirname "$0")"

if [[ "$1" == "--docker" ]]; then
    FLAGS=""
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rebuild) FLAGS="$FLAGS --build" ;;
            -d) FLAGS="$FLAGS -d" ;;
        esac
        shift
    done
    docker compose -f docker-compose.dev.yaml up $FLAGS
else
    # Local development mode
    set -a
    source .env
    set +a

    if [ -d ".venv" ]; then
        source .venv/bin/activate
    fi

    uvicorn app.main:app --host 0.0.0.0 --port ${RELAY_PORT:-7735} --reload
fi
