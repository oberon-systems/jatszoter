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

# The stock nginx.conf carries its own default_server on port 80.
cat >/etc/nginx/nginx.conf <<'MAIN'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log;
pid /run/nginx.pid;
include /usr/share/nginx/modules/*.conf;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log /var/log/nginx/access.log;
    include /etc/nginx/conf.d/*.conf;
}
MAIN

cat >/etc/nginx/conf.d/app.conf <<'SITE'
server {
    listen 80 default_server;
    location / {
        proxy_pass http://127.0.0.1:8081;
    }
}
SITE

setsebool -P httpd_can_network_connect 1
systemctl daemon-reload
systemctl enable --now app.service nginx
