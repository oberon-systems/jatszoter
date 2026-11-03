import asyncio
import gzip
import os
import tarfile
import time
import tracemalloc
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jatszoter_api import bundles
from jatszoter_api.errors import InvalidBundleError
from jatszoter_api.file import DISK_BASE, File
from jatszoter_api.middleware import BODY_LIMIT
from jatszoter_api.models import Image, Role, Scenario
from jatszoter_api.routes import scenarios
from jatszoter_api.routes.scenarios import ADDRESS_REFUSED, _fetch, fetch_transport
from jatszoter_api.settings import Settings

SCENARIOS = "/api/v1/scenarios"
BUNDLE_URL = "https://example.com/nginx-502.tar.gz"
DOCUMENT = Path(__file__).resolve().parents[3] / "scenarios" / "nginx-502" / "scenario.yaml"
Pack = Callable[..., bytes]


def member(name: str, kind: bytes = tarfile.REGTYPE) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.type = kind
    if kind == tarfile.SYMTYPE:
        info.linkname = "/etc/passwd"
        return info, b""
    info.size = 5
    return info, b"alpha"


def sent(body: bytes, **headers: str) -> httpx.Response:
    """A response still to be read, as the network gives one; content= arrives consumed."""
    return httpx.Response(200, headers=headers, stream=httpx.ByteStream(body))


