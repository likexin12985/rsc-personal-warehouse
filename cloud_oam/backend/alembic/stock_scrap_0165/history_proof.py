"""Compile private copies of frozen validators, changing only proof calls.

Used only by the candidate compiler. The release consumes frozen literal SQL.
Original standalone entry points retain their complete fresh validation.
"""
import re

PRIVATE = {
    'rsc_check_loss_chain_requests_0159': 'rsc_loss_requests_with_proof_0165',
    'rsc_check_loss_chain_events_0159': 'rsc_loss_events_with_proof_0165',
    'rsc_check_loss_first_inverse_plan_0159': 'rsc_loss_inverse_with_proof_0165',
    'rsc_check_loss_first_correction_plan_0159': 'rsc_loss_correction_with_proof_0165',
}


def replace_once(body, old, new):
    if body.count(old) != 1:
        raise ValueError('0165 exact private proof compiler anchor required')
    return body.replace(old, new, 1)


def calls(body):
    """Rewrite explicit qualified calls; retain arguments, including nesting."""
    names = tuple(PRIVATE) + ('rsc_check_scrap_history_0165',)
    pattern = re.compile(r'public\.(' + '|'.join(names) + r')\(')
    output = []
    position = 0
    for match in pattern.finditer(body):
        if match.start() < position:
            raise ValueError('0165 nested proof compiler call not supported')
        index = match.end()
        depth = 1
        quoted = False
        while index < len(body) and depth:
            char = body[index]
            if char == "'":
                if quoted and index + 1 < len(body) and body[index + 1] == "'":
                    index += 2
                    continue
                quoted = not quoted
            elif not quoted:
                depth += (char == '(') - (char == ')')
            index += 1
        if depth or quoted:
            raise ValueError('0165 unbalanced proof compiler call')
        name = match.group(1)
        arguments = body[match.end():index - 1]
        if name == 'rsc_check_scrap_history_0165':
            replacement = 'rsc_scrap_history_from_proof_0165'
            arguments += ',root.id,checked_proof'
        else:
            replacement = PRIVATE[name]
            arguments += ',checked_proof'
        output.append(body[position:match.start()] + 'public.' + replacement + '(' + arguments + ')')
        position = index
    output.append(body[position:])
    return ''.join(output)


def graph(body):
    body = replace_once(body, '    observed bigint; heads jsonb;',
        '    checked_proof jsonb;\n    observed bigint; heads jsonb;')
    body = replace_once(body,
        "        IF root.disposition IN ('restore_available','convert_used','convert_damaged') THEN",
        "        checked_proof:=public.rsc_build_scrap_history_proof_0165(root.id);\n"
        "        IF root.disposition IN ('restore_available','convert_used','convert_damaged') THEN")
    body = replace_once(body,
        '        PERFORM public.rsc_check_loss_upstream_history_0159(root.id);',
        '        -- The fresh root proof above includes the complete upstream history.')
    return calls(body)


def root_definitions(upstream, bridge):
    """Share only freshly verified root ancestry and audit membership.

    Every retained fact still checks its exact binding, events, plan, opening
    time and stock edges. Standalone historical entry points are unchanged.
    """
    body = replace_once(upstream['prosrc'],
        '    PERFORM public.rsc_loss_inventory_audit_members_0159();',
        "    IF audit_ids IS NULL OR cardinality(audit_ids)=0 THEN\n"
        "        RAISE EXCEPTION '0165 fresh root audit proof required' USING ERRCODE='23514'; END IF;")
    definition = replace_once(upstream['definition'], upstream['prosrc'], body)
    definition = replace_once(definition,
        'CREATE OR REPLACE FUNCTION public.rsc_check_loss_upstream_history_0159(' + upstream['identity_arguments'] + ')',
        'CREATE FUNCTION public.rsc_scrap_upstream_with_audit_0165(' + upstream['identity_arguments'] + ', audit_ids uuid[])')
    definition = definition.rstrip()
    if not definition.endswith('$function$'):
        raise ValueError('0165 exact upstream function delimiter required')
    definition += ';\nREVOKE ALL ON FUNCTION public.rsc_scrap_upstream_with_audit_0165(uuid,uuid[])\n'
    definition += 'FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;\n'
    fact = bridge.replace('rsc_check_scrap_history_0165', 'rsc_scrap_fact_with_root_proof_0165')
    fact = replace_once(fact, '(kind text, checked_fact uuid)',
        '(kind text, checked_fact uuid, checked_root uuid, audit_ids uuid[])')
    fact = replace_once(fact, '(text,uuid)', '(text,uuid,uuid,uuid[])')
    fact = replace_once(fact,
        '    PERFORM public.rsc_check_scrap_execution_events_0165(line.id);\n'
        '    PERFORM public.rsc_check_loss_upstream_history_0159(line.root_disposition_id);',
        "    IF checked_root IS NULL OR line.root_disposition_id IS DISTINCT FROM checked_root\n"
        "       OR audit_ids IS NULL OR cardinality(audit_ids)=0 THEN\n"
        "        RAISE EXCEPTION '0165 exact fresh root proof required' USING ERRCODE='23514'; END IF;\n"
        '    PERFORM public.rsc_check_scrap_execution_events_with_audit_0165(line.id,audit_ids);')
    return [definition, fact]


def definitions(records):
    result = []
    for name, private in PRIVATE.items():
        record = records[name]
        body = calls(record['prosrc'])
        definition = replace_once(record['definition'], record['prosrc'], body)
        definition = replace_once(definition,
            'CREATE OR REPLACE FUNCTION public.' + name + '(' + record['identity_arguments'] + ')',
            'CREATE FUNCTION public.' + private + '(' + record['identity_arguments'] + ', checked_proof jsonb)')
        # pg_get_functiondef omits the final SQL statement delimiter.
        definition = definition.rstrip()
        if not definition.endswith('$function$'):
            raise ValueError('0165 exact frozen function delimiter required')
        definition += ';\nREVOKE ALL ON FUNCTION public.' + private + '(uuid,jsonb)\n'
        definition += 'FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;\n'
        result.append(definition)
    return result
