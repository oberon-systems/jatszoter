# 10 - Reverse proxy

No stand needs a reverse proxy, but a production install should put
[nginx](https://nginx.org/) in front of `web` and `api` and terminate TLS
there. Neither image serves TLS itself, and `web` sets every cookie
`Secure`, so a browser that reaches it over plain HTTP never sends them
back. This runbook sets nginx up so that TLS, the client's address and the
security checks of [06](06-security.md) all keep working.

- [Layout](#layout)
- [Requirements](#requirements)
- [Trust the proxy](#trust-the-proxy)
- [Configure nginx](#configure-nginx)
- [Check the setup](#check-the-setup)
- [Known issues](#known-issues)

## Layout

Browsers reach `web` through nginx, agents reach `api` through nginx, and
`web` reaches `api` directly on the internal network:

```text
browser --https--> nginx 192.0.2.10 --http--> web 192.0.2.20:8000
                                                  |
                                                  | http, JATSZOTER_WEB_API_URL
                                                  v
agent ----https--> nginx 192.0.2.10 --http--> api 192.0.2.30:8080
```

- `web.example.com` serves the web UI, `api.example.com` the agent API.
- `JATSZOTER_WEB_API_URL` points at `api` itself, `http://192.0.2.30:8080`,
  not at nginx. `web` then reaches `api` from one stable address, which
  `api` trusts.
- `web` and `api` listen on addresses nginx reaches and nobody else does:
  `127.0.0.1` on one host, an internal network otherwise.

## Requirements

- nginx 1.25.1 or newer, for the `http2` directive.
- A certificate and key for each name, for example from
  [Let's Encrypt](https://letsencrypt.org/) through
  [certbot](https://certbot.eff.org/).
- The address nginx connects to `web` and `api` from, and the address
  `web` connects to `api` from. Below they are `192.0.2.10` and
  `192.0.2.20`.

## Trust the proxy

`web` and `api` run under [uvicorn](https://www.uvicorn.org/), which takes
`X-Forwarded-For` and `X-Forwarded-Proto` only from a peer listed in its
`FORWARDED_ALLOW_IPS` environment variable, `127.0.0.1` by default. From
any other peer the headers are ignored, so a client cannot claim another
address by sending them itself.

| Service | `FORWARDED_ALLOW_IPS`       | Why                                                       |
|---------|-----------------------------|-----------------------------------------------------------|
| `web`   | `192.0.2.10`                | sees the browser's address and `https` from nginx         |
| `api`   | `192.0.2.10,192.0.2.20`     | sees the agent's address from nginx, the browser's from `web` |

Set them in the environment of each service:

```bash
FORWARDED_ALLOW_IPS=192.0.2.10
```

```bash
FORWARDED_ALLOW_IPS=192.0.2.10,192.0.2.20
```

- `web` sends the browser's address to `api` in `X-Forwarded-For` when it
  logs an operator in. The API counts login failures per that address,
  see [06](06-security.md#credentials).
- `api` counts agent registrations per the address it sees, see
  [01](01-architecture.md#agent-protocol).
- uvicorn reads `X-Forwarded-For` from the right and takes the first
  address it does not trust, so a value the client sent ahead of nginx's
  is never used.
- A network such as `192.0.2.0/24` is accepted too. Never use `*`: any
  client could then set its own address.

## Configure nginx

One file, for example `/etc/nginx/conf.d/jatszoter.conf`. TLS follows the
"intermediate" profile of the
[Mozilla SSL Configuration Generator](https://ssl-config.mozilla.org/).

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    listen [::]:80;
    server_name web.example.com api.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name web.example.com;

    ssl_certificate     /etc/nginx/tls/web.example.com/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/web.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;
    ssl_session_timeout 1d;
    ssl_session_cache   shared:jatszoter:10m;
    ssl_session_tickets off;

    add_header Strict-Transport-Security "max-age=63072000" always;
    client_max_body_size 11m;

    location / {
        proxy_pass http://192.0.2.20:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        $connection_upgrade;
        proxy_read_timeout 1h;
        proxy_send_timeout 1h;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name api.example.com;

    ssl_certificate     /etc/nginx/tls/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/api.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;
    ssl_session_timeout 1d;
    ssl_session_cache   shared:jatszoter:10m;
    ssl_session_tickets off;

    add_header Strict-Transport-Security "max-age=63072000" always;
    client_max_body_size 11m;

    location /api/v1/agents/ {
        proxy_pass http://192.0.2.30:8080;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        $connection_upgrade;
        proxy_read_timeout 1h;
        proxy_send_timeout 1h;
    }

    location = /healthz {
        proxy_pass http://192.0.2.30:8080;
    }

    location / {
        return 404;
    }
}
```

- `Host $host` is required. `web` and `api` refuse a request whose
  `Origin` names another host than its `Host`, so without it every login
  and every form gets 403, see [08](08-web-ui.md#csrf).
- `Upgrade` and `Connection` carry the WebSockets: the operator's terminal
  and the trainee's link through `web`, the agent's console through `api`.
  The one-hour timeouts keep an idle terminal open for a whole session.
- `client_max_body_size 11m` lets a scenario bundle through: 10 MiB plus
  the multipart framing. `web` and `api` still cut every body at their own
  limit, see [06](06-security.md#request-bodies).
- The `api.example.com` server passes on what agents call and nothing
  else. Operators log in through `web`, so the API's login is not exposed
  to the internet. Open more locations only for a client that calls the
  API directly.
- `web` already sends its security headers, see
  [08](08-web-ui.md#security-headers). nginx adds HSTS alone, since only it
  knows the site is served over TLS.

Check the file and load it:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

`nginx -t` prints:

```text
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

## Check the setup

Run each step from a machine outside the internal network.

1. TLS and HSTS on both names:

   ```bash
   curl -sI https://web.example.com/login | grep -iE '^(HTTP|strict-transport|set-cookie)'
   curl -s https://api.example.com/healthz
   ```

   `/login` answers `HTTP/2 200` with `strict-transport-security` and a
   `set-cookie: jatszoter_csrf=...; Secure` line; `/healthz` prints
   `{"status":"ok"}`.

2. The API is closed apart from the agents:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://api.example.com/api/v1/auth/me
   ```

   It prints `404`.

3. The browser's address reaches the API. Log in eleven times with a wrong
   password for a name that does not exist, so no real user is affected:

   ```bash
   jar="$(mktemp)"
   token="$(curl -s -c "$jar" https://web.example.com/login | grep -o 'name="csrf_token" value="[^"]*"' | cut -d '"' -f 4)"
   for attempt in $(seq 11); do curl -s -o /dev/null -w '%{http_code}\n' -b "$jar" --data-urlencode "csrf_token=$token" --data-urlencode name=probe --data-urlencode password=wrong https://web.example.com/login; done
   ```

   It prints `401` ten times, then `429`. Run the same commands from a
   second machine with another address within 15 minutes: the first line
   must be `401`. A `429` there means every browser counts as one address,
   see [Known issues](#known-issues).

## Known issues

### Every login and form gets 403

- Symptom: the login page loads, but logging in and every form answer
  `403 forbidden`.
- Trigger: nginx forwards its upstream's address as `Host` instead of the
  name the browser used, so the CSRF check sees a foreign `Origin`.
- Fix: add `proxy_set_header Host $host;` to the `location` and reload
  nginx.

### One machine's failures lock everyone out

- Symptom: after ten failed logins from one machine, every browser
  without a device cookie gets `429` for that name.
- Trigger: `FORWARDED_ALLOW_IPS` of `web` misses nginx's address, or that
  of `api` misses `web`'s, so the API counts every browser as one address.
- Fix: set both as in [Trust the proxy](#trust-the-proxy), restart `web`
  and `api`, and repeat step 3 of [Check the setup](#check-the-setup).

### The terminal drops after a minute

- Symptom: an operator's terminal or a trainee's session closes after 60
  seconds without output.
- Trigger: nginx's default `proxy_read_timeout` of 60 seconds closes an
  idle WebSocket.
- Fix: set `proxy_read_timeout 1h;` and `proxy_send_timeout 1h;` in the
  `location`, as above, and reload nginx.
