"""qa_render — render TC proposals into Playwright JS POM + spec files
in BoardQA style (class-based POM with getByRole locators + action methods,
spec with TC_NN_MM naming).

Consumes:
  ctx["vars"]["__tc_proposals__"]   — propose_tc output
  ctx["vars"]["__map__"]            — enriched map (view + structure + hidden)
  step["pick"]: optional list of TC_NN_MM ids (default: all)
"""

import re
from typing import Any

from .. import qa_paths
from ..registry import register

_SAFE_RE = re.compile(r"[^A-Za-z0-9]+")
_WORD_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_ROLE_NAME_RE = re.compile(r'^role=(\w+)\[name="([^"]+)"\](?:\s*>>\s*nth=(\d+))?$')
_NTH_RE = re.compile(r"^(.+?)\s*>>\s*nth=(\d+)$")


def _safe(text: str | None) -> str:
    return _SAFE_RE.sub("", text or "") or "X"


def _short_text(text: str | None, max_words: int = 4) -> str:
    """Pick up to N first words for a locator name. Drops emails / hashes
    / long row-text noise that table anchors carry."""
    if not text:
        return ""
    # collapse whitespace + drop email-like / @ tokens
    words = [w for w in _WORD_SPLIT_RE.split(text) if w and "@" not in w]
    return " ".join(words[:max_words])


def _pascal_from_template(template: str) -> str:
    parts = [p for p in (template or "").split("/")
             if p and not p.startswith(":")]
    if not parts:
        parts = ["Root"]
    return "".join(p[:1].upper() + p[1:] for p in parts) + "Page"


def _camel(*parts: str) -> str:
    cleaned = [_safe(p) for p in parts if p]
    cleaned = [p for p in cleaned if p]
    if not cleaned:
        return "x"
    out = cleaned[0][:1].lower() + cleaned[0][1:]
    for p in cleaned[1:]:
        out += p[:1].upper() + p[1:]
    return out


def _locator_expr(locator: str) -> str:
    """Convert internal locator string to idiomatic Playwright JS expression.
    `role=button[name="X"]`     -> page.getByRole('button', { name: 'X' })
    `role=button[name="X"] >> nth=0` -> ...first() / .nth(N)
    `#id`, `[data-testid=...]`  -> page.locator('...')
    """
    base, nth = locator, None
    m = _NTH_RE.match(locator)
    if m:
        base, nth = m.group(1), int(m.group(2))
    rm = _ROLE_NAME_RE.match(locator) or _ROLE_NAME_RE.match(base)
    if rm:
        role, name = rm.group(1), rm.group(2).replace("'", "\\'")
        expr = f"page.getByRole('{role}', {{ name: '{name}' }})"
    else:
        loc = base.replace("\\", "\\\\").replace("'", "\\'")
        expr = f"page.locator('{loc}')"
    if nth is not None:
        expr += f".nth({nth})" if nth > 0 else ".first()"
    return expr


def _locator_name(kind: str, text: str | None, suffix: str) -> str:
    base = _short_text(text)
    if base:
        return _camel(base, suffix)
    return _camel(kind, suffix)


