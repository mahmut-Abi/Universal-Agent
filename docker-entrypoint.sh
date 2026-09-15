#!/bin/sh
set -e

# Domain configuration via environment variables (UA-CS-008):
#   AGENT_DOMAIN_BACKEND: fake (default) | kubectl | kubernetes_api
#   AGENT_KUBERNETES_API_SERVER: kubernetes API server URL (for kubernetes_api)
#   AGENT_KUBERNETES_API_NAMESPACE: namespace (default: default)
#   AGENT_KUBERNETES_API_TOKEN_SECRET: secret name for the API token

INIT_ARGS=""
BACKEND="${AGENT_DOMAIN_BACKEND:-fake}"

if [ "$BACKEND" != "fake" ]; then
  INIT_ARGS="$INIT_ARGS --domain-backend $BACKEND"
  if [ -n "$AGENT_KUBERNETES_API_SERVER" ]; then
    INIT_ARGS="$INIT_ARGS --kubernetes-api-server $AGENT_KUBERNETES_API_SERVER"
  fi
  if [ -n "$AGENT_KUBERNETES_API_NAMESPACE" ]; then
    INIT_ARGS="$INIT_ARGS --kubernetes-api-namespace $AGENT_KUBERNETES_API_NAMESPACE"
  fi
  if [ -n "$AGENT_KUBERNETES_API_TOKEN_SECRET" ]; then
    INIT_ARGS="$INIT_ARGS --kubernetes-api-token-secret $AGENT_KUBERNETES_API_TOKEN_SECRET"
  fi
  if [ -n "$AGENT_KUBERNETES_API_TOKEN_ENV" ]; then
    INIT_ARGS="$INIT_ARGS --kubernetes-api-token-env $AGENT_KUBERNETES_API_TOKEN_ENV"
  fi
fi

echo "[entrypoint] domain backend: $BACKEND"
echo "[entrypoint] init args: $INIT_ARGS"

if [ ! -f /config/profile.json ]; then
  echo "[entrypoint] creating profile config..."
  agent init $INIT_ARGS
else
  echo "[entrypoint] using existing profile config"
fi

echo "[entrypoint] starting agentd..."
exec agent --profile-config /config/profile.json serve \
  --host 0.0.0.0 --port 8765 \
  --auth-token-env AGENTD_AUTH_TOKEN \
  --deployment-config /config/deployment.json
