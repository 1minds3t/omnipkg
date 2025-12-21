# Start from a Python base image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies
# Note: redis-server removed - omnipkg uses SQLite fallback when Redis unavailable
RUN apt-get update && apt-get install -y \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user and group
RUN addgroup --system omnipkg && \
    adduser --system --ingroup omnipkg --no-create-home omnipkg

# Set the working directory and create it with correct ownership
WORKDIR /home/omnipkg
RUN chown omnipkg:omnipkg /home/omnipkg

# Copy project files with ownership
COPY --chown=omnipkg:omnipkg pyproject.toml poetry.lock* ./
COPY --chown=omnipkg:omnipkg src/ ./src/
COPY --chown=omnipkg:omnipkg README.md ./

# Install Python dependencies AS ROOT (needed for pip)
RUN pip install --no-cache-dir .

# Copy the entrypoint script
COPY --chown=omnipkg:omnipkg docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Create data directory and set proper ownership — MUST be done as root before switching user
RUN mkdir -p /home/omnipkg/.omnipkg && \
    chown -R omnipkg:omnipkg /home/omnipkg

# NOW switch to non-root user
USER omnipkg

# Expose port (removed Redis port 6379 since we're not including Redis in container)
EXPOSE 8000

# Entry point
ENTRYPOINT ["/home/omnipkg/docker-entrypoint.sh"]
