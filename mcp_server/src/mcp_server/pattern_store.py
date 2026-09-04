from pathlib import Path
from urllib.parse import urlparse

import yaml


class PatternStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def _normalize(target: str) -> tuple[str, str]:
        """Return (target_type, key)."""
        if target.startswith("http://") or target.startswith("https://"):
            host = urlparse(target).netloc
            return "web", host
        return "desktop", target

    def _path_for(self, target_type: str, key: str) -> Path:
        return self.root / target_type / f"{key}.yaml"

    def lookup(self, target: str) -> dict | None:
        target_type, key = self._normalize(target)
        p = self._path_for(target_type, key)
        if not p.exists():
            return None
        return yaml.safe_load(p.read_text(encoding="utf-8"))

    def save(self, target: str, pattern: dict) -> Path:
        target_type, key = self._normalize(target)
        p = self._path_for(target_type, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(pattern, sort_keys=False), encoding="utf-8")
        return p

    def list_all(self) -> list[dict]:
        out = []
        for sub in ("web", "desktop"):
            d = self.root / sub
            if not d.exists():
                continue
            for f in sorted(d.glob("*.yaml")):
                if f.name.startswith("_"):
                    continue
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                out.append({
                    "key": f.stem,
                    "target_type": sub,
                    "confidence": data.get("confidence", "unknown"),
                })
        return out
