#!/usr/bin/env bash
set -euo pipefail

api=http://127.0.0.1:8080
www=http://127.0.0.1:8081
web=http://127.0.0.1:8000

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
for url in "$api" "$web" "$www"; do
    if curl -s -o /dev/null "$url"; then
        echo "something already answers on $url" >&2
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
    for group in "${front:-}" "${server:-}" "${files:-}"; do
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
        args+=(-H "Content-Type: application/json" --data-binary @-)
    fi
    printf '%s' "$body" | curl "${args[@]}" "$api$path"
}

# A request to web as the operator who logged in there; cookies stay in the jar.
browsed() {
    curl -sS -b "$TEMP_DIR/operator.jar" -c "$TEMP_DIR/operator.jar" "$@"
}

jarred() {
    awk -v name="$1" '$6 == name {print $7}' "$TEMP_DIR/operator.jar"
}

# A form a page posts back with its CSRF token; the fields reach curl on stdin.
posted() {
    local path="$1" fields="$2"
    shift 2
    printf 'csrf_token=%s&%s' "$(jarred jatszoter_csrf)" "$fields" |
        browsed --data-binary @- "$@" "$web$path"
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

# The token reaches curl on stdin; the redirect is not followed.
open_link() {
    printf 'url = "%s"\n' "$web/s/$token" | curl -sS -K - -o /dev/null "$@" | tr -d '\r'
}

header() {
    sed -n "s/^$1: //Ip" <<<"$2"
}

# The cookies and the CSRF token reach curl on stdin; `bare` leaves the token out.
linked() {
    local sent="X-CSRF-Token: $csrf"
    if [ "$1" = bare ]; then
        sent="X-CSRF-Token:"
        shift
    fi
    printf 'request = "%s"\nurl = "%s"\nheader = "Cookie: jatszoter_link=%s; jatszoter_csrf=%s"\nheader = "%s"\n' \
        "$1" "$web$2" "$cookie" "$csrf" "$sent" | curl -sS -K - "${@:3}"
}

# Asks as `linked` or `signed` and prints the status the check settles with, nothing else.
check() {
    local ask="$1" path="/api/v1/deployments/$deployment/checks" id again status
    local left=$((check_seconds + 60))
    id="$("$ask" POST "$path" | jq -er .id)" || {
        echo "the check asked as $ask was not accepted" >&2
        exit 1
    }
    if [ "${2:-}" = twice ]; then
        again="$(linked POST "$path" -o /dev/null -w '%{http_code}')"
        if [ "$again" != 409 ]; then
            echo "a second check while one is pending answered $again, not 409" >&2
            exit 1
        fi
    fi
    while :; do
        "$ask" GET "$path/$id" >"$TEMP_DIR/check.json"
        status="$(jq -er .status "$TEMP_DIR/check.json")" || {
            echo "the check asked as $ask could not be read" >&2
            exit 1
        }
        if [ "$status" != pending ]; then break; fi
        left=$((left - 1))
        if [ "$left" -le 0 ]; then
            echo "the check is still pending after $((check_seconds + 60)) seconds" >&2
            exit 1
        fi
        sleep 1
    done
    printf '%s\n' "$status"
}

# Fails by name unless the last check read has the status and the message.
settled() {
    local message
    message="$(jq -r .message "$TEMP_DIR/check.json")"
    if [ "$2" != "$3" ] || [ "$message" != "$4" ]; then
        echo "the check $1 is $2 with the message '$message', not $3 with '$4'" >&2
        exit 1
    fi
}

# Through web: the trainee socket by the link cookie, the operator's by the session; prints check names only.
# shellcheck disable=SC2016
terminal_py='
import json, os, sys, time
from urllib.request import Request, urlopen
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

web = os.environ["TERMINAL_WEB"]
deployment = os.environ["TERMINAL_DEPLOYMENT"]
session = {"Cookie": "jatszoter_session=" + os.environ["TERMINAL_SESSION"]}


class Terminal:
    def __init__(self, socket):
        self.socket = socket
        self.offset, self.control, self.seen = None, None, bytearray()

    def until(self, done, seconds=30):
        deadline = time.monotonic() + seconds
        while not done():
            try:
                frame = self.socket.recv(timeout=max(deadline - time.monotonic(), 0))
            except TimeoutError:
                return False
            if isinstance(frame, str):
                told = json.loads(frame)
                self.offset = told.get("offset", self.offset)
                self.control = told.get("control", self.control)
            else:
                self.seen += frame
        return True

    def held_by(self, holder, seconds=30):
        return self.until(lambda: self.offset is not None and self.control == holder, seconds)

    def prints(self, line, text, seconds=30):
        self.seen.clear()
        self.socket.send(line.encode() + b"\r")
        return self.until(lambda: text.encode() in self.seen, seconds)

    # The echo shows the arithmetic as typed; only the guest expands it.
    def runs(self, mark, seconds=30, before=""):
        return self.prints(f"{before}printf {mark}-%d $((40 + 2))", f"{mark}-42", seconds)


# The form of the detail page; its 303 back to the page is followed.
def take_control():
    headers = {**session, "X-CSRF-Token": os.environ["TERMINAL_CSRF"]}
    url = f"http://{web}/deployments/{deployment}/control"
    request = Request(url, data=b"holder=operator", headers=headers)
    with urlopen(request, timeout=30) as response:
        response.read()
    return trainee.held_by("operator") and operator.held_by("operator")


def resize():
    operator.socket.send(json.dumps({"cols": 100, "rows": 30}))
    return operator.prints("stty size", "30 100")


def fixes(mark, command):
    return operator.runs(mark, 60, f"sudo {command} && ")


# A 502 is retried, so the page is waited for while the app unit comes up.
def serves():
    line = "curl -fsS --retry 10 --retry-delay 1 http://127.0.0.1/"
    return operator.prints(line, "hello from app", 60)


unit = "sed -i s/8018/8081/ /etc/systemd/system/app.service"
parts = {
    "handover": [
        ("the trainee attaches holding control", lambda: trainee.held_by("trainee")),
        ("the trainee types into the guest", lambda: trainee.runs("one")),
        ("the operator takes control", take_control),
        ("the keys of the trainee are cut", lambda: not trainee.runs("two", 5)),
        ("the operator types into the guest", lambda: operator.runs("three")),
        ("a resize reaches the guest", resize),
    ],
    "fix": [
        ("the operator still holds control", lambda: operator.held_by("operator")),
        ("the app unit goes back to port 8081", lambda: fixes("unit", unit)),
        ("systemd reloads its units", lambda: fixes("reload", "systemctl daemon-reload")),
        ("the app unit restarts", lambda: fixes("restart", "systemctl restart app.service")),
        ("nginx serves the app page", serves),
    ],
}
checks = parts[os.environ["TERMINAL_PART"]]
def attach(path, headers, origin=None):
    url = f"ws://{web}{path}"
    return connect(url, additional_headers=headers, origin=origin, open_timeout=30)


name = "opening the sockets"
cookie = {"Cookie": "jatszoter_link=" + os.environ["TERMINAL_COOKIE"]}
relayed = f"/api/v1/deployments/{deployment}/attach"
own = f"/deployments/{deployment}/attach"
try:
    with (
        attach(relayed, cookie) as first,
        attach(own, session, f"http://{web}") as second,
    ):
        trainee, operator = Terminal(first), Terminal(second)
        for name, check in checks:
            if not check():
                counts = f"{len(trainee.seen)} trainee and {len(operator.seen)} operator bytes"
                sys.exit(f"the terminal failed at: {name}, with {counts} since each last line")
            print(f"the terminal passed: {name}", flush=True)
except (OSError, WebSocketException) as err:
    sys.exit(f"the terminal was refused or dropped at: {name}: {type(err).__name__}")
'

# The cookies and the token reach the terminal through its environment, never a command line.
terminal() {
    TERMINAL_PART="$1" TERMINAL_WEB="${web#http://}" TERMINAL_DEPLOYMENT="$deployment" \
        TERMINAL_COOKIE="$cookie" TERMINAL_SESSION="$(jarred jatszoter_session)" \
        TERMINAL_CSRF="$operator_csrf" \
        "$VENV/bin/python" -I -c "$terminal_py"
}

# Reads the recording as a player would; prints check names only, never a line of it.
replay_py='
import json, os, sys

first, cut, second = (os.environ[f"REPLAY_{name}"] for name in ("FIRST", "CUT", "SECOND"))
with open(os.environ["REPLAY_FILE"], encoding="utf-8") as file:
    lines = file.read().splitlines()
events = []


def header():
    told = json.loads(lines[0])
    sized = all(type(told.get(side)) is int for side in ("width", "height"))
    return told.get("version") == 2 and sized


def well_formed():
    last = 0
    for line in lines[1:]:
        event = json.loads(line)
        if not isinstance(event, list) or len(event) != 3:
            return False
        at, code, text = event
        if type(at) not in (int, float) or code not in ("o", "r", "m"):
            return False
        if not isinstance(text, str) or at < last:
            return False
        last = at
        events.append((code, text))
    return True


# The event with which the output first holds the text; None when it never does.
def reached(text):
    shown = ""
    for index, (code, part) in enumerate(events):
        if code == "o":
            shown += part
            if text in shown:
                return index
    return None


def typed():
    return None not in (reached(first), reached(second)) and reached(cut) is None


def marked():
    marks = [index for index, event in enumerate(events) if event == ("m", "control operator")]
    return any(reached(first) < index < reached(second) for index in marks)


# The first marker starting with the text; None when there is none.
def mark(text):
    marks = (index for index, (code, part) in enumerate(events) if code == "m")
    return next((index for index in marks if events[index][1].startswith(text)), None)


def checked():
    order = [mark(text) for text in ("control operator", "check failed", "check passed")]
    return None not in order and order == sorted(order)


checks = [
    ("the first line is an asciicast header", header),
    ("every event is well formed and in order", well_formed),
    ("the output holds what both typed and not the cut line", typed),
    ("the control change sits between the two", marked),
    ("the resize is an event", lambda: ("r", "100x30") in events),
    ("a failed check comes before a passed one, after the control change", checked),
]
for name, check in checks:
    try:
        passed = bool(lines) and check()
    except ValueError:
        passed = False
    if not passed:
        sys.exit(f"the replay failed at: {name}, with {len(lines)} lines")
    print(f"the replay passed: {name}", flush=True)
'

# The texts are what the guest prints for the lines `terminal` types.
replay() {
    REPLAY_FILE="$1" REPLAY_FIRST=one-42 REPLAY_CUT=two-42 REPLAY_SECOND=three-42 \
        "$VENV/bin/python" -I -c "$replay_py"
}

recordings() {
    signed GET "/api/v1/deployments/$deployment/recordings"
}

new_password() {
    "$VENV/bin/python" -c "import secrets; print(secrets.token_urlsafe(24), end='')"
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
touch "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log" "$TEMP_DIR/web.log"
tail -f "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log" "$TEMP_DIR/web.log" &
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

echo "starting web..."
sealing="$("$VENV/bin/python" -c "import secrets; print(secrets.token_urlsafe(32), end='')")"
JATSZOTER_WEB_API_URL="$api" \
    JATSZOTER_WEB_COOKIE_KEYS="{\"smoke\": \"$sealing\"}" \
    JATSZOTER_WEB_COOKIE_KID=smoke \
    setsid "$MAKE" -f "$ROOT/Makefile" VENV="$VENV" run-web >"$TEMP_DIR/web.log" 2>&1 &
front=$!
unset sealing
wait_for 30 curl -fs -o /dev/null "$web/healthz"

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
login="$(PASSWORD="$(new_password)" jq -cn \
    '{name: "admin", password: "admin", new_password: env.PASSWORD}' |
    curl -fsS -H "Content-Type: application/json" --data-binary @- "$api/api/v1/auth/login")"
owner="$(jq -r .user_id <<<"$login")"
credential="$(jq -r .credential.credential <<<"$login")"
key="$(jq -r .credential.key <<<"$login")"
unset login

# web logs in as an operator of its own: a second login of admin would supersede the one above.
echo "creating the operator who logs in through web..."
gamma="$(signed POST /api/v1/users "$(jq -cn '{name: "gamma", role: "operator"}')" | jq -r .id)"
password="$(new_password)"
signed POST "/api/v1/users/$gamma/password" \
    "$(PASSWORD="$password" jq -cn '{password: env.PASSWORD}')" >/dev/null

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
check_seconds="$(jq -r .check_seconds <<<"$created")"
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

echo "logging in through web as gamma..."
if ! browsed -f -o /dev/null "$web/login"; then
    echo "the login page of web could not be loaded" >&2
    exit 1
fi
answer="$(posted /login "name=gamma&password=$password" \
    -o "$TEMP_DIR/login.html" -w '%{http_code}')"
pending="$(sed -n 's/.*name="pending" value="\([^"]*\)".*/\1/p' "$TEMP_DIR/login.html")"
if [ "$answer" != 200 ] || [ -z "$pending" ]; then
    echo "the first login through web answered $answer without asking for a new password" >&2
    exit 1
fi
password="$(new_password)"
answered="$(posted /login \
    "pending=$pending&new_password=$password&repeat_password=$password" -o /dev/null -D - |
    tr -d '\r')"
answer="$(head -n 1 <<<"$answered" | cut -d' ' -f2)"
unset pending password
if [ "$answer" != 303 ] || [ "$(header location "$answered")" != /deployments ]; then
    echo "the new password through web answered $answer, not 303 to /deployments" >&2
    exit 1
fi
if [ -z "$(jarred jatszoter_session)" ]; then
    echo "the login through web set no jatszoter_session cookie" >&2
    exit 1
fi
answer="$(browsed -o "$TEMP_DIR/deployments.html" -w '%{http_code}' "$web/deployments")"
if [ "$answer" != 200 ] || ! grep -qF "/deployments/$deployment\"" "$TEMP_DIR/deployments.html"; then
    echo "the deployments page answered $answer and does not name the deployment" >&2
    exit 1
fi
operator_csrf="$(sed -n 's/.*<meta name="csrf-token" content="\([^"]*\)".*/\1/p' \
    "$TEMP_DIR/deployments.html")"
if [ -z "$operator_csrf" ]; then
    echo "the deployments page carries no CSRF token" >&2
    exit 1
fi
answer="$(browsed -o /dev/null -w '%{http_code}' -d '' "$web/deployments/$deployment/check")"
if [ "$answer" != 403 ]; then
    echo "a check posted to web without its CSRF token answered $answer, not 403" >&2
    exit 1
fi

echo "opening the trainee link..."
link="$(jq -r .link.id <<<"$created")"
token="$(jq -r .link.token <<<"$created")"
unset created
if [[ ! "$token" =~ ^[A-Za-z0-9_-]{32,}$ ]]; then
    echo "the create response carries no link token" >&2
    exit 1
fi
opened="$(open_link -D -)"
answer="$(head -n 1 <<<"$opened" | cut -d' ' -f2)"
if [ "$answer" != 303 ] || [ "$(header location "$opened")" != "/t/$deployment" ]; then
    echo "opening the link answered $answer, not 303 to /t/$deployment" >&2
    exit 1
fi
jar="$(header set-cookie "$opened")"
cookie="${jar%%;*}"
attributes="; ${jar#*; };"
attributes="${attributes,,}"
unset opened jar
if [[ "$cookie" != jatszoter_link=?* ]]; then
    echo "opening the link set no jatszoter_link cookie" >&2
    exit 1
fi
cookie="${cookie#jatszoter_link=}"
for attribute in httponly secure samesite=strict "path=/api/v1/deployments/$deployment"; do
    if [[ "$attributes" != *"; $attribute;"* ]]; then
        echo "the link cookie is not $attribute" >&2
        exit 1
    fi
done

echo "loading the trainee page..."
shown="$(curl -sS -o /dev/null -D - "$web/t/$deployment" | tr -d '\r')"
answer="$(head -n 1 <<<"$shown" | cut -d' ' -f2)"
csrf="$(header set-cookie "$shown" | sed -n 's/^jatszoter_csrf=\([^;]*\);.*/\1/p')"
unset shown
if [ "$answer" != 200 ] || [ -z "$csrf" ]; then
    echo "the trainee page answered $answer and set no jatszoter_csrf cookie" >&2
    exit 1
fi
task="/api/v1/deployments/$deployment/task"
if ! linked GET "$task" | jq -e '.role == "trainee" and .status == "READY"' >/dev/null; then
    echo "the relayed task route does not answer the trainee at READY" >&2
    exit 1
fi
answer="$(linked bare POST "/api/v1/deployments/$deployment/checks" -o /dev/null -w '%{http_code}')"
if [ "$answer" != 403 ]; then
    echo "a relayed check without its CSRF token answered $answer, not 403" >&2
    exit 1
fi

echo "typing as the trainee, then taking control..."
terminal handover

echo "checking the broken guest as the trainee..."
status="$(check linked twice)"
settled "before the fix" "$status" failed 'nginx still does not serve the app page'
if ! jq -e 'has("detail") | not' "$TEMP_DIR/check.json" >/dev/null; then
    echo "the trainee's answer to a check carries a detail key" >&2
    exit 1
fi

echo "repairing the guest as the operator..."
terminal fix

echo "checking the repaired guest as the operator..."
status="$(check signed)"
settled "after the fix" "$status" passed solved

echo "replaying the recording..."
listed="$(recordings)"
if [ "$(jq length <<<"$listed")" != 1 ]; then
    echo "the deployment lists $(jq length <<<"$listed") recordings, not 1" >&2
    exit 1
fi
recording="$(jq -r '.[0].id' <<<"$listed")"
replayed="$web/deployments/$deployment/recordings/$recording"
answer="$(browsed -o /dev/null -w '%{http_code}' "$replayed")"
if [ "$answer" != 200 ]; then
    echo "the replay page answered $answer, not 200" >&2
    exit 1
fi
if ! browsed -f -o "$TEMP_DIR/recording.cast" "$replayed.cast"; then
    echo "the recording could not be read through web" >&2
    exit 1
fi
replay "$TEMP_DIR/recording.cast"
stored="$TEMP_DIR/data/storage/recordings/$deployment/$recording.cast"

echo "revoking the trainee link..."
signed POST "/api/v1/links/$link/revoke" >/dev/null
if ! recordings | jq -e '.[0].size_bytes > 0 and .[0].duration_seconds > 0' >/dev/null; then
    echo "the revoked link's recording has no size or no duration" >&2
    exit 1
fi
answer="$(open_link -w '%{http_code}')"
if [ "$answer" != 404 ]; then
    echo "opening the revoked link answered $answer, not 404" >&2
    exit 1
fi
answer="$(curl -sS -o /dev/null -w '%{http_code}' "$web/t/$deployment")"
if [ "$answer" != 200 ]; then
    echo "the trainee page answered $answer after the revoke, not 200" >&2
    exit 1
fi
answer="$(linked GET "$task" -o /dev/null -w '%{http_code}')"
if [ "$answer" != 401 ] && [ "$answer" != 403 ]; then
    echo "the relayed task route answered $answer after the revoke, not 401 or 403" >&2
    exit 1
fi
if grep -qF -f <(printf '%s\n%s\n%s\n' "$token" "$cookie" "$(jarred jatszoter_session)") \
    "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log" "$TEMP_DIR/web.log" "$stored"; then
    echo "a log or the recording holds the link token or a cookie value" >&2
    exit 1
fi

echo "stopping the deployment..."
signed POST "/api/v1/deployments/$deployment/stop" >/dev/null
wait_for 60 vm_destroyed
if [ ! -s "$stored" ]; then
    echo "the recording is not at $stored once the deployment completed" >&2
    exit 1
fi
signed GET "/api/v1/recordings/$recording.cast" >"$TEMP_DIR/recording.final"
if [ "$(wc -c <"$TEMP_DIR/recording.final")" -lt "$(wc -c <"$TEMP_DIR/recording.cast")" ]; then
    echo "the recording shrank after the deployment completed" >&2
    exit 1
fi

echo "smoke test passed"
