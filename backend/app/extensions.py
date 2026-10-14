"""Extension instances, created once and initialised by the app factory.

Importing this module has no side effects: nothing connects to the database
until ``init_app`` runs inside ``create_app()``.
"""

from __future__ import annotations

from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData

# Naming convention so Alembic autogenerate produces stable, predictable names.
#
# The constraint names written in the models and in the migrations are already
# fully qualified (`ck_users_status_valid`, `fk_vehicles_owner_id_users`), so
# these templates must not re-insert the table name or another prefix. Two
# earlier attempts got this wrong: `ck_%(table_name)s_%(constraint_name)s`
# produced `ck_parking_zones_ck_parking_zones_capacity_positive`, and
# `ck_%(constraint_name)s` produced `ck_ck_parking_zones_capacity_positive`,
# because `%(constraint_name)s` already resolves to the whole explicit name.
# Either way the migrated schema and the ORM metadata disagreed, which is
# exactly the drift autogenerate exists to prevent.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

db = SQLAlchemy(metadata=MetaData(naming_convention=NAMING_CONVENTION))
cors = CORS()
