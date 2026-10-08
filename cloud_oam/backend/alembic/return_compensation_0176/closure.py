"""Extend the frozen closure proof with exact posted-return compensation.

Only the 0176 isolated compiler installs this; older 0171 evidence is unchanged.
"""
from pathlib import Path
import runpy
from sqlalchemy import text

prior = runpy.run_path(str(Path(__file__).parents[1] / 'remaining_cancel_0171/candidate.py'))
PREDECESSOR = prior['CLOSURE_EVIDENCE']
replace_once = prior['replace_once']
BODY = replace_once(PREDECESSOR, 'line_facts jsonb; settled jsonb;',
                    'line_facts jsonb; settled jsonb; returned jsonb;')
BODY = replace_once(BODY,
    '    SELECT id INTO STRICT revision_id FROM public.material_request_revisions',
    '''    PERFORM public.rsc_validate_return_compensation_0176(c.id)
      FROM public.material_request_return_compensations c WHERE c.request_id=checked_request_id;
    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'compensation_id',c.id::text,'inbound_id',c.inbound_id::text,'request_line_id',c.request_line_id::text,
        'original_receipt_line_id',c.evidence_jsonb->'origin'->>'original_receipt_line_id',
        'cancelled_qty',c.cancelled_qty::numeric(18,3)::text,
        'request_hash',c.request_hash,'evidence_sha256',c.evidence_sha256) ORDER BY c.id),'[]'::jsonb)
      INTO returned FROM public.material_request_return_compensations c WHERE c.request_id=checked_request_id;
    SELECT id INTO STRICT revision_id FROM public.material_request_revisions''')
old_qty = ('(l.cancelled_qty+coalesce((SELECT c.cancelled_qty FROM public.material_request_remaining_cancellation_lines c '
           'WHERE c.request_line_id=l.id),0))')
new_qty = ('(' + old_qty + '+coalesce((SELECT sum(c.cancelled_qty) FROM public.material_request_return_compensations c '
           'WHERE c.request_line_id=l.id AND c.request_id=checked_request_id),0))::numeric(18,3)')
if BODY.count(old_qty) != 2:
    raise ValueError('0176 predecessor closure quantity anchors changed')
BODY = BODY.replace(old_qty, new_qty)
BODY = replace_once(BODY, 'SELECT sum(r.accepted_qty)\n', 'SELECT sum(r.accepted_qty+r.rejected_qty)\n')
BODY = replace_once(BODY,
    'OR EXISTS(SELECT 1 FROM public.receipt_lines r WHERE r.shipment_line_id=s.id AND r.rejected_qty<>0)',
    '''OR EXISTS(SELECT 1 FROM public.receipt_lines r WHERE r.shipment_line_id=s.id
            AND r.rejected_qty<>coalesce((SELECT sum(c.cancelled_qty)
              FROM public.material_request_return_compensations c
              WHERE c.request_id=checked_request_id
                AND c.evidence_jsonb->'origin'->>'original_receipt_line_id'=r.id::text),0))''')
BODY = replace_once(BODY,
    "    RETURN jsonb_build_object('schema','rsc.material_request_closure_evidence.v1','settled_fulfillment',settled,",
    """    IF jsonb_array_length(returned)>0 THEN
        settled:=settled||jsonb_build_object('return_compensations',returned);
    END IF;
    RETURN jsonb_build_object('schema',CASE WHEN jsonb_array_length(returned)>0
        THEN 'rsc.material_request_closure_evidence.v2' ELSE 'rsc.material_request_closure_evidence.v1' END,
        'settled_fulfillment',settled,""")


def install(db):
    if db.scalar(text("SELECT prosrc FROM pg_proc WHERE oid='public.rsc_closure_evidence_0169(uuid)'::regprocedure")) != PREDECESSOR:
        raise ValueError('0176 exact predecessor closure evidence required')
    db.execute(text('CREATE OR REPLACE FUNCTION public.rsc_closure_evidence_0169(checked_request_id uuid) RETURNS jsonb '
        'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$' + BODY + '$body$'))
