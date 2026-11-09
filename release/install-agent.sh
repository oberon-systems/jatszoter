#!/bin/sh
# Installs the jatszoter agent of a jatszoter release and its systemd unit:
#   curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh -s -- --help
set -eu

stamped_release=
stamped_agent=

repo="${JATSZOTER_REPO:-oberon-systems/jatszoter}"
runbook="https://github.com/$repo/blob/main/docs/project/09-guest-network.md"
unit_name=jatszoter-agent.service

usage() {
    cat <<EOF
usage: install-agent.sh [doctor|uninstall] [flags]

Without a command it installs or upgrades the agent; uninstall removes all of it. Every value it lacks is
asked on the terminal; --yes, or no terminal, turns a missing value into an error.

  --release X.Y.Z             jatszoter release to install (default: the latest)
  --layout xdg|system         xdg: your home, no sudo; system: /opt/jatszoter/agent
  --api-url URL               jatszoter api the agent leases from
  --name NAME                 agent name (default: the host name)
  --capacity N                VMs at once (default 1)
  --labels A,B                labels
  --systemd | --no-systemd    install the systemd unit (default: yes)
  --linger | --no-linger      keep the user unit running without a login (xdg)
  --yes                       never ask
  --force                     uninstall without asking
EOF
}

fail() {
    echo "install-agent: $*" >&2
    exit 1
}

say() {
    echo "install-agent: $*"
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
    curl -fsSL -o "$dir/install-agent.sh" "$base/install-agent.sh" ||
        fail "release $1 has no install-agent.sh"
    curl -fsSL -o "$dir/SHA256SUMS" "$base/SHA256SUMS" || fail "release $1 has no SHA256SUMS"
    (cd "$dir" && grep ' install-agent.sh$' SHA256SUMS | sha256sum -c - >/dev/null) ||
        fail "install-agent.sh of $1 does not match its SHA256SUMS"
    shift
    status=0
    JATSZOTER_INSTALL_DELEGATED=1 sh "$dir/install-agent.sh" "$@" || status=$?
    rm -rf "$dir"
    exit "$status"
}

wanted=
previous=
for arg in "$@"; do
    case "$arg" in -h | --help) usage && exit 0 ;; esac
    [ "$previous" != --release ] || wanted="$arg"
    previous="$arg"
done
for needed in curl sha256sum; do
    command -v "$needed" >/dev/null || fail "$needed is required"
done
target="${wanted:-$(latest_release)}"
[ "$target" = "$stamped_release" ] || delegate "$target" "$@"

command=install
layout=''
api_url=''
name=''
capacity=''
labels=''
systemd=''
linger=''
yes=''
force=''
while [ $# -gt 0 ]; do
    case "$1" in
    doctor | uninstall) command="$1" ;;
    --release) need_value "$1" $# && shift ;;
    --layout) need_value "$1" $# && layout="$2" && shift ;;
    --api-url) need_value "$1" $# && api_url="$2" && shift ;;
    --name) need_value "$1" $# && name="$2" && shift ;;
    --capacity) need_value "$1" $# && capacity="$2" && shift ;;
    --labels) need_value "$1" $# && labels="$2" && shift ;;
    --systemd) systemd=yes ;;
    --no-systemd) systemd=no ;;
    --linger) linger=yes ;;
    --no-linger) linger=no ;;
    --yes) yes=1 ;;
    --force) force=1 ;;
    *) fail "unknown argument $1, see --help" ;;
    esac
    shift
done

interactive() {
    [ -z "$yes" ] && (: </dev/tty) 2>/dev/null
}

# ask VAR QUESTION DEFAULT FLAG: keeps a value already set, else asks, else takes the default.
ask() {
    eval "current=\${$1}"
    [ -z "$current" ] || return 0
    if interactive; then
        printf '%s [%s]: ' "$2" "$3" >/dev/tty
        read -r answer </dev/tty || answer=
        eval "$1=\${answer:-\$3}"
    elif [ -n "$3" ]; then
        eval "$1=\$3"
    else
        fail "no terminal to ask for $2: pass $4"
    fi
}

