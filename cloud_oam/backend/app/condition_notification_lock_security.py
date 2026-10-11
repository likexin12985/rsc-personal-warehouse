"""Reviewed 0182 successor metadata for the condition-seal lock scope.

This module is deliberately an overlay: the 0167 runtime catalog remains
frozen. It publishes the exact successor source digest for validators after
the 0182 candidate is selected; the database function is changed only by
migration 20261231_0182.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

REVISION = "20261231_0182"
SIGNATURE = "rsc_condition_decision_seal_lock()"
LEGACY_SHA256 = "3e954671d1f03deb037d6cf7c70ce7f171e12a44c2402cb44e2c30c1d2a44d7c"
SUCCESSOR_SHA256 = "5413f2f5b383d9754cfd326a70d18e988c94cf2f94eb21ebc2ba9080dfab6278"

# Keep this fragment byte-for-byte identical to the reviewed successor
# migration.  The deferred fence remains broad; only ordinary notification
# projections bypass the shared inventory-head lock. A request_id alone is
# not a condition marker: shipment notifications deliberately carry one too.
LEGACY_ANCHOR = """    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;"""
SUCCESSOR_FRAGMENT = """    -- Notification projections remain under the deferred condition-decision
    -- fence, but ordinary inventory notifications must not serialize
    -- unrelated transactions on the shared ledger head. Only condition
    -- evidence (or the reserved decision-seal namespace) participates in the
    -- global decision lock. A payload request_id/request_reference alone is
    -- insufficient because shipment and receipt notifications carry them.
    IF TG_TABLE_NAME='notification_events'
       AND NOT (
          COALESCE(COALESCE(next_row->>'business_type',prior->>'business_type') IN ('stock_condition_decision_seal','stock_condition_event'),false)
          OR COALESCE(COALESCE(next_row->>'event_type',prior->>'event_type') LIKE 'stock_condition.%',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'schema_version',prior->'payload_jsonb'->>'schema_version')='condition_result/1',false)
          OR COALESCE(COALESCE(next_row->>'action',prior->>'action')='seal_condition_decision_request',false)
          OR COALESCE(COALESCE(next_row->>'request_id',prior->'request_id') LIKE 'condition-decision-seal:%',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'aggregate_type',prior->'payload_jsonb'->>'aggregate_type')='stock_condition_decision_seal',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'action',prior->'payload_jsonb'->>'action')='seal_condition_decision_request',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'request_id',prior->'payload_jsonb'->>'request_id') LIKE 'condition-decision-seal:%',false)
       ) THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
