"""Exact forward history patches; candidate only, without runtime activation.

Frozen predecessor files remain untouched. Installation refuses a different
body, signature, owner, security configuration or ACL instead of overwriting it.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import runpy

from sqlalchemy import text

FOLDER = Path(__file__).resolve().parent
PROOF = runpy.run_path(str(FOLDER / 'history_proof.py'))
CATALOGS = (
    ('stock_loss_corrections_0159/frozen-catalog.json', '29bee9907c271dfc9e8bc616e99c658f52de71261b9c6cbfc91b71ade6ec2d50'),
    ('stock_loss_multigeneration_0160/catalog.json', '90142dbf0721f4dd1efff3bdfbb6d6633eef2536a49f44cbe276d4217bad4c7e'),
    ('stock_loss_correction_seals_0161/catalog.json', 'b79b960823852cd9ad1474c0da78bdcfcfbe3c513253d5d9b255264f5ae5bc89'),
    ('stock_loss_return_stops_0163/catalog.json', 'cab3ea4a356b2bd8a21d8207b6f46c4023bce492f5401380dd225cda43e14b42'),
)


def predecessors():
    catalogs = []
    for name, expected in CATALOGS:
        raw = (FOLDER.parent / name).read_bytes()
        if sha256(raw).hexdigest() != expected:
            raise ValueError('0165 historical catalog digest mismatch: ' + name)
        catalogs.append(json.loads(raw))
    base, multi, seals, stops = catalogs
    records = {r['proname']: r for r in base['newFunctions'] + base['replacedFunctions']}
    for catalog, key in ((multi, 'functions'), (seals, 'changedFunctions')):
        for patch in catalog[key]:
            before, after = patch['before'], patch['after']
            if records[before['proname']]['prosrc'] != before['prosrc']:
                raise ValueError('0165 predecessor source discontinuity')
            records[after['proname']] = after
    for record in seals['addedFunctions']:
        if record['proname'] in ('rsc_check_loss_approval_seal_0161', 'rsc_check_loss_execution_seal_0161'):
            records[record['proname']] = record
    for record in stops['replacedFunctions']:
        if record['proname'] in records and records[record['proname']]['prosrc'] != record['before_prosrc']:
            raise ValueError('0165 return predecessor source discontinuity')
        records[record['proname']] = record
    return records


def replace_once(body, old, new):
    if body.count(old) != 1:
        raise ValueError('0165 exact single historical patch anchor required')
    return body.replace(old, new, 1)


def patches():
    records = predecessors()
    changes = {}
    name = 'rsc_check_loss_history_graph_0159'
    body = replace_once(records[name]['prosrc'],
        "        ELSE\n            RAISE EXCEPTION '0159 dedicated scrap history proof required'",
        "        ELSIF root.disposition='scrap' THEN\n"
        "            PERFORM public.rsc_check_scrap_history_0165('original',root.id);\n"
        "        ELSE\n            RAISE EXCEPTION '0159 dedicated scrap history proof required'")
    changes[name] = PROOF['graph'](replace_once(body,
        '        PERFORM public.rsc_check_loss_upstream_history_0159(root.id);',
        '        PERFORM public.rsc_check_loss_upstream_history_0159(root.id);\n'
        '        PERFORM public.rsc_check_loss_chain_events_0159(root.id);'))

    name = 'rsc_check_loss_first_inverse_plan_0159'
    anchor = "    IF selected_kind NOT IN ('restore_available','convert_used','convert_damaged','return_to_region')"
    changes[name] = replace_once(records[name]['prosrc'], anchor,
        "    IF selected_kind='scrap' THEN\n"
        "        RETURN public.rsc_check_scrap_history_0165('inverse',inverse.id);\n"
        "    END IF;\n" + anchor)

    name = 'rsc_check_loss_first_correction_plan_0159'
    anchor = '    IF correction.id IS NULL OR root.id IS NULL OR inverse.id IS NULL OR decision.id IS NULL'
    changes[name] = replace_once(records[name]['prosrc'], anchor,
        "    IF correction.disposition='scrap' THEN\n"
        "        PERFORM public.rsc_check_loss_first_inverse_plan_0159(correction.reversal_id);\n"
        "        RETURN public.rsc_check_scrap_history_0165('correction',correction.id);\n"
        "    END IF;\n" + anchor)

    name = 'rsc_check_loss_chain_requests_0159'
    anchor = "        intent:=jsonb_build_object('root_disposition_id',root.id::text,'expected_root_request_hash',root.request_hash,"
    changes[name] = replace_once(records[name]['prosrc'], anchor,
        "        IF (item.kind='reverse_loss' AND body->>'scrap_line_id' IS NOT NULL)\n"
        "            OR (item.kind='correct_loss' AND body->>'disposition'='scrap') THEN\n"
        "            PERFORM public.rsc_check_scrap_history_0165(\n"
        "                CASE item.kind WHEN 'reverse_loss' THEN 'inverse' ELSE 'correction' END,(body->>'id')::uuid);\n"
        "            returned_requests:=returned_requests||jsonb_build_array(jsonb_build_object(\n"
        "                'id',body->>'id','action',body#>>'{command_jsonb,action}','request_hash',body->>'request_hash'));\n"
        "            CONTINUE;\n"
        "        END IF;\n" + anchor)

    name = 'rsc_check_loss_chain_events_0159'
    anchor = "        common:=jsonb_build_object('root_disposition_id',root.id::text,'operation_id',parent.id::text,"
    changes[name] = replace_once(records[name]['prosrc'], anchor,
        "        IF (item.category='inverse' AND fact->>'scrap_line_id' IS NOT NULL)\n"
        "            OR (item.category='correction' AND fact->>'disposition'='scrap') THEN\n"
        "            PERFORM public.rsc_check_scrap_history_0165(item.category,identifier);\n"
        "            CONTINUE;\n"
        "        END IF;\n" + anchor)
    # A scrap root can have a proved recovery and later ordinary correction
    # commands. Their stock-neutral seals retain the complete ancestry, hash,
    # event and collision checks; only the root kind gains formal support.
    for name in ('rsc_check_loss_approval_seal_0161', 'rsc_check_loss_execution_seal_0161'):
        changes[name] = replace_once(records[name]['prosrc'],
            "OR root.disposition NOT IN ('restore_available','convert_used','convert_damaged')",
            "OR root.disposition NOT IN ('restore_available','convert_used','convert_damaged','scrap')")
    result = []
    for name, body in changes.items():
        before = records[name]
        after = deepcopy(before)
        after['prosrc'] = body
        after['definition'] = replace_once(before['definition'], before['prosrc'], body)
        result.append(dict(before=before, after=after,
            beforeSha256=sha256(before['prosrc'].encode()).hexdigest(),
            afterSha256=sha256(body.encode()).hexdigest()))
    return result


def verify(db, expected):
    signature = 'public.' + expected['signature']
    actual = db.execute(text('''SELECT pg_get_functiondef(p.oid) AS definition,p.prosrc,
        p.prosecdef,p.provolatile,p.proparallel,p.proisstrict,p.proleakproof,p.proconfig,
        pg_get_userbyid(p.proowner) AS owner,
        pg_get_function_identity_arguments(p.oid) AS identity_arguments,
        ARRAY(SELECT jsonb_build_object('grantee',CASE WHEN a.grantee=0 THEN 'PUBLIC'
            ELSE pg_get_userbyid(a.grantee) END,'privilege',a.privilege_type,'grantable',a.is_grantable)
            FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a) AS acl
        FROM pg_proc p WHERE p.oid=to_regprocedure(:signature)'''), dict(signature=signature)).mappings().one_or_none()
    if actual is None:
        raise ValueError('0165 missing predecessor: ' + signature)
    wanted = {key: expected[key] for key in actual}
    actual = dict(actual)
    for value in (actual, wanted):
        value['acl'] = sorted(value['acl'], key=lambda item: (item['grantee'], item['privilege'], item['grantable']))
    if actual != wanted:
        raise ValueError('0165 exact history function catalog mismatch: ' + signature)


def install(db):
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16:
        raise ValueError('0165 direct PostgreSQL16 migration owner required')
    if db.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')):
        raise ValueError('0165 migration owner must not be superuser')
    if db.scalar(text('SELECT version_num FROM public.alembic_version')) != '20261213_0164':
        raise ValueError('0165 exact predecessor revision required')
    changes = patches()
    # Check every before state before replacing any function; caller owns the
    # atomic migration transaction and the surrounding table/schema locks.
    for patch in changes:
        verify(db, patch['before'])
    upstream = predecessors()['rsc_check_loss_upstream_history_0159']
    verify(db, upstream)
    db.execute(text((FOLDER / 'history_bridge.sql').read_text()))
    for definition in PROOF['root_definitions'](upstream, (FOLDER / 'history_bridge.sql').read_text()):
        db.execute(text(definition))
    db.execute(text((FOLDER / 'history_proof.sql').read_text()))
    records = {patch['after']['proname']: patch['after'] for patch in changes}
    for definition in PROOF['definitions'](records):
        db.execute(text(definition))
    for patch in changes:
        db.execute(text(patch['after']['definition']))
        verify(db, patch['after'])
    return changes
