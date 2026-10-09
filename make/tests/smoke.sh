#!/usr/bin/env bash
set -euo pipefail

api=http://127.0.0.1:8080
www=http://127.0.0.1:8081

for tool in qemu-system-x86_64 qemu-img xorriso openssl jq curl sha256sum; do
    if ! command -v "$tool" >/dev/null; then
        echo "smoke test needs $tool on PATH" >&2
        exit 1
    fi
done
for firmware in /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_VARS_4M.fd; do
    if [ ! -r "$firmware" ]; then
        echo "smoke test needs OVMF: $firmware is missing" >&2
        exit 1
    fi
done

# QEMU daemonizes out of the agent's process group, so its pids are killed one by one.
cleanup() {
    if [ -n "${agent:-}" ]; then kill -- "-$agent" 2>/dev/null || true; fi
    for pidfile in "$TEMP_DIR"/agent/vms/*/qemu.pid; do
        local pid
        pid="$(cat "$pidfile" 2>/dev/null || true)"
        if [[ "$pid" =~ ^[0-9]+$ ]] && grep -qa qemu "/proc/$pid/cmdline" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
    for group in "${server:-}" "${files:-}"; do
        if [ -n "$group" ]; then kill -- "-$group" 2>/dev/null || true; fi
    done
    if [ -n "${logs:-}" ]; then kill "$logs" 2>/dev/null || true; fi
    if [ -n "${part:-}" ]; then rm -f "$part"; fi
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

# An operator request: hex HMAC-SHA256 of `ts\nMETHOD\npath\nsha256(body)` under the key.
signed() {
    local method="$1" path="$2" body="${3:-}" ts hash signature
    ts="$(date +%s)"
    hash="$(printf '%s' "$body" | sha256sum | cut -d' ' -f1)"
    signature="$(printf '%s\n%s\n%s\n%s' "$ts" "$method" "$path" "$hash" |
        openssl dgst -sha256 -hmac "$key" | awk '{print $NF}')"
    local args=(-fsS -X "$method"
        -H "X-Owner: $owner" -H "X-Credential: $credential"
        -H "X-Timestamp: $ts" -H "X-Signature: $signature")
    if [ -n "$body" ]; then
        args+=(-H "Content-Type: application/json" --data-binary "$body")
    fi
    curl "${args[@]}" "$api$path"
}

deployment_status() {
    signed GET "/api/v1/deployments/$deployment" | jq -r .status
}

vm_alive() {
    local pid
    pid="$(cat "$TEMP_DIR/agent/vms/$deployment/qemu.pid" 2>/dev/null)" &&
        kill -0 "$pid" 2>/dev/null
}

vm_ready() {
    local status
    status="$(deployment_status)" || return 1
    if [ "$status" = FAILED ]; then
        echo "the deployment failed: $(signed GET "/api/v1/deployments/$deployment" | jq -r .reason)" >&2
        exit 1
    fi
    [ "$status" = READY ]
}

vm_destroyed() {
    [ "$(deployment_status)" = COMPLETED ] &&
        ! kill -0 "$qemu" 2>/dev/null &&
        [ ! -e "$TEMP_DIR/agent/vms/$deployment" ]
}

# Attaches as the operator and reads until it holds an offset and console bytes.
viewer_py='
import hashlib, hmac, json, os, sys, time
from urllib.parse import urlsplit
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

url = os.environ["VIEWER_URL"]
ts = str(int(time.time()))
message = "\n".join([ts, "GET", urlsplit(url).path, hashlib.sha256(b"").hexdigest()])
key = os.environ["VIEWER_KEY"].encode()
headers = {
    "X-Owner": os.environ["VIEWER_OWNER"],
    "X-Credential": os.environ["VIEWER_CREDENTIAL"],
    "X-Timestamp": ts,
    "X-Signature": hmac.new(key, message.encode(), hashlib.sha256).hexdigest(),
}
deadline = time.monotonic() + 30
offset, received = None, 0
try:
    with connect(url, additional_headers=headers, open_timeout=30) as socket:
        while offset is None or not received:
            frame = socket.recv(timeout=max(deadline - time.monotonic(), 0))
            if isinstance(frame, str):
                offset = json.loads(frame).get("offset", offset)
            else:
                received += len(frame)
except TimeoutError:
    sys.exit(f"the viewer timed out with offset {offset} and {received} bytes")
except (OSError, WebSocketException) as err:
    sys.exit(f"the viewer was refused or dropped: {type(err).__name__}")
print(f"the viewer got offset {offset} and {received} bytes")
'

# The key reaches the viewer through its environment, never a command line.
viewer() {
    VIEWER_URL="ws://${api#http://}/api/v1/deployments/$deployment/attach" \
        VIEWER_OWNER="$owner" VIEWER_CREDENTIAL="$credential" VIEWER_KEY="$key" \
        "$VENV/bin/python" -I -c "$viewer_py"
}

cached_sha256() {
    sha256sum "$1" | cut -d' ' -f1
}

image="$SMOKE_CACHE/${SMOKE_IMAGE_URL##*/}"
if [ ! -f "$image" ] || [ "$(cached_sha256 "$image")" != "$SMOKE_IMAGE_SHA256" ]; then
    echo "downloading $SMOKE_IMAGE_URL..."
    mkdir -p "$SMOKE_CACHE"
    part="$image.part"
    curl -fL --retry 3 -o "$part" "$SMOKE_IMAGE_URL"
    if [ "$(cached_sha256 "$part")" != "$SMOKE_IMAGE_SHA256" ]; then
        echo "the downloaded image does not match SMOKE_IMAGE_SHA256" >&2
        exit 1
    fi
    mv "$part" "$image"
    part=
