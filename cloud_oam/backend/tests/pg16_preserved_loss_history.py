"""Child program executed with only preserved 0164 source on PYTHONPATH.

The orchestrator passes its newly owned synthetic cluster. Capture committed
requests from unchanged legacy services; never fabricate business facts.
"""
from collections import Counter
from functools import wraps
import json
import os
from pathlib import Path
import runpy

runpy.run_path('backend/tests/conftest.py')

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from app.database_security import validate_production_database_security
from app.formal_access import load_formal_principal
from app.formal_services import stock_loss_disposition_commands as original_commands
from app.formal_services import stock_loss_disposition_recovery as original_recovery
from app.formal_services.stock_loss_corrections import bound_commands, bound_recovery
from pg16_stock_loss_sources_gate import run as opening
from pg16_loss_multigeneration_fixture import exercise
from pg16_loss_multigeneration_business import run as generations


def json_value(value):
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def main():
    tracking = os.environ['RSC_OWNED_HISTORY_TRACKING']
    if tracking not in ('quantity', 'serial'):
        raise ValueError('explicit tracking mode required')
    urls = json.loads(os.environ['RSC_OWNED_TEST_ENGINE_URLS'])
    if set(urls) != {'star_oam_migrator', 'star_oam_api', 'edge_inbox'}:
        raise ValueError('exact owned gate roles required')
    engines = {role: create_engine(url) for role, url in urls.items()}
    captured = []
    pending_key = 'rsc_preserved_history_commands'

    def capture(module, name, kind):
        actual = getattr(module, name)
        @wraps(actual)
        def wrapped(db, *, actor, request):
            result = actual(db, actor=actor, request=request)
            if db.get_bind().url.username == 'star_oam_api':
                db.info.setdefault(pending_key, []).append(dict(kind=kind,
                    actor=actor.user_id, commandType=type(request).__name__,
                    command=request.model_dump(mode='json'), result=json_value(result)))
            return result
        setattr(module, name, wrapped)

    @event.listens_for(Session, 'after_commit')
    def committed(db):
        if not db.in_nested_transaction():
            captured.extend(db.info.pop(pending_key, ()))

    @event.listens_for(Session, 'after_rollback')
    def rolled_back(db):
        db.info.pop(pending_key, None)

    try:
        for role, engine in engines.items():
            with engine.connect() as db:
                assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
                assert db.scalar(text('SELECT current_user')) == role
        with engines['star_oam_migrator'].connect() as db:
            assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261213_0164'
            assert not db.scalar(text('SELECT EXISTS(SELECT 1 FROM inventory_transactions)'))
        validate_production_database_security(engines['star_oam_api'],
            expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')
        capture(original_commands, 'execute_disposition', 'original')
        for method, kind in (('inverse', 'inverse'), ('approve', 'approval'), ('correct', 'correction'), ('seal', 'seal')):
            capture(bound_commands, method, kind)
        context = {}
        def business(value):
            context.update(value)
            return generations(value, exercise(value))
        proof = opening(engines, tracking=tracking, after_preview=business)
        assert Counter(row['kind'] for row in captured) == Counter(original=1, inverse=3, approval=3, correction=3, seal=1)
        # Save the exact old reader response for every committed command.
        from app.stock_loss_schemas import StockLossDispositionExecuteIn
        from app.formal_services.stock_loss_corrections.request_contracts import ReversalExecute, CorrectionApprove, CorrectionExecute
        types = {cls.__name__: cls for cls in (StockLossDispositionExecuteIn, ReversalExecute, CorrectionApprove, CorrectionExecute)}
        for row in captured:
            request = types[row['commandType']].model_validate(row['command'])
            with Session(engines['star_oam_api']) as db:
                db.execute(text('SET TRANSACTION READ ONLY'))
                actor = load_formal_principal(db, row['actor'])
                outcome = (original_recovery.lookup_disposition_request(db, actor=actor, request=request, flow='disposition')
                           if row['kind'] == 'original' else bound_recovery.lookup(db, actor=actor, request=request))
                row['lookup'] = json_value(outcome)
                assert outcome.get('retry_allowed', outcome.get('retry_permitted')) is False
        with engines['star_oam_migrator'].connect() as db:
            counts = {name: db.scalar(text('SELECT count(*) FROM public.' + name)) for name in (
                'stock_loss_dispositions', 'stock_loss_disposition_reversals', 'stock_loss_correction_decisions',
                'stock_loss_correction_executions', 'stock_loss_request_key_bindings', 'stock_loss_inverse_request_seals')}
        assert counts == dict(stock_loss_dispositions=1, stock_loss_disposition_reversals=3,
            stock_loss_correction_decisions=3, stock_loss_correction_executions=3,
            stock_loss_request_key_bindings=10, stock_loss_inverse_request_seals=1)
        output = dict(tracking=tracking, revision='20261213_0164', committedCommands=captured,
                      factCounts=counts, legacyBusinessProof=proof, productionAcceptance=False)
        Path(os.environ['RSC_OWNED_HISTORY_OUTPUT']).write_text(json.dumps(output, default=str, indent=2) + '\n')
        print('preserved 0164 genuine three-generation history and eleven exact requests PASS', flush=True)
    finally:
        for engine in engines.values():
            engine.dispose()


if __name__ == '__main__':
    main()
