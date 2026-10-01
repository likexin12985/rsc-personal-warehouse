"""Read the original outcome after proving all represented successor history.

No current stock claim or replay authority is returned. Callers retain exact
original-command and current read authorization checks. The immutable native
binding function attests successor raw keys; a historical read has no access
to those keys and does not pretend to recompute their global tokens.
"""
import re

from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError

from app.stock_loss_correction_models import stock_loss_request_key_bindings as bindings
from app.stock_operation_models import StockLossDisposition
from app.formal_services import stock_loss_disposition_facts as original
from app.formal_services import stock_loss_sources as sources
from app.formal_services.work_order_query import _aware
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.stock_loss_corrections.historical_original import _bound, verify_historical_original
from .history_chain import verify_chain
from app.formal_services.stock_loss_corrections.history_inventory import _candidate_rows
from app.formal_services.stock_loss_corrections.history_events import load_event_checked_inventory_history


def _unknown():
    sources._fail('stock_loss_disposition_history_unknown',
        '原处置或后继冲正证据不完整，不能判定未执行或重发，请保留原请求核验', 503)


def _binding_snapshot(db, roots, facts):
    """Read native provenance, including conflicting coordinates and orphans.

Read by root as well as fact identity: moving a binding to another root or
adding an otherwise unrepresented binding cannot hide it from the proof.
Permanent request seals describe other unexecuted commands, not stock edges.
"""
    coordinates = [bindings.c.root_disposition_id.in_(tuple(roots))
        & (bindings.c.binding_kind != 'inverse_seal')]
    coordinates.extend((bindings.c.fact_id == row.id)
        | ((bindings.c.actor_user_id == row.actor_user_id)
           & (bindings.c.request_id == row.request_id)) for _, row in facts)
    rows = tuple(dict(row) for row in db.execute(select(bindings).where(
        or_(*coordinates)).order_by(bindings.c.fact_id)).mappings())
    expected = {row.id: (kind, row) for kind, row in facts}
    if len(rows) != len(expected) or {row['fact_id'] for row in rows} != set(expected):
        _unknown()
    columns = {'inverse': 'inverse_id', 'approval': 'approval_id', 'correction': 'correction_id'}
    keys = {'inverse': 'reversal_key_hash', 'approval': 'approval_key_hash', 'correction': 'correction_key_hash'}
    for binding in rows:
        kind, fact = expected[binding['fact_id']]
        values = dict(binding_kind=kind, root_disposition_id=fact.root_disposition_id,
            actor_user_id=fact.actor_user_id, request_id=fact.request_id, request_hash=fact.request_hash)
        values.update({column: fact.id if column == columns[kind] else None
            for column in ('inverse_id', 'approval_id', 'correction_id', 'seal_id')})
        digests = tuple(binding[column] for column in keys.values())
        if (any(binding[column] != value for column, value in values.items())
                or binding[keys[kind]] != fact.idempotency_key_hash
                or _aware(binding['created_at']) != _aware(fact.created_at)
                or len(set(digests)) != 3
                or any(not isinstance(value, str) or re.fullmatch('[a-f0-9]{64}', value) is None
                    for value in (*digests, binding['key_token'], binding['request_hash']))):
            _unknown()
        # Cross-action aliases or collisions with other roots/seals are not
        # accepted just because the selected action's hash is well formed.
        collisions = tuple(db.scalars(select(bindings.c.fact_id).where(or_(
            bindings.c.key_token == binding['key_token'],
            *(bindings.c[column].in_(digests) for column in keys.values()))).limit(3)))
        if collisions != (fact.id,):
            _unknown()
    return rows


def verified(db, *, row):
    """Prove history first; never retry the old verifier as an error fallback."""
    with db.no_autoflush:
        try:
            start = _bound(db)
            proof = verify_historical_original(db, root_disposition_id=row.id)
            roots = proof.verified_original_ids
            signatures, facts = {}, []
            for root_id in sorted(roots, key=str):
                load_event_checked_inventory_history(db, root_disposition_id=root_id)
                groups = _candidate_rows(db, root_id)
                signatures[root_id] = tuple(tuple(fact.id for fact in group) for group in groups)
                inverses, decisions, corrections = groups
                verify_chain(db, root_disposition_id=root_id)
                facts.extend((kind, fact) for kind, group in zip(
                    ('inverse', 'approval', 'correction'), groups) for fact in group)
            first = _binding_snapshot(db, roots, facts)
            # Existing unreversed outcomes retain their exact old response and
            # verifier. A proved successor uses the original immutable payload.
            fresh = db.get(StockLossDisposition, row.id, populate_existing=True)
            result = original.payload(fresh) if facts else original.verified(db, row=fresh)
            for root_id, signature in signatures.items():
                if tuple(tuple(fact.id for fact in group) for group in _candidate_rows(db, root_id)) != signature:
                    _unknown()
            if _binding_snapshot(db, roots, facts) != first or _bound(db) != start:
                _unknown()
            return result
        except (InvalidChain, DBAPIError, KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
            _unknown()
