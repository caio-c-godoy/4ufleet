"""Add prospects table

Revision ID: e6e95da84641
Revises: 371eac6b3b5e
Create Date: 2025-09-15 10:17:10.928640
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e6e95da84641"
down_revision = "371eac6b3b5e"
branch_labels = None
depends_on = None


def _has_table(insp: sa.engine.reflection.Inspector, table: str) -> bool:
    try:
        return insp.has_table(table)
    except Exception:
        return False


def _has_index(insp: sa.engine.reflection.Inspector, table: str, name: str) -> bool:
    try:
        return any(ix.get("name") == name for ix in insp.get_indexes(table))
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # --- Prospects: cria tabela se não existir ---
    if not _has_table(insp, "prospects"):
        op.create_table(
            "prospects",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("email", sa.String(length=120), nullable=False),
            sa.Column("phone", sa.String(length=60), nullable=True),
            sa.Column("source", sa.String(length=60), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column("last_contact_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_channel", sa.String(length=20), nullable=True),
        )

    # --- Prospects: cria índices apenas se faltarem ---
    for idx_name, cols in [
        (op.f("ix_prospects_created_at"), ["created_at"]),
        (op.f("ix_prospects_email"), ["email"]),
        (op.f("ix_prospects_status"), ["status"]),
    ]:
        if _has_table(insp, "prospects") and not _has_index(insp, "prospects", idx_name):
            op.create_index(idx_name, "prospects", cols, unique=False)

    # --- Vehicle maintenance: dropar com segurança se existir ---
    vm_table = "vehicle_maintenance"
    if _has_table(insp, vm_table):
        # dropar índices se existirem
        for idx_name in [
            op.f("ix_vehicle_maintenance_active"),
            op.f("ix_vehicle_maintenance_tenant_id"),
            op.f("ix_vehicle_maintenance_vehicle_id"),
        ]:
            if _has_index(insp, vm_table, idx_name):
                op.drop_index(idx_name, table_name=vm_table)

        # por fim, dropar a tabela
        op.drop_table(vm_table)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # --- Recria vehicle_maintenance se não existir ---
    vm_table = "vehicle_maintenance"
    if not _has_table(insp, vm_table):
        op.create_table(
            vm_table,
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("vehicle_id", sa.Integer(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=True),
        )

        # recria índices se faltarem
        for idx_name, cols in [
            (op.f("ix_vehicle_maintenance_vehicle_id"), ["vehicle_id"]),
            (op.f("ix_vehicle_maintenance_tenant_id"), ["tenant_id"]),
            (op.f("ix_vehicle_maintenance_active"), ["active"]),
        ]:
            if not _has_index(insp, vm_table, idx_name):
                op.create_index(idx_name, vm_table, cols, unique=False)

    # --- Prospects: excluir com segurança ---
    if _has_table(insp, "prospects"):
        for idx_name in [
            op.f("ix_prospects_status"),
            op.f("ix_prospects_email"),
            op.f("ix_prospects_created_at"),
        ]:
            if _has_index(insp, "prospects", idx_name):
                op.drop_index(idx_name, table_name="prospects")

        op.drop_table("prospects")
