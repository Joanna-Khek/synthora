import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}


class BlockedURLError(Exception):
    """The URL must not be fetched. The message says why."""


async def resolve(host: str, port: int) -> list[str]:
    """Every IP address the host name resolves to (IPv4 and IPv6)."""
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return [info[4][0] for info in infos]


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(
        address.split("%")[0]
    )  # drop an IPv6 zone such as fe80::1%eth0
    # is_global is False for private, loopback, link-local, carrier-grade NAT
    # (100.64.0.0/10) and reserved ranges. Multicast counts as "global", so
    # it is excluded separately.
    return ip.is_global and not ip.is_multicast


async def check_url(url: str) -> None:
    """Raise BlockedURLError unless the URL is http(s) and EVERY address it resolves to is public."""
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        # Fails closed: anything that isn't clearly http(s) is refused, including
        # URLs httpx has mangled (it turns file:///x into the relative URL /x).
        raise BlockedURLError(
            f"refusing {url}: only absolute http:// and https:// URLs are fetched"
        )
    host = parts.hostname
    if not host:
        raise BlockedURLError(f"no host name in {url}")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise BlockedURLError(f"invalid port in {url}") from e

    try:
        addresses = await resolve(host, port)
    except socket.gaierror as e:
        raise BlockedURLError(f"could not resolve {host}") from e

    blocked = [a for a in addresses if not is_public(a)]
    if blocked:
        raise BlockedURLError(
            f"refusing {host}: it points to a non-public address ({blocked[0]})"
        )
