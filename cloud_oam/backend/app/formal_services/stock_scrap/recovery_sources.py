"""Scoped read-only discovery of exact scrap/recovery approval references."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import select
from app.foundation_models import Person
from app.models import User
from app.inventory_models import StockAccount, FormalMaterial, InventorySerial
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.stock_loss_correction_models import StockLossDispositionReversal, StockLossCorrectionExecution
from app.stock_scrap_recovery_schemas import ScrapRecoverySource
from app.stock_scrap_recovery_source_schemas import RecoverySources, RecoveryQueue, BlockedSource
from app.formal_services import stock_loss_review_query as reviews
from app.formal_services.stock_loss_recovery import authorize_lookup
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.stock_loss_corrections.historical_original import _bound
from app.formal_services.stock_loss_corrections.history_chain import verify_chain
from app.formal_services.stock_loss_corrections.history_events import load_event_checked_inventory_history
from app.formal_services.stock_loss_corrections.chain_projection import project, InvalidChain
from app.formal_services.stock_loss_corrections import business_events
from app.formal_services.work_order_query import _aware
from . import recovery_authority as authority, recovery_facts as facts, recovery_events, recovery_history
from .recovery_lookup import _authorize
from .tables import tables


def _scope(db, actor, stage):
    if stage not in authority.ACTIONS:
        authority.fail('source_stage_invalid', '请选择准确找回阶段', 400)
    if stage != 'apply':
        return reviews._scope(db, actor, 'regional' if stage == 'regional' else 'headquarters')
    current = authorize_lookup(db, actor)
    user = db.get(User, current.user_id, populate_existing=True)
    assignments = {g.assignment_id for g in current.assignments if
        (g.role_code, g.scope_type, g.scope_id) == ('technician', 'person', str(current.person_id))}
    if (user is None or not user.is_active or not any(e.assignment_id in assignments and
        (e.role_code, e.scope_type, e.scope_id, e.resource, e.action, e.field_code, e.effect) ==
        ('technician', 'person', str(current.person_id), 'stock_operation', 'read', '', 'allow') for e in current.entitlements)):
        authority.fail('source_read_forbidden', '没有本人找回记录查看权限', 403)
    return current, {}


def _ids(db, actor, owners, stage, *, identifier=None, after=None, limit=1):
    scrap = tables()['stock_scrap_lines']
    stmt = select(scrap.c.id).join(StockLossDisposition, StockLossDisposition.id == scrap.c.root_disposition_id).join(
        StockOperationOrder, StockOperationOrder.id == StockLossDisposition.operation_id).join(
        StockAccount, StockAccount.id == scrap.c.frozen_account_id).where(StockOperationOrder.operation_type == 'loss_report')
    if stage == 'apply':
        stmt = stmt.where(StockOperationOrder.requester_id == actor.person_id)
    else:
        stmt = stmt.where(StockAccount.owner_org_id.in_(owners), StockOperationOrder.requester_id != actor.person_id,
            StockOperationOrder.actor_user_id != actor.user_id)
    if identifier is not None: stmt = stmt.where(scrap.c.id == identifier)
    if after is not None: stmt = stmt.where(scrap.c.id > after)
    return tuple(db.scalars(stmt.order_by(scrap.c.id).limit(limit)))


def _source(db, actor, stage, identifier):
    table = tables()['stock_scrap_lines']
    line = db.execute(select(table).where(table.c.id == identifier)).mappings().one()
    if line['source_kind'] == 'original':
        fact = db.get(StockLossDisposition, line['root_disposition_id'], populate_existing=True)
    else:
        facts.need(line['source_kind'] == 'correction')
        fact = db.get(StockLossCorrectionExecution, line['correction_execution_id'], populate_existing=True)
    facts.need(fact is not None)
    reference = ScrapRecoverySource(scrap_line_id=identifier, expected_scrap_request_hash=fact.request_hash)
    source = authority.load_source(db, reference)
    _authorize(db, actor, SimpleNamespace(operator_person_id=actor.person_id), source, stage)
    return source, reference


def _rows(db, identifier):
    result = {}
    for name in (*facts.NAMES.values(), 'stock_scrap_recovery_executions'):
        t = tables()[name]
        result[name] = [dict(r) for r in db.execute(select(t).where(t.c.scrap_line_id == identifier)
            .order_by(t.c.created_at, t.c.id).limit(1001)).mappings()]
    if sum(map(len, result.values())) > 1000:
        authority.fail('source_limit', '找回历史超过完整展示上限，请联系总部核验', 503)
    return result


def _observe(db, actor, stage, identifier):
    source, ref = _source(db, actor, stage, identifier)
    proof = verify_chain(db, root_disposition_id=source['root'].id)
    loaded = load_event_checked_inventory_history(db, root_disposition_id=source['root'].id)
    h = loaded.history; chain = project(h.basis, h.executions, h.reversals, h.decisions)
    facts.need(loaded.observed_ledger_cursor == proof.observed_ledger_cursor)
    current = chain.active_execution_id == source['fact'].id and chain.pending_reversal_id is None
    rows = _rows(db, identifier); applications = rows[facts.NAMES['apply']]
    ids = {a['id'] for a in applications}
    facts.need(all(r['recovery_request_id'] in ids for name in (facts.NAMES['regional'], facts.NAMES['headquarters'],
        'stock_scrap_recovery_executions') for r in rows[name]))
    executions = rows['stock_scrap_recovery_executions']; facts.need(len(executions) <= 1)
    histories = []; eligible = None; prior_at = _aware(source['line']['created_at'])
    for application in applications:
        facts.need(_aware(application['created_at']) > prior_at)
        state, regional, final, prior_at = facts.verify_history(db, application=application, source=source)
        # A second application is allowed only after the earlier one explicitly
        # requested new evidence. Never choose the latest among competing facts.
        facts.need(eligible is None)
        if state != 'needs_evidence': eligible = (application, state, regional, final)
        command = facts.command(application, 'apply', source)
        histories.append(dict(application=recovery_events.payload(application, 'apply'),
            evidence_file_ids=command.evidence_file_ids, status=state,
            regional_reviews=tuple(recovery_events.payload(r, 'regional') for r in rows[facts.NAMES['regional']] if r['recovery_request_id'] == application['id']),
            headquarters_reviews=tuple(recovery_events.payload(r, 'headquarters') for r in rows[facts.NAMES['headquarters']] if r['recovery_request_id'] == application['id'])))
    posting = None
    if executions:
        execution = executions[0]
        facts.need(not current and eligible is not None and eligible[0]['id'] == execution['recovery_request_id'])
        inverse = db.get(StockLossDispositionReversal, execution['reversal_id'], populate_existing=True)
        facts.need(inverse is not None)
        recovery_history.verify_execution(db, inverse=inverse, source=source)
        posting = business_events.payload(inverse, root=source['root'], order=source['order'])
    else:
        facts.need(current)
    state = 'recovered' if posting else eligible[1] if eligible else 'needs_evidence' if histories else 'awaiting_application'
    next_reference = None
    if current and stage == 'apply' and eligible is None:
        next_reference = dict(stage=stage, source=ref)
    elif current and eligible is not None:
        application, app_state, regional, final = eligible
        base = dict(stage=stage, source=ref, recovery_request_id=application['id'], expected_request_hash=application['request_hash'])
        independent = actor.person_id != application['actor_person_id'] and actor.user_id != application['actor_user_id']
        if stage == 'regional' and app_state == 'awaiting_regional' and independent:
            next_reference = base
        elif stage == 'headquarters' and app_state == 'awaiting_headquarters' and independent and regional is not None:
            if actor.person_id != regional['actor_person_id'] and actor.user_id != regional['actor_user_id']:
                next_reference = dict(base, regional_review_id=regional['id'], expected_regional_hash=regional['request_hash'])
        elif stage == 'execute' and app_state == 'approved_pending_execution' and final is not None:
            next_reference = dict(base, headquarters_review_id=final['id'], expected_headquarters_hash=final['request_hash'])
    material = db.get(FormalMaterial, source['account'].material_id, populate_existing=True)
    person = db.get(Person, source['order'].requester_id, populate_existing=True)
    facts.need(material is not None and person is not None)
    serials = tables()['stock_scrap_serials']
    serial_ids = tuple(db.scalars(select(serials.c.serial_id).where(serials.c.scrap_line_id == identifier).order_by(serials.c.serial_id)))
    serial_rows = tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(serial_ids)).order_by(InventorySerial.id).execution_options(populate_existing=True)))
    facts.need(tuple(s.id for s in serial_rows) == serial_ids)
    facts.need(all(s.material_id == material.id and s.serial_no and s.qr_code for s in serial_rows))
    serial_references = tuple(dict(serial_id=s.id, serial_no=s.serial_no, qr_code=s.qr_code) for s in serial_rows)
    return dict(observed_ledger_cursor=proof.observed_ledger_cursor, requested_stage=stage, scrap_reference=ref,
        root_disposition_id=source['root'].id, operation_id=source['order'].id, operation_no=source['order'].operation_no,
        line_id=source['line']['loss_line_id'], owner_org_id=source['account'].owner_org_id,
        requester_person_id=person.id, requester_name=person.name, material_id=material.id,
        sku_code=material.sku_code, material_name=material.name, base_unit=material.base_unit,
        condition_code=source['account'].condition_code, quantity=source['line']['quantity'], serial_ids=serial_ids,
        serials=serial_references, is_current_scrap=current, state=state, next_reference=next_reference, applications=tuple(histories), recovery_posting=posting)


def read(db, *, actor, stage, scrap_line_id):
    if type(scrap_line_id) is not UUID or scrap_line_id.int == 0:
        authority.fail('source_identifier_invalid', '请选择准确报废记录', 400)
    with db.no_autoflush:
        current, owners = _scope(db, actor, stage); before = _bound(db)
        if not _ids(db, current, owners, stage, identifier=scrap_line_id):
            authority.fail('source_not_found', '当前范围内没有此报废记录', 404)
        try:
            first = _observe(db, current, stage, scrap_line_id)
            latest, latest_owners = _scope(db, current, stage)
            second = _observe(db, latest, stage, scrap_line_id)
            final, final_owners = _scope(db, latest, stage)
            if (latest != current or latest_owners != owners or final != current or final_owners != owners or first != second or _bound(db) != before
                or not _ids(db, latest, latest_owners, stage, identifier=scrap_line_id)):
                authority.fail('source_changed', '读取期间历史或权限变化，请刷新核验', 409)
            return RecoverySources(person_id=current.person_id, authorization_version=current.authorization_version,
                queried_at=datetime.now(timezone.utc), **first)
        except (InvalidChain, ValueError, KeyError, TypeError, AttributeError, ArithmeticError):
            authority.fail('source_unproven', '找回来源历史不完整，请保留原请求并联系总部核验', 503)


def queue(db, *, actor, stage, after_id=None, limit=5):
    if type(limit) is not int or not 1 <= limit <= 10 or (after_id is not None and (type(after_id) is not UUID or not after_id.int)):
        authority.fail('source_page_invalid', '分页参数无效', 400)
    with db.no_autoflush:
        current, owners = _scope(db, actor, stage); before = _bound(db)
        ids = _ids(db, current, owners, stage, after=after_id, limit=limit+1); items = []
        for identifier in ids[:limit]:
            try: items.append(read(db, actor=current, stage=stage, scrap_line_id=identifier))
            except (InventoryReadError, InventoryPostingError) as error:
                status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
                if status != 503: raise
                items.append(BlockedSource(scrap_line_id=identifier))
        latest, latest_owners = _scope(db, current, stage)
        if (latest != current or latest_owners != owners or _bound(db) != before
            or _ids(db, latest, latest_owners, stage, after=after_id, limit=limit+1) != ids):
            authority.fail('source_changed', '读取期间历史或权限变化，请刷新核验', 409)
        return RecoveryQueue(person_id=current.person_id, authorization_version=current.authorization_version,
            requested_stage=stage, queried_at=datetime.now(timezone.utc), items=tuple(items),
            next_after_id=ids[limit-1] if len(ids) > limit else None)
