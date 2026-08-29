# Container Image

The repository ships a generic Universal Agent Runtime image. The image starts
`agent serve` by default and keeps domain selection/configuration outside the
image through CLI arguments, mounted Profile config files, and environment
variables.

## Build

```bash
docker build -t universal-agent-runtime:local .
```

## Run the default runtime API

```bash
docker run --rm -p 8765:8765 universal-agent-runtime:local
```

The default command is equivalent to:

```bash
agent serve --host 0.0.0.0 --port 8765
```

## Run with a Profile config

Generate or provide a Profile JSON on the host, then mount it read-only and pass
it to the universal CLI:

```bash
docker run --rm \
  -p 8765:8765 \
  -v "$PWD/profile.json:/config/profile.json:ro" \
  -v "$PWD/.universal-agent:/data" \
  -e OPENAI_API_KEY \
  universal-agent-runtime:local \
  --profile-config /config/profile.json \
  serve --host 0.0.0.0 --port 8765
```

For a Kubernetes production slice, prefer the `kubernetes_api` backend with a
mounted service-account token or an environment-backed token reference in the
Profile config. The image intentionally does not bake Kubernetes-specific
behavior into the runtime entrypoint.

## Authenticated serving

```bash
docker run --rm \
  -p 8765:8765 \
  -e AGENTD_AUTH_TOKEN \
  universal-agent-runtime:local \
  serve --host 0.0.0.0 --port 8765 --auth-token-env AGENTD_AUTH_TOKEN
```

## Kubernetes deployment shape

Container args should make the selected Profile explicit:

```yaml
args:
  - --profile-config
  - /config/profile.json
  - serve
  - --host
  - 0.0.0.0
  - --port
  - "8765"
  - --auth-token-env
  - AGENTD_AUTH_TOKEN
```

Mount persistent state at a path referenced by the Profile config, for example
`/data/store`, and mount tokens as Kubernetes Secrets instead of embedding them
in the image.