class Trickle(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(1000):
            yield b"\0"
            await asyncio.sleep(0.01)


async def stall(_: httpx.Request) -> httpx.Response:
    await asyncio.sleep(10)
    return sent(b"")


async def trickle(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, stream=Trickle())


@pytest.fixture
def served(api: TestClient) -> Iterator[dict[str, httpx.Response]]:
    """Responses by URL, answering the bundle fetch in place of the network."""
    responses: dict[str, httpx.Response] = {}

    def answer(request: httpx.Request) -> httpx.Response:
        return responses.get(str(request.url), httpx.Response(404))

    transport = httpx.MockTransport(answer)
    overrides = api.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[fetch_transport] = lambda: transport
    yield responses
    overrides.clear()


@pytest.fixture(autouse=True)
def rocky(session: Session) -> None:
    image = Image(
        session=session,
        name="rocky-9",
        url="https://example.com/rocky-9.qcow2",
        digest="a" * 64,
    )
    image.save()


@pytest.fixture
def small_limit(settings: Settings) -> int:
    """The app reads its settings per request, so lowering the limit here takes effect."""
    settings.scenario_bundle_limit_bytes = 1024
    return settings.scenario_bundle_limit_bytes


def upload(operator: Any, bundle: bytes) -> httpx.Response:
    response: httpx.Response = operator.request(
        "POST", SCENARIOS, files={"bundle": ("nginx-502.tar.gz", bundle, "application/gzip")}
    )
    return response


def fetch(operator: Any, url: str = BUNDLE_URL) -> httpx.Response:
    response: httpx.Response = operator.request("POST", SCENARIOS, json={"url": url})
    return response


def stored(tmp_path: Path) -> list[Path]:
    return sorted(path for path in tmp_path.joinpath("data").rglob("*") if path.is_file())


def bundle_path(tmp_path: Path, version: int = 2) -> Path:
    return tmp_path / "data" / "storage" / "scenarios" / "nginx-502" / f"{version}.tar.gz"


def assert_nothing_kept(tmp_path: Path, session: Session) -> None:
    assert stored(tmp_path) == []
    assert Scenario._load(session) == []


def test_an_upload_registers_the_sample(operator: Any, pack: Pack, tmp_path: Path) -> None:
    bundle = pack()

    response = upload(operator, bundle)

    assert response.status_code == 201
    assert response.json() == {
        "id": response.json()["id"],
        "slug": "nginx-502",
        "version": 2,
        "title": "Nginx answers 502",
        "image": "rocky-9",
    }
    assert bundle_path(tmp_path).read_bytes() == bundle


def test_a_url_registers_the_sample(
    operator: Any, pack: Pack, served: dict[str, httpx.Response], session: Session
) -> None:
    served[BUNDLE_URL] = sent(pack())

    response = fetch(operator)

    scenario = Scenario(session=session).load(slug="nginx-502", version=2)
    assert response.status_code == 201
    assert scenario.bundle_url == f"{DISK_BASE}scenarios/nginx-502/2.tar.gz"
    assert scenario.document["check"] == "check.sh"


def test_the_same_version_again_conflicts_and_keeps_the_bundle(
    operator: Any, pack: Pack, tmp_path: Path
) -> None:
    bundle = pack()
    upload(operator, bundle)

    again = upload(operator, pack(mtime=1_700_000_000, top=None))

    assert again.status_code == 409
    assert stored(tmp_path) == [bundle_path(tmp_path)]
    assert bundle_path(tmp_path).read_bytes() == bundle


def test_a_new_version_registers_beside_the_old(operator: Any, pack: Pack, tmp_path: Path) -> None:
    second = DOCUMENT.read_text().replace("version: 2\n", "version: 3\n")
    upload(operator, pack())

    response = upload(operator, pack(changes={"scenario.yaml": second.encode()}))

    listed = operator.request("GET", SCENARIOS).json()
    assert response.status_code == 201
    assert {(item["slug"], item["version"]) for item in listed} == {
        ("nginx-502", 2),
        ("nginx-502", 3),
    }
    assert stored(tmp_path) == [bundle_path(tmp_path), bundle_path(tmp_path, 3)]


def test_the_same_version_with_changed_content_conflicts(
    operator: Any, pack: Pack, tmp_path: Path
) -> None:
    bundle = pack()
    upload(operator, bundle)

    response = upload(operator, pack(changes={"check.sh": b"exit 0\n"}))

    assert response.status_code == 409
    assert bundle_path(tmp_path).read_bytes() == bundle


@pytest.mark.parametrize(
    ("change", "code", "message"),
    [
        (
            {"extra": [member("nginx-502/link", tarfile.SYMTYPE)]},
            "bundle-member-not-file",
            "not a regular file",
        ),
        ({"extra": [member("../x")]}, "bundle-member-climbs-out", "'\\.\\.'"),
        ({"extra": [member("other/x")]}, "bundle-layout-invalid", "more than one top directory"),
        ({"changes": {"break.sh": None}}, "bundle-file-missing", "steps.break names 'break.sh'"),
        (
            {"changes": {"scenario.yaml": b"id: nginx-502\n"}},
            "scenario-invalid",
            "scenario.yaml at \\$: ",
        ),
    ],
)
def test_an_invalid_bundle_is_refused_and_leaves_no_trace(
    operator: Any,
    pack: Pack,
    tmp_path: Path,
    session: Session,
    refused: Any,
    change: dict[str, Any],
    code: str,
    message: str,
) -> None:
    response = upload(operator, pack(**change))

    assert response.status_code == 422
    refused(response, code, message)
    assert_nothing_kept(tmp_path, session)


def test_an_unknown_image_is_refused(
    operator: Any, pack: Pack, tmp_path: Path, session: Session, refused: Any
) -> None:
    document = DOCUMENT.read_text().replace("image: rocky-9", "image: alpha")

    response = upload(operator, pack(changes={"scenario.yaml": document.encode()}))

    assert response.status_code == 422
    refused(response, "image-not-registered", "image alpha is not registered")
    assert_nothing_kept(tmp_path, session)


def test_an_oversized_upload_is_refused(
    operator: Any, pack: Pack, small_limit: int, tmp_path: Path, session: Session
) -> None:
    bundle = pack()
    assert len(bundle) > small_limit

    response = upload(operator, bundle)

    assert response.status_code == 413
    assert_nothing_kept(tmp_path, session)


def test_an_oversized_download_is_refused(
    operator: Any,
    pack: Pack,
    served: dict[str, httpx.Response],
    small_limit: int,
    tmp_path: Path,
    session: Session,
) -> None:
    served[BUNDLE_URL] = sent(pack())

    response = fetch(operator)

    assert response.status_code == 413
    assert_nothing_kept(tmp_path, session)


def test_a_bundle_over_the_small_body_limit_still_uploads(operator: Any, pack: Pack) -> None:
    bundle = pack(changes={"filler.bin": os.urandom(2 * BODY_LIMIT)})
    assert len(bundle) > BODY_LIMIT

    assert upload(operator, bundle).status_code == 201


def test_a_body_past_the_upload_limit_is_refused_by_size(
    operator: Any, settings: Settings, tmp_path: Path, session: Session, refused: Any
) -> None:
    bundle = bytes(settings.scenario_bundle_limit_bytes + BODY_LIMIT + 1)

    response = upload(operator, bundle)

    assert response.status_code == 413
    refused(response, "too-large", "the request body is too large")
    assert_nothing_kept(tmp_path, session)


def test_another_route_keeps_the_small_limit(operator: Any) -> None:
    response = operator.request("POST", "/api/v1/images", files={"alpha": bytes(2 * BODY_LIMIT)})

    assert response.status_code == 413


@pytest.mark.parametrize("encoding", ["gzip", "gzip, gzip", "br"])
def test_an_encoded_download_is_refused(
    operator: Any,
    pack: Pack,
    served: dict[str, httpx.Response],
    tmp_path: Path,
    session: Session,
    refused: Any,
    encoding: str,
) -> None:
    served[BUNDLE_URL] = sent(pack(), **{"Content-Encoding": encoding})

    response = fetch(operator)

    assert response.status_code == 422
    refused(response, "bundle-url-encoded", "Content-Encoding")
    assert_nothing_kept(tmp_path, session)


def test_a_nested_encoding_is_never_inflated() -> None:
    limit = 10 * 1024 * 1024
    bomb = gzip.compress(gzip.compress(bytes(8 * limit), 1), 1)
    transport = httpx.MockTransport(lambda _: sent(bomb, **{"Content-Encoding": "gzip, gzip"}))

    tracemalloc.start()
    try:
        with pytest.raises(InvalidBundleError):
            asyncio.run(_fetch(BUNDLE_URL, transport, limit))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < limit


def test_the_fetch_asks_for_the_bytes_as_stored() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return sent(b"alpha")

    fetched = asyncio.run(_fetch(BUNDLE_URL, httpx.MockTransport(answer), 1024))

    assert fetched == b"alpha"
    assert seen[0].headers["accept-encoding"] == "identity"


@pytest.mark.parametrize("handler", [stall, trickle])
def test_a_slow_server_is_cut_off_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[..., Any]
) -> None:
    monkeypatch.setattr(scenarios, "FETCH_DEADLINE", 0.2)
    started = time.monotonic()

    with pytest.raises(InvalidBundleError, match="did not finish"):
        asyncio.run(_fetch(BUNDLE_URL, httpx.MockTransport(handler), 1024 * 1024))

    assert time.monotonic() - started < 5


