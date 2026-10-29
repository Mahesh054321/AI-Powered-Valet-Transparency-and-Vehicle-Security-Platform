"""Image upload.

    POST /uploads/images   auth - store a drop-off or pickup photograph

Separating upload from check-in matters: a valet takes four to eight photos
over a couple of minutes, and each one should be acknowledged as it lands rather
than held in the browser until the whole form is submitted. The response is the
``ImageRef`` that ``POST /tickets/<id>/check-in`` then persists, so the check-in
request stays small JSON instead of a large multipart body.

The file's own signature bytes are checked against its declared extension
before anything is written, so a renamed executable cannot be stored and later
served back over ``/media``.
"""

from __future__ import annotations

from flask import Blueprint, request

from services import media
from utils.exceptions import ValidationError
from utils.responses import created
from utils.security import current_user, require_authentication

#: Photos are optional metadata on an inspection, so a generous cap keeps a
#: slow phone camera usable without inviting abuse.
MAX_PHOTOS_PER_REQUEST = 8


def create_uploads_blueprint() -> Blueprint:
    """Build a fresh uploads blueprint (a new instance per app)."""
    blueprint = Blueprint("uploads", __name__)

    @blueprint.post("/images")
    @require_authentication
    def upload_image():
        """Store one or more images and return their references.

        Accepts a single ``image`` field or several ``images`` fields. Every
        file is validated independently, so one bad file does not silently drop
        the others.
        """
        current_user()  # authentication is the whole authorisation rule here

        files = request.files.getlist("images") or []
        single = request.files.get("image")
        if single is not None:
            files = [single, *files]
        if not files:
            raise ValidationError(
                "No image was uploaded. Send a file in the 'image' or 'images' field.",
                details={"field": "image"},
            )
        if len(files) > MAX_PHOTOS_PER_REQUEST:
            raise ValidationError(
                f"At most {MAX_PHOTOS_PER_REQUEST} images per request.",
                details={"count": len(files), "max": MAX_PHOTOS_PER_REQUEST},
            )

        subdir = (request.form.get("subdir") or "inspections").strip("/")
        if not subdir.isidentifier() and "/" in subdir:
            raise ValidationError(
                "'subdir' must be a simple folder name.", details={"field": "subdir"}
            )
        if not subdir.replace("_", "").isalnum():
            raise ValidationError(
                "'subdir' may contain letters, digits and underscores only.",
                details={"field": "subdir"},
            )

        references = [
            media.store_dated_upload(
                file,
                subdir=subdir,
                label=(request.form.get("label") or None),
                field=f"images[{index}]",
            )
            for index, file in enumerate(files)
        ]

        return created(
            {
                "images": references,
                "total": len(references),
                "message": f"Stored {len(references)} image(s).",
            }
        )

    return blueprint


__all__ = ["create_uploads_blueprint"]
