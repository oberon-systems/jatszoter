#!/bin/sh
set -eu

sed -i 's/8081/8018/' /etc/systemd/system/app.service
systemctl daemon-reload
systemctl restart app.service
