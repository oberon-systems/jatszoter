import gzip
import tarfile
import time
import tracemalloc
from collections.abc import Callable

import pytest

from jatszoter_api import bundles
from jatszoter_api.errors import InvalidBundleError, TooLargeError

LIMIT = 1024 * 1024
Pack = Callable[..., bytes]
Member = tuple[tarfile.TarInfo, bytes]
GIB_4 = 4 * 1024 * 1024 * 1024
VALID = (
    b"id: nginx-502\nversion: 1\ntitle: alpha\ndescription: description.md\n"
    b"image: rocky-9\nsteps: {break: break.sh}\ncheck: check.sh\n"
)


def member(name: str, kind: bytes = tarfile.REGTYPE) -> Member:
    info = tarfile.TarInfo(name)
    info.type = kind
    if kind != tarfile.REGTYPE:
        info.linkname = "/etc/passwd"
        return info, b""
    info.size = 5
    return info, b"alpha"


def gnu_sparse_bundle(declared: int) -> bytes:
    """One old-style GNU sparse member: five stored bytes, a hole up to `declared`."""
    info = tarfile.TarInfo("nginx-502/big")
    info.type = tarfile.GNUTYPE_SPARSE
    info.size = 5
    header = bytearray(info.tobuf(tarfile.GNU_FORMAT))
    header[386:398] = b"%011o\0" % (declared - 5)
    header[398:410] = b"%011o\0" % 5
    header[483:495] = b"%011o\0" % declared
    header[148:156] = b" " * 8
    header[148:156] = b"%06o\0 " % sum(header)
    return gzip.compress(bytes(header) + b"alpha".ljust(512, b"\0") + bytes(1024))


def pax_sparse_bundle(pack: Pack, declared: int) -> bytes:
    info = tarfile.TarInfo("nginx-502/big")
    info.size = 5
    info.pax_headers = {
        "GNU.sparse.map": f"{declared - 5},5",
        "GNU.sparse.realsize": str(declared),
    }
    return pack(extra=[(info, b"alpha")])


def nested_aliases() -> bytes:
    levels = [b"a0: &a0 [alpha]\n"]
    for level in range(1, 10):
        items = b", ".join([b"*a%d" % (level - 1)] * 10)
        levels.append(b"a%d: &a%d [%s]\n" % (level, level, items))
    return b"".join(levels) + b"title: *a9\n"


def flow_list(key: bytes, items: list[bytes]) -> bytes:
    return VALID + key + b": [" + b", ".join(items) + b"]\n"


def numbered(count: int) -> list[bytes]:
    return [b"n%d" % number for number in range(count)]


def test_the_sample_reads_with_its_document(pack: Pack) -> None:
    bundle = bundles.read(pack(), LIMIT)

    assert bundle.document["id"] == "nginx-502"
    assert bundle.document["image"] == "rocky-9"
    assert len(bundle.digest) == 64


def test_re_packing_keeps_the_digest(pack: Pack) -> None:
    digest = bundles.read(pack(), LIMIT).digest

    assert bundles.read(pack(mtime=1_700_000_000), LIMIT).digest == digest
    assert bundles.read(pack(top=None), LIMIT).digest == digest


def test_changed_content_changes_the_digest(pack: Pack) -> None:
    digest = bundles.read(pack(), LIMIT).digest

    assert bundles.read(pack(changes={"check.sh": b"exit 1\n"}), LIMIT).digest != digest


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (member("nginx-502/link", tarfile.SYMTYPE), "not a regular file"),
        (member("nginx-502/hard", tarfile.LNKTYPE), "not a regular file"),
        (member("nginx-502/device", tarfile.CHRTYPE), "not a regular file"),
        (member("../x"), "'..'"),
        (member("/etc/x"), "absolute"),
        (member("other/x"), "more than one top directory"),
    ],
)
def test_a_bad_member_is_refused(pack: Pack, extra: Member, message: str) -> None:
    with pytest.raises(InvalidBundleError, match=message):
        bundles.read(pack(extra=[extra]), LIMIT)


def test_files_at_the_root_and_in_a_directory_are_refused(pack: Pack) -> None:
    with pytest.raises(InvalidBundleError, match="both at the root"):
        bundles.read(pack(extra=[member("stray.txt")]), LIMIT)


def test_a_top_directory_not_named_like_the_id_is_refused(pack: Pack) -> None:
    with pytest.raises(InvalidBundleError, match="not named like the id"):
        bundles.read(pack(top="alpha"), LIMIT)


def test_a_missing_step_file_is_refused(pack: Pack) -> None:
    with pytest.raises(InvalidBundleError, match="steps.configure names 'configure.sh'"):
        bundles.read(pack(changes={"configure.sh": None}), LIMIT)


def test_a_schema_violation_names_the_path_and_the_rule(pack: Pack) -> None:
    broken = (
        b"id: nginx-502\nversion: 1\ntitle: alpha\ndescription: description.md\n"
        b"image: debian-12\nsteps: {break: break.sh, reboot: install.sh}\ncheck: check.sh\n"
    )

    with pytest.raises(InvalidBundleError, match=r"\$\.steps: additionalProperties"):
        bundles.read(pack(changes={"scenario.yaml": broken}), LIMIT)


