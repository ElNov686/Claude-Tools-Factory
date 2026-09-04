import inspect
from typing import Any

from .registry import get_handler


async def _call(handler, step, ctx):
    result = handler(step, ctx)
    if inspect.isawaitable(result):
        return await result
    return result


async def run_steps(steps: list[dict[str, Any]], ctx: dict[str, Any],
                    start_index: int = 0) -> None:
    """Execute steps[start_index:]. Raises PauseSignal upward unchanged."""
    i = start_index
    while i < len(steps):
        step = steps[i]
        handler = get_handler(step["action"])
        result = await _call(handler, step, ctx)

        # branch returned inline sub-steps (if_var case)
        if step["action"] == "branch":
            sub = await _resolve_branch(step, result, ctx)
            if sub:
                await run_steps(sub, ctx, 0)
        i += 1


async def _resolve_branch(step, result, ctx):
    # if_var branch already returned its sub-steps
    if result is not None:
        return result
    # UI predicate branch: executor evaluates via is_visible action
    pending = ctx.pop("__pending_branch__", None)
    if pending is None:
        return []
    visible = await _call(get_handler("is_visible"),
                          {"action": "is_visible", "selector": pending["if_visible"]}, ctx)
    return pending.get("then", []) if visible else pending.get("else_", [])


import time
from dataclasses import dataclass, field

from ..schemas import SessionState
from ..session_store import SessionStore
from .actions.control import PauseSignal


@dataclass
class RunOutcome:
    status: str            # "ok" | "paused" | "failed"
    ctx: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    prompt: str | None = None
    error: str | None = None
    retriable: bool = False


async def run_pattern(steps, ctx, store: SessionStore, *, target: str,
                      pattern_path: str, start_index: int = 0) -> RunOutcome:
    i = start_index
    while i < len(steps):
        step = steps[i]
        try:
            handler = get_handler(step["action"])
            result = await _call(handler, step, ctx)
            if step["action"] == "branch":
                sub = await _resolve_branch(step, result, ctx)
                if sub:
                    await run_steps(sub, ctx, 0)
        except PauseSignal as p:
            state = SessionState(
                target=target, pattern_path=pattern_path, step_index=i + 1,
                vars=dict(ctx["vars"]),
                backend_state={"save_as": p.save_as, "steps": steps},
                created_at=time.time(),
            )
            sid = store.save(state)
            return RunOutcome(status="paused", session_id=sid, prompt=p.prompt, ctx=ctx)
        except Exception as e:  # structured failure, not traceback
            return RunOutcome(status="failed", error=str(e), retriable=False, ctx=ctx)
        i += 1
    return RunOutcome(status="ok", ctx=ctx)


async def resume_pattern(session_id: str, user_input: str, store: SessionStore) -> RunOutcome:
    state = store.load(session_id)
    if state is None:
        return RunOutcome(status="failed", error="session expired or missing", retriable=True)
    steps = state.backend_state.get("steps")
    if steps is None:
        import yaml
        pattern = yaml.safe_load(open(state.pattern_path, encoding="utf-8").read())
        steps = pattern["steps"]
    save_as = state.backend_state.get("save_as", "user_input")
    ctx = {"input": {}, "creds": {}, "env": {}, "vars": dict(state.vars),
           "output": {}, "__target__": state.target}
    ctx["vars"][save_as] = user_input
    store.clear(session_id)
    return await run_pattern(steps, ctx, store, target=state.target,
                             pattern_path=state.pattern_path, start_index=state.step_index)
