"""extract_routes — extract a SPA's full route table from its internals.
resolve_params — fill route templates with real IDs harvested from the live app.

Strategy A: ask Vue Router 4 directly via getRoutes() (fully-resolved paths).
Strategy B: regex-scan same-origin JS bundles for path: "..." definitions.
"""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..registry import register

# ---------------------------------------------------------------------------
# JS: ask the live Vue Router instance (Vue Router 4)
# ---------------------------------------------------------------------------
_VUE_ROUTER_JS = """
() => {
  let app = null;
  for (const el of document.querySelectorAll('*')) {
    if (el.__vue_app__) { app = el.__vue_app__; break; }
  }
  if (!app) return null;
  const gp = app.config && app.config.globalProperties;
  const router = gp && gp.$router;
  if (!router || typeof router.getRoutes !== 'function') return null;
  try {
    return router.getRoutes().map(r => ({ path: r.path, name: (r.name || null) }));
  } catch (e) { return null; }
}
"""

# ---------------------------------------------------------------------------
# JS: regex-scan same-origin JS bundles (fallback)
# ---------------------------------------------------------------------------
_BUNDLE_REGEX_JS = """
async () => {
  const scripts = Array.from(document.querySelectorAll('script[src]'))
    .map(s => s.src)
    .filter(u => u.endsWith('.js') && u.startsWith(location.origin));
  const found = new Set();
  for (const url of scripts) {
    try {
      const t = await (await fetch(url)).text();
      const re = /path\\s*:\\s*["'`]([^"'`]{1,64})["'`]/g;
      let m;
      while ((m = re.exec(t)) !== null) {
        const p = m[1];
        if (!p || p.includes('${')) continue;
        found.add(p);
      }
    } catch (e) { /* skip */ }
  }
  return Array.from(found).map(p => ({ path: p, name: null }));
}
"""


def _routes_dir() -> Path:
    return Path(os.environ.get("TEMP", "/tmp")) / "mcp_routes"