def test_a_schema_violation_does_not_repeat_the_value(pack: Pack) -> None:
    broken = VALID.replace(b"title: alpha", b"title: [beta]")

    with pytest.raises(InvalidBundleError, match=r"\$\.title: type string") as refused:
        bundles.read(pack(changes={"scenario.yaml": broken}), LIMIT)

    assert "beta" not in str(refused.value)


@pytest.mark.parametrize(
    "document",
    [
        b"id: [nginx-502\n",
        b"[" * 60000,
        b"id: 2001-13-45\n",
        b"version: " + b"1" * 5000 + b"\n",
    ],
    ids=["unclosed", "deep", "bad-date", "huge-integer"],
)
def test_invalid_yaml_is_refused(pack: Pack, document: bytes) -> None:
    with pytest.raises(InvalidBundleError, match="not valid YAML"):
        bundles.read(pack(changes={"scenario.yaml": document}), LIMIT)


@pytest.mark.parametrize(
    "document",
    [
        VALID + b"summary: &a alpha\nhints: [*a, *a]\n",
        VALID.replace(b"title: alpha", b"title: *missing"),
        nested_aliases(),
        b"base: &a {id: nginx-502}\nmerged: {<<: *a}\n",
    ],
    ids=["hints", "undefined", "nested", "merge-key"],
)
def test_an_alias_is_refused_at_once(pack: Pack, document: bytes) -> None:
    started = time.monotonic()

    with pytest.raises(InvalidBundleError, match="YAML alias"):
        bundles.read(pack(changes={"scenario.yaml": document}), LIMIT)

    assert time.monotonic() - started < 1


def test_an_anchor_nobody_names_is_only_a_label(pack: Pack) -> None:
    document = VALID.replace(b"title: alpha", b"title: &a alpha")

    bundle = bundles.read(pack(changes={"scenario.yaml": document}), LIMIT)

    assert bundle.document["title"] == "alpha"


def test_an_oversized_document_is_refused_before_it_is_parsed(pack: Pack) -> None:
    document = VALID + b"#" * bundles.DOCUMENT_LIMIT

    with pytest.raises(TooLargeError, match="scenario.yaml is larger"):
        bundles.read(pack(changes={"scenario.yaml": document}), LIMIT)


@pytest.mark.parametrize(
    ("document", "path"),
    [
        (flow_list(b"hints", numbered(33)), r"\$\.hints: maxItems"),
        (flow_list(b"hints", [b"a" * 1001]), r"\$\.hints\[0\]: maxLength"),
        (flow_list(b"tags", [b"a" * 33]), r"\$\.tags\[0\]: maxLength"),
        (flow_list(b"tags", numbered(17)), r"\$\.tags: maxItems"),
        (flow_list(b"packages", [b"a" * 129]), r"\$\.packages\[0\]: maxLength"),
        (flow_list(b"packages", numbered(65)), r"\$\.packages: maxItems"),
    ],
    ids=["hints", "hint", "tag", "tags", "package", "packages"],
)
def test_a_list_past_its_limit_is_refused(pack: Pack, document: bytes, path: str) -> None:
    with pytest.raises(InvalidBundleError, match=path):
        bundles.read(pack(changes={"scenario.yaml": document}), LIMIT)


@pytest.mark.parametrize("kind", ["gnu", "pax"])
def test_a_sparse_member_is_refused_without_expanding(pack: Pack, kind: str) -> None:
    data = gnu_sparse_bundle(GIB_4) if kind == "gnu" else pax_sparse_bundle(pack, GIB_4)

    tracemalloc.start()
    try:
        with pytest.raises(InvalidBundleError, match="sparse file"):
            bundles.read(data, LIMIT)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < LIMIT


def test_a_member_declaring_more_than_the_bundle_holds_is_refused(pack: Pack) -> None:
    info = tarfile.TarInfo("nginx-502/big")
    info.size = 5
    info.pax_headers = {"size": str(GIB_4)}

    with pytest.raises(InvalidBundleError, match="declares more than is left"):
        bundles.read(pack(extra=[(info, b"alpha")]), LIMIT)


def test_a_bundle_without_the_document_is_refused(pack: Pack) -> None:
    with pytest.raises(InvalidBundleError, match="no scenario.yaml"):
        bundles.read(pack(changes={"scenario.yaml": None}), LIMIT)


def test_something_else_than_a_tar_gz_is_refused() -> None:
    with pytest.raises(InvalidBundleError, match="not a tar.gz"):
        bundles.read(b"alpha", LIMIT)
    with pytest.raises(InvalidBundleError, match="not a tar.gz"):
        bundles.read(gzip.compress(b"alpha" * 200), LIMIT)


def test_trailing_data_after_the_gzip_stream_is_refused(pack: Pack) -> None:
    with pytest.raises(InvalidBundleError, match="single complete gzip"):
        bundles.read(pack() + b"alpha", LIMIT)


def test_an_oversized_bundle_is_refused(pack: Pack) -> None:
    data = pack()

    with pytest.raises(TooLargeError):
        bundles.read(data, len(data) - 1)


def test_a_bundle_unpacking_past_four_times_the_limit_is_refused(pack: Pack) -> None:
    limit = 64 * 1024
    data = pack(changes={"filler.bin": bytes(4 * limit)})

    assert len(data) < limit
    with pytest.raises(TooLargeError, match="unpacks"):
        bundles.read(data, limit)