ask_yes() {
    eval "current=\${$1}"
    [ -z "$current" ] || return 0
    if interactive; then
        printf '%s [Y/n]: ' "$2" >/dev/tty
        read -r answer </dev/tty || answer=
        case "$answer" in [nN]*) eval "$1=no" ;; *) eval "$1=yes" ;; esac
    else
        eval "$1=yes"
    fi
}

paths() {
    case "$layout" in
    xdg)
        bin_dir="${XDG_DATA_HOME:-$HOME/.local/share}/jatszoter/bin"
        conf_dir="${XDG_CONFIG_HOME:-$HOME/.config}/jatszoter"
        # The passt AppArmor profile of docs/project/09-guest-network.md allows these two.
        state_dir="$HOME/.cache/jatszoter"
        link="$bin_dir/jatszoter-agent"
        unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
        ;;
    system)
        bin_dir=/opt/jatszoter/agent/bin
        conf_dir=/opt/jatszoter/agent
        state_dir=/var/lib/jatszoter-agent
        link=/usr/local/bin/jatszoter-agent
        unit_dir=/etc/systemd/system
        ;;
    *) fail "--layout is xdg or system, not $layout" ;;
    esac
    env_file="$conf_dir/agent.env"
}

detect_layout() {
    [ -z "$layout" ] || return 0
    if [ -f /opt/jatszoter/agent/agent.env ]; then layout=system; fi
    if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/jatszoter/agent.env" ]; then layout=xdg; fi
}

choose_layout() {
    detect_layout
    [ -z "$layout" ] || return 0
    interactive || fail "no terminal to choose a layout: pass --layout xdg or --layout system"
    cat >/dev/tty <<EOF
Where should the agent live?
  xdg     ${XDG_DATA_HOME:-$HOME/.local/share}/jatszoter/bin, ${XDG_CONFIG_HOME:-$HOME/.config}/jatszoter,
          $HOME/.cache/jatszoter, a systemd user unit; no sudo
  system  /opt/jatszoter/agent, /var/lib/jatszoter-agent, /usr/local/bin/jatszoter-agent,
          a system unit that runs as $(id -un); needs sudo
EOF
    ask layout "layout" xdg --layout
}

as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}

# Writes with root only where the system layout puts a file.
put() {
    if [ "$layout" = system ]; then as_root "$@"; else "$@"; fi
}

systemctl_unit() {
    if [ "$layout" = system ]; then as_root systemctl "$@"; else systemctl --user "$@"; fi
}

setting() {
    [ -n "${env_file:-}" ] && [ -f "$env_file" ] || return 0
    sed -n "s/^$1=//p" "$env_file"
}

# tool NAME DEFAULT: what the agent runs for NAME, from the env file or by default.
tool() {
    value="$(setting "JATSZOTER_AGENT_$1")"
    printf '%s\n' "${value:-$2}"
}

problems=

problem() {
    problems="$problems
  - $1
    fix: $2"
}

