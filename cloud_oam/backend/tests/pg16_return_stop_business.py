"""Native malformed-transaction rollback and immutable stop proof."""
from uuid import UUID
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.stock_loss_return_stop_models import StockLossReturnStop as Stop
from app.formal_access import load_formal_principal
from app.foundation_models import Permission,Role,RolePermission
from app.inventory_models import CustodyAssignment,StockLocation,StockBalance
from app.stock_operation_models import StockOperationOrder,StockLossDisposition,StockLossHeadquartersDecision,StockLossHeadquartersReview
from app.stock_loss_return_schemas import StockLossReturnExecuteIn
from app.formal_services import stock_loss_return_plan,stock_loss_return_commands,stock_loss_disposition_recovery
from app.formal_services.stock_loss_corrections import reversal_stock,inverse_posting,inverse_recovery,return_stop,bound_commands,sealed_inverse
from app.formal_services.stock_loss_corrections.request_contracts import ReversalPreview,ReversalExecute
from pg16_stock_loss_sources_gate import run as source_fixture
from pg16_stock_loss_return_preview_gate import run as return_fixture


def exercise(context,command,original,original_result,root_id,source_id,target_id,amount):
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    from pg16_return_stop_negative import exercise as negative_cases
    rejected = negative_cases(context, command)
    with Session(api) as db:
        before_source=db.get(StockBalance,source_id).quantity;before_target=db.get(StockBalance,target_id).quantity
        result=inverse_posting.execute_unshipped_return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=command)
        bound_commands.register(db,kind='inverse',identifier=result['reversal_id'],client_key=command.idempotency_key)
        db.commit()
        assert db.get(StockBalance,source_id).quantity==before_source-amount
        assert db.get(StockBalance,target_id).quantity==before_target+amount
    print('PG16 exact original return stop/inverse API COMMIT and request binding PASS',flush=True)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor=load_formal_principal(db,context['admin_id'])
        answer=inverse_recovery.lookup_unshipped_return_inverse(db,actor=actor,request=command)
        assert answer['request_state']=='found' and answer['result']==result
        recovered=stock_loss_disposition_recovery.lookup_disposition_request(db,actor=actor,request=original,flow='return')
        assert recovered['lookup_status']=='found' and recovered['disposition']==original_result
        db.commit()
    with owner.begin() as db:
        graph=db.scalar(text('SELECT public.rsc_check_loss_history_graph_0159(:id)'),dict(id=root_id))
        assert graph['verified_inverse_ids']==[result['reversal_id']]
    for sql in ('UPDATE public.stock_loss_return_stops SET evidence_fingerprint=repeat(\'f\',64)',
                'DELETE FROM public.stock_loss_return_stops','TRUNCATE public.stock_loss_return_stops'):
        with owner.connect() as db:
            try: db.execute(text(sql));db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate=='23514';db.rollback()
            else: raise AssertionError('stop mutation accepted')
    with api.connect() as db:
        try:db.execute(text('SELECT public.rsc_check_loss_return_stop_0163(:id)'),dict(id=UUID(result['reversal_id'])))
        except DBAPIError as error:
            assert error.orig.sqlstate=='42501';db.rollback()
        else:raise AssertionError('private stop proof exposed')
    return dict(passed=True,tracking=context['tracking'],apiStopInverseCommit=True,
        nativeRequestBinding=True,portableBoundaryMatches=True,readOnlyRecovery=True,originalRequestRecovery=True,
        immutableStop=True,privateGuardDenied=True,negativeCommitCases=rejected,formalMigrationInstalled=True,concurrentRaceTested=False,productionAcceptance=False)
