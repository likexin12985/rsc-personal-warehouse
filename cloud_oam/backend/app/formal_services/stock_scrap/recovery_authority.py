"""Current, separately granted recovery authority and exact live scrap source.

Private candidate only. No role seeding, public route or SQL grant is installed.
Historical approvals survive a reviewer's departure; each new caller must have
current authority. A recovery approval never authorizes a balance update.
"""
from datetime import datetime, timezone
from sqlalchemy import select
from app.inventory_models import StockAccount, InventorySerial, SerialCurrentPosition
from app.models import User
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.stock_loss_correction_models import StockLossCorrectionExecution
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.serial_ledger import rebuild_serial_states
from app.formal_services.stock_loss_corrections.history_chain import verify_chain
from app.formal_services.stock_loss_corrections.history_events import load_event_checked_inventory_history
from app.formal_services.stock_loss_corrections.chain_projection import project
from app.formal_services.stock_loss_corrections.reversal_stock import _custody
from .tables import tables

ACTIONS = dict(apply='apply_scrap_recovery', regional='review_scrap_recovery_regional',
    headquarters='review_scrap_recovery_headquarters', execute='execute_scrap_recovery')


def fail(code, message, status=409):
    sources._fail('stock_scrap_recovery_' + code, message, status)


def load_source(db, source):
    table = tables()['stock_scrap_lines']
    line = db.execute(select(table).where(table.c.id == source.scrap_line_id)).mappings().one_or_none()
    if line is None:
        fail('not_found', '准确报废记录不存在', 404)
    root = db.get(StockLossDisposition, line['root_disposition_id'], populate_existing=True)
    fact = db.get(StockLossCorrectionExecution, line['correction_execution_id'], populate_existing=True) if line['source_kind'] == 'correction' else root
    order = db.get(StockOperationOrder, root.operation_id, populate_existing=True) if root else None
    account = db.get(StockAccount, line['frozen_account_id'], populate_existing=True)
    if (fact is None or order is None or account is None or fact.disposition != 'scrap'
            or fact.request_hash != source.expected_scrap_request_hash):
        fail('source_changed', '本次找回绑定的报废来源或原请求不一致')
    return dict(line=line, root=root, fact=fact, order=order, account=account)


def _authorize_current(db, *, actor, source, stage, applicant=None, regional=None):
    if stage not in ACTIONS:
        raise ValueError('an explicit recovery authority stage is required')
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    user = db.get(User, current.user_id, populate_existing=True)
    account, order = source['account'], source['order']
    if user is None or not user.is_active:
        fail('forbidden', '当前找回操作账号不可用', 403)
    if stage == 'apply':
        scope, identifier = 'person', str(order.requester_id)
        role, grant_scope, grant_id = 'technician', scope, identifier
        if current.person_id != order.requester_id or current.person_id != account.custodian_person_id:
            fail('forbidden', '只能为本人保管的准确原报废提交找回申请', 403)
    else:
        scope, identifier = 'organization', str(account.owner_org_id)
        role, grant_scope, grant_id = ('provincial_manager', scope, identifier) if stage == 'regional' else ('admin', 'national', '*')
        for person in ((applicant, regional) if stage != 'execute' else ()):
            if person is not None and (current.user_id == person['actor_user_id'] or current.person_id == person['actor_person_id']):
                fail('self_review', '找回申请和两级复核必须由独立人员完成', 403)
    assignment_ids = {g.assignment_id for g in current.assignments
        if (g.role_code, g.scope_type, g.scope_id) == (role, grant_scope, grant_id)}
    if (not assignment_ids or not current.allows(db, 'stock_operation', ACTIONS[stage],
            target_scope_type=scope, target_scope_id=identifier)
            or not any(g.assignment_id in assignment_ids and
                (g.role_code, g.scope_type, g.scope_id, g.resource, g.action, g.field_code, g.effect) ==
                (role, grant_scope, grant_id, 'stock_operation', ACTIONS[stage], '', 'allow') for g in current.entitlements)):
        fail('forbidden', '没有该范围当前阶段的独立找回权限', 403)
    return current


def authorize(db, *, actor, source, stage, applicant=None, regional=None):
    """Business writes always require the original current physical custody."""
    current = _authorize_current(db, actor=actor, source=source, stage=stage,
        applicant=applicant, regional=regional)
    _custody(db, account=source['account'], requester_id=source['order'].requester_id,
        at=datetime.now(timezone.utc))
    return current


def authorize_closure(db, *, actor, source, stage, applicant=None, regional=None):
    """Only the current write-grant component of request closure authority.

No stock permit is returned. The seal service must additionally prove current
read access, the complete original request/history, absence and audit under
locks. Closing an old request does not require today's physical custody, but
does require the same role, scope, identity and reviewer independence.
    """
    if stage not in ACTIONS:
        raise ValueError('an explicit recovery closure stage is required')
    if stage == 'apply':
        if applicant is not None or regional is not None:
            raise ValueError('application closure has no review ancestors')
    else:
        if applicant is None or applicant['scrap_line_id'] != source['line']['id']:
            fail('request_changed', '关闭请求必须绑定准确的找回申请')
        if stage == 'headquarters':
            if (regional is None or regional['scrap_line_id'] != source['line']['id']
                    or regional['recovery_request_id'] != applicant['id']
                    or regional['decision'] != 'verified'):
                fail('regional_changed', '关闭总部请求必须绑定准确的独立区域核实')
        elif regional is not None:
            raise ValueError('only headquarters closure directly binds a regional review')
    return _authorize_current(db, actor=actor, source=source, stage=stage,
        applicant=applicant, regional=regional)


def require_current_scrap(db, source):
    root, fact, line = source['root'], source['fact'], source['line']
    verify_chain(db, root_disposition_id=root.id)
    history = load_event_checked_inventory_history(db, root_disposition_id=root.id).history
    state = project(history.basis, history.executions, history.reversals, history.decisions)
    if state.active_execution_id != fact.id or state.pending_reversal_id is not None:
        fail('not_current', '原报废已恢复或不再是当前执行，不能重复找回')
    table = tables()['stock_scrap_recovery_executions']
    if db.scalar(select(table.c.id).where(table.c.scrap_line_id == line['id'])) is not None:
        fail('already_recovered', '原报废已有恢复执行，请回查完整历史')
    sn = tables()['stock_scrap_serials']
    rows = db.execute(select(sn).where(sn.c.scrap_line_id == line['id'])).mappings().all()
    states = rebuild_serial_states(db, tuple(r['serial_id'] for r in rows))
    for row in rows:
        s = states[row['serial_id']]
        serial = db.get(InventorySerial, row['serial_id'], populate_existing=True)
        position = db.get(SerialCurrentPosition, row['serial_id'], populate_existing=True)
        if (s.lifecycle_status != 'scrapped' or s.stock_account_id is not None
                or s.last_movement_id != line['posting_movement_id'] or s.admission_movement_id != row['admission_movement_id']
                or serial is None or position is None or serial.lifecycle_status != s.lifecycle_status
                or position.stock_account_id is not None or position.last_movement_id != s.last_movement_id):
            fail('serial_changed', '找回 SN 的原报废、生命周期或最后流水不一致')
