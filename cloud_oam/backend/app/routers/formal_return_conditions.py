"""Condition commands, read-only exact recovery and explicit permanent closure.

The services retain stock/identity locks and database admission. This adapter
validates the public result before one COMMIT; an uncertain outcome never
replays a command or substitutes a new idempotency key.
"""
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_formal_principal
from ..formal_access import FormalPrincipal
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.inventory_query import InventoryReadError
from ..formal_services.inventory_posting import InventoryPostingError, _canonical_hash
from ..formal_services.stock_loss_corrections import (
    return_condition_submission_source as preparation,
    return_condition_submission as submission, return_condition_decisions as decisions,
    return_condition_settlement as settlement, return_condition_recovery as recovery,
    return_condition_decision_sealed_recovery as action_recovery,
    return_condition_request_seals as seals, return_condition_decision_request_seals as action_seals,
    return_condition_request_inputs as inputs, return_condition_decision_seal_admission as action_inputs,
)
from ..formal_services.stock_loss_corrections.return_condition_authority import ACTIONS
from ..return_condition_requests import ConditionSubmit
from ..return_condition_decision_requests import ConditionDecision
from ..return_condition_http_schemas import (
    ConditionCommand, ConditionRecoveryRequest, ConditionFact, ConditionFound,
    ConditionSealed, ConditionLookup, ConditionClosure, ConditionSourcePreview,
)
from ..formal_services.stock_loss_corrections.request_contracts import FactId

PRIVATE = {'Cache-Control': 'private, no-store', 'Pragma': 'no-cache', 'Referrer-Policy': 'no-referrer'}


class PrivateConditionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def checked(request):
            try:
                response = await handler(request)
                response.headers.update(PRIVATE)
                return response
            except RequestValidationError:
                return JSONResponse(status_code=422, headers=PRIVATE, content={'detail': {
                    'code': 'return_condition_request_invalid',
                    'message': '请求字段无效，请保留完整原请求并核对所选动作'}})
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **PRIVATE}
                raise
            except SQLAlchemyError:
                # Authentication/permission dependencies can fail before the
                # service boundary. Session teardown rolls their transaction
                # back; do not expose SQL or the submitted original command.
                return JSONResponse(status_code=503, headers=PRIVATE, content={'detail': {
                    'code': 'return_condition_admission_unavailable',
                    'message': '暂时无法核验访问权限，请保留原请求，勿自动重发'}})
        return checked


router = APIRouter(prefix='/return-condition-corrections', route_class=PrivateConditionRoute)


def _kind(command):
    return 'submit' if type(command) is ConditionSubmit else command.action


def _original(command):
    return inputs.canonical(command) if type(command) is ConditionSubmit else action_inputs.canonical(command)


def _admit(db, principal, command, mode, operator, trace, key):
    if operator is not None and operator != principal.person_id:
        raise HTTPException(403, detail={'code': 'operator_mismatch', 'message': '请以原操作人登录后回查'})
    for value, expected in ((trace, command.request_id), (key, command.idempotency_key)):
        if value is not None and value != expected:
            raise HTTPException(400, detail={'code': 'request_coordinate_mismatch', 'message': '请求头必须与完整原请求一致'})
    action = 'read' if mode == 'lookup' else ACTIONS[_kind(command)]
    if not principal.allows(db, 'stock_operation', action):
        raise HTTPException(403, detail={'code': 'forbidden', 'message': '没有该成色纠正动作权限'})


def _fact_binding(fact, command, principal):
    if (fact.request_id != command.request_id or fact.action != _kind(command)
            or fact.actor_user_id != principal.user_id or fact.actor_person_id != principal.person_id
            or fact.reason != command.reason):
        raise ValueError('outcome does not belong to this original command and actor')
    if type(command) is ConditionSubmit:
        if fact.inbound_line_id != command.inbound_line_id or fact.quantity != command.quantity:
            raise ValueError('outcome source or quantity mismatch')
    elif fact.case_id != command.case_id:
        raise ValueError('outcome belongs to another case')


def _public(raw, command, principal, mode):
    if mode == 'write':
        result = ConditionFact.model_validate(raw)
        _fact_binding(result, command, principal)
        return result
    data = dict(raw)
    # Readers intentionally return no transport envelope. Add only this exact
    # validated request coordinate, never a key, authorization proof or raw JSON.
    if 'request_id' in data:
        raise ValueError('unexpected recovery transport field')
    data['request_id'] = command.request_id
    original = _original(command)
    if data.get('request_state') == 'sealed':
        sealed = data['seal']
        if (sealed['request_id'] != command.request_id or sealed['kind'] != _kind(command)
                or sealed['actor_user_id'] != principal.user_id
                or str(sealed['actor_person_id']) != str(principal.person_id)
                or sealed['command_jsonb'] != original
                or sealed['request_hash'] != _canonical_hash(original)
                or sealed['request_state'] != 'sealed' or sealed['retry_allowed'] is not False
                or sealed['stock_effect'] != 'none'):
            raise ValueError('closure does not bind exact original input')
        if type(command) is ConditionSubmit:
            if str(sealed['inbound_line_id']) != str(command.inbound_line_id):
                raise ValueError('closure belongs to another inbound line')
        elif (str(sealed['case_id']) != str(command.case_id)
                or str(sealed['expected_event_id']) != str(command.expected_event_id)):
            raise ValueError('closure belongs to another action reference')
        data['seal'] = dict(seal_id=sealed['id'], kind=sealed['kind'],
            inbound_line_id=sealed['inbound_line_id'], case_id=sealed.get('case_id'),
            expected_event_id=sealed.get('expected_event_id'), sealed_at=sealed['created_at'], stock_effect='none')
        data.setdefault('original_preflight_verified', False)
    schema = ConditionClosure if mode == 'seal' else ConditionLookup
    result = TypeAdapter(schema).validate_python(data)
    if isinstance(result, (ConditionFound, ConditionSealed)):
        if result.original_input_hash != _canonical_hash(original):
            raise ValueError('original input fingerprint mismatch')
    if isinstance(result, ConditionFound):
        _fact_binding(result.result, command, principal)
    return result


