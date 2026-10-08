"""Actual independent recovery approvals and inverse; no synthetic ledger import."""
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.stock_scrap_recovery_schemas import (
    ScrapRecoveryApply, ScrapRecoveryRegionalReview, ScrapRecoveryHeadquartersReview,
    ScrapRecoveryPreview, ScrapRecoveryExecute,
)
from app.formal_services.stock_scrap import (
    recovery_approval, recovery_authority, recovery_execution, recovery_plan, bound_commands,
)
from app.formal_services.stock_loss_corrections.bound_commands import register
from app.formal_services.inventory_posting import _storage_hash
from pg16_stock_scrap_structure_gate import original_columns, facts
from test_stock_scrap_plan import _upload
from pg16_scrap_raw_bindings import reject_unbound


def coordinates():
    return dict(request_id=uuid4().hex, idempotency_key=uuid4().hex)


def inventory(owner):
    with owner.connect() as db:
        return {name: db.execute(text('SELECT to_jsonb(t) FROM public.' + name + ' t ORDER BY ' + key)).scalars().all()
            for name, key in (('inventory_transactions', 'id'), ('inventory_movements', 'id'),
                ('stock_balances', 'stock_account_id'), ('inventory_serials', 'id'),
                ('serial_current_positions', 'serial_id'))}


