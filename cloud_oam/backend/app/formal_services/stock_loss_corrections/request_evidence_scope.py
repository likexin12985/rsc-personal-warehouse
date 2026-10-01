"""Keep authentication correlations outside inventory request evidence.

Unknown or malformed events still count as possible writes. Only the existing
formal authentication state vocabulary and its hashed correlation are excluded.
"""
from sqlalchemy import and_, func
from app.foundation_models import StateTransitionEvent
from app.formal_services.authentication_audit import AUTHENTICATION_AGGREGATE_TYPES


def authentication_state():
    body = StateTransitionEvent.metadata_jsonb
    return func.coalesce(and_(
        StateTransitionEvent.aggregate_type.in_(sorted(AUTHENTICATION_AGGREGATE_TYPES)),
        body['operation'].as_string() == 'formal_authentication_state_transition',
        body['request_id'].as_string().regexp_match(r'^authreq-[a-f0-9]{64}$'),
        body['request_reference'].as_string().is_(None),
    ), False)
