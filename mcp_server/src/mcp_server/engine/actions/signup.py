"""run_signup_pattern — universal pattern-based registration runner.

Mirrors the login architecture, but for sign-up flows. A sign-up pattern is
a host-specific yaml under

    MS/patterns/web/<host>.signup.yaml

that lists ordered steps to drive a fresh browser through the registration
form (open_browser / goto / wait_for_selector / fill / click /
assert_url_contains) and may include an optional pause_for_user step for
email-verification codes.

If no host yaml exists, the universal DOM-driven fallback kicks in. It
runs a simple loop:

    probe → for each Handler in HANDLERS: fill what it can →
    advance (Next/Skip/Consent) → wait → repeat

Each Handler is one pluggable "thing the tool knows how to do on a page"
(plain text inputs, custom combobox, radio pick-list with create-your-own,
etc). Adding a new UI shape = add a new Handler — no monolith to touch.

Behavior:
- If ctx already has a backend (e.g. set by use_session or a test fixture),
  reuse it. Otherwise open a new browser.
- Run the pattern steps in-place on the same ctx using the engine executor.
- If keep_open is true and the run completes ok, hand the browser off to
  live_sessions so a later tool can attach it via use_session. Otherwise
  close it cleanly.
- ctx['vars']['__forced_signup_pattern__'] is a test-override escape hatch
  so tests don't need a real pattern file on disk.
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ... import live_sessions
from ..registry import register
from ..value_resolver import resolve

_PATTERNS_DIR = Path(__file__).resolve().parents[3].parent / "patterns"

# ============================================================
#  Section 1: PROBE — single JS that reads the current page
# ============================================================
# Universal probe — read every visible interactive on the page and the
# accessible label sources for each input so Python can decide what to fill.
_PROBE_FORM_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const labelOf = (el) => {
    const id = el.id;
    if (id) {
      const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (lab) {
        const t = norm(lab.innerText);
        if (t) return t.slice(0, 120);
      }
    }
    const wrap = el.closest('label');
    if (wrap) {
      const t = norm(wrap.innerText);
      if (t) return t.slice(0, 120);
    }
    // aria-labelledby
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const parts = labelledby.split(/\s+/)
        .map(x => document.getElementById(x))
        .filter(Boolean)
        .map(n => norm(n.innerText))
        .filter(Boolean);
      if (parts.length) return parts.join(' ').slice(0, 120);
    }
    return '';
  };
  const isVisible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    // Note: do NOT filter by opacity === 0. Material/MDC and many SPA UI
    // libraries hide the native <input> with opacity:0 and overlay a styled
    // visual on top — the input is still focusable and clickable. Filtering
    // by opacity makes the probe blind to half of modern forms.
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2 && r.top < window.innerHeight + 200;
  };
  const inputs = [];
  for (const el of document.querySelectorAll(
        'input:not([type=hidden]), textarea, select, [role="radio"]')) {
    if (!isVisible(el)) continue;
    const type = (el.getAttribute('type')
                  || el.getAttribute('role')
                  || el.tagName.toLowerCase());
    const filledAlready = (el.value || '').length > 0;
    inputs.push({
      tag: el.tagName.toLowerCase(),
      type: type.toLowerCase(),
      id: el.id || '',
      name: el.getAttribute('name') || '',
      placeholder: el.getAttribute('placeholder') || '',
      aria_label: el.getAttribute('aria-label') || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      label: labelOf(el),
      required: el.hasAttribute('required'),
      disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
      value_already: filledAlready,
    });
  }
  const buttons = [];
  for (const el of document.querySelectorAll(
        'button, input[type=submit], input[type=button], [role="button"]')) {
    if (!isVisible(el)) continue;
    if (el.disabled) continue;
    if (el.getAttribute('aria-disabled') === 'true') continue;
    buttons.push({
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      text: norm(el.innerText) || norm(el.value) ||
            norm(el.getAttribute('aria-label')),
      id: el.id || '',
    });
  }
  // Combobox-style custom dropdowns — present on many SPA sign-up flows
  // (Gmail's Month + Gender, for example). They are not <select>, but
  // they semantically fill a form field — so we treat them like inputs.
  const comboboxes = [];
  const seenCombo = new Set();
  for (const el of document.querySelectorAll(
        '[role="combobox"], [aria-haspopup="listbox"], '
        + '[aria-haspopup="menu"], [aria-haspopup="true"], '
        + '[role="button"][aria-expanded]')) {
    if (!isVisible(el)) continue;
    if (el.disabled) continue;
    // Dedupe: Material/SPA often nests combobox wrappers. Key by
    // top-most ancestor that still has the role.
    const key = (el.id || '') + '|' + (el.getAttribute('aria-label') || '')
              + '|' + Math.round(el.getBoundingClientRect().top);
    if (seenCombo.has(key)) continue;
    seenCombo.add(key);
    comboboxes.push({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      id: el.id || '',
      aria_label: el.getAttribute('aria-label') || '',
      label: labelOf(el),
      text: norm(el.innerText).slice(0, 80),
    });
  }
  return { inputs, buttons, comboboxes, url: location.href };
}
"""


