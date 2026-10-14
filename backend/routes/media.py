"""Serving stored media.

    GET /media/<path>   public - a stored image, by its reference path

Mounted **unversioned** at ``/media``, like ``/api/health``: it serves bytes,
not a contract, and the dev proxy already forwards ``/media`` to the backend.

WHY THIS ROUTE IS NOT BEHIND AUTHENTICATION
-------------------------------------------
A browser rendering ``<img src="/media/...">`` cannot attach an
``Authorization`` header, so an authenticated media route would simply not
display in the SPA. The access control here is therefore the **unguessability of
the path**: every stored filename is a random UUID4 hex generated server-side
(``services/media.py`` never trusts a client-supplied name), giving 122 bits of
entropy, and the directory structure is not listable.

That is a real trade-off and it is stated plainly rather than glossed over:
anyone holding a full media URL can fetch that image, and URLs do leak - into
browser history, logs and referrer headers. The mitigations in place are the
unguessable names, the fact that these are vehicle condition photos rather than
identity documents, and no directory listing. The production answer is a
short-lived signed URL or authenticated streaming via a blob response, which is
noted in the README as pre-deployment work.

Path traversal is refused twice over: the reference is re-validated against the
same rules used when it was written, and the resolved path is then confirmed to
still sit inside the storage root.
"""

from __future__ import annotations

from flask import Blueprint, send_from_directory
from werkzeug.exceptions import NotFound

from services.media import resolve_media_path
from utils.exceptions import ValidationError

#: Condition photos are re-fetched often and never mutated in place (a new
#: upload gets a new filename), so they cache well.
CACHE_SECONDS = 3600


def create_media_blueprint() -> Blueprint:
    """Build a fresh media blueprint (a new instance per app)."""
    blueprint = Blueprint("media", __name__)

    @blueprint.get("/<path:reference>")
    def serve(reference: str):
        """Serve one stored image.

        A missing or invalid reference answers 404 rather than 422: to a client
        fetching an ``<img>``, "not here" is the honest and useful answer, and
        the distinction between "never existed" and "was deleted" is not worth
        leaking.
        """
        try:
            absolute = resolve_media_path(reference)
        except ValidationError as exc:
            raise NotFound(description=str(exc)) from exc

        # Served by parent directory plus filename rather than by a path
        # relative to the storage root: `resolve_media_path` resolves symlinks
        # and `storage_root()` does not, so `relative_to` could raise on a
        # symlinked storage folder. Containment was already proven above.
        response = send_from_directory(absolute.parent, absolute.name)
        response.cache_control.public = True
        response.cache_control.max_age = CACHE_SECONDS
        return response

    return blueprint


__all__ = ["create_media_blueprint"]