""" + LEGACY_ANCHOR

_CONDITION_DATA = json.loads((Path(__file__).with_name(
    'return_condition_security.json')).read_text())
LEGACY_SOURCE = _CONDITION_DATA['functions'][SIGNATURE]['after']['prosrc']
if sha256(LEGACY_SOURCE.encode()).hexdigest() != LEGACY_SHA256:
    raise ValueError('0182 frozen condition-seal predecessor digest mismatch')
SUCCESSOR_SOURCE = LEGACY_SOURCE.replace(LEGACY_ANCHOR, SUCCESSOR_FRAGMENT, 1)
if sha256(SUCCESSOR_SOURCE.encode()).hexdigest() != SUCCESSOR_SHA256:
    raise ValueError('0182 successor condition-seal digest mismatch')

_READY_DATA = json.loads((Path(__file__).with_name(
    'material_request_contact_envelope_security.json')).read_text())['readiness']['after']
READY_SIGNATURE = 'rsc_oam_runtime_binding_ready_0044()'
READY_LEGACY_SHA256 = 'beaeca9daa8c2f3d9407358d08d28a28554b7abf1a63a27fed622833fee7554f'
READY_SOURCE = _READY_DATA['prosrc']
if sha256(READY_SOURCE.encode()).hexdigest() != READY_LEGACY_SHA256:
    raise ValueError('0182 frozen readiness predecessor digest mismatch')
READY_SUCCESSOR_SOURCE = READY_SOURCE.replace('20261230_0181', REVISION, 1)
READY_SUCCESSOR_SHA256 = sha256(READY_SUCCESSOR_SOURCE.encode()).hexdigest()


def successor_source(legacy_source: str) -> str:
    """Return the exact successor source, rejecting source drift."""
    if sha256(legacy_source.encode()).hexdigest() != LEGACY_SHA256:
        raise ValueError("0182 frozen condition-seal lock source drift")
    if legacy_source.count(LEGACY_ANCHOR) != 1:
        raise ValueError("0182 condition-seal lock anchor mismatch")
    result = legacy_source.replace(LEGACY_ANCHOR, SUCCESSOR_FRAGMENT)
    if sha256(result.encode()).hexdigest() != SUCCESSOR_SHA256:
        raise ValueError("0182 successor source digest mismatch")
    return result


def verify_successor_source(source: str) -> None:
    """Check a live ``pg_proc.prosrc`` value without changing the catalog."""
    if sha256(source.encode()).hexdigest() != SUCCESSOR_SHA256:
        raise ValueError("0182 successor condition-seal lock source mismatch")


def register(namespace):
    """Publish the successor digest in the read-only runtime registry."""
    registry = namespace.get('FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256')
    coordinate = ('rsc_condition_decision_seal_lock', '')
    if type(registry) is not dict or registry.get(coordinate) not in (
            LEGACY_SHA256, SUCCESSOR_SHA256):
        raise ValueError('0182 condition-seal predecessor digest not admitted')
    condition_catalog = namespace.get('_condition_catalog')
    if condition_catalog is None or not isinstance(condition_catalog.DATA, dict):
        raise ValueError('0182 condition catalog predecessor not admitted')
    condition_data = deepcopy(condition_catalog.DATA)
    condition_row = condition_data.get('functions', {}).get(
        'rsc_condition_decision_seal_lock()')
    if not isinstance(condition_row, dict):
        raise ValueError('0182 condition catalog function predecessor not admitted')
    condition_after = condition_row.get('after')
    if not isinstance(condition_after, dict) \
            or condition_after.get('prosrc') != LEGACY_SOURCE \
            or sha256(condition_after['prosrc'].encode()).hexdigest() != LEGACY_SHA256:
        raise ValueError('0182 condition catalog function source predecessor not admitted')
    successor_after = deepcopy(condition_after)
    successor_after['prosrc'] = SUCCESSOR_SOURCE
    definition = successor_after.get('definition')
    if not isinstance(definition, str) or definition.count(LEGACY_SOURCE) != 1:
        raise ValueError('0182 condition catalog function definition predecessor not admitted')
    successor_after['definition'] = definition.replace(LEGACY_SOURCE, SUCCESSOR_SOURCE, 1)
    condition_row['after'] = successor_after
    registry[coordinate] = SUCCESSOR_SHA256
    manifest = namespace.get('OAM_SYNC_FUNCTION_MANIFEST')
    current = manifest.get(READY_SIGNATURE) if isinstance(manifest, dict) else None
    if not isinstance(current, tuple) or current[-1] != READY_LEGACY_SHA256:
        raise ValueError('0182 readiness predecessor manifest not admitted')
    manifest[READY_SIGNATURE] = (*current[:-1], READY_SUCCESSOR_SHA256)
    oam_hashes = namespace.get('OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256')
    ready_coordinate = ('rsc_oam_runtime_binding_ready_0044', '')
    if type(oam_hashes) is not dict or oam_hashes.get(ready_coordinate) != READY_LEGACY_SHA256:
        raise ValueError('0182 readiness digest registry not admitted')
    oam_hashes[ready_coordinate] = READY_SUCCESSOR_SHA256
    readiness = namespace.get('_stock_scrap_readiness')
    if readiness is None or readiness.DATA.get('revision') != '20261230_0181':
        raise ValueError('0182 readiness runtime predecessor not admitted')
    ready_data = dict(readiness.DATA)
    ready_after = dict(ready_data['after'])
    ready_after['prosrc'] = READY_SUCCESSOR_SOURCE
    ready_after['definition'] = _READY_DATA['definition'].replace(
        '20261230_0181', REVISION, 1)
    ready_data['after'] = ready_after
    ready_data['afterSha256'] = READY_SUCCESSOR_SHA256
    ready_data['revision'] = REVISION
    loss_return_stop_catalog = namespace.get('_loss_return_stop_catalog')
    if loss_return_stop_catalog is None or not isinstance(loss_return_stop_catalog.DATA, dict):
        raise ValueError('0182 loss-return-stop catalog predecessor not admitted')
    loss_return_stop_data = deepcopy(loss_return_stop_catalog.DATA)
    readiness_rows = [
        row for family in ('newFunctions', 'replacedFunctions')
        for row in loss_return_stop_data.get(family, [])
        if row.get('signature') == READY_SIGNATURE
    ]
    if len(readiness_rows) != 1:
        raise ValueError('0182 loss-return-stop readiness predecessor not admitted')
    loss_readiness = readiness_rows[0]
    if (loss_readiness.get('prosrc') != READY_SOURCE
            or loss_readiness.get('prosrcSha256') != READY_LEGACY_SHA256
            or not isinstance(loss_readiness.get('definition'), str)
            or loss_readiness['definition'].count(READY_SOURCE) != 1):
        raise ValueError('0182 loss-return-stop readiness source predecessor not admitted')
    loss_readiness['prosrc'] = READY_SUCCESSOR_SOURCE
    loss_readiness['definition'] = loss_readiness['definition'].replace(
        READY_SOURCE, READY_SUCCESSOR_SOURCE, 1)
    loss_readiness['prosrcSha256'] = READY_SUCCESSOR_SHA256
    readiness.DATA = ready_data
    condition_catalog.DATA = condition_data
    loss_return_stop_catalog.DATA = loss_return_stop_data
