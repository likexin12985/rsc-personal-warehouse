"""Private graph copies must retain all frozen checks, not just happy paths."""
from pathlib import Path
import re
import runpy

import pytest

FOLDER = Path(__file__).parents[1] / 'alembic/stock_scrap_0165'
MODULE = runpy.run_path(str(FOLDER / 'forward_history.py'))
PROOF = MODULE['PROOF']


def test_private_copies_keep_every_guard_and_only_change_call_arguments():
    records = {p['after']['proname']: p['after'] for p in MODULE['patches']()}
    definitions = PROOF['definitions'](records)
    assert len(definitions) == 4
    for (original, private), definition in zip(PROOF['PRIVATE'].items(), definitions, strict=True):
        body = definition.split('AS $function$', 1)[1].split('$function$', 1)[0]
        body = body.replace('public.rsc_scrap_history_from_proof_0165(', 'public.rsc_check_scrap_history_0165(')
        body = body.replace(',root.id,checked_proof)', ')')
        for old, new in PROOF['PRIVATE'].items():
            body = body.replace('public.'+new+'(', 'public.'+old+'(')
        body = body.replace(',checked_proof)', ')')
        assert body == records[original]['prosrc']
        assert not re.search(r'\b(INSERT|DELETE|EXECUTE|set_config)\b', body, re.I)
        assert 'SECURITY DEFINER' in definition
        assert "SET search_path TO 'pg_catalog', 'public'" in definition
        assert 'REVOKE ALL ON FUNCTION public.'+private+'(uuid,jsonb)' in definition
        assert '$function$;\nREVOKE ALL ON FUNCTION' in definition
        assert 'FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;' in definition


def test_proof_rewrite_keeps_nested_casts_and_quoted_parentheses():
    sql = "PERFORM public.rsc_check_scrap_history_0165('inver''se)',(body->>'id')::uuid);"
    assert PROOF['calls'](sql) == (
        "PERFORM public.rsc_scrap_history_from_proof_0165('inver''se)',(body->>'id')::uuid,root.id,checked_proof);"
    )


@pytest.mark.parametrize('sql', [
    "PERFORM public.rsc_check_scrap_history_0165('inverse',",
    "PERFORM public.rsc_check_scrap_history_0165('inverse)",
])
def test_incomplete_compiler_input_is_rejected(sql):
    with pytest.raises(ValueError, match='unbalanced'):
        PROOF['calls'](sql)


def test_root_helpers_retain_frozen_upstream_and_every_fact_check():
    upstream = MODULE['predecessors']()['rsc_check_loss_upstream_history_0159']
    bridge = (FOLDER / 'history_bridge.sql').read_text()
    ancestry, fact = PROOF['root_definitions'](upstream, bridge)
    body = ancestry.split('AS $function$', 1)[1].split('$function$', 1)[0]
    body = body.replace(
        "    IF audit_ids IS NULL OR cardinality(audit_ids)=0 THEN\n"
        "        RAISE EXCEPTION '0165 fresh root audit proof required' USING ERRCODE='23514'; END IF;",
        '    PERFORM public.rsc_loss_inventory_audit_members_0159();')
    assert body == upstream['prosrc']
    restored = fact.replace('rsc_scrap_fact_with_root_proof_0165', 'rsc_check_scrap_history_0165')
    restored = restored.replace('(kind text, checked_fact uuid, checked_root uuid, audit_ids uuid[])',
        '(kind text, checked_fact uuid)').replace('(text,uuid,uuid,uuid[])', '(text,uuid)')
    restored = restored.replace(
        "    IF checked_root IS NULL OR line.root_disposition_id IS DISTINCT FROM checked_root\n"
        "       OR audit_ids IS NULL OR cardinality(audit_ids)=0 THEN\n"
        "        RAISE EXCEPTION '0165 exact fresh root proof required' USING ERRCODE='23514'; END IF;\n"
        '    PERFORM public.rsc_check_scrap_execution_events_with_audit_0165(line.id,audit_ids);',
        '    PERFORM public.rsc_check_scrap_execution_events_0165(line.id);\n'
        '    PERFORM public.rsc_check_loss_upstream_history_0159(line.root_disposition_id);')
    assert restored == bridge
    for definition in (ancestry, fact):
        assert 'SECURITY DEFINER' in definition
        assert 'FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;' in definition
        assert not re.search(r'\b(INSERT|UPDATE|DELETE|EXECUTE|set_config)\b', definition.split('AS $function$', 1)[1].split('$function$', 1)[0], re.I)
