from pathlib import Path

import yaml
from jsonschema import Draft7Validator

_TOOLS_SCHEMA = Path(__file__).resolve().parents[2].parent / "tools" / "_schema.yaml"
_PATTERNS_SCHEMA = Path(__file__).resolve().parents[2].parent / "patterns" / "_schema.yaml"


class ValidationError(Exception):
    pass


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _validate(instance: dict, schema_path: Path) -> None:
    validator = Draft7Validator(_load(schema_path))
    errors = sorted(validator.iter_errors(instance), key=lambda e: e.path)
    if errors:
        msgs = "; ".join(e.message for e in errors[:5])
        raise ValidationError(msgs)


def validate_tool(spec: dict) -> None:
    _validate(spec, _TOOLS_SCHEMA)


def validate_pattern(pat: dict) -> None:
    _validate(pat, _PATTERNS_SCHEMA)
