"""Additional requester/headquarters matrix; staged until v1 source freeze ends."""
import cProfile
import json
import pstats

import pytest
from sqlalchemy import text

from app.formal_access import load_formal_principal
from app.foundation_models import Person
from app.models import User
from app.formal_services.stock_loss_corrections import return_condition_authority as authority
from app.formal_services.stock_loss_corrections import return_condition_decisions as decisions
from app.formal_services.stock_loss_corrections import return_condition_decision_seal_admission as subject
from test_formal_access import assign
from test_return_condition_decisions import start, make_request
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived,
    ready, parcel, acceptance, prepared, regional_opening, context, regional_source, ERRORS,
)

pytestmark = pytest.mark.parametrize('stock,command_name',
    [('quantity', 'quantity_command'), ('serial', 'serial_command')], indirect=['stock'])


def read_only_prepare(db, actor, command, *, profile_directory=None):
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        if profile_directory is None:
            result = subject.authorize_absence_seal(db, actor=actor, request=command)
        else:
            with cProfile.Profile() as profiler:
                result = subject.authorize_absence_seal(db, actor=actor, request=command)
            stats = pstats.Stats(profiler)
            rows = [dict(file=filename.split('/backend/', 1)[-1], line=line, function=function,
                primitive_calls=values[0], total_calls=values[1], own_seconds=values[2],
                cumulative_seconds=values[3])
                for (filename, line, function), values in stats.stats.items()
                if '/app/formal_services/' in filename]
            rows.sort(key=lambda row: row['cumulative_seconds'], reverse=True)
            target = profile_directory / 'headquarters-closure-profile.json'
            target.write_text(json.dumps(dict(
                scope='single synthetic SQLite preparation; not PostgreSQL or production performance',
                cumulativeTimesOverlap=True, functions=rows), ensure_ascii=False, indent=2) + '\n')
            print('condition headquarters closure profile:', target, flush=True)
        assert not result.absence_sealed and not result.retry_allowed
        assert not result.current_stock_verified and not result.original_preflight_verified
        assert not db.new and not db.dirty and snapshot(db) == before
        return result
    finally:
        db.execute(text('PRAGMA query_only=OFF'))


def test_requester_and_headquarters_closure_retain_independence_after_state_advances(
        db, regional_source, request, command_name, tmp_path):
    c = regional_source
    first = start(db, c, request.getfixturevalue(command_name)); db.commit()
    requester, old_withdraw = make_request(db, c.actor, first, 'withdraw')
    assert read_only_prepare(db, requester, old_withdraw).action == 'withdraw_return_condition'
    before = snapshot(db)
    with pytest.raises(ERRORS):
        subject.authorize_absence_seal(db,
            actor=load_formal_principal(db, c.reviewer.user_id), request=old_withdraw)
    assert snapshot(db) == before

    reviewer, verify = make_request(db, c.reviewer, first, 'verify_region')
    checked = decisions.decide(db, actor=reviewer, request=verify); db.commit()
    headquarters, missing_approval = make_request(db, c.hq, checked, 'approve_hq')
    assert read_only_prepare(db, headquarters, missing_approval,
        profile_directory=tmp_path).action == 'review_return_condition_headquarters'
    _, actual_approval = make_request(db, c.hq, checked, 'approve_hq')
    approved = decisions.decide(db, actor=headquarters, request=actual_approval); db.commit()
    assert approved['status'] == 'approved'
    # Closing an old missing coordinate must not be confused with executing it.
    with pytest.raises(ERRORS):
        authority.authorize_action(db, actor=requester, case_id=old_withdraw.case_id,
            expected_event_id=old_withdraw.expected_event_id, kind='withdraw')
    read_only_prepare(db, requester, old_withdraw)
    read_only_prepare(db, headquarters, missing_approval)

    # A real transfer to headquarters plus a valid current admin grant still
    # cannot erase the original person's requester/regional-review identity.
    before = snapshot(db)
    for original_actor in (c.actor, c.reviewer):
        headquarters_org = db.get(Person, c.hq.person_id).organization_id
        db.get(Person, original_actor.person_id).organization_id = headquarters_org
        assign(db, db.get(User, original_actor.user_id), c.hq_role,
            scope_type='national', scope_id='*')
        db.flush()
        current = load_formal_principal(db, original_actor.user_id)
        with pytest.raises(ERRORS) as error:
            subject.authorize_absence_seal(db, actor=current, request=missing_approval)
        assert error.value.code == 'return_condition_self_review_forbidden'
        db.rollback()
        assert snapshot(db) == before
