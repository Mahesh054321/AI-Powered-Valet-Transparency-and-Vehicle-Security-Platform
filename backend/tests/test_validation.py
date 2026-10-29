"""Phase 1 tests: input validation and upload safety.

These run without a database, so `pytest` is meaningful on a fresh clone.
"""

from __future__ import annotations

import io

import pytest

from utils.exceptions import PayloadTooLargeError, UnsupportedMediaError, ValidationError
from utils.validation import (
    require_fields,
    validate_choice,
    validate_email,
    validate_hex_color,
    validate_image_upload,
    validate_latitude,
    validate_longitude,
    validate_password,
    validate_phone,
    validate_plate_number,
    validate_text,
    validate_uuid,
)


class _FakeUpload:
    """Minimal stand-in for werkzeug's FileStorage used by upload tests."""

    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.stream = io.BytesIO(content)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP_BYTES = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"\x00" * 32


class TestScalars:
    @pytest.mark.parametrize(
        "value",
        ["customer@valet.local", "  Admin.User+tag@sub.domain.co.in  ", "a_b-c%d@e.io"],
    )
    def test_valid_emails(self, value):
        assert validate_email(value) == value.strip().lower()

    @pytest.mark.parametrize(
        "value", ["", "plainaddress", "a@b", "@no-user.com", "a b@c.com", "x" * 400]
    )
    def test_invalid_emails(self, value):
        with pytest.raises(ValidationError):
            validate_email(value)

    def test_phone_optional(self):
        assert validate_phone("", required=False) is None
        assert validate_phone("+91 98765 43210") == "+91 98765 43210"
        with pytest.raises(ValidationError):
            validate_phone("12345")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("UP85CA1234", "UP85CA1234"),
            ("up 85 ca 1234", "UP85CA1234"),
            ("DL-01-AB-0001", "DL01AB0001"),
            ("MH12DE1234", "MH12DE1234"),
            ("KA01MJ1234", "KA01MJ1234"),
            ("KA01MJK1234", "KA01MJK1234"),
            ("25BH0987AB", "25BH0987AB"),
            ("25 BH 0987 AB", "25BH0987AB"),
        ],
    )
    def test_valid_plates(self, raw, expected):
        assert validate_plate_number(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "ABC123",
            "UP85C12345",  # 5 trailing digits
            "12345",
            "UP85CA123",  # only 3 trailing digits
            "12XY1234AB",  # non-BH numeric prefix
            "UP85CA12345",  # too long
            "UP8-5CA-1234-9",
        ],
    )
    def test_invalid_plates(self, raw):
        with pytest.raises(ValidationError):
            validate_plate_number(raw)

    def test_password_policy(self):
        assert validate_password("Correct-Horse-1") == "Correct-Horse-1"
        with pytest.raises(ValidationError, match="too weak") as excinfo:
            validate_password("short1")
        assert "at least 8 characters" in excinfo.value.details["requirements"]
        with pytest.raises(ValidationError):
            validate_password("alllettershere")  # no digit

    def test_uuid(self):
        parsed = validate_uuid("11111111-1111-1111-1111-111111111111")
        assert str(parsed) == "11111111-1111-1111-1111-111111111111"
        with pytest.raises(ValidationError):
            validate_uuid("not-a-uuid")

    @pytest.mark.parametrize(
        ("value", "ok"), [(27.1767, True), (-90, True), (91, False), ("abc", False)]
    )
    def test_latitude(self, value, ok):
        if ok:
            assert validate_latitude(value) == float(value)
        else:
            with pytest.raises(ValidationError):
                validate_latitude(value)

    @pytest.mark.parametrize(("value", "ok"), [(78.0081, True), (180, True), (181, False)])
    def test_longitude(self, value, ok):
        if ok:
            assert validate_longitude(value) == float(value)
        else:
            with pytest.raises(ValidationError):
                validate_longitude(value)

    def test_choice(self):
        assert validate_choice("sedan", ["SEDAN", "SUV"]) == "SEDAN"
        with pytest.raises(ValidationError):
            validate_choice("truck", ["SEDAN", "SUV"])

    def test_text_bounds(self):
        assert validate_text("  Mathura  ", field="city") == "Mathura"
        assert validate_text("", field="city", required=False) is None
        with pytest.raises(ValidationError):
            validate_text("x" * 500, field="city", max_length=200)

    def test_hex_color(self):
        assert validate_hex_color("#1f8a4c") == "#1f8a4c"
        with pytest.raises(ValidationError):
            validate_hex_color("green")

    def test_require_fields(self):
        payload = require_fields({"email": "a@b.com", "password": "x"}, "email", "password")
        assert payload["email"] == "a@b.com"
        with pytest.raises(ValidationError) as excinfo:
            require_fields({"email": "a@b.com"}, "email", "password")
        assert excinfo.value.details["missing_fields"] == ["password"]
        with pytest.raises(ValidationError, match="JSON object"):
            require_fields(None, "email")


class TestUploadValidation:
    def test_accepts_png(self, app):
        with app.test_request_context():
            upload, extension = validate_image_upload(_FakeUpload("car.png", PNG_BYTES))
        assert extension == "png"
        assert upload.stream.tell() == 0  # rewound for the caller

    def test_accepts_jpeg_and_normalises_extension(self, app):
        with app.test_request_context():
            _, extension = validate_image_upload(_FakeUpload("car.jpeg", JPEG_BYTES))
        assert extension == "jpg"

    def test_accepts_webp(self, app):
        with app.test_request_context():
            _, extension = validate_image_upload(_FakeUpload("car.webp", WEBP_BYTES))
        assert extension == "webp"

    def test_rejects_disallowed_extension(self, app):
        with app.test_request_context(), pytest.raises(UnsupportedMediaError, match="not allowed"):
            validate_image_upload(_FakeUpload("payload.exe", b"MZ" + b"\x00" * 64))

    def test_rejects_content_that_lies_about_its_type(self, app):
        """A PNG file renamed to .jpg must be rejected by signature sniffing."""
        with app.test_request_context(), pytest.raises(
            UnsupportedMediaError, match="does not match"
        ):
            validate_image_upload(_FakeUpload("fake.jpg", PNG_BYTES))

    def test_rejects_riff_file_that_is_not_webp(self, app):
        avi = b"RIFF" + b"\x24\x00\x00\x00" + b"AVI " + b"\x00" * 32
        with app.test_request_context(), pytest.raises(UnsupportedMediaError):
            validate_image_upload(_FakeUpload("video.webp", avi))

    def test_rejects_oversized_image(self, app):
        app.config["MAX_CONTENT_LENGTH"] = 32
        with app.test_request_context(), pytest.raises(PayloadTooLargeError):
            validate_image_upload(_FakeUpload("big.png", PNG_BYTES))

    def test_rejects_empty_file(self, app):
        with app.test_request_context(), pytest.raises(ValidationError, match="empty"):
            validate_image_upload(_FakeUpload("empty.png", b""))

    def test_rejects_missing_file(self, app):
        with app.test_request_context(), pytest.raises(ValidationError, match="No file"):
            validate_image_upload(None)
