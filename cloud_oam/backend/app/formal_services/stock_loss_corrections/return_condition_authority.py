"""Current admission checks for new historical inbound correction actions.

Internal and non-mutating; not a write permit or historical proof. The eventual
atomic service must repeat these checks under locks and enforce them at COMMIT.
Never call this module to validate old approvals or recover an original request:
old facts survive reviewer suspension/expiry. No production grants are seeded.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from uuid import UUID

from sqlalchemy import or_, select

from app.formal_access import FormalPrincipal
from app.foundation_models import Organization
from app.inventory_models import CustodyAssignment, StockAccount, StockLocation
from app.models import User
from app.stock_operation_models import StockOperationReturnInbound, StockOperationReturnInboundLine
from app.return_condition_schema import build_schema, TRANSITIONS
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources

# Intentionally distinct from personal loss-report permissions and historical
# read/recovery. A read grant or an unrelated inventory action cannot admit work.
ACTIONS = {
    'submit': 'submit_return_condition',
    'supplement': 'supplement_return_condition',
    'withdraw': 'withdraw_return_condition',
    'execute': 'execute_return_condition',
    'release': 'release_return_condition',
    'verify_region': 'review_return_condition_regional',
    'return_evidence': 'review_return_condition_regional',
    'reject_region': 'review_return_condition_regional',
    'return_region': 'review_return_condition_headquarters',
    'reject_hq': 'review_return_condition_headquarters',
    'approve_hq': 'review_return_condition_headquarters',
    'cancel_approved': 'cancel_return_condition_approval',
}
REQUESTER = frozenset(('submit', 'supplement', 'withdraw', 'execute', 'release'))
REGIONAL = frozenset(('verify_region', 'return_evidence', 'reject_region'))


def _fail(code='forbidden', message='没有该历史入库成色纠正动作的当前权限', status=403):
    sources._fail('return_condition_' + code, message, status)


def _id(value):
    if type(value) is not UUID or value.int == 0:
        raise ValueError('an exact nonzero UUID reference is required')
    return value


@dataclass(frozen=True)
class Admission:
    actor: FormalPrincipal
    action: str
    inbound_line_id: UUID
    source_account_id: UUID
    owner_org_id: UUID
    location_id: UUID
    custodian_person_id: UUID
    custody_assignment_id: UUID
    case_id: UUID | None = None
    previous_event_id: UUID | None = None
    previous_request_hash: str | None = None
    regional_event_id: UUID | None = None


@lru_cache(maxsize=1)
def _tables():
    # Cache table definitions only, never authority or persisted business facts.
    _, (cases, events, _, _), _ = build_schema()
    return cases, events


def _current(db, actor):
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    user = db.get(User, current.user_id, populate_existing=True)
    if user is None or not user.is_active:
        _fail()
    return current


def _source(db, inbound_line_id):
    line = db.get(StockOperationReturnInboundLine, inbound_line_id, populate_existing=True)
    header = db.get(StockOperationReturnInbound, line.inbound_id, populate_existing=True) if line else None
    account = db.get(StockAccount, line.target_account_id, populate_existing=True) if line else None
    if (header is None or account is None or not isinstance(header.plan_jsonb, dict)
            or header.plan_jsonb.get('schema_version') != '1.0'
            or line.condition_code not in ('new', 'used')
            or account.condition_code != line.condition_code or account.availability_bucket != 'available'
            or account.material_id != line.material_id or account.lot_id != line.lot_id
            or account.location_id != header.target_location_id
            or account.custodian_person_id != header.operator_person_id):
        _fail('reference_changed', '原入库及保管维度不一致，请重新核验准确来源', 409)
    location = db.get(StockLocation, account.location_id, populate_existing=True)
    if (location is None or location.status != 'active' or location.location_type != 'region'
            or location.owner_org_id != account.owner_org_id
            or location.custodian_person_id != account.custodian_person_id):
        _fail('custody_changed', '区域仓保管责任已变化，须先核验交接', 409)
    # A national grant must not hide an inactive/cyclic source organization tree.
    owner = db.get(Organization, account.owner_org_id, populate_existing=True)
    if owner is None or owner.org_type != 'region_company':
        _fail()
    seen = set()
    node = owner
    while node is not None:
        if node.id in seen or node.status != 'active':
            _fail()
        seen.add(node.id)
        if node.parent_id is None:
            break
        node = db.get(Organization, node.parent_id, populate_existing=True)
        if node is None:
            _fail()
    at = datetime.now(timezone.utc)
    custody = tuple(db.scalars(select(CustodyAssignment).where(
        CustodyAssignment.location_id == location.id, CustodyAssignment.valid_from <= at,
        or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > at))
        .limit(2).execution_options(populate_existing=True)))
    if len(custody) != 1 or custody[0].custodian_person_id != account.custodian_person_id:
        _fail('custody_changed', '缺少唯一有效的区域仓保管责任', 409)
    return line, header, account, custody[0]


def _permission(db, actor, kind, owner):
    role, scope_type, scope_id = ('provincial_manager', 'organization', str(owner)) if (
        kind in REQUESTER or kind in REGIONAL) else ('admin', 'national', '*')
    assignments = {g.assignment_id for g in actor.assignments
        if (g.role_code, g.scope_type, g.scope_id) == (role, scope_type, scope_id)}
    action = ACTIONS[kind]
    if (not assignments or not actor.allows(db, 'stock_operation', action,
            target_scope_type='organization', target_scope_id=str(owner))
            or not any(e.assignment_id in assignments
                and (e.role_code, e.scope_type, e.scope_id) == (role, scope_type, scope_id)
                and (e.resource, e.action, e.field_code, e.effect) == ('stock_operation', action, '', 'allow')
                for e in actor.entitlements)):
        _fail()


def _once(db, actor, *, kind, inbound_line_id=None, case_id=None, expected_event_id=None):
    current = _current(db, actor)
    case = latest = submit = regional = None
    if kind != 'submit':
        cases, events = _tables()
        case = db.execute(select(cases).where(cases.c.id == case_id)).mappings().one_or_none()
        if case is None:
            _fail('not_found', '准确纠正单不存在或不可用', 404)
        inbound_line_id = case['inbound_line_id']
    line, header, source, custody = _source(db, inbound_line_id)
    _permission(db, current, kind, source.owner_org_id)
    if case is not None:
        if (case['operation_type'] != 'condition_correction' or case['inbound_id'] != header.id
                or case['source_account_id'] != source.id or case['recorded_condition'] != source.condition_code
                or case['custody_assignment_id'] != custody.id):
            _fail('reference_changed', '纠正单与原入库或当前保管责任不一致', 409)
        latest = db.execute(select(events).where(events.c.case_id == case_id)
            .order_by(events.c.event_sequence.desc()).limit(1)).mappings().one_or_none()
        submit = db.execute(select(events).where(events.c.id == case['submit_event_id'],
            events.c.case_id == case_id, events.c.kind == 'submit')).mappings().one_or_none()
        if (latest is None or submit is None or latest['id'] != expected_event_id
                or not any(k == kind and before == latest['to_state'] for k, before, _ in TRANSITIONS)):
            _fail('action_changed', '纠正状态已变化，请重新读取原单，不能复用旧动作', 409)
        if submit['actor_person_id'] != source.custodian_person_id:
            _fail('reference_changed', '申请人和原区域仓保管人不一致', 409)
        if kind in REQUESTER:
            if current.user_id != submit['actor_user_id'] or current.person_id != submit['actor_person_id']:
                _fail()
        elif current.user_id == submit['actor_user_id'] or current.person_id == submit['actor_person_id']:
            _fail('self_review_forbidden', '申请人不能核实或审批自己的成色纠正', 403)
        if kind not in REQUESTER and kind not in REGIONAL:
            # State-chain proof is separate. Select the most recent verification,
            # never a caller-provided reviewer or an arbitrary old verification.
            regional = db.execute(select(events).where(events.c.case_id == case_id,
                events.c.kind == 'verify_region', events.c.event_sequence <= latest['event_sequence'])
                .order_by(events.c.event_sequence.desc()).limit(1)).mappings().one_or_none()
            if regional is None:
                _fail('reference_changed', '缺少本次区域实物核实记录', 409)
            if current.user_id == regional['actor_user_id'] or current.person_id == regional['actor_person_id']:
                _fail('self_review_forbidden', '区域核实人与总部审批人必须独立', 403)
    if kind in REQUESTER and current.person_id != source.custodian_person_id:
        _fail()
    return Admission(current, ACTIONS[kind], line.id, source.id, source.owner_org_id, source.location_id,
        source.custodian_person_id, custody.id, case_id,
        latest['id'] if latest else None, latest['request_hash'] if latest else None,
        regional['id'] if regional else None)


def _checked(db, actor, **coordinates):
    # Re-read mutable references and actual DB identity. This detects changes
    # within preparation; it is NOT the later write/COMMIT concurrency fence.
    with db.no_autoflush:
        before = _once(db, actor, **coordinates)
        after = _once(db, before.actor, **coordinates)
        if before != after:
            _fail('authority_changed', '权限或保管责任在核验期间变化，请重新读取', 409)
        return after


def authorize_submission(db, *, actor, inbound_line_id):
    return _checked(db, actor, kind='submit', inbound_line_id=_id(inbound_line_id))


def authorize_action(db, *, actor, case_id, expected_event_id, kind):
    if kind not in ACTIONS or kind == 'submit':
        raise ValueError('an exact subsequent condition action is required')
    return _checked(db, actor, kind=kind, case_id=_id(case_id), expected_event_id=_id(expected_event_id))
