"""Exact forward extension for a newly created execution target account.

All prior account admission branches and session-user checks are retained.
Only the migrator may install against the exact accepted predecessor catalog;
this is an unpublished candidate, not permission to migrate a live database.
"""
from hashlib import sha256
from pathlib import Path
import runpy
from sqlalchemy import text

SIGNATURE = 'public.rsc_require_opening_observation_account_0023()'
EXPECTED_SHA256 = '5e782fe76c2a6d2426919e7ddfa20641a23e0e98e697a30fbbd9b406d763bc26'
BRANCH = """    IF EXISTS (
        SELECT 1 FROM public.stock_condition_events e
        JOIN public.stock_condition_cases c ON c.id=e.case_id
        JOIN public.stock_condition_events decision ON decision.id=e.decision_event_id
            AND decision.id=e.previous_event_id AND decision.case_id=c.id AND decision.kind='approve_hq'
        JOIN public.stock_condition_events submitted ON submitted.id=c.submit_event_id
            AND submitted.case_id=c.id AND submitted.kind='submit'
        JOIN public.stock_accounts source ON source.id=c.source_account_id
        JOIN public.stock_accounts frozen ON frozen.id=c.frozen_account_id
        JOIN public.inventory_transactions tx ON tx.id=e.posting_transaction_id
        JOIN public.inventory_transactions freeze_tx ON freeze_tx.id=c.freeze_transaction_id
        JOIN public.inventory_movements m ON m.id=e.posting_movement_id AND m.transaction_id=tx.id
        JOIN public.stock_balances balance ON balance.stock_account_id=NEW.id
        JOIN public.inventory_opening_establishments established ON established.owner_org_id=NEW.owner_org_id
            AND established.location_id=NEW.location_id
        WHERE e.kind='execute' AND e.from_state='approved' AND e.to_state='executed'
          AND decision.to_state='approved' AND e.previous_sequence=decision.event_sequence
          AND e.event_sequence>decision.event_sequence AND e.decision_kind=decision.kind
          AND e.submit_event_id=c.submit_event_id AND e.inbound_line_id=c.inbound_line_id
          AND e.source_account_id=source.id AND e.frozen_account_id=frozen.id
          AND e.from_account_id=frozen.id AND e.to_account_id=NEW.id
          AND e.quantity=c.quantity AND m.quantity=c.quantity AND c.quantity>0
          AND NEW.id NOT IN (source.id,frozen.id) AND NEW.condition_code='damaged'
          AND NEW.availability_bucket='available' AND source.availability_bucket='available'
          AND source.condition_code IN ('new','used') AND frozen.availability_bucket='frozen'
          AND frozen.condition_code=source.condition_code
          AND ROW(NEW.owner_org_id,NEW.custodian_person_id,NEW.location_id,NEW.material_id,NEW.lot_id)
            IS NOT DISTINCT FROM ROW(source.owner_org_id,source.custodian_person_id,source.location_id,source.material_id,source.lot_id)
          AND ROW(frozen.owner_org_id,frozen.custodian_person_id,frozen.location_id,frozen.material_id,frozen.lot_id)
            IS NOT DISTINCT FROM ROW(source.owner_org_id,source.custodian_person_id,source.location_id,source.material_id,source.lot_id)
          AND e.actor_user_id=submitted.actor_user_id AND e.actor_person_id=submitted.actor_person_id
          AND e.actor_person_id=NEW.custodian_person_id AND tx.actor_user_id=e.actor_user_id
          AND tx.status='posted' AND tx.movement_type='status_change' AND tx.reversed_transaction_id IS NULL
          AND tx.source_document_type='stock_condition_event' AND tx.source_document_id=e.id::text
          AND freeze_tx.status='posted' AND tx.ledger_cursor>freeze_tx.ledger_cursor
          AND m.from_account_id=frozen.id AND m.to_account_id=NEW.id AND m.line_no=1 AND m.external_boundary_code IS NULL
          AND NEW.created_at=e.created_at AND NEW.created_at=tx.effective_at AND NEW.updated_at=NEW.created_at
          AND NEW.created_at>=established.established_at AND NEW.created_at<=tx.created_at
          AND balance.version=1 AND balance.ledger_cursor=tx.ledger_cursor AND balance.quantity=c.quantity
          AND (SELECT count(*) FROM public.inventory_movements WHERE transaction_id=tx.id)=1
          AND NOT EXISTS(SELECT 1 FROM public.inventory_movements other
              WHERE (other.from_account_id=NEW.id OR other.to_account_id=NEW.id) AND other.id<>m.id)
    ) THEN
        PERFORM public.rsc_condition_check_source(c.inbound_line_id)
            FROM public.stock_condition_cases c JOIN public.stock_condition_events e ON e.case_id=c.id
            WHERE e.kind='execute' AND e.to_account_id=NEW.id;
        PERFORM public.rsc_condition_check_posting(e.case_id), public.rsc_condition_check_identity(e.case_id),
            public.rsc_condition_check_current_event(e.id), public.rsc_condition_check_settlement_input(e.id),
            public.rsc_condition_check_business_effects(e.id)
            FROM public.stock_condition_events e WHERE e.kind='execute' AND e.to_account_id=NEW.id;
        RETURN NEW;
    END IF;

"""