doctor() {
    problems=
    [ "$(uname -s)/$(uname -m)" = Linux/x86_64 ] ||
        problem "the agent is built for Linux on x86_64, this is $(uname -s)/$(uname -m)" "use an x86_64 Linux host"
    [ "$(id -u)" != 0 ] ||
        problem "running as root; the agent refuses to run as root" "run the installer as the agent's user, sudo is used where needed"
    { [ -r /dev/kvm ] && [ -w /dev/kvm ]; } ||
        problem "no read and write access to /dev/kvm" "enable KVM and add $(id -un) to the kvm group"
    for binary in "$(tool QEMU qemu-system-x86_64)" "$(tool QEMU_IMG qemu-img)" "$(tool XORRISO xorriso)"; do
        command -v "$binary" >/dev/null || problem "$binary is missing" "install the package that provides it"
    done
    passt="$(tool PASST passt)"
    command -v "$passt" >/dev/null ||
        problem "$passt is missing" "$runbook (passt 2026_10_02 or newer, AppArmor profile on Ubuntu)"
    for file in "$(tool OVMF_CODE /usr/share/OVMF/OVMF_CODE_4M.fd)" "$(tool OVMF_VARS /usr/share/OVMF/OVMF_VARS_4M.fd)"; do
        [ -r "$file" ] || problem "$file is missing" "install the OVMF package"
    done
    if [ -n "$api_url" ]; then
        curl -fsS --max-time 10 -o /dev/null "${api_url%/}/healthz" ||
            problem "the api does not answer at ${api_url%/}/healthz" "check --api-url and that the stack is up"
    fi
    if [ "$systemd" = yes ]; then
        command -v systemctl >/dev/null || problem "no systemctl" "pass --no-systemd and start the agent yourself"
    fi
    if [ "$layout" = system ] && [ "$(id -u)" != 0 ]; then
        command -v sudo >/dev/null || problem "the system layout needs sudo" "install sudo or use --layout xdg"
    fi
    if [ -n "$problems" ]; then
        echo "install-agent: doctor found problems:$problems" >&2
        return 1
    fi
    say "doctor: the host can run the agent"
}

download() {
    asset="jatszoter-agent-$stamped_agent-x86_64-linux-gnu"
    url="https://github.com/$repo/releases/download/agent-$stamped_agent"
    tmp="$(mktemp -d)"
    curl -fsSL -o "$tmp/$asset" "$url/$asset" || fail "cannot download $asset"
    curl -fsSL -o "$tmp/SHA256SUMS" "$url/SHA256SUMS" || fail "cannot download SHA256SUMS"
    (cd "$tmp" && grep " $asset\$" SHA256SUMS | sha256sum -c - >/dev/null) ||
        fail "$asset does not match its SHA256SUMS"
    put install -d -m 0755 "$bin_dir"
    put install -m 0755 "$tmp/$asset" "$bin_dir/jatszoter-agent-$stamped_agent"
    rm -rf "$tmp"
    if [ "$layout" = system ]; then
        as_root ln -sfn "$bin_dir/jatszoter-agent-$stamped_agent" "$bin_dir/jatszoter-agent"
        as_root ln -sfn "$bin_dir/jatszoter-agent" "$link"
    else
        ln -sfn "$bin_dir/jatszoter-agent-$stamped_agent" "$link"
    fi
}

configure() {
    owner="$(id -un)"
    group="$(id -gn)"
    put install -d -m 0700 -o "$owner" -g "$group" "$conf_dir" "$state_dir"
    {
        echo "JATSZOTER_AGENT_API_URL=$api_url"
        echo "JATSZOTER_AGENT_NAME=$name"
        echo "JATSZOTER_AGENT_STATE_DIR=$state_dir"
        [ -z "$capacity" ] || echo "JATSZOTER_AGENT_CAPACITY=$capacity"
        [ -z "$labels" ] || echo "JATSZOTER_AGENT_LABELS=$labels"
        # Tool paths and any other setting written by hand survive an upgrade.
        [ ! -f "$env_file" ] || grep -E '^JATSZOTER_AGENT_[A-Z_]+=' "$env_file" |
            grep -Ev '^JATSZOTER_AGENT_(API_URL|NAME|STATE_DIR|CAPACITY|LABELS)=' || true
    } >"$env_file.new"
    chmod 0600 "$env_file.new"
    mv "$env_file.new" "$env_file"
}

# No flag skips it: a guest network that is not safe must never get a unit.
check_network() {
    output="$(JATSZOTER_AGENT_ENV_FILE="$env_file" "$link" check-network 2>&1)" && return 0
    printf '%s\n' "$output" >&2
    fail "the guest network is not safe; follow $runbook (passt 2026_10_02 or newer, AppArmor profile on Ubuntu)"
}

linger_on() {
    loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -qx Linger=yes
}

