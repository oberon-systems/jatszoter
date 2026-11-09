#!/bin/sh
# Installs the jatszoter agent of a jatszoter release and its systemd unit:
#   curl -fsSL https://github.com/oberon-systems/jatszoter/releases/latest/download/install-agent.sh | sh -s -- --help
set -eu

stamped_release=
stamped_agent=

repo="${JATSZOTER_REPO:-oberon-systems/jatszoter}"
runbook="https://github.com/$repo/blob/main/docs/project/09-guest-network.md"
unit_name=jatszoter-agent.service
passt_tag=2026_10_02.cba3570
passt_commit=cba357068dc586e7540971eb40bb56c5e62c4de1
passt_marker=/usr/local/share/jatszoter/passt

usage() {
    cat <<EOF
usage: install-agent.sh [doctor|uninstall] [flags]

Without a command it installs or upgrades the agent; uninstall removes all of it. Every value it lacks is
asked on the terminal; --yes, or no terminal, turns a missing value into an error.

  --release X.Y.Z             jatszoter release to install (default: the latest)
  --from-source               build the agent of this checkout with cargo instead
  --layout xdg|system         xdg: your home, no sudo; system: /opt/jatszoter/agent
  --api-url URL               jatszoter api the agent leases from
  --name NAME                 agent name (default: the host name)
  --capacity N                VMs at once (default 1)
  --labels A,B                labels
  --passt | --no-passt        build passt $passt_tag into /usr/local/bin with its AppArmor profile
                              (default: ask when passt is missing or older than 2026_10_02)
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
    [ -f "$source_dir/packages/agent/Cargo.toml" ] ||
        fail "--from-source runs from a jatszoter checkout: sh release/install-agent.sh --from-source"
    stamped_release=source
    stamped_agent="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$source_dir/packages/agent/Cargo.toml" | head -n 1)"
else
    target="${wanted:-$(latest_release)}"
    [ "$target" = "$stamped_release" ] || delegate "$target" "$@"
fi

command=install
layout=''
api_url=''
name=''
capacity=''
labels=''
systemd=''
linger=''
build_passt=''
yes=''
force=''
while [ $# -gt 0 ]; do
    case "$1" in
    doctor | uninstall) command="$1" ;;
    --release) need_value "$1" $# && shift ;;
    --from-source) ;;
    --layout) need_value "$1" $# && layout="$2" && shift ;;
    --api-url) need_value "$1" $# && api_url="$2" && shift ;;
    --name) need_value "$1" $# && name="$2" && shift ;;
    --capacity) need_value "$1" $# && capacity="$2" && shift ;;
    --labels) need_value "$1" $# && labels="$2" && shift ;;
    --systemd) systemd=yes ;;
    --no-systemd) systemd=no ;;
    --linger) linger=yes ;;
    --no-linger) linger=no ;;
    --passt) build_passt=yes ;;
    --no-passt) build_passt=no ;;
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
        # The passt AppArmor profile of docs/project/09-guest-network.md allows writes below ~/.cache/jatszoter.
        state_dir="$HOME/.cache/jatszoter/agent"
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
          $HOME/.cache/jatszoter/agent, a systemd user unit; no sudo
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

