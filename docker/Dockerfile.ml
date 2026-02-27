FROM nvidia/cuda:12.6.3-runtime-ubuntu24.04

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMNIPKG_HOME=/home/omnipkg \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    curl git build-essential libmagic1 zstd xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

# Handle GID+UID 1000 already taken by ubuntu user in this base image
RUN (getent group 1000 && groupmod -n omnipkg $(getent group 1000 | cut -d: -f1)) || \
    groupadd --system --gid 1000 omnipkg && \
    (getent passwd 1000 && usermod -l omnipkg -d $OMNIPKG_HOME -m $(getent passwd 1000 | cut -d: -f1)) || \
    useradd --system --uid 1000 --gid omnipkg --create-home --home-dir $OMNIPKG_HOME omnipkg

# Create venv (PEP 668 - Ubuntu 24.04 blocks system pip)
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade "pip>=25.3"

WORKDIR $OMNIPKG_HOME

COPY --chown=omnipkg:omnipkg pyproject.toml README.md build_hooks.py ./
COPY --chown=omnipkg:omnipkg src/ ./src/

RUN /opt/venv/bin/pip install --no-cache-dir .

# Pre-bake multi-version ML packages via omnipkg
# This is the demo: conflicting versions coexisting, zero compromise
COPY --chown=omnipkg:omnipkg docker/requirements-ml.txt ./
RUN omnipkg install -r requirements-ml.txt -y

RUN mkdir -p $OMNIPKG_HOME/.omnipkg && \
    chown -R omnipkg:omnipkg $OMNIPKG_HOME

COPY --chown=omnipkg:omnipkg docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

USER omnipkg

LABEL org.opencontainers.image.source="https://github.com/1minds3t/omnipkg"
LABEL org.opencontainers.image.description="OmniPkg ML - torch 2.1/2.2/2.9 + tensorflow 2.13/2.20 coexisting. No compromises."
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["/bin/bash"]
