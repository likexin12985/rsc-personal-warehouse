"""Actual CHECK/UNIQUE origin shape; PG16 separately proves cross-row posting."""
from uuid import UUID, uuid4
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import AddConstraint
from sqlalchemy.dialects import postgresql
from pglast import parser
from app import stock_operation_models as module

TABLES = {
 'header': (module.StockOperationOrder.__table__,
  ('id','operation_type','oam_work_order_id','loss_headquarters_decision_id','loss_correction_decision_id','source_location_id','target_location_id','transit_location_id','target_custody_assignment_id'),
  ('ck_stock_operation_orders_locations', 'uq_stock_operation_orders_loss_decision')),
 'line': (module.StockOperationLine.__table__,
  ('id','operation_id','operation_type','source_recovery_line_id','source_loss_line_id','stock_account_id','reserved_account_id','target_condition'),
  ('ck_stock_operation_lines_dimensions','ck_stock_operation_lines_loss_origin_not_self','uq_stock_operation_lines_loss_origin')),
 'disposition': (module.StockLossDisposition.__table__,
  ('id','operation_id','disposition','return_operation_id','scrap_operation_id'),
  ('ck_loss_disposition_kind','uq_loss_disposition_return_operation')),
}

@pytest.fixture
def probe():
 engines = []
 def make(kind):
  original, names, constraints = TABLES[kind]
  metadata = sa.MetaData()
  table = sa.Table('probe_'+kind, metadata, *(sa.Column(name,original.c[name].type.copy(),
      nullable=original.c[name].nullable,primary_key=original.c[name].primary_key) for name in names))
  selected = {c.name:c for c in original.constraints if c.name in constraints}
  assert set(selected)==set(constraints)
  for name, constraint in selected.items():
   if isinstance(constraint,sa.CheckConstraint):
    table.append_constraint(sa.CheckConstraint(str(constraint.sqltext),name=name))
   elif isinstance(constraint,sa.UniqueConstraint):
    table.append_constraint(sa.UniqueConstraint(*(column.name for column in constraint.columns),name=name))
   else:
    raise AssertionError(type(constraint))
  engine=sa.create_engine('sqlite+pysqlite:///:memory:');engines.append(engine);metadata.create_all(engine)
  return engine,table
 yield make
 for engine in engines: engine.dispose()


def insert(probe,kind,row,accepted):
 engine,table=probe(kind)
 if accepted:
  with engine.begin() as db: db.execute(table.insert(),row)
  with engine.connect() as db: assert db.scalar(sa.select(sa.func.count()).select_from(table))==1
 else:
  with pytest.raises(IntegrityError),engine.begin() as db: db.execute(table.insert(),row)
  with engine.connect() as db: assert db.scalar(sa.select(sa.func.count()).select_from(table))==0


def header(kind='return',work=True,loss=False):
 return dict(id=uuid4(),operation_type=kind,oam_work_order_id=uuid4() if work else None,
   loss_headquarters_decision_id=uuid4() if loss else None,source_location_id=UUID(int=1),
   target_location_id=UUID(int=2) if kind=='return' else None,
   transit_location_id=UUID(int=3) if kind=='return' else None,
   target_custody_assignment_id=uuid4() if kind=='return' else None)

@pytest.mark.parametrize('kind',['return','loss_report'])
@pytest.mark.parametrize('work,loss',[(False,False),(True,False),(False,True),(True,True)])
def test_header_exclusive_origins_and_unchanged_loss_reports(probe,kind,work,loss):
 accepted=(kind=='return' and work!=loss) or (kind=='loss_report' and not work and not loss)
 insert(probe,'header',header(kind,work,loss),accepted)

@pytest.mark.parametrize('field,value',[('target_location_id',None),('transit_location_id',None),
 ('target_custody_assignment_id',None),('target_location_id',UUID(int=1)),
 ('transit_location_id',UUID(int=1)),('transit_location_id',UUID(int=2))])
@pytest.mark.parametrize('work',[True,False])
def test_both_return_origins_still_require_complete_distinct_route(probe,field,value,work):
 row=header(work=work,loss=not work);row[field]=value;insert(probe,'header',row,False)


def line(kind='return',recovery=True,loss=False,condition='used'):
 return dict(id=uuid4(),operation_id=uuid4(),operation_type=kind,
   source_recovery_line_id=uuid4() if recovery else None,source_loss_line_id=uuid4() if loss else None,
   stock_account_id=UUID(int=4),reserved_account_id=UUID(int=5),target_condition=condition)