# A shell from sudo -iu or su - has no XDG_RUNTIME_DIR, and systemctl --user finds no bus without it.
systemctl_unit() {
    if [ "$layout" = system ]; then
        as_root systemctl "$@"
    else
        XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" systemctl --user "$@"
    fi
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

# passt_date BINARY: the release date of a passt as YYYYMMDD, empty for a build of no release.
passt_date() {
    "$1" --version 2>/dev/null | head -n 1 |
        sed -n 's/^passt \([0-9]\{4\}\)_\([0-9][0-9]\)_\([0-9][0-9]\).*/\1\2\3/p'
}

passt_current() {
    command -v "$1" >/dev/null && [ "$(passt_date "$1")" -ge 20261002 ] 2>/dev/null
}

problems=0

good() {
    printf '  [ok]   %s\n' "$1"
}

bad() {
    problems=$((problems + 1))
    printf '  [FAIL] %s\n         fix: %s\n' "$1" "$2"
}

doctor() {
    problems=0
    say "doctor:"
    if [ "$(uname -s)/$(uname -m)" = Linux/x86_64 ]; then
        good "Linux on x86_64"
    else
        bad "this is $(uname -s)/$(uname -m), the agent is built for Linux on x86_64" "use an x86_64 Linux host"
    fi
    if [ "$(id -u)" != 0 ]; then
        good "runs as $(id -un), not root"
    else
        bad "runs as root, the agent refuses to" "run the installer as the agent's user, sudo is used where needed"
    fi
    if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
        good "/dev/kvm is readable and writable"
    else
        bad "no read and write access to /dev/kvm" "enable KVM and add $(id -un) to the kvm group"
    fi
    for binary in "$(tool QEMU qemu-system-x86_64)" "$(tool QEMU_IMG qemu-img)" "$(tool XORRISO xorriso)"; do
        if command -v "$binary" >/dev/null; then
            good "$binary: $(command -v "$binary")"
        else
            bad "$binary is missing" "install the package that provides it"
        fi
    done
    passt="$(tool PASST passt)"
    if [ "$build_passt" = yes ]; then
        good "passt $passt_tag is built into /usr/local/bin with its AppArmor profile"
        for binary in git gcc make; do
            if command -v "$binary" >/dev/null; then
                good "$binary: $(command -v "$binary"), building passt needs it"
            else
                bad "$binary is missing, building passt needs it" "sudo apt install git gcc make"
            fi
        done
        if [ "$(id -u)" != 0 ] && ! command -v sudo >/dev/null; then
            bad "installing passt needs sudo" "install sudo, or pass --no-passt and follow $runbook"
        fi
    elif ! command -v "$passt" >/dev/null; then
        bad "$passt is missing" "run the installer with --passt, or follow $runbook"
    elif passt_current "$passt"; then
        good "$passt: $(command -v "$passt"), $("$passt" --version | head -n 1)"
    else
        bad "$passt is \"$("$passt" --version 2>/dev/null | head -n 1)\", older than 2026_10_02" \
            "run the installer with --passt, or follow $runbook"
    fi
    for file in "$(tool OVMF_CODE /usr/share/OVMF/OVMF_CODE_4M.fd)" "$(tool OVMF_VARS /usr/share/OVMF/OVMF_VARS_4M.fd)"; do
        if [ -r "$file" ]; then good "$file"; else bad "$file is missing" "install the OVMF package"; fi
    done
    if [ -z "$api_url" ]; then
        good "api: no --api-url given, not checked"
    elif curl -fsS --max-time 10 -o /dev/null "${api_url%/}/healthz"; then
        good "the api answers at ${api_url%/}/healthz"
    else
        bad "the api does not answer at ${api_url%/}/healthz" "check --api-url and that the stack is up"
    fi
    if [ -n "$source_dir" ]; then
        if command -v cargo >/dev/null; then
            good "cargo: $(command -v cargo)"
        else
            bad "cargo is missing, --from-source builds with it" "install Rust, see https://rustup.rs"
        fi
    fi
    if [ "$systemd" = yes ]; then
        if command -v systemctl >/dev/null; then
            good "systemctl: $(command -v systemctl)"
        else
            bad "no systemctl" "pass --no-systemd and start the agent yourself"
        fi
    fi
    if [ "$layout" = system ] && [ "$(id -u)" != 0 ]; then
        if command -v sudo >/dev/null; then
            good "sudo, the system layout needs it"
        else
            bad "the system layout needs sudo" "install sudo or use --layout xdg"
        fi
    fi
    if [ "$problems" -gt 0 ]; then
        echo "install-agent: doctor: $problems problem(s)" >&2
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
    place "$tmp/$asset"
    rm -rf "$tmp"
}

# The steps of "Install on Ubuntu" in docs/project/09-guest-network.md.
install_passt() {
    [ "$build_passt" = yes ] || return 0
    say "building passt $passt_tag from passt.top"
    tmp="$(mktemp -d)"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$passt_tag" https://passt.top/passt "$tmp/passt" ||
        fail "cannot clone passt $passt_tag from passt.top"
    [ "$(git -C "$tmp/passt" rev-parse HEAD)" = "$passt_commit" ] ||
        fail "passt $passt_tag is not the commit $passt_commit"
    make -s -C "$tmp/passt" >/dev/null || fail "building passt failed"
    as_root install -m 0755 "$tmp/passt/passt" "$tmp/passt/passt.avx2" /usr/local/bin/
    if [ -d /etc/apparmor.d ]; then
        sed 's#/usr/bin/passt.avx2#/usr/local/bin/passt.avx2#' \
            "$tmp/passt/contrib/apparmor/abstractions/passt" |
            as_root tee /etc/apparmor.d/abstractions/jatszoter-passt >/dev/null
        sed -e 's#^profile passt /usr/bin/passt{,.avx2}#profile passt-local /usr/local/bin/passt{,.avx2}#' \
            -e 's#include <abstractions/passt>#include <abstractions/jatszoter-passt>#' \
            -e 's#^\( *\)/usr/bin/passt r,#\1/usr/local/bin/passt r,#' \
            -e 's#^}#  owner @{HOME}/.cache/jatszoter/** rw,\n  owner /var/lib/jatszoter-agent/** rw,\n}#' \
            "$tmp/passt/contrib/apparmor/usr.bin.passt" |
            as_root tee /etc/apparmor.d/usr.local.bin.passt >/dev/null
        as_root apparmor_parser -r /etc/apparmor.d/usr.local.bin.passt
    fi
    as_root install -d -m 0755 "${passt_marker%/*}"
    echo "$passt_tag" | as_root tee "$passt_marker" >/dev/null
    rm -rf "$tmp"
    say "passt $passt_tag is in /usr/local/bin"
}

uninstall_passt() {
    [ -f "$passt_marker" ] || return 0
    if [ -f /etc/apparmor.d/usr.local.bin.passt ]; then
        as_root apparmor_parser -R /etc/apparmor.d/usr.local.bin.passt 2>/dev/null || true
    fi
    as_root rm -f /etc/apparmor.d/usr.local.bin.passt /etc/apparmor.d/abstractions/jatszoter-passt \
        /usr/local/bin/passt /usr/local/bin/passt.avx2 "$passt_marker"
    as_root rmdir "${passt_marker%/*}" 2>/dev/null || true
}

build() {
    cargo build --release --locked -p jatszoter-agent --manifest-path "$source_dir/Cargo.toml" ||
        fail "cargo build failed"
    place "${CARGO_TARGET_DIR:-$source_dir/target}/release/jatszoter-agent"
}

place() {
    put install -d -m 0755 "$bin_dir"
    put install -m 0755 "$1" "$bin_dir/jatszoter-agent-$stamped_agent"
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
    fail "the guest network is not safe; run the installer with --passt, or follow $runbook"
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
    extra=
    [ ! -f "$passt_marker" ] || extra="
It also removes passt $(cat "$passt_marker") from /usr/local/bin with its AppArmor profile."
    confirm "This removes the jatszoter agent: $unit_name, $link, the binaries in $bin_dir,
the settings in $env_file and the state in $state_dir.$extra"
    if [ -f "$unit_dir/$unit_name" ]; then
        systemctl_unit disable --now "$unit_name" >/dev/null 2>&1 || true
        put rm -f "$unit_dir/$unit_name"
        systemctl_unit daemon-reload
    fi
    put rm -f "$link"
    put rm -rf "$bin_dir" "$env_file"
    put rm -rf "$state_dir" 2>/dev/null || as_root rm -rf "$state_dir"
    uninstall_passt
    if [ "$layout" = system ]; then
        as_root rm -rf "$conf_dir"
        as_root rmdir "${conf_dir%/*}" 2>/dev/null || true
    else
        rmdir "$conf_dir" "${bin_dir%/*}" "${state_dir%/*}" 2>/dev/null || true
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
if [ -z "$build_passt" ] && [ "$(tool PASST passt)" = passt ] && ! passt_current passt; then
    ask_yes build_passt "passt is missing or older than 2026_10_02: build $passt_tag into /usr/local/bin with its AppArmor profile (sudo)?"
fi

doctor || fail "fix the problems above, nothing was installed"

install_passt
if [ -n "$source_dir" ]; then build; else download; fi
configure
check_network
install_unit
say "agent $stamped_agent of jatszoter $stamped_release installed, $link"
[ "$systemd" = yes ] || say "start it with: JATSZOTER_AGENT_ENV_FILE=$env_file $link"
say "it waits for an operator to approve it in the web UI, unless the stack runs with JATSZOTER_AUTO_REGISTER_AGENTS=true"
