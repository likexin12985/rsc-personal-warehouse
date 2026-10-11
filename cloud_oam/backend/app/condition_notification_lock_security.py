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
SUCCESSOR_SHA256 = "cbbbb786f5a92b9e463b1334d0a60a1842eb145ec70fa78bcaf15a2b831f2a13"

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
          OR COALESCE(COALESCE(next_row->>'request_id',prior->>'request_id') LIKE 'condition-decision-seal:%',false)
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


def overlay(previous):
    """Select 0182 before shared readiness registration builds its catalogs."""
    data = previous.DATA
    if (data.get('revision') != '20261230_0181'
            or data.get('after') != _READY_DATA
            or data.get('afterSha256') != READY_LEGACY_SHA256):
        raise ValueError('0182 exact readiness predecessor not admitted')
    updated = deepcopy(data)
    after = updated['after']
    after['prosrc'] = READY_SUCCESSOR_SOURCE
    after['definition'] = after['definition'].replace(READY_SOURCE, READY_SUCCESSOR_SOURCE, 1)
    updated.update(afterSha256=READY_SUCCESSOR_SHA256, revision=REVISION)
    previous.DATA = updated


def register(namespace):
    """Validate both predecessor views, then publish only the lock successor.

    Shared readiness belongs to the existing readiness registration chain.
    Updating its manifest here would leave already-rendered Edge/projector
    SQL and independent catalogs bound to the previous revision.
    """
    registry = namespace.get('FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256')
    coordinate = ('rsc_condition_decision_seal_lock', '')
    if type(registry) is not dict or registry.get(coordinate) != LEGACY_SHA256:
        raise ValueError('0182 condition-seal predecessor digest not admitted')
    catalog = namespace.get('_condition_catalog')
    if catalog is None or not isinstance(catalog.DATA, dict):
        raise ValueError('0182 condition catalog predecessor not admitted')
    updated = deepcopy(catalog.DATA)
    row = updated.get('functions', {}).get(SIGNATURE)
    if not isinstance(row, dict) or row.get('after') != _CONDITION_DATA['functions'][SIGNATURE]['after']:
        raise ValueError('0182 exact condition function predecessor not admitted')
    after = row['after']
    if after['definition'].count(LEGACY_SOURCE) != 1:
        raise ValueError('0182 condition function definition predecessor not admitted')
    after['prosrc'] = SUCCESSOR_SOURCE
    after['definition'] = after['definition'].replace(LEGACY_SOURCE, SUCCESSOR_SOURCE, 1)
    # No publication occurs until the complete source, metadata and ACL agree.
    registry[coordinate] = SUCCESSOR_SHA256
    catalog.DATA = updated
