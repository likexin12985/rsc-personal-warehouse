"""Owned, newly initialized PG16 cluster for local development checks only.

No supplied DSN, host, port, database or existing data directory is accepted.
The process creates a private Unix socket and a new data directory, verifies its
own child PID and the server's data directory/system id, and retains all files.
This does not enable or alter the GitHub-only destructive release gate.
"""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

import psycopg
from psycopg import sql
from sqlalchemy import URL,create_engine
from sqlalchemy.pool import NullPool

DATABASE='rsc_pg16_release_gate'
ROLES=('star_oam_migrator','star_oam_api','star_oam_backup','star_oam_projector','star_oam_edge','edge_inbox')


def _checked_bin(directory):
    directory=Path(directory).resolve(strict=True)
    for name in ('postgres','initdb'):
        program=directory/name
        if not program.is_file() or not os.access(program,os.X_OK):raise ValueError('native PostgreSQL binaries required')
        result=subprocess.run([str(program),'--version'],capture_output=True,text=True,check=True,timeout=10)
        if not re.fullmatch(name+r' \(PostgreSQL\) 16\.\d+\n',result.stdout):raise ValueError('real PostgreSQL 16 required')
    return directory


def _identity(connection,data,pid,started):
    row=connection.execute("SELECT current_database(),current_user,current_setting('server_version_num')::int,"
        "current_setting('data_directory'),current_setting('listen_addresses'),pg_postmaster_start_time(),"
        "(SELECT system_identifier::text FROM pg_control_system())").fetchone()
    if row[:2]!=('postgres','postgres') or not 160000<=row[2]<170000 or Path(row[3]).resolve()!=data \
            or row[4]!='' or not started<=row[5] or int((data/'postmaster.pid').read_text().splitlines()[0])!=pid:
        raise RuntimeError('owned PostgreSQL server identity mismatch')
    return dict(database=row[0],user=row[1],serverVersionNum=row[2],dataDirectory=str(data),tcpListenAddresses=row[4],
        serverStartedAt=row[5].isoformat(),systemIdentifier=row[6],pid=pid)


def _bootstrap(socket_directory):
    # All names are fixed and this function is called only after fresh-cluster
    # identity, empty database set and role set have been proved by the owner.
    with psycopg.connect(host=str(socket_directory),dbname='postgres',user='postgres',autocommit=True) as db:
        db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DATABASE)))
    with psycopg.connect(host=str(socket_directory),dbname=DATABASE,user='postgres',autocommit=True) as db:
        for role in ROLES:
            mode='NOLOGIN' if role=='star_oam_edge' else 'LOGIN'
            db.execute(sql.SQL('CREATE ROLE {} '+mode+' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT').format(sql.Identifier(role)))
            db.execute(sql.SQL('ALTER ROLE {} SET search_path=public').format(sql.Identifier(role)))
        for name, in db.execute('SELECT datname FROM pg_database WHERE datallowconn'):
            db.execute(sql.SQL('REVOKE CONNECT,TEMPORARY ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
        db.execute(sql.SQL('ALTER DATABASE {} OWNER TO star_oam_migrator').format(sql.Identifier(DATABASE)))
        for role in ROLES:
            if role!='star_oam_edge':db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(DATABASE),sql.Identifier(role)))
        db.execute('ALTER SCHEMA public OWNER TO star_oam_migrator')
        db.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
        db.execute('GRANT USAGE,CREATE ON SCHEMA public TO star_oam_migrator')
        db.execute('GRANT USAGE ON SCHEMA public TO star_oam_api,star_oam_backup,star_oam_projector,edge_inbox')
        for object_type in ('TABLES','SEQUENCES'):
            db.execute('ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator IN SCHEMA public REVOKE ALL ON '+object_type+' FROM PUBLIC')
            db.execute('ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator IN SCHEMA public GRANT SELECT ON '+object_type+' TO star_oam_backup')
        db.execute('ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC')