install_unit() {
    [ "$systemd" = yes ] || return 0
    if [ "$layout" = system ]; then
        user_line="User=$(id -un)"
        target=multi-user.target
    else
        user_line=
        target=default.target
    fi
    unit="$(mktemp)"
    cat >"$unit" <<EOF
[Unit]
Description=jatszoter agent
After=network-online.target
Wants=network-online.target

[Service]
$user_line
Environment=JATSZOTER_AGENT_ENV_FILE=$env_file
ExecStart=$link
Restart=on-failure
RestartSec=5

[Install]
WantedBy=$target
EOF
    put install -d -m 0755 "$unit_dir"
    put install -m 0644 "$unit" "$unit_dir/$unit_name"
    rm -f "$unit"
    systemctl_unit daemon-reload
    systemctl_unit enable "$unit_name" >/dev/null 2>&1
    systemctl_unit restart "$unit_name"
    say "the agent runs as $unit_name"
    [ "$layout" = xdg ] || return 0
    linger_on && return 0
    ask_yes linger "keep the agent running after you log out (loginctl enable-linger)?"
    [ "$linger" = yes ] || return 0
    loginctl enable-linger "$(id -un)" 2>/dev/null || as_root loginctl enable-linger "$(id -un)" ||
        say "could not enable linger, run: sudo loginctl enable-linger $(id -un)"
}

confirm() {
    [ -z "$force" ] || return 0
    interactive || fail "no terminal to confirm: pass --force"
    printf '%s\nremove all of it? [y/N]: ' "$1" >/dev/tty
    read -r answer </dev/tty || answer=
    case "$answer" in [yY]*) ;; *) fail "nothing removed" ;; esac
}

# The state can hold files a VM left behind that only root removes.
uninstall() {
    detect_layout
    [ -n "$layout" ] || { say "no agent is installed" && return 0; }
    paths
    confirm "This removes the jatszoter agent: $unit_name, $link, the binaries in $bin_dir,
the settings in $env_file and the state in $state_dir."
    if [ -f "$unit_dir/$unit_name" ]; then
        systemctl_unit disable --now "$unit_name" >/dev/null 2>&1 || true
        put rm -f "$unit_dir/$unit_name"
        systemctl_unit daemon-reload
    fi
    put rm -f "$link"
    put rm -rf "$bin_dir" "$env_file"
    put rm -rf "$state_dir" 2>/dev/null || as_root rm -rf "$state_dir"
    if [ "$layout" = system ]; then
        as_root rm -rf "$conf_dir"
        as_root rmdir "${conf_dir%/*}" 2>/dev/null || true
    else
        rmdir "$conf_dir" "${bin_dir%/*}" 2>/dev/null || true
    fi
    say "removed the agent"
    [ "$layout" = system ] || ! linger_on || say "linger stays on, turn it off with: loginctl disable-linger"
}

if [ "$command" = doctor ]; then
    detect_layout
    if [ -n "$layout" ]; then
        paths
        api_url="${api_url:-$(setting JATSZOTER_AGENT_API_URL)}"
    fi
    doctor
    exit
fi

if [ "$command" = uninstall ]; then
    uninstall
    exit 0
fi

choose_layout
paths

api_url="${api_url:-$(setting JATSZOTER_AGENT_API_URL)}"
name="${name:-$(setting JATSZOTER_AGENT_NAME)}"
capacity="${capacity:-$(setting JATSZOTER_AGENT_CAPACITY)}"
labels="${labels:-$(setting JATSZOTER_AGENT_LABELS)}"
ask api_url "jatszoter api url" "" --api-url
ask name "agent name" "$(uname -n)" --name
if [ ! -f "$env_file" ]; then
    ask capacity "VMs at once" 1 --capacity
fi
if command -v systemctl >/dev/null; then
    ask_yes systemd "install the systemd unit?"
else
    systemd="${systemd:-no}"
fi

doctor || exit 1

download
configure
check_network
install_unit
say "agent $stamped_agent of jatszoter $stamped_release installed, $link"
[ "$systemd" = yes ] || say "start it with: JATSZOTER_AGENT_ENV_FILE=$env_file $link"
say "it waits for an operator to approve it in the web UI, unless the stack runs with JATSZOTER_AUTO_REGISTER_AGENTS=true"
