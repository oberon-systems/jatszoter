#!/bin/sh
set -eu

cat >/etc/systemd/system/app.service <<'UNIT'
[Unit]
Description=Demo app

[Service]
ExecStart=/usr/bin/python3 -m http.server 8081 --bind 127.0.0.1 --directory /srv/app
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

cat >/etc/nginx/sites-available/default <<'SITE'
server {
    listen 80 default_server;
    location / {
        proxy_pass http://127.0.0.1:8081;
    }
}
SITE

systemctl daemon-reload
systemctl enable --now app.service
systemctl reload nginx
