import base64
import binascii
from typing import Self

from pydantic import AnyHttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AES_KEY_BYTES = 32


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JATSZOTER_WEB_")

    api_url: AnyHttpUrl
    cookie_keys: dict[str, str]
    cookie_kid: str

    @model_validator(mode="after")
    def _keys(self) -> Self:
        if self.cookie_kid not in self.cookie_keys:
            raise ValueError("cookie_kid names no key of cookie_keys")
        for kid in self.cookie_keys:
            if not kid or "." in kid:
                raise ValueError("a cookie kid is empty or holds a dot")
        self.aes_keys()
        return self

    def aes_keys(self) -> dict[str, bytes]:
        """Every cookie key by kid, decoded; a key that is not 32 bytes of base64url is refused."""
        keys = {}
        for kid, value in self.cookie_keys.items():
            try:
                key = b64url_decode(value)
            except (binascii.Error, ValueError):
                raise ValueError(f"cookie key {kid} is not base64url") from None
            if len(key) != AES_KEY_BYTES:
                raise ValueError(f"cookie key {kid} is not {AES_KEY_BYTES} bytes")
            keys[kid] = key
        return keys

    @property
    def api_base(self) -> str:
        return str(self.api_url).rstrip("/")
