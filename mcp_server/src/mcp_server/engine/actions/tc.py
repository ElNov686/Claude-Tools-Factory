"""tc — propose_tc action: read enriched __map__ (after map/view/touch/
hidden) and emit a TC_NN_MM proposal list — the chat-facing menu the user
picks from. Universal templates per category (form / transition / modal /
pre-mounted / disabled / search-filter / no-op / click-failed)."""

from typing import Any

from .. import qa_paths
from ..registry import register


# ---------------------------------------------------------------------------
# Universal TC templates — applied per page on hidden + view + touch data.
# Each template emits one TC_NN_MM record per match. Categories are
# product-agnostic; they fire on any SPA that surfaces the same kind of
# UI signal (form, navigable link, modal, etc).
# ---------------------------------------------------------------------------

_PAGE_NAME_TRIM = 28


def _page_id(template: str, index: int) -> str:
    # NN = 1-based index, zero-padded to 2 digits (matches BoardQA convention)
    return f"{index:02d}"


def _short(text: str | None, n: int = _PAGE_NAME_TRIM) -> str:
    s = (text or "").strip()
    if len(s) > n:
        s = s[: n - 1] + "…"
    return s


def _page_title(node: dict) -> str:
    """Short human title for a page (used in TC names)."""
    name = node.get("name") or ""
    if name:
        return name
    tpl = node.get("template", "")
    seg = [s for s in tpl.split("/") if s and not s.startswith(":")]
    return seg[-1] if seg else tpl


def _proposals_for_page(node: dict, page_idx: int) -> list[dict]:
    page_nn = _page_id(node.get("template", ""), page_idx)
    page_title = _page_title(node)
    structure = node.get("structure") or {}
    containers = structure.get("containers") or []
    transitions = structure.get("transitions") or []
    probed = node.get("hidden_probed_buttons") or []
    pre = node.get("hidden_pre_mounted") or []
    unprobed = node.get("hidden_unprobed_buttons") or []

    proposals: list[dict] = []
    mm = 1

    def _add(category: str, name: str, evidence: dict) -> None:
        nonlocal mm
        proposals.append({
            "id": f"TC_{page_nn}_{mm:02d}",
            "page_id": page_nn,
            "page_template": node.get("template"),
            "page_title": page_title,
            "category": category,
            "name": name,
            "evidence": evidence,
        })
        mm += 1

    # 1) Form-like containers — ANY container with at least one input + at
    # least one submit-ish button counts (semantic <form> OR div with inputs
    # + a Save / Submit / Send / Apply button). Universal across SPAs that
    # use div-based layouts (most modern Vue/React apps).
    _SUBMIT_TOKENS = ("save", "submit", "apply", "send", "continue", "next",
                      "create", "add")
    for c in containers:
        elems = c.get("elements") or []
        inputs = [e for e in elems if e.get("kind") == "input"]
        if not inputs:
            continue
        buttons = [e for e in elems if e.get("kind") == "button"]
        submit_btn = next(
            (b for b in buttons
             if any(t in (b.get("text") or "").lower()
                    for t in _SUBMIT_TOKENS)),
            None,
        )
        if not submit_btn and c.get("tag") != "form":
            continue
        cname = c.get("name") or c.get("tag") or "Form"
        ev = {"container": cname, "tag": c.get("tag"),
              "input_count": len(inputs),
              "submit_locator": (submit_btn or {}).get("locator"),
              "submit_text": (submit_btn or {}).get("text"),
              "input_locators": [e.get("locator") for e in inputs]}
        _add("form-valid", f"Submit {_short(cname)} with valid data", ev)
        _add("form-empty", f"Submit {_short(cname)} with empty fields", ev)
        _add("form-invalid",
             f"Submit {_short(cname)} with invalid data", ev)

    # 1b) Search-and-action — input[type=search] / placeholder containing
    # 'search' adjacent to action buttons (Invite, Add, Filter…). Common
    # SPA pattern that lives outside any <form>.
    for c in containers:
        elems = c.get("elements") or []
        search_input = next(
            (e for e in elems if e.get("kind") == "input"
             and ((e.get("type") == "search")
                  or "search" in (e.get("placeholder") or "").lower())),
            None,
        )
        if not search_input:
            continue
        ev = {"container": c.get("name"),
              "search_locator": search_input.get("locator"),
              "placeholder": search_input.get("placeholder")}
        _add("search-filter",
             f"Search {_short(c.get('name') or 'list')} filters rows",
             ev)

    # 2) Transitions -> "navigates to <target>"
    seen_targets: set[str] = set()
    for t in transitions:
        target = t.get("to_template")
        if not target or target in seen_targets:
            continue
        seen_targets.add(target)
        ev = {"trigger_locator": t.get("trigger_locator"),
              "trigger_text": t.get("trigger_text"),
              "to_template": target}
        _add("navigation",
             f"Click '{_short(t.get('trigger_text') or 'link')}' "
             f"navigates to {_short(target)}",
             ev)

    # 3) Modals discovered by hidden -> open / content / close (3 tests each)
    seen_modal_keys: set[str] = set()
    for r in probed:
        if r.get("outcome") != "opened-modal":
            continue
        opener_loc = r.get("opener_locator") or ""
        if opener_loc in seen_modal_keys:
            continue
        seen_modal_keys.add(opener_loc)
        surface = r.get("surface") or {}
        surf_name = surface.get("title") or surface.get("surface_kind") or "modal"
        ev = {"opener_locator": opener_loc,
              "opener_text": r.get("opener_text"),
              "surface_kind": surface.get("surface_kind"),
              "modal_element_count": surface.get("element_count"),
              "modal_inner_locators": [
                  el.get("locator")
                  for el in (surface.get("elements") or [])[:5]
              ]}
        _add("modal-open",
             f"Click '{_short(r.get('opener_text') or '')}' opens "
             f"{_short(surf_name)}", ev)
        _add("modal-content",
             f"{_short(surf_name)} shows expected fields", ev)
        _add("modal-close", f"{_short(surf_name)} can be closed", ev)

    # 4) Pre-mounted (already in DOM) -> presence + activation
    for s in pre:
        kind = s.get("surface_kind") or "widget"
        title = s.get("title") or kind
        ev = {"surface_kind": kind, "title": title,
              "element_count": s.get("element_count")}
        _add("pre-mounted-presence",
             f"{_short(title)} {kind} is present in DOM", ev)

    # 5) Disabled buttons -> assert disabled
    for u in unprobed:
        if u.get("skip_reason") != "disabled":
            continue
        ev = {"locator": u.get("locator"), "text": u.get("text")}
        _add("disabled-assertion",
             f"'{_short(u.get('text') or '')}' is disabled when expected",
             ev)

    # 6) No-op buttons -> sanity test that click doesn't error
    seen_noop: set[str] = set()
    for r in probed:
        if r.get("outcome") != "no-op":
            continue
        loc = r.get("opener_locator") or ""
        if loc in seen_noop:
            continue
        seen_noop.add(loc)
        text = r.get("opener_text") or ""
        ev = {"opener_locator": loc, "opener_text": text}
        _add("noop-click",
             f"Click '{_short(text or 'button')}' does not raise or navigate",
             ev)

    # 7) Click-failed buttons -> flag for human review (low confidence
    # candidate, often off-screen / overlay-obstructed / disabled-but-
    # not-flagged elements)
    seen_failed: set[str] = set()
    for r in probed:
        if r.get("outcome") != "click-failed":
            continue
        loc = r.get("opener_locator") or ""
        if loc in seen_failed:
            continue
        seen_failed.add(loc)
        text = r.get("opener_text") or ""
        ev = {"opener_locator": loc, "opener_text": text,
              "error": (r.get("error") or "")[:120]}
        _add("click-failed-review",
             f"'{_short(text or 'button')}' was unclickable during probe — "
             "verify by hand",
             ev)

    return proposals


