import asyncio
import ipaddress
from collections.abc import Callable

import httpx
import pytest
from pydantic import ValidationError

from jatszoter_api.outbound import CheckedTransport, RefusedAddress, Resolver, allowed, is_public
from jatszoter_api.settings import Settings

Handler = Callable[[httpx.Request], httpx.Response]
PUBLIC = "1.1.1.1"
URL = "https://example.com/alpha.tar.gz?token=beta"


def answers(*addresses: str) -> Resolver:
    async def resolve(_: str) -> list[str]:
        return list(addresses)

    return resolve


def sent_through(handler: Handler, *addresses: str) -> httpx.Response:
    transport = CheckedTransport(resolve=answers(*addresses), connect=httpx.MockTransport(handler))
    request = httpx.Request("GET", URL)
    return asyncio.run(transport.handle_async_request(request))


def nobody_connects(_: httpx.Request) -> httpx.Response:
    raise AssertionError("the transport connected")


@pytest.mark.parametrize("address", ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_public(address: str) -> None:
    assert is_public(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",  # noqa: S104
        "10.0.0.1",
        "100.64.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "172.16.0.1",
        "192.0.2.1",
        "192.168.0.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",
        "255.255.255.255",
        "::",  # noqa: S104
        "::1",
        "64:ff9b::7f00:1",
        "2001::1",
        "2001:db8::1",
        "2002:7f00:1::",
        "3fff::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
    ],
)
def test_internal_addresses_are_not_public(address: str) -> None:
    assert not is_public(ipaddress.ip_address(address))


def test_a_mapped_address_counts_as_the_address_it_maps() -> None:
    mapped = ipaddress.ip_address("::ffff:127.0.0.1")

    assert not allowed(mapped, [])
    assert allowed(mapped, [ipaddress.ip_network("127.0.0.1/32")])


def test_only_a_listed_network_opens_an_internal_address() -> None:
    listed = [ipaddress.ip_network("192.0.2.0/25")]

    assert allowed(ipaddress.ip_address("192.0.2.1"), listed)
    assert not allowed(ipaddress.ip_address("192.0.2.200"), listed)
    assert not allowed(ipaddress.ip_address("127.0.0.1"), listed)


@pytest.mark.parametrize(
    "addresses", [("127.0.0.1",), (PUBLIC, "10.0.0.1"), ("10.0.0.1", PUBLIC), ("::ffff:10.0.0.1",)]
)
def test_one_refused_address_refuses_the_whole_answer(addresses: tuple[str, ...]) -> None:
    with pytest.raises(RefusedAddress):
        sent_through(nobody_connects, *addresses)


@pytest.mark.parametrize(
    ("address", "netloc"), [(PUBLIC, "1.1.1.1"), ("2606:4700::1", "[2606:4700::1]")]
)
def test_the_connection_goes_to_the_checked_address_under_the_name(
    address: str, netloc: str
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    response = sent_through(handler, address)

    assert response.status_code == 200
    assert seen[0].url.netloc.decode() == netloc
    assert seen[0].url.path == "/alpha.tar.gz"
    assert seen[0].url.query == b"token=beta"
    assert seen[0].headers["host"] == "example.com"
    assert seen[0].extensions["sni_hostname"] == "example.com"


def test_the_next_address_is_tried_when_one_does_not_connect() -> None:
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.host)
        if len(tried) == 1:
            raise httpx.ConnectError("alpha")
        return httpx.Response(200)

    assert sent_through(handler, PUBLIC, "8.8.8.8").status_code == 200
    assert tried == [PUBLIC, "8.8.8.8"]


def test_the_last_failure_stands_when_no_address_connects() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("alpha")

    with pytest.raises(httpx.ConnectTimeout):
        sent_through(handler, PUBLIC, "8.8.8.8")


def test_a_read_failure_tries_no_other_address() -> None:
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.host)
        raise httpx.ReadTimeout("alpha")

    with pytest.raises(httpx.ReadTimeout):
        sent_through(handler, PUBLIC, "8.8.8.8")

    assert tried == [PUBLIC]


def test_a_name_without_an_answer_is_a_connect_error() -> None:
    with pytest.raises(httpx.ConnectError):
        sent_through(nobody_connects)


def test_a_failed_lookup_is_a_connect_error() -> None:
    async def fails(_: str) -> list[str]:
        raise OSError("alpha")

    transport = CheckedTransport(resolve=fails, connect=httpx.MockTransport(nobody_connects))

    with pytest.raises(httpx.ConnectError):
        asyncio.run(transport.handle_async_request(httpx.Request("GET", URL)))


def test_the_real_lookup_refuses_a_name_on_loopback() -> None:
    transport = CheckedTransport(connect=httpx.MockTransport(nobody_connects))

    with pytest.raises(RefusedAddress):
        asyncio.run(transport.handle_async_request(httpx.Request("GET", "https://localhost/x")))


def test_nothing_is_built_or_closed_before_a_connection() -> None:
    transport = CheckedTransport()

    asyncio.run(transport.aclose())

    assert transport.connect is None


def test_the_setting_is_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JATSZOTER_SCENARIO_FETCH_INTERNAL_CIDRS", " 192.0.2.0/24 , ::1/128,,")

    settings = Settings(database_dsn="sqlite:///alpha.db")

    assert [str(net) for net in settings.scenario_fetch_internal_cidrs] == [
        "192.0.2.0/24",
        "::1/128",
    ]


def test_the_setting_is_empty_by_default(settings: Settings) -> None:
    assert settings.scenario_fetch_internal_cidrs == []


@pytest.mark.parametrize("bad", ["192.0.2.1/24", "192.0.2.0/33", "alpha"])
def test_a_bad_network_stops_the_settings(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("JATSZOTER_SCENARIO_FETCH_INTERNAL_CIDRS", bad)

    with pytest.raises(ValidationError):
        Settings(database_dsn="sqlite:///alpha.db")
