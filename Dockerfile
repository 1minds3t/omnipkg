# Start from a Python base image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    redis-server \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user and group
RUN addgroup --system omnipkg && adduser --system --ingroup omnipkg omnipkg

# Set the working directory
WORKDIR /home/omnipkg

# Copy project files (including src/) BEFORE installing
COPY --chown=omnipkg:omnipkg pyproject.toml poetry.lock* ./
COPY --chown=omnipkg:omnipkg src/ ./src/
COPY --chown=omnipkg:omnipkg README.md ./

# Install Python dependencies
RUN pip install --no-cache-dir .

# Copy the rest of the application code (entrypoint script, etc.)
COPY --chown=omnipkg:omnipkg docker-entrypoint.sh ./
RUN chmod +x /home/omnipkg/docker-entrypoint.sh

# Switch to non-root user
USER omnipkg

# Create directories for omnipkg data
RUN mkdir -p /home/omnipkg/.omnipkg

# Expose Redis port (in case external access needed)
EXPOSE 6379

# Expose the application port
EXPOSE 8000

# Specify the entrypoint script
ENTRYPOINT ["/home/omnipkg/docker-entrypoint.sh"]
