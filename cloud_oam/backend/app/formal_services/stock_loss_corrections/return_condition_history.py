"""Complete persisted condition event graph relative to an independently proved basis.

Internal only: the scoped reader must prove the original inbound and current
read authority before calling this adapter, and recheck its observation bounds.
This is historical outcome evidence, never current stock or replay authority.
"""
from dataclasses import dataclass
import hashlib
import json
from functools import lru_cache

from sqlalchemy import or_, select

from app.return_condition_request_schema import NAME
from app.return_condition_key_schema import NAME as KEY_NAME
from app.return_condition_complete_schema import build_schema
from app.return_condition_settlement_input_schema import NAME as SETTLEMENT_INPUT, SCANS as SETTLEMENT_SCANS
from app.return_condition_schema import TRANSITIONS
from app.formal_services import stock_loss_sources as sources
from app.formal_services.stock_loss_planning import Account, TrackedQuantity, ContractError
from app.formal_services.work_order_query import _aware
from .historical_original import _bound
from . import return_condition_identity as identity, return_condition_business_events as business
from . import return_condition_request_inputs as inputs
from . import return_condition_keys as keys
from . import return_condition_settlement_inputs as settlement_inputs
from .return_condition_event_files import read_event_evidence
from .return_condition_ledger_facts import verify_event_posting
from .return_condition_contracts import Claim, Action, Projection
from .return_condition_projection import project

LIMIT = 20000


@lru_cache(maxsize=1)
def tables():
    return build_schema()[0].tables


def invalid():
    sources._fail('return_condition_history_invalid', '纠正单完整历史或业务关联不一致，不能确认结果或重发', 503)


def changed():
    sources._fail('return_condition_history_changed', '核验期间纠正历史发生变化，请重新查询', 409)


def _need(value):
    if not value:
        invalid()


