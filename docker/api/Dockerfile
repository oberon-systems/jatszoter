FROM python:3.15-slim

RUN useradd --system --create-home --home-dir /var/lib/jatszoter jatszoter

COPY packages/api /src/api
RUN pip install --no-cache-dir "/src/api[server]" && rm -rf /src

USER jatszoter
WORKDIR /var/lib/jatszoter
EXPOSE 8080
CMD ["uvicorn", "--factory", "jatszoter_api.app:create_app", "--host", "0.0.0.0", "--port", "8080"]
