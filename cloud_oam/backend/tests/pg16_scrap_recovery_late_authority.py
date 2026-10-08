"""Complete recovery service+registrar, followed by owner-injected late changes.

These are actual deferred COMMIT checks, not an API ability to edit identity or
custody. Separate concurrent revocation and natural expiry still need coverage.
"""
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.formal_services.stock_scrap import bound_commands
from pg16_stock_scrap_structure_gate import original_columns, facts


def exercise(context, *, command, prepared):
    owner = context['engines']['star_oam_migrator']
    with owner.connect() as db:
        columns = original_columns(db)
        before = facts(db, columns)
    rejected = []
    for case in ('actor_version','custody_ends'):
        phase = 'service'
        with Session(owner) as db:
            try:
                actor = load_formal_principal(db, context['admin_id'])
                bound_commands.recover(db, actor=actor, request=command)
                if case == 'actor_version':
                    db.execute(text('UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id'),
                        dict(id=actor.user_id))
                else:
                    db.execute(text('UPDATE custody_assignments SET valid_to=clock_timestamp() WHERE id=:id'),
                        dict(id=UUID(prepared.document['custody_assignment_id'])))
                phase = 'commit'
                db.commit()
            except DBAPIError as error:
                assert phase == 'commit' and error.orig.sqlstate == '23514', (case, phase, str(error.orig))
                assert 'rsc_assert_scrap_recovery_authority_0165' in (error.orig.diag.context or ''), (case, str(error.orig))
                rejected.append(dict(case=case,phase=phase,sqlstate=error.orig.sqlstate,
                    message=error.orig.diag.message_primary))
                db.rollback()
            else:
                raise AssertionError('late recovery authority change committed: '+case)
        with owner.connect() as db:
            assert facts(db, columns) == before, 'rejected recovery changed database: '+case
    print('actual physical recovery late version/custody COMMIT rejection and full rollback PASS', flush=True)
    return rejected
