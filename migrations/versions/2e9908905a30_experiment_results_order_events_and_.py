"""experiment results, order events and ledger

Revision ID: 2e9908905a30
Revises: 90cbb9b3b6ed
Create Date: 2026-11-12 19:24:23.418526
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '2e9908905a30'
down_revision: Union[str, None] = '90cbb9b3b6ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('order_events',
    sa.Column('order_id', sa.UUID(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('from_status', sa.String(length=24), nullable=True),
    sa.Column('to_status', sa.String(length=24), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('order_id', 'sequence', name='uq_order_events_order_sequence')
    )
    op.create_table('ledger_transactions',
    sa.Column('portfolio_id', sa.UUID(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('trade_id', sa.UUID(), nullable=True),
    sa.Column('currency_code', sa.String(length=3), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('portfolio_id', 'sequence', name='uq_ledger_tx_portfolio_sequence')
    )
    op.create_table('ledger_entries',
    sa.Column('transaction_id', sa.UUID(), nullable=False),
    sa.Column('portfolio_id', sa.UUID(), nullable=False),
    sa.Column('line_number', sa.Integer(), nullable=False),
    sa.Column('account', sa.String(length=32), nullable=False),
    sa.Column('instrument_id', sa.UUID(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint('amount <> 0', name='ck_ledger_entry_nonzero_amount'),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['transaction_id'], ['ledger_transactions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ledger_entries_portfolio_account', 'ledger_entries', ['portfolio_id', 'account'], unique=False)
    op.create_index('ix_ledger_entries_transaction', 'ledger_entries', ['transaction_id'], unique=False)
    op.add_column('experiments', sa.Column('results', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('experiments', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('experiments', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('rejection_reason', sa.Text(), nullable=True))
    # Autogenerate does not detect CHECK constraints added to existing tables.
    op.create_check_constraint(
        'ck_order_filled_within_quantity',
        'orders',
        'filled_quantity >= 0 AND filled_quantity <= quantity',
    )
    op.drop_constraint(op.f('orders_idempotency_key_key'), 'orders', type_='unique')
    op.create_unique_constraint('uq_orders_portfolio_idempotency', 'orders', ['portfolio_id', 'idempotency_key'])
    op.create_unique_constraint('uq_strategy_versions_strategy_version', 'strategy_versions', ['strategy_id', 'version'])


def downgrade() -> None:
    op.drop_constraint('uq_strategy_versions_strategy_version', 'strategy_versions', type_='unique')
    op.drop_constraint('uq_orders_portfolio_idempotency', 'orders', type_='unique')
    op.create_unique_constraint(op.f('orders_idempotency_key_key'), 'orders', ['idempotency_key'], postgresql_nulls_not_distinct=False)
    op.drop_constraint('ck_order_filled_within_quantity', 'orders', type_='check')
    op.drop_column('orders', 'rejection_reason')
    op.drop_column('experiments', 'completed_at')
    op.drop_column('experiments', 'started_at')
    op.drop_column('experiments', 'results')
    op.drop_index('ix_ledger_entries_transaction', table_name='ledger_entries')
    op.drop_index('ix_ledger_entries_portfolio_account', table_name='ledger_entries')
    op.drop_table('ledger_entries')
    op.drop_table('ledger_transactions')
    op.drop_table('order_events')
