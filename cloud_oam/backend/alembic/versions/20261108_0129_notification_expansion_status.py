"""Permit event expansion with one status-column grant and immutable facts.

No event, recipient, delivery or business fact is rewritten. Downgrade removes
the new runtime capability and guards while retaining every existing row.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = '20261108_0129'
down_revision = '20261107_0128'
branch_labels = depends_on = None
_folder = Path(__file__).parent
_previous = runpy.run_path(str(_folder / '20261107_0128_opening_start_seals.py'))
OLD_HASH = _previous['NEW_HASH']
_ready = _previous['_previous']['_ready']
_ready_parent = _previous['_previous']['_ready_parent']
NEW_HASH = hashlib.sha256(_ready.replace(_ready_parent, revision).encode()).hexdigest()
FUNCTION = 'rsc_guard_notification_event_state_0129'
BODY = """
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION '0129 notification event facts cannot be removed' USING ERRCODE='23514';
    END IF;
    IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
        RAISE EXCEPTION '0129 notification event content is immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.status IS NOT DISTINCT FROM OLD.status
       OR (OLD.status='pending' AND NEW.status IN ('expanded','cancelled'))
       OR (OLD.status='expanded' AND NEW.status='cancelled') THEN
        RETURN NEW;
    END IF;
    -- Recovering a retained person may append a newly verified channel after
    -- an earlier expansion. Only that outstanding bound recipient may reopen
    -- expansion; existing deliveries, attempts and cancellations stay intact.
    IF OLD.status='expanded' AND NEW.status='pending' AND EXISTS (
        SELECT 1 FROM public.notification_recipients r
        JOIN public.notification_target_bindings b ON b.recipient_id=r.id
        JOIN public.notification_person_targets t ON t.id=b.target_id AND t.event_id=r.event_id
        WHERE r.event_id=OLD.id AND r.status='active'
          AND NOT EXISTS (SELECT 1 FROM public.notification_deliveries d WHERE d.recipient_id=r.id)
    ) THEN RETURN NEW; END IF;
    RAISE EXCEPTION '0129 notification event state cannot move backwards' USING ERRCODE='23514';
