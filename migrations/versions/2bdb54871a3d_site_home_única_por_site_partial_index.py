"""site: home única por site (partial index)

Revision ID: 2bdb54871a3d
Revises: 9c7fde03ffe9
Create Date: 2025-09-19 18:45:53.589805

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2bdb54871a3d'
down_revision = '9c7fde03ffe9'
branch_labels = None
depends_on = None


def upgrade():
    # (1) Saneamento: se houver mais de uma 'home' no mesmo site, mantenha só a primeira
    op.execute("""
        WITH ranked AS (
          SELECT id, site_id, is_home,
                 ROW_NUMBER() OVER (PARTITION BY site_id ORDER BY id) AS rn
          FROM site_pages
          WHERE is_home = TRUE
        )
        UPDATE site_pages sp
        SET is_home = FALSE
        FROM ranked r
        WHERE sp.id = r.id AND r.rn > 1;
    """)

    # (2) Índice parcial ÚNICO garantindo UMA home por site (Postgres)
    # Se já existir (rodou antes), ignore o erro
    try:
        op.create_index(
            "uq_sitepage_home_per_site",
            "site_pages",
            ["site_id"],
            unique=True,
            postgresql_where=sa.text("is_home = true"),
        )
    except Exception:
        pass

    # (3) Índices úteis (idempotentes)
    try:
        op.create_index("ix_site_pages_site_order", "site_pages", ["site_id", "order"], unique=False)
    except Exception:
        pass
    try:
        op.create_index("ix_site_blocks_page_order", "site_blocks", ["page_id", "order"], unique=False)
    except Exception:
        pass


def downgrade():
    # Remover apenas o índice parcial e os auxiliares criados aqui
    try:
        op.drop_index("uq_sitepage_home_per_site", table_name="site_pages")
    except Exception:
        pass
    try:
        op.drop_index("ix_site_pages_site_order", table_name="site_pages")
    except Exception:
        pass
    try:
        op.drop_index("ix_site_blocks_page_order", table_name="site_blocks")
    except Exception:
        pass