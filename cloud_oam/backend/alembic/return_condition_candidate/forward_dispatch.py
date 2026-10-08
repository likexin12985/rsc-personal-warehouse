"""Unpublished forward dispatch for the dedicated condition common order.

Preserves the literal 0165 dispatcher byte-for-byte outside one new branch.
The caller verifies the exact installed predecessor before applying this DDL.
This is not a formal migration or permission/runtime-catalog activation.
"""
from hashlib import sha256
import json
from pathlib import Path

raw = (Path(__file__).resolve().parents[1] / 'stock_scrap_0165/catalog.json').read_bytes()
if sha256(raw).hexdigest() != '32d18ea227d77bf2512a2fccb563716e306ef9de75a5777433825aec2ac6294c':
    raise ValueError('condition dispatcher requires exact frozen 0165 catalog')
SIGNATURE = 'public.rsc_dispatch_stock_return_0100()'
OLD = json.loads(raw)['functions']['rsc_dispatch_stock_return_0100()']['after']
EXPECTED_BODY = OLD['prosrc']
ANCHOR = "    IF EXISTS(SELECT 1 FROM public.stock_operation_orders WHERE id=checked_order AND operation_type='scrap') THEN"
BRANCH = """    IF EXISTS(SELECT 1 FROM public.stock_operation_orders WHERE id=checked_order AND operation_type='condition_correction') THEN
        IF checked_cancel IS NOT NULL OR NOT EXISTS(
            SELECT 1 FROM public.stock_condition_cases c
            JOIN public.stock_operation_orders o ON o.id=c.id AND o.condition_case_id=c.id
            WHERE c.id=checked_order AND c.operation_type='condition_correction') THEN
            RAISE EXCEPTION 'condition exact dedicated order required' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_condition_check_source((SELECT inbound_line_id
            FROM public.stock_condition_cases WHERE id=checked_order));
        PERFORM public.rsc_condition_check_posting(checked_order);
        PERFORM public.rsc_condition_check_identity(checked_order);
        PERFORM public.rsc_condition_check_event_files(id)
            FROM public.stock_condition_events WHERE case_id=checked_order;
        RETURN NULL;
    END IF;
"""
if EXPECTED_BODY.count(ANCHOR) != 1:
    raise ValueError('condition dispatcher anchor drift')
BODY = EXPECTED_BODY.replace(ANCHOR, BRANCH + ANCHOR, 1)
if OLD['definition'].count(EXPECTED_BODY) != 1:
    raise ValueError('condition dispatcher definition drift')
DEFINITION = OLD['definition'].replace(EXPECTED_BODY, BODY, 1)
EXPECTED_SHA256 = sha256(EXPECTED_BODY.encode()).hexdigest()
BODY_SHA256 = sha256(BODY.encode()).hexdigest()


def statements():
    return [DEFINITION]
