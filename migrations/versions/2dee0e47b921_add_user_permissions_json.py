"""add user.permissions json

Revision ID: 2dee0e47b921
Revises: 3db00de11367
Create Date: 2025-09-19 10:57:58.220212

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '2dee0e47b921'
down_revision = '3db00de11367'
branch_labels = None
depends_on = None


TABLE = "users"

def upgrade():
    # JSON em Postgres; em SQLite o Alembic cai para TEXT compatível
    try:
        op.add_column(TABLE, sa.Column('permissions', sa.JSON(), nullable=True))
    except Exception:
        # fallback: alguns ambientes precisam de TEXT
        op.add_column(TABLE, sa.Column('permissions', sa.Text(), nullable=True))

    # inicializa com {}
    conn = op.get_bind()
    conn.execute(sa.text(f"UPDATE {TABLE} SET permissions = '{{}}' WHERE permissions IS NULL"))

    # agora seta NOT NULL (em motores que suportam)
    try:
        op.alter_column(TABLE, 'permissions', nullable=False)
    except Exception:
        pass

def downgrade():
    op.drop_column(TABLE, 'permissions')
