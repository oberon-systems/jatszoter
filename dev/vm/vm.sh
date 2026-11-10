#!/usr/bin/env bash
set -euo pipefail

version=1.0.0
cache="${XDG_CACHE_HOME:-$HOME/.cache}/jatszoter/vm"
cloud=https://cloud-images.ubuntu.com/releases/resolute/release
base_image=ubuntu-26.04-server-cloudimg-amd64.img
disk="$cache/disk.qcow2"
seed="$cache/seed.iso"
key="$cache/id_ed25519"
pidfile="$cache/qemu.pid"
tls="$cache/tls"
download="$cache/www/oberon-systems/jatszoter/releases/download"
network=jatszoter-vm-mock
nginx=jatszoter-vm-nginx
nginx_image=nginx:1.30.6-alpine
registry=jatszoter-vm-registry
registry_image=registry:3.0.0
# Off QEMU's default 10.0.2.0/24, so the VM's own addresses never look like those of an agent's guest.
subnet=192.0.2.0/24
host_address=192.0.2.2
ssh_options=(-i "$key" -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)

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

# The CA is trusted inside the VM only; it signs the one certificate nginx shows for both hosts.
certs() {
    [ ! -f "$tls/mock.crt" ] || return 0
    mkdir -p "$tls"
    openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=jatszoter demo CA" \
        -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign \
        -keyout "$cache/ca.key" -out "$cache/ca.crt" 2>/dev/null
    openssl req -newkey rsa:2048 -nodes -subj "/CN=github.com" \
        -keyout "$tls/mock.key" -out "$cache/mock.csr" 2>/dev/null
    openssl x509 -req -in "$cache/mock.csr" -CA "$cache/ca.crt" -CAkey "$cache/ca.key" -CAcreateserial \
        -days 30 -out "$tls/mock.crt" \
        -extfile <(printf 'subjectAltName=DNS:github.com,DNS:ghcr.io\nextendedKeyUsage=serverAuth\n') 2>/dev/null
    chmod 0644 "$tls/mock.key"
}

digest() {
    curl -fsSI \
        -H 'Accept: application/vnd.oci.image.index.v1+json' \
        -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json' \
        -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
        -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
        "http://127.0.0.1:5000/v2/oberon-systems/jatszoter-$1/manifests/$version" |
        tr -d '\r' | sed -n 's/^docker-content-digest: //Ip'
}

release_down() {
    docker rm -f "$nginx" "$registry" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}

