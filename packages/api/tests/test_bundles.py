import gzip
import tarfile
from collections.abc import Callable

import pytest

from jatszoter_api import bundles
from jatszoter_api.errors import InvalidBundleError, TooLargeError

LIMIT = 1024 * 1024
Pack = Callable[..., bytes]
Member = tuple[tarfile.TarInfo, bytes]


def member(name: str, kind: bytes = tarfile.REGTYPE) -> Member:
    info = tarfile.TarInfo(name)
    info.type = kind
    if kind != tarfile.REGTYPE:
        info.linkname = "/etc/passwd"
        return info, b""
    info.size = 5
    return info, b"alpha"


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


def test_a_schema_violation_names_the_path(pack: Pack) -> None:
    broken = (
        b"id: nginx-502\nversion: 1\ntitle: alpha\ndescription: description.md\n"
        b"image: debian-12\nsteps: {break: break.sh, reboot: install.sh}\ncheck: check.sh\n"
    )

    with pytest.raises(InvalidBundleError, match=r"\$\.steps: .*'reboot'"):
        bundles.read(pack(changes={"scenario.yaml": broken}), LIMIT)


def test_invalid_yaml_is_refused(pack: Pack) -> None:
    with pytest.raises(InvalidBundleError, match="not valid YAML"):
        bundles.read(pack(changes={"scenario.yaml": b"id: [nginx-502\n"}), LIMIT)


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
