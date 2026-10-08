"""Candidate original scrap admission; full request/lifecycle integration pending."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import runpy
from sqlalchemy import text

FOLDER = Path(__file__).resolve().parent
history = runpy.run_path(str(FOLDER / 'forward_history.py'))


def patches():
    records = history['predecessors']()
    replace = history['replace_once']
    changes = {}
    name = 'rsc_check_loss_disposition_0150'
    changes[name] = replace(records[name]['prosrc'], '    ELSIF require_current THEN',
        "    ELSIF root.disposition='scrap' AND require_current THEN\n"
        "        PERFORM public.rsc_check_scrap_current_0165('original',root.id);\n"
        '    ELSIF require_current THEN')
    name = 'rsc_dispatch_stock_return_0100'
    body = replace(records[name]['prosrc'], '    checked_order uuid;',
        '    checked_order uuid;\n    scrap record;')
    anchor = "    IF EXISTS(SELECT 1 FROM public.stock_operation_orders WHERE id=checked_order AND operation_type='loss_report') THEN"
    changes[name] = replace(body, anchor,
        "    IF EXISTS(SELECT 1 FROM public.stock_operation_orders WHERE id=checked_order AND operation_type='scrap') THEN\n"
        "        IF checked_cancel IS NOT NULL OR (SELECT count(*) FROM public.stock_scrap_lines WHERE operation_id=checked_order)<>1 THEN\n"
        "            RAISE EXCEPTION '0165 exact scrap order requires its dedicated fact' USING ERRCODE='23514'; END IF;\n"
        "        SELECT source_kind,CASE source_kind WHEN 'original' THEN root_disposition_id ELSE correction_execution_id END AS fact_id\n"
        "            INTO scrap FROM public.stock_scrap_lines WHERE operation_id=checked_order;\n"
        "        PERFORM public.rsc_check_scrap_current_0165(scrap.source_kind,scrap.fact_id);\n"
        "        RETURN NULL;\n"
        "    END IF;\n" + anchor)
    result = []
    for name, body in changes.items():
        before = records[name]
        after = deepcopy(before)
        after['prosrc'] = body
        after['definition'] = replace(before['definition'], before['prosrc'], body)
        result.append(dict(before=before, after=after,
            beforeSha256=sha256(before['prosrc'].encode()).hexdigest(),
            afterSha256=sha256(body.encode()).hexdigest()))
    return result


def install(db):
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16:
        raise ValueError('0165 direct PostgreSQL16 migration owner required')
    if db.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')):
        raise ValueError('0165 migration owner must not be superuser')
    if db.scalar(text('SELECT version_num FROM public.alembic_version')) != '20261213_0164':
        raise ValueError('0165 exact predecessor revision required')
    for patch in history['patches']():
        history['verify'](db, patch['after'])
    changes = patches()
    for patch in changes:
        history['verify'](db, patch['before'])
    db.execute(text((FOLDER / 'request_coordinates.sql').read_text()))
    db.execute(text((FOLDER / 'current_stock.sql').read_text()))
    for patch in changes:
        db.execute(text(patch['after']['definition']))
        history['verify'](db, patch['after'])
    return changes
