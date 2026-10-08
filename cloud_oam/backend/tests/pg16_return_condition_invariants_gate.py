"""Native invariant and locking component; external parents are explicit stubs."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from queue import Queue
import time
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.return_condition_guards import statements
from app.return_condition_schema import TABLE_NAMES
from return_condition_guard_fixture import Source, fixture_schema


def snapshot(owner, meta):
    with owner.connect() as db:
        rows = {name: sorted(json.dumps(dict(r), sort_keys=True, default=str)
            for r in db.execute(select(table)).mappings()) for name, table in meta.tables.items()}
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def run(owner, api):
    meta = fixture_schema()
    meta.create_all(owner)
    ddl = statements()
    with owner.begin() as db:
        for command in ddl:
            db.exec_driver_sql(command)
        # Deliberately give this isolated API role DML privileges, so rejection
        # must come from actual triggers rather than a missing GRANT.
        for name in meta.tables:
            db.exec_driver_sql('GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON public.' + name + ' TO star_oam_api')
        for name in ('rsc_condition_reject_mutation()', 'rsc_condition_lock_source()',
                     'rsc_condition_check_source(uuid)', 'rsc_condition_validate_insert()'):
            assert not db.scalar(text('SELECT has_function_privilege(:role, :name, :priv)'),
                dict(role='star_oam_api', name='public.'+name, priv='EXECUTE'))
    rejected, positive, races = [], [], []

    def source(mode):
        item = Source(meta, mode)
        with owner.begin() as db:
            item.seed(db)
        return item

    def refuse(name, action, *, phase='commit', message=None, sqlstate='23514'):
        before = snapshot(owner, meta)
        inserted = False
        try:
            with api.begin() as db:
                action(db)
                inserted = True
        except DBAPIError as error:
            assert error.orig.sqlstate == sqlstate, (name, str(error.orig))
            assert inserted == (phase == 'commit'), (name, 'wrong failure phase', str(error.orig))
            if message:
                assert message in str(error.orig), (name, str(error.orig))
            assert snapshot(owner, meta) == before, name + ': failed transaction changed facts'
            rejected.append(dict(name=name, phase=phase, sqlstate=sqlstate, rollbackPreservedAllRows=True))
        else:
            raise AssertionError(name + ': invalid write committed')

    for table in TABLE_NAMES:
        for mutation in ('UPDATE', 'DELETE', 'TRUNCATE'):
            command = (f'UPDATE public.{table} SET ' + next(iter(meta.tables[table].c)).name + '=' +
                next(iter(meta.tables[table].c)).name if mutation == 'UPDATE' else f'{mutation} ' +
                ('FROM ' if mutation == 'DELETE' else '') + f'public.{table}' + (' CASCADE' if mutation == 'TRUNCATE' else ''))
            refuse(table+'_'+mutation, lambda db, sql=command: db.exec_driver_sql(sql),
                phase='statement', message='append only')

    for mode in ('none', 'lot', 'serial', 'lot_and_serial'):
        for event_first in (False, True):
            s = source(mode)
            with api.begin() as db:
                s.claim(db, event_first=event_first)
                s.claim(db, seq=2, serial_indices=(1,))
            positive.append(f'{mode}:two_nonoverlapping_claims:event_first={event_first}')
        s = source(mode)
        refuse(mode+':forged_budget', lambda db: s.claim(db, case_changes={'affected_quantity': 3}),
            message='share does not match')
        s = source(mode)
        with api.begin() as db:
            initial = s.claim(db, quantity='2', serial_indices=(0, 1))
            rejected_event = s.action(db, initial, 'reject_region', 'rejected_pending_release', actor='region')
        refuse(mode+':rejection_does_not_release', lambda db: s.claim(db, seq=3), message='budget exceeded')
        with api.begin() as db:
            s.action(db, rejected_event, 'release', 'released_rejected')
            s.claim(db, seq=4, quantity='2', serial_indices=(0, 1))
        positive.append(mode+':exact_release_reopens_share')
        s = source(mode)
        with api.begin() as db:
            initial = s.claim(db, quantity='2', serial_indices=(0, 1))
            withdrawn = s.action(db, initial, 'withdraw', 'cancelled_pending_release')
        refuse(mode+':withdrawal_does_not_release', lambda db: s.claim(db, seq=3), message='budget exceeded')
        s = source(mode)
        def transient_overclaim(db):
            initial = s.claim(db, quantity='2', serial_indices=(0, 1))
            s.claim(db, seq=2)
            withdrawn = s.action(db, initial, 'withdraw', 'cancelled_pending_release', seq=3)
            s.action(db, withdrawn, 'release', 'released_cancelled')
        refuse(mode+':later_release_cannot_hide_prefix_overclaim', transient_overclaim, message='budget exceeded')
        s = source(mode)
        with api.begin() as db:
            initial = s.claim(db, quantity='2', serial_indices=(0, 1))
            region = s.action(db, initial, 'verify_region', 'awaiting_headquarters', actor='region')
            hq = s.action(db, region, 'approve_hq', 'approved', actor='hq')
            s.action(db, hq, 'execute', 'executed')
        refuse(mode+':execution_permanently_consumes_budget', lambda db: s.claim(db, seq=5), message='budget exceeded')
        positive.append(mode+':independent_review_then_execution')
        s = source(mode)
        with api.begin() as db:
            initial = s.claim(db)
            region = s.action(db, initial, 'verify_region', 'awaiting_headquarters', actor='region', seq=10)
        refuse(mode+':backdated_event', lambda db: s.claim(db, seq=5, serial_indices=(1,)),
            phase='statement', message='prior history')
        refuse(mode+':same_region_hq', lambda db: s.action(db, region, 'approve_hq', 'approved', actor='region'),
            message='headquarters must be independent')
        # Different login ID for the same person must not evade independence.
        refuse(mode+':region_person_alias_hq', lambda db: s.action(db, region, 'approve_hq', 'approved', actor='hq',
            changes={'actor_person_id': region['actor_person_id']}), message='headquarters must be independent')
        s = source(mode)
        with api.begin() as db:
            initial = s.claim(db)
        for actor, changes in (('requester', {}), ('region', {'actor_person_id': initial['actor_person_id']}),
                               ('region', {'actor_user_id': initial['actor_user_id']})):
            refuse(mode+':self_review:'+str(len(rejected)), lambda db, a=actor, c=changes:
                s.action(db, initial, 'verify_region', 'awaiting_headquarters', actor=a, changes=c),
                message='requester cannot review')
        refuse(mode+':foreign_withdraw', lambda db: s.action(db, initial, 'withdraw', 'cancelled_pending_release', actor='other'),
            message='requester identity mismatch')
        if s.tracked:
            refuse(mode+':same_sn_under_quantity_budget', lambda db: s.claim(db, seq=2), message='serial claimed more than once')
            s = source(mode)
            refuse(mode+':undamaged_sn', lambda db: s.claim(db, serial_indices=(2,)), message='not accepted damaged')
            refuse(mode+':missing_sn', lambda db: s.claim(db, serial_indices=()), message='share does not match')
            def overlapping_interval(db):
                first = s.claim(db)
                s.claim(db, seq=2)
                withdrawn = s.action(db, first, 'withdraw', 'cancelled_pending_release', seq=3)
                s.action(db, withdrawn, 'release', 'released_cancelled')
            refuse(mode+':later_release_cannot_hide_sn_overlap', overlapping_interval, message='serial claimed more than once')
        else:
            s = source(mode)
            refuse(mode+':disallowed_fraction', lambda db: s.claim(db, quantity='0.375', case_changes={'allow_fraction': False}),
                message='share does not match')
            with api.begin() as db:
                s.claim(db, quantity='0.375')
                s.claim(db, quantity='1.625', seq=2)
            positive.append(mode+':fractional_budget_exact')

    for isolation in ('REPEATABLE READ', 'SERIALIZABLE'):
        s = source('none')
        def wrong_isolation(db):
            db.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL '+isolation)
            s.claim(db)
        refuse(isolation, wrong_isolation, phase='statement', message='require read committed')

    for mode in ('none', 'serial'):
        for outcome in ('commit', 'rollback', 'release'):
            s, unrelated = source(mode), source(mode)
            quantity, indices = ('1', (0,)) if s.tracked else ('2', ())
            withdrawn = None
            if outcome == 'release':
                with api.begin() as db:
                    first = s.claim(db, quantity=quantity, serial_indices=indices)
                    withdrawn = s.action(db, first, 'withdraw', 'cancelled_pending_release')
            first_db = api.connect()
            first_tx = first_db.begin()
            pool = ThreadPoolExecutor(max_workers=1)
            ready = Queue()
            try:
                holder_pid = first_db.scalar(text('SELECT pg_backend_pid()'))
                if outcome == 'release':
                    s.action(first_db, withdrawn, 'release', 'released_cancelled')
                else:
                    s.claim(first_db, quantity=quantity, serial_indices=indices)
                def contender():
                    inserted = False
                    try:
                        with api.begin() as db:
                            db.exec_driver_sql("SET LOCAL statement_timeout='10s'")
                            ready.put(db.scalar(text('SELECT pg_backend_pid()')))
                            s.claim(db, seq=10, quantity=quantity, serial_indices=indices)
                            inserted = True
                    except DBAPIError as error:
                        return dict(committed=False, sqlstate=error.orig.sqlstate, atCommit=inserted,
                                    message=str(error.orig).splitlines()[0])
                    return dict(committed=True)
                future = pool.submit(contender)
                pid = ready.get(timeout=5)
                deadline = time.monotonic()+5
                while True:
                    with owner.connect() as db:
                        blockers = db.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': pid})
                    if holder_pid in blockers:
                        break
                    if time.monotonic() > deadline:
                        raise AssertionError('contender never blocked on exact source owner')
                    time.sleep(0.02)
                # Prove this isn't a global inventory mutex: a different source
                # commits while the same-source contender is still blocked.
                with api.begin() as db:
                    db.exec_driver_sql("SET LOCAL statement_timeout='2s'")
                    unrelated.claim(db)
                if outcome == 'rollback':
                    first_tx.rollback()
                else:
                    first_tx.commit()
                result = future.result(timeout=12)
                if outcome == 'commit':
                    assert result['committed'] is False and result['sqlstate']=='23514' and result['atCommit'], result
                    assert ('serial claimed more than once' if s.tracked else 'budget exceeded') in result['message'], result
                else:
                    assert result['committed'], result
                with owner.connect() as db:
                    count = db.scalar(text('SELECT count(*) FROM stock_condition_events WHERE inbound_line_id=:id'), {'id': s.ids['line']})
                    assert count == (4 if outcome == 'release' else 1)
                races.append(dict(mode=mode, holderOutcome=outcome, sameSourceBlockingObserved=True,
                    unrelatedSourceCommittedWhileBlocked=True, finalEvents=count, contender=result))
            finally:
                if first_tx.is_active:
                    first_tx.rollback()
                first_db.close()
                pool.shutdown(wait=True)
    return dict(passed=True, rejected=rejected, positive=positive, races=races,
        ddlSha256=hashlib.sha256('\n'.join(ddl).encode()).hexdigest(),
        externalParents='minimal relational stubs; predecessor business triggers absent',
        realApiRoleSql=True, mountedHttp=False, completeHistoricalProof=False,
        currentAuthorityProved=False, formalMigration=False, productionAcceptance=False)
