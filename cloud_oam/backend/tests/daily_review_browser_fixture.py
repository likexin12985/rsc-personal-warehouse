"""Owned local HTTPS/PG16 browser fixture; never a production entry point.

Requires an already trusted, test-only localhost certificate/key under the
ignored artifacts directory. No TLS bypass, certificate installation, browser
launch, external credentials, supplied database or archived database is used.
The HTTPS probe verifies Python's default trust store; browser trust and the
actual browser workflow must still be recorded independently by the caller.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import Request as UrlRequest, build_opener, HTTPSHandler, ProxyHandler, HTTPRedirectHandler
from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import Response, FileResponse, RedirectResponse

CLOUD = Path(__file__).resolve().parents[2]
ROOT = CLOUD.parent
MAX_OBJECT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_GRANTS = 512
IMMUTABLE_TABLES = ('inventory_transactions', 'inventory_movements', 'stock_balances', 'stock_accounts',
                    'stock_locations', 'inventory_ledger_heads', 'daily_reconciliation_cutoffs')
STORAGE_KEY = re.compile(r'formal-files/v1/daily_reconciliation_evidence/[0-9a-f]{2}/[0-9a-f]{32}')
SHA256 = re.compile(r'[0-9a-f]{64}')
SAFE_HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'}


def certificate_metadata(certificate: Path, private_key: Path, *, root: Path = CLOUD / 'artifacts') -> dict:
    """Inspect the public certificate before ever loading a test private key."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    root = root.resolve()
    for path in (certificate, private_key):
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError('test TLS files must be regular files under artifacts')
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('test TLS files must be regular files')
    if private_key.stat().st_mode & 0o077:
        raise ValueError('test private key must not be group/world accessible')
    cert = x509.load_pem_x509_certificate(certificate.read_bytes())
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns = names.get_values_for_type(x509.DNSName)
        ips = names.get_values_for_type(x509.IPAddress)
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        raise ValueError('test TLS certificate needs explicit localhost SANs and CA=false') from None
    if basic.ca or dns != ['localhost'] or ips != [ipaddress.ip_address('127.0.0.1')] or len(names) != 2:
        raise ValueError('only the exact localhost and 127.0.0.1 leaf certificate is accepted')
    now = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= now or cert.not_valid_after_utc <= now + timedelta(minutes=31):
        raise ValueError('test TLS certificate is not valid for the complete fixture lifetime')
    # Only the above narrowly identified, local-only test key is loaded here.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certificate), str(private_key))
    return dict(certificateSha256=cert.fingerprint(hashes.SHA256()).hex(),
                expiresAt=cert.not_valid_after_utc.isoformat(), hostnames=['localhost', '127.0.0.1'])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def tls_open(request: UrlRequest):
    # Ignore ambient HTTP proxies; the only destinations created here are the
    # two loopback hosts. Default certificate/hostname checks stay enabled.
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    return opener.open(request, timeout=5)


@dataclass(frozen=True)
class Grant:
    method: str
    storage_key: str
    expires: int
    headers: dict[str, str]
    size: int = 0
    sha256: str = ''


