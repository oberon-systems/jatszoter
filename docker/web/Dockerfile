FROM python:3.15-slim

RUN useradd --system --create-home --home-dir /var/lib/jatszoter jatszoter

COPY packages/web /src/web
RUN pip install --no-cache-dir "/src/web[server]" && rm -rf /src

USER jatszoter
WORKDIR /var/lib/jatszoter
EXPOSE 8000
CMD ["uvicorn", "--factory", "jatszoter_web.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
