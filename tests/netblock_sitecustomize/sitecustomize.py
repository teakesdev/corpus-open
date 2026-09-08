"""R1 isolation harness: Python-level socket denial for MCP child processes.

Loaded automatically via PYTHONPATH (sitecustomize runs at interpreter start).
This is PYTHON-LEVEL denial inside the child — NOT OS-level containment; a
non-Python egress path or a process that avoids the socket module is out of
scope by construction. Negative controls in tests/test_rfc0001.py prove the
block is real (socket attempt fails with this on the path, succeeds without).
"""
import socket


def _deny(*args, **kwargs):
    raise OSError("network denied by R1 isolation harness (Python-level)")


socket.socket = _deny
socket.create_connection = _deny
socket.getaddrinfo = _deny
socket.gethostbyname = _deny
