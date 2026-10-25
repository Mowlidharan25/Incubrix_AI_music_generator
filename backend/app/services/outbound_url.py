from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


def validate_outbound_url(url: str, allowed_hosts: list[str]) -> None:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    allowed = {
        host.strip().rstrip(".").casefold()
        for host in allowed_hosts
        if host.strip()
    }

    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or hostname not in allowed
    ):
        raise ValueError("Outbound URL must use HTTPS and an explicitly allowed host.")

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("Outbound URL host must be a DNS name, not an IP address.")

    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Outbound URL host could not be resolved.") from exc

    if not addresses or any(
        not ipaddress.ip_address(address[4][0]).is_global for address in addresses
    ):
        raise ValueError("Outbound URL host must resolve only to public IP addresses.")