# ============================================================
#  Section 2: MATCHING — env keys <-> visible fields
# ============================================================

# Token aliases — when an env key name has terms that don't literally
# appear as form labels, add synonyms here. The matcher walks every
# SIGNUP_* env var, splits the key into tokens, augments with aliases,
# and looks for ANY of them in a field's accessibility hints.
_TOKEN_ALIASES: dict[str, list[str]] = {
    "first": ["first", "given", "firstname"],
    "last": ["last", "family", "surname", "lastname"],
    "name": ["name", "имя", "фамилия"],
    "username": ["username", "handle", "userid", "login", "user"],
    "email": ["email", "mail", "почт", "e-mail"],
    "phone": ["phone", "mobile", "telephone", "tel", "телефон"],
    "dob": ["birth", "birthday", "dob"],
    "day": ["day", "день"],
    "month": ["month", "месяц"],
    "year": ["year", "год"],
    "company": ["company", "organization", "organisation", "компан"],
    "country": ["country", "страна"],
    "zip": ["zip", "postal", "postcode", "индекс"],
    "gender": ["gender", "sex", "пол"],
    "address": ["address", "street", "адрес"],
    "city": ["city", "town", "город"],
    "state": ["state", "province", "регион"],
}

# Value aliases — when the env value the user wrote is colloquial but the
# UI shows the formal form ("Man" → "Male"). Used when picking options from
# combobox menus. Lowercased keys; each value is a list of acceptable
# alternatives tried in order.
_VALUE_ALIASES: dict[str, list[str]] = {
    "man": ["male", "man"],
    "woman": ["female", "woman"],
    "male": ["male"],
    "female": ["female"],
    "other": ["other", "non-binary", "prefer not to say", "rather not say",
              "custom"],
}

_NEXT_BUTTON_RE = re.compile(
    r"^\s*(?:next|continue|submit|sign\s*up|create\s*account|register|"
    r"далее|продолжить)\s*$",
    re.IGNORECASE,
)

# User instruction: skip whatever is skippable during sign-up. Matches the
# common "skip / not now / maybe later / no thanks" UI buttons.
_SKIP_BUTTON_RE = re.compile(
    r"^\s*(?:skip|not\s*now|maybe\s*later|no\s*thanks|пропустить|позже)\s*$",
    re.IGNORECASE,
)

# Consent / cookie / TOS acceptance — clicked when it stands alone on a page.
_CONSENT_BUTTON_RE = re.compile(
    r"^\s*(?:i\s*agree|i\s*accept|accept(?:\s*all)?|allow(?:\s*all)?|"
    r"got\s*it|ok|разрешить|принять|согласен)\s*$",
    re.IGNORECASE,
)

# Radio / picklist option meaning "let me type my own": triggers
# RadioPickListHandler to click it so a custom-input becomes enabled.
_CREATE_OWN_RE = re.compile(
    r"create\s*(?:your)?\s*own|use\s*my\s*own|custom|enter\s*(?:my)?\s*own|"
    r"другое|своё|свое",
    re.IGNORECASE,
)


def _field_text(field: dict) -> str:
    """Build a single searchable string from all label-ish sources."""
    parts = [
        field.get("label"), field.get("aria_label"),
        field.get("placeholder"), field.get("name"),
        field.get("id"), field.get("autocomplete"), field.get("text"),
    ]
    return " ".join(p for p in parts if p).lower()


