"""partners by code (partner_invites + tenant_partners)

Revision ID: 43c3e6eadd59
Revises: 83622fc75aec
Create Date: 2025-09-23 16:00:11.039865

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '43c3e6eadd59'
down_revision = '83622fc75aec'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)


    # ---------- partner_invites ----------
    if 'partner_invites' not in insp.get_table_names():
        op.create_table(
            'partner_invites',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('code', sa.String(length=40), nullable=False, unique=True),
            sa.Column('inviter_tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
            sa.Column('invitee_tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
            sa.Column('note', sa.String(length=120)),
            sa.Column('expires_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        )
        # índices úteis (opcionais, mas bons p/ consulta)
        op.create_index('ix_partner_invites_code', 'partner_invites', ['code'], unique=True)
        op.create_index('ix_partner_invites_inviter', 'partner_invites', ['inviter_tenant_id'])
        op.create_index('ix_partner_invites_invitee', 'partner_invites', ['invitee_tenant_id'])
        op.create_index('ix_partner_invites_status', 'partner_invites', ['status'])

    # ---------- tenant_partners ----------
    if 'tenant_partners' not in insp.get_table_names():
        op.create_table(
            'tenant_partners',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
            sa.Column('partner_tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
            sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.UniqueConstraint('tenant_id', 'partner_tenant_id', name='uq_tenant_partner_unique'),
        )
        op.create_index('ix_tenant_partners_tenant', 'tenant_partners', ['tenant_id'])
        op.create_index('ix_tenant_partners_partner', 'tenant_partners', ['partner_tenant_id'])

def downgrade():
    # drop tables/indices em ordem segura
    try:
        op.drop_index('ix_tenant_partners_partner', table_name='tenant_partners')
        op.drop_index('ix_tenant_partners_tenant', table_name='tenant_partners')
        op.drop_constraint('uq_tenant_partner_unique', 'tenant_partners', type_='unique')
        op.drop_table('tenant_partners')
    except Exception:
        pass

    try:
        op.drop_index('ix_partner_invites_status', table_name='partner_invites')
        op.drop_index('ix_partner_invites_invitee', table_name='partner_invites')
        op.drop_index('ix_partner_invites_inviter', table_name='partner_invites')
        op.drop_index('ix_partner_invites_code', table_name='partner_invites')
        op.drop_table('partner_invites')
    except Exception:
        pass
