"""Manager dashboard and report endpoints.

    GET /manager/dashboard          composed analytics for the selected filters
    GET /manager/report?format=csv  the same figures, flattened for export

**The report is the same code path as the dashboard**, not a parallel
implementation. A report that disagreed with the screen it summarised would be
worse than no report, because the report is the artefact someone files and the
screen is not.

Exports are audited with ``EXPORT``: a manager downloading operational figures
is doing something worth recording, and the audit row names who and when.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from flask import Blueprint, Response, request

from models.audit_log import AuditAction
from services.manager_dashboard import build_dashboard, build_report
from utils.responses import ok
from utils.security import current_user, require_authentication


def _args() -> dict[str, str]:
    """The filter query string, as a plain dict."""
    return {key: value for key, value in request.args.items() if value not in (None, "")}


def _audit_export(actor, fmt: str, args: dict[str, str]) -> None:
    """Record that a report was downloaded.

    Best-effort: a failure to write the audit row must not turn a successful
    export into a 500, but it is logged so the gap is visible.
    """
    from app.extensions import db
    from services.tickets import _audit  # noqa: PLC2701 - shared audit helper

    try:
        # `actor_context`, not `actor`: current_user() returns an AuthContext,
        # and the helper takes the ORM user under a different keyword.
        _audit(
            action=str(AuditAction.EXPORT),
            actor_context=actor,
            entity_type="custody_sessions",
            entity_id=None,
            description=(
                f"Exported the manager operations report as {fmt.upper()} "
                f"with filters {json.dumps(args, sort_keys=True)}."
            ),
            outcome="SUCCESS",
        )
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        import logging

        logging.getLogger(__name__).exception("Failed to audit report export")


def _flatten(report: dict) -> list[dict]:
    """Flatten the report into tabular rows for CSV.

    Metrics, series and table rows each become labelled rows rather than being
    dumped side by side, because they have different shapes and a CSV with
    ragged columns is not something anyone can open.
    """
    rows: list[dict] = []

    rows.append({"section": "report", "key": "generated_at", "value": report["generated_at"]})
    rows.append(
        {
            "section": "report",
            "key": "generated_by",
            "value": report["generated_by"]["role"],
        }
    )
    for name, value in report["filters"].items():
        rows.append({"section": "filter", "key": name, "value": value or ""})
    rows.append(
        {
            "section": "provenance",
            "key": "contains_generated_history",
            "value": report["provenance"]["contains_generated_history"],
        }
    )
    rows.append(
        {
            "section": "provenance",
            "key": "generated_tickets",
            "value": report["provenance"]["generated_tickets"],
        }
    )

    for name, metric in report["metrics"].items():
        rows.append(
            {
                "section": "metric",
                "key": name,
                "value": metric["value"],
                "unit": metric["unit"],
                "windowed": metric["windowed"],
                "n": metric.get("n", ""),
                "note": metric.get("note", ""),
            }
        )

    for name, series in report["series"].items():
        for index, point in enumerate(series["points"]):
            label = point.get("date") or point.get("week") or point.get("bucket") or index
            rows.append(
                {
                    "section": f"series:{name}",
                    "key": str(label),
                    "value": point.get("value", point.get("total", "")),
                    "unit": series["unit"],
                    "n": point.get("n", ""),
                    "sufficient": series["sufficient"],
                }
            )

    for name, table in report["tables"].items():
        for record in table:
            flat = {
                key: (
                    json.dumps(value, sort_keys=True)
                    if isinstance(value, dict | list)
                    else value
                )
                for key, value in record.items()
            }
            rows.append({"section": f"table:{name}", **flat})

    return rows


def _csv_response(report: dict) -> Response:
    rows = _flatten(report)
    # Union of keys, in first-seen order, so columns are stable and readable
    # rather than alphabetised into nonsense.
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="valet-operations-{stamp}.csv"',
            # The report is synthetic-data-aware and the file should say so too,
            # because a CSV loses the banner the screen shows.
            "X-Report-Contains-Generated-History": str(
                report["provenance"]["contains_generated_history"]
            ).lower(),
        },
    )


def create_manager_blueprint() -> Blueprint:
    """Build a fresh manager dashboard blueprint."""
    blueprint = Blueprint("manager", __name__)

    @blueprint.get("/dashboard")
    @blueprint.get("/dashboard/")
    @require_authentication
    def dashboard():
        """Every metric, chart and table the manager screen renders.

        Filters arrive as query parameters: ``date_from``, ``date_to``,
        ``status``, ``severity``, ``vehicle_id``. All four are validated and an
        invalid value is a 422 naming the allowed set, so a client cannot
        silently receive an unfiltered aggregate it believes was filtered.
        """
        return ok(build_dashboard(current_user(), _args()))

    @blueprint.get("/report")
    @blueprint.get("/report/")
    @require_authentication
    def report():
        """The same figures as a downloadable report.

        ``format=csv`` returns an attachment; anything else returns the JSON
        body. Both are produced from ``build_report``, which is the dashboard's
        own query path.
        """
        args = _args()
        fmt = str(args.pop("format", "json")).lower()
        actor = current_user()
        payload = build_report(actor, args)

        if fmt not in {"csv", "json"}:
            from utils.exceptions import ValidationError

            raise ValidationError(
                "'format' must be 'csv' or 'json'.",
                details={"field": "format", "allowed": ["csv", "json"]},
            )

        _audit_export(actor, fmt, args)

        if fmt == "csv":
            return _csv_response(payload)
        return ok(payload)

    return blueprint


__all__ = ["create_manager_blueprint"]
