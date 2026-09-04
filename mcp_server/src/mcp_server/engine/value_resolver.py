from typing import Any


class ResolveError(Exception):
    pass


_NAMESPACES = ("input", "creds", "env")


def resolve(value: Any, ctx: dict[str, Any]) -> Any:
    """Resolve a step value against context.

    - dict {"literal": X} → X verbatim (escape hatch)
    - "$name" → ctx["vars"]["name"]
    - "ns.key" where ns in (input, creds, env) → ctx[ns]["key"]
    - anything else (incl. non-str) → returned as-is
    """
    if isinstance(value, dict) and set(value.keys()) == {"literal"}:
        return value["literal"]

    if not isinstance(value, str):
        return value

    if value.startswith("$"):
        name = value[1:]
        try:
            return ctx["vars"][name]
        except KeyError as e:
            raise ResolveError(f"unknown variable ${name}") from e

    if "." in value:
        ns, _, key = value.partition(".")
        if ns in _NAMESPACES:
            try:
                return ctx[ns][key]
            except KeyError as e:
                raise ResolveError(f"unknown reference {value}") from e

    return value
