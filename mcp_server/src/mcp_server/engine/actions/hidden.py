"""HIDDEN — discover overlays (modals, dropdowns, menus, popovers) per page.

For every node in the map, navigate there (router.push), then for every
button/clickable touch already recorded — click it once, look for a new
overlay (role=dialog/menu/listbox/tooltip, [aria-modal], common modal/popup
class hints), read its DOM structure, and Escape out.

Universal — overlay detection is by semantic role + universal class hints,
not framework-specific. Safe — danger-text skipped, form-submit skipped,
URL-changing clicks reverted (those are touch's transitions, not hidden).
"""

import json
import re
from typing import Any

from .. import qa_paths
from ..registry import register
from .view import _navigate

# HARD block only — these end the session with no recovery. Everything else
# (delete / save / confirm / submit) is clicked: in most apps it opens a
# confirm modal which is exactly the surface we want to discover.
_HARD_BLOCK_RE = re.compile(
    r"^(log\s*out|sign\s*out|sign[-\s]?out|выйти|exit\s*account)$",
    re.IGNORECASE,
)

# Static pre-mounted scan: many SPAs pre-mount modals in DOM with display:none
# (Vue/React app-mounted dialogs / dropdowns / drawers). We read them without
# clicking — pure "collect info" path. Universal, framework-agnostic.
_PRE_MOUNTED_JS = r"""
() => {
  const sel = [
    '[role="dialog"]', '[role="alertdialog"]', '[role="menu"]',
    '[role="listbox"]', '[role="tooltip"]',
    '[class*="modal" i]', '[class*="popup" i]', '[class*="dropdown" i]',
    '[class*="overlay" i]', '[class*="drawer" i]', '[class*="sheet" i]',
    '[id^="modal" i]', '[id^="dialog" i]', '[id^="popover" i]',
  ].join(',');
  const isCurrentlyVisible = (el, cs) => {
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (parseFloat(cs.opacity) === 0) return false;
    if (!el.offsetParent && cs.position !== 'fixed') return false;
    const r = el.getBoundingClientRect();
    return r.width > 4 && r.height > 4;
  };
  const accName = (el) => {
    let t = el.getAttribute && el.getAttribute('aria-label');
    if (t && t.trim()) return t.trim();
    t = (el.innerText || '').trim();
    if (t) return t.slice(0, 120);
    return '';
  };
  const stableLocator = (el) => {
    const dt = el.getAttribute('data-testid');
    if (dt) return { by: 'data-testid', sel: `[data-testid="${dt}"]` };
    if (el.id) return { by: 'id', sel: `#${el.id}` };
    if (el.name) return { by: 'name',
      sel: `${el.tagName.toLowerCase()}[name="${el.name}"]` };
    const role = el.getAttribute('role') || el.tagName.toLowerCase();
    const nm = accName(el);
    if (nm) return { by: 'role-name',
      sel: `role=${role}[name="${nm.slice(0,60)}"]` };
    return { by: 'css', sel: el.tagName.toLowerCase() };
  };

  const out = [];
  const seenSig = new Set();
  for (const surf of document.querySelectorAll(sel)) {
    const cs = getComputedStyle(surf);
    // skip surfaces that are currently visible — those are caught by the
    // click-based scan as "live overlays"
    if (isCurrentlyVisible(surf, cs)) continue;

    const sig = `${surf.getAttribute('role')||''}|${surf.id||''}|${(surf.getAttribute('class')||'').slice(0,80)}`;
    if (seenSig.has(sig)) continue;
    seenSig.add(sig);

    const elements = [];
    const sel2 = 'a[href], button, input, textarea, select, '
      + '[role="button"], [role="link"], [role="menuitem"]';
    for (const el of surf.querySelectorAll(sel2)) {
      const tag = el.tagName.toLowerCase();
      const role = el.getAttribute('role');
      let kind = null;
      if (tag === 'a' && el.hasAttribute('href')) kind = 'link';
      else if (tag === 'button' || role === 'button') kind = 'button';
      else if (tag === 'input') {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        kind = (t === 'submit' || t === 'button') ? 'button' : 'input';
      } else if (tag === 'textarea') kind = 'input';
      else if (tag === 'select') kind = 'select';
      if (!kind) continue;
      const loc = stableLocator(el);
      const rec = { kind, text: accName(el), locator: loc.sel,
                    locator_by: loc.by };
      if (kind === 'input') {
        rec.type = (el.getAttribute('type') || 'text').toLowerCase();
        if (el.hasAttribute('required')) rec.required = true;
        const ph = el.getAttribute('placeholder');
        if (ph) rec.placeholder = ph;
      }
      elements.push(rec);
    }
    if (elements.length === 0) continue;
    const heading = surf.querySelector(
      'h1, h2, h3, [role="heading"], [class*="title" i]');
    out.push({
      surface_kind: surf.getAttribute('role')
        || (surf.getAttribute('class') || '').match(/(modal|popup|dropdown|menu|drawer|overlay|sheet)/i)?.[0]
        || surf.tagName.toLowerCase(),
      title: (heading && heading.innerText || '').trim().slice(0, 80)
        || (surf.getAttribute('aria-label') || '').slice(0, 80),
      element_count: elements.length,
      elements,
    });
  }
  return out;
}
"""

