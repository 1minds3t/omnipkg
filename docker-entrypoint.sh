#!/bin/bash
set -e

# Check if Redis is available (optional)
if command -v redis-server >/dev/null 2>&1; then
    echo "Starting Redis server..."
    redis-server --daemonize yes --port 6379 --bind 127.0.0.1
    
    echo "Waiting for Redis to be ready..."
    until redis-cli ping > /dev/null 2>&1; do
        sleep 1
    done
    echo "Redis is ready!"
else
    echo "Redis not available - omnipkg will use SQLite fallback"
fi

# Execute the main command
exec "$@"
