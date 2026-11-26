"""Introduce a dedicated staff-facing pickup-request alert category.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# The previous check values, including the additions from 0009.
OLD = ("GEOFENCE_BREACH", "SUSPICIOUS_MOVEMENT", "OVERSPEED",
       "POTENTIAL_DAMAGE", "RETRIEVAL_READY", "TAMPER_ALERT", "SECURITY_ALERT")
NEW = OLD + ("RETRIEVAL_REQUESTED",)

def check(values):
    return "type IN (" + ", ".join("'" + v + "'" for v in values) + ")"

def upgrade():
    op.drop_constraint("ck_alerts_type_valid", "alerts", type_="check")
    op.create_check_constraint("ck_alerts_type_valid", "alerts", check(NEW))

def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM alerts WHERE type = 'RETRIEVAL_REQUESTED'")).scalar_one():
        raise RuntimeError("Cannot downgrade while pickup-request alerts exist; archive and remove them first")
    op.drop_constraint("ck_alerts_type_valid", "alerts", type_="check")
    op.create_check_constraint("ck_alerts_type_valid", "alerts", check(OLD))