def test_a_redirect_is_not_followed(
    operator: Any,
    pack: Pack,
    served: dict[str, httpx.Response],
    tmp_path: Path,
    session: Session,
    refused: Any,
) -> None:
    target = "https://example.com/moved.tar.gz"
    served[BUNDLE_URL] = httpx.Response(302, headers={"Location": target})
    served[target] = httpx.Response(200, content=pack())

    response = fetch(operator)

    assert response.status_code == 422
    refused(response, "bundle-url-redirects", "redirects")
    assert_nothing_kept(tmp_path, session)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/nginx-502.tar.gz",
        "http://192.0.2.1/nginx-502.tar.gz",
        "ftp://example.com/nginx-502.tar.gz",
        "file:///srv/nginx-502.tar.gz",
    ],
)
def test_a_url_the_api_may_not_fetch_is_refused(
    operator: Any, served: dict[str, httpx.Response], url: str
) -> None:
    response = fetch(operator, url)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "https://10.0.0.1/x",
        "https://169.254.169.254/x",
        "https://[fe80::1]/x",
        "https://[::ffff:127.0.0.1]/x",
        "https://localhost/x",
        "http://127.0.0.1:1/x",
    ],
)
def test_an_internal_address_is_refused_before_any_connection(
    operator: Any, refused: Any, url: str
) -> None:
    response = fetch(operator, url)

    assert response.status_code == 422
    refused(response, "bundle-fetch-refused", ADDRESS_REFUSED)


async def _status_503(_: httpx.Request) -> httpx.Response:
    return httpx.Response(503)


async def _connect_timeout(_: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("alpha")


async def _connect_error(_: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("alpha")


@pytest.mark.parametrize(
    ("handler", "cause"),
    [
        (_status_503, "status 503"),
        (_connect_timeout, "ConnectTimeout"),
        (_connect_error, "ConnectError"),
    ],
)
def test_every_failure_answers_one_code_and_the_log_tells_them_apart(
    handler: Callable[..., Any], cause: str
) -> None:
    transport = httpx.MockTransport(handler)

    with pytest.raises(InvalidBundleError) as refused:
        asyncio.run(_fetch(f"{BUNDLE_URL}?token=beta", transport, 1024))

    logged = str(refused.value)
    assert refused.value.code == "bundle-fetch-failed"
    assert cause in logged
    assert "example.com" in logged
    assert "token" not in logged


def test_a_proxy_from_the_environment_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://192.0.2.1:1")
    transport = httpx.MockTransport(lambda _: sent(b"alpha"))

    assert asyncio.run(_fetch(BUNDLE_URL, transport, 1024)) == b"alpha"


def test_http_to_loopback_is_fetched(
    operator: Any, pack: Pack, served: dict[str, httpx.Response]
) -> None:
    served["http://127.0.0.1:8081/nginx-502.tar.gz"] = sent(pack())

    response = fetch(operator, "http://127.0.0.1:8081/nginx-502.tar.gz")

    assert response.status_code == 201


def test_a_failed_save_writes_no_bundle(
    session: Session,
    settings: Settings,
    pack: Pack,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_: Scenario) -> None:
        raise RuntimeError("alpha")

    bundle = pack()
    checked = bundles.read(bundle, settings.scenario_bundle_limit_bytes)
    scenario = Scenario(
        session=session,
        slug="nginx-502",
        version=1,
        document=checked.document,
        digest=checked.digest,
    )
    monkeypatch.setattr(Scenario, "_save", fail)

    with pytest.raises(RuntimeError):
        scenario.save(File(scenario.bundle_path, bundle))

    assert_nothing_kept(tmp_path, session)


def test_only_an_operator_reaches_scenarios(
    sign_in: Callable[..., Any], api: TestClient, pack: Pack
) -> None:
    viewer = sign_in(name="beta", role=Role.VIEWER)

    assert upload(viewer, pack()).status_code == 403
    assert viewer.request("GET", SCENARIOS).status_code == 403
    assert api.get(SCENARIOS).status_code == 401


def test_a_viewer_makes_the_api_fetch_nothing(sign_in: Callable[..., Any], api: TestClient) -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404)

    transport = httpx.MockTransport(answer)
    overrides = api.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[fetch_transport] = lambda: transport
    viewer = sign_in(name="beta", role=Role.VIEWER)

    response = fetch(viewer)
    overrides.clear()

    assert response.status_code == 403
    assert seen == []
