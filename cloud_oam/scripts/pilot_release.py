#!/usr/bin/env python3
"""Bind a reviewed pilot prepare/start to immutable local Docker artifacts.

Only opaque digests and Docker identities enter the durable receipt. Resolved
Compose environments stay in a private temporary file and never reach output.
Static bind mounts are copied into a private, retained attempt directory: a
running database must not lose its scripts when this process exits.
"""
from __future__ import annotations
import ast
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urlsplit

if __package__:
    from .pilot_preflight import checks_for, strict_object, PILOT_PROJECT_RE, IMAGE_TAG_RE
    from .pilot_network_preflight import check_networks
else:
    from pilot_preflight import checks_for, strict_object, PILOT_PROJECT_RE, IMAGE_TAG_RE
    from pilot_network_preflight import check_networks

ID = re.compile(r"sha256:[0-9a-f]{64}$")
HOST_IMAGE_ARCHITECTURES = {'x86_64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}
CONTAINER = re.compile(r"[0-9a-f]{64}$")
SKIP_DIRS = {'.git', '.venv', 'node_modules', 'artifacts', 'dist', 'dist-warehouse',
             '__pycache__', '.pytest_cache', '.test_uploads', 'coverage', 'runtime',
             'tmp', 'exports', 'backups', 'outbox', 'inbox', 'quarantine', 'uploads'}
SCHEMA = 'rsc.pilot.prepare-receipt.v1'

class Refused(RuntimeError):
    pass

def require(ok, code):
    if not ok:
        raise Refused(code)

def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()

def digest(value):
    return hashlib.sha256(value).hexdigest()

def plain_file(path):
    require(path.is_file() and path == path.resolve(), 'input_file_invalid')
    return path.read_bytes()

def tree_digest(path, *, candidate=False):
    if path.is_file():
        return digest(encoded([stat.S_IMODE(path.stat().st_mode), digest(plain_file(path))]))
    require(path.is_dir() and not path.is_symlink(), 'input_directory_invalid')
    files = {'.': ['directory', stat.S_IMODE(path.stat().st_mode)]}
    for parent, dirs, names in os.walk(path, followlinks=False):
        relative_parent = Path(parent).relative_to(path).as_posix()
        inside_image_sources = relative_parent == 'frontend' or relative_parent.startswith(('frontend/', 'backend/app'))
        skipped = ({'__pycache__'} if not candidate or relative_parent.startswith('backend/app') else set()) if not candidate or inside_image_sources else SKIP_DIRS
        if candidate and relative_parent == 'frontend':
            skipped = skipped | {'node_modules', 'dist', 'dist-warehouse', '.git', 'coverage'}
        dirs[:] = sorted(d for d in dirs if d not in skipped)
        for name in dirs:
            require(not (Path(parent)/name).is_symlink(), 'input_symlink_refused')
            directory = Path(parent)/name
            files[directory.relative_to(path).as_posix()+'/'] = ['directory', stat.S_IMODE(directory.stat().st_mode)]
        for name in sorted(names):
            relative = (Path(parent)/name).relative_to(path).as_posix()
            in_build_sources = relative.startswith(('frontend/', 'backend/app/', 'deployment/'))
            if name.endswith('.pyc') and not relative.startswith('frontend/'):
                continue
            if candidate and not in_build_sources:
                if (name == '.env' or name.startswith('.test_') or name in {'.DS_Store', '.coverage'}
                        or name.lower().endswith(('.md', '.db', '.sqlite', '.sqlite3', '.log', '.pid', '.tsbuildinfo'))
                        or re.search(r'\.(?:db|sqlite|sqlite3)-(?:wal|shm|journal)$', name)):
                    continue
            file = Path(parent)/name
            require(not file.is_symlink(), 'input_symlink_refused')
            if file.is_file():
                files[file.relative_to(path).as_posix()] = [stat.S_IMODE(file.stat().st_mode), digest(file.read_bytes())]
    require(bool(files), 'input_directory_empty')
    return digest(encoded(files))

def private_directory(path):
    require(path.is_absolute() and path == path.resolve(), 'state_path_invalid')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.stat()
    require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == os.getuid()
            and stat.S_IMODE(metadata.st_mode) == 0o700, 'state_directory_permissions')
    return path

