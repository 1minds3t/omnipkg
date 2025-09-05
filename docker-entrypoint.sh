#!/bin/bash
set -e

# Start Redis server in the background
echo "Starting Redis server..."
redis-server --daemonize yes --port 6379 --bind 127.0.0.1

# Wait for Redis to be ready
echo "Waiting for Redis to be ready..."
until redis-cli ping > /dev/null 2>&1; do
    sleep 1
done
echo "Redis is ready!"

# Execute the main command
exec "$@"
