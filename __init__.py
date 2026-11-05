"""Hermes Agent entry point for Corpus Open (see plugin.yaml and hermes_plugin/).

Hermes loads this folder as a package and calls register(ctx). The CLI (matter.py) and the MCP
server (matter-mcp.py) never import this file.
"""


def register(ctx):
    from .hermes_plugin import register as _register
    _register(ctx)
