"""Candidate read-only PG16 ledger capture; no DSN, credentials or region guesses.

This captures raw database facts. It does not authenticate an operator, prove
the application's trigger/audit catalog, approve a mapping or persist a daily
reconciliation. Only an internal authorized adapter may consume its result.
"""
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import time
from uuid import UUID

from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from .capture_role_contract import LEDGER_ROLE as ROLE, LEDGER_TABLES as TABLES
from .capture_security import validate_capture_roles_cursor
HEAD_SQL = "SELECT id,stream_key,next_cursor FROM public.inventory_ledger_heads ORDER BY id LIMIT 2"
QUERIES = {
    'inventory_transactions': "SELECT id,ledger_cursor,movement_type,status,reversed_transaction_id,effective_at,posted_at FROM public.inventory_transactions WHERE ledger_cursor<=%s ORDER BY ledger_cursor",
    'inventory_movements': "SELECT m.id,m.transaction_id,m.line_no,m.from_account_id,m.to_account_id,m.quantity,m.external_boundary_code FROM public.inventory_movements m JOIN public.inventory_transactions t ON t.id=m.transaction_id WHERE t.ledger_cursor<=%s ORDER BY t.ledger_cursor,m.line_no",
    'stock_accounts': "SELECT id,owner_org_id,custodian_person_id,location_id,material_id,condition_code,availability_bucket,lot_id FROM public.stock_accounts ORDER BY id",
    'stock_locations': "SELECT id,owner_org_id,parent_id,location_type,status,custodian_person_id,code FROM public.stock_locations ORDER BY id",
    'organizations': "SELECT id,parent_id,org_type,status,code FROM public.organizations ORDER BY id",
}


class CaptureError(RuntimeError):
    pass


def require(ok, code):
    if not ok: raise CaptureError(code)


def canonical(value):
    if isinstance(value, datetime):
        require(value.tzinfo is not None and value.utcoffset() is not None, 'timestamp_unaware')
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, UUID): return str(value)
    if isinstance(value, Decimal):
        require(value.is_finite() and value == value.quantize(Decimal('.001')), 'quantity_invalid')
        return format(value, '.3f')
    if isinstance(value, dict): return {k: canonical(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)): return [canonical(v) for v in value]
    return value