def _expand_tokens(env_key: str) -> list[str]:
    """SIGNUP_FIRST_NAME -> ['first', 'name'] + aliases. Lowercased."""
    raw = env_key.removeprefix("SIGNUP_").lower().split("_")
    tokens: set[str] = set()
    for r in raw:
        tokens.add(r)
        tokens.update(_TOKEN_ALIASES.get(r, []))
    return sorted(tokens, key=len, reverse=True)  # longer tokens first


def _signup_env_keys(env: dict) -> list[str]:
    return [k for k in env.keys() if k.startswith("SIGNUP_") and env[k]]


def _best_env_for_haystack(haystack: str, env: dict,
                            exclude: set[str] | None = None) -> tuple[str, str] | None:
    """Score every SIGNUP_* key by how many of its tokens appear in haystack.
    Tie-break by which key has its longest token actually present."""
    exclude = exclude or set()
    best_score = 0
    best_key: str | None = None
    for env_key in _signup_env_keys(env):
        if env_key in exclude:
            continue
        tokens = _expand_tokens(env_key)
        hits = sum(1 for t in tokens if t and t in haystack)
        if hits > best_score:
            best_score, best_key = hits, env_key
    if best_key:
        return best_key, env[best_key]
    return None


def _match_env_for(field: dict, env: dict,
                    exclude: set[str] | None = None) -> tuple[str, str] | None:
    """Return (env_key, value) for the best matching env entry against this
    field's accessibility hints. Dynamic: walks every SIGNUP_* env key."""
    if field.get("disabled") or field.get("value_already"):
        return None
    ftype = (field.get("type") or "").lower()
    exclude = exclude or set()

    # Unambiguous type fast paths
    if ftype == "password" and "SIGNUP_PASSWORD" not in exclude:
        v = env.get("SIGNUP_PASSWORD")
        if v:
            return "SIGNUP_PASSWORD", v
    if ftype == "email":
        for key in ("SIGNUP_EMAIL", "SIGNUP_USERNAME"):
            if key in exclude:
                continue
            v = env.get(key)
            if v:
                return key, v

    return _best_env_for_haystack(
        _field_text(field), env,
        exclude=exclude | {"SIGNUP_PASSWORD"},
    )


def _selector_for(field: dict) -> str | None:
    """Build a Playwright-safe CSS selector from a probed field/button.
    Handles Gmail-style funky IDs (`:r1:`) by switching to [id=\"...\"]."""
    fid = field.get("id") or ""
    tag = field.get("tag") or "*"
    if fid:
        if re.match(r"^[A-Za-z_][\w-]*$", fid):
            return f"#{fid}"
        return f'{tag}[id="{fid}"]'
    name = field.get("name") or ""
    if name:
        return f'{tag}[name="{name}"]'
    return None


def _expand_value_aliases(value: str) -> list[str]:
    """For dropdown option picking: try the exact value first, then any
    known aliases ('Man' → ['Man', 'Male'])."""
    key = value.strip().lower()
    aliases = _VALUE_ALIASES.get(key)
    if aliases:
        out: list[str] = []
        for v in [value, *aliases]:
            if v not in out:
                out.append(v)
        return out
    return [value]


async def _pick_option(page: Any, value: str, timeout_ms: int = 3500) -> bool:
    """After a combobox/menu is open, click the option matching value.
    Tries exact name first, then aliases. Anchored regex to avoid
    'Man' matching 'Woman'."""
    candidates = _expand_value_aliases(value)
    for v in candidates:
        pat = re.compile(rf"^\s*{re.escape(v)}\s*$", re.IGNORECASE)
        for role in ("option", "menuitem", "listitem"):
            try:
                opt = page.get_by_role(role, name=pat).first
                await opt.wait_for(state="visible", timeout=timeout_ms)
                await opt.click(timeout=timeout_ms)
                return True
            except Exception:
                continue
    # Looser final fallback: any visible element whose text equals one
    # of the candidates (still anchored). Works for plain <li>/<div> menus.
    for v in candidates:
        try:
            loc = page.get_by_text(
                re.compile(rf"^\s*{re.escape(v)}\s*$", re.IGNORECASE),
                exact=False,
            ).first
            await loc.wait_for(state="visible", timeout=1500)
            await loc.click(timeout=1500)
            return True
        except Exception:
            continue
    return False


