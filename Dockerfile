FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENTD_HEALTH_URL=http://127.0.0.1:8765/ready

WORKDIR /app

RUN groupadd --system agent \
    && useradd --system --gid agent --home-dir /app --shell /usr/sbin/nologin agent

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

RUN mkdir -p /data \
    && chown -R agent:agent /app /data

USER agent

EXPOSE 8765

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json, os, urllib.request; response = urllib.request.urlopen(os.environ.get('AGENTD_HEALTH_URL', 'http://127.0.0.1:8765/ready'), timeout=3); data = json.load(response); raise SystemExit(0 if data.get('ready') is True else 1)"

ENTRYPOINT ["agent"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8765"]
