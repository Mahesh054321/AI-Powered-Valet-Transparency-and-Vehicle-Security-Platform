"""Cross-check the generated OpenAPI spec against the live Flask URL map.

This is the guard behind the claim that the documentation matches the
implementation. It fails on drift in *both* directions:

  * a documented path that the application does not serve (invented endpoint), and
  * a served route that the documentation omits (undocumented endpoint).

Aliases and trailing-slash variants are resolved rather than ignored: the spec
documents the canonical ``/api/v1`` path and records the alias set in prose, so
an alias is only acceptable if its canonical form is documented.

Run with the app importable:  python verify_spec.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BACKEND_ROOT = HERE.parents[2]
REPO_ROOT = HERE.parents[3]
sys.path.insert(0, str(BACKEND_ROOT))

SPEC_PATH = REPO_ROOT / "docs" / "api" / "openapi.yaml"

#: Flask converter syntax -> OpenAPI brace syntax.
FLASK_PARAM = re.compile(r"<(?:[^:>]+:)?([^>]+)>")

#: Routes that are not part of the documented domain API.
IGNORED_ENDPOINTS = {"static", "service_root"}

#: Unversioned alias prefixes. Each maps onto a documented /api/v1 prefix.
ALIAS_PREFIXES = (
    "/api/auth",
    "/api/inspections",
    "/api/gps",
    "/api/geofences",
    "/api/alerts",
    "/api/ml",
    "/api/retrieval",
)


def flask_to_openapi(rule: str) -> str:
    """Convert a Werkzeug rule to an OpenAPI path template."""
    return FLASK_PARAM.sub(r"{\1}", rule)


def canonicalise(path: str) -> str:
    """Map an alias or trailing-slash variant onto its canonical form."""
    p = path.rstrip("/") or "/"
    for alias in ALIAS_PREFIXES:
        if p == alias or p.startswith(alias + "/"):
            p = "/api/v1" + p[len("/api"):]
            break
    return p


def main() -> int:
    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    documented = {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "patch", "put", "delete"}
    }

    from app import create_app

    app = create_app()

    served: set[tuple[str, str]] = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint in IGNORED_ENDPOINTS:
            continue
        path = canonicalise(flask_to_openapi(str(rule)))
        for method in rule.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            served.add((method.upper(), path))

    invented = sorted(documented - served)
    undocumented = sorted(served - documented)

    print(f"documented operations : {len(documented)}")
    print(f"served operations     : {len(served)} (canonicalised, aliases folded)")
    print()

    ok = True

    if invented:
        ok = False
        print(f"FAIL: {len(invented)} documented operation(s) the app does not serve:")
        for method, path in invented:
            print(f"  {method:6} {path}")
        print()

    if undocumented:
        ok = False
        print(f"FAIL: {len(undocumented)} served operation(s) missing from the docs:")
        for method, path in undocumented:
            print(f"  {method:6} {path}")
        print()

    if ok:
        print("OK: the spec and the live URL map agree exactly.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
