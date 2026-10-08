"""Reconcile prior condition movements before a new, independently keyed claim.

Only complete retained condition history can explain outgoing fungible stock.
A returned balance alone is never attribution. SN must be at the original
account after its original inbound or an exactly proved condition release.
This is preparation evidence; the writer still locks, repeats and posts once.
"""
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select
from app.inventory_models import InventoryMovement, InventoryTransaction
from app.formal_services import inventory_query as inventory
from app.formal_services.serial_ledger import SerialLedgerError, rebuild_serial_states
from . import return_condition_history as graph, return_condition_history_read as history
from . import return_condition_source as source


def reconcile(db, *, actor, inbound_line_id, document):
    if document['source_status'] == 'recorded_stock_retained':
        return document
    proved = history.read(db, actor=actor, inbound_line_id=inbound_line_id)
    basis = proved.basis
    if (proved.observed_ledger_cursor != document['observed_ledger_cursor']
            or str(basis.source.id) != document['source_account_id']
            or str(basis.original_movement_id) != document['original_movement_id']
            or basis.original_ledger_cursor != document['original_ledger_cursor']):
        graph.changed()
    projection = proved.graph.projection
    if not projection.cases:
        return document
    policies = document['policy_fingerprint']
    if (len(policies) != 1 or tuple(policies[0][2:5]) !=
            (basis.affected.tracking_mode, basis.affected.quantity_scale, basis.affected.allow_fraction)):
        source._fail('tracking_changed', '当前追踪策略与已核验的纠正历史不同', 409)
    allowed_serials = set()
    claimable = projection.unclaimed_quantity
    tracked = basis.affected.tracking_mode in ('serial', 'lot_and_serial')
    if not tracked:
        outgoing = tuple(db.execute(select(InventoryMovement.id, InventoryMovement.quantity)
            .join(InventoryTransaction, InventoryTransaction.id == InventoryMovement.transaction_id)
            .where(InventoryMovement.from_account_id == basis.source.id,
                InventoryTransaction.status == 'posted',
                InventoryTransaction.ledger_cursor > basis.original_ledger_cursor,
                InventoryTransaction.ledger_cursor <= proved.observed_ledger_cursor)).all())
        # Even an unrelated withdrawal followed by equal replenishment blocks
        # attribution. Compare every actual outgoing edge to the verified graph.
        expected = {state.claim.posting.movement_id: state.claim.selected.quantity
            for state in projection.cases}
        if (len(outgoing) != len(expected) or dict(outgoing) != expected
                or len(outgoing) != document['later_outgoing_count']
                or sum((row.quantity for row in outgoing), Decimal(0)) != Decimal(document['later_outgoing_quantity'])):
            return document
    else:
        event_table = graph.tables()['stock_condition_events']
        released = {state.case_id: state for state in projection.cases
            if state.status in ('released_rejected', 'released_cancelled')}
        rows = tuple(db.execute(select(event_table).where(event_table.c.case_id.in_(tuple(released)),
            event_table.c.kind == 'release')).mappings())
        if (len(rows) != len(released) or {row['case_id'] for row in rows} != set(released)
                or any(row['id'] not in proved.graph.event_ids for row in rows)):
            graph.invalid()
        releases = {row['posting_movement_id']: set(released[row['case_id']].claim.selected.serial_ids)
            for row in rows}
        try:
            states = rebuild_serial_states(db, basis.affected.serial_ids,
                through_cursor=proved.observed_ledger_cursor)
        except SerialLedgerError:
            inventory._invalid_current_projection()
        for identifier in projection.unclaimed_serial_ids:
            current = states.get(identifier)
            if (current is not None and current.stock_account_id == basis.source.id
                    and current.lifecycle_status == 'active'
                    and (current.last_movement_id == basis.original_movement_id
                        or identifier in releases.get(current.last_movement_id, set()))):
                allowed_serials.add(identifier)
        claimable = Decimal(len(allowed_serials))
        if claimable > projection.unclaimed_quantity:
            graph.invalid()
    if Decimal(document['account_balance_quantity']) < claimable:
        inventory._invalid_current_projection()
    return {**document, 'source_status': 'verified_condition_history',
        'claimable_quantity': format(claimable, '.3f'),
        'condition_history_fingerprint': proved.graph.fingerprint,
        'condition_held_quantity': format(projection.held_quantity, '.3f'),
        'condition_corrected_quantity': format(projection.corrected_quantity, '.3f'),
        'serials': [{**row, 'claimable_for_correction': UUID(row['serial_id']) in allowed_serials}
            for row in document['serials']]}
