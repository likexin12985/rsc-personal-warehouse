"""Read-only preparation for closing an unknown condition action request.

This does not persist an absence certificate, permit retry, or reauthorize a
stock action. A future registrar must repeat these proofs under locks and add
reciprocal COMMIT fences. Historical found results use lookup, not this module.
"""
from dataclasses import dataclass, field
import json
from uuid import UUID

from sqlalchemy import select

from app.foundation_models import Organization
from app.formal_access import FormalPrincipal
from app.return_condition_decision_requests import validate_decision
from app.return_condition_settlement_requests import ConditionSettlement, validate_settlement
from . import return_condition_settlement_inputs as settlement_inputs
from app.return_condition_schema import TRANSITIONS
from app.formal_services import inventory_posting as posting
from app.formal_services.stock_loss_planning import Account
from .historical_original import _bound
from . import return_condition_authority as authority
from . import return_condition_coordinates as coordinates
from . import return_condition_history_read as history
from . import return_condition_keys as keys
from .return_condition_seal_admission import SealAdmission


@dataclass(frozen=True)
class DecisionSealScope:
    actor: FormalPrincipal
    inbound_id: UUID
    source: Account
    organization_path: tuple
    references: tuple


@dataclass(frozen=True)
class DecisionSealAdmission(SealAdmission):
    # Retaining an old claimed hash is not proof of that unsaved request.
    original_preflight_verified: bool = field(default=False, init=False)


def validate_request(request):
    if type(request) is ConditionSettlement:
        return validate_settlement(request)
    return validate_decision(request)


def canonical(request):
    request = validate_request(request)
    if type(request) is ConditionSettlement:
        return settlement_inputs.canonical(request)
    document = request.model_dump(mode='json', exclude={'idempotency_key'})
    document['evidence_file_ids'] = sorted(document['evidence_file_ids'])
    document.update(schema_version='condition_decision_input/1',
        idempotency_key_hash=keys.aliases(request.idempotency_key)['condition_key_hash'])
    return document


def _scope(db, actor, request):
    current = authority._current(db, actor)
    cases, events = authority._tables()
    case = db.execute(select(cases).where(cases.c.id == request.case_id)).mappings().one_or_none()
    if case is None:
        authority._fail('not_found', '准确纠正单不存在或不可用', 404)
    current, line, source = history._scope(db, current, case['inbound_line_id'])
    authority._permission(db, current, request.action, source.owner_org_id)
    previous = db.execute(select(events).where(events.c.id == request.expected_event_id,
        events.c.case_id == case['id'])).mappings().one_or_none()
    submit = db.execute(select(events).where(events.c.id == case['submit_event_id'],
        events.c.case_id == case['id'], events.c.kind == 'submit')).mappings().one_or_none()
    if (previous is None or submit is None or case['inbound_id'] != line.inbound_id
            or case['source_account_id'] != source.id
            or submit['actor_person_id'] != source.custodian_person_id
            or not any(kind == request.action and before == previous['to_state']
                for kind, before, _ in TRANSITIONS)):
        authority._fail('reference_changed', '缺少准确原纠正动作的历史引用', 409)
    if request.action in authority.REQUESTER:
        if (current.user_id != submit['actor_user_id']
                or current.person_id != submit['actor_person_id']):
            authority._fail()
    elif (current.user_id == submit['actor_user_id']
            or current.person_id == submit['actor_person_id']):
        authority._fail('self_review_forbidden', '申请人不能核实或审批自己的成色纠正', 403)
    regional = None
    if request.action not in authority.REQUESTER and request.action not in authority.REGIONAL:
        regional = db.execute(select(events).where(events.c.case_id == case['id'],
            events.c.kind == 'verify_region', events.c.event_sequence <= previous['event_sequence'])
            .order_by(events.c.event_sequence.desc()).limit(1)).mappings().one_or_none()
        if regional is None:
            authority._fail('reference_changed', '缺少原动作对应的区域核实记录', 409)
        if (current.user_id == regional['actor_user_id']
                or current.person_id == regional['actor_person_id']):
            authority._fail('self_review_forbidden', '区域核实人与总部审批人必须独立', 403)
    node = db.get(Organization, source.owner_org_id, populate_existing=True)
    if node is None or node.org_type != 'region_company':
        authority._fail()
    path, seen = [], set()
    while node is not None:
        if node.id in seen or node.status != 'active':
            authority._fail()
        seen.add(node.id)
        path.append((node.id, node.parent_id, node.org_type, node.status))
        if node.parent_id is None:
            break
        node = db.get(Organization, node.parent_id, populate_existing=True)
        if node is None:
            authority._fail()
    # Historical custody expiry, a later case event or a changed available
    # balance cannot authorize execution, but must not strand an unknown key.
    refs = (case['id'], submit['id'], previous['id'], previous['request_hash'],
        regional['id'] if regional else None)
    account = Account(source.id, source.owner_org_id, source.custodian_person_id,
        source.location_id, source.material_id, source.condition_code,
        source.availability_bucket, source.lot_id)
    return DecisionSealScope(current, line.inbound_id, account, tuple(path), refs)


def authorize_absence_seal(db, *, actor, request):
    request = validate_request(request)
    document = canonical(request)
    with db.no_autoflush:
        scope = _scope(db, actor, request)
        before = _bound(db)
        observed = coordinates.capture(db, actor=scope.actor, request=request)
        coordinates.verify(observed)
        cases, _ = authority._tables()
        inbound_line_id = db.scalar(select(cases.c.inbound_line_id).where(cases.c.id == request.case_id))
        proved = history.read(db, actor=scope.actor, inbound_line_id=inbound_line_id)
        if (proved.basis.source != scope.source
                or request.expected_event_id not in proved.graph.event_ids
                or not any(case.case_id == request.case_id for case in proved.graph.projection.cases)):
            coordinates.unknown()
        fresh = coordinates.capture(db, actor=scope.actor, request=request)
        coordinates.verify(fresh)
        latest = _scope(db, scope.actor, request)
        if fresh != observed or latest != scope or _bound(db) != before:
            coordinates.unknown()
        return DecisionSealAdmission(scope.actor, authority.ACTIONS[request.action], scope.inbound_id,
            proved.basis, json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            posting._canonical_hash(document), tuple(keys.aliases(request.idempotency_key).items()),
            proved.graph.fingerprint, before)
