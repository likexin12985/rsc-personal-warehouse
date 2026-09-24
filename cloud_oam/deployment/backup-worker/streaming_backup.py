"""Disk-indexed v1 backup compatibility with bounded records and tar metadata."""
from contextlib import contextmanager
from datetime import datetime
import gzip,json,os,sqlite3,tarfile
import shutil,re
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from formal_file_integrity import FileObject,StoredObjectHead,_validate_object_head,_head_manifest_sha256
from formal_object_backup import canonical,_catalog,_verified_head,ObjectBackupError
from joint_backup import JointBackupError,decode,digest,copy_bounded,unpack_exact

RECORD_LIMIT=1024*1024
SMALL_LIMIT=64*1024


def small_json(path):
    with path.open('rb') as stream:data=stream.read(SMALL_LIMIT+1)
    if len(data)>SMALL_LIMIT:raise JointBackupError('manifest_header_too_large')
    return decode(data)


class JsonRecords:
    """Iterate top-level manifest arrays, decoding only one bounded JSON value."""
    def __init__(self,stream):self.stream=stream;self.buffer=b'';self.position=0
    def peek(self):
        if self.position==len(self.buffer):
            self.buffer=self.stream.read(64*1024);self.position=0
        return self.buffer[self.position:self.position+1]
    def take(self):
        value=self.peek();self.position+=bool(value);return value
    def whitespace(self):
        while self.peek() and self.peek() in b' \t\r\n':self.take()
    def require(self,expected):
        self.whitespace()
        if self.take()!=expected:raise JointBackupError('invalid_manifest_json')
    def value(self):
        self.whitespace();value=bytearray();depth=0;string=False;escape=False
        while True:
            c=self.peek()
            if not c:break
            if not string and depth==0 and c in b',}]':break
            if not string and depth==0 and c in b' \r\n\t' and value:break
            c=self.take();value.extend(c)
            if len(value)>RECORD_LIMIT:raise JointBackupError('manifest_record_too_large')
            if string:
                if escape:escape=False
                elif c==b'\\':escape=True
                elif c==b'"':string=False
            elif c==b'"':string=True
            elif c in (b'[',b'{'):
                depth+=1
                if depth>64:raise JointBackupError('manifest_nesting_too_deep')
            elif c in (b']',b'}'):depth-=1
        if not value or string or depth:raise JointBackupError('invalid_manifest_json')
        try:return decode(value)
        except (RecursionError,UnicodeError):raise JointBackupError('invalid_manifest_json') from None
    def manifest(self,accept):
        self.require(b'{');seen=set();scalars={};first=True
        while True:
            self.whitespace()
            if self.peek()==b'}':self.take();break
            if not first:self.require(b',')
            first=False
            # Keys are small; reuse the value scanner but ':' terminates keys.
            self.require(b'"');key=bytearray()
            while self.peek()!=b'"':
                c=self.take()
                if not c or c==b'\\' or len(key)>100:raise JointBackupError('invalid_manifest_key')
                key.extend(c)
            self.take();self.require(b':')
            try:name=key.decode('ascii')
            except UnicodeError:raise JointBackupError('invalid_manifest_key') from None
            if name in seen:raise JointBackupError('duplicate_json_key')
            seen.add(name)
            if name in {'catalog','objects'}:
                self.require(b'[');item_first=True
                while True:
                    self.whitespace()
                    if self.peek()==b']':self.take();break
                    if not item_first:self.require(b',')
                    item_first=False;accept(name,self.value())
                scalars[name]=True
            else:scalars[name]=self.value()
        self.whitespace()
        if self.peek():raise JointBackupError('invalid_manifest_json')
        return scalars


