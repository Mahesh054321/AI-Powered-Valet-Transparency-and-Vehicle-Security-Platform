"""Pure QR tests: no PostgreSQL needed."""
import uuid
import pytest
from services.qr_codes import build_qr_payload


def test_new_ticket_qr_opens_authenticated_detail_url():
    ticket_id = uuid.uuid4()
    url = build_qr_payload("VT-123", ticket_id=str(ticket_id), base_url="https://example.org")
    assert url == f"https://example.org/tickets/{ticket_id}"
    assert "VT-123" not in url


def test_legacy_qr_is_still_human_readable():
    assert build_qr_payload("VT-123") == "VALET:VT-123"


def test_public_qr_must_be_https():
    with pytest.raises(ValueError, match="HTTPS"):
        build_qr_payload("VT-123", ticket_id=str(uuid.uuid4()), base_url="http://example.org")
