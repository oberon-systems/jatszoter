FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS build

COPY packages/web /src/web
# PyYAML has no wheel for 3.15 yet: it alone builds, from its hash-checked sdist.
RUN pip install --no-cache-dir --require-hashes --only-binary :all: -r /src/web/requirements-build.txt \
    && pip install --no-cache-dir --prefix=/install --require-hashes --only-binary :all: \
        --no-binary pyyaml --no-build-isolation -r /src/web/requirements.txt \
    && pip wheel --no-cache-dir --no-deps --no-build-isolation -w /wheels /src/web \
    && pip install --no-cache-dir --prefix=/install --no-deps --no-index /wheels/*.whl

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

RUN useradd --system --create-home --home-dir /var/lib/jatszoter jatszoter

COPY --from=build /install /usr/local
# Fails the build when pyproject.toml names a dependency the lock lacks.
# pip then goes: the runtime never installs, and its vendored copies only add advisories.
RUN pip check && pip uninstall --yes --no-cache-dir pip

USER jatszoter
WORKDIR /var/lib/jatszoter
EXPOSE 8000
CMD ["uvicorn", "--factory", "jatszoter_web.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
