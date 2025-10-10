"""vehicle share + booking/owner in reservations

Revision ID: 9c61fcd29578
Revises: f8cb5afe8623
Create Date: 2025-09-23 11:59:51.940146

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c61fcd29578'
down_revision = 'f8cb5afe8623'
branch_labels = None
depends_on = None


def upgrade():
    # --- 1) Criar tabela vehicle_shares ---
    op.create_table(
        'vehicle_shares',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('shared_with_tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('TRUE')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    # Unique: um mesmo veículo não pode ser compartilhado duas vezes com o mesmo tenant
    op.create_unique_constraint(
        'uq_vehicle_share_unique',
        'vehicle_shares',
        ['vehicle_id', 'shared_with_tenant_id']
    )
    # Índices úteis
    op.create_index('ix_vehicle_shares_vehicle_id', 'vehicle_shares', ['vehicle_id'])
    op.create_index('ix_vehicle_shares_shared_with_tenant_id', 'vehicle_shares', ['shared_with_tenant_id'])
    op.create_index('ix_vehicle_shares_active', 'vehicle_shares', ['active'])

    # --- 2) Adicionar colunas na reservations ---
    op.add_column('reservations', sa.Column('booking_tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True))
    op.add_column('reservations', sa.Column('owner_tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True))

    op.create_index('ix_reservations_booking_tenant_id', 'reservations', ['booking_tenant_id'])
    op.create_index('ix_reservations_owner_tenant_id', 'reservations', ['owner_tenant_id'])

    # --- 3) Backfill de dados existentes ---
    # Preenche owner_tenant_id com o tenant dono do veículo da reserva.
    # Estratégia SQL “agnóstica” o suficiente para SQLite/Postgres.
    conn = op.get_bind()

    # owner_tenant_id = vehicles.tenant_id via subquery
    # Nota: alguns SQLite antigos não gostam de UPDATE com JOIN; o subselect resolve.
    conn.execute(sa.text("""
        UPDATE reservations
        SET owner_tenant_id = (
            SELECT v.tenant_id
            FROM vehicles v
            WHERE v.id = reservations.vehicle_id
        )
        WHERE owner_tenant_id IS NULL
          AND vehicle_id IS NOT NULL
    """))

    # booking_tenant_id = owner_tenant_id (histórico coerente)
    conn.execute(sa.text("""
        UPDATE reservations
        SET booking_tenant_id = owner_tenant_id
        WHERE booking_tenant_id IS NULL
    """))

    # Opcional: se quiser deixar NOT NULL depois, só quando tiver certeza.
    # op.alter_column('reservations', 'owner_tenant_id', existing_type=sa.Integer(), nullable=False)
    # op.alter_column('reservations', 'booking_tenant_id', existing_type=sa.Integer(), nullable=False)


def downgrade():
    # Reverte índices e colunas de reservations
    op.drop_index('ix_reservations_owner_tenant_id', table_name='reservations')
    op.drop_index('ix_reservations_booking_tenant_id', table_name='reservations')
    op.drop_column('reservations', 'owner_tenant_id')
    op.drop_column('reservations', 'booking_tenant_id')

    # Reverte vehicle_shares
    op.drop_index('ix_vehicle_shares_active', table_name='vehicle_shares')
    op.drop_index('ix_vehicle_shares_shared_with_tenant_id', table_name='vehicle_shares')
    op.drop_index('ix_vehicle_shares_vehicle_id', table_name='vehicle_shares')
    op.drop_constraint('uq_vehicle_share_unique', 'vehicle_shares', type_='unique')
    op.drop_table('vehicle_shares')
