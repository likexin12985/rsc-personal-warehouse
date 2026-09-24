"""Supervised, one-shot owner entry for daily mapping decisions.

Only fixed code paths are dispatched. A lost worker or COMMIT acknowledgement is
unknown and must be checked with the original command and review digest.
"""
from .process_entry import ProcessEntryError, run_owned_job

REQUIRED_HEAD = '20261119_0140'


def _worker(payload, expires):
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import NullPool
    from psycopg.conninfo import conninfo_to_dict
    import psycopg

    from .deadline_entry import Deadline
    from . import mapping

    if set(payload) != {'conninfo', 'database_name', 'command', 'access_token',
                        'expected_authorization_version', 'operation', 'review_sha256'}:
        raise ProcessEntryError('daily_mapping_job_invalid')
    operation = payload['operation']
    if operation not in ('preview', 'apply', 'status'):
        raise ProcessEntryError('daily_mapping_operation_invalid')
    options = conninfo_to_dict(payload['conninfo'])
    if set(options) - {'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'sslrootcert'} or \
            any(not options.get(key) for key in ('host', 'port', 'dbname', 'user', 'password')) or \
            options['user'] != 'star_oam_migrator' or options['dbname'] != payload['database_name']:
        raise ProcessEntryError('daily_mapping_database_config_invalid')
    command = mapping.MappingCommand.model_validate(payload['command'])
    deadline = Deadline(expires)

    def connect():
        remaining = deadline.reader_seconds()
        return psycopg.connect(**dict(options, connect_timeout=max(2, remaining)))

    engine = create_engine('postgresql+psycopg://', creator=connect,
                           poolclass=NullPool, hide_parameters=True)
    def limit(conn, cursor, statement, parameters, context, many):
        remaining = deadline.milliseconds()
        cursor.execute('SET LOCAL statement_timeout = ' + str(remaining))
        cursor.execute('SET LOCAL lock_timeout = ' + str(min(5000, remaining)))
        cursor.execute('SET LOCAL idle_in_transaction_session_timeout = ' + str(remaining))

    try:
        with engine.connect() as connection:
            info = connection.connection.driver_connection.info
            if (info.server_version // 10000 != 16 or info.user != 'star_oam_migrator'
                    or info.dbname != payload['database_name'] or info.host != options['host']
                    or str(info.port) != str(options['port'])):
                raise ProcessEntryError('daily_mapping_database_identity_mismatch')
            event.listen(connection, 'before_cursor_execute', limit)
            transaction = connection.begin()
            try:
                with Session(bind=connection, autoflush=False) as db:
                    if db.execute(text('SELECT current_user, session_user, current_database()')).one() != \
                            ('star_oam_migrator', 'star_oam_migrator', payload['database_name']) or \
                            tuple(db.scalars(text('SELECT version_num FROM alembic_version'))) != (REQUIRED_HEAD,):
                        raise ProcessEntryError('daily_mapping_database_head_mismatch')
                    kwargs = dict(access_token=payload['access_token'],
                                  expected_authorization_version=payload['expected_authorization_version'],
                                  command=command)
                    if operation == 'preview':
                        result = mapping.preview_inventory_control_mapping(db, **kwargs)
                        review = result['review']
                        receipt = dict(review_sha256=result['review_sha256'],
                            subject=review['subject'],
                            evidence_file_id=str(command.evidence_file_id),
                            rules_revision=command.rules['revision'] if command.rules else None,
                            history_count=len(review['history']),
                            projection_published=False, start_ready=False)
                    elif operation == 'status':
                        receipt = mapping.read_inventory_control_mapping(
                            db, **kwargs, review_sha256=payload['review_sha256'])
                    else:
                        receipt = mapping.execute_inventory_control_mapping(
                            db, **kwargs, review_sha256=payload['review_sha256'])
                        db.flush()
                        deadline.milliseconds()
                        connection.exec_driver_sql('SET LOCAL statement_timeout = ' + str(deadline.milliseconds()))
                    if operation == 'apply':
                        transaction.commit()
                    else:
                        transaction.rollback()
                    return dict(operation=operation, **receipt)
            finally:
                if transaction.is_active:
                    transaction.rollback()
                event.remove(connection, 'before_cursor_execute', limit)
    finally:
        engine.dispose()


def execute(*, conninfo, database_name, command, access_token,
            expected_authorization_version, operation, review_sha256=None,
            maximum_seconds=10):
    payload = dict(conninfo=conninfo, database_name=database_name, command=command,
                   access_token=access_token,
                   expected_authorization_version=expected_authorization_version,
                   operation=operation, review_sha256=review_sha256)
    return run_owned_job(_worker, payload, maximum_seconds=maximum_seconds)
