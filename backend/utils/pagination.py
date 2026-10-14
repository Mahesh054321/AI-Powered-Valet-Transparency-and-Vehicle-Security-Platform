"""Pagination parsing shared by list endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from flask import request

from utils.exceptions import ValidationError

MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class PaginationParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def _positive_int(raw: str | None, default: int, *, field: str, maximum: int | None = None) -> int:
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValidationError(
            f"'{field}' must be a whole number.", details={"field": field}
        ) from exc
    if value < 1:
        raise ValidationError(f"'{field}' must be 1 or greater.", details={"field": field})
    if maximum is not None and value > maximum:
        raise ValidationError(
            f"'{field}' must not exceed {maximum}.", details={"field": field, "maximum": maximum}
        )
    return value


def parse_pagination(default_page_size: int = 20) -> PaginationParams:
    """Read ``?page=`` and ``?page_size=`` from the query string, with sane bounds."""
    page = _positive_int(request.args.get("page"), 1, field="page")
    page_size = _positive_int(
        request.args.get("page_size"),
        default_page_size,
        field="page_size",
        maximum=MAX_PAGE_SIZE,
    )
    return PaginationParams(page=page, page_size=page_size)
