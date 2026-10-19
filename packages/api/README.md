# jatszoter-api

The control plane of jatszoter, a self-hosted training and exam stand for
Linux troubleshooting. It is a [FastAPI](https://fastapi.tiangolo.com/) app
that keeps agents, leases, scenarios, deployments, links, recordings and
checks, and applies its database migrations when it starts.

## Install

```bash
pip install "jatszoter-api[server]"
```

## Run

```bash
export JATSZOTER_DATABASE_DSN="sqlite:///jatszoter.db"
export JATSZOTER_MASTER_KEYS='{"alpha": "<64 hex characters>"}'
export JATSZOTER_MASTER_KID=alpha
uvicorn --factory jatszoter_api.app:create_app --host 0.0.0.0 --port 8080
```

## Container

```bash
docker run --rm -p 8080:8080 \
    -e JATSZOTER_DATABASE_DSN -e JATSZOTER_MASTER_KEYS -e JATSZOTER_MASTER_KID \
    ghcr.io/<owner>/jatszoter-api:latest
```

The architecture and every setting are described in `docs/project/` of the
repository.
