"""Shared envelope contracts.

These are plain dataclasses rather than a validation library: they document the
wire format and are used by the API documentation step. Field-level input
validation lives in ``utils/validation.py``, which raises ``ValidationError``
with a machine-readable ``details`` payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MetaSchema:
    """Present on every response."""

    request_id: str
    server_time: float


@dataclass(frozen=True)
class ErrorSchema:
    """Stable, machine-readable error shape."""

    code: str
    message: str
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class SuccessEnvelope:
    """Top-level success response."""

    success: bool = True
    data: Any = None
    meta: MetaSchema | None = None


@dataclass(frozen=True)
class PaginationMeta:
    """Returned inside ``data.pagination`` for list endpoints."""

    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_prev: bool


@dataclass(frozen=True)
class ListEnvelope:
    success: bool = True
    items: list[Any] = field(default_factory=list)
    pagination: PaginationMeta | None = None
