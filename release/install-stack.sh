#!/bin/sh
# Installs the jatszoter stack of a jatszoter release with docker compose:
#   curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-stack.sh | sh -s -- --help
set -eu

stamped_release=

repo="${JATSZOTER_REPO:-oberon-systems/jatszoter}"

usage() {
    cat <<EOF
usage: install-stack.sh [doctor|uninstall] [flags]

Without a command it installs or upgrades the stack: compose.yaml and a .env with generated secrets, then
docker compose up. An upgrade keeps .env. uninstall removes the stack with all its data. Every value it
lacks is asked on the terminal; --yes, or no terminal, takes the defaults.

  --release X.Y.Z                       jatszoter release to install (default: the latest)
  --from-source                         build the images of this checkout instead
  --layout xdg|system                   xdg: your home, no sudo; system: /opt/jatszoter/stack
  --bind ADDR                           address the two ports are published on (default 127.0.0.1)
  --api-port N                          api port (default 8080)
  --web-port N                          web port (default 8000)
  --auto-register | --no-auto-register  take new agents in without approval (default: no)
  --yes                                 never ask
  --force                               uninstall without asking
EOF
}

fail() {
    echo "install-stack: $*" >&2
    exit 1
}

say() {
    echo "install-stack: $*"
}

need_value() {
    [ "$2" -ge 2 ] || fail "$1 needs a value"
}

latest_release() {
    url="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$repo/releases/latest")" ||
        fail "cannot reach github.com/$repo"
    case "${url##*/}" in
    [0-9]*.[0-9]*.[0-9]*) printf '%s\n' "${url##*/}" ;;
    *) fail "github.com/$repo has no jatszoter release yet" ;;
    esac
}

# An unstamped copy, or a request for another release, runs that release's own script.
delegate() {
    [ -z "${JATSZOTER_INSTALL_DELEGATED:-}" ] || fail "release $1 does not stamp its installer"
    dir="$(mktemp -d)"
    base="https://github.com/$repo/releases/download/$1"
    curl -fsSL -o "$dir/install-stack.sh" "$base/install-stack.sh" ||
        fail "release $1 has no install-stack.sh"
    curl -fsSL -o "$dir/SHA256SUMS" "$base/SHA256SUMS" || fail "release $1 has no SHA256SUMS"
    (cd "$dir" && grep ' install-stack.sh$' SHA256SUMS | sha256sum -c - >/dev/null) ||
        fail "install-stack.sh of $1 does not match its SHA256SUMS"
    shift
    status=0
    JATSZOTER_INSTALL_DELEGATED=1 sh "$dir/install-stack.sh" "$@" || status=$?
    rm -rf "$dir"
    exit "$status"
}

wanted=
source_dir=
previous=
for arg in "$@"; do
    case "$arg" in
    -h | --help) usage && exit 0 ;;
    --from-source) source_dir="$(cd "$(dirname "$0")/.." && pwd)" ;;
    esac
    [ "$previous" != --release ] || wanted="$arg"
    previous="$arg"
done
for needed in curl sha256sum; do
    command -v "$needed" >/dev/null || fail "$needed is required"
done
if [ -n "$source_dir" ]; then
    [ -z "$wanted" ] || fail "--release and --from-source exclude each other"
    [ -f "$source_dir/docker/compose.yaml" ] ||
        fail "--from-source runs from a jatszoter checkout: sh release/install-stack.sh --from-source"
    stamped_release=source
else
    target="${wanted:-$(latest_release)}"
    [ "$target" = "$stamped_release" ] || delegate "$target" "$@"
fi

command=install
layout=''
bind=''
api_port=''
web_port=''
auto_register=''
yes=''
force=''
while [ $# -gt 0 ]; do
    case "$1" in
    doctor | uninstall) command="$1" ;;
    --release) need_value "$1" $# && shift ;;
    --from-source) ;;
    --layout) need_value "$1" $# && layout="$2" && shift ;;
    --bind) need_value "$1" $# && bind="$2" && shift ;;
    --api-port) need_value "$1" $# && api_port="$2" && shift ;;
    --web-port) need_value "$1" $# && web_port="$2" && shift ;;
    --auto-register) auto_register=true ;;
    --no-auto-register) auto_register=false ;;
    --yes) yes=1 ;;
    --force) force=1 ;;
    *) fail "unknown argument $1, see --help" ;;
    esac
    shift
done

interactive() {
    [ -z "$yes" ] && (: </dev/tty) 2>/dev/null
}

# ask VAR QUESTION DEFAULT: keeps a value already set, else asks, else takes the default.
ask() {
    eval "current=\${$1}"
    [ -z "$current" ] || return 0
    if interactive; then
        printf '%s [%s]: ' "$2" "$3" >/dev/tty
        read -r answer </dev/tty || answer=
        eval "$1=\${answer:-\$3}"
    else
        eval "$1=\$3"
    fi
}

ask_no() {
    eval "current=\${$1}"
    [ -z "$current" ] || return 0
    answer=
    if interactive; then
        printf '%s [y/N]: ' "$2" >/dev/tty
        read -r answer </dev/tty || answer=
    fi
    case "$answer" in [yY]*) eval "$1=true" ;; *) eval "$1=false" ;; esac
}

