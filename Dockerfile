# Start from a Python base image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

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

# Copy requirements and install Python dependencies
# Copy first to leverage Docker layer caching
COPY --chown=omnipkg:omnipkg pyproject.toml poetry.lock* ./
RUN pip install .

# Copy the rest of the application code
COPY --chown=omnipkg:omnipkg . .

# Create directories for omnipkg data as the correct user
# Switched the order: chown the directory, then switch user, then create subdir
RUN chown -R omnipkg:omnipkg /home/omnipkg
USER omnipkg
RUN mkdir -p /home/omnipkg/.omnipkg

# Expose Redis port (in case external access needed)
EXPOSE 6379

# Expose the application port
EXPOSE 8000

# Specify the entrypoint script
ENTRYPOINT ["/home/omnipkg/docker-entrypoint.sh"]