def digest(document):
    return sha256(json.dumps(canonical(document),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _validate_graph(rows, cutoff):
    transactions = rows['inventory_transactions']
    require(len(transactions)==cutoff and all(t['ledger_cursor']==n for n,t in enumerate(transactions,1)), 'ledger_prefix_incomplete')
    by_id={t['id']:t for t in transactions}
    require(len(by_id)==len(transactions) and all(t['status']=='posted' for t in transactions), 'transaction_facts_invalid')
    accounts={a['id']:a for a in rows['stock_accounts']}
    locations={a['id']:a for a in rows['stock_locations']}
    organizations={a['id']:a for a in rows['organizations']}
    require(len(accounts)==len(rows['stock_accounts']) and len(locations)==len(rows['stock_locations'])
        and len(organizations)==len(rows['organizations']), 'reference_duplicate')
    for table in (locations, organizations):
        for row in table.values():
            current=row;seen=set()
            while current is not None:
                require(current['id'] not in seen and len(seen)<128, 'reference_tree_cycle_or_depth')
                seen.add(current['id']);parent=current['parent_id']
                require(parent is None or parent in table, 'reference_parent_missing')
                current=table.get(parent)
    for row in locations.values(): require(row['owner_org_id'] in organizations, 'location_owner_missing')
    for row in accounts.values():
        require(row['owner_org_id'] in organizations and row['location_id'] in locations, 'account_reference_missing')
    lines={t['id']:[] for t in transactions}; movement_ids=set()
    for m in rows['inventory_movements']:
        require(m['id'] not in movement_ids and m['transaction_id'] in lines, 'movement_identity_invalid')
        movement_ids.add(m['id']);lines[m['transaction_id']].append(m['line_no'])
        require(m['from_account_id'] != m['to_account_id'] and m['quantity'].is_finite() and m['quantity']>0, 'movement_invalid')
        for account_id in (m['from_account_id'],m['to_account_id']):
            require(account_id is None or account_id in accounts, 'movement_account_missing')
        boundary=m['from_account_id'] is None or m['to_account_id'] is None
        require((boundary and bool(m['external_boundary_code'])) or (not boundary and m['external_boundary_code'] is None), 'movement_boundary_invalid')
    require(all(numbers and numbers==list(range(1,len(numbers)+1)) for numbers in lines.values()), 'movement_prefix_incomplete')


def capture_ledger(connection, *, maximum_rows, maximum_seconds=10):
    """Own one fresh REPEATABLE READ READ ONLY transaction; leave the connection idle.

    Limits are explicit; overflow fails the capture instead of returning a
    truncated inventory. Public schema, table names and the SELECT-only role
    are fixed. Existing caller transactions are never committed or rolled back.
    """
    require(connection.info.transaction_status == TransactionStatus.IDLE, 'connection_not_idle')
    require(type(maximum_rows) is int and 1<=maximum_rows<=1000000, 'row_budget_invalid')
    require(type(maximum_seconds) is int and 1<=maximum_seconds<=60, 'time_budget_invalid')
    started=time.monotonic();rows={}
    def budget(): require(time.monotonic()-started<=maximum_seconds, 'capture_deadline_exceeded')
    with connection.transaction():
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            # SET and LOCK are utility statements that do not freeze an MVCC
            # snapshot. Do not replace these with SELECT set_config(...): RLS
            # can change before locks, leaving a stale policy preflight.
            for setting,value in (('statement_timeout',str(maximum_seconds*1000)+'ms'),
                    ('lock_timeout','500ms'),('idle_in_transaction_session_timeout',str(maximum_seconds*1000)+'ms')):
                cursor.execute(sql.SQL('SET LOCAL {} = {}').format(sql.Identifier(setting),sql.Literal(value)))
            cursor.execute(sql.SQL('LOCK TABLE {} IN ACCESS SHARE MODE').format(
                sql.SQL(',').join(sql.Identifier('public',name) for name in TABLES)))
            cursor.execute("SELECT clock_timestamp() AS captured_at,pg_current_snapshot()::text AS snapshot_id,"
                "current_database() AS database_name,current_user AS role_name,session_user AS session_role,"
                "current_setting('server_version_num')::int AS server_version")
            observation=cursor.fetchone()
            require(observation['role_name']==observation['session_role']==ROLE, 'capture_role_mismatch')
            require(160000<=observation['server_version']<170000, 'postgresql16_required')
            cursor.execute("SELECT rolsuper,rolinherit,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,"
                "EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid) AS has_memberships "
                "FROM pg_roles r WHERE rolname=current_user")
            role=cursor.fetchone();require(role is not None and not any(role.values()), 'capture_role_privileged')
            cursor.execute("SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,"
                "pg_has_role(current_user,c.relowner,'USAGE') AS owns_table,"
                "has_table_privilege(current_user,c.oid,'SELECT') AS readable,"
                "has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS writable "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname", (list(TABLES),))
            table_checks=cursor.fetchall()
            require({r['relname'] for r in table_checks}==set(TABLES), 'capture_tables_missing')
            cursor.execute("SELECT c.relname,p.polname,p.polcmd,p.polpermissive,"
                "pg_get_expr(p.polqual,p.polrelid) AS using_expression,p.polwithcheck IS NULL AS no_check,"
                "p.polroles=ARRAY[r.oid]::oid[] AS capture_only,(0=ANY(p.polroles) OR r.oid=ANY(p.polroles)) AS applicable "
                "FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN pg_roles r WHERE n.nspname='public' AND c.relname=ANY(%s) AND r.rolname=current_user "
                "AND p.polcmd IN ('r','*') ORDER BY c.relname,p.polname",(list(TABLES),))
            policies=cursor.fetchall();policy_checks=[]
            for table in table_checks:
                require(table['relkind']=='r', 'capture_table_not_plain')
                require(table['readable'] and not table['writable'] and not table['owns_table'], 'capture_table_privileges_invalid')
                if table['relrowsecurity']:
                    selected=[p for p in policies if p['relname']==table['relname']]
                    # Memberships can change independently of relation locks.
                    # Refuse all restrictive read policies, even inactive ones.
                    require(all(p['polpermissive'] for p in selected), 'capture_restrictive_policy')
                    applicable=[p for p in selected if p['applicable']]
                    require(len(applicable)==1 and all(p['polname']=='rsc_daily_capture_select' and p['polcmd']=='r'
                        and p['capture_only'] and p['using_expression']=='true' and p['no_check'] for p in applicable),
                        'capture_full_read_policy_missing')
                    policy_checks.append(dict(table=table['relname'],dedicatedSelectOnly=True,usingTrue=True,restrictiveReadPoliciesAbsent=True))
            validate_capture_roles_cursor(cursor)
            cursor.execute(HEAD_SQL);heads=cursor.fetchall()
            require(len(heads)==1 and heads[0]['stream_key']=='inventory' and heads[0]['next_cursor']>0, 'ledger_head_invalid')
            cutoff=heads[0]['next_cursor']-1;require(cutoff<=maximum_rows, 'capture_row_budget_exceeded')
            for name, query in QUERIES.items():
                budget()
                remaining=max(1,int((maximum_seconds-(time.monotonic()-started))*1000))
                cursor.execute("SELECT set_config('statement_timeout',%s,true)",(str(remaining)+'ms',))
                # Bound the server result too: fetchmany alone cannot prevent
                # a regular libpq cursor from buffering an oversized response.
                cursor.execute(query+' LIMIT %s', ((cutoff,) if '%s' in query else ())+(maximum_rows+1,))
                values=[]
                while batch:=cursor.fetchmany(min(maximum_rows+1,1000)):
                    values.extend(batch);require(len(values)<=maximum_rows, 'capture_row_budget_exceeded');budget()
                rows[name]=values
            _validate_graph(rows,cutoff)
            cursor.execute("SELECT current_setting('transaction_isolation') AS isolation,current_setting('transaction_read_only') AS readonly,clock_timestamp() AS completed_at")
            finished=cursor.fetchone();require(finished['isolation']=='repeatable read' and finished['readonly']=='on', 'capture_transaction_changed')
            budget()
            document=canonical(dict(schema='rsc.daily_raw_ledger_capture_candidate.v1', observation=observation,
                completed_at=finished['completed_at'], transaction_isolation=finished['isolation'], transaction_read_only=True,
                ledger_head=heads[0], cutoff_cursor=cutoff, table_checks=table_checks, rls_policy_checks=policy_checks,
                counts={name:len(values) for name,values in rows.items()}, facts=rows,
                owner_and_location_scope_distinct=True, region_resolution_pending=True,
                operator_authority_verified=False, production_schema_verified=False, persisted=False))
            document['content_sha256']=digest(document)
    require(connection.info.transaction_status==TransactionStatus.IDLE, 'capture_transaction_not_closed')
    return document