def expected_head(root):
    revisions = {}
    for file in (root/'backend/alembic/versions').glob('*.py'):
        values = {}
        for node in ast.parse(plain_file(file)).body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                key = node.target.id
                value = node.value
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                key = node.targets[0].id
                value = node.value
            else:
                continue
            if key in ('revision', 'down_revision'):
                values[key] = ast.literal_eval(value)
        if 'revision' in values:
            require(values['revision'] not in revisions, 'candidate_migration_graph_invalid')
            revisions[values['revision']] = values.get('down_revision')
    parents = {item for parent in revisions.values() for item in
               (parent if isinstance(parent, (list, tuple)) else [parent]) if item is not None}
    heads = set(revisions)-parents
    require(len(heads) == 1 and parents <= set(revisions), 'candidate_migration_graph_invalid')
    return heads.pop()

def decoded_config(value):
    # Compose config canonical JSON doubles every dollar for safe re-parsing.
    # Decode exactly one layer only for filesystem and semantic validation.
    if isinstance(value, str):
        return value.replace('$$', '$')
    if isinstance(value, list):
        return [decoded_config(item) for item in value]
    if isinstance(value, dict):
        return {key: decoded_config(item) for key, item in value.items()}
    return value

def atomic_receipt(path, value):
    # link is an atomic create-if-absent operation; replace must never overwrite
    # a previously successful receipt, even if another writer ignores our lock.
    temporary = path.parent/('.receipt-'+uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(encoded(value)+b'\n'); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)

def load_receipt(path):
    metadata = path.lstat()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid()
            and stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_size <= 1024*1024,
            'receipt_permissions_invalid')
    value = json.loads(path.read_text(), object_pairs_hook=strict_object)
    require(isinstance(value, dict) and set(value) == {
        'schema','project','tag','input_sha256','source_sha256','configuration_sha256',
        'head','images','database','static_mounts'}, 'receipt_shape_invalid')
    require(value['schema'] == SCHEMA, 'receipt_schema_invalid')
    return value

def host_public_tcp_ports(output):
    """Parse numeric local addresses from `ss -H -ltn`; refuse ambiguity."""
    occupied = set()
    for line in output.splitlines():
        fields = line.split()
        require(len(fields) >= 5 and fields[0] == 'LISTEN', 'host_socket_inspection_invalid')
        local = fields[3]
        require(':' in local, 'host_socket_inspection_invalid')
        port_text = local.rsplit(':', 1)[1]
        require(port_text.isascii() and port_text.isdecimal(), 'host_socket_inspection_invalid')
        port = int(port_text)
        require(0 < port <= 65535, 'host_socket_inspection_invalid')
        if port in (80, 443):
            occupied.add(port)
    return occupied

def host_image_architecture():
    architecture = HOST_IMAGE_ARCHITECTURES.get(platform.machine().lower())
    require(architecture is not None, 'host_architecture_unsupported')
    return architecture