# JS-level Escape — dispatched programmatically so we never visibly act on
# the UI. Routed to both document and the currently-focused element.
_ESC_JS = r"""
() => {
  const evt = new KeyboardEvent('keydown', {
    key: 'Escape', code: 'Escape', keyCode: 27, which: 27,
    bubbles: true, cancelable: true,
  });
  document.dispatchEvent(evt);
  if (document.activeElement) document.activeElement.dispatchEvent(evt);
}
"""

# Universal "an overlay is open" JS — catches: explicit role+aria-modal
# hints, common modal/popup/dropdown class names, AND any visible element
# with position:fixed/absolute + z-index > 500 OR id starting with
# modal/dialog/popover. Works on custom modal libs without role=dialog.
_OVERLAY_SCAN_JS = r"""
() => {
  const explicit = [
    '[role="dialog"]', '[role="alertdialog"]', '[role="menu"]',
    '[role="listbox"]', '[role="tooltip"]', '[aria-modal="true"]',
    '[class*="modal" i]', '[class*="popup" i]', '[class*="dropdown" i]',
    '[class*="overlay" i]', '[class*="drawer" i]', '[class*="sheet" i]',
    '[class*="backdrop" i]', '[id^="modal" i]', '[id^="dialog" i]',
    '[id^="popover" i]',
  ].join(',');
  const isVisible = (el, cs) => {
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (parseFloat(cs.opacity) === 0) return false;
    if (!el.offsetParent && cs.position !== 'fixed') return false;
    const r = el.getBoundingClientRect();
    return r.width > 4 && r.height > 4;
  };
  const visited = new Set();
  const out = [];
  const push = (el) => {
    if (visited.has(el)) return;
    visited.add(el);
    const cs = getComputedStyle(el);
    if (!isVisible(el, cs)) return;
    out.push({
      role: el.getAttribute('role') || null,
      class: (el.getAttribute('class') || '').slice(0, 160),
      tag: el.tagName.toLowerCase(),
      aria_label: el.getAttribute('aria-label') || '',
      id: el.id || '',
      z_index: cs.zIndex,
      position: cs.position,
      rect: el.getBoundingClientRect().toJSON(),
    });
  };
  for (const el of document.querySelectorAll(explicit)) push(el);
  // Heuristic pass: any element with z-index > 500 + fixed/absolute is
  // almost certainly an overlay (toast, dialog, popover, dropdown).
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'absolute') continue;
    const z = parseInt(cs.zIndex, 10);
    if (isNaN(z) || z <= 500) continue;
    push(el);
  }
  return out;
}
"""

