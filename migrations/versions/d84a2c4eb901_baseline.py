"""baseline"""

from alembic import op
import sqlalchemy as sa

# Revisão atual
revision = "d84a2c4eb901"
down_revision = None          # <— IMPORTANTE: nenhuma revisão anterior
branch_labels = None
depends_on = None


def upgrade():
    # baseline vazia
    pass


def downgrade():
    # baseline vazia
    pass
