#!/usr/bin/env bash
set -euo pipefail

api=http://127.0.0.1:8080
web=http://127.0.0.1:8000
www=http://127.0.0.1:8081
state="$ROOT/dev/stack/local"
images="${XDG_CACHE_HOME:-$HOME/.cache}/jatszoter/smoke"
parts=(agent web api files)

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

secret() {
    "$VENV/bin/python" -c "import secrets; print(secrets.$1, end='')"
}

# Each part runs in its own process group, so `down` stops it with its children.
start() {
    local name="$1"
    shift
    setsid "$@" </dev/null >"$state/$name.log" 2>&1 &
    echo $! >"$state/$name.pid"
}

up() {
    if [ -e "$state" ]; then
        echo "the stand is up, or was not brought down: run down first" >&2
        exit 1
    fi
    for url in "$api" "$web" "$www"; do
        if curl -s -o /dev/null "$url"; then
            echo "something already answers on $url" >&2
            exit 1
        fi
    done
    mkdir -m 700 "$state"
    trap 'echo "the stand did not come up, bringing it down" >&2; down' EXIT

    mkdir "$state/www"
    for image in "$images"/*.qcow2; do
        if [ -f "$image" ]; then ln -s "$image" "$state/www/"; fi
    done
    for scenario in "$ROOT"/scenarios/*/; do
        scenario="$(basename "$scenario")"
        tar -czf "$state/www/$scenario.tar.gz" -C "$ROOT/scenarios" "$scenario"
    done
    start files "$VENV/bin/python" -m http.server --bind 127.0.0.1 8081 --directory "$state/www"

    # The API's local file store is data/storage under its working directory.
    JATSZOTER_DATABASE_DSN="sqlite:///$state/jatszoter.db" \
        JATSZOTER_MASTER_KEYS="{\"dev\": \"$(secret 'token_hex(32)')\"}" \
        JATSZOTER_MASTER_KID=dev \
        JATSZOTER_AUTO_REGISTER_AGENTS=true \
        start api "$MAKE" -C "$state" -f "$ROOT/Makefile" VENV="$VENV" run-api

    JATSZOTER_WEB_API_URL="$api" \
        JATSZOTER_WEB_COOKIE_KEYS="{\"dev\": \"$(secret 'token_urlsafe(32)')\"}" \
        JATSZOTER_WEB_COOKIE_KID=dev \
        start web "$MAKE" -f "$ROOT/Makefile" VENV="$VENV" run-web

    JATSZOTER_AGENT_API_URL="$api" \
        JATSZOTER_AGENT_NAME=alpha \
        JATSZOTER_AGENT_STATE_DIR="$state/agent" \
        RUST_LOG=jatszoter_agent=info \
        start agent cargo run -q --manifest-path "$ROOT/Cargo.toml" -p jatszoter-agent

    wait_for 30 curl -fs -o /dev/null "$www/"
    wait_for 30 curl -fs -o /dev/null "$api/healthz"
    wait_for 30 curl -fs -o /dev/null "$web/healthz"
    trap - EXIT

    echo "the stand is up: http://localhost:8000/login, admin / admin, a new password is asked at once"
    echo "the agent alpha builds and registers on its own; logs: make stand-logs, stop: make stand-down"
    echo "images, by URL and sha256:"
    local found=
    for image in "$state"/www/*.qcow2; do
        if [ -e "$image" ]; then
            echo "  $www/${image##*/} $(sha256sum "$image" | cut -d' ' -f1)"
            found=1
        fi
    done
    if [ -z "$found" ]; then echo "  none in $images: make smoke fetches one"; fi
    echo "scenario bundles:"
    for bundle in "$state"/www/*.tar.gz; do echo "  $www/${bundle##*/}"; done
}

# QEMU daemonizes out of the agent's process group, so its pids are killed one by one.
down() {
    if [ ! -d "$state" ]; then
        echo "the stand is not up"
        return
    fi
    local pid
    for part in "${parts[@]}"; do
        pid="$(cat "$state/$part.pid" 2>/dev/null || true)"
        if [[ "$pid" =~ ^[0-9]+$ ]]; then kill -- "-$pid" 2>/dev/null || true; fi
    done
    for pidfile in "$state"/agent/vms/*/qemu.pid; do
        pid="$(cat "$pidfile" 2>/dev/null || true)"
        if [[ "$pid" =~ ^[0-9]+$ ]] && grep -qa qemu "/proc/$pid/cmdline" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
    rm -rf "$state"
    echo "the stand is down"
}

logs() {
    if [ ! -d "$state" ]; then
        echo "the stand is not up" >&2
        exit 1
    fi
    tail -n 50 -f "$state"/*.log
}

case "${1:-}" in
up | down | logs) "$1" ;;
*)
    echo "usage: stand.sh up|down|logs" >&2
    exit 2
    ;;
esac