def _rollback(db):
    try:
        db.rollback()
    except SQLAlchemyError:
        pass


def _operate(db, principal, command, mode, operator=None, trace=None, key=None):
    _admit(db, principal, command, mode, operator, trace, key)
    write = mode != 'lookup'
    if mode == 'lookup':
        handler = recovery.lookup if type(command) is ConditionSubmit else action_recovery.lookup
    elif mode == 'seal':
        handler = seals.seal if type(command) is ConditionSubmit else action_seals.seal
    else:
        handler = submission.submit if type(command) is ConditionSubmit else (
            decisions.decide if type(command) is ConditionDecision else settlement.settle)
    try:
        result = _public(handler(db, actor=principal, request=command), command, principal, mode)
        answer = result.model_dump(mode='json')
        if write:
            db.commit()
        return answer
    except (InventoryReadError, InventoryPostingError) as error:
        if write:
            _rollback(db)
        status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
        raise HTTPException(status, detail=error.as_detail()) from None
    except (SQLAlchemyError, AuditChainError, ValidationError, ValueError, KeyError, TypeError):
        if write:
            _rollback(db)
        raise HTTPException(503, detail={'code': 'return_condition_outcome_unconfirmed',
            'message': '结果暂时无法确认，请保留完整原请求回查，勿自动重发'}) from None
    except BaseException:
        if write:
            _rollback(db)
        raise


@router.post('/commands', response_model=ConditionFact)
def command(payload: ConditionCommand, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(get_formal_principal),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    return _operate(db, principal, payload, 'write', trace=trace, key=key)


@router.post('/request-lookup', response_model=ConditionLookup)
def lookup(payload: ConditionRecoveryRequest, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(get_formal_principal),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    return _operate(db, principal, payload.original, 'lookup', payload.operator_person_id, trace, key)


@router.post('/request-seal', response_model=ConditionClosure)
def seal(payload: ConditionRecoveryRequest, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(get_formal_principal),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    return _operate(db, principal, payload.original, 'seal', payload.operator_person_id, trace, key)


@router.get('/sources/{inbound_line_id}', response_model=ConditionSourcePreview)
def source(inbound_line_id: FactId, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(get_formal_principal)):
    if not principal.allows(db, 'stock_operation', ACTIONS['submit']):
        raise HTTPException(403, detail={'code': 'forbidden', 'message': '没有该成色纠正来源的查看权限'})
    try:
        try:
            checked = preparation.inspect_submission_source(db, actor=principal, inbound_line_id=inbound_line_id)
            doc = checked.document
            if (doc['selection']['inbound_line_id'] != str(inbound_line_id)
                    or doc['actor_user_id'] != principal.user_id
                    or doc['actor_person_id'] != str(principal.person_id)
                    or doc['posting_allowed'] is not False
                    or doc['physical_verification_required'] is not True
                    or doc['current_projection_verified'] is not True
                    or doc['submission_permission_checked'] is not True):
                raise ValueError('source preview does not bind selected source and current actor')
            policies = doc['policy_fingerprint']
            if len(policies) != 1 or policies[0][0] != doc['material_id']:
                raise ValueError('source tracking policy mismatch')
            retained = doc['source_status'] == 'recorded_stock_retained'
            reconciled = doc['source_status'] == 'verified_condition_history'
            fields = {name: doc[name] for name in ConditionSourcePreview.model_fields if name in doc}
            fields.update(inbound_line_id=inbound_line_id,
                root_disposition_id=doc['selection']['root_disposition_id'],
                tracking_mode=policies[0][2], expected_source_hash=checked.evidence_hash,
                checked_at=checked.checked_at,
                claimable_quantity=(doc['historical_damaged_quantity'] if retained else
                    doc['claimable_quantity'] if reconciled else None),
                serials=[dict(serial_id=s['serial_id'], serial_no=s['serial_no'], qr_code=s['qr_code'],
                    claimable_for_correction=s['retained_at_original_inbound'] if retained else
                        s['claimable_for_correction'] if reconciled else False) for s in doc['serials']])
            answer = ConditionSourcePreview.model_validate(fields).model_dump(mode='json')
        finally:
            # Source verification holds opening/custody row locks. Release them
            # promptly without committing any transaction or issuing a permit.
            db.rollback()
        return answer
    except (InventoryReadError, InventoryPostingError) as error:
        status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
        raise HTTPException(status, detail=error.as_detail()) from None
    except (SQLAlchemyError, AuditChainError, ValidationError, ValueError, KeyError, TypeError):
        raise HTTPException(503, detail={'code': 'return_condition_source_unavailable',
            'message': '暂时无法核验原入库份额，请稍后重新读取'}) from None
