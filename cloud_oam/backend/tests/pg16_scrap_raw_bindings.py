"""Real API missing/wrong raw-key rejection and cross-registry ownership."""
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.formal_services.stock_scrap import bound_commands
from pg16_stock_scrap_structure_gate import original_columns, facts


def reject_unbound(context, *, actor_id, kind, command, service, reused_keys=()):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator','star_oam_api'))
    with owner.connect() as db:
        columns = original_columns(db)
        before = facts(db, columns)
    cases = [('missing_binding', None), ('wrong_client_key', None), *reused_keys]
    rejected = []
    for case, reused in cases:
        phase = 'service'
        with Session(api) as db:
            try:
                value = command.model_copy(update={'idempotency_key': reused}) if reused else command
                result = service(db, actor=load_formal_principal(db, actor_id), request=value)
                if case != 'missing_binding':
                    phase = 'register'
                    bound_commands.register_scrap(db, kind=kind,
                        identifier=result['root_disposition_id'] if kind == 'original' else result['fact_id'],
                        client_key=uuid4().hex if case == 'wrong_client_key' else value.idempotency_key)
                phase = 'commit'
                db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate == ('23505' if case == 'original_key_reuse' else '23514'), (case, phase, str(error.orig))
                expected = ('exact database-owned scrap binding required' if case == 'missing_binding'
                    else 'client key does not prove stored scrap action hash' if case == 'wrong_client_key'
                    else 'raw request key reused across registries')
                if case == 'original_key_reuse':
                    assert error.orig.diag.constraint_name.startswith('stock_scrap_request_key_bindings_')
                else:
                    assert expected in error.orig.diag.message_primary, (case, phase, str(error.orig))
                assert phase == ('commit' if case == 'missing_binding' else 'register'), (case, phase)
                db.rollback()
                rejected.append(dict(case=case,kind=kind,phase=phase,sqlstate=error.orig.sqlstate))
            else:
                raise AssertionError('unbound or reused raw key committed: '+case)
        with owner.connect() as db:
            assert facts(db, columns) == before, 'binding rejection changed database: '+case
    return rejected


def privileges(engines):
    rejected = []
    for sql in ('INSERT INTO public.stock_scrap_request_key_bindings DEFAULT VALUES',
        'UPDATE public.stock_scrap_request_key_bindings SET request_id=request_id',
        'DELETE FROM public.stock_scrap_request_key_bindings', 'TRUNCATE public.stock_scrap_request_key_bindings',
        "SELECT public.rsc_scrap_binding_fact_0165('original',NULL::uuid)",
        "SELECT public.rsc_check_scrap_request_binding_0165('original',NULL::uuid)"):
        with engines['star_oam_api'].connect() as db:
            try:
                db.execute(text(sql))
            except DBAPIError as error:
                assert error.orig.sqlstate == '42501', str(error.orig)
                db.rollback()
                rejected.append(sql.split()[0])
            else:
                raise AssertionError('API directly controlled owned request provenance')
    return dict(apiDirectWritesAndPrivateCallsDenied=len(rejected))


def immutable(engines):
    owner = engines['star_oam_migrator']
    with owner.connect() as db:
        columns = original_columns(db)
        before = facts(db, columns)
        assert db.scalar(text('SELECT count(*) FROM stock_scrap_request_key_bindings')) == 7
    for sql in ('UPDATE public.stock_scrap_request_key_bindings SET request_id=request_id',
        'DELETE FROM public.stock_scrap_request_key_bindings','TRUNCATE public.stock_scrap_request_key_bindings'):
        with owner.connect() as db:
            try:
                db.execute(text(sql))
            except DBAPIError as error:
                assert error.orig.sqlstate == '23514', str(error.orig)
                assert 'rsc_scrap_facts_append_only_0165' in (error.orig.diag.context or '')
                db.rollback()
            else:
                raise AssertionError('immutable request binding changed')
        with owner.connect() as db:
            assert facts(db, columns) == before
    return dict(newBindingsRetained=7,ownerMutationsRejected=3)
