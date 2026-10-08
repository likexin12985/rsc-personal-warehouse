"""Read-only candidate registry handle; never modifies Base or creates tables."""
from sqlalchemy import or_, select
from app.formal_services.work_order_query import _aware
from app.stock_scrap_binding_schema import ALIASES, TYPED, NAME
from .tables import tables

REGISTRY = tables()[NAME]


def verify(db, *, actor, request, hashes, token, unknown, kind=None, fact=None, root=None):
    clauses = [REGISTRY.c.key_token == token,
        (REGISTRY.c.actor_user_id == actor.user_id) & (REGISTRY.c.request_id == request.request_id)]
    clauses.extend(REGISTRY.c[name].in_(hashes) for name in ALIASES)
    if fact is not None:
        clauses.append(REGISTRY.c.fact_id == fact['id'])
    rows = [dict(row) for row in db.execute(select(REGISTRY).where(or_(*clauses)).limit(3)).mappings()]
    if fact is None:
        if rows:
            unknown()
    else:
        expected = dict(fact_id=fact['id'], binding_kind=kind, root_disposition_id=root,
            actor_user_id=actor.user_id, actor_person_id=actor.person_id, request_id=request.request_id,
            request_hash=fact['request_hash'], key_token=token, **dict(zip(ALIASES, hashes)),
            **{name: fact['id'] if name == TYPED[kind] else None for name in TYPED.values()})
        if (len(rows) != 1 or any(rows[0].get(k) != v for k, v in expected.items())
                or _aware(rows[0]['created_at']) != _aware(fact['created_at'])):
            unknown()
    return rows
