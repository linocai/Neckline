"""Offline guards. Tests use temporary databases only and do not load `.env`."""
from __future__ import annotations

import os as _os
from pathlib import Path

import pytest

_os.environ["PYTHON_DOTENV_DISABLED"] = "1"


@pytest.fixture(autouse=True)
def deny_external_network(monkeypatch):
    """Offline acceptance: deny DNS and sockets except explicit loopback addresses."""
    import ipaddress
    import socket

    def allowed(host):
        if host in {None, "localhost"}:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    resolve, connect, connect_ex, sendto = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto

    def checked_resolve(host, *args, **kwargs):
        if not allowed(host):
            raise RuntimeError(f"Offline tests deny external DNS: {host}")
        return resolve(host, *args, **kwargs)

    def check_address(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6} and not allowed(address[0]):
            raise RuntimeError(f"Offline tests deny external network: {address[0]}")

    def checked_connect(sock, address):
        check_address(sock, address)
        return connect(sock, address)

    def checked_connect_ex(sock, address):
        check_address(sock, address)
        return connect_ex(sock, address)

    def checked_sendto(sock, data, *args):
        check_address(sock, args[-1])
        return sendto(sock, data, *args)

    monkeypatch.setattr(socket, "getaddrinfo", checked_resolve)
    monkeypatch.setattr(socket.socket, "connect", checked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", checked_connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", checked_sendto)


@pytest.fixture(autouse=True)
def inherit_offline_subprocess_guard(monkeypatch):
    import subprocess
    original = subprocess.Popen
    guard = str(Path(__file__).parent / 'offline_guard')

    def guarded(*args, **kwargs):
        import shlex
        command = args[0] if args else kwargs.get('args', [])
        words = shlex.split(command) if isinstance(command, str) else list(command)
        # Python children inherit the socket/DNS guard. Reject native network
        # clients and shell wrappers rather than letting them bypass Python.
        if words and Path(str(words[0])).name in {'curl','wget','ssh','scp','sftp','nc','ncat','netcat','sh','bash','zsh','fish','env'}:
            raise RuntimeError('Offline tests deny native network subprocess')
        if kwargs.get('shell'):
            raise RuntimeError('Offline tests deny shell subprocess bypass')
        env = dict(kwargs.get('env') or _os.environ)
        env['PYTHONPATH'] = guard + _os.pathsep + env.get('PYTHONPATH', '')
        kwargs['env'] = env
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', guarded)
