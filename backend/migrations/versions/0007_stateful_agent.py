"""Add operational memory, effect ledger and channel inbox/outbox."""
import sqlalchemy as sa
from alembic import op

revision = '0007_stateful_agent'
down_revision = '0006_telnyx_call_platform'
branch_labels = None
depends_on = None


def conversation_fk():
    return sa.ForeignKeyConstraint(['conversation_id', 'organization_id'], ['conversations.id', 'conversations.organization_id'], ondelete='CASCADE')


def upgrade():
    op.create_table('agent_snapshots',
        sa.Column('conversation_id', sa.Uuid(), primary_key=True),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False), conversation_fk())
    op.create_table('agent_operations',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('conversation_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('tool', sa.String(80), nullable=False),
        sa.Column('arguments', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), conversation_fk())
    op.create_table('channel_bindings',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('conversation_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('phone_number_id', sa.String(80), nullable=False),
        sa.Column('wa_id', sa.String(32), nullable=False),
        sa.Column('phone', sa.String(32), nullable=False),
        sa.Column('opt_in', sa.Boolean(), nullable=False),
        sa.Column('template_name', sa.String(120), nullable=True),
        sa.Column('template_language', sa.String(16), nullable=False),
        sa.Column('last_inbound_at', sa.DateTime(timezone=True), nullable=True),
        conversation_fk(),
        sa.UniqueConstraint('phone_number_id', 'wa_id', name='uq_channel_identity'),
        sa.UniqueConstraint('organization_id', 'phone', name='uq_channel_phone'),
        sa.UniqueConstraint('conversation_id', name='uq_channel_conversation'))
    op.create_table('channel_events',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('binding_id', sa.Uuid(), sa.ForeignKey('channel_bindings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_channel_events_external_id', 'channel_events', ['external_id'])
    op.create_index('ix_channel_pending', 'channel_events', ['status', 'created_at'])


def downgrade():
    for name in ('channel_events', 'channel_bindings', 'agent_operations', 'agent_snapshots'):
        op.drop_table(name)
