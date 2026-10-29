"""Application settings, assembled entirely from environment variables.

The attributes are grouped by the concern they configure. Every value is
documented in ``backend/.env.example``; nothing is hardcoded.

    Flask        APP_NAME, API_VERSION, DEBUG/TESTING, MAX_CONTENT_LENGTH
    PostgreSQL   POSTGRES_* / DATABASE_URL, DB_* pool settings
    CORS         CORS_ALLOWED_ORIGINS
    JWT          JWT_SECRET_KEY, JWT_ALGORITHM, JWT_ISSUER, JWT_AUDIENCE, TTLs
    Passwords    PASSWORD_HASH_SCHEME, PASSWORD_HASH_ITERATIONS
    Uploads      UPLOAD_FOLDER, ALLOWED_IMAGE_EXTENSIONS, pixel bounds
    Logging      LOG_LEVEL, JSON_LOGS
    Domain       GEOFENCE_BUFFER_METRES, SUSPICIOUS_SPEED_KMPH, TELEMETRY_*
    Bootstrap    SEED_ADMIN_*

``ProductionSettings.validate()`` refuses to start with a weak secret or a
wildcard CORS origin - failing at boot beats failing at request time.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from config.environment import (
    BACKEND_ROOT,
    env_bool,
    env_float,
    env_int,
    env_list,
    env_set,
    env_str,
    load_environment,
)

DEFAULT_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]
DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
MINIMUM_JWT_SECRET_LENGTH = 32


def mask_database_url(url: str) -> str:
    """Return a DSN with credentials removed, e.g. ``...://***:***@host:5432/db``."""
    if "://" not in url:
        return "***REDACTED***"
    scheme, rest = url.split("://", 1)
    host = rest.rsplit("@", 1)[-1] if "@" in rest else rest
    return f"{scheme}://***:***@{host}"


def build_database_url() -> str:
    """Assemble the SQLAlchemy DSN for PostgreSQL.

    ``DATABASE_URL`` wins when present; otherwise the DSN is built from the
    discrete ``POSTGRES_*`` variables. Credentials are percent-encoded so a
    password containing ``@`` or ``:`` cannot corrupt the URI.
    """
    explicit = env_str("DATABASE_URL")
    if explicit:
        if explicit.startswith("postgres://"):
            explicit = explicit.replace("postgres://", "postgresql+psycopg2://", 1)
        return explicit

    user = env_str("POSTGRES_USER", "valet_user")
    password = env_str("POSTGRES_PASSWORD", "")
    host = env_str("POSTGRES_HOST", "localhost")
    port = env_int("POSTGRES_PORT", 5432)
    database = env_str("POSTGRES_DB", "valet_db")

    if not password:
        raise ValueError(
            "POSTGRES_PASSWORD is not set. Copy backend/.env.example to backend/.env "
            "and set a real password, or provide DATABASE_URL."
        )

    credentials = f"{quote_plus(user)}:{quote_plus(password)}"
    return f"postgresql+psycopg2://{credentials}@{host}:{port}/{database}"


