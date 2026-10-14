"""Add the revoked-token denylist that makes logout real.

JWTs are stateless, so a signed-out token would otherwise stay valid until its
own expiry. This table records the ``jti`` of every token that was explicitly
revoked, and ``load_current_user`` rejects any token found here.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-07
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "revoked_tokens",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column("user_id", UUID),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reason", sa.String(40), nullable=False, server_default="logout"),
        sa.Column("ip_address", sa.String(45)),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_revoked_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "reason IN ('logout', 'password_changed', 'admin_revoked')",
            name="ck_revoked_tokens_reason_valid",
        ),
        comment=(
            "Denylist of signed-out access tokens, keyed by jti. Holds no token "
            "value and no credential material; rows are prunable once "
            "expires_at has passed."
        ),
    )
    op.create_index("uq_revoked_tokens_jti", "revoked_tokens", ["jti"], unique=True)
    op.create_index("ix_revoked_tokens_user_id", "revoked_tokens", ["user_id"])
    op.create_index("ix_revoked_tokens_expires_at", "revoked_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_table("revoked_tokens")
