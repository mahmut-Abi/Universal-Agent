FROM ghcr.io/astral-sh/uv:0.9.10 AS uv
FROM python:3.12-slim AS runtime

ARG IMAGE_VERSION=0.1.0
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Universal Agent Runtime" \
    org.opencontainers.image.description="Generic Universal Agent Runtime agentd image" \
    org.opencontainers.image.version="${IMAGE_VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    AGENT_DATA_DIR=/data \
    AGENT_CONFIG_DIR=/config \
    AGENTD_HEALTH_URL=http://127.0.0.1:8765/health

WORKDIR /app

RUN groupadd --system agent \
    && useradd --system --gid agent --home-dir /app --shell /usr/sbin/nologin agent

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && arch="$(dpkg --print-architecture)" \
    && case "$arch" in amd64|arm64) ;; *) echo "unsupported architecture: $arch" >&2; exit 1 ;; esac \
    && kubectl_version="$(curl -fsSL https://dl.k8s.io/release/stable.txt)" \
    && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${kubectl_version}/bin/linux/${arch}/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && kubectl version --client=true >/tmp/kubectl-version.txt \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-dev --no-editable --all-extras --compile-bytecode

RUN agent version >/tmp/agent-version.json \
    && agent health >/tmp/agent-health.json

RUN mkdir -p /data /config \
    && chown -R agent:agent /app /data /config

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER agent

EXPOSE 8765

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json, os, urllib.request; response = urllib.request.urlopen(os.environ.get('AGENTD_HEALTH_URL', 'http://127.0.0.1:8765/health'), timeout=3); data = json.load(response); raise SystemExit(0 if data.get('status') == 'ok' else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# First boot: the entrypoint script generates /config/profile.json via agent
# init, honoring AGENT_DOMAIN_BACKEND (fake|kubectl|kubernetes_api|workspace)
# and the domain-specific environment variables documented in the script.
# Persistence is selected with AGENTD_STORE_BACKEND (memory|file|sqlite;
# postgres via the postgres profile + AGENTD_PG_URL) and AGENTD_STORE_PATH;
# the generated profile is written only once, edit /config/profile.json to
# change it later.
