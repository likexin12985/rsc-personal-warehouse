"""Public case timeline from the complete scoped historical proof.

Never return stored commands, idempotency aliases, source snapshots, or file URLs.
This read neither acquires new-write authority nor checks present stock availability.
"""
from app.return_condition_read_schemas import ConditionCaseHistory
from . import return_condition_history_read as history
from . import return_condition_history as graph
from .return_condition_business_events import payload
from .historical_original import _bound
from app.formal_services.work_order_query import _aware
from app.inventory_models import FormalMaterial, InventorySerial


def _labels(db, basis):
    material = db.get(FormalMaterial, basis.source.material_id, populate_existing=True)
    if material is None:
        graph.invalid()
    serials = []
    for identifier in sorted(basis.affected.serial_ids, key=str):
        row = db.get(InventorySerial, identifier, populate_existing=True)
        if row is None or row.material_id != material.id or row.lot_id != basis.source.lot_id:
            graph.invalid()
        serials.append(dict(serial_id=identifier, serial_no=row.serial_no, qr_code=row.qr_code))
    return dict(sku_code=material.sku_code, material_name=material.name, base_unit=material.base_unit, serials=serials)


def read(db, *, actor, inbound_line_id):
    with db.no_autoflush:
        # Admit before reading graph data, including on an empty source.
        current, _, _ = history._scope(db, actor, inbound_line_id)
        bound = _bound(db)
        verified = history.read(db, actor=current, inbound_line_id=inbound_line_id)
        groups, fingerprint = graph.capture(db, inbound_line_id)
        if fingerprint != verified.graph.fingerprint or _bound(db) != bound:
            graph.changed()
        try:
            basis = verified.basis; projection = verified.graph.projection
            labels = _labels(db, basis)
            cases = {row['id']: row for row in groups['stock_condition_cases']}
            events = sorted(groups['stock_condition_events'], key=lambda row: row['event_sequence'])
            if (len(cases) != len(groups['stock_condition_cases'])
                    or set(cases) != {case.case_id for case in projection.cases}
                    or tuple(event['id'] for event in events) != verified.graph.event_ids):
                graph.invalid()
            latest = {event['case_id']: event for event in events}
            answer = ConditionCaseHistory.model_validate(dict(
                schema_version='condition_history/1', result_scope='verified_condition_history',
                inbound_line_id=basis.inbound_line_id, root_disposition_id=basis.root_disposition_id,
                material_id=basis.source.material_id, source_account_id=basis.source.id,
                **labels,
                recorded_condition=basis.source.condition, required_condition='damaged',
                historical_damaged_quantity=basis.affected.quantity,
                held_quantity=projection.held_quantity, corrected_quantity=projection.corrected_quantity,
                unclaimed_quantity=projection.unclaimed_quantity,
                cases=[dict(case_id=case.case_id, status=case.status, quantity=case.claim.selected.quantity,
                    serial_ids=list(case.claim.selected.serial_ids), latest_event_id=latest[case.case_id]['id'],
                    latest_event_hash=latest[case.case_id]['request_hash']) for case in projection.cases],
                events=[dict(sequence=event['event_sequence'], occurred_at=_aware(event['created_at']),
                    previous_event_id=event['previous_event_id'],
                    evidence_file_ids=sorted((row['file_id'] for row in groups['stock_condition_files']
                        if row['event_id'] == event['id']), key=str),
                    fact=payload(cases[event['case_id']], event)) for event in events],
                observed_ledger_cursor=verified.observed_ledger_cursor, history_fingerprint=fingerprint,
                current_stock_verified=False, write_authorization_provided=False,
                retry_allowed=False, stock_effect='none'))
        except (KeyError, ValueError, TypeError, AttributeError):
            graph.invalid()
        final, _, _ = history._scope(db, current, inbound_line_id)
        if final != current or _bound(db) != bound or _labels(db, basis) != labels:
            graph.changed()
        return answer


