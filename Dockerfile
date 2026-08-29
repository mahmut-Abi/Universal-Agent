FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

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

ENTRYPOINT ["agent"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8765"]
