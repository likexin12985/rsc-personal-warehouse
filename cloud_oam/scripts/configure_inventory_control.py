#!/usr/bin/env python3
"""Owner-side control authorization commands and authorized capture inspection.

RSC access credentials are read through a hidden terminal prompt or an inherited
file descriptor, never arguments, command JSON, environment, logs or receipts.
The dedicated owner DSN and exact database name come from managed process env.
This program never migrates the schema or retries an unknown commit outcome.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys


MAX_REQUEST_BYTES = 65536
MAX_MATERIAL_PUBLICATION_BYTES = 1024 * 1024
MAX_HANDOFF_BYTES = 2 * 1024 * 1024
REQUIRED_HEAD = '20261119_0140'


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Mistakenly supplying a credential flag must not echo its value.
        self.exit(2, 'control_configuration_invalid_arguments; use --help\n')


def _document(path, *, handoff=False, inspection=False, material_inspection=False, material_publication=False, projection_plan=False):
    limit = MAX_HANDOFF_BYTES if handoff else MAX_MATERIAL_PUBLICATION_BYTES if material_publication else MAX_REQUEST_BYTES
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('request too large')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    allowed = {'command', 'expected_authorization_version'} if projection_plan else \
        {'receipt_id', 'expected_authorization_version'} if material_inspection else \
        {'payload', 'key_id', 'signature'} if handoff else \
        {'preparation_id', 'expected_authorization_version', 'normalization_rules', 'mapping_decision_id'} if inspection else \
        {'command', 'expected_authorization_version', 'review_sha256'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('invalid envelope')
    return value


def _credential(fd):
    if fd is None:
        if not sys.stdin.isatty():
            raise ValueError('an inherited token fd is required without a terminal')
        return getpass.getpass('RSC 当前网页登录凭据（隐藏输入）：')
    if fd < 3:
        raise ValueError('token fd must be a separate inherited descriptor')
    with os.fdopen(os.dup(fd), 'r', encoding='ascii') as stream:
        value = stream.read(8194)
    value = value.removesuffix('\n')
    if not value or len(value) > 8192:
        raise ValueError('invalid credential')
    return value


def _connection_config():
    from sqlalchemy.engine import make_url
    raw = os.environ.get('OAM_CONTROL_CONFIGURATION_DATABASE_URL', '')
    target = os.environ.get('OAM_CONTROL_CONFIGURATION_DATABASE_NAME', '')
    url = make_url(raw)
    if url.drivername != 'postgresql+psycopg' or url.username != 'star_oam_migrator' \
            or not target or url.database != target:
        raise ValueError('explicit owner database configuration required')
    return raw, target


def _database_preflight(db, target):
    from sqlalchemy import text
    if db.scalar(text('SELECT current_user')) != 'star_oam_migrator' \
            or db.scalar(text('SELECT session_user')) != 'star_oam_migrator' \
            or db.scalar(text('SELECT current_database()')) != target \
            or int(db.scalar(text('SHOW server_version_num'))) // 10000 != 16 \
            or tuple(db.scalars(text('SELECT version_num FROM alembic_version'))) != (REQUIRED_HEAD,):
        raise ValueError('owner database identity, version or migration mismatch')
    db.execute(text("SET LOCAL lock_timeout = '5s'"))
    db.execute(text("SET LOCAL statement_timeout = '30s'"))
    db.execute(text("SET LOCAL idle_in_transaction_session_timeout = '30s'"))


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('preview', 'apply', 'status', 'inspect'))
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--command-file')
    inputs.add_argument('--handoff-file')
    inputs.add_argument('--inspection-file')
    inputs.add_argument('--mapping-file')
    inputs.add_argument('--material-source-file')
    inputs.add_argument('--material-publication-file')
    inputs.add_argument('--material-inspection-file')
    inputs.add_argument('--control-projection-plan-file')
    inputs.add_argument('--control-publication-file')
    parser.add_argument('--access-token-fd', type=int)
    args = parser.parse_args(argv)
    if args.handoff_file and (args.mode is not None or args.access_token_fd is not None):
        parser.error('handoff mode is signed and has no access token')
    inspection = bool(args.inspection_file or args.material_inspection_file or args.control_projection_plan_file)
    if (inspection and args.mode not in (None, 'inspect')) or (args.mode == 'inspect' and not inspection):
        parser.error('inspection requires its own document and cannot apply commands')
    engine = None
    commit_attempted = False
    try:
        document = _document(args.handoff_file or args.inspection_file or args.mapping_file or args.material_source_file or args.material_publication_file
                             or args.material_inspection_file or args.control_projection_plan_file or args.control_publication_file or args.command_file, handoff=bool(args.handoff_file),
                             inspection=bool(args.inspection_file), material_inspection=bool(args.material_inspection_file),
                             material_publication=bool(args.material_publication_file), projection_plan=bool(args.control_projection_plan_file))
        credential = None if args.handoff_file else _credential(args.access_token_fd)
        raw, target = _connection_config()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from sqlalchemy.pool import NullPool
        from app.inventory_control_authority import AuthorityCommand
        from app import inventory_control_configuration as service
        mode = args.mode or ('inspect' if inspection else 'preview')
        if args.handoff_file:
            from app.inventory_control_handoff import run_owner_handoff
            function, kwargs = run_owner_handoff, {'envelope': document}
        elif args.control_projection_plan_file:
            from app.inventory_control_projection_plan import ControlProjectionPlanRequest, inspect_inventory_control_projection_plan
            function = inspect_inventory_control_projection_plan
            kwargs = dict(access_token=credential, request=ControlProjectionPlanRequest.model_validate(document['command']),
                          expected_authorization_version=document['expected_authorization_version'])
        elif args.control_publication_file:
            from app import inventory_control_projection as control
            kwargs = dict(access_token=credential, command=control.ControlPublicationCommand.model_validate(document['command']),
                          expected_authorization_version=document['expected_authorization_version'])
            if mode != 'preview': kwargs['review_sha256'] = document['review_sha256']
            function = {'preview': control.preview_control_publication, 'apply': control.execute_control_publication,
                        'status': control.read_control_publication}[mode]
        elif args.material_publication_file:
            from app import material_projection as material
            kwargs = dict(access_token=credential, command=material.MaterialPublicationCommand.model_validate(document['command']),
                          expected_authorization_version=document['expected_authorization_version'])
            if mode != 'preview': kwargs['review_sha256'] = document['review_sha256']
            function = {'preview': material.preview_material_publication, 'apply': material.execute_material_publication,
                        'status': material.read_material_publication}[mode]
        elif args.material_source_file:
            from app import material_source_authority as material
            kwargs = dict(access_token=credential, command=material.MaterialSourceCommand.model_validate(document['command']),
                          expected_authorization_version=document['expected_authorization_version'])
            if mode != 'preview': kwargs['review_sha256'] = document['review_sha256']
            function = {'preview': material.preview_material_source_authority, 'apply': material.execute_material_source_authority,
                        'status': material.read_material_source_authority}[mode]
        elif args.material_inspection_file:
            from uuid import UUID
            from app.material_source_authority import inspect_material_source_configuration
            function = inspect_material_source_configuration
            kwargs = dict(access_token=credential, receipt_id=UUID(document['receipt_id']),
                          expected_authorization_version=document['expected_authorization_version'])
        elif args.mapping_file:
            from app.inventory_control_mapping import MappingCommand, preview_inventory_control_mapping, execute_inventory_control_mapping, read_inventory_control_mapping
            kwargs = dict(access_token=credential, command=MappingCommand.model_validate(document['command']),
                          expected_authorization_version=document['expected_authorization_version'])
            if mode != 'preview': kwargs['review_sha256'] = document['review_sha256']
            function = {'preview': preview_inventory_control_mapping, 'apply': execute_inventory_control_mapping,
                        'status': read_inventory_control_mapping}[mode]
        elif args.inspection_file:
            from uuid import UUID
            function = service.inspect_inventory_control_capture
            kwargs = dict(access_token=credential, preparation_id=UUID(document['preparation_id']),
                          expected_authorization_version=document['expected_authorization_version'])
            if 'normalization_rules' in document:
                if document['normalization_rules'] is None:
                    raise ValueError('normalization rules required')
                kwargs['normalization_rules'] = document['normalization_rules']
            if 'mapping_decision_id' in document:
                if 'normalization_rules' in document:
                    raise ValueError('conflicting rule inputs')
                kwargs['mapping_decision_id'] = UUID(document['mapping_decision_id'])
        else:
            kwargs = dict(access_token=credential, command=AuthorityCommand.model_validate(document['command']),
                          expected_authorization_version=document['expected_authorization_version'])
            if mode != 'preview': kwargs['review_sha256'] = document['review_sha256']
            function = {'preview': service.preview_inventory_control_configuration,
                        'apply': service.execute_inventory_control_configuration,
                        'status': service.read_inventory_control_configuration}[mode]
        engine = create_engine(raw, poolclass=NullPool, hide_parameters=True,
                               connect_args={'connect_timeout': 5}, echo=False)
        with Session(engine, autoflush=False) as db:
            _database_preflight(db, target)
            result = function(db, **kwargs)
            # Serialize before committing, so serialization cannot hide a commit.
            output = json.dumps(result if args.handoff_file else dict(mode=mode, **result), ensure_ascii=False, sort_keys=True)
            writes = result['payload']['request']['payload']['purpose'] == 'execute' if args.handoff_file else mode == 'apply'
            if writes:
                commit_attempted = True
                db.commit()
            else:
                db.rollback()
        print(output)
        return 0
    except Exception:
        # DB/validation exceptions may contain a DSN, token or command input.
        # An unsuccessful COMMIT acknowledgement is not proof of a rollback.
        print(json.dumps(dict(code='control_configuration_outcome_unknown' if commit_attempted else 'control_configuration_failed',
                              next_action='read_exact_status' if commit_attempted else 'check_request_and_current_access'),
                         ensure_ascii=False), file=sys.stderr)
        return 3 if commit_attempted else 2
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass  # Connection disposal cannot change an acknowledged result.


if __name__ == '__main__':
    raise SystemExit(main())
