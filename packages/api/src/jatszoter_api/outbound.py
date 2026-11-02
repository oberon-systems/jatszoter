import asyncio
import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable, Sequence

import httpx

log = logging.getLogger(__name__)

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address
type Network = ipaddress.IPv4Network | ipaddress.IPv6Network
type Resolver = Callable[[str], Awaitable[list[str]]]

# The same ranges as the agent's image downloads, see docs/project/06-security.md.
_INTERNAL_V4 = tuple(
    ipaddress.IPv4Network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/3",
    )
)
_GLOBAL_V6 = ipaddress.IPv6Network("2000::/3")
_INTERNAL_V6 = tuple(
    ipaddress.IPv6Network(net) for net in ("2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20")
)


class RefusedAddress(httpx.TransportError):
    """A URL's host resolves to an address the rule refuses; nothing was connected to."""


def is_public(address: Address) -> bool:
    if isinstance(address, ipaddress.IPv4Address):
        return not any(address in net for net in _INTERNAL_V4)
    return address in _GLOBAL_V6 and not any(address in net for net in _INTERNAL_V6)


def allowed(address: Address, internal: Sequence[Network]) -> bool:
    """Whether `address`, or the IPv4 address it maps, is public or in a listed network."""
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    canonical = mapped or address
    return is_public(canonical) or any(canonical in net for net in internal)


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]).partition("%")[0] for info in infos]


class CheckedTransport(httpx.AsyncBaseTransport):
    """Connects only to addresses it checked, so a name cannot rebind past the rule."""

    def __init__(
        self,
        internal: Sequence[Network] = (),
        resolve: Resolver = _resolve,
        connect: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.internal = internal
        self.resolve = resolve
        self.connect = connect

    async def _checked(self, host: str) -> list[str]:
        try:
            addresses = await self.resolve(host)
        except (OSError, UnicodeError):
            raise httpx.ConnectError("the name does not resolve") from None
        if not addresses:
            raise httpx.ConnectError("the name does not resolve")
        for address in addresses:
            if not allowed(ipaddress.ip_address(address), self.internal):
                log.warning("the bundle host %s resolves to refused %s", host, address)
                raise RefusedAddress("the address is refused")
        return addresses

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        addresses = await self._checked(host)
        # Built on first use: an upload never connects, and a TLS context takes a while.
        self.connect = self.connect or httpx.AsyncHTTPTransport()
        failure: httpx.TransportError = httpx.ConnectError("no address connected")
        for address in addresses:
            # The certificate is still checked against the name: that is what sni_hostname sets.
            pinned = httpx.Request(
                request.method,
                request.url.copy_with(host=address),
                headers=request.headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": host},
            )
            try:
                return await self.connect.handle_async_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                failure = error
        raise failure

    async def aclose(self) -> None:
        if self.connect is not None:
            await self.connect.aclose()
