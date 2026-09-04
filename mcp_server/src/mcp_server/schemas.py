from typing import Any, Literal

from pydantic import BaseModel


class LoginResult(BaseModel):
    status: Literal["ok", "needs_2fa", "pattern_missing", "failed"]
    target: str
    session_id: str | None = None
    prompt: str | None = None
    suggest: str | None = None
    error: str | None = None
    retriable: bool = False


class ScreenshotResult(BaseModel):
    path: str
    size: tuple[int, int]
    target: str


class UITreeResult(BaseModel):
    dump_path: str
    summary: list[str]
    target: str


class NotifyResult(BaseModel):
    sent: bool
    channel: str
    error: str | None = None


class CredsResult(BaseModel):
    found: bool
    fields: list[str] = []
    error: str | None = None


class SessionState(BaseModel):
    target: str
    pattern_path: str
    step_index: int
    vars: dict[str, Any] = {}
    backend_state: dict[str, Any] = {}
    created_at: float
