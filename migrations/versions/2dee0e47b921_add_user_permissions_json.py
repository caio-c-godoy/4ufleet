"""add user.permissions json

Revision ID: 2dee0e47b921
Revises: 3db00de11367
Create Date: 2025-09-19 10:57:58.220212

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '2dee0e47b921'
down_revision = '3db00de11367'
branch_labels = None
depends_on = None


TABLE = "users"


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return False
    return any(col["name"] == column_name for col in columns)


def upgrade():
    if not _column_exists(TABLE, "permissions"):
        # JSON em Postgres; em SQLite/others usa TEXT compatível
        bind = op.get_bind()
        column_type = sa.JSON() if bind.dialect.name == "postgresql" else sa.Text()
        op.add_column(TABLE, sa.Column("permissions", column_type, nullable=True))

    # inicializa com {}
    if _column_exists(TABLE, "permissions"):
        conn = op.get_bind()
        conn.execute(sa.text(f"UPDATE {TABLE} SET permissions = '{{}}' WHERE permissions IS NULL"))

    # agora seta NOT NULL (em motores que suportam)
    if _column_exists(TABLE, "permissions"):
        try:
            op.alter_column(TABLE, "permissions", nullable=False)
        except Exception:
            pass

def downgrade():
    if _column_exists(TABLE, "permissions"):
        op.drop_column(TABLE, "permissions")
