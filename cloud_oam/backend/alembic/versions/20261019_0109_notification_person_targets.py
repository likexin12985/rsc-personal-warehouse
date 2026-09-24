"""Seal intended notification people independently of account/channel readiness.

Existing events retain a NULL manifest. No historical audience is inferred or
backfilled. New target/binding facts are append-only; populated facts prevent
downgrade. The API gains SELECT/INSERT on these two tables only.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa

revision = "20261019_0109"
down_revision = "20261018_0108"
branch_labels = depends_on = None
OLD_HASH = "c118644da158bf2d36f7425105d7071d93b831e379d878ae4f9301139ae39246"
NEW_HASH = "8422e51272e7384f2c464f6f37292e846f6c0dd12a6584c5f9624700571ead52"
TARGETS = "notification_person_targets"
BINDINGS = "notification_target_bindings"

GUARD_BODY = """
DECLARE
    event_key uuid;
    expected_hash text;
    actual_hash text;
    target_person uuid;
    recipient_person uuid;
    recipient_event uuid;
    event_status text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE', 'TRUNCATE') THEN
        IF TG_TABLE_NAME = 'notification_events' THEN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.target_manifest_sha256 IS NOT DISTINCT FROM OLD.target_manifest_sha256 THEN RETURN NEW; END IF;
            ELSIF TG_OP = 'DELETE' THEN
                IF OLD.target_manifest_sha256 IS NULL THEN RETURN OLD; END IF;
            ELSIF TG_OP = 'TRUNCATE' THEN
                IF NOT EXISTS (SELECT 1 FROM public.notification_events WHERE target_manifest_sha256 IS NOT NULL) THEN RETURN NULL; END IF;
            END IF;
        END IF;
        RAISE EXCEPTION '0109 notification target facts are immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'notification_target_bindings' THEN
        SELECT event_id, person_id INTO STRICT event_key, target_person
          FROM public.notification_person_targets WHERE id = NEW.target_id;
        SELECT status INTO STRICT event_status FROM public.notification_events WHERE id = event_key FOR UPDATE;
        SELECT recipient.event_id, account.person_id INTO STRICT recipient_event, recipient_person
          FROM public.notification_recipients recipient JOIN public.users account ON account.id = recipient.user_id
         WHERE recipient.id = NEW.recipient_id FOR SHARE OF recipient, account;
        IF recipient_event <> event_key OR recipient_person IS DISTINCT FROM target_person OR event_status = 'cancelled' THEN
            RAISE EXCEPTION '0109 notification target recipient binding mismatch' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'notification_events' THEN event_key := NEW.id;
    ELSE event_key := NEW.event_id;
    END IF;
    SELECT target_manifest_sha256 INTO STRICT expected_hash
      FROM public.notification_events WHERE id = event_key FOR UPDATE;
    IF expected_hash IS NULL THEN
        IF EXISTS (SELECT 1 FROM public.notification_person_targets WHERE event_id = event_key) THEN
            RAISE EXCEPTION '0109 legacy notification audience cannot be inferred' USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END IF;
    SELECT encode(sha256(convert_to('notification-person-targets.v1' || chr(10) ||
        COALESCE(string_agg(person_id::text, ',' ORDER BY person_id::text), ''), 'UTF8')), 'hex') INTO actual_hash
      FROM public.notification_person_targets WHERE event_id = event_key;
    IF actual_hash <> expected_hash THEN
        RAISE EXCEPTION '0109 notification audience manifest mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
