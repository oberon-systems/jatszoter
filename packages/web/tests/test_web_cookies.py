import uuid

import pytest
from pydantic import ValidationError

from jatszoter_web.cookies import (
    SESSION_COOKIE,
    Operator,
    seal,
    seal_operator,
    set_cookie_header,
    unseal_operator,
)
from jatszoter_web.settings import Settings, b64url_encode

KEYS = {"old": bytes(32), "new": bytes(range(32))}
WHO = Operator(
    user_id=uuid.uuid4(),
    name="alpha",
    credential="m1:1:1700000000",
    expires_at=1700000000,
    key="signing-key-alpha",
    csrf="csrf-token-alpha",
)


def test_a_sealed_operator_opens_with_its_kid() -> None:
    sealed = seal_operator(WHO, KEYS, "new")

    assert sealed.startswith("new.")
    assert unseal_operator(sealed, KEYS) == WHO
    assert unseal_operator(seal_operator(WHO, KEYS, "old"), KEYS) == WHO


def test_the_cookie_holds_no_plain_text() -> None:
    sealed = seal_operator(WHO, KEYS, "new")

    for secret in (WHO.key, WHO.credential, WHO.csrf, WHO.name):
        assert secret not in sealed
    assert seal_operator(WHO, KEYS, "new") != sealed


@pytest.mark.parametrize(
    "value",
    ["", "new", "new.", "gone.AAAA", "new.!!!!", "new.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"],
)
def test_a_value_that_does_not_open_is_no_session(value: str) -> None:
    assert unseal_operator(value, KEYS) is None


def test_a_tampered_cookie_is_no_session() -> None:
    sealed = seal_operator(WHO, KEYS, "new")
    flipped = sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1]

    assert unseal_operator(flipped, KEYS) is None


def test_a_value_sealed_for_another_purpose_is_no_session() -> None:
    sealed = seal(WHO.model_dump_json().encode(), KEYS, "new", "jatszoter_login")

    assert unseal_operator(sealed, KEYS) is None


def test_the_cookie_is_http_only_secure_and_strict() -> None:
    header = set_cookie_header(SESSION_COOKIE, "new.value")

    assert header.startswith(f"{SESSION_COOKIE}=new.value;")
    for attribute in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
        assert attribute in header
    assert "Max-Age" not in header and "expires" not in header


def test_a_cleared_cookie_expires_at_once() -> None:
    assert "Max-Age=0" in set_cookie_header(SESSION_COOKIE, None)


@pytest.mark.parametrize(
    ("keys", "kid"),
    [
        ({"k1": b64url_encode(bytes(32))}, "k2"),
        ({"k1": b64url_encode(bytes(16))}, "k1"),
        ({"k1": "not base64 !"}, "k1"),
        ({"k.1": b64url_encode(bytes(32))}, "k.1"),
    ],
)
def test_settings_refuse_bad_cookie_keys(keys: dict[str, str], kid: str) -> None:
    with pytest.raises(ValidationError):
        Settings(api_url="http://api.example.com", cookie_keys=keys, cookie_kid=kid)
