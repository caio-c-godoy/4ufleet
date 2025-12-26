"""Add login hero fields to Tenant

Revision ID: 3db00de11367
Revises: 85046ed6f53a
Create Date: 2025-09-19 03:19:50.613332
"""
from alembic import op
import sqlalchemy as sa


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return False
    return any(col["name"] == column_name for col in columns)

# revision identifiers, used by Alembic.
revision = "3db00de11367"
down_revision = "85046ed6f53a"
branch_labels = None
depends_on = None


def upgrade():
    # Adiciona as colunas do "Login Hero" sem afetar outras tabelas.
    # NOT NULL + server_default=TRUE para evitar NotNullViolation nas linhas já existentes.
    if not _column_exists("tenants", "login_hero_enabled"):
        op.add_column(
            "tenants",
            sa.Column(
                "login_hero_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )
    if not _column_exists("tenants", "login_hero_kicker"):
        op.add_column("tenants", sa.Column("login_hero_kicker", sa.String(length=120), nullable=True))
    if not _column_exists("tenants", "login_hero_title"):
        op.add_column("tenants", sa.Column("login_hero_title", sa.String(length=180), nullable=True))
    if not _column_exists("tenants", "login_hero_desc"):
        op.add_column("tenants", sa.Column("login_hero_desc", sa.Text(), nullable=True))
    if not _column_exists("tenants", "login_hero_image"):
        op.add_column("tenants", sa.Column("login_hero_image", sa.String(length=300), nullable=True))

    # Remove o default a nível de banco (fica só o default do modelo Python)
    if _column_exists("tenants", "login_hero_enabled"):
        op.alter_column("tenants", "login_hero_enabled", server_default=None)


def downgrade():
    # Reverte apenas o que foi criado nesta revisão.
    if _column_exists("tenants", "login_hero_image"):
        op.drop_column("tenants", "login_hero_image")
    if _column_exists("tenants", "login_hero_desc"):
        op.drop_column("tenants", "login_hero_desc")
    if _column_exists("tenants", "login_hero_title"):
        op.drop_column("tenants", "login_hero_title")
    if _column_exists("tenants", "login_hero_kicker"):
        op.drop_column("tenants", "login_hero_kicker")
    if _column_exists("tenants", "login_hero_enabled"):
        op.drop_column("tenants", "login_hero_enabled")
