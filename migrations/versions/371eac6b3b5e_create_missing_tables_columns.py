"""create missing tables/columns

Revision ID: 371eac6b3b5e
Revises: d84a2c4eb901
Create Date: 2025-09-14 11:57:47.816590
"""

from alembic import op
import sqlalchemy as sa

# --- Identificadores da revisão (obrigatórios pelo Alembic) ---
revision = "371eac6b3b5e"
down_revision = "d84a2c4eb901"
branch_labels = None
depends_on = None
# ---------------------------------------------------------------


def _has_index(insp: sa.engine.reflection.Inspector, table: str, name: str) -> bool:
    """Verifica se um índice com o 'name' já existe na 'table'."""
    try:
        return any(ix.get("name") == name for ix in insp.get_indexes(table))
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1) Garante a existência da tabela antes de criar índices
    if not insp.has_table("support_messages"):
        op.create_table(
            "support_messages",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.Integer, nullable=True),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("email", sa.String(255), nullable=True),
            sa.Column("subject", sa.String(255), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            # Ex.: FK futura
            # sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        )

    # 2) Cria o índice de created_at se ainda não existir
    idx_name = op.f("ix_support_messages_created_at")
    if insp.has_table("support_messages") and not _has_index(
        insp, "support_messages", idx_name
    ):
        op.create_index(
            idx_name,
            "support_messages",
            ["created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # Remove apenas o índice (não derruba a tabela, para evitar perda de dados)
    idx_name = op.f("ix_support_messages_created_at")
    if insp.has_table("support_messages") and _has_index(
        insp, "support_messages", idx_name
    ):
        op.drop_index(idx_name, table_name="support_messages")
