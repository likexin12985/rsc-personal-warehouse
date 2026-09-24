"""Candidate joint backup coordinator; callers own export success and snapshot locks."""
from datetime import datetime
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
from tempfile import TemporaryDirectory
from uuid import UUID

from formal_file_integrity import FileObject
from formal_object_backup import capture_objects, canonical, ObjectBackupError


class JointBackupError(RuntimeError): pass


class _RegularTarInfo(tarfile.TarInfo):
    def _proc_member(self,archive):
        # Generated archives are USTAR regular files only. Refuse extended
        # headers before tarfile can allocate their declared payload.
        if self.type not in (tarfile.REGTYPE,tarfile.AREGTYPE):
            raise JointBackupError('invalid_archive_member')
        return super()._proc_member(archive)


class _BudgetReader:
    def __init__(self, stream, maximum_bytes):
        self.stream=stream;self.remaining=maximum_bytes
    def read(self,size):
        if size<0:raise JointBackupError('unbounded_archive_read')
        data=self.stream.read(min(size,self.remaining+1))
        self.remaining-=len(data)
        if self.remaining<0:raise JointBackupError('archive_read_budget_exceeded')
        return data


def copy_bounded(source,output,maximum_bytes):
    total=0
    for chunk in iter(lambda:source.read(1024*1024),b''):
        total+=len(chunk)
        if total>maximum_bytes:raise JointBackupError('copy_budget_exceeded')
        output.write(chunk)
    return total


def digest(path):
    result = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""): result.update(chunk)
    return result.hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise JointBackupError("duplicate_json_key")
        result[key] = value
    return result


def decode(data):
    try: return json.loads(data, object_pairs_hook=unique_object)
    except JointBackupError: raise
    except (ValueError, TypeError): raise JointBackupError("invalid_json") from None


def unpack_exact(bundle, directory, names, *, maximum_bytes):
    """Never use tar extraction paths, symlinks, directories or metadata."""
    if type(maximum_bytes) is not int or maximum_bytes<=0:raise JointBackupError('invalid_archive_budget')
    bundle=Path(bundle)
    if bundle.is_symlink() or not bundle.is_file() or bundle.stat().st_size>maximum_bytes:
        raise JointBackupError('archive_input_refused')
    seen=set();total=0
    # All snapshot, outer and nested formal-object archives are uncompressed
    # tar. Stream headers and cap actual reads, including extended headers.
    with bundle.open('rb') as stream,tarfile.open(fileobj=_BudgetReader(stream,maximum_bytes),mode='r|',tarinfo=_RegularTarInfo) as archive:
        for member in archive:
            if member.name not in names or member.name in seen:
                raise JointBackupError('unexpected_archive_members')
            if member.type not in (tarfile.REGTYPE,tarfile.AREGTYPE) or member.sparse is not None or member.size<0 or total+member.size>maximum_bytes:
                raise JointBackupError('invalid_archive_member')
            seen.add(member.name);total+=member.size
            with archive.extractfile(member) as source, (directory/member.name).open("xb") as output:
                os.chmod(directory/member.name, 0o600)
                if copy_bounded(source,output,member.size)!=member.size:raise JointBackupError('archive_member_truncated')
            archive.members.clear()
        if seen!=set(names):raise JointBackupError('unexpected_archive_members')


def read_catalog(path, snapshot):
    with path.open() as stream:
        header = decode(stream.readline())
        entries = [decode(line) for line in stream]
    required = {"schema", "snapshot_id", "database", "role", "isolation", "read_only", "file_count", "migration_head"}
    if set(header) != required or header["schema"] != "cloud_oam.formal_file_snapshot_candidate.v1" or header["snapshot_id"] != snapshot["snapshot_id"] or header["role"] != "star_oam_backup" or header["isolation"] != "repeatable read" or header["read_only"] != "on" or type(header["file_count"]) is not int or header["file_count"] != len(entries):
        raise JointBackupError("catalog_snapshot_mismatch")
    if not isinstance(header["database"], str) or re.fullmatch(r"[0-9A-Za-z_]{1,63}", header["database"]) is None or not isinstance(header["migration_head"], str) or not header["migration_head"]:
        raise JointBackupError("catalog_database_invalid")
    rows = []
    required = {"id", "storage_key", "sha256", "size_bytes", "mime_type", "original_filename", "uploaded_by", "status", "metadata_jsonb", "created_at"}
    for value in entries:
        if not isinstance(value, dict) or set(value) != required:
            raise JointBackupError("file_catalog_shape_changed")
        try:
            fields = dict(value, id=UUID(value["id"]), created_at=datetime.fromisoformat(value["created_at"]))
        except (TypeError, ValueError): raise JointBackupError("invalid_file_catalog") from None
        rows.append(FileObject(**fields))
    return header, entries, rows


def publish_joint(*args,**kwargs):
    from streaming_backup import publish_joint as execute
    return execute(*args,**kwargs)


def verify_joint(*args,**kwargs):
    from streaming_backup import verify_joint as execute
    return execute(*args,**kwargs)
