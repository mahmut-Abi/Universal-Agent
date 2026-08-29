# Container Image

The repository ships a generic Universal Agent Runtime image. The image starts
`agent serve` by default and keeps domain selection/configuration outside the
image through CLI arguments, mounted Profile config files, and environment
variables.

## Build

```bash
docker build -t universal-agent-runtime:local .
```

For traceable builds, pass OCI metadata as build arguments:

```bash
docker build \
  --build-arg IMAGE_VERSION=0.1.0 \
  --build-arg VCS_REF="$(git rev-parse --short HEAD)" \
  --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t universal-agent-runtime:local .
```

The build installs runtime dependencies from `uv.lock` with `uv sync --locked`
and excludes development dependency groups. It also runs `agent version` and
`agent health` during the image build so packaging or console-script regressions
fail before the image is shipped. If dependencies change, refresh and commit the
lock file before building the image.

## Run the default runtime API

```bash
docker run --rm -p 8765:8765 universal-agent-runtime:local
```

The default command is equivalent to:

```bash
agent serve --host 0.0.0.0 --port 8765
```

The image creates `/data` and `/config`, exposes them through
`AGENT_DATA_DIR` and `AGENT_CONFIG_DIR`, and runs as the non-root `agent` user.
Mount persistent runtime state under `/data` and mount Profile or Domain package
configuration under `/config`.

Inside the image, `agent init` uses those environment variables for its default
paths. Running `agent init --force` writes `/config/profile.json`, and file or
SQLite-backed runtime stores default under `/data`.

The image includes a Docker health check against `GET /ready`. Override
`AGENTD_HEALTH_URL` if the container command binds a different internal port.

To smoke-test the packaged CLI without starting `agentd`:

```bash
docker run --rm --entrypoint agent universal-agent-runtime:local version
docker run --rm --entrypoint agent universal-agent-runtime:local health
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

When a Profile uses package-loaded Domain runtimes, include
`runtime.domain_package_paths` in the Profile config and mount those package
roots into the container. The image entrypoint remains `agent`; Domain code is
selected by the mounted config and package paths.

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
