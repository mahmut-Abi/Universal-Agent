# Universal Agent Web — Vue 3 dashboard (Vite build) + Node.js static/API
# proxy server. Multi-stage: build the SPA, then ship dist + server only.
FROM node:22-alpine AS build

WORKDIR /app

# Install dependencies first for layer caching
COPY package.json ./
RUN npm install --no-fund --no-audit

# Build the Vue SPA into ./dist
COPY index.html vite.config.js styles.css ./
COPY src ./src
RUN npm run build

FROM node:22-alpine

ARG IMAGE_VERSION=0.2.0
LABEL org.opencontainers.image.title="Universal Agent Web" \
    org.opencontainers.image.description="Vue dashboard + API proxy for agentd" \
    org.opencontainers.image.version="${IMAGE_VERSION}" \
    org.opencontainers.image.licenses="MIT"

ENV NODE_ENV=production \
    PORT=8080

WORKDIR /app

# Runtime: the zero-dep proxy/static server + built SPA. No node_modules
# needed at runtime (server.mjs uses only the Node standard library).
COPY server.mjs ./
COPY --from=build /app/dist ./dist

RUN addgroup -S -g 10001 web && adduser -S -u 10001 -G web web \
    && chown -R web:web /app
USER 10001:10001

EXPOSE 8080

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["wget", "-qO-", "http://127.0.0.1:8080/api/health"]

CMD ["node", "server.mjs"]