class HttpsObjects:
    """Synthetic storage with real HTTP bytes; no metadata materialization shortcut."""
    provider_code = 'aliyun_oss_v2'

    def __init__(self, *, app_origin: str, object_origin: str, clock=time.time):
        # Import only after run() selects its local-only settings. The formal
        # services package initializes application configuration on import.
        from app.formal_services import file_storage
        self._types = file_storage
        self.app_origin = app_origin
        self.object_origin = object_origin
        self.clock = clock
        self._secret = secrets.token_bytes(32)
        self._grants: dict[str, Grant] = {}
        self._objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self._lock = threading.Lock()
        self.counts = {'put': 0, 'head': 0, 'get': 0, 'rejected': 0}

    def _url(self, grant: Grant) -> str:
        with self._lock:
            self._grants = {k: v for k, v in self._grants.items() if v.expires > self.clock()}
            if len(self._grants) >= MAX_GRANTS:
                raise self._types.FileStorageError('synthetic object grant capacity exceeded')
            nonce = secrets.token_hex(16)
            digest = hmac.new(self._secret, nonce.encode(), hashlib.sha256).hexdigest()
            token = nonce + '.' + digest
            self._grants[token] = grant
        return self.object_origin + '/__objects/' + token

    def _checked_grant(self, token: str, method: str) -> Grant:
        nonce, _, signature = token.partition('.')
        expected = hmac.new(self._secret, nonce.encode(), hashlib.sha256).hexdigest()
        with self._lock:
            grant = self._grants.get(token)
        if not hmac.compare_digest(expected, signature) or grant is None or grant.method != method or grant.expires <= self.clock():
            self.counts['rejected'] += 1
            raise HTTPException(403, 'synthetic object grant rejected')
        return grant

    def create_upload_intent(self, *, storage_key, file_id, sha256, size_bytes, mime_type, ttl_seconds):
        if not STORAGE_KEY.fullmatch(storage_key) or not SHA256.fullmatch(sha256) or not 0 < size_bytes <= MAX_OBJECT_BYTES \
                or not 1 <= ttl_seconds <= 900 or str(UUID(file_id)) != file_id:
            raise self._types.FileStorageError('invalid synthetic upload coordinates')
        headers = {'Content-Type': mime_type, 'x-oss-meta-sha256': sha256,
                   'x-oss-meta-file-id': file_id, 'x-oss-forbid-overwrite': 'true'}
        expiry = int(self.clock()) + ttl_seconds
        url = self._url(Grant('PUT', storage_key, expiry, headers, size_bytes, sha256))
        return self._types.UploadIntent(storage_key, url, datetime.fromtimestamp(expiry, timezone.utc), headers)

    def create_download_intent(self, *, storage_key, ttl_seconds):
        with self._lock:
            present = storage_key in self._objects
        if not present or not 1 <= ttl_seconds <= 900:
            raise self._types.FileStorageError('synthetic object is unavailable')
        expiry = int(self.clock()) + ttl_seconds
        return self._types.DownloadIntent(storage_key, self._url(Grant('GET', storage_key, expiry, {})),
                              datetime.fromtimestamp(expiry, timezone.utc))

    def head_object(self, *, storage_key):
        # Formal /complete calls back over verified HTTPS, independently of the
        # request that wrote the bytes. No requested hash becomes stored facts.
        url = self._url(Grant('HEAD', storage_key, int(self.clock()) + 30, {}))
        try:
            with tls_open(UrlRequest(url, method='HEAD')) as response:
                return self._types.StoredObjectHead(storage_key, int(response.headers['Content-Length']),
                    response.headers['Content-Type'],
                    {'sha256': response.headers['x-oss-meta-sha256'], 'file-id': response.headers['x-oss-meta-file-id']},
                    response.headers['ETag'])
        except (URLError, ValueError, KeyError, TypeError):
            raise self._types.FileStorageError('synthetic HTTPS HEAD verification failed') from None

    def evidence(self):
        with self._lock:
            return dict(counts=dict(self.counts), objects=[dict(storageKey=key, sizeBytes=len(body),
                sha256=hashlib.sha256(body).hexdigest()) for key, (body, _) in sorted(self._objects.items())])

    def mount(self, app: FastAPI):
        @app.api_route('/__objects/{token}', methods=['OPTIONS', 'PUT', 'HEAD', 'GET'])
        async def objects(token: str, request: Request):
            # The app's host-only Cookie lives on 127.0.0.1; objects use localhost.
            if str(request.base_url).rstrip('/') != self.object_origin or any(
                    name in request.headers for name in ('cookie', 'authorization', 'referer')):
                self.counts['rejected'] += 1
                raise HTTPException(403, 'synthetic object transport boundary rejected')
            origin = request.headers.get('origin')
            cors = dict(SAFE_HEADERS)
            if origin:
                if origin != self.app_origin:
                    raise HTTPException(403, 'synthetic object origin rejected')
                cors.update({'Access-Control-Allow-Origin': origin, 'Vary': 'Origin'})
            if request.method == 'OPTIONS':
                requested = {h.strip().lower() for h in request.headers.get('access-control-request-headers', '').split(',') if h.strip()}
                expected = {'content-type', 'x-oss-meta-sha256', 'x-oss-meta-file-id', 'x-oss-forbid-overwrite'}
                if origin != self.app_origin or request.headers.get('access-control-request-method') != 'PUT' or not requested <= expected:
                    raise HTTPException(403, 'synthetic object preflight rejected')
                self._checked_grant(token, 'PUT')
                return Response(status_code=204, headers={**cors, 'Access-Control-Allow-Methods': 'PUT',
                    'Access-Control-Allow-Headers': ', '.join(sorted(expected))})
            grant = self._checked_grant(token, request.method)
            if request.method == 'PUT':
                if origin != self.app_origin or any(request.headers.get(k) != v for k, v in grant.headers.items()):
                    raise HTTPException(403, 'synthetic object signed headers rejected')
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > grant.size:
                        raise HTTPException(413, 'synthetic object size rejected')
                if len(body) != grant.size or hashlib.sha256(body).hexdigest() != grant.sha256:
                    raise HTTPException(422, 'synthetic object bytes rejected')
                with self._lock:
                    if grant.storage_key in self._objects:
                        raise HTTPException(409, 'synthetic object overwrite rejected')
                    if sum(len(v[0]) for v in self._objects.values()) + len(body) > MAX_TOTAL_BYTES:
                        raise HTTPException(507, 'synthetic object capacity exceeded')
                    self._objects[grant.storage_key] = (bytes(body), dict(grant.headers))
                    self.counts['put'] += 1
                return Response(status_code=200, headers=cors)
            with self._lock:
                entry = self._objects.get(grant.storage_key)
                if entry is None:
                    raise HTTPException(404, 'synthetic object not found')
                body, headers = entry
                self.counts[request.method.lower()] += 1
            output = {**cors, **headers, 'Content-Length': str(len(body)), 'ETag': hashlib.sha256(body).hexdigest()}
            return Response(content=body if request.method == 'GET' else b'', headers=output)