@register("extract_routes")
async def extract_routes(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Extract the SPA's full route table without clicking.

    Tries Vue Router 4's getRoutes() first; falls back to regex-scanning
    same-origin JS bundles.

    Also stores route list on ctx["vars"]["__routes__"] for resolve_params.
    """
    page = ctx["backend"].page

    routes = await page.evaluate(_VUE_ROUTER_JS)
    source = "vue-router"
    if not routes:
        routes = await page.evaluate(_BUNDLE_REGEX_JS)
        source = "bundle-regex"

    routes = routes or []

    # Classify each route record.
    for r in routes:
        p = r.get("path", "")
        r["parametrized"] = ":" in p
        r["absolute"] = p.startswith("/")

    d = _routes_dir()
    d.mkdir(parents=True, exist_ok=True)
    dump = str(d / f"{uuid.uuid4().hex}.json")
    Path(dump).write_text(
        json.dumps({"source": source, "routes": routes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ctx["output"]["route_count"] = len(routes)
    ctx["output"]["source"] = source
    ctx["output"]["static_count"] = sum(1 for r in routes if not r["parametrized"])
    ctx["output"]["routes_preview"] = [r["path"] for r in routes[:25]]
    ctx["output"]["dump_path"] = dump

    # Store for resolve_params
    ctx.setdefault("vars", {})
    ctx["vars"]["__routes__"] = routes


# ---------------------------------------------------------------------------
# Pure-python core: template <-> URL helpers
# ---------------------------------------------------------------------------

def template_to_regex(template: str) -> re.Pattern:
    """Vue route template -> compiled regex with named groups.

    Handles:
      :name          -> required segment  (?P<name>[^/]+)
      :name?         -> optional segment  (?:/(?P<name>[^/]+))?
      :name(...)* catch-alls             -> tail (.*)  (not navigable)
      two params in one segment          -> each :name gets [^/-]+ so literal '-' splits them
    Anchored with ^...$, optional trailing slash.
    """
    # Check for catch-all first (entire template or tail segment)
    # e.g. /:pathMatch(.*)* or just :pathMatch(.*)*
    if re.search(r':\w+\([^)]*\)\*', template):
        # catch-all — match anything
        return re.compile(r'^.*$')

    parts = template.split('/')
    regex_parts: list[str] = []

    for i, seg in enumerate(parts):
        if seg == '' and i == 0:
            # leading slash -> root anchor, handled by '^'
            continue

        if not seg:
            # double slash / trailing slash in template — skip
            continue

        # Check if this whole segment is a single optional param: :name?
        sole_optional = re.fullmatch(r':(\w+)\?', seg)
        if sole_optional:
            name = sole_optional.group(1)
            regex_parts.append(f'(?:/(?P<{name}>[^/]+))?')
            continue

        # Regular segment — may contain mix of literals and :params
        # Within a segment, params separated by literals (like '-') use [^/-]+
        seg_has_literal = bool(re.search(r'[^:\w?]', seg))
        if seg_has_literal:
            inner_sep = '[^/-]+'
        else:
            inner_sep = '[^/]+'

        def replace_param(m: re.Match) -> str:
            name = m.group(1)
            return f'(?P<{name}>{inner_sep})'

        seg_re = re.sub(r':(\w+)\??', replace_param, re.escape(seg))
        # re.escape will escape the colon in :name — undo that since we replaced the tokens
        # Actually we need to escape the literal parts but not the param tokens.
        # Better approach: build the segment regex token-by-token.
        regex_parts.append('/' + _seg_to_regex(seg))

    # Build full pattern
    body = ''.join(regex_parts)
    if not body:
        body = '/'
    pattern = f'^{body}/?$'
    return re.compile(pattern)


def _seg_to_regex(seg: str) -> str:
    """Convert a single path segment (no leading slash) to a regex string.

    Handles mixed literal+param segments like ':projectId-:itemId'.
    """
    # Tokenize: split into param tokens (:name or :name?) and literal chunks
    token_re = re.compile(r':(\w+)(\?)?')
    result = []
    pos = 0
    has_literal_sep = bool(re.search(r'[^:\w?]', seg))

    # If this segment has literal separators between params, params get [^/-]+
    # otherwise [^/]+
    param_char_class = '[^/-]+' if has_literal_sep else '[^/]+'

    for m in token_re.finditer(seg):
        # Literal text before this param
        literal = seg[pos:m.start()]
        if literal:
            result.append(re.escape(literal))
        name = m.group(1)
        result.append(f'(?P<{name}>{param_char_class})')
        pos = m.end()

    # Remaining literal after last param
    tail = seg[pos:]
    if tail:
        result.append(re.escape(tail))

    return ''.join(result)


def match_url(path: str, template: str) -> dict | None:
    """Return {param: value} if `path` matches `template`, else None."""
    rx = template_to_regex(template)
    # Catch-all regex matches everything but is not useful for param extraction
    if rx.pattern == r'^.*$':
        return None
    m = rx.match(path.rstrip('/') or '/')
    if not m:
        return None
    return {k: v for k, v in m.groupdict().items() if v is not None}


def instantiate(template: str, params: dict) -> tuple[str | None, list[str]]:
    """Fill a template with params. Returns (concrete_url or None, missing_param_names).

    - Optional params (':name?') that are missing -> drop that segment.
    - Catch-all (:pathMatch(.*)*) -> not navigable (return None, []).
    """
    # Catch-all -> not navigable
    if re.search(r':\w+\([^)]*\)\*', template):
        return None, []

    parts = template.split('/')
    result_parts: list[str] = []
    missing: list[str] = []

    for i, seg in enumerate(parts):
        if seg == '' and i == 0:
            result_parts.append('')
            continue
        if not seg:
            continue

        # Sole optional segment: :name?
        sole_optional = re.fullmatch(r':(\w+)\?', seg)
        if sole_optional:
            name = sole_optional.group(1)
            val = params.get(name)
            if val is not None:
                result_parts.append(val)
            # else: drop segment (it's optional)
            continue

        # Regular segment — replace each :param token
        new_seg = seg
        seg_missing: list[str] = []

        for m in re.finditer(r':(\w+)(\?)?', seg):
            name = m.group(1)
            optional = m.group(2) == '?' if m.group(2) else False
            val = params.get(name)
            if val is not None:
                new_seg = new_seg.replace(m.group(0), val, 1)
            elif optional:
                new_seg = new_seg.replace(m.group(0), '', 1)
            else:
                seg_missing.append(name)

        if seg_missing:
            missing.extend(seg_missing)
        else:
            result_parts.append(new_seg)

    if missing:
        return None, missing

    return '/'.join(result_parts) or '/', []


# ---------------------------------------------------------------------------
# DOM-wide URL harvest. Picks up path literals from anchors, data-* attrs,
# and any string literal embedded in attribute values / inline scripts.
# Vue's @click="$router.push('/x/y')" / data-href="/x/y" / template strings
# / React's onClick="navigate('/x/y')" all surface here without clicking.
# ---------------------------------------------------------------------------

_DOM_URL_HARVEST_JS = r"""
() => {
  const found = new Set();
  // 1) anchors
  for (const a of document.querySelectorAll('a[href]')) {
    try {
      const u = new URL(a.href, location.origin);
      if (u.origin === location.origin) found.add(u.pathname + (u.search || ''));
    } catch (e) {}
  }
  // 2) common data-* navigation attributes
  for (const el of document.querySelectorAll(
        '[data-href], [data-path], [data-route], [data-url], [data-to]')) {
    for (const attr of ['data-href','data-path','data-route','data-url','data-to']) {
      const v = el.getAttribute(attr);
      if (v && v.startsWith('/') && v.length < 200) found.add(v);
    }
  }
  // 3) mine attribute values + inline script text for path-like literals.
  // Pattern targets URL strings inside quoted text — universal across
  // frameworks (Vue/React/Angular all emit these into DOM/scripts).
  const re = /['"`](\/[A-Za-z0-9_\-./?=&+%]+)['"`]/g;
  const sources = [];
  for (const s of document.querySelectorAll('script:not([src])')) {
    sources.push(s.textContent || '');
  }
  for (const el of document.querySelectorAll('[onclick],[v-on\\:click],[\\@click]')) {
    sources.push(el.outerHTML || '');
  }
  // also scan a bounded chunk of the full HTML — keeps cost predictable
  sources.push(document.documentElement.outerHTML.slice(0, 500000));
  for (const src of sources) {
    let m;
    while ((m = re.exec(src)) !== null) {
      const p = m[1];
      // skip junk: assets, mailto-ish, deep query strings
      if (p.length < 2 || p.length > 180) continue;
      if (p.startsWith('//')) continue;
      if (/\.(js|css|png|jpg|svg|ico|woff2?|json)(\?|$)/i.test(p)) continue;
      found.add(p);
    }
  }
  return Array.from(found);
}
"""


# ---------------------------------------------------------------------------
# Action: resolve_params
# ---------------------------------------------------------------------------

@register("resolve_params")
async def resolve_params(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    page = ctx["backend"].page
    routes = ctx["vars"].get("__routes__") or []

    # 1) accountId from the current URL path's first segment, if present
    cur = urlparse(page.url).path.strip("/").split("/")
    params: dict[str, str] = {}
    if cur and cur[0]:
        params["accountId"] = cur[0]

    # 2) Universal URL harvest from the current DOM. Reads BOTH:
    #    a) explicit <a href> and data-* attributes
    #    b) string literals inside attribute values + script texts that
    #       look like router paths ("/x/y/z"). Vue / React / Angular
    #       all bake target URLs into onclick handlers / router-link
    #       directives — picking up the literals is framework-agnostic.
    async def harvest():
        return await page.evaluate(_DOM_URL_HARVEST_JS)

    harvested = set(await harvest())

    # try to also visit the projects list via the live router (in-app, no reload)
    if params.get("accountId"):
        await _router_push(page, f"/{params['accountId']}/projects")
        await page.wait_for_timeout(800)
        harvested |= set(await harvest())
        # drill into the first concrete project link to grab deeper ids (depth 1)
        for p in list(harvested):
            segs = p.strip('/').split('/')
            if len(segs) >= 2 and segs[0] == params['accountId'] and segs[1] not in ('projects', 'myWork', 'timesheet', 'accounts'):
                await _router_push(page, p)
                await page.wait_for_timeout(800)
                harvested |= set(await harvest())
                break

        # depth 2: drill into deeper harvested URLs (>= 3 path segments total)
        # to surface params that only appear inside nested views (e.g. sprintId
        # under /:accountId/:projectId/sprints).
        max_drills = int(step.get("max_drills") or 6)
        _DANGER = ("/logout", "/delete", "/signout")
        drilled: set[str] = set()
        for p in list(harvested):
            if max_drills <= 0:
                break
            if p in drilled:
                continue
            if any(d in p for d in _DANGER):
                continue
            segs = p.strip('/').split('/')
            # need at least 3 segments (e.g. /acc/proj/sprints)
            if len(segs) < 3 or segs[0] != params['accountId']:
                continue
            drilled.add(p)
            await _router_push(page, p)
            await page.wait_for_timeout(800)
            harvested |= set(await harvest())
            max_drills -= 1

    # 3) reverse-match harvested concrete paths against templates -> collect param examples
    for url in harvested:
        for r in routes:
            got = match_url(url, r["path"])
            if got:
                for k, v in got.items():
                    params.setdefault(k, v)

    # 4) instantiate every template
    resolved = []
    nav_count = 0
    for r in routes:
        url, missing = instantiate(r["path"], params)
        rec = {
            "template": r["path"],
            "name": r.get("name"),
            "navigable_url": url,
            "missing": missing,
        }
        if url:
            nav_count += 1
        resolved.append(rec)

    ctx["vars"]["__navigable_map__"] = resolved

    # 5) write dump + lean output
    d = _routes_dir()
    d.mkdir(parents=True, exist_ok=True)
    dump = str(d / f"nav_{uuid.uuid4().hex}.json")
    Path(dump).write_text(
        json.dumps({"params": params, "routes": resolved}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ctx["output"]["navigable_count"] = nav_count
    ctx["output"]["total_routes"] = len(resolved)
    ctx["output"]["params_resolved"] = sorted(params.keys())
    ctx["output"]["navigable_preview"] = [r["navigable_url"] for r in resolved if r["navigable_url"]][:25]
    ctx["output"]["dump_path"] = dump


async def _router_push(page, path: str) -> None:
    """Navigate the live Vue Router in-app (no reload). Best-effort."""
    try:
        await page.evaluate("""(p) => {
            for (const el of document.querySelectorAll('*')) {
              if (el.__vue_app__) {
                const r = el.__vue_app__.config?.globalProperties?.$router;
                if (r && r.push) { r.push(p); return true; }
              }
            }
            return false;
        }""", path)
    except Exception:
        pass