END
"""
BODY_HASH = hashlib.sha256(BODY.encode()).hexdigest()
TRIGGERS = {
    'trg_notification_event_state_0129': ('UPDATE OR DELETE', 27),
    'trg_notification_event_truncate_0129': ('TRUNCATE', 34),
}
IMMUTABLE_COLUMNS = ('id', 'event_type', 'business_type', 'business_id', 'dedup_key',
                     'payload_jsonb', 'occurred_at', 'created_at', 'target_manifest_sha256')


def _verify_acl(status_update):
    expected = str(status_update).lower()
    op.execute(f"""DO $acl_0129$ BEGIN
      IF EXISTS (SELECT 1 FROM (VALUES ('notification_events'),('notification_recipients')) AS t(name)
        WHERE NOT has_table_privilege('star_oam_api','public.'||t.name,'SELECT')
           OR NOT has_table_privilege('star_oam_api','public.'||t.name,'INSERT')
           OR has_table_privilege('star_oam_api','public.'||t.name,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
           OR has_table_privilege('star_oam_api','public.'||t.name,'SELECT WITH GRANT OPTION,INSERT WITH GRANT OPTION'))
        OR EXISTS (SELECT 1 FROM pg_attribute a
          WHERE a.attrelid IN ('public.notification_events'::regclass,'public.notification_recipients'::regclass)
            AND a.attnum>0 AND NOT a.attisdropped
            AND (has_column_privilege('star_oam_api',a.attrelid,a.attnum,'UPDATE')
                 IS DISTINCT FROM ({expected} AND a.attrelid='public.notification_events'::regclass AND a.attname='status')
              OR has_column_privilege('star_oam_api',a.attrelid,a.attnum,'UPDATE WITH GRANT OPTION'))) THEN
        RAISE EXCEPTION '0129 notification runtime ACL drift';
      END IF;
    END $acl_0129$""")


def _verify_guards():
    op.execute(f"""DO $function_0129$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='public.{FUNCTION}()'::regprocedure
          AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
          AND p.prokind='f' AND p.prorettype='trigger'::regtype AND NOT p.proretset
          AND NOT p.prosecdef AND NOT p.proleakproof AND p.provolatile='v' AND NOT p.proisstrict
          AND p.proparallel='u' AND p.pronargs=0 AND p.pronargdefaults=0 AND p.proargmodes IS NULL
          AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
          AND p.proconfig=ARRAY['search_path=pg_catalog, public']
          AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{BODY_HASH}'
          AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
        OR (SELECT count(*) FROM pg_trigger WHERE tgfoid='public.{FUNCTION}()'::regprocedure)<>2 THEN
        RAISE EXCEPTION '0129 notification event function or private ACL drift';
      END IF;
    END $function_0129$""")
    for name, (_, kind) in TRIGGERS.items():
        op.execute(f"""DO $trigger_0129$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='public.notification_events'::regclass
              AND tgname='{name}' AND tgfoid='public.{FUNCTION}()'::regprocedure AND tgtype={kind}
              AND tgenabled='A' AND NOT tgisinternal AND NOT tgdeferrable AND NOT tginitdeferred
              AND tgconstraint=0 AND tgqual IS NULL AND tgnargs=0 AND tgattr=''::int2vector) THEN
            RAISE EXCEPTION '0129 notification event trigger drift';
          END IF;
        END $trigger_0129$""")


def _sqlite_transition(up):
    if up:
        different = ' OR '.join(f'NEW.{column} IS NOT OLD.{column}' for column in IMMUTABLE_COLUMNS)
        op.execute(f"""CREATE TRIGGER trg_notification_event_update_0129 BEFORE UPDATE ON notification_events
          WHEN ({different}) OR NOT (NEW.status IS OLD.status
            OR (OLD.status='pending' AND NEW.status IN ('expanded','cancelled'))
            OR (OLD.status='expanded' AND NEW.status='cancelled')
            OR (OLD.status='expanded' AND NEW.status='pending' AND EXISTS (
              SELECT 1 FROM notification_recipients r
              JOIN notification_target_bindings b ON b.recipient_id=r.id
              JOIN notification_person_targets t ON t.id=b.target_id AND t.event_id=r.event_id
              WHERE r.event_id=OLD.id AND r.status='active'
                AND NOT EXISTS (SELECT 1 FROM notification_deliveries d WHERE d.recipient_id=r.id))))
          BEGIN SELECT RAISE(ABORT,'0129 notification event content or state is immutable'); END""")
        op.execute("""CREATE TRIGGER trg_notification_event_delete_0129 BEFORE DELETE ON notification_events
          BEGIN SELECT RAISE(ABORT,'0129 notification event facts cannot be removed'); END""")
    else:
        op.execute('DROP TRIGGER trg_notification_event_update_0129')
        op.execute('DROP TRIGGER trg_notification_event_delete_0129')


def _transition(up):
    dialect = op.get_bind().dialect.name
    if dialect not in {'postgresql', 'sqlite'}:
        raise RuntimeError('0129 unsupported database')
    helper = runpy.run_path(str(_folder / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect == 'sqlite':
        _sqlite_transition(up)
        return
    op.execute("DO $owner_0129$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0129 direct schema owner required'; END IF; END $owner_0129$")
    op.execute('LOCK TABLE public.alembic_version, public.notification_events, public.notification_recipients IN ACCESS EXCLUSIVE MODE')
    _verify_acl(not up)
    if up:
        op.execute(f'CREATE FUNCTION public.{FUNCTION}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog, public AS $body$'+BODY+'$body$')
        op.execute(f'REVOKE ALL ON FUNCTION public.{FUNCTION}() FROM PUBLIC, star_oam_api')
        for name, (events, _) in TRIGGERS.items():
            op.execute(f"CREATE TRIGGER {name} BEFORE {events} ON public.notification_events FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'} EXECUTE FUNCTION public.{FUNCTION}()")
            op.execute(f'ALTER TABLE public.notification_events ENABLE ALWAYS TRIGGER {name}')
        _verify_guards()
        op.execute('GRANT UPDATE(status) ON public.notification_events TO star_oam_api')
    else:
        _verify_guards()
        op.execute('REVOKE UPDATE(status) ON public.notification_events FROM star_oam_api')
        for name in TRIGGERS:
            op.execute(f'DROP TRIGGER {name} ON public.notification_events')
        op.execute(f'DROP FUNCTION public.{FUNCTION}()')
    _verify_acl(up)
    replace = runpy.run_path(str(_folder / '20260909_0069_stock_reservations.py'))['_replace_function_source']
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if up else NEW_HASH,
        replacement_hash=NEW_HASH if up else OLD_HASH,replacements=((down_revision,revision),) if up else ((revision,down_revision),),
        label='notification_expansion_readiness_0129')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