async def _open_and_pick_combobox(page: Any, trigger_loc: Any, value: str,
                                    timeout_ms: int = 3500) -> bool:
    """Click a combobox trigger, wait for the option menu, pick the option."""
    try:
        await trigger_loc.click(timeout=timeout_ms)
    except Exception:
        return False
    return await _pick_option(page, value, timeout_ms=timeout_ms)


# ============================================================
#  Section 3: HANDLERS — pluggable units of "fill what I know"
# ============================================================

class FillResult:
    __slots__ = ("filled",)

    def __init__(self, filled: list[str] | None = None) -> None:
        self.filled: list[str] = filled or []

    def __bool__(self) -> bool:
        return bool(self.filled)


class Handler:
    """A page-shape handler. Each one knows about ONE kind of UI."""
    name = "handler"

    async def fill(self, page: Any, probe: dict,
                   env: dict, taken: set[str]) -> FillResult:
        """Fill whatever this handler recognizes on the current probe.
        Mutates `taken` with env keys it consumed so later handlers in
        the same tick don't double-fill."""
        return FillResult()


class TextInputsHandler(Handler):
    """The default: <input>, <textarea>, <select> matched by env tokens."""
    name = "text_inputs"

    async def fill(self, page: Any, probe: dict,
                   env: dict, taken: set[str]) -> FillResult:
        filled: list[str] = []
        for field in probe["inputs"]:
            ftype = (field.get("type") or "").lower()
            if ftype in ("radio", "checkbox"):
                continue  # owned by RadioPickListHandler
            hit = _match_env_for(field, env, exclude=taken)
            if not hit:
                continue
            env_key, value = hit
            sel = _selector_for(field)
            if not sel:
                continue
            tag = (field.get("tag") or "").lower()
            try:
                if tag == "select":
                    try:
                        await page.locator(sel).first.select_option(
                            label=value, timeout=2500)
                    except Exception:
                        await page.locator(sel).first.select_option(
                            value=value, timeout=2500)
                else:
                    await page.locator(sel).first.fill(value, timeout=4000)
                filled.append(env_key)
                taken.add(env_key)
            except Exception:
                continue
        return FillResult(filled)


class ComboboxHandler(Handler):
    """Custom dropdowns: role=combobox / listbox / role=button[aria-expanded].
    Click trigger → wait for option menu → pick value (with aliases)."""
    name = "combobox"

    async def fill(self, page: Any, probe: dict,
                   env: dict, taken: set[str]) -> FillResult:
        filled: list[str] = []
        for cb in probe.get("comboboxes", []):
            # Skip combobox-styled Next button
            if _NEXT_BUTTON_RE.match(cb.get("text", "")):
                continue
            haystack = _field_text(cb)
            hit = _best_env_for_haystack(haystack, env, exclude=taken)
            if not hit:
                continue
            env_key, value = hit
            trigger = self._build_trigger(page, cb)
            if trigger is None:
                continue
            if await _open_and_pick_combobox(page, trigger, value):
                filled.append(env_key)
                taken.add(env_key)
        return FillResult(filled)

    @staticmethod
    def _build_trigger(page: Any, cb: dict) -> Any | None:
        """Locate the clickable trigger for this combobox. Prefers stable
        attributes; falls back to role+name."""
        fid = cb.get("id") or ""
        if fid:
            if re.match(r"^[A-Za-z_][\w-]*$", fid):
                return page.locator(f"#{fid}").first
            return page.locator(f'[id="{fid}"]').first
        aria = cb.get("aria_label") or ""
        if aria:
            return page.locator(f'[aria-label="{aria}"]').first
        text = cb.get("text") or cb.get("label") or ""
        if text:
            pat = re.compile(re.escape(text), re.IGNORECASE)
            for role in ("combobox", "button", "listbox"):
                try:
                    return page.get_by_role(role, name=pat).first
                except Exception:
                    continue
        return None


