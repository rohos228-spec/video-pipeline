"""Неблокирующее ревью #352: кэш getaddrinfo с TTL и сбросом на ConnectError."""

from __future__ import annotations

import socket
import time

import httpx


def test_dns_cache_expires_and_ipv6_is_not_emptied(monkeypatch) -> None:
    from app.services import gpt_api

    host = "gpt-dns-ttl.invalid"
    gpt_api.forget_host_ipv4(host)
    gpt_api._remember_host_ipv4(host, "203.0.113.9")
    try:
        infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        assert infos[0][4][0] == "203.0.113.9"

        gpt_api._HOST_IPV4[gpt_api._dns_host_key(host)] = (
            "203.0.113.9",
            time.monotonic() - gpt_api._DNS_TTL_SEC - 5,
        )
        seen: list[tuple] = []

        def _orig(h, port, family=0, type=0, proto=0, flags=0):
            seen.append((h, family))
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.4", port))]

        monkeypatch.setattr(gpt_api, "_GETADDRINFO_ORIG", _orig)
        expired = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        assert expired[0][4][0] == "198.51.100.4"
        assert seen and seen[0][0] == host

        gpt_api._remember_host_ipv4(host, "203.0.113.9")
        seen.clear()

        def _v6(h, port, family=0, type=0, proto=0, flags=0):
            seen.append((h, family))
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", port, 0, 0))]

        monkeypatch.setattr(gpt_api, "_GETADDRINFO_ORIG", _v6)
        v6 = socket.getaddrinfo(host, 443, socket.AF_INET6, socket.SOCK_STREAM)
        assert v6
        assert v6[0][4][0] == "2001:db8::1"
        assert seen[0][1] == socket.AF_INET6
    finally:
        gpt_api.forget_host_ipv4(host)


def test_connect_error_drops_cached_host() -> None:
    from app.services import gpt_api

    host = "gpt-dns-drop.invalid"
    gpt_api._remember_host_ipv4(host, "203.0.113.10")
    try:
        gpt_api.note_gpt_connect_error(
            f"https://{host}/v1/chat/completions",
            httpx.ConnectError("boom"),
        )
        assert gpt_api._cached_ipv4(host) is None
    finally:
        gpt_api.forget_host_ipv4(host)
