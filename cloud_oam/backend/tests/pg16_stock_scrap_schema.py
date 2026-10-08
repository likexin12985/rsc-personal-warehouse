"""Structural draft compiler used by owned PG16 checks, not a release revision.

New foreign keys are installed after all parent columns and unique constraints
exist. No existing FK is replaced; no API grant or business activation occurs.
"""
import hashlib
from sqlalchemy import MetaData, CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable, CreateColumn, AddConstraint, DropConstraint
from app import models
from app.database import Base
from app import stock_scrap_persistence_schema as schema
from app.stock_scrap_schema import predecessor_schema


def compile_structure():
    metadata = predecessor_schema()
    tables = schema.define(metadata)
    names = ('stock_operation_orders', 'stock_loss_dispositions',
             'stock_loss_correction_executions', 'stock_loss_disposition_reversals')
    before = {name: (set(metadata.tables[name].columns.keys()), set(metadata.tables[name].constraints)) for name in names}
    parents = schema.extend_parents(metadata)
    pg = dialect()
    statements = [str(CreateTable(table, include_foreign_key_constraints=[]).compile(dialect=pg)) for table in tables]
    foreign_keys = [c for table in tables for c in table.constraints if isinstance(c, ForeignKeyConstraint)]
    for table in tables:
        statements.append('REVOKE ALL ON TABLE public.' + table.name +
            ' FROM PUBLIC, star_oam_api, star_oam_projector, star_oam_edge, edge_inbox')
    for table in parents:
        columns, constraints = before[table.name]
        for column in table.columns:
            if column.name not in columns:
                statements.append('ALTER TABLE public.' + table.name + ' ADD COLUMN ' +
                    str(CreateColumn(column).compile(dialect=pg)))
        if table.name == 'stock_loss_dispositions':
            statements.append('ALTER TABLE public.stock_loss_dispositions ALTER COLUMN target_account_id DROP NOT NULL')
        for constraint in sorted(constraints - table.constraints, key=lambda item: item.name):
            assert isinstance(constraint, CheckConstraint), 'existing foreign keys must never be dropped'
            statements.append(str(DropConstraint(constraint).compile(dialect=pg)))
        for constraint in sorted(table.constraints - constraints,
                                 key=lambda item: item.name or ','.join(c.name for c in item.columns)):
            if isinstance(constraint, ForeignKeyConstraint):
                foreign_keys.append(constraint)
            else:
                assert isinstance(constraint, (CheckConstraint, UniqueConstraint))
                statements.append(str(AddConstraint(constraint).compile(dialect=pg)))
    for constraint in sorted(foreign_keys, key=lambda c: (c.table.name, c.name or ','.join(k.name for k in c.columns))):
        if constraint.name is None:
            identity = constraint.table.name + '_' + '_'.join(c.name for c in constraint.columns)
            constraint.name = 'fk_scrap_' + identity[:40] + '_' + hashlib.sha256(identity.encode()).hexdigest()[:12]
        statements.append(str(AddConstraint(constraint).compile(dialect=pg)))
    return metadata, tables, parents, statements