def capture(db, inbound_line_id):
    """Follow both ends of relationships, including unrepresented common orders."""
    t=tables(); groups={}
    def rows(name, predicate):
        table=t[name]
        value=tuple(dict(r) for r in db.execute(select(table).where(predicate)
            .order_by(*table.primary_key.columns).limit(LIMIT+1)).mappings())
        groups[name]=value
        if sum(map(len,groups.values()))>LIMIT:
            sources._fail('return_condition_history_limit','纠正历史超过完整核验范围',503)
        return value
    c=t['stock_condition_cases'];e=t['stock_condition_events'];o=t['stock_operation_orders']
    seed_events=select(e.c.case_id).where(e.c.inbound_line_id==inbound_line_id)
    seed_orders=select(o.c.id).where(o.c.operation_type=='condition_correction',
                                   o.c.plan_jsonb['inbound_line_id'].as_string()==str(inbound_line_id))
    incoming=t['stock_operation_return_inbound_lines'];headers=t['stock_operation_return_inbounds']
    movements=t['inventory_movements']
    original_movements=(select(movements.c.id).join(headers,
        headers.c.posting_transaction_id==movements.c.transaction_id).join(incoming,
        (incoming.c.inbound_id==headers.c.id)&(incoming.c.line_no==movements.c.line_no))
        .where(incoming.c.id==inbound_line_id))
    cases=rows(c.name,or_(c.c.inbound_line_id==inbound_line_id,c.c.id.in_(seed_events),c.c.id.in_(seed_orders),
        c.c.original_movement_id.in_(original_movements),
        c.c.source_jsonb['selection']['inbound_line_id'].as_string()==str(inbound_line_id)))
    ids=tuple(r['id'] for r in cases)
    events=rows(e.name,or_(e.c.inbound_line_id==inbound_line_id,e.c.case_id.in_(ids)))
    event_ids=tuple(r['id'] for r in events)
    rows(o.name,or_(o.c.id.in_(ids),o.c.condition_case_id.in_(ids),o.c.id.in_(seed_orders)))
    lines=t['stock_operation_lines']
    actual_lines=rows(lines.name,or_(lines.c.operation_id.in_(ids),lines.c.condition_case_id.in_(ids),
                                   lines.c.id.in_(tuple(r['line_id'] for r in cases))))
    serials=t['stock_operation_serials'];rows(serials.name,serials.c.line_id.in_(tuple(r['id'] for r in actual_lines)))
    serials=t['stock_condition_serials'];rows(serials.name,or_(serials.c.case_id.in_(ids),serials.c.inbound_line_id==inbound_line_id))
    files=t['stock_condition_files'];rows(files.name,files.c.event_id.in_(event_ids))
    registry=t[NAME];rows(NAME,or_(registry.c.event_id.in_(event_ids),
                                 registry.c.input_jsonb['inbound_line_id'].as_string()==str(inbound_line_id)))
    registry=t[KEY_NAME];rows(KEY_NAME,or_(registry.c.event_id.in_(event_ids),registry.c.case_id.in_(ids)))
    registry=t[SETTLEMENT_INPUT];rows(SETTLEMENT_INPUT,or_(registry.c.event_id.in_(event_ids),registry.c.case_id.in_(ids)))
    registry=t[SETTLEMENT_SCANS];rows(SETTLEMENT_SCANS,or_(registry.c.event_id.in_(event_ids),registry.c.case_id.in_(ids)))
    cancellations=t['stock_operation_cancellations'];rows(cancellations.name,cancellations.c.operation_id.in_(ids))
    accounts=t['stock_accounts']
    account_ids={r[k] for r in cases for k in ('source_account_id','frozen_account_id')}
    account_ids.update(r[k] for r in events for k in ('from_account_id','to_account_id') if r[k])
    rows(accounts.name,accounts.c.id.in_(tuple(account_ids)))
    digest=hashlib.sha256(json.dumps(groups,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
    return groups,digest


def account(row):
    return Account(row['id'],row['owner_org_id'],row['custodian_person_id'],row['location_id'],
                   row['material_id'],row['condition_code'],row['availability_bucket'],row['lot_id'])


def _common(case,event,serials,groups,source):
    orders=[r for r in groups['stock_operation_orders'] if r['id']==case['id']]
    lines=[r for r in groups['stock_operation_lines'] if r['operation_id']==case['id']]
    _need(len(orders)==len(lines)==1)
    order,line=orders[0],lines[0]
    same=('actor_user_id','authorization_version','reason','request_id','idempotency_key_hash',
          'request_hash','plan_hash','command_jsonb','plan_jsonb','posting_transaction_id')
    expected=dict(id=case['id'],condition_case_id=case['id'],operation_type='condition_correction',
                  operation_no='COND-'+case['id'].hex.upper(),status='submitted',
                  source_location_id=source.location_id,requester_id=event['actor_person_id'])
    expected.update({k:event[k] for k in same})
    expected.update({k:None for k in ('target_location_id','transit_location_id','target_custody_assignment_id',
                                      'oam_work_order_id','loss_headquarters_decision_id','loss_correction_decision_id')})
    _need(all(order[k]==v for k,v in expected.items()) and _aware(order['created_at'])==_aware(event['created_at']))
    expected=dict(id=case['line_id'],operation_id=case['id'],operation_type='condition_correction',
        condition_case_id=case['id'],line_no=1,stock_account_id=source.id,reserved_account_id=case['frozen_account_id'],
        material_id=source.material_id,quantity=case['quantity'],target_condition='damaged',reason=event['reason'],
        source_recovery_line_id=None,source_loss_line_id=None)
    _need(all(line[k]==v for k,v in expected.items()) and _aware(line['created_at'])==_aware(event['created_at']))
    common=[r for r in groups['stock_operation_serials'] if r['line_id']==line['id']]
    _need(tuple(sorted((r['serial_id'] for r in common),key=str))==serials)
    _need(all(r['sku_verified'] and r['qr_verified'] and _aware(r['created_at'])==_aware(event['created_at']) for r in common))


def _case(case,basis):
    expected=dict(operation_type='condition_correction',root_disposition_id=basis.root_disposition_id,
        inbound_line_id=basis.inbound_line_id,original_transaction_id=basis.original_transaction_id,
        original_movement_id=basis.original_movement_id,original_ledger_cursor=basis.original_ledger_cursor,
        source_account_id=basis.source.id,recorded_condition=basis.source.condition,target_condition='damaged',
        affected_quantity=basis.affected.quantity,tracking_mode=basis.affected.tracking_mode,
        quantity_scale=basis.affected.quantity_scale,allow_fraction=basis.affected.allow_fraction,submit_kind='submit')
    _need(all(case[k]==v for k,v in expected.items()))
    # The saved source hash binds original intent; the basis must come from
    # real independently verified history, never from this JSON alone.
    doc=case['source_jsonb']
    _need(sources._hash(doc)==case['source_hash'])
    _need(doc['selection']==dict(root_disposition_id=str(basis.root_disposition_id),
        inbound_line_id=str(basis.inbound_line_id),expected_history_fingerprint=case['history_hash']))
    expected=dict(inbound_id=str(case['inbound_id']),source_account_id=str(basis.source.id),
        owner_org_id=str(basis.source.owner_org_id),location_id=str(basis.source.location_id),
        custodian_person_id=str(basis.source.custodian_person_id),material_id=str(basis.source.material_id),
        lot_id=str(basis.source.lot_id) if basis.source.lot_id else None,recorded_condition=basis.source.condition,
        required_condition='damaged',availability_bucket='available',
        historical_damaged_quantity=format(basis.affected.quantity,'.3f'),
        original_transaction_id=str(basis.original_transaction_id),original_movement_id=str(basis.original_movement_id),
        original_ledger_cursor=basis.original_ledger_cursor,custody_assignment_id=str(case['custody_assignment_id']))
    _need(all(doc[k]==v for k,v in expected.items()))
    _need(tuple(sorted((r['serial_id'] for r in doc['serials'])))==tuple(sorted(map(str,basis.affected.serial_ids))))


@dataclass(frozen=True)
class ConditionGraph:
    projection: Projection
    fingerprint: str
    event_ids: tuple
    original_input_hashes: tuple
    current_stock_verified: bool = False
    retry_allowed: bool = False


def verify(db, *, basis):
    """Internal composition; current read scope and source proof are mandatory upstream."""
    with db.no_autoflush:
        before=_bound(db);groups,fingerprint=capture(db,basis.inbound_line_id)
        try:
            cases={r['id']:r for r in groups['stock_condition_cases']}
            events=sorted(groups['stock_condition_events'],key=lambda r:r['event_sequence'])
            accounts={r['id']:account(r) for r in groups['stock_accounts']}
            _need(not groups['stock_operation_cancellations'])
            _need(len(groups['stock_operation_orders'])==len(groups['stock_operation_lines'])==len(cases))
            _need(tuple(e['event_sequence'] for e in events)==tuple(range(1,len(events)+1)))
            _need({r['event_id'] for r in groups[NAME]}=={e['id'] for e in events if e['kind']=='submit'})
            _need({r['event_id'] for r in groups[KEY_NAME]}=={e['id'] for e in events})
            settlement_ids={e['id'] for e in events if e['kind'] in ('execute','release')}
            _need({r['event_id'] for r in groups[SETTLEMENT_INPUT]}==settlement_ids)
            _need(all(r['event_id'] in settlement_ids for r in groups[SETTLEMENT_SCANS]))
            _need(all(r['case_id'] in cases and r['inbound_line_id']==basis.inbound_line_id
                      for r in groups['stock_condition_serials']))
            for case in cases.values():
                _case(case,basis)
                _need(accounts[case['source_account_id']]==basis.source)
            previous={};fold=[];hashes=[];submitted=set()
            for event in events:
                case=cases[event['case_id']];prior=previous.get(case['id'])
                _need(all(event[k]==case[k] for k in ('inbound_line_id','source_account_id','frozen_account_id','quantity','submit_event_id')))
                _need((event['kind'],event['from_state'],event['to_state']) in TRANSITIONS)
                _need(event['previous_event_id']==(prior['id'] if prior else None))
                _need(event['previous_sequence']==(prior['event_sequence'] if prior else None))
                _need(event['from_state']==(prior['to_state'] if prior else 'draft'))
                _need(prior is None or _aware(event['created_at'])>=_aware(prior['created_at']))
                if event['kind'] in ('execute','release'):
                    _need(prior is not None and event['decision_event_id']==prior['id'] and event['decision_kind']==prior['kind'])
                else:
                    _need(event['decision_event_id'] is None and event['decision_kind'] is None)
                serials=tuple(sorted((r['serial_id'] for r in groups['stock_condition_serials'] if r['case_id']==case['id']),key=str))
                files=read_event_evidence(db,event_id=event['id'])
                canonical=identity.identity(case,event,serial_ids=serials,
                    evidence=[dict(file_id=f.file_id,metadata_sha256=f.metadata_sha256) for f in files],
                    previous_request_hash=prior['request_hash'] if prior else None)
                _need((event['plan_jsonb'],event['plan_hash'],event['command_jsonb'],event['request_hash'])==
                      (canonical.plan,canonical.plan_hash,canonical.command,canonical.request_hash))
                edge=verify_event_posting(db,event=event,serial_ids=serials)
                business.verify(db,case=case,event=event,recipient=basis.source.custodian_person_id)
                keys.verify(db,event=event)
                if event['kind']=='submit':
                    _need(prior is None and case['id'] not in submitted and event['id']==case['submit_event_id'])
                    _need(edge.transaction_id==case['freeze_transaction_id'] and edge.movement_id==case['freeze_movement_id'])
                    _common(case,event,serials,groups,basis.source)
                    hashes.append((event['id'],inputs.verify(db,case=case,event=event)['input_hash']))
                    submitted.add(case['id'])
                    fold.append(Claim(event['id'],case['id'],event['event_sequence'],basis.inbound_line_id,
                        event['actor_user_id'],event['actor_person_id'],TrackedQuantity(case['quantity'],case['tracking_mode'],
                        case['quantity_scale'],case['allow_fraction'],serials),accounts[case['frozen_account_id']],
                        edge,event['reason'],tuple(f.file_id for f in files)))
                else:
                    if event['kind'] in ('execute','release'):
                        hashes.append((event['id'],settlement_inputs.verify(db,case=case,event=event)['input_hash']))
                    fold.append(Action(event['id'],case['id'],event['event_sequence'],event['kind'],event['actor_user_id'],
                        event['actor_person_id'],event['reason'],tuple(f.file_id for f in files),event['decision_event_id'],
                        edge,accounts[event['to_account_id']] if edge else None))
                previous[case['id']]=event
            _need(submitted==set(cases))
            projection=project(basis,tuple(fold))
            _need(all(state.status==previous[state.case_id]['to_state'] for state in projection.cases))
        except (KeyError,TypeError,ValueError,AttributeError,ContractError):
            invalid()
        if capture(db,basis.inbound_line_id)[1]!=fingerprint or _bound(db)!=before:
            changed()
        return ConditionGraph(projection,fingerprint,tuple(e['id'] for e in events),tuple(hashes))