@contextmanager
def owned_https(app, sock, certificate, private_key, *, state=None):
    """Stop this listener before returning control to the database owner."""
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, proxy_headers=False,
        timeout_graceful_shutdown=5, ssl_certfile=str(certificate), ssl_keyfile=str(private_key)))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    state = state if state is not None else {}
    state['stopped'] = False
    try:
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        if not server.started:
            raise RuntimeError('owned HTTPS server failed to start')
        yield server, thread
    finally:
        server.should_exit = True
        if thread.ident is not None:
            thread.join(10)
            if thread.is_alive():
                server.force_exit = True
                sock.close()
                thread.join(5)
        state['stopped'] = not thread.is_alive()
        if not state['stopped']:
            raise RuntimeError('owned HTTPS server did not stop')


def freeze_inputs() -> dict[str, str]:
    paths = set()
    for folder, suffixes in [('backend', ('.py', '.ini', '.txt')), ('edge_sync', ('.py',)),
                             ('deployment', ('.sql',)), ('frontend/src', ('.ts', '.tsx', '.css')),
                             ('frontend/build', ('.mjs',)),
                             ('frontend/dist', ()), ('frontend/dist-warehouse', ())]:
        for path in (CLOUD / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and (not suffixes or path.suffix in suffixes):
                paths.add(path)
    paths.add(CLOUD / 'alembic.ini')
    paths.update(p for p in (CLOUD / 'frontend').glob('*') if p.is_file() and p.suffix in ('.json', '.ts', '.html'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def verify_inputs(manifest):
    if freeze_inputs() != manifest:
        raise RuntimeError('fixture input changed during verification')


def _bootstrap(engines, directory: Path, artifact: Path, postgres_bin: Path):
    """Only the fresh owned local cluster is admitted, using the empty-db route."""
    from sqlalchemy import URL, create_engine, text
    from sqlalchemy.pool import NullPool
    from local_pg16_cluster import DATABASE
    owner = engines['star_oam_migrator']
    state = json.loads((directory / 'cluster-state.json').read_text())
    unix = state['socketDirectory']
    admin = create_engine(URL.create('postgresql+psycopg', username='postgres', database=DATABASE,
        query={'host': unix}), poolclass=NullPool, hide_parameters=True)
    try:
        with owner.connect() as db:
            if db.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) != 0:
                raise RuntimeError('fixture requires its own empty database')
        environment = dict(os.environ, OAM_ENVIRONMENT='production',
            OAM_DATABASE_URL=owner.url.render_as_string(hide_password=False),
            OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator', OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
        commands = [[sys.executable, '-m', 'alembic', '-c', str(CLOUD / 'alembic.ini'), 'upgrade', 'head']]
        for name, variables in [('create_oam_edge_staging.sql', {'edge_role': 'edge_inbox'}),
                ('provision_oam_work_order_source.sql', {'edge_source_instance': 'synthetic-daily-browser',
                    'company_id': 'synthetic-company', 'org_code': 'synthetic-org', 'scope_key': 'work-orders:recent-30d'})]:
            cmd = [str(postgres_bin / 'psql'), '-X', '-w', '-h', unix, '-U', 'star_oam_migrator',
                '--dbname', DATABASE, '--set=ON_ERROR_STOP=1']
            for key, value in variables.items():
                cmd.extend(['-v', key + '=' + value])
            commands.append(cmd + ['-f', str(CLOUD / 'deployment' / name)])
        for index, cmd in enumerate(commands):
            with (artifact / f'bootstrap-{index}.log').open('wb') as log:
                subprocess.run(cmd, cwd=CLOUD, env=environment, stdout=log, stderr=subprocess.STDOUT,
                               timeout=240, check=True)
        return admin
    except BaseException:
        admin.dispose()
        raise


def mount_identity_reads(app, auth_router, access_router):
    # Reuse the production read endpoints and their dependencies, without
    # exposing SMS/WeChat, password, refresh, logout or grant mutations.
    for router, path in ((auth_router, '/auth/me'), (access_router, '/access/context')):
        readonly = APIRouter()
        readonly.routes = [route for route in router.routes
                           if route.path == path and route.methods == {'GET'}]
        if len(readonly.routes) != 1:
            raise RuntimeError('exact formal identity read route is required')
        app.include_router(readonly, prefix='/api')


def _app(engines, settings, identities, app_origin, object_origin):
    from sqlalchemy.orm import Session
    from fastapi.staticfiles import StaticFiles
    from app.config import get_settings
    from app.database import get_db
    from app import dependencies
    from app.routers import auth, access, formal_daily_reconciliation, formal_files
    dependencies.get_settings = lambda: settings
    auth.settings = settings
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        expected = object_origin if request.url.path.startswith('/__objects/') else app_origin
        if request.url.scheme != 'https' or str(request.base_url).rstrip('/') != expected \
                or request.client is None or request.client.host not in ('127.0.0.1', '::1'):
            return Response(status_code=403, headers=SAFE_HEADERS)
        response = await call_next(request)
        response.headers.update(SAFE_HEADERS)
        return response
    def database():
        with Session(engines['star_oam_api']) as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_settings] = lambda: settings
    storage = HttpsObjects(app_origin=app_origin, object_origin=object_origin)
    app.dependency_overrides[formal_files.get_formal_file_storage_adapter] = lambda: storage
    storage.mount(app)
    mount_identity_reads(app, auth.router, access.router)
    for router in (formal_daily_reconciliation.router, formal_files.router):
        app.include_router(router, prefix='/api')
    @app.get('/__fixture/health')
    def health():
        return {'synthetic': True}
    @app.get('/__fixture/identity/{identity}')
    def bootstrap(identity: str, request: Request):
        if identity not in identities or request.headers.get('sec-fetch-site', 'none') not in ('none', 'same-origin'):
            raise HTTPException(404)
        response = RedirectResponse('/xx/daily-reconciliations', status_code=303)
        response.set_cookie('access_token', identities[identity]['token'], secure=True, httponly=True,
                            samesite='strict', path='/')
        return response
    app.mount('/xx/assets', StaticFiles(directory=CLOUD / 'frontend/dist-warehouse/assets'))
    @app.get('/xx/{path:path}')
    @app.get('/xx')
    def private(path: str = ''):
        return FileResponse(CLOUD / 'frontend/dist-warehouse/index.html')
    return app, storage


def run(args) -> int:
    """Own every child process and retain safe evidence even on a failed run."""
    certificate, private_key = Path(args.certificate), Path(args.private_key)
    tls = certificate_metadata(certificate, private_key)
    if not 30 <= args.lifetime_seconds <= 1800:
        raise ValueError('fixture lifetime must be between 30 and 1800 seconds')
    artifact = Path(args.artifact_dir).resolve()
    if not artifact.is_relative_to((CLOUD / 'artifacts').resolve()):
        raise ValueError('fixture evidence must be under artifacts')
    artifact.mkdir(parents=True, mode=0o700, exist_ok=False)
    for path in ('frontend/dist-warehouse/index.html', 'frontend/dist/index.html'):
        if not (CLOUD / path).is_file():
            raise ValueError('both current frontend builds are required')
    manifest = freeze_inputs()
    (artifact / 'source-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    result = dict(status='starting', syntheticData=True, realOss=False, productionStartup=False,
                  githubReleaseGate=False, browserWorkflowVerified=False, browserTrustVerified=False,
                  clusterStopped=False, serverStopped=True, tls=tls)
    def save(name, value):
        (artifact / name).write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')
    # These credentials are freshly generated test values, never written to an artifact.
    os.environ.update(OAM_ENVIRONMENT='test', OAM_DATABASE_URL='sqlite://', OAM_DATABASE_SCHEMA_MODE='alembic',
        OAM_JWT_SECRET=secrets.token_urlsafe(48), OAM_LEGACY_PROTOTYPE_WRITES_ENABLED='false')
    sys.path[:0] = [str(ROOT), str(CLOUD / 'backend'), str(CLOUD / 'backend/tests')]
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from app.config import get_settings
    from app.daily_reconciliation.capture_provisioning import provision_capture_roles
    from app.daily_reconciliation.capture_role_contract import ROLES
    from app.database_security import validate_production_database_security
    from local_pg16_cluster import native_cluster, DATABASE
    from pg16_daily_review_fixture import prepare
    from pg16_daily_review_runtime import facts
    postgres_bin = CLOUD / 'artifacts/pg16-native-20260920/install/bin'
    directory = admin = server = thread = sock = storage = None
    listener_state = {'stopped': True}
    readers = {}
    try:
        with native_cluster(postgres_bin=postgres_bin, artifact_root=artifact / 'native-checks') as (directory, engines):
            admin = _bootstrap(engines, directory, artifact, postgres_bin)
            passwords = {role: secrets.token_urlsafe(40) for role in ROLES}
            with admin.begin() as db:
                observed = provision_capture_roles(db, database=DATABASE, apply=False)
                if observed['configured']:
                    raise RuntimeError('fresh fixture capture roles unexpectedly exist')
                provision_capture_roles(db, database=DATABASE, passwords=passwords, apply=True)
            readers = {role: create_engine(engines['star_oam_migrator'].url.set(username=role, password=passwords[role]),
                poolclass=NullPool, hide_parameters=True) for role in ROLES}
            identities, cutoffs = prepare(engines['star_oam_migrator'], engines['edge_inbox'], readers)
            validate_production_database_security(engines['star_oam_api'], expected_runtime_role='star_oam_api',
                                                  expected_migration_role='star_oam_migrator')
            before = facts(engines['star_oam_migrator']); save('facts-before.json', before)
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            app_origin, object_origin = f'https://127.0.0.1:{port}', f'https://localhost:{port}'
            settings = get_settings().model_copy(update=dict(environment='production',
                database_url=engines['star_oam_api'].url.render_as_string(hide_password=False), app_origin=app_origin,
                file_storage_enabled=True, file_storage_provider='aliyun_oss_v2', file_storage_region='cn-shanghai',
                file_storage_bucket='synthetic-private-bucket', file_idempotency_hmac_secret=secrets.token_urlsafe(48)))
            app, storage = _app(engines, settings, identities, app_origin, object_origin)
            with owned_https(app, sock, certificate, private_key, state=listener_state) as (server, thread):
                with tls_open(UrlRequest(app_origin + '/__fixture/health')) as response:
                    if response.status != 200:
                        raise RuntimeError('verified local HTTPS health failed')
                result.update(status='ready', origin=app_origin, objectOrigin=object_origin, pid=os.getpid(),
                    tlsVerifiedByPythonDefaultTrust=True, cutoffIds=[str(x) for x in cutoffs], apiRole='star_oam_api',
                    clusterDirectory=str(directory), lifetimeSeconds=args.lifetime_seconds)
                save('ready.json', result); print(json.dumps({'status': 'ready', 'origin': app_origin}), flush=True)
                deadline = time.monotonic() + args.lifetime_seconds
                while not (artifact / 'STOP').exists() and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.2)
                if not thread.is_alive():
                    raise RuntimeError('owned HTTPS server stopped unexpectedly')
            after = facts(engines['star_oam_migrator']); save('facts-after.json', after)
            result['changedTables'] = [name for name in before if before[name] != after[name]]
            if any(before[name] != after[name] for name in IMMUTABLE_TABLES):
                raise RuntimeError('browser review changed stock or cutoff facts')
            result['immutableFactsUnchanged'] = True
            result['objects'] = storage.evidence()
            from sqlalchemy import text
            with engines['star_oam_migrator'].connect() as db:
                result['events'] = [dict(r) for r in db.execute(text(
                    'SELECT cutoff_id,version,payload_jsonb FROM daily_review_events WHERE cutoff_id=ANY(:ids) ORDER BY cutoff_id,version'),
                    {'ids': cutoffs}).mappings()]
                result['seals'] = db.scalar(text('SELECT count(*) FROM daily_review_request_seals WHERE cutoff_id=ANY(:ids)'), {'ids': cutoffs})
            verify_inputs(manifest); result['inputHashesUnchanged'] = True
            result['status'] = 'stopped_after_checks'
    except BaseException as exc:
        # Exception messages may include a signed URL or connection credential.
        result.update(status='failed', errorType=type(exc).__name__)
    finally:
        if server:
            server.should_exit = True
        if thread:
            thread.join(15)
        if sock:
            sock.close()
        for reader in readers.values():
            reader.dispose()
        if admin:
            admin.dispose()
        if directory:
            result['clusterStopped'] = json.loads((directory / 'cluster-state.json').read_text())['status'] == 'stopped'
        result['serverStopped'] = listener_state['stopped'] and (thread is None or not thread.is_alive())
        if storage:
            result['objects'] = storage.evidence()
        save('result.json', result)
        print(json.dumps({k: result.get(k) for k in ('status', 'clusterStopped', 'serverStopped', 'errorType')}), flush=True)
    return 0 if result['status'] == 'stopped_after_checks' and result['clusterStopped'] and result['serverStopped'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-dir', required=True)
    parser.add_argument('--certificate', required=True)
    parser.add_argument('--private-key', required=True)
    parser.add_argument('--lifetime-seconds', type=int, default=900)
    args = parser.parse_args()
    try:
        return run(args)
    except BaseException as exc:
        # Validation fails before any server/database starts; expose no paths/keys.
        print(json.dumps({'status': 'preflight_failed', 'errorType': type(exc).__name__}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
