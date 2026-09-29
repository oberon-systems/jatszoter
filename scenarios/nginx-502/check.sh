#!/bin/sh
# Exit 0 means solved; the first stdout line is shown to the trainee.
set -u

if ! curl -fsS http://127.0.0.1/ | grep -q 'hello from app'; then
    echo 'nginx still does not serve the app page'
    exit 1
fi
if ! grep -q 'proxy_pass http://127.0.0.1:8081;' /etc/nginx/sites-available/default; then
    echo 'the nginx site config was changed'
    exit 1
fi
echo 'solved'
