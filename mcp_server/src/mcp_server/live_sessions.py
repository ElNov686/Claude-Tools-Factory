"""Registry of live browser sessions, keyed by target host.

The MCP server is ONE long-lived process, so a browser opened during one tool
call (e.g. login with keep_open=True) survives in this module-level dict and can
be reused by later tool calls (crawl_map, scan, ...) in the same server process.
No cookies / disk state — the live browser itself is reused, then closed
explicitly. This keeps per-action token measurement clean.
"""

from typing import Any
from urllib.parse import urlparse

_LIVE: dict[str, Any] = {}


def _key(target: str) -> str:
    netloc = urlparse(target).netloc
    return netloc or target


def put(target: str, session: Any) -> None:
    _LIVE[_key(target)] = session


def get(target: str) -> Any | None:
    return _LIVE.get(_key(target))


def has(target: str) -> bool:
    return _key(target) in _LIVE


def pop(target: str) -> Any | None:
    return _LIVE.pop(_key(target), None)


def keys() -> list[str]:
    return list(_LIVE)
