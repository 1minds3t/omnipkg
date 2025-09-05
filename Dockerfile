# Use Python 3.10 slim image as base
FROM python:3.10-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies including Redis
RUN apt-get update && apt-get install -y \
    redis-server \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user and group
RUN addgroup --system omnipkg && adduser --system --ingroup omnipkg omnipkg

# Set working directory
WORKDIR /home/omnipkg

# Copy requirements first (for better caching)
COPY --chown=omnipkg:omnipkg requirements.txt* ./

# Install omnipkg from PyPI
RUN pip install omnipkg

# Switch to non-root user
USER omnipkg

# Create directories for omnipkg data
RUN mkdir -p /home/omnipkg/.omnipkg

# Expose Redis port (in case external access needed)
EXPOSE 6379

# Copy startup script
COPY --chown=omnipkg:omnipkg docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD redis-cli ping || exit 1

# Default command
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["omnipkg", "--help"]
