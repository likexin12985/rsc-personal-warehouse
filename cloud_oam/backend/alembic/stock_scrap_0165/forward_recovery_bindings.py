"""Exact recovery and corrected scrap key provenance; legacy aliases retain meaning.

This is not the full new approval/original/corrected-scrap lookup/seal system.
It composes a real recovery inverse or corrected scrap with the DB-owned registry.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import runpy
from sqlalchemy import text

FOLDER = Path(__file__).resolve().parent
recovery = runpy.run_path(str(FOLDER / 'forward_recovery.py'))
history = recovery['history']


def patches():
    records = history['predecessors']()
    replace = history['replace_once']
    changes = {}
    name = 'rsc_register_loss_request_binding_0159'
    body = replace(records[name]['prosrc'], '    prior public.stock_loss_request_key_bindings%ROWTYPE;',
        '    prior public.stock_loss_request_key_bindings%ROWTYPE; recovery_hash text; scrap_hash text;')
    anchor = "    IF fact IS NULL OR fact->>'idempotency_key_hash' IS DISTINCT FROM expected"
    body = replace(body, anchor,
        "    IF checked_kind='inverse' AND fact->>'scrap_line_id' IS NOT NULL THEN\n"
        "        IF fact#>>'{command_jsonb,action}' IS DISTINCT FROM 'execute_scrap_recovery' THEN\n"
        "            RAISE EXCEPTION '0165 exact recovery binding action required' USING ERRCODE='23514'; END IF;\n"
        "        recovery_hash:=encode(sha256(convert_to('cloud_oam.inventory.idempotency.v1','UTF8')||decode('00','hex')\n"
        "            ||convert_to('stock-scrap-recovery:'||client_key,'UTF8')),'hex');\n"
        "        expected:=recovery_hash;\n"
        "    ELSIF checked_kind='correction' AND fact->>'disposition'='scrap' THEN\n"
        "        IF fact#>>'{command_jsonb,action}' IS DISTINCT FROM 'scrap' THEN\n"
        "            RAISE EXCEPTION '0165 exact corrected scrap binding action required' USING ERRCODE='23514'; END IF;\n"
        "        scrap_hash:=encode(sha256(convert_to('cloud_oam.inventory.idempotency.v1','UTF8')||decode('00','hex')\n"
        "            ||convert_to('stock-scrap:'||client_key,'UTF8')),'hex');\n"
        "        expected:=scrap_hash;\n"
        "    END IF;\n" + anchor)
    body = replace(body, '           OR prior.correction_key_hash IS DISTINCT FROM hashes[3] THEN',
        '           OR prior.correction_key_hash IS DISTINCT FROM hashes[3]\n'
        '           OR prior.recovery_key_hash IS DISTINCT FROM recovery_hash\n'
        '           OR prior.scrap_key_hash IS DISTINCT FROM scrap_hash THEN')
    body = replace(body, 'inverse_id,approval_id,correction_id,seal_id,approval_seal_id,correction_seal_id,created_at)',
        'inverse_id,approval_id,correction_id,seal_id,approval_seal_id,correction_seal_id,recovery_key_hash,scrap_key_hash,created_at)')
    body = replace(body, "CASE WHEN checked_kind='correction_seal' THEN checked_fact END,(fact->>'created_at')::timestamptz);",
        "CASE WHEN checked_kind='correction_seal' THEN checked_fact END,recovery_hash,scrap_hash,(fact->>'created_at')::timestamptz);")
    changes[name] = body

    name = 'rsc_check_loss_request_binding_0159'
    body = replace(records[name]['prosrc'],
        'keys:=ARRAY[binding.reversal_key_hash,binding.approval_key_hash,binding.correction_key_hash];',
        'keys:=array_remove(ARRAY[binding.reversal_key_hash,binding.approval_key_hash,binding.correction_key_hash,binding.recovery_key_hash,binding.scrap_key_hash],NULL);')
    body = replace(body, "       OR fact->>'idempotency_key_hash' IS DISTINCT FROM (CASE checked_kind",
        "       OR (binding.recovery_key_hash IS NOT NULL) IS DISTINCT FROM\n"
        "            (checked_kind='inverse' AND fact->>'scrap_line_id' IS NOT NULL)\n"
        "       OR (binding.scrap_key_hash IS NOT NULL) IS DISTINCT FROM\n"
        "            COALESCE(checked_kind='correction' AND fact->>'disposition'='scrap',false)\n"
        "       OR fact->>'idempotency_key_hash' IS DISTINCT FROM (CASE\n"
        "            WHEN checked_kind='inverse' AND fact->>'scrap_line_id' IS NOT NULL THEN binding.recovery_key_hash\n"
        "            WHEN checked_kind='correction' AND fact->>'disposition'='scrap' THEN binding.scrap_key_hash\n"
        "            ELSE CASE checked_kind")
    body = replace(body, '            ELSE binding.reversal_key_hash END)', '            ELSE binding.reversal_key_hash END END)')
    body = replace(body, "('stock_loss_correction_execution_seals',true)) requests(name,keyed)",
        "('stock_loss_correction_execution_seals',true),('stock_scrap_recovery_requests',true),"
        "('stock_scrap_recovery_regional_reviews',true),('stock_scrap_recovery_headquarters_reviews',true),"
        "('stock_scrap_recovery_executions',true)) requests(name,keyed)")
    body = replace(body, "||CASE WHEN other_table=source_table THEN ' AND id<>$4' ELSE '' END||')',other_table)",
        "||CASE WHEN other_table=source_table THEN ' AND id<>$4' ELSE '' END\n"
        "            ||CASE WHEN other_table='stock_scrap_recovery_executions' AND binding.recovery_key_hash IS NOT NULL\n"
        "                THEN ' AND id IS DISTINCT FROM $5' ELSE '' END\n"
        "            ||CASE WHEN other_table='stock_operation_orders' AND binding.scrap_key_hash IS NOT NULL\n"
        "                THEN ' AND id IS DISTINCT FROM $6' ELSE '' END||')',other_table)")
    body = replace(body, 'INTO collision USING keys,binding.actor_user_id,binding.request_id,checked_fact;',
        "INTO collision USING keys,binding.actor_user_id,binding.request_id,checked_fact,\n"
        "                (fact->>'scrap_recovery_execution_id')::uuid,(fact->>'scrap_operation_id')::uuid;")
    changes[name] = body

    name = 'rsc_fence_loss_request_binding_0159'
    changes[name] = replace(records[name]['prosrc'],
        'WHERE key IN (b.reversal_key_hash,b.approval_key_hash,b.correction_key_hash)',
        'WHERE key IN (b.reversal_key_hash,b.approval_key_hash,b.correction_key_hash,b.recovery_key_hash,b.scrap_key_hash)')
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
    for patch in recovery['patches']():
        history['verify'](db, patch['after'])
    changes = patches()
    for patch in changes:
        history['verify'](db, patch['before'])
    db.execute(text((FOLDER / 'recovery_bindings.sql').read_text()))
    for patch in changes:
        db.execute(text(patch['after']['definition']))
        history['verify'](db, patch['after'])
    return changes
