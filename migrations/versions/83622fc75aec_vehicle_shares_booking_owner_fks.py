"""vehicle_shares + booking/owner FKs

Revision ID: 83622fc75aec
Revises: 9c61fcd29578
Create Date: 2025-09-23 15:41:37.084425

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '83622fc75aec'
down_revision = '9c61fcd29578'
branch_labels = None
depends_on = None



def upgrade():
    # 1) Tabela vehicle_shares
    op.create_table(
        'vehicle_shares',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('shared_with_tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_unique_constraint('uq_vehicle_share_unique', 'vehicle_shares', ['vehicle_id', 'shared_with_tenant_id'])

    # 2) Colunas em reservations (se ainda não existirem)
    conn = op.get_bind()
    insp = sa.inspect(conn)

    cols = [c['name'] for c in insp.get_columns('reservations')]

    if 'owner_tenant_id' not in cols:
        op.add_column('reservations', sa.Column('owner_tenant_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            'fk_reservations_owner_tenant', 'reservations', 'tenants',
            ['owner_tenant_id'], ['id'], ondelete='SET NULL'
        )
        op.create_index('ix_reservations_owner_tenant_id', 'reservations', ['owner_tenant_id'])

    if 'booking_tenant_id' not in cols:
        op.add_column('reservations', sa.Column('booking_tenant_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            'fk_reservations_booking_tenant', 'reservations', 'tenants',
            ['booking_tenant_id'], ['id'], ondelete='SET NULL'
        )
        op.create_index('ix_reservations_booking_tenant_id', 'reservations', ['booking_tenant_id'])


def downgrade():
    # remover FKs/índices de reservations (se existirem)
    with op.batch_alter_table('reservations') as batch:
        try:
            batch.drop_constraint('fk_reservations_booking_tenant', type_='foreignkey')
        except Exception:
            pass
        try:
            batch.drop_index('ix_reservations_booking_tenant_id')
        except Exception:
            pass
        try:
            batch.drop_column('booking_tenant_id')
        except Exception:
            pass

        try:
            batch.drop_constraint('fk_reservations_owner_tenant', type_='foreignkey')
        except Exception:
            pass
        try:
            batch.drop_index('ix_reservations_owner_tenant_id')
        except Exception:
            pass
        try:
            batch.drop_column('owner_tenant_id')
        except Exception:
            pass

    # remover vehicle_shares
    try:
        op.drop_constraint('uq_vehicle_share_unique', 'vehicle_shares', type_='unique')
    except Exception:
        pass
    op.drop_table('vehicle_shares')