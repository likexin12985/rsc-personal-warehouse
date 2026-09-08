from pathlib import Path
import runpy
import sqlalchemy as sa

ROOT=Path(__file__).resolve().parents[1]
MIGRATION=ROOT/'alembic/versions/20260913_0073_shipments.py'

def test_shipment_migration_is_forward_and_declares_all_tables(monkeypatch):
    m=runpy.run_path(str(MIGRATION)); tables={}
    monkeypatch.setattr(m['op'],'create_table',lambda name,*args,**kwargs: tables.setdefault(name,args))
    monkeypatch.setattr(m['op'],'execute',lambda *args,**kwargs:None)
    monkeypatch.setattr(m['op'],'get_bind',lambda: type('B',(),{'dialect':type('D',(),{'name':'sqlite'})()})())
    m['upgrade']()
    assert m['revision']=='20260913_0073' and m['down_revision']=='20260912_0072'
    assert set(tables)=={'shipments','shipment_lines','shipment_serials'}
    for name in tables:
        assert any(isinstance(c,sa.Column) for c in tables[name])
