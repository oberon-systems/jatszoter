import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from jatszoter_web.catalog import BUNDLE_LIMIT
from jatszoter_web.cookies import Operator

Answer = Callable[..., None]
SignIn = Callable[..., Operator]

HTMX = {"hx-request": "true"}
BUNDLE_URL = "https://example.com/web-basics.tar.gz"


def posted(calls: list[httpx.Request]) -> list[httpx.Request]:
    return [call for call in calls if call.method == "POST"]


@pytest.mark.usefixtures("stand")
def test_the_tabs_list_scenarios_and_images(client: TestClient, sign_in: SignIn) -> None:
    sign_in()

    scenarios = client.get("/scenarios")
    images = client.get("/images")

    assert scenarios.status_code == images.status_code == 200
    for text in ("web-basics", "Web basics", "alpha-base", "1 scenario version"):
        assert text in scenarios.text
    assert 'hx-get="/scenarios/new"' in scenarios.text
    for text in ("https://example.com/alpha-base.qcow2", "sha256:" + "0" * 64, "1 image"):
        assert text in images.text
    assert 'hx-get="/images/new"' in images.text


@pytest.mark.usefixtures("stand")
def test_empty_tabs_show_their_empty_boards(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/scenarios", [])
    answer("GET", "/api/v1/images", [])

    assert "No scenarios yet" in client.get("/scenarios").text
    assert "No images yet" in client.get("/images").text


@pytest.mark.usefixtures("stand")
def test_a_refused_tab_shows_the_refusal(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/images", {"detail": "images are not listed"}, 422)

    page = client.get("/images")

    assert page.status_code == 422
    assert "images are not listed (422)" in page.text


def test_the_register_dialogs_are_fragments(client: TestClient, sign_in: SignIn) -> None:
    who = sign_in()

    for path, name in (("/scenarios/new", "bundle"), ("/images/new", "digest")):
        dialog = client.get(path, headers=HTMX)
        assert dialog.status_code == 200
        assert "<html" not in dialog.text
        assert f'name="{name}"' in dialog.text
        assert f'value="{who.csrf}"' in dialog.text


@pytest.mark.usefixtures("stand")
def test_a_scenario_registered_by_url_lands_on_the_list(
    client: TestClient, sign_in: SignIn, answer: Answer, calls: list[httpx.Request]
) -> None:
    who = sign_in()
    answer("POST", "/api/v1/scenarios", {"slug": "web-basics", "version": 4}, 201)

    registered = client.post(
        "/scenarios", data={"csrf_token": who.csrf, "url": BUNDLE_URL}, headers=HTMX
    )
    shown = client.get("/scenarios")

    assert registered.status_code == 204
    assert registered.headers["hx-redirect"] == "/scenarios"
    assert json.loads(posted(calls)[0].content) == {"url": BUNDLE_URL}
    assert "Scenario registered" in shown.text
    assert "web-basics v4 can be deployed now." in shown.text


@pytest.mark.usefixtures("stand")
def test_an_uploaded_bundle_is_relayed_as_multipart(
    client: TestClient,
    sign_in: SignIn,
    answer: Answer,
    calls: list[httpx.Request],
    signed_by: Callable[[httpx.Request, str], bool],
) -> None:
    who = sign_in()
    answer("POST", "/api/v1/scenarios", {"slug": "web-basics", "version": 4}, 201)

    registered = client.post(
        "/scenarios",
        data={"csrf_token": who.csrf, "url": ""},
        files={"bundle": ("web-basics.tar.gz", b"bundle-bytes-alpha")},
    )

    relayed = posted(calls)[0]
    assert registered.status_code == 303
    assert relayed.headers["content-type"].startswith("multipart/form-data")
    assert b'filename="web-basics.tar.gz"' in relayed.content
    assert b"bundle-bytes-alpha" in relayed.content
    assert signed_by(relayed, who.key)


def test_a_bundle_over_the_limit_never_reaches_the_api(
    client: TestClient, sign_in: SignIn, calls: list[httpx.Request]
) -> None:
    who = sign_in()
    error = f'<span class="field-error">the bundle is larger than {BUNDLE_LIMIT} bytes'

    dialog = client.post(
        "/scenarios",
        data={"csrf_token": who.csrf},
        files={"bundle": ("web-basics.tar.gz", b"0" * (BUNDLE_LIMIT + 1))},
        headers=HTMX,
    )

    assert dialog.status_code == 413
    assert error in dialog.text
    assert posted(calls) == []


@pytest.mark.usefixtures("stand")
def test_a_refused_scenario_shows_the_refusal_next_to_its_field(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    who = sign_in()
    detail = [{"loc": ["body", "url"], "msg": "the url is not https"}]
    answer("POST", "/api/v1/scenarios", {"detail": detail}, 422)

    dialog = client.post(
        "/scenarios", data={"csrf_token": who.csrf, "url": "http://example.com/a"}, headers=HTMX
    )

    assert dialog.status_code == 422
    assert dialog.text.lstrip().startswith("<dialog")
    assert '<span class="field-error">the url is not https</span>' in dialog.text
    assert "the url is not https (422)" in dialog.text
    assert 'value="http://example.com/a"' in dialog.text


@pytest.mark.usefixtures("stand")
def test_an_image_registered_lands_on_its_tab(
    client: TestClient,
    sign_in: SignIn,
    stand: dict[str, Any],
    answer: Answer,
    calls: list[httpx.Request],
) -> None:
    who = sign_in()
    image = stand["image"]
    answer("POST", "/api/v1/images", image, 201)
    form = {"csrf_token": who.csrf, "name": image["name"], "url": image["url"]}

    registered = client.post("/images", data={**form, "digest": image["digest"]})
    shown = client.get("/images")

    assert registered.status_code == 303
    assert registered.headers["location"] == "/images"
    assert json.loads(posted(calls)[0].content) == {
        "name": image["name"],
        "url": image["url"],
        "digest": image["digest"],
    }
    assert "alpha-base can be named by scenarios now." in shown.text


@pytest.mark.usefixtures("stand")
def test_a_refused_image_keeps_the_dialog_open(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    who = sign_in()
    answer("POST", "/api/v1/images", {"detail": "image alpha-base is registered already"}, 409)

    dialog = client.post(
        "/images", data={"csrf_token": who.csrf, "name": "alpha-base"}, headers=HTMX
    )

    assert dialog.status_code == 409
    assert dialog.text.lstrip().startswith("<dialog")
    assert "image alpha-base is registered already (409)" in dialog.text
    assert 'value="alpha-base"' in dialog.text
