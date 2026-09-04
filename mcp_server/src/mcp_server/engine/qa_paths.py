"""Central QA dump-path helper.

All four DOM-discovery tools (map, view, touch, hidden) write their dumps
into a per-host QA tree, idempotently:

    <FACTORY_QA_ROOT>/<host>/
        pom/                              QA's hand-written tests (qa_render → spec)
        po/                               page-object classes (qa_render → POM)
        dom/
            map/graph.json                full route graph (one file)
            view/<page-slug>.json         per-page view dump
            view/screenshots/<page>.png   per-page screenshot
            containers/<page-slug>.json   per-page touch dump (containers + transitions)
            functions/<page-slug>.json    per-page hidden dump (modals/dropdowns/...)

Filenames are deterministic — re-running a tool overwrites the same file
in place; no UUIDs, no run-id subdirs. When `page` input is set, only that
page's file is touched; sibling per-page files are untouched.

Host is `urlparse(target).netloc` so the tree is universal across SPAs.
"""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

_DEFAULT_ROOT = Path(__file__).resolve().parents[4] / "QA"

_SAFE_RE = re.compile(r"[^A-Za-z0-9]+")


def qa_root() -> Path:
    """Project-level QA root. Override via FACTORY_QA_ROOT env var."""
    env = os.environ.get("FACTORY_QA_ROOT")
    return Path(env).expanduser() if env else _DEFAULT_ROOT


def host_root(target_or_host: str) -> Path:
    """`<qa_root>/<host>/` for a target URL or bare host string."""
    host = urlparse(target_or_host).netloc or target_or_host
    return qa_root() / host


def host_from_ctx(ctx: dict) -> str:
    """Best-effort: prefer ctx['target_host'], else input.target, else page.url."""
    h = ctx.get("target_host")
    if h:
        return h
    tgt = (ctx.get("input") or {}).get("target")
    if tgt:
        nl = urlparse(tgt).netloc
        if nl:
            return nl
        return tgt
    backend = ctx.get("backend")
    page = getattr(backend, "page", None) if backend else None
    if page and getattr(page, "url", None):
        return urlparse(page.url).netloc or "unknown"
    return "unknown"


def dom_dir(target_or_host: str, sub: str) -> Path:
    """`<qa_root>/<host>/dom/<sub>/`, created on demand."""
    p = host_root(target_or_host) / "dom" / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def screenshots_dir(target_or_host: str) -> Path:
    p = host_root(target_or_host) / "dom" / "view" / "screenshots"
    p.mkdir(parents=True, exist_ok=True)
    return p


def po_dir(target_or_host: str) -> Path:
    p = host_root(target_or_host) / "po"
    p.mkdir(parents=True, exist_ok=True)
    return p


def pom_dir(target_or_host: str) -> Path:
    p = host_root(target_or_host) / "pom"
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_page_filter(page_arg: str | None, routes: list[dict]) -> set[str] | None:
    """Translate the optional `page` input to a set of matching route templates.

    Accepts either a route template literally (`/:accountId/projects`) or a
    concrete URL path (`/abc/projects`) — the latter is regex-matched against
    every template via routes.template_to_regex.

    Returns None when no filter is set → tool processes every page.
    Returns an empty set when the filter didn't match anything → tool skips
    everything (and the caller logs that for the user).
    """
    if not page_arg:
        return None
    # Local import to avoid circular import on module load.
    from .actions.routes import template_to_regex
    candidates: set[str] = set()
    direct = page_arg
    # Strip origin if user pasted full URL
    if direct.startswith("http://") or direct.startswith("https://"):
        direct = urlparse(direct).path or "/"
    if not direct.startswith("/"):
        direct = "/" + direct
    # 1) literal template match
    for r in routes:
        tpl = r.get("path", "")
        if tpl == page_arg or tpl == direct:
            candidates.add(tpl)
    if candidates:
        return candidates
    # 2) URL → template via regex
    path_only = direct.split("?")[0].split("#")[0]
    for r in routes:
        tpl = r.get("path", "")
        if not tpl.startswith("/"):
            continue
        try:
            rx = template_to_regex(tpl)
        except Exception:
            continue
        if rx.match(path_only):
            candidates.add(tpl)
    return candidates


def template_slug(template: str) -> str:
    """Stable file-safe slug per route template.

    `/` -> 'root'
    `/:accountId/projects` -> 'accountId__projects'
    `/projects/:id` -> 'projects__id'
    """
    if not template or template == "/":
        return "root"
    s = template.strip("/").replace(":", "")
    s = _SAFE_RE.sub("_", s).strip("_")
    return s or "root"
