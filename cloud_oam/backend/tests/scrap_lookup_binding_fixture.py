"""Synthetic SQLite reader evidence only; native registrar tests are separate."""
import hashlib
from uuid import UUID
from sqlalchemy import Column, MetaData, Table, insert, select
from app.stock_operation_models import StockLossDisposition
from app.formal_services.stock_scrap.binding_reads import REGISTRY, ALIASES, TYPED
from app.formal_services.stock_scrap.lookup_coordinates import keys
from app.formal_services.stock_scrap.recovery_facts import NAMES
from app.formal_services.stock_scrap.tables import tables


def create(db):
    # Deliberately degraded storage for reader fault injection only. Use the
    # production columns, but do not pretend that FK-corrupt rows could be
    # created through the native immutable registrar.
    damaged_storage = Table(REGISTRY.name, MetaData(), *(Column(column.name, column.type,
        primary_key=column.primary_key, nullable=column.nullable) for column in REGISTRY.columns))
    damaged_storage.create(db.connection(), checkfirst=True)
    from app.formal_services.stock_scrap.seal_reads import table
    table().create(db.connection(), checkfirst=True)


def seed(db, *, kind, command, identifier, root):
    if kind == 'original':
        row = db.get(StockLossDisposition, UUID(str(identifier)))
        actor, person, digest, created = row.actor_user_id, row.executor_person_id, row.request_hash, row.created_at
    else:
        table = tables()[NAMES[kind]]
        row = db.execute(select(table).where(table.c.id == UUID(str(identifier)))).mappings().one()
        actor, person, digest, created = (row[c] for c in ('actor_user_id','actor_person_id','request_hash','created_at'))
    identifier = UUID(str(identifier))
    db.execute(insert(REGISTRY).values(fact_id=identifier, binding_kind=kind, root_disposition_id=UUID(str(root)),
        actor_user_id=actor, actor_person_id=person, request_id=command.request_id, request_hash=digest,
        key_token=hashlib.sha256(('cloud_oam.loss.correction.key.v1\0'+command.idempotency_key).encode()).hexdigest(),
        **dict(zip(ALIASES, keys(command))), **{v: identifier if k == kind else None for k,v in TYPED.items()}, created_at=created))
