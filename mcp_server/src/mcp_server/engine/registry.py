from collections.abc import Callable
from typing import Any

Handler = Callable[[dict[str, Any], dict[str, Any]], Any]

_REGISTRY: dict[str, Handler] = {}


class UnknownAction(Exception):
    pass


def register(name: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        if name in _REGISTRY:
            raise ValueError(f"action {name!r} already registered")
        _REGISTRY[name] = fn
        return fn
    return deco


def get_handler(name: str) -> Handler:
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise UnknownAction(f"no handler for action {name!r}") from e


def all_actions() -> list[str]:
    return sorted(_REGISTRY)
