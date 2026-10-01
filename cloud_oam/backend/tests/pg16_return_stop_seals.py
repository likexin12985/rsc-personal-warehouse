"""Original return inverse closure, key exclusion, native provenance and recovery."""
from uuid import uuid4
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.foundation_models import Permission,Role,RolePermission
from app.models import User
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import stock_loss_disposition_recovery
from app.formal_services.stock_loss_corrections import bound_commands,sealed_inverse
from app.stock_loss_return_stop_models import StockLossReturnStop as Stop
from app.formal_services.stock_loss_corrections.seal_model import StockLossInverseRequestSeal as Seal


def snapshot(owner):
    from pg16_stock_loss_submit_gate import snapshot as stock
    value=stock(owner)
    with owner.connect() as db:
        for name in ('stock_loss_return_stops','stock_loss_disposition_reversals','stock_loss_inverse_request_seals',
                     'stock_loss_request_key_bindings','audit_events','audit_chain_heads'):
            value[name]=tuple(db.scalars(text('SELECT to_jsonb(t)::text FROM public.'+name+' t ORDER BY to_jsonb(t)::text')))
    return value


def read(api,context,request):
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        answer=sealed_inverse.lookup_unshipped_return(db,actor=load_formal_principal(db,context['admin_id']),request=request)
        db.commit();return answer


def exercise(context,command,original,original_result,root_id):
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    before=snapshot(owner)
    # The actual schema owner can stage a same-transaction late identity edit;
    # the deferred business guard must reject the seal and that edit together.
    with Session(owner) as db:
        bound_commands.seal_return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=command)
        db.get(User,context['admin_id']).authorization_version+=1
        try:db.commit()
        except DBAPIError as error:
            assert error.orig.sqlstate=='23514';db.rollback()
        else:raise AssertionError('late authority change committed a return seal')
    assert snapshot(owner)==before
    with Session(api) as db:
        sealed=bound_commands.seal_return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=command)
        assert sealed['request_state']=='sealed' and not sealed['retry_allowed']
        db.commit()
        assert not tuple(db.scalars(select(Stop.id)))
        assert len(tuple(db.scalars(select(Seal.id))))==1
    stable=snapshot(owner)
    for table in ('inventory_transactions','inventory_movements','stock_balances','serial_current_positions'):
        assert stable[table]==before[table],table
    context['verify_retention']('seal-only')
    assert read(api,context,command)==sealed
    with Session(api) as db:
        assert bound_commands.seal_return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=command)==sealed
        db.commit()
    assert snapshot(owner)==stable
    for changes in ({},{'request_id':uuid4().hex},{'idempotency_key':uuid4().hex}):
        with Session(api) as db:
            try:bound_commands.return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=command.model_copy(update=changes))
            except InventoryReadError as error:
                assert error.code=='loss_inverse_request_sealed';db.rollback()
            else:raise AssertionError('sealed request alias executed')
        assert snapshot(owner)==stable
    print('Native return seal COMMIT, no stock change, repeat, late-identity rollback and three aliases PASS',flush=True)
    fresh=command.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    with Session(api) as db:
        posted=bound_commands.return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=fresh)
        db.commit()
    stable=snapshot(owner)
    assert read(api,context,command)==sealed
    context['verify_retention']('stop-posted')
    assert read(api,context,fresh)['result']==posted
    with Session(api) as db:
        found=bound_commands.seal_return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=fresh)
        assert found['request_state']=='found' and found['result']==posted;db.commit()
    assert snapshot(owner)==stable
    with Session(owner) as db:
        permission=db.scalar(select(Permission).where(Permission.resource=='stock_operation',Permission.action=='reverse_loss',Permission.field_code==''))
        role=db.scalar(select(Role).where(Role.code=='admin'))
        db.scalars(select(RolePermission).where(RolePermission.role_id==role.id,RolePermission.permission_id==permission.id)).one().effect='deny'
        db.commit()
    stable=snapshot(owner)
    assert read(api,context,command)==sealed and read(api,context,fresh)['result']==posted
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        recovered=stock_loss_disposition_recovery.lookup_disposition_request(db,actor=load_formal_principal(db,context['admin_id']),request=original,flow='return')
        assert recovered['lookup_status']=='found' and recovered['disposition']==original_result;db.commit()
    with Session(api) as db:
        try:bound_commands.seal_return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=command)
        except InventoryReadError as error:
            assert error.status_code==403;db.rollback()
        else:raise AssertionError('revoked writer sealed request')
    assert snapshot(owner)==stable
    print('Native independent new inverse, retained old seal, posted-result recovery and revoke/read separation PASS',flush=True)
    return dict(passed=True,tracking=context['tracking'],apiSealCommit=True,stockNeutralSeal=True,
        nativeSealBinding=True,permanentAliasesRejected=3,lateAuthorityRollback=True,
        explicitNewRequestPosts=True,oldSealPreservedAfterStop=True,postedResultNotResealed=True,
        readOnlyRecoveryAfterRevoke=True,originalReturnRecovery=True,formalMigrationInstalled=True,sealOnlyDowngradeRejected=True,stopHistoryDowngradeRejected=True,
        nativeSealExecuteRace=False,productionAcceptance=False)
