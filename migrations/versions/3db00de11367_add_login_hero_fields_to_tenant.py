"""Add login hero fields to Tenant

Revision ID: 3db00de11367
Revises: 85046ed6f53a
Create Date: 2025-09-19 03:19:50.613332
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "3db00de11367"
down_revision = "85046ed6f53a"
branch_labels = None
depends_on = None


def upgrade():
    # Adiciona as colunas do "Login Hero" sem afetar outras tabelas.
    # NOT NULL + server_default=TRUE para evitar NotNullViolation nas linhas já existentes.
    op.add_column(
        "tenants",
        sa.Column(
            "login_hero_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column("tenants", sa.Column("login_hero_kicker", sa.String(length=120), nullable=True))
    op.add_column("tenants", sa.Column("login_hero_title", sa.String(length=180), nullable=True))
    op.add_column("tenants", sa.Column("login_hero_desc", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("login_hero_image", sa.String(length=300), nullable=True))

    # Remove o default a nível de banco (fica só o default do modelo Python)
    op.alter_column("tenants", "login_hero_enabled", server_default=None)


def downgrade():
    # Reverte apenas o que foi criado nesta revisão.
    op.drop_column("tenants", "login_hero_image")
    op.drop_column("tenants", "login_hero_desc")
    op.drop_column("tenants", "login_hero_title")
    op.drop_column("tenants", "login_hero_kicker")
    op.drop_column("tenants", "login_hero_enabled")
