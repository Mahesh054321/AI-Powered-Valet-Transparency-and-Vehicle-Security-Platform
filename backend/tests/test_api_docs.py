"""The API documentation must match the implementation.

These tests exist because hand-written API docs rot the moment a route changes,
and stale docs are worse than none: a client built against them fails in
production rather than at review time.

The check runs in both directions, because each direction catches a different
kind of lie:

* a documented route the app does not serve  -> the docs promise an endpoint
  that does not exist;
* a served route the docs omit               -> the docs are incomplete, and a
  caller cannot discover part of the API.

Aliases and trailing-slash variants are folded onto their canonical form rather
than ignored, so an alias can only pass if its canonical path is documented.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

#: tests/ -> backend -> <repo root>.
HERE = Path(__file__).resolve().parents[1]
REPO_ROOT = HERE.parent
DOCS_DIR = REPO_ROOT / "docs" / "api"
SPEC_PATH = DOCS_DIR / "openapi.yaml"
REFERENCE_PATH = DOCS_DIR / "API-REFERENCE.md"

HTTP_METHODS = {"get", "post", "patch", "put", "delete"}

#: Flask converter syntax -> OpenAPI brace syntax.
FLASK_PARAM = re.compile(r"<(?:[^:>]+:)?([^>]+)>")

#: Not part of the documented domain API.
IGNORED_ENDPOINTS = {"static", "service_root"}

#: Statuses that count as "this operation documents a failure mode".
ERROR_STATUSES = {
    "400", "401", "403", "404", "409", "413", "415", "422", "429", "500", "503",
}

#: Unversioned alias prefixes, each mapping onto a documented /api/v1 prefix.
ALIAS_PREFIXES = (
    "/api/auth",
    "/api/inspections",
    "/api/gps",
    "/api/geofences",
    "/api/alerts",
    "/api/ml",
    "/api/retrieval",
)

pytestmark = pytest.mark.integration


def _flask_to_openapi(rule: str) -> str:
    return FLASK_PARAM.sub(r"{\1}", rule)


def _canonicalise(path: str) -> str:
    p = path.rstrip("/") or "/"
    for alias in ALIAS_PREFIXES:
        if p == alias or p.startswith(alias + "/"):
            return "/api/v1" + p[len("/api") :]
    return p


@pytest.fixture(scope="module")
def spec() -> dict:
    assert SPEC_PATH.exists(), f"missing generated spec at {SPEC_PATH}"
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def documented(spec) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


@pytest.fixture
def served(app) -> set[tuple[str, str]]:
    """The operations the live application actually serves.

    Function-scoped to match the `app` fixture; the URL map is cheap to walk and
    a scope mismatch here would be a harder bug to spot than the saved time.
    """
    operations = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint in IGNORED_ENDPOINTS:
            continue
        path = _canonicalise(_flask_to_openapi(str(rule)))
        for method in rule.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            operations.add((method.upper(), path))
    return operations


class TestSpecMatchesImplementation:
    def test_no_documented_route_is_invented(self, documented, served):
        """A documented endpoint must actually be served."""
        invented = sorted(documented - served)
        assert not invented, (
            "The documentation promises endpoints the application does not "
            f"serve: {invented}. Remove them, or the route was renamed and the "
            "docs were not regenerated."
        )

    def test_no_served_route_is_undocumented(self, documented, served):
        """Every served endpoint must appear in the documentation."""
        missing = sorted(served - documented)
        assert not missing, (
            "The application serves endpoints the documentation omits: "
            f"{missing}. Regenerate the docs with "
            "'python utils/scripts/api_docs/build_docs.py'."
        )

    def test_the_two_directions_agree_exactly(self, documented, served):
        """Belt and braces: the sets are identical, not merely overlapping."""
        assert documented == served

    def test_the_documented_surface_is_not_trivially_small(self, documented):
        """Guard against a spec that passes by documenting almost nothing."""
        assert len(documented) >= 60, f"only {len(documented)} operations documented"


class TestSpecIsWellFormed:
    def test_declares_openapi_3(self, spec):
        assert spec["openapi"].startswith("3."), spec["openapi"]

    def test_every_operation_has_an_id_summary_and_tag(self, spec):
        for path, item in spec["paths"].items():
            for method, op in item.items():
                if method not in HTTP_METHODS:
                    continue
                where = f"{method.upper()} {path}"
                assert op.get("operationId"), f"{where} has no operationId"
                assert op.get("summary"), f"{where} has no summary"
                assert op.get("tags"), f"{where} has no tag"
                assert op.get("responses"), f"{where} declares no responses"

    def test_operation_ids_are_unique(self, spec):
        ids = [
            op["operationId"]
            for item in spec["paths"].values()
            for method, op in item.items()
            if method in HTTP_METHODS
        ]
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, f"duplicate operationId(s): {sorted(duplicates)}"

    def test_every_operation_states_its_auth_and_role_requirement(self, spec):
        """The brief asks for auth and role per endpoint; both must be present."""
        for path, item in spec["paths"].items():
            for method, op in item.items():
                if method not in HTTP_METHODS:
                    continue
                where = f"{method.upper()} {path}"
                assert "x-auth" in op, f"{where} does not state its auth requirement"
                assert "x-roles" in op, f"{where} does not state its role requirement"

    def test_every_operation_declares_at_least_one_error_response(self, spec):
        """A route that cannot fail is not a route anyone has used."""
        for path, item in spec["paths"].items():
            for method, op in item.items():
                if method not in HTTP_METHODS:
                    continue
                codes = {str(c) for c in op["responses"]}
                assert codes & ERROR_STATUSES, (
                    f"{method.upper()} {path} declares no error responses"
                )

    def test_security_scheme_is_defined(self, spec):
        assert "bearerAuth" in spec["components"]["securitySchemes"]

    def test_every_tag_is_declared(self, spec):
        declared = {t["name"] for t in spec["tags"]}
        used = {
            tag
            for item in spec["paths"].values()
            for method, op in item.items()
            if method in HTTP_METHODS
            for tag in op.get("tags", [])
        }
        assert used <= declared, f"undeclared tag(s): {sorted(used - declared)}"


class TestGeneratedArtefactsAreCurrent:
    def test_the_reference_was_generated_alongside_the_spec(self):
        assert REFERENCE_PATH.exists(), f"missing {REFERENCE_PATH}"

    def test_regenerating_produces_no_diff(self):
        """The committed docs must match what the generator produces now.

        Without this, someone can edit the YAML by hand and the reference falls
        out of step, or add a route to the catalogue and forget to rebuild.
        """
        import sys

        sys.path.insert(0, str(HERE / "utils" / "scripts" / "api_docs"))
        import build_docs

        fresh = build_docs.build_openapi()
        committed = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
        assert fresh == committed, (
            "docs/api/openapi.yaml is out of date. Rebuild it with "
            "'python utils/scripts/api_docs/build_docs.py'."
        )

    def test_the_markdown_reference_covers_every_group(self, spec):
        text = REFERENCE_PATH.read_text(encoding="utf-8")
        for tag in spec["tags"]:
            assert f"## {tag['name']}" in text, f"reference has no section for {tag['name']}"
