# jatszoter-web

The operator and trainee UI of jatszoter, a self-hosted training and exam
stand for Linux troubleshooting. It is a [FastAPI](https://fastapi.tiangolo.com/)
app with [Jinja2](https://jinja.palletsprojects.com/) templates,
[HTMX](https://htmx.org/) and [xterm.js](https://xtermjs.org/), and it
reaches the API on behalf of the browser.

## Install

```bash
pip install "jatszoter-web[server]"
```

## Run

```bash
export JATSZOTER_WEB_API_URL="http://192.0.2.10:8080"
export JATSZOTER_WEB_COOKIE_KEYS='{"alpha": "<random token>"}'
export JATSZOTER_WEB_COOKIE_KID=alpha
uvicorn --factory jatszoter_web.app:create_app --host 0.0.0.0 --port 8000
```

## Container

```bash
docker run --rm -p 8000:8000 \
    -e JATSZOTER_WEB_API_URL -e JATSZOTER_WEB_COOKIE_KEYS -e JATSZOTER_WEB_COOKIE_KID \
    ghcr.io/<owner>/jatszoter-web:latest
```

The screens and every setting are described in `docs/project/` of the
repository.
