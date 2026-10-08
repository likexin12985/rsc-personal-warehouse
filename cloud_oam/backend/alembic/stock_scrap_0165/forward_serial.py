"""Preserve the current 0092+0093+0098 replay and add proved scrap edges.

The predecessor was captured from a full native 0164 migration. Its body hash
also matches the independently registered 0164 database-security expectation.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import runpy
from sqlalchemy import text

FOLDER = Path(__file__).resolve().parent
CATALOG_SHA = '689d278b4b9ac9d54b5973965a0b36783bcfbd5bdcb269a3682a7b5bf0d37578'
BODY_SHA = '334f3ca3d06af1d1dfe047b135d00859376c671c9f8140e9d56a2c56055415d9'
history = runpy.run_path(str(FOLDER / 'forward_history.py'))

RECOVERY = """        IF move.movement_type='reversal' AND move.source_document_type='stock_loss_disposition_reversal'
            AND expected_status='scrapped' THEN
            SELECT * INTO original_move FROM public.inventory_movements WHERE id=expected_movement;
            prior:=snapshots->expected_movement::text;
            SELECT id INTO recovery_id FROM public.stock_loss_disposition_reversals
                WHERE posting_transaction_id=move.transaction_id AND original_movement_id=expected_movement
                  AND scrap_line_id IS NOT NULL AND scrap_recovery_execution_id IS NOT NULL;
            IF original_move.id IS NULL OR recovery_id IS NULL OR prior IS NULL
                OR move.ledger_cursor<=expected_cursor OR expected_account IS NOT NULL
                OR move.from_account_id IS NOT NULL OR move.to_account_id IS NULL
                OR original_move.transaction_id IS DISTINCT FROM move.reversed_transaction_id
                OR original_move.to_account_id IS NOT NULL OR original_move.from_account_id IS DISTINCT FROM move.to_account_id
                OR move.quantity IS DISTINCT FROM original_move.quantity
                OR move.external_boundary_code IS DISTINCT FROM 'stock_operation_scrap'
                OR original_move.external_boundary_code IS DISTINCT FROM move.external_boundary_code
                OR prior->>'status' IS DISTINCT FROM 'active'
                OR (prior->>'account')::uuid IS DISTINCT FROM move.to_account_id
                OR (prior->>'owner')::uuid IS DISTINCT FROM move.target_owner
                OR move.target_material IS DISTINCT FROM serial.material_id OR move.target_lot IS DISTINCT FROM serial.lot_id THEN
                RAISE EXCEPTION '0165 serial recovery requires the last exact scrap edge' USING ERRCODE='23514'; END IF;
            PERFORM public.rsc_check_scrap_history_0165('inverse',recovery_id);
            expected_status:='active'; terminal_time:=move.created_at;
            expected_owner:=(prior->>'owner')::uuid; expected_account:=move.to_account_id;
            expected_movement:=move.id; expected_cursor:=move.ledger_cursor;
            CONTINUE;
        END IF;
"""
SCRAP = """            SELECT source_kind,CASE source_kind WHEN 'original' THEN root_disposition_id
                ELSE correction_execution_id END AS fact_id INTO scrap
                FROM public.stock_scrap_lines WHERE posting_movement_id=move.id AND posting_transaction_id=move.transaction_id;
            IF scrap.fact_id IS NULL OR move.from_account_id IS NULL OR move.to_account_id IS NOT NULL
                OR move.external_boundary_code IS DISTINCT FROM 'stock_operation_scrap'
                OR move.source_document_type IS DISTINCT FROM (CASE scrap.source_kind
                    WHEN 'original' THEN 'stock_loss_disposition' ELSE 'stock_loss_correction_execution' END) THEN
                RAISE EXCEPTION '0165 serial scrap requires its exact retained fact' USING ERRCODE='23514'; END IF;
            PERFORM public.rsc_check_scrap_history_0165(scrap.source_kind,scrap.fact_id);
            expected_status:='scrapped'; terminal_time:=move.created_at;"""


def patch():
    raw = (FOLDER / 'serial-predecessor.json').read_bytes()
    if sha256(raw).hexdigest() != CATALOG_SHA:
        raise ValueError('0165 serial predecessor catalog digest mismatch')
    before = json.loads(raw)
    if sha256(before['prosrc'].encode()).hexdigest() != BODY_SHA:
        raise ValueError('0165 serial predecessor body mismatch')
    replace = history['replace_once']
    body = replace(before['prosrc'], '    move record;', '    move record;\n    scrap record;\n    recovery_id uuid;')
    anchor = "        IF move.movement_type = 'reversal' AND move.source_document_type = 'work_order_material' THEN"
    body = replace(body, anchor, RECOVERY + anchor)
    body = replace(body, "            RAISE EXCEPTION '0092 serial scrap requires controlled lifecycle evidence' USING ERRCODE = '23514';", SCRAP)
    after = deepcopy(before)
    after['prosrc'] = body
    after['definition'] = replace(before['definition'], before['prosrc'], body)
    return dict(before=before, after=after, beforeSha256=BODY_SHA,
        afterSha256=sha256(body.encode()).hexdigest())


def install(db):
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16:
        raise ValueError('0165 direct PostgreSQL16 migration owner required')
    if db.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')):
        raise ValueError('0165 migration owner must not be superuser')
    if db.scalar(text('SELECT version_num FROM public.alembic_version')) != '20261213_0164':
        raise ValueError('0165 exact predecessor revision required')
    for item in history['patches']():
        history['verify'](db, item['after'])
    item = patch()
    history['verify'](db, item['before'])
    db.execute(text(item['after']['definition']))
    history['verify'](db, item['after'])
    return item
