import os
import secrets
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ValidationError

from jatszoter_web.settings import b64url_decode, b64url_encode

SESSION_COOKIE = "jatszoter_session"
CSRF_COOKIE = "jatszoter_csrf"
LINK_COOKIE = "jatszoter_link"
FLASH_COOKIE = "jatszoter_flash"
NONCE_BYTES = 12


class Operator(BaseModel):
    """The plain text of the session cookie."""

    user_id: uuid.UUID
    name: str
    credential: str
    expires_at: int
    key: str
    csrf: str


def new_token() -> str:
    return secrets.token_urlsafe(32)


def seal(plain: bytes, keys: dict[str, bytes], kid: str, purpose: str) -> str:
    """`<kid>.<base64url of nonce and ciphertext>`, AES-256-GCM, `purpose` as associated data."""
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(keys[kid]).encrypt(nonce, plain, purpose.encode())
    return f"{kid}.{b64url_encode(nonce + sealed)}"


def unseal(value: str, keys: dict[str, bytes], purpose: str) -> bytes | None:
    kid, dot, blob = value.partition(".")
    key = keys.get(kid)
    if not dot or key is None:
        return None
    try:
        raw = b64url_decode(blob)
        plain: bytes = AESGCM(key).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], purpose.encode())
    except (InvalidTag, ValueError):
        return None
    return plain


def seal_operator(operator: Operator, keys: dict[str, bytes], kid: str) -> str:
    return seal(operator.model_dump_json().encode(), keys, kid, SESSION_COOKIE)


def unseal_operator(value: str | None, keys: dict[str, bytes]) -> Operator | None:
    plain = unseal(value, keys, SESSION_COOKIE) if value else None
    if plain is None:
        return None
    try:
        return Operator.model_validate_json(plain)
    except ValidationError:
        return None


def set_cookie_header(name: str, value: str | None) -> str:
    """A `Set-Cookie` value, HttpOnly, Secure, SameSite=Strict on `/`; None clears the cookie."""
    parts = [f"{name}={value or ''}", "Path=/", "HttpOnly", "Secure", "SameSite=Strict"]
    if value is None:
        parts += ["Max-Age=0", "expires=Thu, 01 Jan 1970 00:00:00 GMT"]
    return "; ".join(parts)