paths() {
    case "$layout" in
    xdg) dir="${XDG_DATA_HOME:-$HOME/.local/share}/jatszoter/stack" ;;
    system) dir=/opt/jatszoter/stack ;;
    *) fail "--layout is xdg or system, not $layout" ;;
    esac
    env_file="$dir/.env"
}

detect_layout() {
    [ -z "$layout" ] || return 0
    if [ -f /opt/jatszoter/stack/.env ]; then layout=system; fi
    if [ -f "${XDG_DATA_HOME:-$HOME/.local/share}/jatszoter/stack/.env" ]; then layout=xdg; fi
}

choose_layout() {
    detect_layout
    [ -z "$layout" ] || return 0
    if ! interactive; then
        layout=xdg
        return 0
    fi
    cat >/dev/tty <<EOF
Where should the stack live?
  xdg     ${XDG_DATA_HOME:-$HOME/.local/share}/jatszoter/stack; no sudo
  system  /opt/jatszoter/stack, owned by $(id -un); sudo only to create it
EOF
    ask layout "layout" xdg
}

as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}

compose() {
    docker compose --project-directory "$dir" -f "$dir/compose.yaml" "$@"
}

setting() {
    [ -n "${env_file:-}" ] && [ -f "$env_file" ] || return 0
    sed -n "s/^$1=//p" "$env_file"
}

running() {
    [ -f "$dir/compose.yaml" ] && [ -f "$env_file" ] && [ -n "$(compose ps -q 2>/dev/null)" ]
}

problems=0

good() {
    printf '  [ok]   %s\n' "$1"
}

bad() {
    problems=$((problems + 1))
    printf '  [FAIL] %s\n         fix: %s\n' "$1" "$2"
}

# A refused connection is a free port; anything else that answers holds it.
port_free() {
    status=0
    curl -s -o /dev/null --max-time 3 "http://$1:$2/" || status=$?
    [ "$status" = 7 ]
}

doctor() {
    problems=0
    say "doctor:"
    if ! command -v docker >/dev/null; then
        bad "docker is missing" "install Docker Engine, see https://docs.docker.com/engine/install/"
    else
        good "docker: $(command -v docker)"
        if docker compose version >/dev/null 2>&1; then
            good "docker compose $(docker compose version --short 2>/dev/null)"
        else
            bad "the docker compose plugin is missing" "install docker-compose-plugin"
        fi
        if docker info >/dev/null 2>&1; then
            good "the docker daemon answers $(id -un)"
        else
            bad "cannot talk to the docker daemon as $(id -un)" "start docker, or add $(id -un) to the docker group"
        fi
    fi
    if [ -f "${env_file:-}" ]; then
        good "$env_file exists, its secrets are kept"
    elif command -v openssl >/dev/null; then
        good "openssl, it generates the secrets"
    else
        bad "openssl is missing, it generates the secrets" "install openssl"
    fi
    if [ -n "$layout" ] && running; then
        good "the stack in $dir is up, an upgrade restarts it"
    elif [ "$command" = install ]; then
        host="${bind:-127.0.0.1}"
        [ "$host" != 0.0.0.0 ] || host=127.0.0.1
        for port in "${api_port:-8080}" "${web_port:-8000}"; do
            if port_free "$host" "$port"; then
                good "$host:$port is free"
            else
                bad "something already listens on $host:$port" "stop it, or pass --api-port / --web-port"
            fi
        done
    fi
    if [ "$layout" = system ] && [ ! -d /opt/jatszoter/stack ] && [ "$(id -u)" != 0 ]; then
        if command -v sudo >/dev/null; then
            good "sudo, the system layout creates /opt/jatszoter/stack with it"
        else
            bad "the system layout needs sudo to create /opt/jatszoter/stack" "install sudo or use --layout xdg"
        fi
    fi
    if [ "$problems" -gt 0 ]; then
        echo "install-stack: doctor: $problems problem(s)" >&2
        return 1
    fi
    say "doctor: the host can run the stack"
}

fetch() {
    tmp="$(mktemp -d)"
    if [ -n "$source_dir" ]; then
        cp "$source_dir/docker/compose.yaml" "$tmp/compose.yaml"
        cp "$source_dir/docker/.env.example" "$tmp/env.example"
        rev="$(git -C "$source_dir" rev-parse HEAD 2>/dev/null || echo unknown)"
        for pkg in api web; do
            docker build -f "$source_dir/docker/$pkg/Dockerfile" -t "jatszoter-$pkg:dev" \
                --label "org.opencontainers.image.revision=$rev" "$source_dir" ||
                fail "cannot build the $pkg image"
        done
    else
        base="https://github.com/$repo/releases/download/$stamped_release"
        for file in compose.yaml env.example SHA256SUMS; do
            curl -fsSL -o "$tmp/$file" "$base/$file" || fail "cannot download $file of $stamped_release"
        done
        (cd "$tmp" && grep -E ' (compose\.yaml|env\.example)$' SHA256SUMS | sha256sum -c - >/dev/null) ||
            fail "compose.yaml or env.example does not match SHA256SUMS"
    fi
    if [ ! -d "$dir" ]; then
        if [ "$layout" = system ]; then
            as_root install -d -m 0700 -o "$(id -un)" -g "$(id -gn)" "$dir"
        else
            install -d -m 0700 "$dir"
        fi
    fi
    install -m 0644 "$tmp/compose.yaml" "$dir/compose.yaml"
    install -m 0644 "$tmp/env.example" "$dir/env.example"
    rm -rf "$tmp"
}

