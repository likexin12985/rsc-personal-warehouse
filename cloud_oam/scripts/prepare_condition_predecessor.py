#!/usr/bin/env python3
"""Prepare exact reviewed Git sources for owned PG16 historical checks.

No network, credentials, live database or current application code is used.
An existing destination is verified only; it is never overwritten or repaired.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

COMMIT = '9dff36f7feca44626b82ceb6e40297b3732a22f0'
ARCHIVE_SHA256 = '2932f348bbdfadb15b58a8562f0a1eb186249dcf4e7441f9a3ce58abd4728709'
SOURCE_COUNT = 1221
SOURCES = {
    '0157': (COMMIT, ARCHIVE_SHA256, SOURCE_COUNT, '20261206_0157'),
    '0164': ('fc7c9269e19f6afd180a0aced55b565f03c244b6',
             'fb422a00689e22fb760c224a772049241ed56b8f42bf464548ed639ed90b2b0c',
             1421, '20261213_0164'),
}
PATHS = ('cloud_oam/alembic.ini', 'cloud_oam/backend', 'cloud_oam/deployment',
         'cloud_oam/edge_sync', 'cloud_oam/scripts')
ROLLBACK_PROFILE = '0164-dropped-column-compat-v1'
ROLLBACK_FILE = 'cloud_oam/backend/app/stock_loss_correction_security.py'
ROLLBACK_BEFORE = b'(a.attisdropped OR a.attacl IS NOT NULL)'
ROLLBACK_AFTER = b'''(a.attacl IS NOT NULL OR (a.attisdropped AND
                    (a.atttypid<>0 OR a.attnotnull OR a.attidentity<>'' OR a.attgenerated<>'')))'''


def digest(content):
    return hashlib.sha256(content).hexdigest()


def reviewed_archive(repository, revision='0157'):
    commit, archive_sha, count, _ = SOURCES[revision]
    raw = subprocess.run(['git', '-c', 'tar.umask=0002', 'archive', '--format=tar', commit, *PATHS],
        cwd=repository, check=True, capture_output=True, timeout=60).stdout
    if digest(raw) != archive_sha:
        raise ValueError('reviewed predecessor Git archive digest mismatch')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'cloud_oam':
                raise ValueError('unsafe predecessor archive path')
            if member.isdir():
                continue
            if not member.isfile() or member.name in files:
                raise ValueError('predecessor links or duplicate entries are forbidden')
            files[member.name] = archive.extractfile(member).read()
    if len(files) != count:
        raise ValueError('reviewed predecessor source count mismatch')
    return raw, dict(sorted(files.items()))


def prepare(repository, destination, revision='0157', *, rollback_compatibility=False):
    if rollback_compatibility and revision != '0164':
        raise ValueError('rollback compatibility applies only to reviewed 0164')
    commit, archive_sha, count, formal_head = SOURCES[revision]
    repository = Path(repository).resolve(strict=True)
    destination = Path(destination).resolve()
    if not destination.is_relative_to(repository / 'cloud_oam/artifacts'):
        raise ValueError('predecessor evidence must remain inside this repository artifacts')
    raw, files = reviewed_archive(repository, revision)
    compatibility = None
    if rollback_compatibility:
        before = files[ROLLBACK_FILE]
        if before.count(ROLLBACK_BEFORE) != 1:
            raise ValueError('reviewed rollback guard no longer matches')
        # This is an explicitly labelled maintenance build, never the original
        # Git predecessor. Keep visible-column, FK, index, ACL and RLS checks.
        after = before.replace(ROLLBACK_BEFORE, ROLLBACK_AFTER)
        files[ROLLBACK_FILE] = after
        compatibility = dict(profile=ROLLBACK_PROFILE, file=ROLLBACK_FILE,
            beforeSha256=digest(before), afterSha256=digest(after))
        buffer = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as original:
            with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as output:
                for member in original.getmembers():
                    content = files[member.name] if member.isfile() else None
                    if content is not None:
                        member.size = len(content)
                    output.addfile(member, io.BytesIO(content) if content is not None else None)
        build_archive = buffer.getvalue()
        compatibility['buildArchiveSha256'] = digest(build_archive)
    expected = {name: digest(content) for name, content in files.items()}
    if destination.exists():
        if (destination / 'source.tar').read_bytes() != raw:
            raise ValueError('existing predecessor base archive drift')
        receipt = json.loads((destination / 'receipt.json').read_text())
        manifest_raw = (destination / 'manifest.json').read_bytes()
        if (json.loads(manifest_raw) != expected or receipt.get('gitCommit') != commit
                or receipt.get('formalHead') != formal_head
                or receipt.get('sourceFiles') != count
                or receipt.get('rollbackCompatibility') != compatibility
                or receipt.get('archiveSha256') != archive_sha
                or receipt.get('manifestSha256') != digest(manifest_raw)):
            raise ValueError('existing predecessor receipt does not match reviewed Git source')
        for name, content in files.items():
            path = destination / 'source' / name
            if (path.is_symlink() or not path.resolve().is_relative_to(destination / 'source')
                    or path.read_bytes() != content):
                raise ValueError('existing predecessor source drift: ' + name)
        if compatibility and (destination / 'rollback-source.tar').read_bytes() != build_archive:
            raise ValueError('existing rollback build archive drift')
        return receipt
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        target = destination / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest_raw = (json.dumps(expected, indent=2) + '\n').encode()
    (destination / 'source.tar').write_bytes(raw)
    if compatibility:
        (destination / 'rollback-source.tar').write_bytes(build_archive)
    (destination / 'manifest.json').write_bytes(manifest_raw)
    receipt = dict(gitCommit=commit, formalHead=formal_head, sourceFiles=count,
        archiveSha256=archive_sha, manifestSha256=digest(manifest_raw), productionAcceptance=False)
    if compatibility:
        receipt['rollbackCompatibility'] = compatibility
    (destination / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--revision', choices=tuple(SOURCES), default='0157')
    parser.add_argument('--rollback-compatibility', action='store_true',
        help='build the labelled one-file 0164 maintenance rollback image source, not pristine Git')
    args = parser.parse_args()
    print(json.dumps(prepare(args.repository, args.destination, args.revision,
        rollback_compatibility=args.rollback_compatibility), sort_keys=True))


if __name__ == '__main__':
    main()
