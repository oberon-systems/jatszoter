#!/bin/sh
set -eu

mkdir -p /srv/app
echo 'hello from app' >/srv/app/index.html
