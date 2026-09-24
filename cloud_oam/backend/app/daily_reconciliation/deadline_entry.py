"""Bound the transaction on three ready, owned connections; consume them.

Connection establishment is outside this boundary. Formal deployment must also
bound pool/DNS/TCP establishment. No DSN or client-supplied snapshots are accepted.
Never retry an uncertain COMMIT automatically; use exact authenticated recovery.
"""
from contextlib import nullcontext
from dataclasses import dataclass
import time
from sqlalchemy import event,text
from sqlalchemy.orm import Session
from psycopg.pq import TransactionStatus
from . import cutoff_service as service

class DeadlineExceeded(RuntimeError):pass
class OutcomeUnknown(RuntimeError):pass
class EntryError(RuntimeError):pass

@dataclass(frozen=True)
class Deadline:
 expires:float
 @classmethod
 def start(cls,seconds):
  if type(seconds) is not int or not 1<=seconds<=60:raise EntryError('deadline_seconds_invalid')
  return cls(time.monotonic()+seconds)
 def milliseconds(self):
  value=int((self.expires-time.monotonic())*1000)
  if value<1:raise DeadlineExceeded('daily_cutoff_transaction_deadline')
  return value
 def reader_seconds(self):
  # Existing readers take whole seconds. Never round upwards and reset the
  # aggregate budget; decline a new phase when less than a second remains.
  value=self.milliseconds()//1000
  if value<1:raise DeadlineExceeded('daily_cutoff_reader_budget_exhausted')
  return min(value,60)

class ReadyReader:
 def __init__(self,connection):self.connection=connection;self.dialect=connection.dialect
 def connect(self):return nullcontext(self.connection)

def execute_ready(*,owner,source,ledger,command,access_token,expected_authorization_version,
                  maximum_seconds=10,operation='capture'):
 # Validate borrowed transaction boundaries before taking ownership. A rejected
 # call must not close, commit or roll back somebody else's active transaction.
 deadline=Deadline.start(maximum_seconds)
 if operation not in ('capture','recover','preview'):raise EntryError('operation_invalid')
 if owner.closed or source.closed or ledger.closed or owner.in_transaction() or source.in_transaction() \
  or ledger.info.transaction_status!=TransactionStatus.IDLE:raise EntryError('ready_idle_connections_required')
 owner_info=owner.connection.driver_connection.info;source_info=source.connection.driver_connection.info;ledger_info=ledger.info
 if len({(c.host,c.port,c.dbname) for c in (owner_info,source_info,ledger_info)})!=1:
  raise EntryError('configured_database_binding_mismatch')
 if any(not 160000<=c.server_version<170000 for c in (owner_info,source_info,ledger_info)):
  raise EntryError('postgresql16_required')
 if (owner_info.user,source_info.user,ledger_info.user)!=(
  'star_oam_migrator','rsc_control_capture','rsc_reconciliation_capture'):
  raise EntryError('daily_capture_connection_roles_invalid')
 owned=False;transaction=None;commit_started=False;committed=False
 def limit(conn,cursor,statement,parameters,context,many):
  remaining=deadline.milliseconds()
  cursor.execute('SET LOCAL statement_timeout = '+str(remaining))
  cursor.execute('SET LOCAL idle_in_transaction_session_timeout = '+str(remaining))
 try:
  owned=True
  source_roles=source.exec_driver_sql('SELECT current_user, session_user').one()
  source.rollback()
  with ledger.cursor() as cursor:
   cursor.execute('SELECT current_user, session_user')
   ledger_roles=cursor.fetchone()
  if source_roles!=('rsc_control_capture','rsc_control_capture') or \
   ledger_roles!=('rsc_reconciliation_capture','rsc_reconciliation_capture'):
   raise EntryError('daily_capture_effective_roles_invalid')
  transaction=owner.begin()
  event.listen(owner,'before_cursor_execute',limit)
  with Session(bind=owner,autoflush=False) as db:
   if tuple(db.scalars(text('SELECT version_num FROM alembic_version'))) != ('20261119_0140',):
    raise EntryError('daily_capture_migration_head_mismatch')
   if operation in ('preview','recover'):
    from .capture_security import validate_capture_roles
    validate_capture_roles(owner)
   if operation=='capture':
    result=service._capture_and_record(db,access_token=access_token,expected_authorization_version=expected_authorization_version,
      command=command,source_reader_engine=ReadyReader(source),ledger_reader_connection=ledger,deadline=deadline)
   elif operation=='recover':
    result=service.recover(db,access_token=access_token,expected_authorization_version=expected_authorization_version,command=command)
   else:
    result=service.preview(db,access_token=access_token,expected_authorization_version=expected_authorization_version,command=command)
    if db.new or db.dirty or db.deleted:
     raise EntryError('daily_capture_preview_mutated_session')
    deadline.milliseconds()
    transaction.rollback()
    return dict(outcome='inspected',receipt=result)
   db.flush()
   deadline.milliseconds()
   # libpq COMMIT is not a SQLAlchemy execute event. Set the remaining SQL
   # budget immediately beforehand, including deferred constraints/triggers.
   owner.exec_driver_sql('SET LOCAL statement_timeout = '+str(deadline.milliseconds()))
   commit_started=True
   transaction.commit();committed=True
   return dict(outcome='committed',receipt=result,deadline_exceeded_after_commit=time.monotonic()>=deadline.expires)
 except BaseException as error:
  if transaction is not None and transaction.is_active:
   try:transaction.rollback()
   except Exception:pass
  if commit_started and not committed:
   raise OutcomeUnknown('daily_cutoff_commit_outcome_unknown_use_exact_recovery') from error
  raise
 finally:
  if owned:
   if event.contains(owner,'before_cursor_execute',limit):event.remove(owner,'before_cursor_execute',limit)
   # Sources may retain their own per-transaction event listener. These owned
   # one-operation connection objects are consumed even when an earlier phase
   # failed; do not return stale listeners/timeouts to callers.
   for connection in (source,ledger,owner):
    try:connection.close()
    except Exception:pass
