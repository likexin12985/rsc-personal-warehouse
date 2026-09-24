#!/usr/bin/env python3
"""Review or provision daily capture database roles on an explicit database.

Bootstrap URL and two independent passwords come from the process environment.
There is no request route, automatic repair, credential rotation or retry.
"""
import argparse
import json
import os
from pathlib import Path
import sys

class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, '{"code":"daily_capture_arguments_invalid"}\n')

def main(argv=None):
    parser=SafeParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--mode',choices=('preview','apply','check',
        'prepare-restore','check-restore','activate-restore'),default='preview')
    parser.add_argument('--restore-id')
    parser.add_argument('--backup-sha256')
    args=parser.parse_args(argv)
    engine=None;commit_attempted=False
    try:
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url
        from sqlalchemy.pool import NullPool
        from app.daily_reconciliation.capture_role_contract import CONTROL_ROLE,LEDGER_ROLE
        from app.daily_reconciliation.capture_provisioning import provision_capture_roles
        from app.daily_reconciliation.capture_restore import manage_capture_restore,restore_coordinates
        restoring=args.mode.endswith('-restore')
        if restoring:
            restore_coordinates(args.restore_id,args.backup_sha256)
        elif args.restore_id is not None or args.backup_sha256 is not None:
            raise ValueError('restore coordinates only apply to restore modes')
        url=make_url(os.environ['OAM_DAILY_CAPTURE_BOOTSTRAP_URL'])
        if url.drivername!='postgresql+psycopg' or url.database!=args.database:
            raise ValueError('explicit PostgreSQL database required')
        passwords=None
        if args.mode in ('apply','activate-restore'):
            passwords={CONTROL_ROLE:os.environ['OAM_DB_CONTROL_CAPTURE_PASSWORD'],
                       LEDGER_ROLE:os.environ['OAM_DB_LEDGER_CAPTURE_PASSWORD']}
        engine=create_engine(url,poolclass=NullPool,hide_parameters=True,echo=False,connect_args={'connect_timeout':5})
        with engine.connect() as connection:
            if restoring:
                result=manage_capture_restore(connection,database=args.database,
                    restore_id=args.restore_id,backup_sha256=args.backup_sha256,
                    mode=args.mode,passwords=passwords)
            else:
                result=provision_capture_roles(connection,database=args.database,passwords=passwords,apply=args.mode=='apply')
            if args.mode=='check' and not result['configured']:
                raise ValueError('capture roles not configured')
            output=json.dumps(dict(mode=args.mode,**result),sort_keys=True)
            if args.mode in ('apply','prepare-restore','activate-restore'):
                commit_attempted=True;connection.commit()
            else:
                connection.rollback()
        print(output)
        return 0
    except Exception:
        print(json.dumps(dict(code='daily_capture_configuration_unknown' if commit_attempted else 'daily_capture_configuration_refused',
                              next_action=('check_restore_with_same_coordinates' if args.mode.endswith('-restore')
                                           else 'check_exact_database_roles') if commit_attempted
                              else 'review_database_and_role_boundary')),file=sys.stderr)
        return 3 if commit_attempted else 2
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass

if __name__=='__main__':
    raise SystemExit(main())