def _emit_pom(class_name: str, locators: list[dict],
              methods: list[dict]) -> str:
    lines = [
        "import { expect } from '@playwright/test';",
        "",
        f"export default class {class_name} {{",
        "    constructor(page) {",
        "        this.page = page;",
    ]
    for l in locators:
        lines.append(f"        this.{l['name']} = {l['expr']};")
    lines.append("    }")
    for m in methods:
        lines.append("")
        params = ", ".join(m.get("params") or [])
        lines.append(f"    async {m['name']}({params}) {{")
        for body_line in m["body"]:
            lines.append(f"        {body_line}")
        lines.append("    }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _emit_spec(class_name: str, instance_name: str, template: str,
               proposals: list[dict], pom_state: dict) -> str:
    """Render the spec with one test per TC_NN_MM proposal. The body of each
    test prefers POM methods over inline locator() calls — matches BoardQA."""
    feature_label = class_name.removesuffix("Page")
    lines = [
        "import { test, expect } from '@playwright/test';",
        f"import {class_name} from '../po/{class_name}.js';",
        "",
        f"test.describe('{feature_label}', () => {{",
        f"    let {instance_name};",
        "",
        "    test.beforeEach(async ({ page }) => {",
        f"        {instance_name} = new {class_name}(page);",
        f"        await page.goto('{template}');",
        "    });",
        "",
    ]
    for p in proposals:
        tc_id = p["id"]
        name = p["name"].replace("'", "\\'")
        body = _test_body(p, instance_name, pom_state)
        lines.append(f"    test('{tc_id} | {name}', async ({{ page }}) => {{")
        for b in body:
            lines.append(f"        {b}")
        lines.append("    });")
        lines.append("")
    lines.append("});")
    lines.append("")
    return "\n".join(lines)


def _test_body(proposal: dict, instance_name: str,
               pom_state: dict) -> list[str]:
    cat = proposal["category"]
    ev = proposal["evidence"]
    method_for_opener = pom_state.get("methods_by_opener", {})

    if cat == "form-valid":
        return [
            "// TODO: fill all inputs with valid data via POM `fill...` methods",
            f"// await {instance_name}.fillNameInput(USER.Roman.name);",
            f"// await {instance_name}.clickSaveBtn();",
            "// await expect(...).toBeVisible();  // success state",
        ]
    if cat == "form-empty":
        return [
            f"// await {instance_name}.clickSaveBtn();",
            f"// await expect({instance_name}.errorText).toBeVisible();",
        ]
    if cat == "form-invalid":
        return [
            f"// await {instance_name}.fillEmailInput('not-an-email');",
            f"// await {instance_name}.clickSaveBtn();",
            f"// await expect({instance_name}.errorText).toContain(/invalid/i);",
        ]
    if cat == "navigation":
        method = method_for_opener.get(ev.get("trigger_locator") or "")
        target = ev.get("to_template") or ""
        if method:
            return [
                f"await {instance_name}.{method}();",
                f"await expect(page).toHaveURL(/{re.escape(target)}/);",
            ]
        expr = _locator_expr(ev.get("trigger_locator") or "")
        return [
            f"await {expr}.click();",
            f"await expect(page).toHaveURL(/{re.escape(target)}/);",
        ]
    if cat == "modal-open":
        method = method_for_opener.get(ev.get("opener_locator") or "")
        first_inner = (ev.get("modal_inner_locators") or [None])[0]
        click = (f"await {instance_name}.{method}();" if method
                 else f"await {_locator_expr(ev.get('opener_locator') or '')}.click();")
        if first_inner:
            return [click,
                    f"await expect({_locator_expr(first_inner)}).toBeVisible();"]
        kind = ev.get("surface_kind") or "dialog"
        return [click,
                f"await expect(page.locator('[role=\"{kind}\"]')).toBeVisible();"]
    if cat == "modal-content":
        method = method_for_opener.get(ev.get("opener_locator") or "")
        click = (f"await {instance_name}.{method}();" if method
                 else f"await {_locator_expr(ev.get('opener_locator') or '')}.click();")
        body = [click]
        for ml in (ev.get("modal_inner_locators") or [])[:3]:
            body.append(f"await expect({_locator_expr(ml)}).toBeVisible();")
        return body
    if cat == "modal-close":
        method = method_for_opener.get(ev.get("opener_locator") or "")
        click = (f"await {instance_name}.{method}();" if method
                 else f"await {_locator_expr(ev.get('opener_locator') or '')}.click();")
        first_inner = (ev.get("modal_inner_locators") or [None])[0]
        body = [click, "await page.keyboard.press('Escape');"]
        if first_inner:
            body.append(
                f"await expect({_locator_expr(first_inner)}).toBeHidden();")
        return body
    if cat == "pre-mounted-presence":
        kind = ev.get("surface_kind") or "div"
        return [
            f"// pre-mounted {kind} — '{(ev.get('title') or '').replace(chr(39), '')}'",
            f"await expect(page.locator('[role=\"{kind}\"]').first())"
            ".toBeAttached();",
        ]
    if cat == "disabled-assertion":
        loc = ev.get("locator") or ""
        return [f"await expect({_locator_expr(loc)}).toBeDisabled();"]
    if cat == "search-filter":
        loc = ev.get("search_locator") or ""
        return [
            f"await {_locator_expr(loc)}.fill('zzz_unlikely_match');",
            "// expect row count to drop / empty-state visible",
            "// await expect(...).toHaveCount(0);",
        ]
    if cat == "noop-click":
        loc = ev.get("opener_locator") or ""
        return [
            f"// click should not navigate or throw",
            f"const before = page.url();",
            f"await {_locator_expr(loc)}.click();",
            f"await expect(page).toHaveURL(before);",
        ]
    if cat == "click-failed-review":
        return [
            "// HUMAN REVIEW REQUIRED — probe could not click this opener.",
            f"// opener: {ev.get('opener_text') or ''}",
            f"// locator: {ev.get('opener_locator')}",
            f"// error: {ev.get('error')}",
            "test.skip();",
        ]
    return ["// TODO: unknown category"]