@contextmanager
def native_cluster(*,postgres_bin,artifact_root):
    # libpq environment must never redirect a supposedly local connection.
    if any(key.startswith('PG') for key in os.environ):
        raise ValueError('local PG16 checks require an unconfigured libpq environment')
    binaries=_checked_bin(postgres_bin)
    cloud=Path(__file__).resolve().parents[2]
    artifact_root=Path(artifact_root).resolve()
    if not artifact_root.is_relative_to(cloud/'artifacts'):raise ValueError('local PG evidence must remain under cloud_oam/artifacts')
    artifact_root.mkdir(parents=True,exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix='run-',dir=artifact_root)).resolve()
    data=directory/'data'
    socket_directory=Path(tempfile.mkdtemp(prefix='rsc-pg16-',dir='/tmp')).resolve()
    os.chmod(directory,0o700);os.chmod(socket_directory,0o700)
    state=dict(scope='local-development-checks',githubReleaseGate=False,createdAt=datetime.now(timezone.utc).isoformat(),
        binarySha256=hashlib.sha256((binaries/'postgres').read_bytes()).hexdigest(),runDirectory=str(directory),
        socketDirectory=str(socket_directory),dataDirectory=str(data),status='initializing')
    def save(): (directory/'cluster-state.json').write_text(json.dumps(state,indent=2)+'\n')
    save();process=None;engines={}
    try:
        with (directory/'initdb.log').open('wb') as output:
            subprocess.run([str(binaries/'initdb'),'-D',str(data),'-U','postgres','--encoding=UTF8','--locale=C',
                '--auth-local=trust','--auth-host=reject','--data-checksums'],stdout=output,stderr=subprocess.STDOUT,check=True,timeout=60)
        started=datetime.now(timezone.utc)
        with (directory/'postgres.log').open('wb') as output:
            process=subprocess.Popen([str(binaries/'postgres'),'-D',str(data),'-c','listen_addresses=',
                '-c','unix_socket_directories='+str(socket_directory),'-c','unix_socket_permissions=0700',
                '-c','log_statement=none','-c','log_min_error_statement=panic'],stdout=output,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+30
        while True:
            if process.poll() is not None:raise RuntimeError('new PostgreSQL server stopped before startup')
            try:
                with psycopg.connect(host=str(socket_directory),dbname='postgres',user='postgres',connect_timeout=1,autocommit=True) as db:
                    state['identity']=_identity(db,data,process.pid,started)
                    if db.execute("SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname").fetchall()!=[('postgres',)]:
                        raise RuntimeError('new cluster contains unexpected databases')
                    if db.execute("SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%' ESCAPE '\\' ORDER BY rolname").fetchall()!=[('postgres',)]:
                        raise RuntimeError('new cluster contains unexpected roles')
                break
            except psycopg.OperationalError:
                if time.monotonic()>=deadline:raise RuntimeError('new PostgreSQL server startup timed out') from None
                time.sleep(0.1)
        state['status']='verified_fresh';save()
        _bootstrap(socket_directory)
        for role in ROLES:
            if role=='star_oam_edge':continue
            url=URL.create('postgresql+psycopg',username=role,database=DATABASE,query={'host':str(socket_directory)})
            engines[role]=create_engine(url,poolclass=NullPool,hide_parameters=True,connect_args={'connect_timeout':5})
        state['status']='running_checks';save()
        yield directory,engines
        state['checks']='passed'
    except BaseException:
        state['checks']='failed';raise
    finally:
        for engine in engines.values():engine.dispose()
        if process is not None and process.poll() is None:
            # This exact Popen child is the only server the process may stop.
            if not (data/'postmaster.pid').is_file() or int((data/'postmaster.pid').read_text().splitlines()[0])!=process.pid:
                state['status']='stop_identity_mismatch';save()
                raise RuntimeError('cannot prove local child server identity for shutdown')
            process.send_signal(signal.SIGINT)
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                state['status']='shutdown_pending';save();raise RuntimeError('owned PG16 child is still shutting down') from None
        state['serverExitCode']=process.returncode if process else None
        state['status']='stopped';state['finishedAt']=datetime.now(timezone.utc).isoformat();save()
