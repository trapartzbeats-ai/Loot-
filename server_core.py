"""Reduced-context Loot Browser server for normal browser agents.

The full 130+ tool server remains available through server.py. This entrypoint
loads only browsing, state, recovery, profile, and diagnostic tools, then uses
FastMCP v3 BM25 progressive disclosure. Benchmark control stays on the
specialist endpoint so normal agents do not pay its schema cost on every task.
"""
from __future__ import annotations

import os

os.environ["LOOT_BROWSER_TOOLSET"] = "core"

import config as _config  # noqa: E402

# Embedded clients may have imported browser/config before this entrypoint.
# Keep the core contract deterministic despite Python's module cache.
_config.TOOLSET = "core"

import server as _server  # noqa: E402

_server.TOOLSET = "core"
_server._apply_toolset()

main = _server.main
mcp = _server.mcp

__all__ = ["mcp", "main"]


if __name__ == "__main__":
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    main(transport=transport, port=port)
