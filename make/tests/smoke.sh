#!/usr/bin/env bash
set -euo pipefail

api=http://127.0.0.1:8080

cleanup() {
    for group in "${agent:-}" "${server:-}"; do
        if [ -n "$group" ]; then kill -- "-$group" 2>/dev/null || true; fi
    done
    if [ -n "${logs:-}" ]; then kill "$logs" 2>/dev/null || true; fi
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

wait_for() {
    local seconds="$1"
    shift
    until "$@"; do
        seconds=$((seconds - 1))
        if [ "$seconds" -le 0 ]; then
            echo "timed out waiting for $*" >&2
            exit 1
        fi
        sleep 1
    done
}

claimed() {
    grep -q '"message":"claimed the credential"' "$TEMP_DIR/agent.log"
}

# Two renewals under one lease id: the agent keeps the lease it opened.
kept_lease() {
    local ids
    ids="$(grep -o '"lease_id":"[^"]*"' "$TEMP_DIR/agent.log" || true)"
    [ "$(printf '%s\n' "$ids" | grep -c .)" -ge 2 ] &&
        [ "$(printf '%s\n' "$ids" | sort -u | wc -l)" -eq 1 ]
}

mkdir -m 700 "$TEMP_DIR"
touch "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log"
tail -f "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log" &
logs=$!
echo "smoke test, working dir: $TEMP_DIR"

echo "starting api..."
master="$("$VENV/bin/python" -c "import secrets; print(secrets.token_hex(32), end='')")"
JATSZOTER_DATABASE_DSN="sqlite:///$TEMP_DIR/jatszoter.db" \
    JATSZOTER_MASTER_KEYS="{\"smoke\": \"$master\"}" \
    JATSZOTER_MASTER_KID=smoke \
    JATSZOTER_AUTO_REGISTER_AGENTS=true \
    setsid "$MAKE" -C "$ROOT" run-api >"$TEMP_DIR/api.log" 2>&1 &
server=$!
wait_for 30 curl -fs -o /dev/null "$api/healthz"

echo "starting agent..."
JATSZOTER_AGENT_API_URL="$api" \
    JATSZOTER_AGENT_NAME=alpha \
    JATSZOTER_AGENT_STATE_DIR="$TEMP_DIR/agent" \
    RUST_LOG=jatszoter_agent=debug \
    setsid cargo run -q -p jatszoter-agent >>"$TEMP_DIR/agent.log" 2>&1 &
agent=$!

wait_for 120 claimed
wait_for 60 kept_lease

echo "smoke test passed"
