"""WSGI entry point.

Development (with reloader):
    flask --app wsgi:app run --host 0.0.0.0 --port 5000

Production on Windows:
    waitress-serve --host 0.0.0.0 --port 5000 wsgi:app

Production on Linux:
    gunicorn --bind 0.0.0.0:5000 --workers 4 wsgi:app
"""

from __future__ import annotations

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Only for `python wsgi.py` during quick local checks. The Flask dev server
    # must not be used to serve real traffic.
    # 0.0.0.0 so the API is reachable from the React dev server (and from a
    # phone on the same Wi-Fi during a demo). This convenience runner is for
    # development only; production uses waitress/gunicorn behind a reverse proxy.
    app.run(host="0.0.0.0", port=5000, debug=app.config.get("DEBUG", False))  # noqa: S104
