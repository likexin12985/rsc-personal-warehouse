"""Prove bounded reuse on actual migrated two-generation HTTP facts.

Only owned native PG16 fixtures may call this helper. Instrumentation changes
one function and a temporary counter inside a rolled-back owner transaction;
no business row is changed and the entire public catalog is compared after it.
"""
from collections import Counter
import json
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import stock_scrap_security
from pg16_scrap_transition_gate import catalog, runtime_admission

PRIVATE_SIGNATURES = {
    'rsc_build_scrap_history_proof_0165': 'uuid',
    'rsc_scrap_history_from_proof_0165': 'text,uuid,uuid,jsonb',
    'rsc_loss_requests_with_proof_0165': 'uuid,jsonb',
    'rsc_loss_events_with_proof_0165': 'uuid,jsonb',
    'rsc_loss_inverse_with_proof_0165': 'uuid,jsonb',
    'rsc_loss_correction_with_proof_0165': 'uuid,jsonb',
    'rsc_scrap_upstream_with_audit_0165': 'uuid,uuid[]',
    'rsc_scrap_fact_with_root_proof_0165': 'text,uuid,uuid,uuid[]',
}


def run(engines, root_id):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    before = catalog(owner)
    record = next(r['after'] for r in stock_scrap_security.DATA['functions'].values()
        if r['after']['proname'] == 'rsc_scrap_fact_with_root_proof_0165')
    body, definition = record['prosrc'], record['definition']
    assert body.count('\nBEGIN\n') == 1
    graph = text('SELECT public.rsc_check_loss_history_graph_0159(:id)')
    rejected = []
    with owner.connect() as db:
        transaction = db.begin()
        try:
            baseline = db.scalar(graph, dict(id=root_id))
            expected = []
            for verified_root in baseline['verified_original_ids']:
                expected += [(row.kind, row.id) for row in db.execute(text('''
                    SELECT 'original' AS kind,id FROM stock_loss_dispositions WHERE id=:id AND disposition='scrap'
                    UNION ALL SELECT 'correction',id FROM stock_loss_correction_executions
                        WHERE root_disposition_id=:id AND disposition='scrap'
                    UNION ALL SELECT 'inverse',id FROM stock_loss_disposition_reversals
                        WHERE root_disposition_id=:id AND scrap_line_id IS NOT NULL
                '''), dict(id=UUID(verified_root)))]
            assert len(expected) == 4 and len(set(expected)) == 4, 'two complete scrap/recovery generations required'
            fresh_audit = db.scalar(text('SELECT public.rsc_loss_inventory_audit_members_0159()'))
            for kind, fact in expected:
                full = db.scalar(text('SELECT public.rsc_check_scrap_history_0165(:kind,:fact)'), dict(kind=kind, fact=fact))
                shared = db.scalar(text('SELECT public.rsc_scrap_fact_with_root_proof_0165(:kind,:fact,:root,:audit)'),
                    dict(kind=kind, fact=fact, root=root_id, audit=fresh_audit))
                assert shared == full, 'shared-root proof changed the standalone retained plan'
            db.execute(text('CREATE TEMP TABLE history_proof_calls_0165(kind text, fact uuid) ON COMMIT DROP'))
            db.execute(text('CREATE TEMP TABLE history_root_calls_0165(root uuid) ON COMMIT DROP'))
            upstream = next(r['after'] for r in stock_scrap_security.DATA['functions'].values()
                if r['after']['proname'] == 'rsc_scrap_upstream_with_audit_0165')
            assert upstream['prosrc'].count('\nBEGIN\n') == 1
            counted_root = upstream['prosrc'].replace('\nBEGIN\n',
                '\nBEGIN\n    INSERT INTO pg_temp.history_root_calls_0165 VALUES(checked_root);\n', 1)
            db.execute(text(upstream['definition'].replace(upstream['prosrc'], counted_root, 1)))
            counted = body.replace('\nBEGIN\n', '\nBEGIN\n'
                '    INSERT INTO pg_temp.history_proof_calls_0165 VALUES(kind,checked_fact);\n', 1)
            db.execute(text(definition.replace(body, counted, 1)))
            for count in (1, 2):
                assert db.scalar(graph, dict(id=root_id)) == baseline
                calls = Counter(tuple(row) for row in db.execute(text('SELECT kind,fact FROM pg_temp.history_proof_calls_0165')))
                assert calls == Counter({pair: count for pair in expected}), 'proof reused across calls or repeated inside one call'
                roots = Counter(db.execute(text('SELECT root FROM pg_temp.history_root_calls_0165')).scalars())
                assert roots == Counter({UUID(root): count for root in baseline['verified_original_ids']}), 'upstream proof repeated or reused across calls'
            # Each retained fact still runs its original complete proof. Make
            # only that proof fail, after prior success in the same transaction.
            for kind, fact in expected:
                savepoint = db.begin_nested()
                try:
                    assert kind in ('original', 'correction', 'inverse') and isinstance(fact, UUID)
                    injected = body.replace('\nBEGIN\n', '\nBEGIN\n'
                        f"    IF kind='{kind}' AND checked_fact='{fact}'::uuid THEN\n"
                        "        RAISE EXCEPTION 'synthetic retained fact proof failure' USING ERRCODE='23514'; END IF;\n", 1)
                    db.execute(text(definition.replace(body, injected, 1)))
                    try:
                        db.scalar(graph, dict(id=root_id))
                    except DBAPIError as error:
                        assert error.orig.sqlstate == '23514'
                        assert error.orig.diag.message_primary == 'synthetic retained fact proof failure'
                        rejected.append('fresh_required:'+kind)
                    else:
                        raise AssertionError('complete history skipped a retained fact')
                finally:
                    savepoint.rollback()
            # A successful graph must not retain proof across later calls,
            # including root lineage and audit corruption in the same tx.
            for name in ('rsc_scrap_upstream_with_audit_0165', 'rsc_loss_inventory_audit_members_0159'):
                savepoint = db.begin_nested()
                try:
                    original = db.scalar(text('SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=\'public\' AND p.proname=:name'), dict(name=name))
                    assert original.count('\nBEGIN\n') == 1
                    injected = original.replace('\nBEGIN\n', "\nBEGIN\n    RAISE EXCEPTION 'synthetic common root proof failure' USING ERRCODE='23514';\n", 1)
                    db.execute(text(injected))
                    try:
                        db.scalar(graph, dict(id=root_id))
                    except DBAPIError as error:
                        assert error.orig.sqlstate == '23514'
                        assert error.orig.diag.message_primary == 'synthetic common root proof failure'
                        rejected.append('fresh_required:'+name)
                    else:
                        raise AssertionError('graph skipped fresh common root proof')
                finally:
                    savepoint.rollback()
            proof = db.scalar(text('SELECT public.rsc_build_scrap_history_proof_0165(:id)'), dict(id=root_id))
            original = next(fact for kind, fact in expected if kind == 'original')
            for label, fact, selected_root in (('foreign_root', original, uuid4()), ('unproved_fact', uuid4(), root_id)):
                savepoint = db.begin_nested()
                try:
                    try:
                        db.scalar(text("SELECT public.rsc_scrap_history_from_proof_0165('original',:fact,:root,CAST(:proof AS jsonb))"),
                            dict(fact=fact, root=selected_root, proof=json.dumps(proof)))
                    except DBAPIError as error:
                        assert error.orig.sqlstate == '23514'
                        rejected.append(label)
                    else:
                        raise AssertionError('foreign or absent proof fact accepted')
                finally:
                    savepoint.rollback()
            for label, selected_root, audit in [('fact_foreign_root', uuid4(), fresh_audit),
                    ('fact_missing_audit', root_id, None), ('fact_empty_audit', root_id, [])]:
                savepoint = db.begin_nested()
                try:
                    try:
                        db.scalar(text("SELECT public.rsc_scrap_fact_with_root_proof_0165('original',:fact,:root,CAST(:audit AS uuid[]))"),
                            dict(fact=original, root=selected_root, audit=audit))
                    except DBAPIError as error:
                        assert error.orig.sqlstate == '23514'
                        rejected.append(label)
                    else:
                        raise AssertionError('unbound root or missing audit accepted')
                finally:
                    savepoint.rollback()
        finally:
            transaction.rollback()
    assert catalog(owner) == before, 'instrumentation changed the public catalog'
    for name, types in PRIVATE_SIGNATURES.items():
        with api.connect() as db:
            try:
                db.execute(text('SELECT public.'+name+'('+','.join('NULL::'+t for t in types.split(','))+')'))
            except DBAPIError as error:
                assert error.orig.sqlstate == '42501'
                rejected.append('api_denied:'+name)
            else:
                raise AssertionError('API can forge a private history proof')
            finally:
                db.rollback()
    assert catalog(owner) == before
    runtime_admission(engines)
    return dict(passed=True, factsProvedPerGraphCall=len(expected), successiveCallsVerified=2,
        noCrossCallReuse=True, rejected=rejected, publicCatalogRestored=True,
        standalonePlansMatched=len(expected), freshRootAndAuditRequired=True,
        upstreamProofsPerGraphCall=len(baseline['verified_original_ids']),
        scope='rolled-back private-function instrumentation on owned real HTTP facts')