def receipt_sources(db, *, actor, receipt_id):
    """Discover exact legacy inbound lines from the receiver's verified receipt.

    Receipt existence, posting, and classification exceptions are independent.
    A posted receipt with no classification exception returns an empty list.
    """
    from app.return_condition_read_schemas import ConditionReceiptHistory
    from app.stock_return_receipt_schemas import LossReturnReceiptOut
    from app.stock_operation_models import StockLossDisposition
    from app.formal_services.stock_return_inbound_plan import authorize_receipt
    from app.formal_services.stock_return_inbound_queries import read_return_inbound_state
    from app.formal_services.stock_return_receipt_facts import receipt_result
    from app.formal_services.stock_loss_corrections import return_history
    from app.formal_services.work_order_return_sources import _fail

    with db.no_autoflush:
        current, receipt = authorize_receipt(db, actor=actor, receipt_id=receipt_id, action='read')
        before = _bound(db)
        proven = receipt_result(db, actor=current, fact=receipt)
        if not isinstance(proven, LossReturnReceiptOut):
            _fail('return_condition_receipt_not_loss', '请选择报损退回的准确验收记录', 412)
        root = db.get(StockLossDisposition, proven.origin.disposition_id, populate_existing=True)
        if root is None:
            graph.invalid()
        state = read_return_inbound_state(db, actor=current, receipt_id=receipt_id)
        _, fingerprint, _, issues = return_history._verified_graph(db, root)
        inbound_id = state['inbound']['inbound_id'] if state['inbound'] else None
        # The state projection may serialize UUIDs; compare canonical strings.
        selected = [issue for issue in issues if str(issue.inbound_id) == str(inbound_id)] if inbound_id else []
        if len(selected) > 100:
            graph.invalid()
        items = [read(db, actor=current, inbound_line_id=issue.inbound_line_id) for issue in selected]
        final, final_receipt = authorize_receipt(db, actor=current, receipt_id=receipt_id, action='read')
        fresh = read_return_inbound_state(db, actor=final, receipt_id=receipt_id)
        if (final != current or final_receipt.id != receipt_id or _bound(db) != before
                or (fresh['status'], fresh['inbound']) != (state['status'], state['inbound'])
                or return_history._verified_graph(db, root)[1] != fingerprint):
            graph.changed()
        return ConditionReceiptHistory(schema_version='condition_receipt_history/1', receipt_id=receipt_id,
            shipment_id=receipt.shipment_id, root_disposition_id=root.id, operator_person_id=current.person_id,
            authorization_version=current.authorization_version, status=state['status'], inbound_id=inbound_id, items=items)


def _inbox_scope(db, actor):
    from sqlalchemy import select
    from app.foundation_models import Organization
    from app.formal_services import inventory_posting as posting, inventory_query as inventory, stock_loss_sources as sources
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    inventory._require_inventory_read(db, current)
    if not set(current.role_codes) & {'admin', 'provincial_manager'} or not current.allows(db, 'stock_operation', 'read'):
        sources._fail('condition_inbox_forbidden', '没有成色纠正案件查询权限', 403)
    organizations = tuple(db.scalars(select(Organization.id).where(Organization.status == 'active').order_by(Organization.id).limit(1001)))
    if len(organizations) > 1000:
        sources._fail('condition_inbox_scope_limit', '组织范围超过完整核验上限', 503)
    allowed = tuple(identifier for identifier in organizations if all(current.allows(db, resource, 'read',
        target_scope_type='organization', target_scope_id=str(identifier)) for resource in ('inventory', 'stock_operation')))
    return current, allowed


def _inbox_ids(db, organizations, *, view, limit, after_id):
    from sqlalchemy import select, func
    from app.inventory_models import StockAccount
    from app.stock_operation_models import StockOperationReturnInboundLine
    cases = graph.tables()['stock_condition_cases']; events = graph.tables()['stock_condition_events']
    query = select(cases.c.inbound_line_id).join(StockOperationReturnInboundLine,
        StockOperationReturnInboundLine.id == cases.c.inbound_line_id).join(StockAccount,
        StockAccount.id == StockOperationReturnInboundLine.target_account_id).where(StockAccount.owner_org_id.in_(organizations))
    if view == 'pending':
        latest = select(events.c.case_id, func.max(events.c.event_sequence).label('last_sequence')).group_by(events.c.case_id).subquery()
        query = query.join(latest, latest.c.case_id == cases.c.id).join(events,
            (events.c.case_id == cases.c.id) & (events.c.event_sequence == latest.c.last_sequence)).where(
                events.c.to_state.not_in(('executed', 'released_rejected', 'released_cancelled')))
    if after_id is not None:
        query = query.where(cases.c.inbound_line_id > after_id)
    return tuple(db.scalars(query.distinct().order_by(cases.c.inbound_line_id).limit(limit + 1)))


def inbox(db, *, actor, view='pending', limit=5, after_id=None):
    from uuid import UUID
    from app.return_condition_read_schemas import ConditionInbox
    if view not in ('pending', 'all') or type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('invalid inbox selection')
    if after_id is not None and (type(after_id) is not UUID or not after_id.int):
        raise ValueError('exact nonzero continuation required')
    with db.no_autoflush:
        current, organizations = _inbox_scope(db, actor); before = _bound(db)
        selection = dict(view=view, limit=limit, after_id=after_id)
        identifiers = _inbox_ids(db, organizations, **selection)
        items = [read(db, actor=current, inbound_line_id=identifier) for identifier in identifiers[:limit]]
        fresh_identifiers = _inbox_ids(db, organizations, **selection)
        latest, fresh_organizations = _inbox_scope(db, current)
        if (latest != current or fresh_organizations != organizations or _bound(db) != before
                or fresh_identifiers != identifiers):
            graph.changed()
        return ConditionInbox(person_id=current.person_id, authorization_version=current.authorization_version,
            view=view, items=items, next_after_id=items[-1].inbound_line_id if len(identifiers) > limit else None)
