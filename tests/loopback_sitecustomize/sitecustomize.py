"""Loopback-only isolation harness for the local UI (sibling of netblock_sitecustomize).

Loaded via PYTHONPATH in a child process. Sockets may bind and connect to loopback addresses
only; name lookups other than localhost, and every reverse lookup, are denied. Like the netblock
harness this is Python-level denial, not OS containment. Negative controls in
tests/test_webui_isolation.py prove it is active.
"""
import ipaddress
import socket
import sys

_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}
_Real = socket.socket
_real_getaddrinfo = socket.getaddrinfo


def _deny(what):
    # logged as well as raised: callers such as socket.getfqdn swallow the error, and the test
    # reads the child's stderr for any denial
    msg = f"network denied by loopback harness: {what}"
    sys.stderr.write(msg + "\n")
    raise OSError(msg)


def _check(address):
    if isinstance(address, (str, bytes)):  # AF_UNIX path: local by definition
        return
    host = address[0] if isinstance(address, tuple) and address else address
    if host in ("localhost",):
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    _deny(repr(host))


class LoopbackSocket(_Real):
    def bind(self, address):
        _check(address)
        return super().bind(address)

    def connect(self, address):
        _check(address)
        return super().connect(address)

    def connect_ex(self, address):
        _check(address)
        return super().connect_ex(address)

    def sendto(self, data, *args):
        _check(args[-1])
        return super().sendto(data, *args)


def _getaddrinfo(host, *args, **kwargs):
    if host is not None and host not in _LOCAL_NAMES:
        _deny(f"lookup {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


def _gethostbyname(host):
    if host not in _LOCAL_NAMES:
        _deny(f"lookup {host!r}")
    return "127.0.0.1"


socket.socket = LoopbackSocket
socket.getaddrinfo = _getaddrinfo
socket.gethostbyname = _gethostbyname
socket.gethostbyname_ex = lambda host: _deny(f"lookup {host!r}")
socket.gethostbyaddr = lambda addr: _deny(f"reverse lookup {addr!r}")
socket.getnameinfo = lambda *a, **kw: _deny("reverse lookup")