class Settings:
    """Environment-driven configuration shared by every profile."""

    #: Environment name, copied into ``app.config["APP_ENV"]`` by the factory.
    #: Flask removed ``app.env`` in 2.3, so the name travels in config.
    ENV_NAME = "development"

    def __init__(self) -> None:
        load_environment()

        # --- Flask ----------------------------------------------------------
        self.APP_NAME = env_str("APP_NAME", "valet-transparency-api")
        self.API_VERSION = env_str("API_VERSION", "v1")
        self.SECRET_NAMESPACE = env_str("SECRET_NAMESPACE", "valet-transparency")
        self.MAX_CONTENT_LENGTH = env_int("MAX_CONTENT_LENGTH_MB", 15) * 1024 * 1024

        # --- PostgreSQL -----------------------------------------------------
        self.SQLALCHEMY_DATABASE_URI = self._build_database_url()
        self.SQLALCHEMY_TRACK_MODIFICATIONS = False
        self.SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_size": env_int("DB_POOL_SIZE", 10),
            "max_overflow": env_int("DB_MAX_OVERFLOW", 20),
            "pool_timeout": env_int("DB_POOL_TIMEOUT", 30),
            "pool_pre_ping": True,
            "echo": env_bool("DB_ECHO", False),
        }

        # --- JWT ------------------------------------------------------------
        self.JWT_SECRET_KEY = env_str("JWT_SECRET_KEY", "")
        self.JWT_ALGORITHM = env_str("JWT_ALGORITHM", "HS256")
        self.JWT_ISSUER = env_str("JWT_ISSUER", self.SECRET_NAMESPACE)
        self.JWT_AUDIENCE = env_str("JWT_AUDIENCE", f"{self.SECRET_NAMESPACE}-clients")
        self.JWT_ACCESS_TOKEN_TTL_MINUTES = env_int("JWT_ACCESS_TOKEN_TTL_MINUTES", 30)
        self.JWT_REFRESH_TOKEN_TTL_DAYS = env_int("JWT_REFRESH_TOKEN_TTL_DAYS", 7)

        # --- Password hashing ------------------------------------------------
        self.PASSWORD_HASH_SCHEME = env_str("PASSWORD_HASH_SCHEME", "pbkdf2:sha256")
        self.PASSWORD_HASH_ITERATIONS = env_int("PASSWORD_HASH_ITERATIONS", 600_000)

        # --- CORS -------------------------------------------------------------
        self.CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ORIGINS)

        # --- File uploads -----------------------------------------------------
        self.UPLOAD_FOLDER = (BACKEND_ROOT / env_str("UPLOAD_FOLDER", "storage/media")).resolve()
        self.ALLOWED_IMAGE_EXTENSIONS = env_set(
            "ALLOWED_IMAGE_EXTENSIONS", DEFAULT_IMAGE_EXTENSIONS
        )
        self.MIN_IMAGE_PIXELS = env_int("MIN_IMAGE_PIXELS", 64)
        self.MAX_IMAGE_PIXELS = env_int("MAX_IMAGE_PIXELS", 4096)

        # --- Login throttling ---------------------------------------------------
        # Brute-force protection for the credential endpoints. Keyed on the
        # *email* rather than the client IP: behind a reverse proxy every caller
        # shares one address, so an IP key would let an attacker lock out a whole
        # NAT group (or, worse, be reset by simply rotating it). Counting against
        # the address being attacked throttles the attack itself.
        self.LOGIN_RATE_LIMIT_ATTEMPTS = env_int("LOGIN_RATE_LIMIT_ATTEMPTS", 5)
        self.LOGIN_RATE_LIMIT_WINDOW_SECONDS = env_int("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 900)
        self.RATE_LIMIT_ENABLED = env_str("RATE_LIMIT_ENABLED", "true").lower() != "false"

        # --- Domain behaviour --------------------------------------------------
        self.GEOFENCE_BUFFER_METRES = env_float("GEOFENCE_BUFFER_METRES", 25.0)
        self.SUSPICIOUS_SPEED_KMPH = env_float("SUSPICIOUS_SPEED_KMPH", 12.0)
        self.TELEMETRY_INTERVAL_SECONDS = env_int("TELEMETRY_INTERVAL_SECONDS", 30)
        # `simulated` unless real GPS hardware is wired up. Surfaced on every
        # telemetry payload so the UI can label provenance honestly.
        self.TELEMETRY_SOURCE = (env_str("TELEMETRY_SOURCE", "simulated") or "simulated").lower()

        # --- Prototype ML security classifier ----------------------------------
        # All optional by design: a checkout with no trained artifact runs on the
        # rule engine alone, which is the behaviour this project shipped and
        # tested before the ML layer existed. Nothing in the security path
        # depends on these being populated.
        self.ML_MODEL_PATH = (
            BACKEND_ROOT / env_str("ML_MODEL_PATH", "storage/models/security_classifier.json")
        ).resolve()
        # Below this P(SUSPICIOUS) the classifier's opinion is recorded but does
        # not change a fix's status. Deliberately above 0.5: the classifier may
        # only escalate a verdict when it is more confident than the threshold
        # says is worth a human's attention, never on a coin flip.
        self.ML_ESCALATION_THRESHOLD = env_float("ML_ESCALATION_THRESHOLD", 0.70)
        self.ML_ENABLED = env_bool("ML_ENABLED", True)

        # --- Retrieval prediction (phase 9b) -----------------------------------
        # Where the retrieval-time artifact lives. Produced by:
        #   python utils/scripts/train_retrieval_model.py
        # As with the classifier, an absent artifact is a supported state: the
        # endpoint then explains what to run instead of returning an error, and
        # nothing else in the platform depends on this being populated.
        self.RETRIEVAL_MODEL_PATH = (
            BACKEND_ROOT / env_str("RETRIEVAL_MODEL_PATH", "storage/models/retrieval_model.json")
        ).resolve()
        self.RETRIEVAL_ML_ENABLED = env_bool("RETRIEVAL_ML_ENABLED", True)

        # --- Bootstrap administrator -------------------------------------------
        self.SEED_ADMIN_EMAIL = env_str("SEED_ADMIN_EMAIL", "admin@valet.local")
        self.SEED_ADMIN_PASSWORD = env_str("SEED_ADMIN_PASSWORD", "")
        self.SEED_ADMIN_FULL_NAME = env_str("SEED_ADMIN_FULL_NAME", "Platform Administrator")

        # --- Logging -------------------------------------------------------------
        self.LOG_LEVEL = (env_str("LOG_LEVEL", "INFO") or "INFO").upper()
        self.JSON_LOGS = env_bool("JSON_LOGS", False)

    # ------------------------------------------------------------------ DSN
    @staticmethod
    def _build_database_url() -> str:
        """Delegate to the module-level builder (see ``build_database_url``)."""
        return build_database_url()

    # ------------------------------------------------------- Flask properties
    @property
    def TESTING(self) -> bool:  # noqa: N802 - Flask API name
        return False

    @property
    def DEBUG(self) -> bool:  # noqa: N802 - Flask API name
        return False

    # ---------------------------------------------------------------- helpers
    def validate(self) -> None:
        """Hook for profile-specific start-up assertions."""

    def as_safe_dict(self) -> dict[str, object]:
        """Snapshot with secrets removed.

        Safe for ``/api/health`` or a console dump: credentials are replaced,
        while the DSN keeps its scheme and host so an operator can still see
        which database is targeted.
        """
        fully_redacted = {"JWT_SECRET_KEY", "SEED_ADMIN_PASSWORD"}
        out: dict[str, object] = {}
        for key, value in vars(self).items():
            if key.startswith("_"):
                continue
            if key in fully_redacted:
                out[key] = "***REDACTED***" if value else "(empty)"
            elif key == "SQLALCHEMY_DATABASE_URI":
                out[key] = mask_database_url(str(value))
            else:
                out[key] = value
        return out


class DevelopmentSettings(Settings):
    """Local development: debug and reloader on."""

    ENV_NAME = "development"

    @property
    def DEBUG(self) -> bool:  # noqa: N802
        return env_bool("FLASK_DEBUG", True)

    def validate(self) -> None:
        # A short secret is tolerated locally, but we generate one rather than
        # signing tokens with an empty string.
        if len(self.JWT_SECRET_KEY) < MINIMUM_JWT_SECRET_LENGTH:
            import secrets

            self.JWT_SECRET_KEY = secrets.token_urlsafe(64)
            print(
                "[config] WARNING: JWT_SECRET_KEY missing or weak. Generated an ephemeral "
                "secret for this process only. Set JWT_SECRET_KEY in backend/.env.",
                flush=True,
            )


class TestingSettings(Settings):
    """Automated tests: a dedicated database, no debug."""

    ENV_NAME = "testing"

    def __init__(self) -> None:
        super().__init__()
        # Tests get their own database so pytest can never touch real data.
        test_db = env_str("POSTGRES_TEST_DB", "valet_db_test")
        override = env_str("DATABASE_URL_TEST")
        if override:
            self.SQLALCHEMY_DATABASE_URI = override
        else:
            server_part = self.SQLALCHEMY_DATABASE_URI.rsplit("/", 1)[0]
            self.SQLALCHEMY_DATABASE_URI = f"{server_part}/{test_db}"

        if len(self.JWT_SECRET_KEY) < MINIMUM_JWT_SECRET_LENGTH:
            import secrets

            self.JWT_SECRET_KEY = secrets.token_urlsafe(64)

        self.SQLALCHEMY_ENGINE_OPTIONS = {
            **self.SQLALCHEMY_ENGINE_OPTIONS,
            "pool_size": 2,
            "max_overflow": 2,
        }

    @property
    def TESTING(self) -> bool:  # noqa: N802
        return True

    @property
    def DEBUG(self) -> bool:  # noqa: N802
        return False


class ProductionSettings(Settings):
    """Production: strict. Refuses to boot with weak settings."""

    ENV_NAME = "production"

    def validate(self) -> None:
        problems: list[str] = []
        if len(self.JWT_SECRET_KEY) < MINIMUM_JWT_SECRET_LENGTH:
            problems.append(
                f"JWT_SECRET_KEY must be at least {MINIMUM_JWT_SECRET_LENGTH} characters "
                "(set it in the environment)."
            )
        if self.JWT_ALGORITHM.lower() == "none":
            problems.append("JWT_ALGORITHM 'none' is not permitted.")
        if "*" in self.CORS_ALLOWED_ORIGINS:
            problems.append("CORS_ALLOWED_ORIGINS must not contain '*'.")
        if problems:
            raise RuntimeError(
                "Production configuration is invalid:\n  - " + "\n  - ".join(problems)
            )


SETTINGS_MAP: dict[str, type[Settings]] = {
    "development": DevelopmentSettings,
    "testing": TestingSettings,
    "test": TestingSettings,
    "production": ProductionSettings,
}


def get_settings(env_name: str | None = None) -> Settings:
    """Instantiate the settings profile named by ``FLASK_ENV`` or an argument."""
    name = (env_name or env_str("FLASK_ENV", "development") or "development").lower()
    settings_cls = SETTINGS_MAP.get(name)
    if settings_cls is None:
        raise ValueError(
            f"Unknown FLASK_ENV {name!r}. Expected one of: {', '.join(sorted(SETTINGS_MAP))}."
        )
    settings = settings_cls()
    settings.validate()
    return settings


# Module-level convenience, kept explicit so callers never re-read the env.
def upload_root() -> Path:
    return Path(get_settings().UPLOAD_FOLDER)
