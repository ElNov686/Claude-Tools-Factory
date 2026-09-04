"""TOUCH — per-page DOM organizer. Pure consumer of view's output.

After view collected each page's visible interactives (with container info,
rect, and unique locators), touch groups them by container and resolves
anchor hrefs to target templates (static transitions). No DOM access, no
clicks — pure Python pass.

Result: each map node gets `node["structure"] = {containers, transitions}`
which downstream hidden / tc consume.
"""

import json
from typing import Any
from urllib.parse import urlparse

from .. import qa_paths
from ..registry import register
from .map import _resolve_href_to_template


def _dump_page(record: dict, host: str) -> str:
    """Write one page's touch dump to <QA>/<host>/dom/containers/<slug>.json."""
    slug = qa_paths.template_slug(record["template"])
    p = qa_paths.dom_dir(host, "containers") / f"{slug}.json"
    p.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return str(p)


def _dump_summary(payload: dict, host: str) -> str:
    p = qa_paths.dom_dir(host, "containers") / "_summary.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return str(p)


@register("touch_page")
async def touch_page(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Group view's per-page elements into containers and extract static
    transitions (anchor href -> target template). Honors input.page filter."""
    page = ctx["backend"].page
    map_nodes: list[dict] = ctx["vars"].get("__map__") or []
    routes: list[dict] = ctx["vars"].get("__routes__") or []
    nav_map: list[dict] = ctx["vars"].get("__navigable_map__") or []
    nav_by_template = {n.get("template"): n.get("navigable_url")
                       for n in nav_map if n.get("template")}
    origin = f"{urlparse(page.url).scheme}://{urlparse(page.url).netloc}" \
        if page.url else ""
    host = qa_paths.host_from_ctx(ctx)

    page_filter_arg = (ctx.get("input") or {}).get("page")
    filter_tpls = qa_paths.resolve_page_filter(page_filter_arg, routes)

    per_page: list[dict] = []
    skipped: list[dict] = []
    written_paths: list[str] = []
    elements_total = 0
    transitions_total = 0

    for node in map_nodes:
        tpl = node["template"]
        if filter_tpls is not None and tpl not in filter_tpls:
            continue
        view = node.get("view")
        if not view:
            skipped.append({"template": tpl, "reason": "no view data"})
            continue
        elements = view.get("elements") or []
        # Group by container fingerprint (tag + name + role)
        groups: dict[tuple, dict] = {}
        for el in elements:
            c = el.get("container") or {}
            key = (c.get("tag"), c.get("name"), c.get("role"))
            if key not in groups:
                groups[key] = {
                    "name": c.get("name") or c.get("tag") or "(no-container)",
                    "tag": c.get("tag"),
                    "role": c.get("role"),
                    "elements": [],
                }
            groups[key]["elements"].append(el)
        containers = []
        for g in groups.values():
            g["element_count"] = len(g["elements"])
            containers.append(g)

        # Static transitions: anchor href -> target template
        transitions: list[dict] = []
        for el in elements:
            href = el.get("href")
            if not href:
                continue
            target = _resolve_href_to_template(href, routes, origin)
            if not target or target == tpl:
                continue
            transitions.append({
                "trigger_text": el.get("text"),
                "trigger_locator": el["locator"],
                "to_template": target,
            })

        record = {
            "template": tpl,
            "page_url": nav_by_template.get(tpl),
            "container_count": len(containers),
            "element_count": len(elements),
            "transition_count": len(transitions),
            "containers": containers,
            "transitions": transitions,
        }
        per_page.append(record)
        node["structure"] = {
            "containers": containers,
            "transitions": transitions,
        }
        written_paths.append(_dump_page(record, host))
        elements_total += len(elements)
        transitions_total += len(transitions)

    summary_path = ""
    if filter_tpls is None:
        summary_path = _dump_summary({
            "page_count": len(per_page),
            "elements_total": elements_total,
            "transitions_total": transitions_total,
            "skipped": skipped,
            "pages": [r["template"] for r in per_page],
        }, host)

    ctx["output"]["touch_pages"] = len(per_page)
    ctx["output"]["touch_elements_total"] = elements_total
    ctx["output"]["touch_transitions_total"] = transitions_total
    ctx["output"]["touch_skipped"] = len(skipped)
    ctx["output"]["touch_dump_paths"] = written_paths
    ctx["output"]["touch_summary_path"] = summary_path
    if page_filter_arg:
        ctx["output"]["touch_filter"] = page_filter_arg
        ctx["output"]["touch_filter_matched"] = len(filter_tpls or set())
