from typing import Any

from ..registry import register
from ..value_resolver import resolve


class PauseSignal(Exception):
    """Raised by pause_for_user; executor catches it to suspend execution."""
    def __init__(self, prompt: str, save_as: str):
        super().__init__(prompt)
        self.prompt = prompt
        self.save_as = save_as


@register("set_var")
def set_var(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    ctx["vars"][step["name"]] = resolve(step["value"], ctx)


@register("branch")
def branch(step: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Returns the sub-steps to execute, or None. The executor inlines them.

    Evaluation is delegated: branch sets ctx['__branch_predicate__'] so the
    executor (which owns backends) can evaluate `if_visible`. For predicates
    that are pure-context (`if_var`), branch evaluates directly.
    """
    if "if_var" in step:
        name = step["if_var"]
        if ctx["vars"].get(name):
            return step.get("then", [])
        return step.get("else_", [])
    # UI predicate: defer to executor via marker
    ctx["__pending_branch__"] = step
    return None


@register("pause_for_user")
def pause_for_user(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    raise PauseSignal(prompt=step["prompt"], save_as=step["save_as"])


@register("set_output")
def set_output(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    ctx["output"][step["key"]] = resolve(step["value"], ctx)


@register("loop")
async def loop(step: dict[str, Any], ctx: dict[str, Any]) -> None:
    from ..executor import run_steps  # local import avoids circular import at module load
    items = resolve(step["over"], ctx)
    var = step["as"]
    for element in items:
        ctx["vars"][var] = element
        await run_steps(step["do"], ctx, 0)