def compile_transition(predecessor_path):
    prior = runpy.run_path(str(Path(predecessor_path)))
    if (prior['SIGNATURE'] != SIGNATURE or sha256(prior['BODY'].encode()).hexdigest() != EXPECTED_SHA256
            or prior['BODY_SHA256'] != EXPECTED_SHA256 or prior['BODY'].count(prior['ANCHOR']) != 1
            or prior['DEFINITION'].count(prior['BODY']) != 1):
        raise ValueError('settlement account predecessor source drift')
    body = prior['BODY'].replace(prior['ANCHOR'], BRANCH + prior['ANCHOR'], 1)
    return dict(before=prior['BODY'], after=body, beforeSha256=EXPECTED_SHA256,
        afterSha256=sha256(body.encode()).hexdigest(),
        definition=prior['DEFINITION'].replace(prior['BODY'], body, 1),
        catalog={key: prior['OLD'][key] for key in
            ('prosecdef', 'provolatile', 'proparallel', 'proisstrict', 'proleakproof', 'proconfig', 'owner', 'acl')})


def catalog(db):
    row = db.execute(text("SELECT p.prosrc,p.prosecdef,p.provolatile,p.proparallel,p.proisstrict,p.proleakproof,p.proconfig,"
        "r.rolname AS owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner "
        "WHERE p.oid=to_regprocedure(:signature)"), dict(signature=SIGNATURE)).mappings().one_or_none()
    if row is None: raise ValueError('settlement account predecessor missing')
    result = dict(row)
    result['acl'] = [dict(r) for r in db.execute(text("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE r.rolname END AS grantee,"
        "a.is_grantable AS grantable,a.privilege_type AS privilege FROM pg_proc p "
        "CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
        "LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=to_regprocedure(:signature) "
        "ORDER BY grantee,privilege,grantable"), dict(signature=SIGNATURE)).mappings()]
    return result


def install(db, predecessor_path):
    if not db.in_transaction() or db.scalar(text('SELECT session_user::text')) != 'star_oam_migrator':
        raise ValueError('settlement account installation requires a migrator transaction')
    compiled = compile_transition(predecessor_path)
    before = catalog(db)
    if before != dict(compiled['catalog'], prosrc=compiled['before']):
        raise ValueError('settlement account predecessor catalog drift')
    for name in ('rsc_condition_check_source', 'rsc_condition_check_posting', 'rsc_condition_check_identity',
            'rsc_condition_check_current_event', 'rsc_condition_check_settlement_input', 'rsc_condition_check_business_effects'):
        if db.scalar(text('SELECT to_regprocedure(:signature)'), dict(signature='public.' + name + '(uuid)')) is None:
            raise ValueError('settlement account dependency missing: ' + name)
    db.execute(text(compiled['definition']))
    if catalog(db) != dict(compiled['catalog'], prosrc=compiled['after']):
        raise ValueError('settlement account forward catalog mismatch; rollback required')
    return compiled