def exercise(context, scrap):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    with Session(owner) as db:
        # Use the exact already independent regional loss reviewer. No inferred
        # person match or real identity is involved in this disposable fixture.
        regional_id = db.execute(text('''SELECT regional.actor_user_id
            FROM stock_loss_dispositions root
            JOIN stock_loss_headquarters_decisions decision ON decision.id=root.headquarters_decision_id
            JOIN stock_loss_headquarters_reviews headquarters ON headquarters.id=decision.review_id
            JOIN stock_loss_regional_reviews regional ON regional.id=headquarters.regional_review_id
            WHERE root.id=:id'''), dict(id=UUID(scrap['root_disposition_id']))).scalar_one()
        roles = {r.code: r for r in db.scalars(select(Role))}
        for stage, action in recovery_authority.ACTIONS.items():
            role = roles['technician' if stage == 'apply' else 'provincial_manager' if stage == 'regional' else 'admin']
            permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
                Permission.action == action, Permission.field_code == ''))
            if permission is None:
                permission = Permission(resource='stock_operation', action=action, field_code='', description='Synthetic native recovery gate')
                db.add(permission); db.flush()
            if db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
                    RolePermission.permission_id == permission.id)) is None:
                db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
        # A dedicated recovery must not borrow the generic loss inverse grant.
        legacy = db.scalar(select(RolePermission).join(Permission).where(
            RolePermission.role_id == roles['admin'].id, Permission.resource == 'stock_operation', Permission.action == 'reverse_loss'))
        if legacy is not None:
            db.delete(legacy)
        db.commit()
        assert db.scalar(select(RolePermission).join(Permission).where(
            RolePermission.role_id == roles['admin'].id, Permission.resource == 'stock_operation', Permission.action == 'reverse_loss')) is None
    actors = dict(apply=context['engineer_id'], regional=regional_id, headquarters=context['admin_id'])
    assert len(set(actors.values())) == 3

    review_rejections = []

    def submit(stage, command):
        from pg16_scrap_closed_execution import before_stage
        before_stage(context, actor_id=actors[stage], command=command)
        reused = []
        if stage == 'apply':
            # First source is in the new registry; second is a real legacy
            # correction approval retained in the old registry.
            key = context['scrap_lookup_cases'][0][1].idempotency_key
            reused = [('original_key_reuse', key)]
            if 'legacy_approval_key' in context:
                reused.append(('legacy_approval_key_reuse', context['legacy_approval_key']))
        review_rejections.extend(reject_unbound(context, actor_id=actors[stage], kind=stage,
            command=command, service=recovery_approval.submit, reused_keys=reused))
        from pg16_scrap_seal_races import commit_write
        result = commit_write(context, actor_id=actors[stage], command=command)
        assert result['stock_effect'] == 'none'
        assert inventory(owner) == before, 'approval changed inventory: ' + stage
        context.setdefault('scrap_recovery_lookup_cases', []).append((actors[stage], command, result))
        print('actual recovery ' + stage + ' API COMMIT; inventory unchanged PASS', flush=True)
        return result

    with Session(api) as db:
        file = _upload(db, load_formal_principal(db, actors['apply']))
        application = ScrapRecoveryApply(action='apply_scrap_recovery',
            source=dict(scrap_line_id=scrap['scrap_line_id'], expected_scrap_request_hash=scrap['request_hash']),
            evidence_file_ids=(file.id,), reason='Synthetic found original scrapped stock', **coordinates())
    before = inventory(owner)
    applied = submit('apply', application)
    binding = dict(source=application.source, recovery_request_id=applied['fact_id'], expected_request_hash=applied['request_hash'])
    regional = submit('regional', ScrapRecoveryRegionalReview(**binding, action='review_scrap_recovery_region',
        decision='verified', reason='Synthetic independent physical verification', **coordinates()))
    approved = submit('headquarters', ScrapRecoveryHeadquartersReview(**binding,
        action='review_scrap_recovery_headquarters', regional_review_id=regional['fact_id'],
        expected_regional_hash=regional['request_hash'], decision='approve',
        reason='Synthetic independent recovery approval', **coordinates()))
    with Session(api) as db:
        actor = load_formal_principal(db, actors['headquarters'])
        preview = ScrapRecoveryPreview(**binding, headquarters_review_id=approved['fact_id'],
            expected_headquarters_hash=approved['request_hash'], reason='Restore only the exact original frozen share')
        prepared = recovery_plan.prepare(db, actor=actor, request=preview)
        command = ScrapRecoveryExecute(**preview.model_dump(), action='execute_scrap_recovery',
            expected_plan_hash=prepared.plan_hash, **coordinates())
    from pg16_scrap_closed_execution import before_stage
    before_stage(context, actor_id=actors['headquarters'], command=command)
    with owner.connect() as db:
        columns = original_columns(db)
        before_execution = facts(db, columns)
    rejected = []
    for case in ('missing_binding', 'wrong_client_key'):
        phase = 'service'
        with Session(api) as db:
            try:
                result = recovery_execution.execute(db, actor=load_formal_principal(db, actors['headquarters']), request=command)
                if case == 'wrong_client_key':
                    phase = 'register'
                    register(db, kind='inverse', identifier=result['reversal_id'], client_key=uuid4().hex)
                phase = 'commit'
                db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate == '23514', (case, str(error.orig))
                expected = 'exact database-owned request binding required' if case == 'missing_binding' else 'client key does not prove stored action hashes'
                assert expected in error.orig.diag.message_primary, (case, str(error.orig))
                assert phase == ('commit' if case == 'missing_binding' else 'register')
                db.rollback()
                rejected.append(dict(case=case, phase=phase, sqlstate=error.orig.sqlstate))
            else:
                raise AssertionError('invalid recovery binding accepted: ' + case)
        with owner.connect() as db:
            assert facts(db, columns) == before_execution, 'rejected binding changed retained database facts'
    print('actual recovery missing/wrong binding and full rollback PASS', flush=True)
    from pg16_scrap_recovery_late_authority import exercise as late_authority
    late_rejections = late_authority(context, command=command, prepared=prepared)
    try:
        from pg16_scrap_seal_races import commit_write
        result = commit_write(context, actor_id=actors['headquarters'], command=command)
    except DBAPIError:
        with owner.connect() as db:
            assert facts(db, columns) == before_execution, 'failed recovery changed retained database facts'
        print('failed actual recovery transaction fully rolled back PASS', flush=True)
        raise
    with owner.begin() as db:
        binding = db.execute(text('SELECT * FROM stock_loss_request_key_bindings WHERE fact_id=:id'),
            dict(id=UUID(result['reversal_id']))).mappings().one()
        for column, action in (('reversal_key_hash', 'reverse_loss'), ('approval_key_hash', 'approve_loss_correction'),
                ('correction_key_hash', 'correct_loss')):
            assert binding[column] == _storage_hash('stock-loss:' + action + ':' + command.idempotency_key)
        assert binding['recovery_key_hash'] == _storage_hash('stock-scrap-recovery:' + command.idempotency_key)
        assert binding['scrap_key_hash'] is None
        target = UUID(prepared.document['target_account_id'])
        assert db.scalar(text('SELECT quantity FROM stock_balances WHERE stock_account_id=:id'), dict(id=target)) == Decimal(scrap['quantity'])
        db.execute(text('SELECT public.rsc_check_loss_history_graph_0159(:id)'), dict(id=UUID(scrap['root_disposition_id'])))
        for value in prepared.document['serial_ids']:
            identifier = UUID(value)
            state = db.execute(text('SELECT s.lifecycle_status,p.stock_account_id,p.last_movement_id FROM inventory_serials s '
                'JOIN serial_current_positions p ON p.serial_id=s.id WHERE s.id=:id'), dict(id=identifier)).one()
            assert tuple(state) == ('active', target, UUID(result['posting_movement_id']))
            db.execute(text('SELECT public.rsc_check_serial_lifecycle_0092(:id)'), dict(id=identifier))
    print('actual recovery API COMMIT and exact frozen/SN restoration PASS', flush=True)
    context.setdefault('scrap_recovery_lookup_cases', []).append((actors['headquarters'], command, result))
    return dict(recoveryApiCommitVerified=True, independentRecoveryApprovalsCommitted=True,
        recoveryResult=result, approvalInventoryUnchanged=True, bindingRejections=rejected,
        reviewBindingRejections=review_rejections,
        lateRecoveryAuthorityCommitRollbacks=late_rejections,
        legacyKeyAliasesPreserved=True, legacyReversePermissionAbsent=True)
