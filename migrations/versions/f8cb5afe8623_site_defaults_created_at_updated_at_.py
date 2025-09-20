"""site: defaults created_at/updated_at with timezone

Revision ID: f8cb5afe8623
Revises: 2bdb54871a3d
Create Date: 2025-09-19 19:03:55.502440

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f8cb5afe8623'
down_revision = '2bdb54871a3d'
branch_labels = None
depends_on = None



def upgrade():
    # 1) Garantir tipo timezone-aware (se já for, o alter não quebra)
    for table, cols in [
        ("sites", ["created_at", "updated_at"]),
        ("site_pages", ["created_at", "updated_at"]),
        ("site_blocks", ["created_at", "updated_at"]),
    ]:
        for col in cols:
            try:
                op.alter_column(
                    table, col,
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=False,
                )
            except Exception:
                pass

    # 2) Preencher nulos preexistentes (só por segurança)
    op.execute("UPDATE sites       SET created_at = NOW() WHERE created_at IS NULL;")
    op.execute("UPDATE sites       SET updated_at = NOW() WHERE updated_at IS NULL;")
    op.execute("UPDATE site_pages  SET created_at = NOW() WHERE created_at IS NULL;")
    op.execute("UPDATE site_pages  SET updated_at = NOW() WHERE updated_at IS NULL;")
    op.execute("UPDATE site_blocks SET created_at = NOW() WHERE created_at IS NULL;")
    op.execute("UPDATE site_blocks SET updated_at = NOW() WHERE updated_at IS NULL;")

    # 3) Adicionar DEFAULT NOW() (server_default) e onupdate via trigger simples
    #    Para created_at e updated_at. O 'onupdate' é tratado app-side (SQLAlchemy),
    #    mas deixamos DEFAULT para inserts.

    for table in ("sites", "site_pages", "site_blocks"):
        try:
            op.alter_column(
                table, "created_at",
                server_default=sa.text("NOW()"),
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
            )
        except Exception:
            pass
        try:
            op.alter_column(
                table, "updated_at",
                server_default=sa.text("NOW()"),
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
            )
        except Exception:
            pass

    # (Opcional) published_at com timezone (pode ser NULL)
    try:
        op.alter_column("sites", "published_at", type_=sa.DateTime(timezone=True))
    except Exception:
        pass


def downgrade():
    # Remover server_default (mantendo timezone)
    for table in ("sites", "site_pages", "site_blocks"):
        try:
            op.alter_column(
                table, "created_at",
                server_default=None,
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
            )
        except Exception:
            pass
        try:
            op.alter_column(
                table, "updated_at",
                server_default=None,
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
            )
        except Exception:
            pass
