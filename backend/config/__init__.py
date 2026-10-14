"""Configuration package.

Public surface:

    from config import get_settings, Settings

``get_settings()`` is the only supported way to obtain configuration; it reads
the environment exactly once per call and validates the profile.
"""

from __future__ import annotations

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
from config.settings import (
    DEFAULT_CORS_ORIGINS,
    DEFAULT_IMAGE_EXTENSIONS,
    MINIMUM_JWT_SECRET_LENGTH,
    SETTINGS_MAP,
    DevelopmentSettings,
    ProductionSettings,
    Settings,
    TestingSettings,
    build_database_url,
    get_settings,
    mask_database_url,
    upload_root,
)

__all__ = [
    "BACKEND_ROOT",
    "build_database_url",
    "DEFAULT_CORS_ORIGINS",
    "DEFAULT_IMAGE_EXTENSIONS",
    "MINIMUM_JWT_SECRET_LENGTH",
    "SETTINGS_MAP",
    "DevelopmentSettings",
    "ProductionSettings",
    "Settings",
    "TestingSettings",
    "env_bool",
    "env_float",
    "env_int",
    "env_list",
    "env_set",
    "env_str",
    "get_settings",
    "load_environment",
    "mask_database_url",
    "upload_root",
]
