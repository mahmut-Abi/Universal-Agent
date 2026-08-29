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
    AGENTD_HEALTH_URL=http://127.0.0.1:8765/ready

WORKDIR /app

RUN groupadd --system agent \
    && useradd --system --gid agent --home-dir /app --shell /usr/sbin/nologin agent

COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-dev --no-editable --compile-bytecode

RUN agent version >/tmp/agent-version.json \
    && agent health >/tmp/agent-health.json

RUN mkdir -p /data /config \
    && chown -R agent:agent /app /data /config

USER agent

EXPOSE 8765

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json, os, urllib.request; response = urllib.request.urlopen(os.environ.get('AGENTD_HEALTH_URL', 'http://127.0.0.1:8765/ready'), timeout=3); data = json.load(response); raise SystemExit(0 if data.get('ready') is True else 1)"

ENTRYPOINT ["agent"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8765"]
