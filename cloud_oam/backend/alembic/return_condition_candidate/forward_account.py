"""Candidate initial frozen-account admission; all earlier branches retained.

The existing 0159 SECURITY DEFINER and session-user boundary are preserved.
Complete dedicated event, identity, posting, source and authority triggers
enforce the referenced case in the same commit. No empty account is admitted.
"""
from hashlib import sha256
from pathlib import Path
import json

raw = (Path(__file__).resolve().parents[1] / 'stock_loss_corrections_0159/frozen-catalog.json').read_bytes()
if sha256(raw).hexdigest() != '29bee9907c271dfc9e8bc616e99c658f52de71261b9c6cbfc91b71ade6ec2d50':
    raise ValueError('condition account requires exact frozen predecessor catalog')
OLD = next(r for r in json.loads(raw)['replacedFunctions']
    if r['proname'] == 'rsc_require_opening_observation_account_0023')
SIGNATURE = 'public.rsc_require_opening_observation_account_0023()'
quality_raw = (Path(__file__).resolve().parents[1] / 'return_inbound_quality_0162/catalog.json').read_bytes()
if sha256(quality_raw).hexdigest() != '64e499dd5d521e3e835de21bcecceab34ae7def5515af416c69f3cce9dcc29bc':
    raise ValueError('condition account requires exact 0162 quality transition')
quality = json.loads(quality_raw)['patches'][SIGNATURE]
if (quality['before'] != OLD['prosrc']
        or sha256(quality['before'].encode()).hexdigest() != quality['beforeSha256']
        or sha256(quality['after'].encode()).hexdigest() != quality['afterSha256']):
    raise ValueError('condition account predecessor continuity mismatch')
EXPECTED_BODY = quality['after']
EXPECTED_SHA256 = sha256(EXPECTED_BODY.encode()).hexdigest()
ANCHOR = "    IF NOT EXISTS (\n        SELECT 1\n          FROM public.inventory_movements AS movement"
BRANCH = """    IF EXISTS (
        SELECT 1 FROM public.stock_condition_cases c
        JOIN public.stock_condition_events e ON e.id=c.submit_event_id AND e.case_id=c.id AND e.kind='submit'
        JOIN public.stock_operation_orders parent ON parent.id=c.id AND parent.condition_case_id=c.id
        JOIN public.stock_operation_lines line ON line.id=c.line_id AND line.operation_id=parent.id
        JOIN public.inventory_transactions tx ON tx.id=c.freeze_transaction_id AND tx.id=e.posting_transaction_id
        JOIN public.inventory_movements m ON m.id=c.freeze_movement_id AND m.id=e.posting_movement_id AND m.transaction_id=tx.id
        JOIN public.stock_accounts source ON source.id=c.source_account_id
        JOIN public.stock_balances balance ON balance.stock_account_id=NEW.id
        JOIN public.inventory_opening_establishments established ON established.owner_org_id=NEW.owner_org_id
            AND established.location_id=NEW.location_id
        WHERE c.frozen_account_id=NEW.id AND e.frozen_account_id=NEW.id AND line.reserved_account_id=NEW.id
          AND parent.operation_type='condition_correction' AND line.operation_type='condition_correction'
          AND line.condition_case_id=c.id AND parent.status='submitted'
          AND line.stock_account_id=source.id AND line.quantity=c.quantity
          AND c.quantity>0 AND e.quantity=c.quantity AND m.quantity=c.quantity
          AND NEW.availability_bucket='frozen' AND source.availability_bucket='available'
          AND source.condition_code IN ('new','used') AND NEW.condition_code=source.condition_code
          AND NEW.owner_org_id=source.owner_org_id AND NEW.location_id=source.location_id
          AND NEW.custodian_person_id=source.custodian_person_id AND NEW.material_id=source.material_id
          AND NEW.lot_id IS NOT DISTINCT FROM source.lot_id
          AND parent.requester_id=NEW.custodian_person_id AND e.actor_person_id=NEW.custodian_person_id
          AND tx.status='posted' AND tx.movement_type='freeze' AND tx.reversed_transaction_id IS NULL
          AND tx.source_document_type='stock_condition_event' AND tx.source_document_id=e.id::text
          AND tx.actor_user_id=e.actor_user_id AND parent.posting_transaction_id=tx.id
          AND m.from_account_id=source.id AND m.to_account_id=NEW.id
          AND NEW.created_at=parent.created_at AND NEW.created_at=e.created_at
          AND NEW.created_at=tx.effective_at AND NEW.updated_at=NEW.created_at
          AND NEW.created_at>=established.established_at AND NEW.created_at<=tx.created_at
          AND balance.version=1 AND balance.ledger_cursor=tx.ledger_cursor AND balance.quantity=c.quantity
          AND (SELECT count(*) FROM public.inventory_movements WHERE transaction_id=tx.id)=1
          AND NOT EXISTS(SELECT 1 FROM public.inventory_movements other
              WHERE (other.from_account_id=NEW.id OR other.to_account_id=NEW.id) AND other.id<>m.id)
    ) THEN
        PERFORM public.rsc_condition_check_source(inbound_line_id)
            FROM public.stock_condition_cases WHERE frozen_account_id=NEW.id;
        PERFORM public.rsc_condition_check_posting(id)
            FROM public.stock_condition_cases WHERE frozen_account_id=NEW.id;
        PERFORM public.rsc_condition_check_identity(id)
            FROM public.stock_condition_cases WHERE frozen_account_id=NEW.id;
        RETURN NEW;
    END IF;

"""
if EXPECTED_BODY.count(ANCHOR) != 1:
    raise ValueError('condition account predecessor anchor drift')
BODY = EXPECTED_BODY.replace(ANCHOR, BRANCH + ANCHOR, 1)
BODY_SHA256 = sha256(BODY.encode()).hexdigest()
if OLD['definition'].count(OLD['prosrc']) != 1 or not OLD['prosecdef']:
    raise ValueError('condition account predecessor definition drift')
DEFINITION = OLD['definition'].replace(OLD['prosrc'], BODY, 1)


def statements():
    return [DEFINITION]
