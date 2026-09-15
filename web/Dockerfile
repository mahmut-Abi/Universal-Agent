# Universal Agent Web — standalone Node.js chat UI.
# Zero npm dependencies: only the Node standard library is used, so no
# npm install/build step is required.
FROM node:22-alpine

ARG IMAGE_VERSION=0.1.0
LABEL org.opencontainers.image.title="Universal Agent Web" \
    org.opencontainers.image.description="Standalone multi-session chat web UI for agentd" \
    org.opencontainers.image.version="${IMAGE_VERSION}" \
    org.opencontainers.image.licenses="MIT"

ENV NODE_ENV=production \
    PORT=8080

WORKDIR /app

COPY server.mjs ./
COPY public ./public

RUN addgroup -S -g 10001 web && adduser -S -u 10001 -G web web \
    && chown -R web:web /app
USER 10001:10001

EXPOSE 8080

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["wget", "-qO-", "http://127.0.0.1:8080/api/health"]

CMD ["node", "server.mjs"]
