"""Ticket QR code rendering.

The QR image is rendered **server-side** and written to storage when the ticket
is issued, for three reasons:

* the same bytes are served every time the ticket is opened, so a gate scanner
  and the customer's phone cannot disagree about what was issued;
* ``valet_tickets.qr_code_path`` exists precisely to hold that reference, and
  the SHA-256 recorded alongside it lets an auditor prove the code shown at
  pickup is the one issued at drop-off;
* no new browser dependency is needed to display it.

The QR payload is deliberately a short, human-inspectable string rather than a
URL: it must stay readable by a scanner that is not connected to this platform,
and it must not embed a secret -- possession of the ticket code is not
authentication. Authorisation still happens through the API's own bearer token.
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


def build_qr_payload(ticket_code: str) -> str:
    """The string encoded into the ticket's QR code."""
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


def issue_ticket_qr(ticket_code: str) -> dict[str, Any]:
    """Render, store and describe the QR image for a newly issued ticket.

    Returns the fields the ticket row needs plus the URL the SPA displays.
    """
    payload = build_qr_payload(ticket_code)
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
