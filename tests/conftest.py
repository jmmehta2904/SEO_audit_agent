"""Offline-by-default tests: any attempted socket connection is an error."""

import socket

import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    creating_socketpair = False

    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected network access in offline test")

    def guarded_connect(sock, address):
        # Windows implements asyncio's internal socketpair using loopback TCP.
        if creating_socketpair:
            return original_connect(sock, address)
        return blocked()

    def internal_socketpair(*args, **kwargs):
        nonlocal creating_socketpair
        creating_socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            creating_socketpair = False

    monkeypatch.setattr(socket, "socketpair", internal_socketpair)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