class PickGroupHandler(Handler):
    """Universal "pick one of N" handler. Sees ANY group of choices on the
    page (native radios sharing a `name`, role=radio elements, or button
    cards) and satisfies the page by clicking one.

    Pick order (so the tool stays helpful on unknown UIs):
      1. The choice whose visible text best matches an unused SIGNUP_* env
         value (e.g. SIGNUP_USERNAME 'slava_factory_01' matches a radio
         labeled 'slava_factory_01@example.com').
      2. The 'create your own / custom / другое' choice — when picked,
         reveals a text input that TextInputsHandler then fills.
      3. The first visible choice (default selection — at least satisfies
         'must pick one' validation).

    After clicking, re-probes once so newly-revealed inputs get filled in
    the same tick.
    """
    name = "pick_group"

    async def fill(self, page: Any, probe: dict,
                   env: dict, taken: set[str]) -> FillResult:
        # Group native radios by `name`. role=radio without a group name
        # forms its own implicit group.
        groups: dict[str, list[dict]] = {}
        for f in probe["inputs"]:
            ftype = (f.get("type") or "").lower()
            if ftype != "radio":
                continue
            key = f.get("name") or f"__role_{id(f)}"
            groups.setdefault(key, []).append(f)

        # Also treat the page's role=button cards as a single implicit
        # group when there are several siblings AND no native radios are
        # present (so we don't accidentally click random toolbar buttons).
        if not groups:
            card_like = [
                b for b in probe.get("buttons", [])
                if b.get("text") and not _NEXT_BUTTON_RE.match(b["text"])
                and not _SKIP_BUTTON_RE.match(b["text"])
                and not _CONSENT_BUTTON_RE.match(b["text"])
            ]
            # Only treat as choices if the page has nothing else to fill
            # and there are multiple text-ish options.
            if len(card_like) >= 2 and not probe["inputs"]:
                # Heuristic: cards with a CREATE_OWN match qualify as a
                # picker; otherwise leave it to the advancer.
                if any(_CREATE_OWN_RE.search(c["text"].lower()) for c in card_like):
                    groups["__card_group__"] = [
                        {"type": "card", "tag": "button", "name": "",
                         "id": c.get("id", ""), "label": c["text"]}
                        for c in card_like
                    ]

        if not groups:
            return FillResult()

        filled: list[str] = []
        for group_key, members in groups.items():
            picked = self._pick_member(members, env, taken)
            if picked is None:
                continue
            member, reason, env_key = picked
            sel = _selector_for(member)
            if not sel:
                continue
            try:
                await page.locator(sel).first.click(timeout=3000)
            except Exception:
                continue
            filled.append(f"(pick:{group_key}:{reason})")
            if env_key:
                taken.add(env_key)
                filled.append(env_key)
            # If we picked a 'create own', wait for the freshly enabled
            # input to appear, then fill via TextInputsHandler so we don't
            # waste a tick.
            if reason == "create_own":
                await asyncio.sleep(0.4)
                try:
                    probe2 = await page.evaluate(_PROBE_FORM_JS)
                except Exception:
                    continue
                text_res = await TextInputsHandler().fill(
                    page, probe2, env, taken)
                filled.extend(text_res.filled)
        return FillResult(filled)

    @staticmethod
    def _pick_member(members: list[dict], env: dict,
                      taken: set[str]) -> tuple[dict, str, str | None] | None:
        """Return (member, reason, env_key_used) or None."""
        # 1) env-value match — does any member's visible label contain
        # the value of a still-unused SIGNUP_* env entry?
        for env_key, value in env.items():
            if (not env_key.startswith("SIGNUP_") or not value
                    or env_key in taken):
                continue
            v = str(value).lower().strip()
            if len(v) < 3:
                continue
            for m in members:
                text = _field_text(m)
                if v in text:
                    return m, f"env:{env_key}", env_key
        # 2) create-your-own / custom option
        for m in members:
            if _CREATE_OWN_RE.search(_field_text(m)):
                return m, "create_own", None
        # 3) fallback: first choice
        return (members[0], "first", None) if members else None


# Order matters: more specific handlers first.
HANDLERS: list[Handler] = [
    PickGroupHandler(),
    ComboboxHandler(),
    TextInputsHandler(),
]


# ============================================================
#  Section 4: ADVANCE — decide which button ends this tick
# ============================================================