fi

mkdir -m 700 "$TEMP_DIR"
touch "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log"
tail -f "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log" &
logs=$!
echo "smoke test, working dir: $TEMP_DIR"

echo "serving the base image and the scenario bundle..."
mkdir "$TEMP_DIR/www"
ln -s "$image" "$TEMP_DIR/www/rocky-9.qcow2"
tar -czf "$TEMP_DIR/www/nginx-502.tar.gz" -C "$ROOT/scenarios" nginx-502
setsid "$VENV/bin/python" -m http.server --bind 127.0.0.1 8081 \
    --directory "$TEMP_DIR/www" >"$TEMP_DIR/www.log" 2>&1 &
files=$!
wait_for 30 curl -fs -o /dev/null "$www/nginx-502.tar.gz"

# The API's local file store is data/storage under its working directory.
echo "starting api..."
master="$("$VENV/bin/python" -c "import secrets; print(secrets.token_hex(32), end='')")"
(
    cd "$TEMP_DIR"
    JATSZOTER_DATABASE_DSN="sqlite:///$TEMP_DIR/jatszoter.db" \
        JATSZOTER_MASTER_KEYS="{\"smoke\": \"$master\"}" \
        JATSZOTER_MASTER_KID=smoke \
        JATSZOTER_AUTO_REGISTER_AGENTS=true \
        exec setsid "$MAKE" -f "$ROOT/Makefile" VENV="$VENV" run-api >"$TEMP_DIR/api.log" 2>&1
) &
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

echo "logging in as the operator..."
refused="$(jq -cn '{name: "admin", password: "admin"}' |
    curl -s -o /dev/null -w '%{http_code}' -H "Content-Type: application/json" \
        --data-binary @- "$api/api/v1/auth/login")"
if [ "$refused" != 403 ]; then
    echo "a login without a password change answered $refused, not 403" >&2
    exit 1
fi
password="$("$VENV/bin/python" -c "import secrets; print(secrets.token_urlsafe(24), end='')")"
login="$(jq -cn --arg password "$password" \
    '{name: "admin", password: "admin", new_password: $password}' |
    curl -fsS -H "Content-Type: application/json" --data-binary @- "$api/api/v1/auth/login")"
owner="$(jq -r .user_id <<<"$login")"
credential="$(jq -r .credential.credential <<<"$login")"
key="$(jq -r .credential.key <<<"$login")"
unset login password

echo "registering the image and the scenario..."
signed POST /api/v1/images \
    "$(jq -cn --arg url "$www/rocky-9.qcow2" --arg digest "$SMOKE_IMAGE_SHA256" \
        '{name: "rocky-9", url: $url, digest: $digest}')" >/dev/null
scenario="$(signed POST /api/v1/scenarios "$(jq -cn --arg url "$www/nginx-502.tar.gz" '{url: $url}')")"
bundle="$TEMP_DIR/data/storage/scenarios/nginx-502/$(jq -r .version <<<"$scenario").tar.gz"
if [ ! -f "$bundle" ]; then
    echo "the scenario bundle is not at $bundle" >&2
    exit 1
fi

if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
    accel=KVM
else
    accel="TCG, no usable /dev/kvm"
fi
echo "deploying the scenario under $accel; the boot takes minutes..."
trainee="$(signed POST /api/v1/users "$(jq -cn '{name: "beta", role: "trainee"}')" | jq -r .id)"
created="$(signed POST /api/v1/deployments \
    "$(jq -cn --arg id "$(jq -r .id <<<"$scenario")" --arg trainee "$trainee" \
        '{scenario_id: $id, trainee_id: $trainee}')")"
deployment="$(jq -r .id <<<"$created")"
wait_for "$(($(jq -r .bootstrap_seconds <<<"$created") + 120))" vm_ready
if ! vm_alive; then
    echo "the VM is not running at READY" >&2
    exit 1
fi
if [ -e "$TEMP_DIR/agent/vms/$deployment/seed.iso" ]; then
    echo "seed.iso is still in the VM directory at READY" >&2
    exit 1
fi
qemu="$(cat "$TEMP_DIR/agent/vms/$deployment/qemu.pid")"

echo "attaching to the console as the operator..."
viewer
signed GET "/api/v1/deployments/$deployment/console" >"$TEMP_DIR/console.download"
if [ ! -s "$TEMP_DIR/console.download" ]; then
    echo "the downloaded console log is empty" >&2
    exit 1
fi
if ! grep -qa 'Rocky Linux' "$TEMP_DIR/console.download"; then
    echo "the downloaded console log does not hold the boot" >&2
    exit 1
fi
if [ ! -s "$TEMP_DIR/agent/vms/$deployment/console.log" ]; then
    echo "console.log is missing or empty in the VM directory" >&2
    exit 1
fi

echo "stopping the deployment..."
signed POST "/api/v1/deployments/$deployment/stop" >/dev/null
wait_for 60 vm_destroyed

echo "smoke test passed"
