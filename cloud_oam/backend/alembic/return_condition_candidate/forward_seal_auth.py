"""Forward-only 0165 authentication exclusion; keep all stock fences intact.

The installer must verify the exact frozen predecessor body before replacement.
Ordinary formal authentication evidence does not consume an inventory request.
Reserved scrap-seal markers and malformed authentication still reach the fence.
"""
from hashlib import sha256
import json
from pathlib import Path

raw = (Path(__file__).resolve().parents[1] / 'stock_scrap_0165/catalog.json').read_bytes()
if sha256(raw).hexdigest() != '32d18ea227d77bf2512a2fccb563716e306ef9de75a5777433825aec2ac6294c':
    raise ValueError('authentication fence requires exact frozen 0165 catalog')
SIGNATURE = 'public.rsc_fence_scrap_seals_0165()'
OLD = json.loads(raw)['functions']['rsc_fence_scrap_seals_0165()']['after']
EXPECTED_BODY = OLD['prosrc']
EXPECTED_SHA256 = sha256(EXPECTED_BODY.encode()).hexdigest()
ANCHOR = "    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;"
BRANCH = """    body:=COALESCE(raw->'after_jsonb',raw->'metadata_jsonb',raw->'payload_jsonb','{}'::jsonb);
    IF TG_TABLE_NAME='audit_events' AND raw->>'stream_key'='authentication'
       AND raw->>'aggregate_type' IN ('authentication_attempt','login_challenge','sms_dispatch','auth_session')
       AND raw->>'request_id' ~ '^authreq-[a-f0-9]{64}$'
       AND COALESCE(raw->>'action','')<>'seal_scrap_request'
       AND jsonb_typeof(body)='object'
       AND body-ARRAY['client_type','outcome','reason_code','status']='{}'::jsonb THEN
        RETURN NULL;
    END IF;
    IF TG_TABLE_NAME='state_transition_events' AND COALESCE(
       raw->>'aggregate_type' IN ('authentication_attempt','login_challenge','sms_dispatch','auth_session')
       AND body->>'operation'='formal_authentication_state_transition'
       AND body->>'request_id' ~ '^authreq-[a-f0-9]{64}$'
       AND body->>'request_reference' IS NULL,false) THEN
        RETURN NULL;
    END IF;
"""
if EXPECTED_BODY.count(ANCHOR) != 1 or OLD['definition'].count(EXPECTED_BODY) != 1:
    raise ValueError('authentication fence predecessor anchor drift')
if not OLD['prosecdef'] or OLD['owner'] != 'star_oam_migrator' or OLD['proconfig'] != ['search_path=pg_catalog, public']:
    raise ValueError('authentication fence predecessor security drift')
BODY = EXPECTED_BODY.replace(ANCHOR, BRANCH + ANCHOR, 1)
BODY_SHA256 = sha256(BODY.encode()).hexdigest()
DEFINITION = OLD['definition'].replace(EXPECTED_BODY, BODY, 1)


def statements():
    return [DEFINITION]
