"""Typed access to environment variables.

This is the *only* module in the backend that reads ``os.environ`` directly.
Every other module receives configuration through the ``Settings`` object, so
there is exactly one place where the process environment is interpreted.

Two rules enforced here:

* Real environment variables win over ``.env`` (``override=False``), so a
  container's environment is never shadowed by a stale file.
* Malformed values fail loudly at start-up rather than silently defaulting.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def load_environment() -> None:
    """Load ``backend/.env`` if present, without overriding real env vars."""
    env_file = BACKEND_ROOT / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)


def env_str(name: str, default: str | None = None) -> str | None:
    """Read a string, treating an empty value as absent."""
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = env_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def env_float(name: str, default: float) -> float:
    raw = env_str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be a number, got {raw!r}") from exc


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    """Read a comma-separated list into a list of trimmed, non-empty strings."""
    raw = env_str(name)
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_set(name: str, default: list[str] | None = None) -> set[str]:
    return {item.lower().lstrip(".") for item in env_list(name, default)}
