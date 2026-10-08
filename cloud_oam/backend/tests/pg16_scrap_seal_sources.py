"""Private SQL source proof over actual committed scrap/recovery API history."""
from copy import deepcopy
import json
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.stock_scrap_schemas import ScrapExecute
from app.formal_services.stock_scrap.request_lookup import canonical
from app.formal_services.stock_scrap.recovery_facts import intent, CONTRACTS
from pg16_stock_scrap_structure_gate import original_columns, facts

CALL = text('SELECT public.rsc_prepare_scrap_seal_source_0165(:kind,CAST(:command AS jsonb),:key)')


def arguments(command):
    if type(command) is ScrapExecute:
        return dict(kind=command.source.kind, command=json.dumps(canonical(command), ensure_ascii=False), key=command.idempotency_key)
    stage = next((stage for stage, model in CONTRACTS.items() if type(command) is model), 'execute')
    return dict(kind=stage, command=json.dumps(intent(command), ensure_ascii=False), key=command.idempotency_key)


def rejected(db, args):
    savepoint = db.begin_nested()
    try:
        db.execute(CALL, args)
    except DBAPIError as error:
        assert error.orig.sqlstate == '23514', error.orig
    else:
        raise AssertionError('invalid persisted seal source accepted')
    finally:
        savepoint.rollback()


def before_original(context, command):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator','star_oam_api'))
    args = arguments(command)
    with owner.begin() as db:
        columns = original_columns(db)
        before = facts(db, columns)
        assert db.scalar(text('SELECT count(*) FROM stock_loss_dispositions')) == 0
        result = db.scalar(CALL, args)
        assert result['kind'] == 'original' and result['root_disposition_id'] is None
        assert result['original_decision_id'] == str(command.source.headquarters_decision_id)
        assert result['plan_hash'] == command.expected_plan_hash
        assert facts(db, columns) == before
    with api.connect() as db:
        try:
            db.execute(CALL, args)
        except DBAPIError as error:
            assert error.orig.sqlstate == '42501'
            db.rollback()
        else:
            raise AssertionError('private source proof exposed to API')
    return dict(passed=True, originalWithoutRoot=True, allFactsUnchanged=True, privateApiCallDenied=True)


def after_history(context):
    owner = context['engines']['star_oam_migrator']
    commands = [c for _, c, _ in context['scrap_lookup_cases'] + context['scrap_recovery_lookup_cases']]
    count = refused = 0
    stages = set()
    with owner.begin() as db:
        columns = original_columns(db)
        before = facts(db, columns)
        for command in commands:
            args = arguments(command)
            result = db.scalar(CALL, args)
            document = json.loads(args['command'])
            source = document['intent']['source'] if type(command) is ScrapExecute else document['source']
            assert result['command_jsonb'] == document and result['kind'] == args['kind']
            assert result['plan_hash'] == getattr(command, 'expected_plan_hash', None)
            if args['kind'] == 'original':
                assert result['root_disposition_id'] is None
                assert result['original_decision_id'] == source['headquarters_decision_id']
            elif args['kind'] == 'correction':
                assert result['root_disposition_id'] == source['root_disposition_id']
                assert result['reversal_id'] == source['reversal_id']
                assert result['correction_decision_id'] == source['correction_decision_id']
            else:
                assert result['scrap_line_id'] == source['scrap_line_id']
                if args['kind'] != 'apply':
                    assert result['recovery_request_id'] == str(command.recovery_request_id)
                if args['kind'] == 'headquarters':
                    assert result['regional_review_id'] == str(command.regional_review_id)
                    assert result['regional_decision'] == 'verified'
                if args['kind'] == 'execute':
                    assert result['headquarters_review_id'] == str(command.headquarters_review_id)
                    assert result['headquarters_decision'] == 'approve' and result['regional_review_id'] is None
            count += 1
            stages.add(args['kind'])
            # Every actual source hash/ID must match, including retained old
            # generations whose stock/approval stage has subsequently changed.
            paths = [('source', key) for key in source if key != 'kind']
            paths += [('command', key) for key in document if key in (
                'recovery_request_id','expected_request_hash','regional_review_id','expected_regional_hash',
                'headquarters_review_id','expected_headquarters_hash')]
            for where, field in paths:
                changed = deepcopy(document)
                target = changed if where == 'command' else (changed['intent']['source'] if type(command) is ScrapExecute else changed['source'])
                target[field] = str(uuid4()) if field.endswith('_id') else '0'*64
                rejected(db, args | {'command':json.dumps(changed, ensure_ascii=False)})
                refused += 1
        assert stages == {'original','correction','apply','regional','headquarters','execute'}
        # A real but unrelated parent's matching hash cannot bypass the FK-like
        # source relation. Two actual generations provide independent parents.
        recovery = context['scrap_recovery_lookup_cases']
        for kind, parent_stage, id_field, hash_field in (
            ('regional','apply','recovery_request_id','expected_request_hash'),
            ('headquarters','regional','regional_review_id','expected_regional_hash'),
            ('execute','headquarters','headquarters_review_id','expected_headquarters_hash')):
            targets = [c for _,c,_ in recovery if arguments(c)['kind']==kind]
            parents = [(c,r) for _,c,r in recovery if arguments(c)['kind']==parent_stage]
            assert len(targets)==len(parents)==2
            args = arguments(targets[0]); changed = json.loads(args['command'])
            changed[id_field] = parents[1][1]['fact_id']
            changed[hash_field] = parents[1][1]['request_hash']
            rejected(db, args | {'command':json.dumps(changed, ensure_ascii=False)})
            refused += 1
        assert facts(db, columns) == before
    return dict(passed=True, exactHistoricalSources=count, wrongSourceBindingsRejected=refused,
        kinds=sorted(stages), allFactsUnchanged=True, closurePermissionProven=False, absenceProven=False,
        registrarImplemented=False, productionAcceptance=False)
