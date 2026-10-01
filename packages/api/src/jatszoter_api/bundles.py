import functools
import hashlib
import io
import tarfile
import zlib
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from jatszoter_api.errors import InvalidBundleError, TooLargeError

DOCUMENT = "scenario.yaml"
# Room for the tar headers and for scripts that compress well.
UNPACKED_FACTOR = 4


@dataclass(frozen=True)
class Bundle:
    """What registering keeps of a checked bundle: the parsed document and the digest."""

    document: dict[str, Any]
    digest: str


@functools.cache
def _validator() -> Draft202012Validator:
    schema = resources.files("jatszoter_api").joinpath("schemas", "scenario.schema.yaml")
    return Draft202012Validator(yaml.safe_load(schema.read_text()))


def _unpacked(data: bytes, limit: int) -> bytes:
    """The tar inside the gzip, refused once it grows past `limit` bytes."""
    inflater = zlib.decompressobj(wbits=31)
    try:
        raw = inflater.decompress(data, limit + 1)
    except zlib.error:
        raise InvalidBundleError("the bundle is not a tar.gz archive") from None
    if len(raw) > limit:
        raise TooLargeError(f"the bundle unpacks to more than {limit} bytes")
    if not inflater.eof or inflater.unused_data:
        raise InvalidBundleError("the bundle is not a single complete gzip stream")
    return raw


def _parts(name: str) -> tuple[str, ...]:
    if name.startswith("/"):
        raise InvalidBundleError(f"member {name!r} has an absolute name")
    parts = tuple(part for part in name.split("/") if part not in ("", "."))
    if ".." in parts:
        raise InvalidBundleError(f"member {name!r} climbs out with '..'")
    return parts


def _members(raw: bytes) -> tuple[dict[tuple[str, ...], bytes], set[tuple[str, ...]]]:
    """Regular files by their path parts, and the directories; anything else is refused."""
    files: dict[tuple[str, ...], bytes] = {}
    directories: set[tuple[str, ...]] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            for member in tar:
                parts = _parts(member.name)
                if member.isdir():
                    if parts:
                        directories.add(parts)
                    continue
                if not member.isreg():
                    raise InvalidBundleError(f"member {member.name!r} is not a regular file")
                if not parts or parts in files:
                    raise InvalidBundleError(f"member {member.name!r} is repeated or unnamed")
                content = tar.extractfile(member)
                files[parts] = content.read() if content is not None else b""
    except tarfile.TarError:
        raise InvalidBundleError("the bundle is not a tar.gz archive") from None
    return files, directories


def _layout(
    files: dict[tuple[str, ...], bytes], directories: set[tuple[str, ...]]
) -> tuple[str | None, dict[str, bytes]]:
    """The top directory, if any, and the files named relative to `scenario.yaml`."""
    tops = {parts[0] for parts in files if len(parts) > 1} | {parts[0] for parts in directories}
    if any(len(parts) == 1 for parts in files):
        if tops:
            raise InvalidBundleError("files sit both at the root and under a directory")
        return None, {parts[0]: content for parts, content in files.items()}
    if len(tops) != 1:
        raise InvalidBundleError("the bundle holds more than one top directory or no files")
    return tops.pop(), {"/".join(parts[1:]): content for parts, content in files.items()}


def _named(document: dict[str, Any]) -> list[tuple[str, str]]:
    """Every `(field, file name)` the document points at."""
    named = [("description", document["description"]), ("check", document["check"])]
    named += [(f"steps.{step}", name) for step, name in document["steps"].items()]
    return named


def _digest(files: dict[str, bytes]) -> str:
    """sha256 of a `sha256sum` listing sorted by name, so re-packing keeps it."""
    listing = "".join(
        f"{hashlib.sha256(files[name]).hexdigest()}  {name}\n" for name in sorted(files)
    )
    return hashlib.sha256(listing.encode()).hexdigest()


def read(data: bytes, limit: int) -> Bundle:
    """Check a tar.gz bundle of at most `limit` bytes; raise with what is wrong."""
    if len(data) > limit:
        raise TooLargeError(f"the bundle is larger than {limit} bytes")
    top, files = _layout(*_members(_unpacked(data, UNPACKED_FACTOR * limit)))
    if DOCUMENT not in files:
        raise InvalidBundleError(f"the bundle has no {DOCUMENT}")
    try:
        document = yaml.safe_load(files[DOCUMENT])
    except yaml.YAMLError:
        raise InvalidBundleError(f"{DOCUMENT} is not valid YAML") from None
    error = best_match(_validator().iter_errors(document))
    if error is not None:
        raise InvalidBundleError(f"{DOCUMENT} at {error.json_path}: {error.message}")
    if top is not None and top != document["id"]:
        raise InvalidBundleError(f"the top directory {top!r} is not named like the id")
    for field, name in _named(document):
        if name not in files:
            raise InvalidBundleError(f"{field} names {name!r}, which the bundle does not hold")
    return Bundle(document=document, digest=_digest(files))