release() {
    certs
    release_down
    docker network create "$network" >/dev/null
    docker run -d --name "$registry" --network "$network" --network-alias registry \
        -p 127.0.0.1:5000:5000 "$registry_image" >/dev/null
    wait_for 30 curl -fs -o /dev/null http://127.0.0.1:5000/v2/

    "$MAKE" -C "$ROOT" images
    local refs=()
    for pkg in api web; do
        docker tag "jatszoter-$pkg:dev" "127.0.0.1:5000/oberon-systems/jatszoter-$pkg:$version"
        docker push -q "127.0.0.1:5000/oberon-systems/jatszoter-$pkg:$version" >/dev/null
        refs+=("ghcr.io/oberon-systems/jatszoter-$pkg:$version@$(digest "$pkg")")
    done

    rm -rf "$cache/www"
    mkdir -p "$download/$version" "$download/agent-$version"
    (cd "$ROOT" && cargo build --release --locked -p jatszoter-agent)
    cp "$ROOT/target/release/jatszoter-agent" "$download/agent-$version/jatszoter-agent-$version-x86_64-linux-gnu"
    (cd "$download/agent-$version" && sha256sum jatszoter-agent-* >SHA256SUMS)

    local out="$download/$version"
    sed -e "s|\${JATSZOTER_API_IMAGE[^}]*}|${refs[0]}|" -e "s|\${JATSZOTER_WEB_IMAGE[^}]*}|${refs[1]}|" \
        "$ROOT/docker/compose.yaml" >"$out/compose.yaml"
    grep -q "${refs[0]}" "$out/compose.yaml" && grep -q "${refs[1]}" "$out/compose.yaml"
    cp "$ROOT/docker/.env.example" "$out/env.example"
    sed -e "s/^stamped_release=\$/stamped_release=$version/" -e "s/^stamped_agent=\$/stamped_agent=$version/" \
        "$ROOT/release/install-agent.sh" >"$out/install-agent.sh"
    sed -e "s/^stamped_release=\$/stamped_release=$version/" \
        "$ROOT/release/install-stack.sh" >"$out/install-stack.sh"
    for dir in "$ROOT"/scenarios/*/; do
        local slug
        slug="$(basename "$dir")"
        tar -czf "$out/$slug.tar.gz" -C "$ROOT/scenarios" "$slug"
    done
    (cd "$out" && sha256sum -- compose.yaml env.example install-agent.sh install-stack.sh *.tar.gz >SHA256SUMS)
    chmod -R a+rX "$cache/www"

    docker run -d --name "$nginx" --network "$network" -p 127.0.0.1:443:443 \
        -v "$ROOT/dev/vm/nginx.conf:/etc/nginx/nginx.conf:ro" -v "$tls:/etc/nginx/tls:ro" \
        -v "$cache/www:/usr/share/nginx/www:ro" "$nginx_image" >/dev/null
    wait_for 30 curl -fs -o /dev/null --cacert "$cache/ca.crt" --resolve github.com:443:127.0.0.1 \
        "https://github.com/oberon-systems/jatszoter/releases/download/$version/SHA256SUMS"
    echo "the mock release $version is served on 127.0.0.1:443 as github.com and ghcr.io"
}

user_data() {
    cat <<EOF
#cloud-config
hostname: jatszoter-demo
manage_etc_hosts: false
users:
  - name: alpha
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - $(cat "$key.pub")
ca_certs:
  trusted:
    - |
$(sed 's/^/      /' "$cache/ca.crt")
runcmd:
  - printf '$host_address github.com\n$host_address ghcr.io\n' >> /etc/hosts
EOF
}

running() {
    [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

up() {
    if running; then
        echo "the VM is already up: make -C dev/vm ssh"
        return 0
    fi
    certs
    mkdir -p "$cache"
    if [ ! -f "$cache/$base_image" ]; then
        curl -fL -o "$cache/$base_image.part" "$cloud/$base_image"
        mv "$cache/$base_image.part" "$cache/$base_image"
    fi
    curl -fsSL -o "$cache/SHA256SUMS" "$cloud/SHA256SUMS"
    (cd "$cache" && grep " \*$base_image\$" SHA256SUMS | sha256sum -c - >/dev/null) || {
        echo "$base_image does not match its SHA256SUMS, remove it and run up again" >&2
        exit 1
    }
    [ -f "$key" ] || ssh-keygen -q -t ed25519 -N '' -C alpha@jatszoter-demo -f "$key"
    if [ ! -f "$disk" ]; then
        qemu-img create -q -f qcow2 -b "$cache/$base_image" -F qcow2 "$disk" 40G
        printf 'instance-id: jatszoter-demo-%s\nlocal-hostname: jatszoter-demo\n' "$(date +%s)" >"$cache/meta-data"
        user_data >"$cache/user-data"
        cloud-localds "$seed" "$cache/user-data" "$cache/meta-data"
    fi
    qemu-system-x86_64 -name jatszoter-demo -machine q35,accel=kvm -cpu host -smp 4 -m 8192 \
        -drive "file=$disk,if=virtio,format=qcow2" -drive "file=$seed,if=virtio,format=raw,readonly=on" \
        -netdev "user,id=net0,net=$subnet,hostfwd=tcp:127.0.0.1:2222-:22" -device virtio-net-pci,netdev=net0 \
        -display none -serial "file:$cache/console.log" -daemonize -pidfile "$pidfile"
    # A degraded cloud-init still leaves a usable VM, so only the login is waited for.
    wait_for 300 ssh "${ssh_options[@]}" -o ConnectTimeout=5 alpha@127.0.0.1 'cloud-init status --wait >/dev/null; true'
    echo "the VM is up: make -C dev/vm ssh"
}

ssh_in() {
    running || {
        echo "the VM is not up" >&2
        exit 1
    }
    exec ssh "${ssh_options[@]}" -L 8000:127.0.0.1:8000 -L 8080:127.0.0.1:8080 alpha@127.0.0.1
}

down() {
    if running; then
        ssh "${ssh_options[@]}" -o ConnectTimeout=5 alpha@127.0.0.1 sudo poweroff >/dev/null 2>&1 || true
        local seconds=60
        while running && [ "$seconds" -gt 0 ]; do
            sleep 1
            seconds=$((seconds - 1))
        done
        if running; then kill "$(cat "$pidfile")"; fi
    fi
    rm -f "$pidfile"
    echo "the VM is down"
}

reset() {
    down
    rm -f "$disk" "$seed" "$cache/user-data" "$cache/meta-data" "$cache/console.log"
    echo "the VM is bare again: make -C dev/vm up"
}

case "${1:-}" in
release) release ;;
release-down) release_down && echo "the mock release is down" ;;
up) up ;;
down) down ;;
ssh) ssh_in ;;
reset) reset ;;
*)
    echo "usage: vm.sh release|release-down|up|down|ssh|reset" >&2
    exit 2
    ;;
esac