# After overlay opens, read its inside structure (universal: actionables
# within the overlay element).
_OVERLAY_STRUCT_JS = r"""
() => {
  const explicit = [
    '[role="dialog"]', '[role="alertdialog"]', '[role="menu"]',
    '[role="listbox"]', '[role="tooltip"]', '[aria-modal="true"]',
    '[class*="modal" i]', '[class*="popup" i]', '[class*="dropdown" i]',
    '[class*="overlay" i]', '[class*="drawer" i]', '[class*="sheet" i]',
    '[id^="modal" i]', '[id^="dialog" i]', '[id^="popover" i]',
  ].join(',');
  const isVisible = (el, cs) => {
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (parseFloat(cs.opacity) === 0) return false;
    if (!el.offsetParent && cs.position !== 'fixed') return false;
    const r = el.getBoundingClientRect();
    return r.width > 4 && r.height > 4;
  };
  // pick the topmost visible overlay (by z-index)
  let target = null;
  let topZ = -1;
  const candidates = new Set();
  for (const el of document.querySelectorAll(explicit)) candidates.add(el);
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'absolute') continue;
    const z = parseInt(cs.zIndex, 10);
    if (!isNaN(z) && z > 500) candidates.add(el);
  }
  for (const el of candidates) {
    const cs = getComputedStyle(el);
    if (!isVisible(el, cs)) continue;
    const z = parseInt(cs.zIndex, 10) || 0;
    if (z >= topZ) { topZ = z; target = el; }
  }
  if (!target) return null;

  const accName = (el) => {
    let t = el.getAttribute && el.getAttribute('aria-label');
    if (t && t.trim()) return t.trim();
    t = (el.innerText || '').trim();
    if (t) return t.slice(0, 120);
    t = el.getAttribute && el.getAttribute('title');
    if (t && t.trim()) return t.trim();
    return '';
  };
  const stableLocator = (el) => {
    const dt = el.getAttribute('data-testid');
    if (dt) return { by: 'data-testid', sel: `[data-testid="${dt}"]` };
    if (el.id) return { by: 'id', sel: `#${el.id}` };
    if (el.name) {
      return { by: 'name',
               sel: `${el.tagName.toLowerCase()}[name="${el.name}"]` };
    }
    const role = el.getAttribute('role') || el.tagName.toLowerCase();
    const nm = accName(el);
    if (nm) return { by: 'role-name', sel: `role=${role}[name="${nm.slice(0,60)}"]` };
    return { by: 'css', sel: el.tagName.toLowerCase() };
  };

  const containerHeading = (c) => {
    const h = c.querySelector('h1, h2, h3, [role="heading"], [class*="title" i]');
    if (h) return (h.innerText || '').trim().slice(0, 80);
    const ar = c.getAttribute('aria-label');
    if (ar && ar.trim()) return ar.trim().slice(0, 80);
    return '';
  };

  const elements = [];
  const sel2 = 'a[href], button, input, textarea, select, '
    + '[role="button"], [role="link"], [role="menuitem"], '
    + '[role="tab"], [role="option"]';
  for (const el of target.querySelectorAll(sel2)) {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role');
    let kind = null;
    if (tag === 'a' && el.hasAttribute('href')) kind = 'link';
    else if (tag === 'button' || role === 'button') kind = 'button';
    else if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      kind = (t === 'submit' || t === 'button') ? 'button' : 'input';
    } else if (tag === 'textarea') kind = 'input';
    else if (tag === 'select') kind = 'select';
    else if (role === 'link') kind = 'link';
    else if (['menuitem', 'tab', 'option'].includes(role)) kind = 'choice';
    if (!kind) continue;
    const loc = stableLocator(el);
    const rec = { kind, text: accName(el), locator: loc.sel,
                  locator_by: loc.by };
    if (kind === 'input') {
      rec.type = (el.getAttribute('type') || 'text').toLowerCase();
      if (el.hasAttribute('required')) rec.required = true;
      const ph = el.getAttribute('placeholder');
      if (ph) rec.placeholder = ph;
    }
    elements.push(rec);
  }
  return {
    surface_kind: target.getAttribute('role')
      || (target.getAttribute('class') || '').match(/(modal|popup|dropdown|menu)/i)?.[0]
      || target.tagName.toLowerCase(),
    title: containerHeading(target),
    aria_label: target.getAttribute('aria-label') || '',
    element_count: elements.length,
    elements,
  };
}
"""


def _signature(overlay: dict) -> str:
    """Identify overlays by role + class fingerprint, ignoring rect."""
    return f"{overlay.get('role') or ''}|{overlay.get('id') or ''}|{overlay.get('aria_label') or ''}|{overlay.get('class') or ''}"


