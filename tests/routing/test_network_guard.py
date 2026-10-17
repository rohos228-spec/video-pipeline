"""Страховка: живой TCP из тестов роутера не открывается."""

from __future__ import annotations

import socket

import pytest


def test_socket_connect_is_blocked():
    sock = socket.socket()
    try:
        with pytest.raises(AssertionError, match="must not open TCP"):
            sock.connect(("127.0.0.1", 9))
    finally:
        sock.close()


def test_socket_connect_ex_is_blocked():
    sock = socket.socket()
    try:
        with pytest.raises(AssertionError, match="must not open TCP"):
            sock.connect_ex(("127.0.0.1", 9))
    finally:
        sock.close()
