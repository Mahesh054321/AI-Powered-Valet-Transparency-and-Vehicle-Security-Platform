"""A fixed-window attempt counter for the credential endpoints.

Why this exists
---------------
``RateLimitError`` was declared in ``utils/exceptions.py`` and exported, but
nothing ever raised it: the login endpoint could be hammered indefinitely, which
makes a password-guessing attack only as slow as the attacker wants it to be.
This module is the missing enforcement.

Why the key is the email, not the client IP
-------------------------------------------
Behind a reverse proxy every caller presents the same peer address, so an
IP-keyed limiter does the wrong thing twice over: one person guessing passwords
locks out their entire office, and an attacker on a rotating address resets
their own counter by moving. Counting attempts *against the account being
attacked* throttles the attack regardless of where it comes from.

The trade-off is real and worth stating: an attacker who knows an address can
deliberately trip the limiter and lock that account's owner out for the length
of the window. That is the lesser harm here -- a temporary lockout beats a
compromised account -- and the window is short enough (15 minutes by default)
that it is an inconvenience rather than a denial of service.

Scope and limits
----------------
This is an in-process counter. It is correct for the single-process deployment
this project targets and for the test suite, and it is *not* shared across
workers: running N processes gives an attacker N x the budget. A multi-worker
deployment should swap this for a Redis or database-backed counter behind the
same two functions, which is why the public surface is deliberately tiny.

Successful logins clear the counter, so a legitimate user who mistyped twice is
not charged for the misses once they get it right.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _Window:
    """Attempt timestamps recorded inside the current window."""

    attempts: list[float] = field(default_factory=list)


class AttemptLimiter:
    """Counts attempts per key inside a rolling window.

    Thread-safe: Flask serves requests from a thread pool, and a dict mutation
    during a concurrent read is exactly the kind of bug that only shows up under
    load, in production, during a demo.
    """

    def __init__(self, *, max_attempts: int, window_seconds: int) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def _prune(self, window: _Window, now: float) -> None:
        """Drop attempts that have aged out of the window."""
        cutoff = now - self.window_seconds
        window.attempts = [t for t in window.attempts if t > cutoff]

    def register_attempt(self, key: str) -> None:
        """Record one attempt against ``key``."""
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if window is None:
                window = self._windows[key] = _Window()
            window.attempts.append(now)
            self._prune(window, now)

    def remaining(self, key: str) -> int:
        """Attempts still allowed for ``key`` in the current window."""
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if window is None:
                return self.max_attempts
            self._prune(window, now)
            return max(0, self.max_attempts - len(window.attempts))

    def is_exceeded(self, key: str) -> bool:
        """True when ``key`` has used its whole budget."""
        return self.remaining(key) <= 0

    def retry_after(self, key: str) -> int:
        """Whole seconds until the oldest attempt leaves the window."""
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if not window or not window.attempts:
                return 0
            self._prune(window, now)
            if not window.attempts:
                return 0
            oldest = min(window.attempts)
            return max(1, int(oldest + self.window_seconds - now) + 1)

    def clear(self, key: str) -> None:
        """Forget a key entirely -- used after a successful login."""
        with self._lock:
            self._windows.pop(key, None)

    def reset(self) -> None:
        """Forget every key. Test-only; production code should not need it."""
        with self._lock:
            self._windows.clear()


def _key(email: str) -> str:
    """Normalise the identity the budget is charged to.

    Lower-cased, because the account lookup is case-insensitive and a limiter
    that treated ``A@b.c`` and ``a@b.c`` as different people would hand an
    attacker a free doubling of the budget for the price of a shift key.
    """
    return f"login:{str(email or '').strip().lower()}"


def register_failed_login(email: str) -> None:
    """Charge one failed attempt to ``email``."""
    limiter = _limiter()
    if limiter is None:
        return
    limiter.register_attempt(_key(email))


def login_is_throttled(email: str) -> tuple[bool, int]:
    """``(throttled, retry_after_seconds)`` for ``email``."""
    limiter = _limiter()
    if limiter is None:
        return False, 0
    key = _key(email)
    return limiter.is_exceeded(key), limiter.retry_after(key)


def clear_failed_logins(email: str) -> None:
    """Clear the counter after a successful authentication."""
    limiter = _limiter()
    if limiter is None:
        return
    limiter.clear(_key(email))


def reset_limiter() -> None:
    """Forget all counters. Intended for test setup."""
    limiter = _limiter()
    if limiter is not None:
        limiter.reset()


def _limiter() -> AttemptLimiter | None:
    """Build the process-wide limiter from the current settings, once.

    Returns ``None`` when throttling is switched off, which lets the operator
    disable it without an import-time dependency on configuration.
    """
    from flask import current_app

    try:
        config = current_app.config
    except RuntimeError:  # pragma: no cover - called outside an app context
        return None

    if not config.get("RATE_LIMIT_ENABLED", True):
        return None

    existing = config.get("_LOGIN_ATTEMPT_LIMITER")
    if existing is not None:
        return existing

    built = AttemptLimiter(
        max_attempts=int(config.get("LOGIN_RATE_LIMIT_ATTEMPTS", 5)),
        window_seconds=int(config.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 900)),
    )
    # Stored on the app config so one Flask app has exactly one counter, and so
    # a test that builds a second app does not inherit the first app's state.
    config["_LOGIN_ATTEMPT_LIMITER"] = built
    logger.debug(
        "login throttling active: %s attempts / %ss",
        built.max_attempts,
        built.window_seconds,
    )
    return built