def _build_pom_for_node(class_name: str, view_elements: list[dict],
                        probed: list[dict]) -> tuple[str, dict]:
    """Build POM text + a side-band state for the spec renderer
    (e.g. opener_locator -> method_name lookup)."""
    locators: list[dict] = []
    methods: list[dict] = []
    name_taken: set[str] = set()
    methods_by_opener: dict[str, str] = {}

    def _unique_locator_name(base_name: str) -> str:
        n = base_name
        i = 0
        while n in name_taken:
            i += 1
            n = f"{base_name}{i}"
        name_taken.add(n)
        return n

    def _add_button(text: str | None, locator: str) -> str:
        seed = text or _name_hint_from_locator(locator)
        loc_name = _unique_locator_name(
            _locator_name("button", seed, "Btn"))
        locators.append({"name": loc_name, "expr": _locator_expr(locator)})
        method_name = "click" + loc_name[:1].upper() + loc_name[1:]
        methods.append({
            "name": method_name,
            "params": [],
            "body": [f"await this.{loc_name}.click();"],
        })
        methods_by_opener[locator] = method_name
        return loc_name

    def _name_hint_from_locator(loc: str) -> str | None:
        # `#someId` -> "someId" -> useful camelCase seed
        m = re.match(r"^#([A-Za-z][\w-]*)$", loc)
        if m:
            return m.group(1)
        m = re.match(r'^[a-z]+\[name="([^"]+)"\]$', loc)
        if m:
            return m.group(1)
        return None

    def _add_input(text: str | None, locator: str,
                   placeholder: str | None) -> str:
        seed = text or placeholder or _name_hint_from_locator(locator)
        loc_name = _unique_locator_name(
            _locator_name("input", seed, "Input"))
        locators.append({"name": loc_name, "expr": _locator_expr(locator)})
        # idiomatic BoardQA: fillX(value)
        method_name = "fill" + loc_name[:1].upper() + loc_name[1:]
        methods.append({
            "name": method_name,
            "params": ["value"],
            "body": [f"await this.{loc_name}.fill(value);"],
        })
        return loc_name

    def _is_data_row_link(text: str | None) -> bool:
        """Heuristic: data-row anchors (table rows, list items with emails /
        ids / very long text). They pollute POMs with junk clickX methods
        and aren't testable navigation triggers. Keep the locator but skip
        the action method."""
        if not text:
            return False
        if "@" in text:  # email
            return True
        if len(text) > 35:
            return True
        # numeric-id-like tails: "Project Test eagle 1777282226883" etc
        tokens = text.split()
        if tokens and any(t.isdigit() and len(t) > 6 for t in tokens):
            return True
        return False

    def _add_link(text: str | None, locator: str) -> str:
        loc_name = _unique_locator_name(
            _locator_name("link", text, "Link"))
        locators.append({"name": loc_name, "expr": _locator_expr(locator)})
        if _is_data_row_link(text):
            # locator only — no clickX method, no opener registration
            return loc_name
        method_name = "click" + loc_name[:1].upper() + loc_name[1:]
        methods.append({
            "name": method_name,
            "params": [],
            "body": [f"await this.{loc_name}.click();"],
        })
        methods_by_opener[locator] = method_name
        return loc_name

    def _add_other(text: str | None, locator: str, kind: str) -> str:
        loc_name = _unique_locator_name(_locator_name(kind, text, ""))
        locators.append({"name": loc_name, "expr": _locator_expr(locator)})
        return loc_name

    for el in view_elements:
        kind = el.get("kind")
        if kind == "button":
            _add_button(el.get("text"), el["locator"])
        elif kind == "input":
            _add_input(el.get("text"), el["locator"],
                       el.get("placeholder"))
        elif kind == "link":
            _add_link(el.get("text"), el["locator"])
        else:
            _add_other(el.get("text"), el["locator"], kind or "el")

    # modal surface inner locators — useful in tests
    for r in probed:
        if r.get("outcome") != "opened-modal":
            continue
        surface = r.get("surface") or {}
        for el in surface.get("elements") or []:
            kind = el.get("kind")
            prefix = "modal"
            base_name = _locator_name(prefix + (kind or "El").capitalize(),
                                       el.get("text"), "")
            if base_name in name_taken:
                continue
            name_taken.add(base_name)
            locators.append({
                "name": base_name,
                "expr": _locator_expr(el["locator"]),
            })

    pom_text = _emit_pom(class_name, locators, methods)
    return pom_text, {"methods_by_opener": methods_by_opener}


