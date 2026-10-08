"""Generation-only installer for the tested 0165 candidate on owned PG16.

The release transition must consume frozen SQL, never import this compiler.
Keeping the compilation here also makes its exact ordered output recordable.
"""
from pathlib import Path
import runpy

from sqlalchemy import text
from sqlalchemy.schema import CreateTable

from pg16_stock_scrap_schema import compile_structure

FOLDER = Path(__file__).resolve().parents[1] / 'alembic/stock_scrap_0165'


def install(db):
    _, tables, _, statements = compile_structure()
    for statement in statements:
        db.execute(text(statement))
    for name in ('recovery_evidence.sql', 'recovery_approval.sql', 'recovery_authority.sql',
                 'recovery_admission.sql', 'inventory_edges.sql', 'historical_plans.sql',
                 'execution_evidence.sql'):
        db.execute(text((FOLDER / name).read_text()))
    for name in ('forward_history', 'forward_business', 'forward_serial', 'forward_recovery',
                 'forward_recovery_bindings', 'forward_scrap_bindings'):
        runpy.run_path(str(FOLDER / (name + '.py')))['install'](db)
    for name in ('seal_request.sql', 'seal_source.sql', 'seal_authority.sql'):
        db.execute(text((FOLDER / name).read_text()))
    from app.stock_scrap_seal_schema import build_schema
    db.execute(CreateTable(build_schema()[1]))
    db.execute(text((FOLDER / 'seal_persistence.sql').read_text()))
    for table in tables:
        db.execute(text('GRANT SELECT,INSERT ON public.' + table.name + ' TO star_oam_api'))
