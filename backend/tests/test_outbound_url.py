from __future__ import annotations

import socket

import pytest

from app.services.outbound_url import validate_outbound_url


def _public_dns_result(address: str) -> list[tuple]:
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (address, 443),
        )
    ]


def test_outbound_url_requires_allowed_https_host_and_public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, type: _public_dns_result("8.8.8.8"),
    )

    validate_outbound_url("https://audio.example.com/track.wav", ["audio.example.com"])


@pytest.mark.parametrize(
    ("url", "allowed_hosts"),
    [
        ("http://audio.example.com/track.wav", ["audio.example.com"]),
        ("https://audio.example.com/track.wav", []),
        ("https://127.0.0.1/track.wav", ["127.0.0.1"]),
        ("https://user:pass@audio.example.com/track.wav", ["audio.example.com"]),
        ("https://audio.example.com:8443/track.wav", ["audio.example.com"]),
    ],
)
def test_outbound_url_rejects_unsafe_or_unconfigured_destinations(
    url: str, allowed_hosts: list[str]
) -> None:
    with pytest.raises(ValueError):
        validate_outbound_url(url, allowed_hosts)


def test_outbound_url_rejects_private_dns_resolution(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, type: _public_dns_result("127.0.0.1"),
    )

    with pytest.raises(ValueError, match="public IP"):
        validate_outbound_url("https://audio.example.com/track.wav", ["audio.example.com"])
