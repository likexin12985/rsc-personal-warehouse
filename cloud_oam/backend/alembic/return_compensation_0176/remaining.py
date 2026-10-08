"""Versioned remaining cancellation after exact returned-demand compensation."""
from pathlib import Path
import runpy
from sqlalchemy import text

closure = runpy.run_path(str(Path(__file__).with_name('closure.py')))
prior = closure['prior']
replace_once = prior['replace_once']
proof = replace_once(closure['BODY'], "('approved','partially_approved','cancelled')", "('approved','partially_approved')")
proof = replace_once(proof,
    '    PERFORM public.rsc_validate_material_request_approval_projection_0045(checked_request_id);',
    '''    IF EXISTS(SELECT 1 FROM public.material_request_remaining_cancellations WHERE request_id=checked_request_id) THEN
        RAISE EXCEPTION '0176 remaining cancellation already exists' USING ERRCODE='23514';
    END IF;
    PERFORM public.rsc_validate_material_request_approval_projection_0045(checked_request_id);''')
proof = replace_once(proof,
    "OR EXISTS(SELECT 1 FROM jsonb_array_elements(line_facts) item WHERE item->>'remaining_qty'<>'0.000')",
    "OR EXISTS(SELECT 1 FROM jsonb_array_elements(line_facts) item WHERE (item->>'remaining_qty')::numeric<0)\n"
    "       OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(line_facts) item WHERE (item->>'remaining_qty')::numeric>0)")
start = proof.index("    RETURN jsonb_build_object('schema',CASE WHEN jsonb_array_length(returned)>0")
assert proof[start:].endswith('END;\n')
proof = proof[:start] + '''    RETURN jsonb_build_object('settled_fulfillment',settled,
        'before',jsonb_build_object('schema_version','2.0','request_id',checked_request_id::text,
          'request_version',request_row.version,'revision_id',revision_id::text,
          'assessment','remaining_fulfillment_quantities','open_supply_tasks',0,'pending_substitutions',0,
          'lines',(SELECT jsonb_agg((item-'remaining_qty')||jsonb_build_object(
            'unreserved_qty',item->>'remaining_qty','reserved_unpicked_qty','0.000',
            'picked_unoutbound_qty','0.000','outbound_unshipped_qty','0.000',
            'shipped_unreceived_qty','0.000','accepted_unposted_qty','0.000','rejected_unsettled_qty','0.000',
            'unfulfilled_cancelled_qty','0.000','return_compensated_qty',item->>'cancelled_qty',
            'returned_pending_compensation_qty','0.000') ORDER BY item->>'request_line_id')
            FROM jsonb_array_elements(line_facts) item)));
END;
'''
PROOF = ('''BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.material_request_return_compensations WHERE request_id=checked_request_id) THEN
''' + prior['PROOF'] + '\n    ELSE\n' + proof + '\n    END IF;\nEND;\n')
INSERT = replace_once(prior['INSERT'], "'schema','rsc.material_request_remaining_cancellation.v1'",
    "'schema',CASE WHEN proof->'before'->>'schema_version'='2.0' THEN 'rsc.material_request_remaining_cancellation.v2' "
    "ELSE 'rsc.material_request_remaining_cancellation.v1' END")


def install(db):
    for signature, args, result, old, body in (
        ('rsc_remaining_cancel_proof_0171(uuid)', 'checked_request_id uuid', 'jsonb', prior['PROOF'], PROOF),
        ('rsc_guard_remaining_cancel_insert_0171()', '', 'trigger', prior['INSERT'], INSERT)):
        actual = db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:name)'), {'name':'public.'+signature})
        if actual != old:
            raise ValueError('0176 exact predecessor remaining cancellation required: '+signature)
        db.execute(text(f'CREATE OR REPLACE FUNCTION public.{signature.split("(")[0]}({args}) RETURNS {result} '
            'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$'+body+'$body$'))