class Catalog:
    def __init__(self,directory,maximum_bytes):
        self.path=directory/'catalog-index.sqlite';self.maximum_bytes=maximum_bytes
        # The index is ephemeral private backup metadata, never application DB.
        fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600);os.close(fd)
        self.db=None
        try:
            self.db=sqlite3.connect(self.path)
            self.db.execute('PRAGMA journal_mode=OFF');self.db.execute('PRAGMA temp_store=FILE')
            self.db.execute('PRAGMA cache_size=-2048');self.db.execute('PRAGMA mmap_size=0')
            self.db.execute('PRAGMA max_page_count='+str(max(16,maximum_bytes//4096)))
            self.db.execute('CREATE TABLE files(id TEXT PRIMARY KEY,storage_key TEXT UNIQUE,row_json BLOB NOT NULL,catalog BLOB NOT NULL,available INTEGER NOT NULL,receipt BLOB,catalog_seen INTEGER NOT NULL DEFAULT 0,object_seen INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID')
        except BaseException:
            if self.db is not None:self.db.close()
            self.path.unlink(missing_ok=True)
            raise
        self.count=0;self.available=0;self.pending=0;self.object_bytes=0;self.header=None
    def close(self):self.db.close()
    def read(self,path,snapshot):
        with path.open('rb') as stream:
            line=stream.readline(RECORD_LIMIT+1)
            if len(line)>RECORD_LIMIT:raise JointBackupError('catalog_record_too_large')
            h=decode(line)
            required={'schema','snapshot_id','database','role','isolation','read_only','file_count','migration_head'}
            if not isinstance(h,dict) or set(h)!=required or h['schema']!='cloud_oam.formal_file_snapshot_candidate.v1' or h['snapshot_id']!=snapshot['snapshot_id'] or h['role']!='star_oam_backup' or h['isolation']!='repeatable read' or h['read_only']!='on' or type(h['file_count']) is not int or h['file_count']<0:
                raise JointBackupError('catalog_snapshot_mismatch')
            import re
            if not isinstance(h['database'],str) or re.fullmatch('[0-9A-Za-z_]{1,63}',h['database']) is None or not isinstance(h['migration_head'],str) or not h['migration_head']:raise JointBackupError('catalog_database_invalid')
            self.header=h
            while True:
                line=stream.readline(RECORD_LIMIT+1)
                if not line:break
                if len(line)>RECORD_LIMIT:raise JointBackupError('catalog_record_too_large')
                value=decode(line);row=self.row(value)
                frozen,items=_catalog([row]);row=frozen[0];item=items[0]
                try:self.db.execute('INSERT INTO files(id,storage_key,row_json,catalog,available) VALUES(?,?,?,?,?)',
                    (row.id.hex,row.storage_key,canonical(value),canonical(item),int(row.status=='available')))
                except sqlite3.IntegrityError:raise ObjectBackupError('duplicate_file_catalog') from None
                self.count+=1;self.available+=row.status=='available';self.pending+=row.status=='pending'
                self.object_bytes+=row.size_bytes if row.status=='available' else 0
                if self.object_bytes>self.maximum_bytes:raise ObjectBackupError('object_budget_exceeded')
            if h['file_count']!=self.count:raise JointBackupError('catalog_snapshot_mismatch')
        self.db.commit()
    @staticmethod
    def row(value):
        required={'id','storage_key','sha256','size_bytes','mime_type','original_filename','uploaded_by','status','metadata_jsonb','created_at'}
        if not isinstance(value,dict) or set(value)!=required:raise JointBackupError('file_catalog_shape_changed')
        try:return FileObject(**dict(value,id=UUID(value['id']),created_at=datetime.fromisoformat(value['created_at'])))
        except (TypeError,ValueError,AttributeError):raise JointBackupError('invalid_file_catalog') from None
    def items(self):
        for key,row in self.db.execute('SELECT id,row_json FROM files ORDER BY id'):yield key,self.row(decode(row))
    def catalog_hash(self):
        h=sha256();h.update(b'[');first=True
        for data, in self.db.execute('SELECT catalog FROM files ORDER BY id'):
            if not first:h.update(b',')
            first=False;h.update(data)
        h.update(b']');return h.hexdigest()
    def manifest(self,path,reader):
        base=dict(schema='cloud_oam.formal_object_backup_candidate.v1',provider='aliyun_oss_v2',region=reader.region,bucket=reader.bucket,
            catalog_sha256=self.catalog_hash(),database_snapshot_bound=False)
        with path.open('xb') as out:
            os.chmod(path,0o600);out.write(b'{');first=True
            for key in sorted([*base,'catalog','objects']):
                if not first:out.write(b',')
                first=False;out.write(canonical(key)+b':')
                if key in {'catalog','objects'}:
                    column='catalog' if key=='catalog' else 'receipt';out.write(b'[');item_first=True
                    for data, in self.db.execute('SELECT '+column+' FROM files WHERE '+column+' IS NOT NULL ORDER BY id'):
                        if not item_first:out.write(b',')
                        item_first=False;out.write(data)
                    out.write(b']')
                else:out.write(canonical(base[key]))
            out.write(b'}\n');out.flush();os.fsync(out.fileno())
        return base


@contextmanager
def catalog_at(directory,maximum_bytes):
    index=None
    try:
        index=Catalog(directory,maximum_bytes)
        yield index
    except sqlite3.Error:raise JointBackupError('catalog_index_refused') from None
    finally:
        if index is not None:index.close();index.path.unlink(missing_ok=True)


def add_member(archive,path,name):
    archive.add(path,arcname=name,recursive=False)
    # Python 3.12 caches members even in stream mode. Only regular sequential
    # members are supported; never resolve links or look up earlier members.
    archive.members.clear()


def capture(index,reader,destination,directory):
    with destination.open('xb') as output,tarfile.open(fileobj=output,mode='w|',format=tarfile.USTAR_FORMAT) as archive:
        os.chmod(destination,0o600)
        for key,row in index.items():
            if row.status=='pending':continue
            observed=_verified_head(row,reader.head(row.storage_key))
            with reader.download(row.storage_key,etag=observed['etag'],version_id=observed['version_id']) as result:
                if _verified_head(row,result)!=observed:raise ObjectBackupError('object_changed_during_download')
                if result.body is None:raise ObjectBackupError('object_body_missing')
                length=0;digest_value=sha256();path=directory/'object-body'
                with path.open('xb') as out:
                    os.chmod(path,0o600)
                    for chunk in result.body.iter_bytes(block_size=64*1024):
                        if not isinstance(chunk,bytes):raise ObjectBackupError('invalid_object_stream')
                        length+=len(chunk)
                        if length>row.size_bytes:raise ObjectBackupError('object_length_mismatch')
                        digest_value.update(chunk);out.write(chunk)
                    if length!=row.size_bytes or digest_value.hexdigest()!=row.sha256:raise ObjectBackupError('object_content_mismatch')
                    out.flush();os.fsync(out.fileno())
            receipt=dict(file_id=str(row.id),archive_member=key,sha256=row.sha256,size_bytes=length,**observed)
            index.db.execute('UPDATE files SET receipt=? WHERE id=?',(canonical(receipt),key))
            add_member(archive,path,key);path.unlink()
        index.db.commit();manifest=directory/'object-manifest.json';index.manifest(manifest,reader)
        add_member(archive,manifest,'manifest.json');manifest.unlink()


def verify_objects(index,path,directory,maximum_bytes):
    from joint_backup import _BudgetReader,_RegularTarInfo
    manifest_seen=False;total=0
    with path.open('rb') as stream,tarfile.open(fileobj=_BudgetReader(stream,maximum_bytes),mode='r|',tarinfo=_RegularTarInfo) as archive:
        for member in archive:
            if member.type not in (tarfile.REGTYPE,tarfile.AREGTYPE) or member.sparse is not None or member.size<0 or total+member.size>maximum_bytes:raise JointBackupError('invalid_archive_member')
            total+=member.size
            with archive.extractfile(member) as body:
                if member.name=='manifest.json':
                    if manifest_seen:raise JointBackupError('object_receipt_mismatch')
                    manifest_seen=True
                    def accept(kind,value):
                        if not isinstance(value,dict):raise JointBackupError('object_catalog_mismatch')
                        key=value.get('file_id')
                        try:key=UUID(key).hex
                        except (ValueError,TypeError,AttributeError):raise JointBackupError('object_catalog_mismatch') from None
                        stored=index.db.execute('SELECT catalog,catalog_seen,receipt,available FROM files WHERE id=?',(key,)).fetchone()
                        if stored is None:raise JointBackupError('object_catalog_mismatch')
                        if kind=='catalog':
                            if stored[1] or canonical(value)!=stored[0]:raise JointBackupError('object_catalog_mismatch')
                            index.db.execute('UPDATE files SET catalog_seen=1 WHERE id=?',(key,))
                        else:
                            if stored[2] is not None or not stored[3] or value.get('archive_member')!=key:raise JointBackupError('object_receipt_mismatch')
                            index.db.execute('UPDATE files SET receipt=? WHERE id=?',(canonical(value),key))
                    class CopyReader:
                        def __init__(self,source,output):self.source=source;self.output=output
                        def read(self,size):
                            data=self.source.read(size);self.output.write(data);return data
                    with (directory/'manifest.json').open('xb') as manifest_copy:
                        os.chmod(directory/'manifest.json',0o600)
                        metadata=JsonRecords(CopyReader(body,manifest_copy)).manifest(accept)
                    required={'schema','provider','region','bucket','catalog','catalog_sha256','objects','database_snapshot_bound'}
                    if set(metadata)!=required or metadata['schema']!='cloud_oam.formal_object_backup_candidate.v1' or metadata['provider']!='aliyun_oss_v2' or metadata['database_snapshot_bound'] is not False or metadata['catalog_sha256']!=index.catalog_hash():raise JointBackupError('object_catalog_mismatch')
                else:
                    stored=index.db.execute('SELECT row_json,available,object_seen FROM files WHERE id=?',(member.name,)).fetchone()
                    if stored is None or not stored[1] or stored[2]:raise JointBackupError('unexpected_archive_members')
                    row=index.row(decode(stored[0]))
                    if member.size!=row.size_bytes:raise JointBackupError('object_content_mismatch')
                    digest_value=sha256();size=0
                    with (directory/member.name).open('xb') as out:
                        os.chmod(directory/member.name,0o600)
                        for chunk in iter(lambda:body.read(64*1024),b''):size+=len(chunk);digest_value.update(chunk);out.write(chunk)
                    if size!=row.size_bytes or digest_value.hexdigest()!=row.sha256:raise JointBackupError('object_content_mismatch')
                    index.db.execute('UPDATE files SET object_seen=1 WHERE id=?',(member.name,))
            archive.members.clear()
        if not manifest_seen or index.db.execute('SELECT count(*) FROM files WHERE catalog_seen!=1 OR (available=1 AND (receipt IS NULL OR object_seen!=1))').fetchone()[0]:raise JointBackupError('object_receipt_mismatch')
    for key,row in index.items():
        if row.status!='available':continue
        receipt=decode(index.db.execute('SELECT receipt FROM files WHERE id=?',(key,)).fetchone()[0])
        if receipt.get('sha256')!=row.sha256 or receipt.get('size_bytes')!=row.size_bytes or receipt.get('file_id')!=str(row.id):raise JointBackupError('object_content_mismatch')
        try:
            head=StoredObjectHead(row.storage_key,row.size_bytes,row.mime_type,receipt['metadata'],receipt['etag'])
            _validate_object_head(row,head);completion=row.metadata_jsonb['completion']
            if sha256(head.etag.encode()).hexdigest()!=completion['etag_sha256'] or _head_manifest_sha256(head)!=completion['head_manifest_sha256']:raise ValueError('completion differs')
        except Exception:raise JointBackupError('object_completion_mismatch') from None
    # Preserve the original v1 manifest for restore callers, without decoding
    # its unbounded arrays into memory a second time.
    return metadata


def publish_joint(snapshot_export,legacy_uploads,reader,destination,*,maximum_bytes):
    destination=Path(destination);legacy_uploads=Path(legacy_uploads)
    if type(maximum_bytes) is not int or maximum_bytes<=0:raise JointBackupError('invalid_backup_budget')
    if legacy_uploads.is_symlink() or not legacy_uploads.is_file() or not 1<=legacy_uploads.stat().st_size<=maximum_bytes:raise JointBackupError('legacy_archive_input_refused')
    if destination.exists() or destination.is_symlink():raise JointBackupError('backup_already_exists')
    with TemporaryDirectory(prefix='.joint-backup-',dir=destination.parent) as temporary:
        work=Path(temporary)
        unpack_exact(snapshot_export,work,{'database.sql','files.ndjson','snapshot.json'},maximum_bytes=maximum_bytes)
        snapshot=small_json(work/'snapshot.json')
        if not isinstance(snapshot,dict) or set(snapshot)!={'schema','snapshot_id'} or snapshot['schema']!='cloud_oam.database_object_snapshot_candidate.v1' or not isinstance(snapshot['snapshot_id'],str) or re.fullmatch('[0-9a-fA-F-]{1,100}',snapshot['snapshot_id']) is None:raise JointBackupError('snapshot_envelope_invalid')
        if (work/'database.sql').stat().st_size==0:raise JointBackupError('database_dump_empty')
        with catalog_at(work,maximum_bytes) as index:
            index.read(work/'files.ndjson',snapshot)
            capture(index,reader,work/'objects.tar',work)
            with (work/'database.sql').open('rb') as source,gzip.open(work/'database.sql.gz','wb') as output:
                copy_bounded(source,output,maximum_bytes)
            with legacy_uploads.open('rb') as source,(work/'uploads.tar.gz').open('xb') as output:
                copy_bounded(source,output,maximum_bytes)
            names=['database.sql.gz','files.ndjson','objects.tar','uploads.tar.gz']
            if sum((work/n).stat().st_size for n in names)>maximum_bytes:raise JointBackupError('joint_backup_budget_exceeded')
            manifest=dict(schema='cloud_oam.joint_backup_candidate.v1',snapshot=index.header,snapshot_id=snapshot['snapshot_id'],
                files={n:dict(sha256=digest(work/n),size_bytes=(work/n).stat().st_size) for n in names},
                formal_object_count=index.available,pending_intent_count=index.pending,database_catalog_bound=True,cloud_restore_verified=False)
            (work/'manifest.json').write_bytes(canonical(manifest)+b'\n')
            bundle=work/'joint.tar'
            with bundle.open('xb') as output:
                os.chmod(bundle,0o600)
                with tarfile.open(fileobj=output,mode='w|',format=tarfile.USTAR_FORMAT) as archive:
                    for name in [*names,'manifest.json']:
                        os.chmod(work/name,0o600);add_member(archive,work/name,name)
                output.flush();os.fsync(output.fileno())
            if bundle.stat().st_size>maximum_bytes:raise JointBackupError('joint_backup_budget_exceeded')
        try:os.link(bundle,destination)
        except FileExistsError:raise JointBackupError('backup_already_exists') from None
        fd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    return manifest


def verify_joint(bundle,destination,*,maximum_bytes):
    destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise JointBackupError('restore_destination_exists')
    with TemporaryDirectory(prefix='.joint-verify-',dir=destination.parent) as temporary:
        work=Path(temporary);names={'database.sql.gz','files.ndjson','objects.tar','uploads.tar.gz'}
        unpack_exact(bundle,work,names|{'manifest.json'},maximum_bytes=maximum_bytes)
        manifest=small_json(work/'manifest.json')
        if not isinstance(manifest,dict) or manifest.get('schema')!='cloud_oam.joint_backup_candidate.v1' or set(manifest.get('files',{}))!=names or manifest.get('database_catalog_bound') is not True:raise JointBackupError('joint_manifest_invalid')
        for name in names:
            if manifest['files'][name]!={'sha256':digest(work/name),'size_bytes':(work/name).stat().st_size}:raise JointBackupError('joint_file_integrity_mismatch')
        with catalog_at(work,maximum_bytes) as index:
            index.read(work/'files.ndjson',manifest)
            if index.header!=manifest['snapshot']:raise JointBackupError('joint_snapshot_mismatch')
            object_directory=work/'objects';object_directory.mkdir(mode=0o700)
            verify_objects(index,work/'objects.tar',object_directory,maximum_bytes)
            if type(manifest['formal_object_count']) is not int or type(manifest['pending_intent_count']) is not int or index.available!=manifest['formal_object_count'] or index.pending!=manifest['pending_intent_count']:raise JointBackupError('joint_object_count_mismatch')
        with gzip.open(work/'database.sql.gz','rb') as source,(work/'database.sql').open('xb') as output:
            os.chmod(work/'database.sql',0o600);copy_bounded(source,output,maximum_bytes)
        destination.mkdir(mode=0o700)
        for p in work.iterdir():shutil.move(p,destination/p.name)
    return manifest
