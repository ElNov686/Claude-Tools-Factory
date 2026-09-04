import keyring as _keyring

_backend = _keyring  # monkeypatch point for tests


class CredsMissing(Exception):
    pass


def _service(name: str) -> str:
    return f"factory:{name}"


def get_creds(name: str, fields: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    missing: list[str] = []
    for field in fields:
        val = _backend.get_password(_service(name), field)
        if val is None:
            missing.append(field)
        else:
            out[field] = val
    if missing:
        raise CredsMissing(
            f"missing keyring fields for {name!r}: {missing}. "
            f"Add with: keyring set factory:{name} <field>"
        )
    return out


def present_fields(name: str, fields: list[str]) -> list[str]:
    return [f for f in fields if _backend.get_password(_service(name), f) is not None]
