from collections.abc import Callable
from typing import Any

import pytest
from sqlmodel import Session

from jatszoter_api.errors import ConflictError
from jatszoter_api.models import Image, Role

URL = "https://example.com/debian-12.qcow2"
DIGEST = "a" * 64
IMAGES = "/api/v1/images"


def body(**change: str) -> dict[str, str]:
    return {"name": "debian-12", "url": URL, "digest": DIGEST, **change}


def test_saving_stores_the_image(session: Session) -> None:
    image = Image(session=session, name="debian-12", url=URL, digest=DIGEST)

    image.save()

    assert image.load(name="debian-12").id == image.id
    assert [stored.name for stored in Image._load(session)] == ["debian-12"]


@pytest.mark.parametrize("change", [{}, {"url": URL + ".new"}, {"digest": "b" * 64}])
def test_the_same_name_again_conflicts(session: Session, change: dict[str, str]) -> None:
    Image(session=session, name="debian-12", url=URL, digest=DIGEST).save()
    other = Image(session=session, **{"name": "debian-12", "url": URL, "digest": DIGEST, **change})

    with pytest.raises(ConflictError):
        other.save()
    assert len(Image._load(session)) == 1


def test_an_unknown_image_does_not_load(session: Session) -> None:
    assert Image._load(session, name="alpha") == []


def test_an_operator_registers_and_lists_images(operator: Any) -> None:
    created = operator.request("POST", IMAGES, json=body())
    other = operator.request("POST", IMAGES, json=body(name="beta", url="http://192.0.2.1/b"))
    listed = operator.request("GET", IMAGES)

    assert created.status_code == 201
    assert other.status_code == 201
    assert {image["name"] for image in listed.json()} == {"beta", "debian-12"}
    assert created.json() in listed.json()
    assert created.json() == {"id": created.json()["id"], **body()}


@pytest.mark.parametrize("change", [{}, {"digest": "b" * 64}])
def test_a_registered_name_conflicts(operator: Any, change: dict[str, str]) -> None:
    operator.request("POST", IMAGES, json=body())

    response = operator.request("POST", IMAGES, json=body(**change))

    assert response.status_code == 409


@pytest.mark.parametrize(
    "change",
    [
        {"name": "Debian"},
        {"name": "-alpha"},
        {"url": "ftp://example.com/alpha.qcow2"},
        {"url": "file:///srv/alpha.qcow2"},
        {"url": "alpha"},
        {"digest": "A" * 64},
        {"digest": "a" * 63},
    ],
)
def test_a_bad_name_url_or_digest_is_unprocessable(operator: Any, change: dict[str, str]) -> None:
    response = operator.request("POST", IMAGES, json=body(**change))

    assert response.status_code == 422


def test_only_an_operator_reaches_images(sign_in: Callable[..., Any], api: Any) -> None:
    viewer = sign_in(name="beta", role=Role.VIEWER)

    assert viewer.request("POST", IMAGES, json=body()).status_code == 403
    assert viewer.request("GET", IMAGES).status_code == 403
    assert api.get(IMAGES).status_code == 401
