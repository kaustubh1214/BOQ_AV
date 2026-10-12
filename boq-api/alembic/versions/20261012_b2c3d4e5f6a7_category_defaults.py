"""Material category defaults (unit, HSN, GST) used to pre-fill the new-material form.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("material_categories", sa.Column("default_uom_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("uoms.id"), nullable=True))
    op.add_column("material_categories", sa.Column("default_hsn_code", sa.String(12), nullable=True))
    op.add_column("material_categories", sa.Column("default_gst_rate", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("material_categories", "default_gst_rate")
    op.drop_column("material_categories", "default_hsn_code")
    op.drop_column("material_categories", "default_uom_id")
