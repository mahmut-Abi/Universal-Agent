#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_ROOT="${DEMO_ROOT:-$(mktemp -d)}"
DEMO_HOME="$DEMO_ROOT/home"
DEMO_WORKSPACE="$DEMO_ROOT/workspace"

mkdir -p "$DEMO_HOME" "$DEMO_WORKSPACE"
cd "$DEMO_WORKSPACE"

export HOME="$DEMO_HOME"
unset AGENT_CONFIG_DIR

run_agent() {
  uv run --project "$ROOT_DIR" ua "$@"
}

echo "Universal Agent local Golden Demo"
echo "Workspace: $DEMO_WORKSPACE"
echo "HOME: $DEMO_HOME"
echo

echo "# 1. init"
run_agent init

echo
echo "# 2. doctor"
run_agent doctor --fail-on never

echo
echo "# 3. run"
run_agent run "Hello" | tee run.out
SESSION_ID="$(awk '/^Session:/ {session=$2} END {print session}' run.out)"
if [[ -z "$SESSION_ID" ]]; then
  echo "Could not parse Session id from run output" >&2
  exit 1
fi

echo
echo "# 4. session list"
run_agent session list

echo
echo "# 5. session show"
run_agent session show "$SESSION_ID"

echo
echo "Demo complete. Session: $SESSION_ID"
