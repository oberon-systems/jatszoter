#!/bin/sh
# Runs in the inspector VM, which has no network. The disk it reads was written by root in the guest.
device() {
    for block in /sys/block/vd*; do
        if [ "$(cat "$block/serial" 2>/dev/null)" = "$1" ]; then
            echo "/dev/${block##*/}"
        fi
    done
}

mdev -s 2>/dev/null
for module in xfs ext4; do
    modprobe "$module" 2>/dev/null
done
disk="$(device jatszoter-disk)"
answers="$(device jatszoter-answers)"
root=/mnt/jatszoter-disk
mkdir -p "$root"
found=
for part in "$disk"[0-9]* "$disk"; do
    [ -b "$part" ] || continue
    mount -t xfs -o ro,norecovery,nouuid "$part" "$root" 2>/dev/null ||
        mount -t ext4 -o ro,noload "$part" "$root" 2>/dev/null ||
        continue
    if [ -e "$root/etc/os-release" ]; then
        found=1
        break
    fi
    umount "$root"
done

ask() {
    : ASSERTIONS
    echo end
}

if [ -n "$found" ] && [ -n "$answers" ]; then
    ask >/tmp/jatszoter-answers
    cat /tmp/jatszoter-answers >"$answers"
    sync
fi
poweroff