@register("qa_render")
async def qa_render(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Render TC proposals into POM classes (QA/<host>/po/) + spec files
    (QA/<host>/pom/). Files use stable per-page names — re-running overwrites
    in place; pages outside the run are left untouched."""
    proposals = ctx["vars"].get("__tc_proposals__") or []
    map_nodes = ctx["vars"].get("__map__") or []

    pick: list[str] = step.get("pick") or []
    if pick:
        proposals = [p for p in proposals if p["id"] in pick]
    if not proposals:
        ctx["output"]["qa_proposal_count"] = 0
        return

    host = qa_paths.host_from_ctx(ctx)
    po_dir = qa_paths.po_dir(host)
    pom_dir = qa_paths.pom_dir(host)

    by_page: dict[str, list[dict]] = {}
    for p in proposals:
        by_page.setdefault(p["page_template"], []).append(p)

    written: list[str] = []
    for template, page_props in by_page.items():
        node = next((n for n in map_nodes if n["template"] == template), None)
        if not node:
            continue
        view_elements = (node.get("view") or {}).get("elements") or []
        probed = node.get("hidden_probed_buttons") or []

        class_name = _pascal_from_template(template)
        instance_name = class_name[:1].lower() + class_name[1:]

        pom_text, pom_state = _build_pom_for_node(
            class_name, view_elements, probed)
        pom_path = po_dir / f"{class_name}.js"
        pom_path.write_text(pom_text, encoding="utf-8")
        written.append(str(pom_path))

        spec_text = _emit_spec(class_name, instance_name, template,
                               page_props, pom_state)
        spec_path = pom_dir / f"{class_name}.spec.js"
        spec_path.write_text(spec_text, encoding="utf-8")
        written.append(str(spec_path))

    ctx["output"]["qa_proposal_count"] = len(proposals)
    ctx["output"]["qa_files_written"] = len(written)
    ctx["output"]["qa_files"] = written
    ctx["output"]["qa_po_dir"] = str(po_dir)
    ctx["output"]["qa_pom_dir"] = str(pom_dir)
