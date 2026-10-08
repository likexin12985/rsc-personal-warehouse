"""Actual predecessor receipts: diagnose, patch, and retain historical files.

Only an owned disposable database is accepted. No stored file or receipt is
changed. Malformed-file probes evaluate row values through the same SQL binding.
"""
from copy import deepcopy
from datetime import timedelta
import json

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from pg16_return_condition_authority_gate import owned_role_connection


def run(owner, *, directory, original, candidate, preinstalled=False):
    with owned_role_connection(owner, directory) as connection:
        catalog = connection.execute(text("""SELECT prosrc,prosecdef,proconfig,
            pg_get_userbyid(proowner) AS owner,proacl::text AS acl
            FROM pg_proc WHERE oid=to_regprocedure(:signature)"""),
            {'signature': candidate['SIGNATURE']}).mappings().one()
        assert catalog['prosrc'] == candidate['BODY' if preinstalled else 'EXPECTED_BODY']
        assert catalog['prosecdef'] and catalog['owner'] == 'star_oam_migrator'
        assert catalog['proconfig'] == ['search_path=pg_catalog, public']
        rows = connection.execute(text('''SELECT fact.id AS receipt_id,
            fact.authorization_version AS receipt_version, uploader.authorization_version AS current_version,
            file.metadata_jsonb AS metadata, fact.created_at AS received_at,
            (''' + candidate['CURRENT_BINDING'] + ''') AS current_binding,
            (''' + candidate['HISTORICAL_BINDING'] + ''') AS historical_binding
            FROM public.stock_operation_return_inbounds inbound
            JOIN public.stock_operation_receipts fact ON fact.id=inbound.receipt_id
            JOIN public.stock_operation_receipt_exceptions exception ON exception.receipt_id=fact.id
            JOIN public.files file ON file.id=exception.evidence_file_id
            JOIN public.users uploader ON uploader.id=fact.actor_user_id
            WHERE inbound.id=:inbound'''), {'inbound': original['inboundId']}).mappings().all()
        assert rows and all(r['historical_binding'] and not r['current_binding']
            and r['receipt_version'] == r['metadata']['authorization_version']
            and r['receipt_version'] != r['current_version'] for r in rows)
        receipt_id = rows[0]['receipt_id']
        assert all(r['receipt_id'] == receipt_id for r in rows)

        def check(current):
            connection.execute(text('SELECT public.rsc_check_loss_receipt_0155(:id,:current)'),
                {'id': receipt_id, 'current': current})

        def rejected(call, message):
            savepoint = connection.begin_nested()
            try:
                try:
                    call()
                except DBAPIError as error:
                    assert error.orig.sqlstate == '23514' and message in str(error.orig)
                else:
                    raise AssertionError('historical receipt negative check unexpectedly passed')
            finally:
                savepoint.rollback()

        if not preinstalled:
            rejected(lambda: check(False), '0105 exception file purpose, recipient, completion or original binding mismatch')
            for statement in candidate['statements']():
                connection.execute(text(statement))
        check(False)
        connection.execute(text('SELECT public.rsc_check_stock_return_inbound_0111(:id)'),
            {'id': original['inboundId']})
        # A fresh write cannot borrow the historical flag. The INSERT trigger
        # still calls require_current=true and rejects the stale actor version.
        rejected(lambda: check(True), '0105')
        patched = connection.execute(text("""SELECT prosrc,prosecdef,proconfig,
            pg_get_userbyid(proowner) AS owner,proacl::text AS acl
            FROM pg_proc WHERE oid=to_regprocedure(:signature)"""),
            {'signature': candidate['SIGNATURE']}).mappings().one()
        assert patched['prosrc'] == candidate['BODY']
        assert all(patched[k] == catalog[k] for k in ('prosecdef','proconfig','owner','acl'))
        for role in ('star_oam_api','star_oam_backup','star_oam_projector','star_oam_edge','edge_inbox'):
            assert not connection.scalar(text('SELECT has_function_privilege(:role,:signature,\'EXECUTE\')'),
                {'role': role, 'signature': candidate['SIGNATURE']})
        # Evaluate corrupt file values without disabling immutable-row guards.
        binding = candidate['HISTORICAL_BINDING'].replace('FROM public.files AS file_row',
            '''FROM (SELECT (jsonb_populate_record(NULL::public.files,
                to_jsonb(f)||CAST(:change AS jsonb))).* FROM public.files f) AS file_row''')
        predicate = '(' + binding + ''') AND EXISTS (SELECT 1 FROM
            (SELECT (jsonb_populate_record(NULL::public.files,
                to_jsonb(f)||CAST(:change AS jsonb))).* FROM public.files f) proof
            WHERE proof.id=exception.evidence_file_id
              AND proof.metadata_jsonb->'authorization_version'=to_jsonb(fact.authorization_version))'''
        query = text('SELECT (' + predicate + ''') FROM public.stock_operation_receipts fact
            JOIN public.stock_operation_receipt_exceptions exception ON exception.receipt_id=fact.id
            WHERE fact.id=:id''')
        refused = []
        for label in ('purpose','uploader','person','version','completion','completion_after_receipt'):
            metadata = deepcopy(rows[0]['metadata'])
            change = {'metadata_jsonb': metadata}
            if label == 'purpose': metadata['purpose'] = 'stock_loss_evidence'
            elif label == 'uploader': change['uploaded_by'] = original['administratorUserId']
            elif label == 'person': metadata['uploader_person_id'] = '00000000-0000-0000-0000-000000000001'
            elif label == 'version': metadata['authorization_version'] += 1
            elif label == 'completion': metadata.pop('completion')
            else:
                metadata['completion']['verified_at'] = (rows[0]['received_at'] + timedelta(days=1)).isoformat()
            results = connection.scalars(query, {'id': receipt_id, 'change': json.dumps(change)}).all()
            assert results and all(value is not True for value in results), label
            refused.append(label)
        connection.commit()
    result = dict(originalFailureReproduced=not preinstalled, formalPreinstalled=preinstalled, historicalReceiptAndInbound=True,
        currentWriteStillRejected=True, securityCatalogPreserved=True,
        malformedHistoricalFileValuesRejected=refused, storedEvidenceUnchanged=True,
        versions=[dict(receipt=r['receipt_version'], current=r['current_version']) for r in rows])
    return result