def _button_selector(btn: dict) -> str:
    """Build a clickable selector for an advance button."""
    bid = btn.get("id") or ""
    if bid:
        if re.match(r"^[A-Za-z_][\w-]*$", bid):
            return f"#{bid}"
        return f'[id="{bid}"]'
    text = btn.get("text") or ""
    if text:
        # has-text uses substring + case-insensitive in Playwright
        return f'button:has-text("{text}")'
    return "button[type=submit]"


async def _advance(page: Any, probe: dict, any_filled: bool) -> str | None:
    """Click Next/Submit/Skip/Consent — whatever's appropriate.
    Returns a label of what was clicked, or None if nothing applicable."""
    buttons = probe["buttons"]
    consent = next(
        (b for b in buttons if _CONSENT_BUTTON_RE.match(b.get("text") or "")),
        None,
    )
    skip = next(
        (b for b in buttons if _SKIP_BUTTON_RE.match(b.get("text") or "")),
        None,
    )
    nxt = next(
        (b for b in buttons
         if b.get("type") == "submit"
         or _NEXT_BUTTON_RE.match(b.get("text") or "")),
        None,
    )

    target: dict | None = None
    label: str | None = None
    if any_filled and nxt is not None:
        target, label = nxt, "(next)"
    elif not any_filled and skip is not None:
        # Nothing matched on this page — skip per user instruction.
        target, label = skip, "(skip)"
    elif nxt is not None:
        target, label = nxt, "(next)"
    elif consent is not None:
        target, label = consent, "(consent)"
    if target is None:
        return None
    sel = _button_selector(target)
    try:
        await page.locator(sel).first.click(timeout=5000)
    except Exception as exc:
        raise RuntimeError(f"advance-click failed ({label}): {exc}") from exc
    return label


# ============================================================
#  Section 5: MAIN LOOP
# ============================================================

# Heuristic: which URL fragments still mean "I'm in the signup flow".
# When the page navigates somewhere that doesn't contain any of these,
# we treat the registration as complete.
_SIGNUP_URL_FRAGMENTS = (
    "/signup", "/sign-up", "/register", "/registration",
    "/createaccount", "/create-account", "/lifecycle/steps",
    "/onboarding",
)

# URL fragments that almost always mean "human-only anti-bot wall ahead".
_HUMAN_WALL_URL_FRAGMENTS = (
    "phoneverification", "phone-verification", "phoneverify",
    "/verification/", "/verify/", "/challenge/", "/captcha",
    "recaptcha", "hcaptcha", "/cf-challenge", "turnstile",
    "/identityverification",
)

# Page-text signals — used as a secondary check so the detector still
# fires on hosts that use non-obvious URLs but show classic wall copy.
_HUMAN_WALL_TEXT_RE = re.compile(
    r"verify\s+some\s+info|scan\s+the\s+qr|we\s+want\s+to\s+make\s+sure|"
    r"i['’`]?m\s+not\s+a\s+robot|prove\s+(?:that\s+)?you'?re\s+human|"
    r"enter\s+the\s+code\s+(?:we\s+)?sent|подтверд(?:ите|ить)\s+номер|"
    r"введите\s+код",
    re.IGNORECASE,
)


async def _detect_human_wall(page: Any, url: str) -> str | None:
    """Return a short reason string if the page is an anti-bot human-only
    wall (QR-verify, phone-verify, captcha, Cloudflare challenge etc.).
    Returns None when the page still looks automatable."""
    u = (url or "").lower()
    for frag in _HUMAN_WALL_URL_FRAGMENTS:
        if frag in u:
            return f"url:{frag}"
    # iframes for reCAPTCHA / hCaptcha / Turnstile are a dead-giveaway
    # even when the host URL itself looks benign.
    try:
        for fr in page.frames:
            f_url = (fr.url or "").lower()
            if any(s in f_url for s in
                   ("recaptcha/api2", "hcaptcha.com", "challenges.cloudflare",
                    "turnstile")):
                return f"iframe:{f_url[:60]}"
    except Exception:
        pass
    try:
        body_text = (await page.evaluate(
            "() => document.body ? document.body.innerText.slice(0, 4000) : ''"
        )) or ""
    except Exception:
        body_text = ""
    if _HUMAN_WALL_TEXT_RE.search(body_text):
        return "text:wall-copy"
    return None


