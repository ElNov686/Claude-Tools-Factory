import time
import uuid
from pathlib import Path

from .schemas import SessionState


class SessionStore:
    def __init__(self, root: Path, ttl_seconds: int = 600):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds

    def _path(self, sid: str) -> Path:
        return self.root / f"{sid}.json"

    def save(self, state: SessionState) -> str:
        sid = uuid.uuid4().hex[:12]
        self._path(sid).write_text(state.model_dump_json(), encoding="utf-8")
        return sid

    def load(self, sid: str) -> SessionState | None:
        p = self._path(sid)
        if not p.exists():
            return None
        state = SessionState.model_validate_json(p.read_text(encoding="utf-8"))
        if time.time() - state.created_at > self.ttl:
            self.clear(sid)
            return None
        return state

    def clear(self, sid: str) -> None:
        self._path(sid).unlink(missing_ok=True)