class Release:
    def __init__(self, root, action):
        self.root = root.resolve(); self.action = action; self.stage = 'prerequisites'
        self.project = os.environ.get('PILOT_COMPOSE_PROJECT', '')
        self.tag = os.environ.get('PILOT_IMAGE_TAG', '')
        require(PILOT_PROJECT_RE.fullmatch(self.project) is not None, 'pilot_project_invalid')
        require(IMAGE_TAG_RE.fullmatch(self.tag) is not None, 'pilot_image_tag_invalid')
        self.origin = os.environ.get('SMOKE_BASE_URL', '')
        self.private_path = os.environ.get('SMOKE_PRIVATE_PATH', '/xx/')
        self.smoke_target = {key:os.environ.get(key,'') for key in ('SMOKE_RESOLVE_HOST','SMOKE_RESOLVE_IP')}
        host, address = self.smoke_target.values()
        require(bool(host)==bool(address), 'smoke_resolution_invalid')
        if host:
            require(host == urlsplit(self.origin).hostname, 'smoke_resolution_invalid')
            try: ipaddress.ip_address(address)
            except ValueError: raise Refused('smoke_resolution_invalid') from None
        def input_path(name, default):
            value = Path(os.environ.get(name, str(default)))
            return value if value.is_absolute() else self.root/value
        self.env_file = input_path('PILOT_ENV_FILE', self.root/'.env').absolute()
        self.compose_file = input_path('PILOT_COMPOSE_FILE', self.root/'docker-compose.yml').absolute()
        self.state = Path(os.environ.get('PILOT_STATE_DIR', str(Path.home()/'.local/state/rsc-pilot')))
        require(self.state.is_absolute(), 'state_path_invalid')
        require(self.state != self.root and self.root not in self.state.parents, 'state_must_be_outside_candidate')
        private_directory(self.state)
        self.project_state = private_directory(self.state/self.project)
        self.receipts = private_directory(self.project_state/'receipts')
        self.receipt_file = self.receipts/(self.tag+'.json')
        # Compose selection flags come only from explicit command arguments.
        self.environment = {key:value for key,value in os.environ.items() if not key.startswith('COMPOSE_')}
        self.environment.update(RSC_RELEASE_PROFILE='pilot', RSC_API_IMAGE='rsc-pilot-api:'+self.tag,
            RSC_WEB_IMAGE='rsc-pilot-web:'+self.tag, RSC_DB_IMAGE='rsc-pilot-db:'+self.tag,
            OAM_EDGE_DB_NETWORK=self.project+'_edge_db', COMPOSE_DISABLE_ENV_FILE='1',
            COMPOSE_PARALLEL_LIMIT='1')
        self.lock = None

    def run(self, args, *, timeout=30, diagnostic=False):
        process = subprocess.Popen(args, env=self.environment, cwd=self.root,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            # Stop only this invocation's process group. Docker daemon side
            # effects may already exist, so no successful receipt is produced.
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.communicate()
            raise
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        require(result.returncode == 0, 'command_failed')
        if diagnostic:
            require(not result.stderr.strip(), 'unexpected_command_diagnostic')
        return result.stdout.decode('utf-8', errors='strict')

    def compose_args(self, path, *, frozen=False):
        return ['docker','compose','--project-directory',str(self.root),'--env-file',
                '/dev/null' if frozen else str(self.env_file),'-f',str(path),
                '--project-name',self.project]

    def resolve(self):
        plain_file(self.env_file); plain_file(self.compose_file)
        document = json.loads(self.run(self.compose_args(self.compose_file)+['config','--format','json'], diagnostic=True),
                              object_pairs_hook=strict_object)
        runtime = decoded_config(document)
        self.validate_builds(runtime)
        database = runtime['services']['db'].get('environment',{}).get('POSTGRES_DB','')
        require(isinstance(database,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}',database) is not None,
                'database_name_invalid')
        checks = checks_for(runtime, self.compose_file, project_name=self.project, image_tag=self.tag,
                            smoke_base_url=self.origin, smoke_private_path=self.private_path)
        require(bool(checks) and all(item['ok'] for item in checks), 'configuration_preflight_refused')
        return document

    def validate_builds(self, document):
        # This entry point supports the three reviewed repository Dockerfiles,
        # not arbitrary Compose build graphs or external build contexts.
        require({'db','api','web'} <= set(document.get('services',{})), 'build_topology_invalid')
        require(not document.get('secrets') and not document.get('configs'), 'unbound_configuration_source')
        for name, service in document['services'].items():
            require(not any(service.get(key) for key in ('secrets','configs','env_file')),
                    'unbound_configuration_source')
            build = service.get('build')
            if build is None:
                require(name not in ('db','api','web'), 'build_topology_invalid')
                continue
            require(isinstance(build,dict) and set(build) <= {'context','dockerfile','args'}, 'build_topology_invalid')
            context = self.root if name=='db' else self.root/'frontend' if name=='web' else self.root/'backend'
            dockerfile = 'deployment/backup/Postgres.Dockerfile' if name=='db' else 'Dockerfile'
            require(build.get('context') == str(context) and build.get('dockerfile','Dockerfile')==dockerfile,
                    'build_topology_invalid')
            require(build.get('args',{}) == ({'RELEASE_PROFILE':'pilot'} if name=='web' else {}), 'build_topology_invalid')

    def fingerprint(self, document):
        mounts = {}
        for service in document['services'].values():
            for mount in service.get('volumes', []):
                if mount.get('type') == 'bind':
                    require(mount.get('read_only') is True, 'writable_static_bind_refused')
                    source = Path(decoded_config(mount['source']))
                    require(source.is_absolute() and source == source.resolve(), 'bind_source_invalid')
                    mounts[str(source)] = tree_digest(source)
        source_hash = tree_digest(self.root, candidate=True)
        config_hash = digest(encoded(document))
        all_inputs = dict(source=source_hash, config=config_hash,
                          env=digest(plain_file(self.env_file)), compose=digest(plain_file(self.compose_file)),
                          mounts=mounts, origin=self.origin, private_path=self.private_path, smoke_target=self.smoke_target)
        return dict(input_sha256=digest(encoded(all_inputs)), source_sha256=source_hash,
                    configuration_sha256=config_hash), mounts

    def stable(self):
        current = self.resolve()
        fingerprint, mounts = self.fingerprint(current)
        require(fingerprint == self.fingerprints and mounts == self.mounts, 'prepared_inputs_changed')
        if hasattr(self, 'static_mounts'):
            for original, row in self.static_mounts.items():
                require(tree_digest(Path(row['snapshot'])) == self.mounts[original], 'frozen_mount_changed')

    def network_check(self):
        ids = self.run(['docker','network','ls','--quiet','--no-trunc'], diagnostic=True).split()
        existing = json.loads(self.run(['docker','network','inspect',*ids], diagnostic=True)) if ids else []
        require(len(existing) == len(ids) and {item['Id'] for item in existing} == set(ids), 'network_inspection_incomplete')
        require(not check_networks(self.document, existing, self.project), 'network_preflight_refused')

    def images_now(self, document=None):
        document = document or self.document
        images = {}
        architecture = host_image_architecture()
        for service in ('db','api','web'):
            result = json.loads(self.run(['docker','image','inspect',document['services'][service]['image']], diagnostic=True))
            require(isinstance(result,list) and len(result)==1 and ID.fullmatch(result[0].get('Id','')),
                    'image_identity_invalid')
            require(result[0].get('Os') == 'linux' and result[0].get('Architecture') == architecture,
                    'image_platform_mismatch')
            images[service] = result[0]['Id']
        return images

    def frozen_document(self):
        document = copy.deepcopy(self.document)
        for name, service in document['services'].items():
            if name in ('db','api','web','migrate','kms-pin-gate','kms-pin-plan'):
                service['image'] = self.images['db' if name=='db' else 'web' if name=='web' else 'api']
            for mount in service.get('volumes', []):
                if mount.get('type') == 'bind':
                    mount['source'] = self.static_mounts[decoded_config(mount['source'])]['snapshot'].replace('$', '$$')
        return document

    def invoke_document(self, document, arguments, *, timeout=120):
        self.stable()
        # Preserve Compose's canonical dollar escaping without a second layer.
        # Verify this installed Compose can roundtrip its own canonical output.
        with tempfile.TemporaryDirectory(prefix='.compose-', dir=self.project_state) as directory:
            path = Path(directory)/'resolved.json'
            descriptor = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(encoded(document))
            reparsed = json.loads(self.run(self.compose_args(path, frozen=True)+['config','--format','json'], diagnostic=True), object_pairs_hook=strict_object)
            require(reparsed == document, 'compose_snapshot_roundtrip_changed')
            self.run(self.compose_args(path, frozen=True)+arguments, timeout=timeout)
        self.stable()

    def invoke(self, arguments, *, timeout=120):
        self.invoke_document(self.frozen_document(), arguments, timeout=timeout)

    def build_images(self):
        # Public tags are daemon-global, even across different Compose projects.
        # Capture IDs from unique attempt tags before publishing public aliases;
        # another prepare can never substitute its image for this build's ID.
        build = copy.deepcopy(self.document)
        suffix = 'build-' + uuid.uuid4().hex
        for service in ('db','api','web'):
            build['services'][service]['image'] = 'rsc-pilot-' + service + ':' + suffix
        self.invoke_document(build, ['build','db','api','web'], timeout=3600)
        self.images = self.images_now(build)
        for service, image in self.images.items():
            self.run(['docker','image','tag',image,self.document['services'][service]['image']])
        require(self.images_now()==self.images,'image_tag_changed_during_prepare')
        self.stable()

    def container(self, service, *, exited=False):
        # ps reads the reviewed service selection; inspect binds its immutable
        # container ID and project labels, never a caller-provided container.
        ids = self.run(self.compose_args(self.compose_file)+['ps','--all','--quiet',service], diagnostic=True).split()
        require(len(ids)==1 and CONTAINER.fullmatch(ids[0]) is not None, 'container_identity_missing')
        rows = json.loads(self.run(['docker','inspect','--type','container',ids[0]], diagnostic=True))
        require(isinstance(rows,list) and len(rows)==1, 'container_inspection_invalid')
        row = rows[0]; labels = row.get('Config',{}).get('Labels',{})
        require(row.get('Id')==ids[0] and labels.get('com.docker.compose.project')==self.project
                and labels.get('com.docker.compose.service')==service, 'container_ownership_invalid')
        expected = self.images['db' if service=='db' else 'web' if service=='web' else 'api']
        require(row.get('Image')==expected, 'container_image_changed')
        state = row.get('State',{})
        require((state.get('Status')=='exited' and state.get('ExitCode')==0) if exited
                else state.get('Running') is True, 'container_state_invalid')
        return row

    def database_identity(self):
        row = self.container('db')
        mounts = [m for m in row.get('Mounts',[]) if m.get('Destination')=='/var/lib/postgresql/data']
        require(len(mounts)==1 and mounts[0].get('Type')=='volume'
                and mounts[0].get('Name')==self.document['volumes']['postgres_data']['name'], 'database_volume_changed')
        mount = mounts[0]
        return dict(container_id=row['Id'], image_id=row['Image'], volume_name=mount['Name'], volume_source=mount['Source'])

    def database_head(self, identity):
        # Fixed local Unix socket inside the already-bound DB container. No
        # external DSN, inherited PGHOST, or caller-supplied SQL is accepted.
        command = 'unset PGHOSTADDR PGSERVICE PGSERVICEFILE; PGHOST=/var/run/postgresql PGPORT=5432 PGPASSWORD="$POSTGRES_PASSWORD" PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=5000" psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT version_num FROM public.alembic_version"'
        actual = self.run(['docker','exec',identity['container_id'],'sh','-c',command], diagnostic=True).strip()
        require(actual == self.head, 'database_head_changed')

    def capture_mounts(self):
        attempt = private_directory(self.project_state/('attempt-'+uuid.uuid4().hex))
        self.static_mounts = {}
        for index,(original, expected) in enumerate(sorted(self.mounts.items())):
            source = Path(original); destination = attempt/str(index)
            if source.is_dir():
                shutil.copytree(source,destination,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            else:
                shutil.copy2(source,destination)
            require(tree_digest(destination)==expected, 'mount_changed_during_copy')
            self.static_mounts[original] = dict(snapshot=str(destination), sha256=expected)
        self.stable()

    def execute(self):
        lock_path = self.project_state/'deployment.lock'
        descriptor = os.open(lock_path, os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(descriptor,'a')
        require(stat.S_IMODE(os.fstat(descriptor).st_mode)==0o600, 'lock_permissions_invalid')
        try:
            try: fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Refused('project_deployment_busy') from None
            if self.action=='prepare':
                require(not os.path.lexists(self.receipt_file), 'successful_prepare_receipt_exists')
            else:
                require(os.path.lexists(self.receipt_file), 'prepare_receipt_required')
            self.stage='preflight'; self.document=self.resolve()
            self.fingerprints,self.mounts=self.fingerprint(self.document)
            self.head=expected_head(self.root)
            self.stage='network_check'; self.network_check()
            if self.action=='prepare':
                self.stage='snapshot'; self.capture_mounts()
                self.stage='build'; self.stable()
                self.build_images()
                self.stage='database'; self.invoke(['up','-d','--no-build','--pull','never','--wait','--wait-timeout','60','db'])
                identity=self.database_identity()
                self.stage='migration'; self.invoke(['up','--no-deps','--no-build','--pull','never','--force-recreate',
                    '--abort-on-container-exit','--exit-code-from','migrate','migrate'])
                self.container('migrate',exited=True)
                require(self.database_identity()==identity,'database_identity_changed')
                self.database_head(identity); self.stable()
                require(self.images_now()==self.images,'image_tag_changed_during_prepare')
                self.stage='receipt'
                atomic_receipt(self.receipt_file,dict(schema=SCHEMA,project=self.project,tag=self.tag,
                    **self.fingerprints,head=self.head,images=self.images,database=identity,static_mounts=self.static_mounts))
                print('pilot-deploy: prepared; application is NOT started; complete reviewed KMS pin provisioning before start')
                return
            self.stage='receipt_check'; receipt=load_receipt(self.receipt_file)
            require(receipt['project']==self.project and receipt['tag']==self.tag and receipt['head']==self.head
                    and all(receipt[key]==value for key,value in self.fingerprints.items()),'prepared_inputs_changed')
            self.images=self.images_now()
            require(self.images==receipt['images'],'prepared_image_changed')
            self.static_mounts=receipt['static_mounts']
            require(isinstance(self.static_mounts,dict) and set(self.static_mounts)==set(self.mounts),'receipt_mounts_invalid')
            for original,row in self.static_mounts.items():
                snapshot=Path(row['snapshot'])
                require(snapshot.is_absolute() and snapshot==snapshot.resolve()
                    and snapshot.parent.parent==self.project_state and snapshot.parent.name.startswith('attempt-')
                    and row['sha256']==self.mounts[original],'receipt_mounts_invalid')
                private_directory(snapshot.parent)
            self.stable()
            identity=self.database_identity()
            require(identity==receipt['database'],'prepared_database_changed')
            self.database_head(identity)
            self.stage='port_check'
            ports=self.run(['docker','ps','--format','{{.Ports}}'],diagnostic=True)
            require(re.search(r':(?:80|443)->',ports) is None,'public_ports_occupied')
            listeners=self.run(['ss','-H','-ltn'],diagnostic=True)
            require(not host_public_tcp_ports(listeners),'public_ports_occupied')
            self.stage='kms_pin_gate'; self.invoke(['up','--no-deps','--no-build','--pull','never','--force-recreate',
                '--abort-on-container-exit','--exit-code-from','kms-pin-gate','kms-pin-gate'])
            self.container('kms-pin-gate',exited=True)
            require(self.database_identity()==identity,'prepared_database_changed')
            self.stage='application'; self.invoke(['up','-d','--no-deps','--no-build','--pull','never','--wait','--wait-timeout','60','api','web'])
            self.container('api');self.container('web')
            require(self.database_identity()==identity,'prepared_database_changed')
            self.stage='smoke'
            for attempt in range(3):
                self.stable()
                try:
                    self.run(['sh',str(self.root/'scripts/smoke_test.sh')],timeout=65)
                except Refused:
                    continue
                else:
                    self.stable()
                    print('pilot-deploy: start checks completed; immutable candidate and read-only smoke passed; business acceptance remains separate')
                    return
            raise Refused('smoke_checks_failed')
        finally:
            self.lock.close()


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    release = None
    try:
        require(len(sys.argv)==2 and sys.argv[1] in ('prepare','start'),'action_invalid')
        release=Release(Path(__file__).resolve().parents[1],sys.argv[1])
        release.execute();return 0
    except KeyboardInterrupt:
        code='interrupted'
    except Refused as error:
        code=str(error)
    except BaseException:
        code='operation_failed'
    print('pilot-deploy: failed stage='+ (release.stage if release else 'prerequisites')
          +' code='+code+'; no automatic rollback or cleanup was performed',file=sys.stderr)
    return 2

if __name__=='__main__':
    raise SystemExit(main())
