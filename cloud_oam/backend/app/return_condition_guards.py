"""Unpublished condition-correction invariant layer, never installed at startup.

This component needs the candidate relational schema. It does not establish
authority, complete historical ledger/audit proof or request admission.
"""
from pathlib import Path

from app.return_condition_schema import TABLE_NAMES


def statements(*, posting=False, identity=False):
    posting = posting or identity
    source = Path(__file__).resolve().parents[1] / 'alembic/return_condition_candidate/invariants.sql'
    result = [source.read_text()]
    for signature in ('rsc_condition_reject_mutation()', 'rsc_condition_lock_source()',
                      'rsc_condition_check_source(uuid)', 'rsc_condition_validate_insert()'):
        result.append('REVOKE ALL ON FUNCTION public.' + signature +
                      ' FROM PUBLIC, star_oam_api, star_oam_projector, star_oam_edge, edge_inbox, star_oam_backup')
    for table in TABLE_NAMES:
        result.extend((
            f'CREATE TRIGGER condition_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.{table} '
            'FOR EACH STATEMENT EXECUTE FUNCTION public.rsc_condition_reject_mutation()',
            f'CREATE TRIGGER condition_source_lock BEFORE INSERT ON public.{table} '
            'FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_lock_source()',
            f'CREATE CONSTRAINT TRIGGER condition_source_complete AFTER INSERT ON public.{table} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_validate_insert()',
        ))
    if posting:
        result.append(source.with_name('posting.sql').read_text())
        for signature in ('rsc_condition_check_posting(uuid)', 'rsc_condition_posting_fence()'):
            result.append('REVOKE ALL ON FUNCTION public.' + signature +
                ' FROM PUBLIC, star_oam_api, star_oam_projector, star_oam_edge, edge_inbox, star_oam_backup')
        for table in ('stock_condition_cases','stock_condition_events','stock_condition_serials',
                      'stock_accounts','inventory_serials','inventory_transactions','inventory_movements','inventory_movement_serials'):
            operations = 'INSERT' if table.startswith('stock_condition_') else 'INSERT OR UPDATE OR DELETE'
            result.append(f'CREATE CONSTRAINT TRIGGER condition_posting_complete AFTER {operations} ON public.{table} '
                'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_posting_fence()')
    if identity:
        result.append(source.with_name('identity.sql').read_text())
        for signature in ('rsc_condition_check_identity(uuid)', 'rsc_condition_identity_fence()'):
            result.append('REVOKE ALL ON FUNCTION public.' + signature +
                ' FROM PUBLIC, star_oam_api, star_oam_projector, star_oam_edge, edge_inbox, star_oam_backup')
        for table in (*TABLE_NAMES,'stock_operation_orders','stock_operation_lines','stock_operation_serials','inventory_transactions'):
            operations = 'INSERT' if table.startswith('stock_condition_') else 'INSERT OR UPDATE OR DELETE'
            result.append(f'CREATE CONSTRAINT TRIGGER condition_identity_complete AFTER {operations} ON public.{table} '
                'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_identity_fence()')
    return result
