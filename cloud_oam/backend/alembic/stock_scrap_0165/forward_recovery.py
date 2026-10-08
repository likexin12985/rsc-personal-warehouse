"""Dedicated recovery current proof without relaxing legacy admission/seals."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import runpy
from sqlalchemy import text

FOLDER = Path(__file__).resolve().parent
business = runpy.run_path(str(FOLDER / 'forward_business.py'))
history = business['history']


def patches():
    records = history['predecessors']()
    records.update({p['after']['proname']: p['after'] for p in business['patches']()})
    replace = history['replace_once']
    changes = {}
    name = 'rsc_check_loss_current_correction_0159'
    anchor = '    SELECT * INTO source FROM public.stock_accounts WHERE id=root.source_account_id;'
    changes[name] = replace(records[name]['prosrc'], anchor,
        "    IF required_action='reverse_loss' AND fact->>'scrap_line_id' IS NOT NULL THEN\n"
        "        PERFORM public.rsc_check_scrap_current_0165('inverse',checked_fact); RETURN;\n"
        "    ELSIF required_action='correct_loss' AND fact->>'disposition'='scrap' THEN\n"
        "        PERFORM public.rsc_check_scrap_current_0165('correction',checked_fact); RETURN;\n"
        "    END IF;\n" + anchor)
    name = 'rsc_check_loss_correction_admission_0159'
    anchor = ('    PERFORM public.rsc_assert_loss_correction_authority_0159(NEW.actor_user_id,NEW.authorization_version,\n'
        '        NEW.actor_person_id,owner_id,required_action);')
    changes[name] = replace(records[name]['prosrc'], anchor,
        "    IF TG_TABLE_NAME='stock_loss_disposition_reversals' AND to_jsonb(NEW)->>'scrap_line_id' IS NOT NULL THEN\n"
        "        PERFORM public.rsc_check_scrap_current_0165('inverse',NEW.id);\n"
        "    ELSE\n" + anchor + '\n    END IF;')
    name = 'rsc_dispatch_stock_return_0100'
    anchor = '        IF tx.reversed_transaction_id IS NOT NULL THEN'
    changes[name] = replace(records[name]['prosrc'], anchor,
        "        IF tx.source_document_type='stock_loss_disposition_reversal' AND EXISTS(\n"
        "            SELECT 1 FROM public.stock_loss_disposition_reversals inverse\n"
        "            JOIN public.stock_scrap_recovery_executions recovery ON recovery.id=inverse.scrap_recovery_execution_id\n"
        "                AND recovery.reversal_id=inverse.id AND recovery.scrap_line_id=inverse.scrap_line_id\n"
        "            WHERE inverse.posting_transaction_id=tx.id AND inverse.original_transaction_id=tx.reversed_transaction_id\n"
        "                AND tx.source_document_id=inverse.id::text) THEN\n"
        "            PERFORM public.rsc_check_scrap_current_0165('inverse',(SELECT id\n"
        "                FROM public.stock_loss_disposition_reversals WHERE posting_transaction_id=tx.id));\n"
        "            RETURN NULL;\n"
        "        END IF;\n" + anchor)
    result = []
    for name, body in changes.items():
        before = records[name]
        after = deepcopy(before)
        after['prosrc'] = body
        after['definition'] = replace(before['definition'], before['prosrc'], body)
        result.append(dict(before=before, after=after,
            beforeSha256=sha256(before['prosrc'].encode()).hexdigest(), afterSha256=sha256(body.encode()).hexdigest()))
    return result


def install(db):
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16:
        raise ValueError('0165 direct PostgreSQL16 migration owner required')
    if db.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')):
        raise ValueError('0165 migration owner must not be superuser')
    if db.scalar(text('SELECT version_num FROM public.alembic_version')) != '20261213_0164':
        raise ValueError('0165 exact predecessor revision required')
    for patch in history['patches']() + business['patches']():
        history['verify'](db, patch['after'])
    changes = patches()
    for patch in changes:
        history['verify'](db, patch['before'])
    for patch in changes:
        db.execute(text(patch['after']['definition']))
        history['verify'](db, patch['after'])
    return changes
