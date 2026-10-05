import re
import tarfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jatszoter_api import bundles
from jatszoter_api.file import DISK_BASE, File
from jatszoter_api.models import Image, Role, Scenario
from jatszoter_api.routes.scenarios import fetch_transport
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
    served[BUNDLE_URL] = httpx.Response(200, content=pack())

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
    ("change", "message"),
    [
        ({"extra": [member("nginx-502/link", tarfile.SYMTYPE)]}, "not a regular file"),
        ({"extra": [member("../x")]}, "'\\.\\.'"),
        ({"extra": [member("other/x")]}, "more than one top directory"),
        ({"changes": {"break.sh": None}}, "steps.break names 'break.sh'"),
        ({"changes": {"scenario.yaml": b"id: nginx-502\n"}}, "scenario.yaml at \\$: "),
    ],
)
def test_an_invalid_bundle_is_refused_and_leaves_no_trace(
    operator: Any,
    pack: Pack,
    tmp_path: Path,
    session: Session,
    change: dict[str, Any],
    message: str,
) -> None:
    response = upload(operator, pack(**change))

    assert response.status_code == 422
    assert re.search(message, response.json()["detail"])
    assert_nothing_kept(tmp_path, session)


def test_an_unknown_image_is_refused(
    operator: Any, pack: Pack, tmp_path: Path, session: Session
) -> None:
    document = DOCUMENT.read_text().replace("image: rocky-9", "image: alpha")

    response = upload(operator, pack(changes={"scenario.yaml": document.encode()}))

    assert response.status_code == 422
    assert response.json()["detail"] == "image alpha is not registered"
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
    served[BUNDLE_URL] = httpx.Response(200, content=pack())

    response = fetch(operator)

    assert response.status_code == 413
    assert_nothing_kept(tmp_path, session)


def test_a_redirect_is_not_followed(
    operator: Any, pack: Pack, served: dict[str, httpx.Response], tmp_path: Path, session: Session
) -> None:
    target = "https://example.com/moved.tar.gz"
    served[BUNDLE_URL] = httpx.Response(302, headers={"Location": target})
    served[target] = httpx.Response(200, content=pack())

    response = fetch(operator)

    assert response.status_code == 422
    assert "redirects" in response.json()["detail"]
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


def test_http_to_loopback_is_fetched(
    operator: Any, pack: Pack, served: dict[str, httpx.Response]
) -> None:
    served["http://127.0.0.1:8081/nginx-502.tar.gz"] = httpx.Response(200, content=pack())

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