def _is_hard_block(el: dict) -> bool:
    t = (el.get("text") or "").strip()
    return bool(t and _HARD_BLOCK_RE.match(t))


def _dump_page(record: dict, host: str) -> str:
    """Write one page's hidden dump to <QA>/<host>/dom/functions/<slug>.json."""
    slug = qa_paths.template_slug(record["template"])
    p = qa_paths.dom_dir(host, "functions") / f"{slug}.json"
    p.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return str(p)


def _dump_summary(payload: dict, host: str) -> str:
    p = qa_paths.dom_dir(host, "functions") / "_summary.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return str(p)


async def _probe_buttons(page: Any, buttons: list[dict],
                          before_sigs: set[str],
                          seen_signatures: set[str],
                          depth: int, max_depth: int) -> list[dict]:
    """Click each safe button; record outcome of each (opened-modal /
    navigated / no-op / click-failed / hard-block). For opened-modal,
    read overlay structure AND recurse into its buttons. Returns one
    record per probed button — never silently drops.
    """
    found: list[dict] = []
    for cand in buttons:
        if cand.get("kind") != "button":
            continue
        rec: dict = {
            "depth": depth,
            "opener_text": cand.get("text"),
            "opener_locator": cand.get("locator"),
            "outcome": None,
            "surface": None,
            "nested": [],
        }
        if _is_hard_block(cand):
            rec["outcome"] = "hard-block"
            found.append(rec)
            continue
        url_before = page.url
        try:
            await page.click(cand["locator"], timeout=3000)
        except Exception as exc:
            rec["outcome"] = "click-failed"
            rec["error"] = str(exc)[:120]
            found.append(rec)
            continue
        try:
            await page.wait_for_load_state("networkidle", timeout=1200)
        except Exception:
            pass
        if page.url != url_before:
            rec["outcome"] = "navigated"
            rec["new_url"] = page.url
            found.append(rec)
            continue
        try:
            after = await page.evaluate(_OVERLAY_SCAN_JS)
        except Exception:
            after = []
        new_o = [o for o in after if _signature(o) not in before_sigs]
        if not new_o:
            rec["outcome"] = "no-op"
            found.append(rec)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            continue
        sig = _signature(new_o[0])
        if sig in seen_signatures:
            rec["outcome"] = "modal-duplicate"
            found.append(rec)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            continue
        seen_signatures.add(sig)
        rec["outcome"] = "opened-modal"
        try:
            rec["surface"] = await page.evaluate(_OVERLAY_STRUCT_JS)
        except Exception:
            rec["surface"] = None
        if depth < max_depth and rec["surface"] and rec["surface"].get("elements"):
            inner_buttons = [el for el in rec["surface"]["elements"]
                             if el.get("kind") == "button"]
            if inner_buttons:
                nested_before = set(before_sigs) | {sig}
                rec["nested"] = await _probe_buttons(
                    page, inner_buttons, nested_before, seen_signatures,
                    depth + 1, max_depth,
                )
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(120)
        except Exception:
            pass
        found.append(rec)
    return found


