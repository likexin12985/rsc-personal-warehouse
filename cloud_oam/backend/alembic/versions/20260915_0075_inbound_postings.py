"""Bind a posted inventory transaction to a personal inbound order immutably."""
from alembic import op
import sqlalchemy as sa
revision="20260915_0075"; down_revision="20260914_0074"; branch_labels=depends_on=None
def upgrade():
    op.create_table("inbound_postings", sa.Column("id",sa.Uuid(),primary_key=True), sa.Column("inbound_order_id",sa.Uuid(),sa.ForeignKey("inbound_orders.id",ondelete="RESTRICT"),nullable=False), sa.Column("inventory_transaction_id",sa.Uuid(),sa.ForeignKey("inventory_transactions.id",ondelete="RESTRICT"),nullable=False), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.UniqueConstraint("inbound_order_id",name="uq_inbound_postings_order"), sa.UniqueConstraint("inventory_transaction_id",name="uq_inbound_postings_transaction"))
def downgrade():
    bind=op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM inbound_postings)")).scalar(): raise RuntimeError("cannot downgrade 0075 while inbound postings exist")
    op.drop_table("inbound_postings")
