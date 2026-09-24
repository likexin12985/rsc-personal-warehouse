"""Fixed, bounded API-role command worker; never retries an uncertain outcome."""
from threading import BoundedSemaphore
import time
from .process_entry import run_owned_job,ProcessOutcomeUnknown,ProcessEntryError

_CAPACITY=BoundedSemaphore(4)
class ReviewEntryBusy(RuntimeError):pass

def _review_worker(payload,expires):
    return _transaction_worker(payload,expires,reference_job=False)

def _recovery_worker(payload,expires):
    return _transaction_worker(payload,expires,reference_job=True)

def _transaction_worker(payload,expires,*,reference_job):
    from sqlalchemy import create_engine,event
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import NullPool
    from sqlalchemy.exc import DBAPIError
    from pydantic import ValidationError
    from app.formal_access import FormalAccessError
    from app.inventory_control_configuration import ControlConfigurationError
    from . import review_service as service,review_core as core
    from . import recovery_service
    from .recovery_http import Reference
    from app.formal_services.audit_chain import AuditChainError
    from .deadline_entry import Deadline
    expected={'database_url','reference','access_token','expected_authorization_version','seal'} if reference_job else {'database_url','command','access_token','expected_authorization_version','recover'}
    if set(payload)!=expected:
        raise ProcessEntryError('invalid_review_job')
    if not payload['database_url'].startswith('postgresql+psycopg://') or type(payload['seal' if reference_job else 'recover']) is not bool:
        raise ProcessEntryError('invalid_review_database')
    command=Reference.model_validate(payload['reference']) if reference_job else core.REQUEST.validate_python(payload['command'])
    deadline=Deadline(expires)
    engine=create_engine(payload['database_url'],poolclass=NullPool,hide_parameters=True,
        connect_args={'connect_timeout':max(2,min(5,deadline.reader_seconds())),'tcp_user_timeout':1000})
    def limit(conn,cursor,statement,parameters,context,many):
        remaining=deadline.milliseconds()
        cursor.execute('SET LOCAL statement_timeout = '+str(remaining))
        cursor.execute('SET LOCAL lock_timeout = '+str(min(5000,remaining)))
        cursor.execute('SET LOCAL idle_in_transaction_session_timeout = '+str(remaining))
    try:
        with engine.connect() as conn:
            event.listen(conn,'before_cursor_execute',limit)
            with Session(bind=conn,autoflush=False) as db:
                commit_started=False
                try:
                    if conn.connection.driver_connection.info.server_version//10000!=16:
                        raise ProcessEntryError('postgresql16_required')
                    auth=dict(access_token=payload['access_token'],expected_authorization_version=payload['expected_authorization_version'])
                    result=(recovery_service.execute(db,**auth,reference=command,seal=payload['seal']) if reference_job else
                        (service.recover if payload['recover'] else service.execute)(db,**auth,command=command))
                    db.flush();deadline.milliseconds()
                    # COMMIT includes the deferred review/audit checks. A lost
                    # commit acknowledgement remains unknown even after cleanup.
                    conn.exec_driver_sql('SET LOCAL statement_timeout = '+str(deadline.milliseconds()))
                    commit_started=True;db.commit()
                    return {'outcome':'observed',('recovery' if reference_job else 'receipt'):result}
                except (core.ReviewError,ControlConfigurationError,FormalAccessError,ValidationError,DBAPIError,AuditChainError) as exc:
                    db.rollback()
                    if commit_started:raise
                    if isinstance(exc,ControlConfigurationError):return {'outcome':'rejected','code':'daily_review_authentication_required','status':401}
                    if isinstance(exc,FormalAccessError):return {'outcome':'rejected','code':'daily_review_forbidden','status':403}
                    if isinstance(exc,ValidationError):return {'outcome':'rejected','code':'daily_review_invalid_command','status':422}
                    if isinstance(exc,AuditChainError):return {'outcome':'rejected','code':'daily_review_recovery_evidence_invalid','status':503}
                    if isinstance(exc,DBAPIError):return {'outcome':'rejected','code':'daily_review_database_rejected','status':409 if getattr(exc.orig,'sqlstate',None) in {'23514','23505','23503'} else 503}
                    codes={'forbidden':403,'authorization_changed':409,'not_found':404,'request_conflict':409,'version_conflict':409,
                        'item_version_conflict':409,'cutoff_binding_changed':409,'evidence_missing':409,'evidence_not_owned':403,
                        'evidence_purpose_invalid':409,'evidence_changed':409,'already_approved':409,'unexplained_differences':409,
                        'self_review_forbidden':403,'review_item_not_explained':409,'item_not_found':404,'matched_item_cannot_be_explained':409,
                        'request_sealed':409,'recovery_evidence_invalid':503}
                    name=str(exc).removeprefix('daily_review_')
                    return {'outcome':'rejected','code':'daily_review_'+name if name in codes else 'daily_review_rejected','status':codes.get(name,409)}
                finally:event.remove(conn,'before_cursor_execute',limit)
    finally:engine.dispose()

def execute(*,database_url,command,access_token,expected_authorization_version,recover=False,maximum_seconds=15):
    return _run(_review_worker,dict(database_url=database_url,command=command,access_token=access_token,
        expected_authorization_version=expected_authorization_version,recover=recover),maximum_seconds)

def execute_recovery(*,database_url,reference,access_token,expected_authorization_version,seal=False,maximum_seconds=15):
    return _run(_recovery_worker,dict(database_url=database_url,reference=reference,access_token=access_token,
        expected_authorization_version=expected_authorization_version,seal=seal),maximum_seconds)

def _run(worker,payload,maximum_seconds):
    # DB coordinates and deadline are server configuration, never request fields.
    if not _CAPACITY.acquire(blocking=False):raise ReviewEntryBusy('daily_review_capacity_busy')
    release=True
    try:
        return run_owned_job(worker,payload,maximum_seconds=maximum_seconds)
    except ProcessOutcomeUnknown:raise
    except ProcessEntryError:
        # An unconfirmed child cleanup must not silently permit more workers.
        release=False;raise
    finally:
        if release:_CAPACITY.release()