"""
FUNCTION_NAME = "rsc_guard_notification_targets_0109"
FUNCTION_HASH = hashlib.sha256(GUARD_BODY.encode()).hexdigest()
TRIGGERS = {
    "trg_notification_event_manifest_0109": ("notification_events", "INSERT", 5, True),
    "trg_notification_target_manifest_0109": (TARGETS, "INSERT", 5, True),
    "trg_notification_target_binding_0109": (BINDINGS, "INSERT", 7, False),
    "trg_notification_manifest_immutable_0109": ("notification_events", "UPDATE", 19, False),
    "trg_notification_event_no_delete_0109": ("notification_events", "DELETE", 11, False),
    "trg_notification_events_no_truncate_0109": ("notification_events", "TRUNCATE", 34, False),
    "trg_notification_targets_immutable_0109": (TARGETS, "UPDATE OR DELETE", 27, False),
    "trg_notification_targets_no_truncate_0109": (TARGETS, "TRUNCATE", 34, False),
    "trg_notification_bindings_immutable_0109": (BINDINGS, "UPDATE OR DELETE", 27, False),
    "trg_notification_bindings_no_truncate_0109": (BINDINGS, "TRUNCATE", 34, False),
}


def _sqlite_guards():
    # SQLite cannot implement the deferred aggregate manifest proof. The
    # service proves its manifest; only PG16 proves the database commit fence.
    for table in (TARGETS, BINDINGS):
        for event in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER trg_{table}_{event.lower()}_0109 BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT, '0109 target facts are immutable'); END")
    op.execute("""CREATE TRIGGER trg_notification_manifest_immutable_0109
        BEFORE UPDATE OF target_manifest_sha256 ON notification_events
        WHEN NEW.target_manifest_sha256 IS NOT OLD.target_manifest_sha256
        BEGIN SELECT RAISE(ABORT, '0109 target manifest is immutable'); END""")
    op.execute("""CREATE TRIGGER trg_notification_event_no_delete_0109
        BEFORE DELETE ON notification_events WHEN OLD.target_manifest_sha256 IS NOT NULL
        BEGIN SELECT RAISE(ABORT, '0109 target manifest is immutable'); END""")
    op.execute("""CREATE TRIGGER trg_notification_target_legacy_0109 BEFORE INSERT ON notification_person_targets
        WHEN (SELECT target_manifest_sha256 FROM notification_events WHERE id = NEW.event_id) IS NULL
        BEGIN SELECT RAISE(ABORT, '0109 legacy audience cannot be inferred'); END""")
    op.execute("""CREATE TRIGGER trg_notification_target_binding_0109 BEFORE INSERT ON notification_target_bindings
        WHEN NOT EXISTS (SELECT 1 FROM notification_person_targets t
          JOIN notification_events e ON e.id = t.event_id
          JOIN notification_recipients r ON r.id = NEW.recipient_id AND r.event_id = t.event_id
          JOIN users u ON u.id = r.user_id AND u.person_id = t.person_id
          WHERE t.id = NEW.target_id AND e.status <> 'cancelled')
        BEGIN SELECT RAISE(ABORT, '0109 target recipient binding mismatch'); END""")


def _transition(upgrade):
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0109 supports PostgreSQL and SQLite only")
    old = runpy.run_path(str(Path(__file__).with_name("20260927_0087_inbound_fulfillment_boundary.py")))
    old["_begin_sqlite"]()
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.notification_events, public.notification_recipients IN SHARE ROW EXCLUSIVE MODE")
    if not upgrade:
        if dialect == "postgresql":
            op.execute(f"LOCK TABLE public.{TARGETS}, public.{BINDINGS} IN SHARE ROW EXCLUSIVE MODE")
        old["_preflight"]("EXISTS (SELECT 1 FROM notification_person_targets) OR EXISTS (SELECT 1 FROM notification_target_bindings) OR EXISTS (SELECT 1 FROM notification_events WHERE target_manifest_sha256 IS NOT NULL)",
                          "0109 downgrade blocked: notification target evidence must be retained")
    if upgrade:
        op.add_column("notification_events", sa.Column("target_manifest_sha256", sa.String(64),
            sa.CheckConstraint("target_manifest_sha256 IS NULL OR length(target_manifest_sha256) = 64", name="ck_notification_events_target_manifest"), nullable=True))
        op.create_table(TARGETS,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("event_id", sa.Uuid(), sa.ForeignKey("notification_events.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("person_id", sa.Uuid(), sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("event_id", "person_id", name="uq_notification_person_targets_event_person"))
        op.create_index("ix_notification_person_targets_person_id", TARGETS, ["person_id"])
        op.create_index("ix_notification_person_targets_created", TARGETS, ["created_at", "id"])
        op.create_table(BINDINGS,
            sa.Column("target_id", sa.Uuid(), sa.ForeignKey(f"{TARGETS}.id", ondelete="RESTRICT"), primary_key=True),
            sa.Column("recipient_id", sa.Uuid(), sa.ForeignKey("notification_recipients.id", ondelete="RESTRICT"), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("recipient_id", name="uq_notification_target_bindings_recipient"))
        if dialect == "sqlite":
            _sqlite_guards()
    if dialect == "postgresql":
        helper = runpy.run_path(str(Path(__file__).with_name("20260912_0072_outbound_postings.py")))
        replace = helper["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        if upgrade:
            op.execute(f"CREATE FUNCTION public.{FUNCTION_NAME}() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${GUARD_BODY}$body$")
            op.execute(f"REVOKE ALL ON FUNCTION public.{FUNCTION_NAME}() FROM PUBLIC, star_oam_api")
            for name, (table, events, _, deferred) in TRIGGERS.items():
                if deferred:
                    statement = f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW"
                else:
                    statement = f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events == 'TRUNCATE' else 'ROW'}"
                op.execute(statement + f" EXECUTE FUNCTION public.{FUNCTION_NAME}()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
            op.execute(f"REVOKE ALL ON TABLE public.{TARGETS}, public.{BINDINGS} FROM PUBLIC")
            op.execute(f"GRANT SELECT, INSERT ON TABLE public.{TARGETS}, public.{BINDINGS} TO star_oam_api")
        else:
            op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                WHERE p.oid = 'public.{FUNCTION_NAME}()'::regprocedure
                  AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{FUNCTION_HASH}'
                  AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                  AND p.prosecdef AND p.proconfig = ARRAY['search_path=pg_catalog, public']
                  AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl WHERE acl.grantee <> p.proowner))
                THEN RAISE EXCEPTION '0109 guard catalog drift'; END IF; END $body$""")
            for name, (table, *_) in TRIGGERS.items():
                op.execute(f"DROP TRIGGER {name} ON public.{table}")
            op.execute(f"DROP FUNCTION public.{FUNCTION_NAME}()")
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()", expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label="notification_person_targets_readiness_0109")
    if not upgrade:
        if dialect == "sqlite":
            op.execute("DROP TRIGGER trg_notification_manifest_immutable_0109")
            op.execute("DROP TRIGGER trg_notification_event_no_delete_0109")
        op.drop_table(BINDINGS)
        op.drop_table(TARGETS)
        if dialect == "sqlite":
            # Native DROP COLUMN preserves the parent's dependent fact tables;
            # a SQLite batch table rebuild could cascade-delete old recipients.
            op.execute("ALTER TABLE notification_events DROP COLUMN target_manifest_sha256")
        else:
            op.drop_column("notification_events", "target_manifest_sha256")


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