def _dump_proposals(payload: dict, host: str) -> str:
    """Single stable file: <QA>/<host>/pom/_proposals.json."""
    p = qa_paths.pom_dir(host) / "_proposals.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return str(p)


@register("propose_tc")
async def propose_tc(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Read enriched __map__ and produce a structured TC_NN_MM proposal list.
    Pure consumer of map+view+touch+hidden output — no DOM access, no clicks.
    """
    map_nodes: list[dict] = ctx["vars"].get("__map__") or []
    # Only propose for pages that have at least some discovered signal
    rich_nodes: list[dict] = []
    for node in map_nodes:
        has_data = bool(
            node.get("structure")
            or node.get("hidden_probed_buttons")
            or node.get("hidden_pre_mounted")
            or node.get("hidden_unprobed_buttons")
        )
        if has_data:
            rich_nodes.append(node)

    all_proposals: list[dict] = []
    for idx, node in enumerate(rich_nodes, start=1):
        all_proposals.extend(_proposals_for_page(node, idx))

    # Group by category for a quick top-line summary
    by_cat: dict[str, int] = {}
    for p in all_proposals:
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1

    dump_path = _dump_proposals({
        "page_count": len(rich_nodes),
        "proposal_count": len(all_proposals),
        "by_category": by_cat,
        "proposals": all_proposals,
    }, qa_paths.host_from_ctx(ctx))

    ctx["vars"]["__tc_proposals__"] = all_proposals
    ctx["output"]["tc_proposal_count"] = len(all_proposals)
    ctx["output"]["tc_pages"] = len(rich_nodes)
    ctx["output"]["tc_by_category"] = by_cat
    ctx["output"]["tc_dump_path"] = dump_path

