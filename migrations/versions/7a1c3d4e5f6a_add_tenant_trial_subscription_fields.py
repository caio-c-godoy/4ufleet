"""add tenant trial and subscription fields

Revision ID: 7a1c3d4e5f6a
Revises: f8cb5afe8623
Create Date: 2025-12-19 20:15:00.000000

"""
from datetime import datetime, timedelta

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7a1c3d4e5f6a"
down_revision = "f8cb5afe8623"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tenants", sa.Column("trial_started_at", sa.DateTime(), nullable=True))
    op.add_column("tenants", sa.Column("trial_ends_at", sa.DateTime(), nullable=True))
    op.add_column("tenants", sa.Column("subscription_status", sa.String(length=20), nullable=True))
    op.add_column("tenants", sa.Column("subscription_provider", sa.String(length=20), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, created_at FROM tenants")).fetchall()
    now = datetime.utcnow()
    for row in rows:
        data = row._mapping
        started = data["created_at"] or now
        ends = started + timedelta(days=30)
        status = "trialing" if ends >= now else "active"
        conn.execute(
            sa.text(
                "UPDATE tenants "
                "SET trial_started_at=:started, trial_ends_at=:ends, "
                "subscription_status=:status, subscription_provider=:provider "
                "WHERE id=:id"
            ),
            {
                "started": started,
                "ends": ends,
                "status": status,
                "provider": "none",
                "id": data["id"],
            },
        )

    op.alter_column(
        "tenants",
        "trial_started_at",
        existing_type=sa.DateTime(),
        nullable=False,
    )
    op.alter_column(
        "tenants",
        "trial_ends_at",
        existing_type=sa.DateTime(),
        nullable=False,
    )
    op.alter_column(
        "tenants",
        "subscription_status",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="trialing",
    )
    op.alter_column(
        "tenants",
        "subscription_provider",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="none",
    )


def downgrade():
    op.drop_column("tenants", "subscription_provider")
    op.drop_column("tenants", "subscription_status")
    op.drop_column("tenants", "trial_ends_at")
    op.drop_column("tenants", "trial_started_at")
