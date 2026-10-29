"""Baseline: enable required PostgreSQL extensions and record the schema anchor.

This revision intentionally creates no domain tables. It exists so that:

  * the ``alembic_version`` table is established,
  * PostgreSQL extensions the schema depends on are enabled once, up front, and
  * a ``schema_meta`` row records which baseline the database was built from.

Phase 2 introduces the identity tables (``users``) and Phase 3 the domain tables
(``vehicles``, ``parking_zones``, ...). Every later migration is reversible.

Revision ID: 0001
Revises:
Create Date: 2026-09-07
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from database.checks import REQUIRED_EXTENSIONS, ensure_extensions

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASELINE_KEY = "baseline"
BASELINE_VALUE = "0001-baseline"


def upgrade() -> None:
    bind = op.get_bind()
    ensure_extensions(bind, REQUIRED_EXTENSIONS)

    op.create_table(
        "schema_meta",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        comment="Non-domain metadata about this database's migration baseline.",
    )
    op.execute(
        sa.text("INSERT INTO schema_meta (key, value) VALUES (:k, :v)").bindparams(
            k=BASELINE_KEY, v=BASELINE_VALUE
        )
    )


def downgrade() -> None:
    op.drop_table("schema_meta")
    # Extensions are deliberately left installed: other objects may depend on
    # them, and dropping an extension is a destructive operator-level decision.
