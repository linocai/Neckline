"""Inherited by Python subprocesses launched by offline acceptance tests."""
import ipaddress
import socket


def _allowed(host):
    if host in {None, 'localhost'}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_resolve, _connect, _connect_ex, _sendto = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto


def _check(sock, address):
    if sock.family in {socket.AF_INET, socket.AF_INET6} and not _allowed(address[0]):
        raise RuntimeError('Offline tests deny external network')


def resolve(host, *args, **kwargs):
    if not _allowed(host):
        raise RuntimeError('Offline tests deny external DNS')
    return _resolve(host, *args, **kwargs)


def connect(sock, address):
    _check(sock, address)
    return _connect(sock, address)


def connect_ex(sock, address):
    _check(sock, address)
    return _connect_ex(sock, address)


def sendto(sock, data, *args):
    _check(sock, args[-1])
    return _sendto(sock, data, *args)


socket.getaddrinfo = resolve
socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.socket.sendto = sendto
