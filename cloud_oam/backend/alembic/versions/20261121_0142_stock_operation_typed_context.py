"""Separate loss context from returns without opening unproved loss writes.

This structural prerequisite retains every existing return proof. A later
business migration must bind loss admission to its own atomic stock facts.
No application models are imported by this immutable schema transition.
"""
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa

revision = '20261121_0142'
down_revision = '20261120_0141'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
OLD_READY_HASH = '41b7fbc32d84f55d1802b33fd4096362393038a24a8d550c617c1ff8d52f5f3d'
NEW_READY_HASH = '894d246b037847695e5c753735daab9977e59a3a4e79046f891681eb5ce38c8b'

CONTEXT_COLUMNS = ('oam_work_order_id', 'target_location_id', 'transit_location_id', 'target_custody_assignment_id')
ORDER_TYPE = "operation_type IN ('return','loss_report') AND status = 'submitted'"
ORDER_CONTEXT = ' '.join("""(
    operation_type='return' AND oam_work_order_id IS NOT NULL
    AND target_location_id IS NOT NULL AND transit_location_id IS NOT NULL
    AND target_custody_assignment_id IS NOT NULL
    AND source_location_id <> target_location_id
    AND source_location_id <> transit_location_id AND target_location_id <> transit_location_id
) OR (
    operation_type='loss_report' AND oam_work_order_id IS NULL
    AND target_location_id IS NULL AND transit_location_id IS NULL
    AND target_custody_assignment_id IS NULL
)""".split())
LINE_DIMENSIONS = ' '.join("""stock_account_id <> reserved_account_id AND (
    (operation_type='return' AND source_recovery_line_id IS NOT NULL
        AND target_condition IN ('used','damaged'))
    OR (operation_type='loss_report' AND source_recovery_line_id IS NULL
        AND target_condition IN ('new','used','damaged'))
)""".split())
LEGACY_ORDER_TYPE = "operation_type = 'return' AND status = 'submitted'"
LEGACY_ORDER_CONTEXT = 'source_location_id <> target_location_id AND source_location_id <> transit_location_id AND target_location_id <> transit_location_id'
LEGACY_LINE_DIMENSIONS = "stock_account_id <> reserved_account_id AND target_condition IN ('used','damaged')"
FUNCTION = 'rsc_guard_stock_loss_admission_0142'
BODY = """
BEGIN
    IF NEW.operation_type = 'loss_report' THEN
        RAISE EXCEPTION '0142 loss submission requires the complete loss posting boundary' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
"""
TRIGGERS = {f'trg_{table}_loss_admission_0142': table for table in ('stock_operation_orders', 'stock_operation_lines')}


def _schema(up):
    db = op.get_bind()
    # SQLite's copy/recreate must neither cascade child rows nor lose local
    # append-only triggers. Its normal test migration connection has FK off.
    saved_triggers = ()
    if db.dialect.name == 'sqlite':
        if db.exec_driver_sql('PRAGMA foreign_keys').scalar():
            raise RuntimeError('0142 SQLite migration requires foreign_keys OFF before the migration transaction')
        saved_triggers = tuple(db.execute(sa.text(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND (tbl_name IN "
            "('stock_operation_orders','stock_operation_lines') OR instr(lower(sql),'stock_operation_orders')>0 "
            "OR instr(lower(sql),'stock_operation_lines')>0) ORDER BY name")))
        # SQLite validates triggers on OTHER tables during a rename too.
        # Preserve dependent request-seal triggers while both tables rebuild.
        for name, _ in saved_triggers:
            db.exec_driver_sql('DROP TRIGGER ' + db.dialect.identifier_preparer.quote(name))
    if not up:
        with op.batch_alter_table('stock_operation_lines') as batch:
            batch.drop_constraint('fk_stock_operation_lines_typed_parent', type_='foreignkey')
            batch.drop_constraint('ck_stock_operation_lines_dimensions', type_='check')
            batch.create_check_constraint('ck_stock_operation_lines_dimensions', LEGACY_LINE_DIMENSIONS)
            batch.alter_column('source_recovery_line_id', existing_type=sa.Uuid(), nullable=False)
            batch.drop_column('operation_type')
    with op.batch_alter_table('stock_operation_orders') as batch:
        batch.drop_constraint('ck_stock_operation_orders_type_status', type_='check')
        batch.drop_constraint('ck_stock_operation_orders_locations', type_='check')
        batch.create_check_constraint('ck_stock_operation_orders_type_status', ORDER_TYPE if up else LEGACY_ORDER_TYPE)
        batch.create_check_constraint('ck_stock_operation_orders_locations', ORDER_CONTEXT if up else LEGACY_ORDER_CONTEXT)
        for name in CONTEXT_COLUMNS:
            batch.alter_column(name, existing_type=sa.Uuid(), nullable=up)
        if up:
            batch.create_unique_constraint('uq_stock_operation_orders_typed_id', ['id', 'operation_type'])
        else:
            batch.drop_constraint('uq_stock_operation_orders_typed_id', type_='unique')
    if up:
        with op.batch_alter_table('stock_operation_lines') as batch:
            batch.drop_constraint('ck_stock_operation_lines_dimensions', type_='check')
            batch.alter_column('source_recovery_line_id', existing_type=sa.Uuid(), nullable=True)
            batch.add_column(sa.Column('operation_type', sa.String(24), nullable=False, server_default=sa.text("'return'")))
            batch.create_check_constraint('ck_stock_operation_lines_dimensions', LINE_DIMENSIONS)
            batch.create_foreign_key('fk_stock_operation_lines_typed_parent', 'stock_operation_orders',
                ['operation_id', 'operation_type'], ['id', 'operation_type'], ondelete='RESTRICT')
    for _, statement in saved_triggers:
        db.exec_driver_sql(statement)


def _admission(up):
    if op.get_bind().dialect.name == 'postgresql':
        if up:
            op.execute(f'CREATE FUNCTION public.{FUNCTION}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${BODY}$body$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{FUNCTION}() FROM PUBLIC,star_oam_api')
            for name, table in TRIGGERS.items():
                op.execute(f'CREATE TRIGGER {name} BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION}()')
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
        else:
            for name, table in TRIGGERS.items(): op.execute(f'DROP TRIGGER {name} ON public.{table}')
            op.execute(f'DROP FUNCTION public.{FUNCTION}()')
    else:
        for name, table in TRIGGERS.items():
            if up:
                op.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN NEW.operation_type='loss_report' BEGIN SELECT RAISE(ABORT,'0142 loss submission requires the complete loss posting boundary'); END")
            else: op.execute(f'DROP TRIGGER {name}')


def _transition(up):
    db = op.get_bind()
    if db.dialect.name not in ('postgresql', 'sqlite'): raise RuntimeError('0142 PostgreSQL or SQLite required')
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if db.dialect.name == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0142 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.stock_operation_orders,public.stock_operation_lines IN ACCESS EXCLUSIVE MODE')
    if not up:
        helper['_preflight']("EXISTS(SELECT 1 FROM stock_operation_orders WHERE operation_type<>'return') OR EXISTS(SELECT 1 FROM stock_operation_lines WHERE operation_type<>'return')",
            '0142 typed operation history must be retained')
        _admission(False)
    _schema(up)
    if up: _admission(True)
    if db.dialect.name == 'postgresql':
        replace = runpy.run_path(str(FOLDER / '20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
            expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
            replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='typed_stock_operation_readiness_0142')


def upgrade(): _transition(True)
def downgrade(): _transition(False)
