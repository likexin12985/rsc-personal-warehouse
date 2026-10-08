"""Real PostgreSQL canonical/key checks; UUIDs here are syntax vectors only.

This does not prove a business source exists or that a seal may be written.
"""
from copy import deepcopy
from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.stock_scrap_schemas import ScrapExecute
from app.stock_scrap_recovery_schemas import (
    ScrapRecoveryApply, ScrapRecoveryRegionalReview, ScrapRecoveryHeadquartersReview, ScrapRecoveryExecute,
)
from app.formal_services.stock_scrap.request_lookup import canonical
from app.formal_services.stock_scrap.recovery_facts import intent
from app.formal_services.stock_scrap.lookup_coordinates import keys
from app.formal_services.stock_loss_sources import _hash
from app.stock_scrap_seal_schema import ALIASES

CALL = text('SELECT public.rsc_prepare_scrap_seal_request_0165(:kind,CAST(:command AS jsonb),:key)')
CANONICAL = text('SELECT public.rsc_scrap_seal_canonical_0165(:kind,CAST(:command AS jsonb))')


def vectors():
    ids = [UUID(int=n) for n in range(1, 9)]
    common = dict(request_id='seal-canonical-contract', idempotency_key='real-request-key-0165')
    reason = '准确原请求\n封存\t核验'
    original = ScrapExecute(**common, source=dict(kind='original', headquarters_decision_id=ids[0],
        expected_headquarters_review_hash='a'*64, expected_submission_plan_hash='b'*64),
        execution_reason=reason, evidence_file_ids=(ids[3], ids[2]), expected_plan_hash='c'*64)
    corrected = ScrapExecute.model_validate(original.model_dump() | {'source':dict(kind='correction', root_disposition_id=ids[0],
        reversal_id=ids[1], correction_decision_id=ids[2], expected_root_request_hash='a'*64,
        expected_submission_plan_hash='b'*64, expected_reversal_hash='d'*64, expected_correction_decision_hash='e'*64)})
    source = dict(scrap_line_id=ids[0], expected_scrap_request_hash='f'*64)
    application = ScrapRecoveryApply(**common, action='apply_scrap_recovery', source=source,
        reason=reason, evidence_file_ids=(ids[3], ids[2]))
    review = dict(**common, source=source, reason=reason, recovery_request_id=ids[1], expected_request_hash='a'*64)
    region = ScrapRecoveryRegionalReview(**review, action='review_scrap_recovery_region', decision='verified')
    hq = ScrapRecoveryHeadquartersReview(**review, action='review_scrap_recovery_headquarters',
        regional_review_id=ids[2], expected_regional_hash='b'*64, decision='approve')
    execution = ScrapRecoveryExecute(**review, action='execute_scrap_recovery', headquarters_review_id=ids[3],
        expected_headquarters_hash='c'*64, expected_plan_hash='d'*64)
    return [('original', original, canonical(original)), ('correction', corrected, canonical(corrected)),
        ('apply', application, intent(application)), ('regional', region, intent(region)),
        ('headquarters', hq, intent(hq)), ('execute', execution, intent(execution)),
        ('regional', region.model_copy(update={'decision':'needs_evidence'}), intent(region) | {'decision':'needs_evidence'}),
        ('headquarters', hq.model_copy(update={'decision':'request_regional_review'}), intent(hq) | {'decision':'request_regional_review'})]


def run(owner, api):
    count = rejected = 0
    with owner.begin() as db:
        for kind, request, document in vectors():
            args = dict(kind=kind, command=json.dumps(document, ensure_ascii=False), key=request.idempotency_key)
            result = db.scalar(CALL, args)
            keyless = db.scalar(CANONICAL, args)
            assert keyless == {key: value for key,value in result.items()
                if key not in ('key_token','idempotency_key_hash',*ALIASES)}
            assert result['command_jsonb'] == document and result['request_hash'] == _hash(document)
            assert result['key_token'] == sha256(('cloud_oam.loss.correction.key.v1\0'+request.idempotency_key).encode()).hexdigest()
            hashes = keys(request)
            assert tuple(result[name] for name in ALIASES) == hashes
            assert result['idempotency_key_hash'] == hashes[4 if kind in ('original','correction') else 3]
            assert result['plan_hash'] == getattr(request, 'expected_plan_hash', None)
            assert request.idempotency_key not in json.dumps(result)
            count += 1
            attacks = [dict(args, kind='unknown'), dict(args, kind=None), dict(args, key='bad key!')]
            changed = [document | {'unexpected':'field'}, document | {'request_id':12345678},
                document | {'request_id':None}]
            body = document['intent'] if kind in ('original','correction') else document
            reason_key = 'execution_reason' if kind in ('original','correction') else 'reason'
            for field, value in [(reason_key,'bad\x01reason'), (reason_key,' trailing '), ('source',None)]:
                clone = deepcopy(document)
                target = clone['intent'] if kind in ('original','correction') else clone
                target[field] = value
                changed.append(clone)
            if 'evidence_file_ids' in body:
                for value in ([], body['evidence_file_ids'][::-1], [body['evidence_file_ids'][0]]*2,
                        ['00000000-0000-0000-0000-000000000000'], [12345678]):
                    clone = deepcopy(document)
                    (clone['intent'] if kind in ('original','correction') else clone)['evidence_file_ids'] = value
                    changed.append(clone)
            if kind in ('original','correction','execute'):
                changed.extend([document | {'expected_plan_hash':None}, document | {'expected_plan_hash':'A'*64}])
            else:
                changed.append(document | {'expected_plan_hash':'a'*64})
            attacks.extend(dict(args, command=json.dumps(value, ensure_ascii=False)) for value in changed)
            for bad in attacks:
                savepoint = db.begin_nested()
                try:
                    db.execute(CALL, bad)
                except DBAPIError as error:
                    assert error.orig.sqlstate == '23514', error.orig
                    rejected += 1
                else:
                    raise AssertionError('malformed canonical seal input accepted')
                finally:
                    savepoint.rollback()
        assert db.scalar(text('SELECT count(*) FROM stock_scrap_request_seals')) == 0
    with api.connect() as db:
        for call in (CALL,CANONICAL):
            try:
                db.execute(call, args)
            except DBAPIError as error:
                assert error.orig.sqlstate == '42501'
                db.rollback()
            else:
                raise AssertionError('private input proof was exposed to API')
    return dict(validCanonicalVectors=count, malformedInputsRejected=rejected, privateApiCallDenied=True,
        pythonAndPostgresHashesAgree=True, keylessCanonicalParity=True, sealRowsCreated=0, sourceExistenceProven=False)