async def _universal_signup_dom(page: Any, env: dict,
                                 max_steps: int = 20) -> dict:
    """Reactive loop: probe → run every Handler → advance → wait → repeat.
    Stops when no handler progresses, the URL leaves the signup flow,
    or max_steps is hit."""
    fields_filled: list[str] = []
    steps_run = 0
    debug = bool(os.environ.get("FACTORY_SIGNUP_DEBUG"))
    shot_dir = Path(os.environ.get("TEMP", "/tmp")) / "mcp_signup_dbg"
    if debug:
        shot_dir.mkdir(parents=True, exist_ok=True)

    for step_no in range(max_steps):
        steps_run = step_no + 1
        if debug:
            try:
                await page.screenshot(
                    path=str(shot_dir / f"step_{step_no:02d}_before.png"))
            except Exception:
                pass
        try:
            probe = await page.evaluate(_PROBE_FORM_JS)
        except Exception as exc:
            return {"status": "failed", "fields_filled": fields_filled,
                    "steps_run": steps_run, "final_url": page.url,
                    "error": f"probe: {exc}"}
        # Anti-bot wall check — bail out before we waste steps clicking a
        # Next button that will never advance (or worse, get the IP flagged).
        wall = await _detect_human_wall(page, page.url)
        if wall:
            return {"status": "needs_verification",
                    "fields_filled": fields_filled,
                    "steps_run": steps_run, "final_url": page.url,
                    "note": f"human wall detected ({wall}) — "
                            "browser kept open for human handoff"}
        if debug:
            print(f"  step {step_no}: url={page.url[:80]}  "
                  f"inputs={len(probe['inputs'])} "
                  f"comboboxes={len(probe.get('comboboxes') or [])} "
                  f"buttons={len(probe['buttons'])}")

        taken: set[str] = set()
        any_filled = False
        for handler in HANDLERS:
            res = await handler.fill(page, probe, env, taken)
            if res.filled:
                fields_filled.extend(f"{handler.name}:{f}" for f in res.filled)
                any_filled = True

        # Re-probe before deciding advance — fills may have enabled Next
        # (aria-disabled drops once required inputs are populated).
        if any_filled:
            try:
                probe = await page.evaluate(_PROBE_FORM_JS)
            except Exception:
                pass

        try:
            advanced = await _advance(page, probe, any_filled)
        except RuntimeError as exc:
            return {"status": "failed", "fields_filled": fields_filled,
                    "steps_run": steps_run, "final_url": page.url,
                    "error": str(exc)}
        if advanced is None:
            return {"status": "ok", "fields_filled": fields_filled,
                    "steps_run": steps_run, "final_url": page.url,
                    "note": "no advance button"}
        fields_filled.append(advanced)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        url = page.url.lower()
        if not any(p in url for p in _SIGNUP_URL_FRAGMENTS):
            return {"status": "ok", "fields_filled": fields_filled,
                    "steps_run": steps_run, "final_url": page.url,
                    "note": "left sign-up path"}

    return {"status": "max_steps", "fields_filled": fields_filled,
            "steps_run": steps_run, "final_url": page.url}


# ============================================================
#  Section 6: PUBLIC REGISTRY GLUE — yaml pattern OR universal
# ============================================================

def _host_for(target: str) -> str:
    netloc = urlparse(target).netloc
    return netloc or target


def _pattern_path(target: str) -> Path:
    host = _host_for(target)
    return _PATTERNS_DIR / "web" / f"{host}.signup.yaml"


