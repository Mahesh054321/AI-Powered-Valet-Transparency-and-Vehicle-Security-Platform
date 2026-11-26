"""Ticket QR code rendering.

The QR image is rendered **server-side** and written to storage when the ticket
is issued, for three reasons:

* the same bytes are served every time the ticket is opened, so a gate scanner
  and the customer's phone cannot disagree about what was issued;
* ``valet_tickets.qr_code_path`` exists precisely to hold that reference, and
  the SHA-256 recorded alongside it lets an auditor prove the code shown at
  pickup is the one issued at drop-off;
* no new browser dependency is needed to display it.

A QR carrying a link opens the authenticated ticket detail screen; it never
contains personal vehicle or customer data. Ticket possession is not proof of
ownership; the API still checks JWT ownership/role. Legacy human-readable QR
codes remain valid as identification but cannot open a browser automatically.
"""

from __future__ import annotations

import io
from typing import Any

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from services.media import media_url, store_bytes

QR_SUBDIR = "qr"
QR_PREFIX = "VALET"

#: Version 2 with medium error correction keeps the symbol readable after the
#: wear and phone-screen glare a printed valet ticket actually sees, while
#: staying small enough to fit on the ticket card.
BOX_SIZE = 10
BORDER_MODULES = 4


def build_qr_payload(ticket_code: str, *, ticket_id: str | None = None,
                     base_url: str | None = None) -> str:
    """Use a private-by-auth ticket URL for modern QR codes.

    Never embed a plate number, phone, damage report or bearer token. The QR
    identifies a ticket and the *authenticated* API authorises the viewer.
    """
    if ticket_id and base_url:
        from urllib.parse import urlsplit
        url = base_url.strip().rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username:
            raise ValueError("TICKET_QR_BASE_URL must be an http(s) origin")
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("Public ticket QR links must use HTTPS")
        import uuid
        ticket_uuid = uuid.UUID(str(ticket_id))
        return f"{url}/tickets/{ticket_uuid}"
    return f"{QR_PREFIX}:{ticket_code}"


def render_png(payload: str) -> bytes:
    """Render ``payload`` as PNG bytes. No filesystem access."""
    factory = qrcode.QRCode(
        error_correction=ERROR_CORRECT_M,
        box_size=BOX_SIZE,
        border=BORDER_MODULES,
    )
    factory.add_data(payload)
    factory.make(fit=True)

    buffer = io.BytesIO()
    factory.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def issue_ticket_qr(ticket_code: str, *, ticket_id: str | None = None, base_url: str | None = None) -> dict[str, Any]:
    """Render, store and describe the QR image for a newly issued ticket.

    Returns the fields the ticket row needs plus the URL the SPA displays.
    """
    payload = build_qr_payload(ticket_code, ticket_id=ticket_id, base_url=base_url)
    blob = render_png(payload)

    reference = store_bytes(
        blob,
        subdir=QR_SUBDIR,
        extension="png",
        label="ticket_qr",
    )
    return {
        "qr_payload": payload,
        "qr_code_path": reference["path"],
        "qr_image": reference,
        "qr_url": media_url(reference["path"]),
    }


__all__ = [
    "QR_PREFIX",
    "build_qr_payload",
    "issue_ticket_qr",
    "render_png",
]