@register("probe_hidden")
async def probe_hidden(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Discover hidden overlays on every map node. Consumes touch's per-page
    button list — no fresh scan needed. Enriches __map__ with hidden_surfaces.
    Tunnels recursively (default max_depth=2) so modal-inside-modal is caught.
    """
    page = ctx["backend"].page
    map_nodes: list[dict] = ctx["vars"].get("__map__") or []
    nav_map: list[dict] = ctx["vars"].get("__navigable_map__") or []
    nav_by_template = {n.get("template"): n.get("navigable_url")
                       for n in nav_map if n.get("template")}
    routes: list[dict] = ctx["vars"].get("__routes__") or []
    max_depth = int(step.get("max_depth") or 2)
    host = qa_paths.host_from_ctx(ctx)

    page_filter_arg = (ctx.get("input") or {}).get("page")
    filter_tpls = qa_paths.resolve_page_filter(page_filter_arg, routes)

    per_page: list[dict] = []
    written_paths: list[str] = []
    surfaces_total = 0
    pages_visited = 0
    pages_skipped: list[dict] = []

    for node in map_nodes:
        tpl = node["template"]
        if filter_tpls is not None and tpl not in filter_tpls:
            continue
        concrete = nav_by_template.get(tpl)
        if not concrete:
            pages_skipped.append({"template": tpl, "reason": "no concrete URL"})
            continue
        # Consume view's per-page elements directly — they're already
        # disambiguated (>> nth=N appended where needed) and chrome-filtered.
        view = node.get("view") or {}
        view_elements = view.get("elements") or []
        candidates: list[dict] = []
        unprobed_buttons: list[dict] = []
        for el in view_elements:
            if el.get("kind") != "button":
                continue
            if _is_hard_block(el):
                unprobed_buttons.append({**el, "skip_reason": "hard-block"})
                continue
            if el.get("disabled"):
                unprobed_buttons.append({**el, "skip_reason": "disabled"})
                continue
            candidates.append(el)
        # Always navigate — we read DOM for pre-mounted modals regardless of
        # whether there are clickable openers.
        try:
            await _navigate(page, concrete)
            try:
                await page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass
        except Exception as exc:
            pages_skipped.append({"template": tpl,
                                  "reason": f"nav fail: {exc!s}"})
            continue

        # 1) pre-mounted surfaces: read invisible modal/dropdown elements
        # already in the DOM. No clicks. Pure info gathering.
        try:
            pre_mounted = await page.evaluate(_PRE_MOUNTED_JS)
        except Exception:
            pre_mounted = []

        # 2) live overlays via clicks (only if we have safe candidates)
        page_surfaces: list[dict] = []
        if candidates:
            try:
                before_overlays = await page.evaluate(_OVERLAY_SCAN_JS)
            except Exception:
                before_overlays = []
            before_sigs = {_signature(o) for o in before_overlays}
            seen_signatures: set[str] = set()
            page_surfaces = await _probe_buttons(
                page, candidates, before_sigs, seen_signatures, depth=1,
                max_depth=max_depth,
            )

        def _count_opened(recs: list[dict]) -> int:
            return sum(
                (1 if r.get("outcome") == "opened-modal" else 0)
                + _count_opened(r.get("nested") or [])
                for r in recs
            )

        opened_count = _count_opened(page_surfaces) if page_surfaces else 0
        pre_count = len(pre_mounted)
        outcomes: dict[str, int] = {}
        for r in page_surfaces:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        if page_surfaces or pre_mounted or unprobed_buttons:
            surfaces_total += opened_count + pre_count
            pages_visited += 1
            record = {
                "template": tpl,
                "page_url": page.url,
                "probed_buttons": page_surfaces,
                "pre_mounted": pre_mounted,
                "unprobed_buttons": unprobed_buttons,
                "opened_modal_count": opened_count,
                "pre_mounted_count": pre_count,
                "outcomes": outcomes,
            }
            per_page.append(record)
            node["hidden_probed_buttons"] = page_surfaces
            node["hidden_pre_mounted"] = pre_mounted
            node["hidden_unprobed_buttons"] = unprobed_buttons
            written_paths.append(_dump_page(record, host))

    summary_path = ""
    if filter_tpls is None:
        summary_path = _dump_summary({
            "page_count": pages_visited,
            "surfaces_total": surfaces_total,
            "skipped": pages_skipped,
            "pages": [r["template"] for r in per_page],
        }, host)

    ctx["output"]["hidden_pages"] = pages_visited
    ctx["output"]["hidden_modals_total"] = sum(
        p.get("opened_modal_count", 0) for p in per_page)
    ctx["output"]["hidden_pre_mounted_total"] = sum(
        p.get("pre_mounted_count", 0) for p in per_page)
    ctx["output"]["hidden_probed_total"] = sum(
        len(p.get("probed_buttons", [])) for p in per_page)
    ctx["output"]["hidden_unprobed_total"] = sum(
        len(p.get("unprobed_buttons", [])) for p in per_page)
    ctx["output"]["hidden_skipped"] = len(pages_skipped)
    ctx["output"]["hidden_dump_paths"] = written_paths
    ctx["output"]["hidden_summary_path"] = summary_path
    if page_filter_arg:
        ctx["output"]["hidden_filter"] = page_filter_arg
        ctx["output"]["hidden_filter_matched"] = len(filter_tpls or set())