def _load_pattern(target: str) -> dict:
    p = _pattern_path(target)
    if not p.exists():
        raise FileNotFoundError(
            f"sign-up pattern not found: {p} (host={_host_for(target)!r})"
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not data.get("steps"):
        raise ValueError(f"sign-up pattern at {p} is missing 'steps'")
    return data


def _fill_target(step: dict[str, Any]) -> str:
    """Best-effort label of what this step filled (for the summary)."""
    return str(step.get("save_as") or step.get("selector") or "")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return False
    return bool(value)


@register("run_signup_pattern")
async def run_signup_pattern(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    target = resolve(step["target"], ctx)
    keep_open = _as_bool(resolve(step.get("keep_open"), ctx))

    if not isinstance(target, str) or not target:
        raise ValueError("run_signup_pattern: 'target' must be a non-empty string")

    visible = _as_bool(resolve(step.get("visible"), ctx))
    forced = (ctx.get("vars") or {}).get("__forced_signup_pattern__")
    pattern: dict | None = forced if isinstance(forced, dict) else None
    if pattern is None:
        try:
            pattern = _load_pattern(target)
        except FileNotFoundError:
            # Universal DOM-driven fallback — no host YAML needed.
            return await _run_universal_signup(
                target, keep_open, ctx, visible=visible)
    steps = pattern.get("steps") or []

    # Collect the names of fields we're about to fill (before the run, so the
    # summary is meaningful even if a step later in the run raises).
    fields_filled: list[str] = []
    for sub in steps:
        if sub.get("action") == "fill":
            name = _fill_target(sub)
            if name:
                fields_filled.append(name)

    # Reuse pre-attached backend if present (test fixture / use_session);
    # otherwise open a fresh browser.
    opened_here = False
    if ctx.get("backend") is None:
        from ...backends import web as _web
        ctx["backend"] = await _web.open_session(headless=True)
        opened_here = True

    from ..actions.control import PauseSignal
    from ..executor import run_steps

    status = "ok"
    try:
        await run_steps(steps, ctx, 0)
    except PauseSignal:
        # Email verification or similar — surface as needs_verification but
        # don't kill the browser so a follow-up tool can resume.
        status = "needs_verification"
        keep_open = True
    except Exception:
        status = "failed"
        if opened_here:
            from ...backends import web as _web
            try:
                await _web.close_session(ctx["backend"])
            except Exception:
                pass
            ctx["backend"] = None
        ctx["output"]["status"] = status
        ctx["output"]["target"] = target
        ctx["output"]["fields_filled"] = fields_filled
        return

    if status == "ok" and keep_open and ctx.get("backend") is not None:
        live_sessions.put(target, ctx["backend"])
    elif status == "ok" and not keep_open and opened_here:
        from ...backends import web as _web
        try:
            await _web.close_session(ctx["backend"])
        except Exception:
            pass
        ctx["backend"] = None
    elif status == "needs_verification" and ctx.get("backend") is not None:
        live_sessions.put(target, ctx["backend"])

    ctx["output"]["status"] = status
    ctx["output"]["target"] = target
    ctx["output"]["fields_filled"] = fields_filled


async def _run_universal_signup(target: str, keep_open: bool,
                                 ctx: dict[str, Any],
                                 visible: bool = False) -> None:
    """Universal DOM-driven sign-up: opens a browser, navigates to target,
    walks the form pages auto-filling from env. No host yaml required.
    When `visible` is true the browser opens in headed mode so a human can
    watch (or step in for captcha / 2FA)."""
    opened_here = False
    if ctx.get("backend") is None:
        from ...backends import web as _web
        ctx["backend"] = await _web.open_session(headless=not visible)
        opened_here = True
    page = ctx["backend"].page
    try:
        await page.goto(target)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        env_snapshot = dict(os.environ)
        result = await _universal_signup_dom(page, env_snapshot)
    except Exception as exc:
        result = {"status": "failed", "fields_filled": [],
                  "final_url": page.url, "error": str(exc)}

    status = result.get("status") or "failed"
    fields_filled = result.get("fields_filled") or []
    backend = ctx.get("backend")
    if status == "ok" and keep_open and backend is not None:
        live_sessions.put(target, backend)
    elif (status != "ok") or (not keep_open and opened_here):
        if opened_here and backend is not None:
            from ...backends import web as _web
            try:
                await _web.close_session(backend)
            except Exception:
                pass
            ctx["backend"] = None

    ctx["output"]["status"] = status
    ctx["output"]["target"] = target
    ctx["output"]["fields_filled"] = fields_filled
    ctx["output"]["final_url"] = result.get("final_url")
    ctx["output"]["steps_run"] = result.get("steps_run", 0)
    if result.get("error"):
        ctx["output"]["error"] = result["error"]
    if result.get("note"):
        ctx["output"]["note"] = result["note"]
