"""Current DB closure grants against actual committed six-kind sources.

These are private authority-component checks, not successful seal writes or
COMMIT/race evidence. Physical custody checks remain independently mandatory.
"""
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from pg16_scrap_seal_sources import arguments
from pg16_stock_scrap_structure_gate import original_columns, facts

CALL = text('SELECT public.rsc_prepare_scrap_seal_authority_0165('
    ':actor,:version,:person,:kind,CAST(:command AS jsonb),:key)')
PHYSICAL = text('SELECT public.rsc_assert_scrap_recovery_authority_0165('
    ':actor,:version,:person,CAST(:owner AS uuid),CAST(:location AS uuid),CAST(:requester AS uuid),:kind)')
ACTIONS = dict(original='dispose_loss', correction='correct_loss', apply='apply_scrap_recovery',
    regional='review_scrap_recovery_regional', headquarters='review_scrap_recovery_headquarters',
    execute='execute_scrap_recovery')


def rejected(db, params, message, call=CALL):
    savepoint = db.begin_nested()
    try:
        db.execute(call, params)
    except DBAPIError as error:
        assert error.orig.sqlstate == '23514', error.orig
        assert message in error.orig.diag.message_primary, error.orig
    else:
        raise AssertionError('invalid seal authority accepted')
    finally:
        savepoint.rollback()


def exercise(context):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    cases = {}
    for actor, command, _ in context['scrap_lookup_cases'] + context['scrap_recovery_lookup_cases']:
        params = arguments(command)
        cases.setdefault(params['kind'], (actor, params))
    assert set(cases) == set(ACTIONS)
    accepted, denied, separated = [], [], []
    with owner.begin() as db:
        columns = original_columns(db)
        before = facts(db, columns)
        for kind, (actor, params) in cases.items():
            user = db.execute(text('SELECT person_id,authorization_version FROM users WHERE id=:actor'),
                dict(actor=actor)).one()
            params = params | dict(actor=actor, person=user.person_id, version=user.authorization_version)
            result = db.scalar(CALL, params)
            assert result['kind'] == kind
            accepted.append(kind)
            for label, change in (('stale_version', dict(version=user.authorization_version+1)),
                                  ('wrong_person', dict(person=uuid4()))):
                rejected(db, params | change, 'current recovery identity invalid')
                denied.append(kind+':'+label)
            role = 'technician' if kind == 'apply' else 'provincial_manager' if kind == 'regional' else 'admin'
            for action in ('read', ACTIONS[kind]):
                for deny in (False, True):
                    patch = db.begin_nested()
                    try:
                        predicate = '''role_id IN (SELECT id FROM roles WHERE code=:role)
                            AND permission_id IN (SELECT id FROM permissions
                                WHERE resource='stock_operation' AND action=:action AND field_code='')'''
                        sql = ('UPDATE role_permissions SET effect=\'deny\' WHERE ' if deny
                            else 'DELETE FROM role_permissions WHERE ') + predicate
                        changed = db.execute(text(sql), dict(role=role, action=action))
                        assert changed.rowcount > 0
                        rejected(db, params, 'current seal read and action permission required')
                        denied.append(kind+':'+('deny_' if deny else 'missing_')+action)
                    finally:
                        patch.rollback()
            for label, sql in (
                ('disabled', "UPDATE users SET account_status='disabled' WHERE id=:actor"),
                ('expired_assignment', 'UPDATE role_assignments SET valid_to=clock_timestamp() '
                    'WHERE user_id=:actor AND valid_from<clock_timestamp()'),
            ):
                patch = db.begin_nested()
                try:
                    assert db.execute(text(sql), params).rowcount > 0
                    rejected(db, params, 'current recovery identity invalid' if label == 'disabled'
                        else 'current seal read and action permission required')
                    denied.append(kind+':'+label)
                finally:
                    patch.rollback()
            if kind in ('regional', 'headquarters'):
                parents = ['applicant_user_id'] + (['regional_user_id'] if kind == 'headquarters' else [])
                for field in parents:
                    prior = result[field]
                    user = db.execute(text('SELECT person_id,authorization_version FROM users WHERE id=:actor'),
                        dict(actor=prior)).one()
                    rejected(db, params | dict(actor=prior, person=user.person_id, version=user.authorization_version),
                        'seal reviewers must be independent')
                    denied.append(kind+':self_'+field)
            patch = db.begin_nested()
            try:
                db.execute(text('UPDATE custody_assignments SET valid_to=clock_timestamp() '
                    'WHERE location_id=CAST(:location AS uuid) AND valid_from<clock_timestamp() '
                    'AND (valid_to IS NULL OR valid_to>clock_timestamp())'), dict(location=result['location_id']))
                assert db.scalar(CALL, params) == result
                if kind in ('apply', 'regional', 'headquarters', 'execute'):
                    rejected(db, params | dict(owner=result['owner_org_id'], location=result['location_id'],
                        requester=result['requester_id']), 'unique current recovery custody required', PHYSICAL)
                separated.append(kind)
            finally:
                patch.rollback()
            assert facts(db, columns) == before
        private_params = params
    with api.connect() as db:
        try:
            db.execute(CALL, private_params)
        except DBAPIError as error:
            assert error.orig.sqlstate == '42501', error.orig
            db.rollback()
        else:
            raise AssertionError('private closure authority exposed to API')
    return dict(passed=True, currentSixKindAuthority=accepted, rejected=denied,
        expiredCustodyClosureAllowed=separated, physicalRecoveryStillRequiresCustody=True,
        allFactsUnchanged=True, privateApiCallDenied=True, sealWrites=False,
        deferredSealProof=False, concurrencyProven=False, productionAcceptance=False)