hex() {
    openssl rand -hex "$1"
}

# set KEY VALUE in .env.new
set_value() {
    sed -i "s|^$1=.*|$1=$2|" "$env_file.new"
}

configure() {
    if [ -f "$env_file" ]; then
        cp "$env_file" "$env_file.new"
    else
        cookie="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')"
        sed -e "s|^JATSZOTER_DB_PASSWORD=\$|JATSZOTER_DB_PASSWORD=$(hex 24)|" \
            -e "s|^JATSZOTER_VALKEY_PASSWORD=\$|JATSZOTER_VALKEY_PASSWORD=$(hex 24)|" \
            -e "s|^JATSZOTER_SEAWEED_JWT_KEY=\$|JATSZOTER_SEAWEED_JWT_KEY=$(hex 32)|" \
            -e "s|^JATSZOTER_MASTER_KEYS='{\"k1\": \"\"}'\$|JATSZOTER_MASTER_KEYS='{\"k1\": \"$(hex 32)\"}'|" \
            -e "s|^JATSZOTER_WEB_COOKIE_KEYS='{\"c1\": \"\"}'\$|JATSZOTER_WEB_COOKIE_KEYS='{\"c1\": \"$cookie\"}'|" \
            "$dir/env.example" >"$env_file.new"
        ! grep -Eq '^JATSZOTER_[A-Z_]+=$|""' "$env_file.new" ||
            fail "env.example has a secret this installer does not generate"
    fi
    chmod 0600 "$env_file.new"
    set_value JATSZOTER_BIND "$bind"
    set_value JATSZOTER_API_PORT "$api_port"
    set_value JATSZOTER_WEB_PORT "$web_port"
    set_value JATSZOTER_AUTO_REGISTER_AGENTS "$auto_register"
    # A release pins its images in compose.yaml; only a source install names its own.
    sed -i '/^JATSZOTER_\(API\|WEB\)_IMAGE=/d' "$env_file.new"
    if [ -n "$source_dir" ]; then
        printf '%s\n' JATSZOTER_API_IMAGE=jatszoter-api:dev JATSZOTER_WEB_IMAGE=jatszoter-web:dev >>"$env_file.new"
    fi
    mv "$env_file.new" "$env_file"
}

up() {
    if ! compose up -d --wait --wait-timeout 300; then
        compose logs --tail 50 >&2 || true
        fail "the stack did not come up healthy"
    fi
}

confirm() {
    [ -z "$force" ] || return 0
    interactive || fail "no terminal to confirm: pass --force"
    printf '%s\nremove all of it? [y/N]: ' "$1" >/dev/tty
    read -r answer </dev/tty || answer=
    case "$answer" in [yY]*) ;; *) fail "nothing removed" ;; esac
}

uninstall() {
    detect_layout
    [ -n "$layout" ] || { say "no stack is installed" && return 0; }
    paths
    confirm "This removes the jatszoter stack in $dir: its containers and images, the volumes with every
scenario, recording and user, and $env_file with the master keys."
    compose down --volumes --rmi all || fail "docker compose down failed, nothing else removed"
    if [ "$layout" = system ]; then
        as_root rm -rf "$dir"
        as_root rmdir /opt/jatszoter 2>/dev/null || true
    else
        rm -rf "$dir"
        rmdir "${dir%/*}" 2>/dev/null || true
    fi
    say "removed the stack"
}

if [ "$command" = doctor ]; then
    detect_layout
    [ -z "$layout" ] || paths
    bind="${bind:-$(setting JATSZOTER_BIND)}"
    api_port="${api_port:-$(setting JATSZOTER_API_PORT)}"
    web_port="${web_port:-$(setting JATSZOTER_WEB_PORT)}"
    doctor
    exit
fi

if [ "$command" = uninstall ]; then
    uninstall
    exit 0
fi

choose_layout
paths

bind="${bind:-$(setting JATSZOTER_BIND)}"
api_port="${api_port:-$(setting JATSZOTER_API_PORT)}"
web_port="${web_port:-$(setting JATSZOTER_WEB_PORT)}"
auto_register="${auto_register:-$(setting JATSZOTER_AUTO_REGISTER_AGENTS)}"
ask bind "address to publish the ports on" 127.0.0.1
ask api_port "api port" 8080
ask web_port "web port" 8000
ask_no auto_register "take new agents in without an operator approving them?"

doctor || fail "fix the problems above, nothing was installed"

fetch
configure
up
say "jatszoter $stamped_release runs from $dir"
say "log in at http://$bind:$web_port/login as admin, password admin; it asks for a new one"
say "keep $env_file: without its master keys no stored credential verifies again"