@pytest.mark.parametrize('kind',['return','loss_report'])
@pytest.mark.parametrize('recovery,loss',[(False,False),(True,False),(False,True),(True,True)])
@pytest.mark.parametrize('condition',['new','used','damaged'])
def test_line_origin_exclusivity_and_condition_preservation(probe,kind,recovery,loss,condition):
 accepted=(kind=='loss_report' and not recovery and not loss) or (kind=='return' and (
   (recovery and not loss and condition in {'used','damaged'}) or (loss and not recovery)))
 insert(probe,'line',line(kind,recovery,loss,condition),accepted)

@pytest.mark.parametrize('condition',['unknown','',None])
def test_loss_return_cannot_remove_condition_validation(probe,condition):
 insert(probe,'line',line(recovery=False,loss=True,condition=condition),False)

def test_derived_line_cannot_reference_itself(probe):
 row=line(recovery=False,loss=True);row['source_loss_line_id']=row['id'];insert(probe,'line',row,False)

@pytest.mark.parametrize('recovery,loss',[(True,False),(False,True)])
def test_both_origins_require_distinct_source_pending_accounts(probe,recovery,loss):
 row=line(recovery=recovery,loss=loss);row['reserved_account_id']=row['stock_account_id'];insert(probe,'line',row,False)

@pytest.mark.parametrize('kind',['restore_available','convert_used','convert_damaged','return_to_region','scrap'])
@pytest.mark.parametrize('child',['none','separate','same'])
def test_original_disposition_and_child_relation_cannot_be_mixed(probe,kind,child):
 parent=uuid4();row=dict(id=uuid4(),operation_id=parent,disposition=kind,
   return_operation_id=None if child=='none' else parent if child=='same' else uuid4())
 accepted=(kind in {'restore_available','convert_used','convert_damaged'} and child=='none') or (kind=='return_to_region' and child=='separate')
 insert(probe,'disposition',row,accepted)


def test_original_hq_decision_cannot_derive_multiple_children_but_legacy_nulls_are_allowed(probe):
 engine,table=probe('header');decision=uuid4();first=header(work=False,loss=True);first['loss_headquarters_decision_id']=decision
 with engine.begin() as db:
  db.execute(table.insert(),header());db.execute(table.insert(),header());db.execute(table.insert(),first)
 second=header(work=False,loss=True);second['loss_headquarters_decision_id']=decision
 with pytest.raises(IntegrityError),engine.begin() as db: db.execute(table.insert(),second)
 with engine.connect() as db: assert db.scalar(sa.select(sa.func.count()).select_from(table))==3


def test_new_coordinates_are_additive_nullable_restricted_foreign_keys():
 for model,column,target in (
  (module.StockOperationOrder,'loss_headquarters_decision_id','stock_loss_headquarters_decisions.id'),
  (module.StockOperationLine,'source_loss_line_id','stock_operation_lines.id'),
  (module.StockLossDisposition,'return_operation_id','stock_operation_orders.id')):
  field=model.__table__.c[column];assert field.nullable and field.server_default is None
  foreign,=field.foreign_keys;assert foreign.target_fullname==target and foreign.ondelete=='RESTRICT'
 child,=module.StockLossDisposition.__table__.c.return_operation_id.foreign_keys
 assert child.deferrable and child.initially=='DEFERRED'


def test_actual_postgresql_constraints_parse():
 for original,_,names in TABLES.values():
  for constraint in original.constraints:
   if constraint.name in names:
    sql=str(AddConstraint(constraint).compile(dialect=postgresql.dialect()))
    parser.parse_sql(sql)


def test_one_derived_child_cannot_be_reused_by_two_root_dispositions(probe):
 engine,table=probe('disposition');child=uuid4()
 first=dict(id=uuid4(),operation_id=uuid4(),disposition='return_to_region',return_operation_id=child)
 second=dict(id=uuid4(),operation_id=uuid4(),disposition='return_to_region',return_operation_id=child)
 with engine.begin() as db: db.execute(table.insert(),first)
 with pytest.raises(IntegrityError),engine.begin() as db: db.execute(table.insert(),second)
 with engine.connect() as db: assert db.scalar(sa.select(sa.func.count()).select_from(table))==1


def test_duplicate_loss_line_is_refused_inside_one_child_without_colliding_legacy_nulls(probe):
 engine,table=probe('line');first=line(recovery=False,loss=True)
 duplicate=dict(first,id=uuid4())
 with engine.begin() as db:
  db.execute(table.insert(),first)
  db.execute(table.insert(),line())
  db.execute(table.insert(),line())
 with pytest.raises(IntegrityError),engine.begin() as db: db.execute(table.insert(),duplicate)
 with engine.connect() as db: assert db.scalar(sa.select(sa.func.count()).select_from(table))==3
