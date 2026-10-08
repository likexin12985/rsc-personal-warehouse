"""Append immutable OpenBao pins and one cross-provider application-version key.

This is binding infrastructure only: no provider activation, key generation,
ciphertext conversion or application-envelope change. The legacy 0040 table,
constraints, indexes and immutable functions remain intact.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import runpy

from alembic import context, op
import sqlalchemy as sa


revision = "20261229_0180"
down_revision = "20261228_0179"
branch_labels = depends_on = None

PIN_TABLE = "openbao_data_key_pins"
CLAIM_TABLE = "application_key_version_claims"
LEGACY_TABLE = "kms_data_key_pins"
MIGRATOR = "star_oam_migrator"
IMMUTABLE_FUNCTION = "rsc_reject_key_provider_binding_mutation_0180"
CLAIM_GUARD_FUNCTION = "rsc_guard_application_key_version_claim_0180"
PIN_CLAIM_FUNCTION = "rsc_claim_application_key_version_0180"
FUNCTION_NAMES = (IMMUTABLE_FUNCTION, CLAIM_GUARD_FUNCTION, PIN_CLAIM_FUNCTION)
PG_TRIGGERS = {
    "trg_openbao_data_key_pins_immutable_0180": (PIN_TABLE, "BEFORE UPDATE OR DELETE", "ROW", IMMUTABLE_FUNCTION),
    "trg_openbao_data_key_pins_no_truncate_0180": (PIN_TABLE, "BEFORE TRUNCATE", "STATEMENT", IMMUTABLE_FUNCTION),
    "trg_application_key_version_claims_immutable_0180": (CLAIM_TABLE, "BEFORE UPDATE OR DELETE", "ROW", IMMUTABLE_FUNCTION),
    "trg_application_key_version_claims_no_truncate_0180": (CLAIM_TABLE, "BEFORE TRUNCATE", "STATEMENT", IMMUTABLE_FUNCTION),
    "trg_application_key_version_claims_insert_0180": (CLAIM_TABLE, "BEFORE INSERT", "ROW", CLAIM_GUARD_FUNCTION),
    "trg_openbao_data_key_pins_claim_0180": (PIN_TABLE, "AFTER INSERT", "ROW", PIN_CLAIM_FUNCTION),
    "trg_kms_data_key_pins_claim_0180": (LEGACY_TABLE, "AFTER INSERT", "ROW", PIN_CLAIM_FUNCTION),
}
RAW = (Path(__file__).parents[1] / "key_provider_bindings_0180/functions.json").read_bytes()
if sha256(RAW).hexdigest() != "39f4a03998a528a5dd9a0e5314e783b9bd9f3ccb14a364fe8014f97a048d4109":
    raise ValueError("0180 frozen readiness catalog changed")
DATA = json.loads(RAW)
DOWNGRADE_BLOCKER = "cannot downgrade 0180 while OpenBao pins or non-exact legacy claims exist"


def _sources():
    return {"public." + signature: (row["before"], row["after"]) for signature, row in DATA["functions"].items()}


def _remainder(expression, alphabet):
    for character in alphabet:
        expression = f"replace({expression}, '{character}', '')"
    return expression


def _sha_check(column):
    return f"length({column}) = 64 AND length({_remainder(column, '0123456789abcdef')}) = 0"


def _create_tables():
    op.create_table(
        PIN_TABLE,
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("provider_instance_id", sa.String(63), nullable=False),
        sa.Column("key_path", sa.String(128), nullable=False),
        sa.Column("application_key_version", sa.Integer(), nullable=False),
        sa.Column("transit_key_version", sa.Integer(), nullable=False),
        sa.Column("ciphertext_sha256", sa.String(64), nullable=False),
        sa.Column("context_sha256", sa.String(64), nullable=False),
        sa.Column("associated_data_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("purpose", "application_key_version", name="pk_openbao_data_key_pins_0180"),
        sa.UniqueConstraint("ciphertext_sha256", name="uq_openbao_data_key_pins_ciphertext_0180"),
        sa.CheckConstraint("purpose IN ('authentication_idempotency', 'material_request_contact')", name="ck_openbao_data_key_pins_purpose_0180"),
        sa.CheckConstraint("environment IN ('development', 'test', 'staging', 'production')", name="ck_openbao_data_key_pins_environment_0180"),
        sa.CheckConstraint("application_key_version BETWEEN 1 AND 2147483647 AND transit_key_version BETWEEN 1 AND 2147483647", name="ck_openbao_data_key_pins_versions_0180"),
        sa.CheckConstraint("length(provider_instance_id) BETWEEN 3 AND 63 AND substr(provider_instance_id, 1, 1) <> '-' AND length(" + _remainder("provider_instance_id", "abcdefghijklmnopqrstuvwxyz0123456789-") + ") = 0", name="ck_openbao_data_key_pins_instance_0180"),
        sa.CheckConstraint("(purpose = 'authentication_idempotency' AND key_path = 'transit/keys/rsc-authentication-idempotency') OR (purpose = 'material_request_contact' AND key_path = 'transit/keys/rsc-material-request-contact')", name="ck_openbao_data_key_pins_key_path_0180"),
        sa.CheckConstraint(" AND ".join(_sha_check(column) for column in ("ciphertext_sha256", "context_sha256", "associated_data_sha256")), name="ck_openbao_data_key_pins_hashes_0180"),
    )
    op.create_table(
        CLAIM_TABLE,
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("application_key_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("ciphertext_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("purpose", "application_key_version", name="pk_application_key_version_claims_0180"),
        sa.CheckConstraint("purpose IN ('authentication_idempotency', 'material_request_contact')", name="ck_application_key_version_claims_purpose_0180"),
        sa.CheckConstraint("provider IN ('aliyun_kms', 'openbao_transit_v1')", name="ck_application_key_version_claims_provider_0180"),
        sa.CheckConstraint("application_key_version BETWEEN 1 AND 2147483647", name="ck_application_key_version_claims_version_0180"),
        sa.CheckConstraint(_sha_check("ciphertext_sha256"), name="ck_application_key_version_claims_hash_0180"),
    )


def _postgresql_functions():
    return {
        IMMUTABLE_FUNCTION: f"""
CREATE FUNCTION public.{IMMUTABLE_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = '55000',
        MESSAGE = 'Application key provider bindings are immutable';
END
$$
""",
        CLAIM_GUARD_FUNCTION: f"""
CREATE FUNCTION public.{CLAIM_GUARD_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF current_user <> 'star_oam_migrator' OR session_user <> 'star_oam_migrator'
       OR TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME <> '{CLAIM_TABLE}'
       OR TG_OP <> 'INSERT' OR TG_WHEN <> 'BEFORE' THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Direct migrator key-claim insertion required';
    END IF;
    IF NOT (
        (NEW.provider = 'aliyun_kms' AND EXISTS (
            SELECT 1 FROM public.{LEGACY_TABLE} p
             WHERE p.purpose = NEW.purpose
               AND p.application_key_version = NEW.application_key_version
               AND p.ciphertext_sha256 = NEW.ciphertext_sha256
               AND p.created_at = NEW.created_at))
        OR (NEW.provider = 'openbao_transit_v1' AND EXISTS (
            SELECT 1 FROM public.{PIN_TABLE} p
             WHERE p.purpose = NEW.purpose
               AND p.application_key_version = NEW.application_key_version
               AND p.ciphertext_sha256 = NEW.ciphertext_sha256
               AND p.created_at = NEW.created_at))
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'Application key claim requires its exact existing pin';
    END IF;
    RETURN NEW;
END
$$
""",
        PIN_CLAIM_FUNCTION: f"""
CREATE FUNCTION public.{PIN_CLAIM_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE provider_name text;
BEGIN
    IF current_user <> 'star_oam_migrator' OR session_user <> 'star_oam_migrator'
       OR TG_TABLE_SCHEMA <> 'public'
       OR TG_TABLE_NAME NOT IN ('{LEGACY_TABLE}', '{PIN_TABLE}')
       OR TG_OP <> 'INSERT' OR TG_WHEN <> 'AFTER' THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Direct migrator key-pin insertion required';
    END IF;
    provider_name := CASE TG_TABLE_NAME WHEN '{LEGACY_TABLE}'
        THEN 'aliyun_kms' ELSE 'openbao_transit_v1' END;
    -- The immediate shared primary key arbitrates concurrent providers.
    -- An ordinary INSERT is intentional: never ignore a conflicting claim.
    INSERT INTO public.{CLAIM_TABLE}
        (purpose, application_key_version, provider, ciphertext_sha256, created_at)
    VALUES (NEW.purpose, NEW.application_key_version, provider_name,
        NEW.ciphertext_sha256, NEW.created_at);
    RETURN NEW;
END
$$
""",
    }


def _check_readiness_source(up):
    # Keep the query local to this frozen migration; runtime modules evolve.
    # Matching the body alone would retain a pre-existing ACL/owner/definer
    # drift across CREATE OR REPLACE. Observe every public overload as well.
    wanted = dict(DATA["readiness"]["before" if up else "after"])
    rows = op.get_bind().execute(sa.text("""
SELECT p.proname, p.proname||'('||pg_catalog.oidvectortypes(p.proargtypes)||')' AS signature,
       pg_catalog.pg_get_functiondef(p.oid) AS definition, p.prosrc, p.prosecdef,
       p.provolatile, p.proparallel, p.proisstrict, p.proleakproof, p.proconfig,
       pg_catalog.pg_get_userbyid(p.proowner) AS owner,
       pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_arguments,
       ARRAY(SELECT pg_catalog.jsonb_build_object(
           'grantee', CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,
           'privilege', a.privilege_type, 'grantable', a.is_grantable)
           FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
           ORDER BY a.grantee,a.privilege_type) AS acl
  FROM pg_catalog.pg_proc p
 WHERE p.pronamespace='public'::regnamespace AND p.proname=:name
 ORDER BY pg_catalog.oidvectortypes(p.proargtypes)
"""), {"name": wanted["proname"]}).mappings().all()
    if len(rows) != 1:
        raise ValueError("0180 exact readiness function identity required")
    actual = dict(rows[0])
    acl_key = lambda item: (item["grantee"], item["privilege"], item["grantable"])
    actual["acl"] = sorted(actual["acl"], key=acl_key)
    wanted["acl"] = sorted(wanted["acl"], key=acl_key)
    if actual != wanted:
        raise ValueError("0180 exact readiness function catalog required")


def _replace_readiness(up):
    helper = runpy.run_path(str(Path(__file__).with_name("20260909_0069_stock_reservations.py")))
    old, new = ("before", "after") if up else ("after", "before")
    for signature, row in DATA["functions"].items():
        helper["_replace_function_source"](signature="public." + signature,
            expected_hash=row[old + "Sha256"], replacement_hash=row[new + "Sha256"],
            replacements=((row[old], row[new]),), label="key_provider_bindings_0180")
    _check_readiness_source(not up)


def _preflight(up):
    if context.is_offline_mode():
        raise ValueError("0180 online predecessor verification required")
    db = op.get_bind()
    dialect = db.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise ValueError("0180 requires PostgreSQL16 or SQLite schema tooling")
    if dialect == "sqlite":
        # SQLite models shape and transaction behavior only, never PG16 ACLs.
        db.exec_driver_sql("UPDATE kms_data_key_pins SET purpose=purpose WHERE 0")
        return dialect
    identity = db.execute(sa.text("SELECT current_user, session_user, current_setting('server_version_num')::int, current_setting('transaction_isolation')")).one()
    if identity[:2] != (MIGRATOR, MIGRATOR) or identity[2] // 10000 != 16 or identity[3] != "read committed":
        raise ValueError("0180 direct PostgreSQL16 read-committed migrator required")
    if any(db.execute(sa.text("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user")).one()):
        raise ValueError("0180 unprivileged migrator required")
    tables = ["alembic_version", LEGACY_TABLE, "auth_idempotency_operations", "material_requests", "material_request_revisions"]
    if not up:
        tables.extend((PIN_TABLE, CLAIM_TABLE))
    db.execute(sa.text("LOCK TABLE " + ", ".join("public." + table for table in tables) + " IN ACCESS EXCLUSIVE MODE"))
    if db.execute(sa.text("SELECT version_num FROM public.alembic_version")).scalars().all() != [down_revision if up else revision]:
        raise ValueError("0180 exact predecessor required")
    _check_legacy_guards()
    _check_readiness_source(up)
    return dialect


def _check_legacy_guards():
    db = op.get_bind()
    # Preserve 0040's actual immutable proof, not just matching trigger names.
    old = runpy.run_path(str(Path(__file__).with_name("20260901_0040_kms_data_key_pins.py")))
    expected_source = old["_postgresql_immutable_function_sql"]().split("AS $$", 1)[1].split("$$", 1)[0]
    row = db.execute(sa.text("SELECT p.prosrc, p.prosecdef, p.proconfig, r.rolname FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner WHERE p.oid=to_regprocedure('public.rsc_reject_kms_data_key_pin_mutation_0040()')")).one_or_none()
    if row is None or tuple(row) != (expected_source, True, ["search_path=pg_catalog, public"], MIGRATOR):
        raise ValueError("0180 legacy immutable function drift")
    actual = db.execute(sa.text("SELECT t.tgname,t.tgtype,t.tgenabled,t.tgdeferrable,t.tginitdeferred,t.tgconstraint,t.tgqual IS NULL,t.tgattr::text,t.tgfoid='public.rsc_reject_kms_data_key_pin_mutation_0040()'::regprocedure FROM pg_catalog.pg_trigger t WHERE t.tgrelid='public.kms_data_key_pins'::regclass AND t.tgname IN ('trg_kms_data_key_pins_immutable_0040','trg_kms_data_key_pins_no_truncate_0040') ORDER BY t.tgname")).all()
    expected = [("trg_kms_data_key_pins_immutable_0040", 27, "A", False, False, 0, True, "", True), ("trg_kms_data_key_pins_no_truncate_0040", 34, "A", False, False, 0, True, "", True)]
    if [tuple(row) for row in actual] != expected:
        raise ValueError("0180 legacy immutable trigger drift")
    if db.scalar(sa.text("SELECT count(*) FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_index i ON i.indexrelid=c.conindid WHERE c.conrelid='public.kms_data_key_pins'::regclass AND c.conname='uq_kms_data_key_pins_purpose_version_0040' AND c.contype='u' AND NOT c.condeferrable AND NOT c.condeferred AND c.convalidated AND i.indisunique AND i.indimmediate AND i.indisvalid AND i.indisready AND i.indislive AND i.indpred IS NULL AND i.indexprs IS NULL AND (SELECT array_agg(a.attname::text ORDER BY u.ordinality) FROM unnest(c.conkey) WITH ORDINALITY u(attnum,ordinality) JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=u.attnum)=ARRAY['purpose','application_key_version']::text[]")) != 1:
        raise ValueError("0180 legacy application-version uniqueness drift")


def _create_pg_trigger(name):
    table, event, level, function = PG_TRIGGERS[name]
    op.execute(f"CREATE TRIGGER {name} {event} ON public.{table} FOR EACH {level} EXECUTE FUNCTION public.{function}()")
    op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")


def _apply_acl():
    # Revoke inherited default object grants too, including worker/projector
    # group grants. Only the direct owner, API reader and optional backup remain.
    for table in (PIN_TABLE, CLAIM_TABLE):
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
        op.execute(f"""DO $$ DECLARE item record; BEGIN
FOR item IN SELECT DISTINCT r.rolname FROM pg_catalog.pg_class c,
  LATERAL aclexplode(c.relacl) a JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
 WHERE c.oid='public.{table}'::regclass AND a.grantee<>c.relowner
LOOP EXECUTE format('REVOKE ALL ON TABLE public.{table} FROM %I',item.rolname); END LOOP;
END $$""")
        op.execute(f"GRANT SELECT ON TABLE public.{table} TO star_oam_api")
        op.execute(f"""DO $$ BEGIN
IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='star_oam_backup') THEN
  GRANT SELECT ON TABLE public.{table} TO star_oam_backup;
END IF; END $$""")
    for function in FUNCTION_NAMES:
        op.execute(f"REVOKE EXECUTE ON FUNCTION public.{function}() FROM PUBLIC")
        op.execute(f"""DO $$ DECLARE item record; BEGIN
FOR item IN SELECT DISTINCT r.rolname FROM pg_catalog.pg_proc p,
  LATERAL aclexplode(p.proacl) a JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
 WHERE p.oid='public.{function}()'::regprocedure AND a.grantee<>p.proowner
LOOP EXECUTE format('REVOKE ALL ON FUNCTION public.{function}() FROM %I',item.rolname); END LOOP;
END $$""")


def _backfill():
    prefix = "public." if op.get_bind().dialect.name == "postgresql" else ""
    op.execute(f"INSERT INTO {prefix}{CLAIM_TABLE} (purpose,application_key_version,provider,ciphertext_sha256,created_at) SELECT purpose,application_key_version,'aliyun_kms',ciphertext_sha256,created_at FROM {prefix}{LEGACY_TABLE}")


def _sqlite_triggers(before_backfill):
    if before_backfill:
        for table in (PIN_TABLE, CLAIM_TABLE):
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER trg_{table}_immutable_{action.lower()}_0180 BEFORE {action} ON {table} FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'Application key provider bindings are immutable'); END")
        # SQLite is single-writer. The explicit duplicate abort also prevents
        # outer INSERT OR REPLACE from overriding a nested INSERT's conflict mode.
        op.execute(f"""CREATE TRIGGER trg_application_key_version_claims_insert_0180 BEFORE INSERT ON {CLAIM_TABLE} FOR EACH ROW BEGIN
SELECT RAISE(ABORT, 'Application key claim already exists') WHERE EXISTS (SELECT 1 FROM {CLAIM_TABLE} c WHERE c.purpose=NEW.purpose AND c.application_key_version=NEW.application_key_version);
SELECT RAISE(ABORT, 'Application key claim requires its exact existing pin') WHERE NOT (
 (NEW.provider='aliyun_kms' AND EXISTS (SELECT 1 FROM {LEGACY_TABLE} p WHERE p.purpose=NEW.purpose AND p.application_key_version=NEW.application_key_version AND p.ciphertext_sha256=NEW.ciphertext_sha256 AND p.created_at=NEW.created_at))
 OR (NEW.provider='openbao_transit_v1' AND EXISTS (SELECT 1 FROM {PIN_TABLE} p WHERE p.purpose=NEW.purpose AND p.application_key_version=NEW.application_key_version AND p.ciphertext_sha256=NEW.ciphertext_sha256 AND p.created_at=NEW.created_at)));
END""")
        return
    for table, provider in ((LEGACY_TABLE, "aliyun_kms"), (PIN_TABLE, "openbao_transit_v1")):
        op.execute(f"""CREATE TRIGGER trg_{table}_no_replace_0180 BEFORE INSERT ON {table} FOR EACH ROW BEGIN
SELECT RAISE(ABORT, 'Application key pin already exists') WHERE EXISTS (SELECT 1 FROM {table} p WHERE (p.purpose=NEW.purpose AND p.application_key_version=NEW.application_key_version) OR p.ciphertext_sha256=NEW.ciphertext_sha256);
END""")
        op.execute(f"""CREATE TRIGGER trg_{table}_claim_0180 AFTER INSERT ON {table} FOR EACH ROW BEGIN
INSERT INTO {CLAIM_TABLE} (purpose,application_key_version,provider,ciphertext_sha256,created_at) VALUES (NEW.purpose,NEW.application_key_version,'{provider}',NEW.ciphertext_sha256,NEW.created_at);
END""")


def _downgrade_blocked():
    prefix = "public." if op.get_bind().dialect.name == "postgresql" else ""
    return op.get_bind().exec_driver_sql(f"""SELECT 1 WHERE
 EXISTS (SELECT 1 FROM {prefix}{PIN_TABLE})
 OR EXISTS (SELECT 1 FROM {prefix}{CLAIM_TABLE} c WHERE c.provider <> 'aliyun_kms' OR NOT EXISTS
   (SELECT 1 FROM {prefix}{LEGACY_TABLE} p WHERE p.purpose=c.purpose AND p.application_key_version=c.application_key_version AND p.ciphertext_sha256=c.ciphertext_sha256 AND p.created_at=c.created_at))
 OR EXISTS (SELECT 1 FROM {prefix}{LEGACY_TABLE} p WHERE NOT EXISTS
   (SELECT 1 FROM {prefix}{CLAIM_TABLE} c WHERE c.purpose=p.purpose AND c.application_key_version=p.application_key_version AND c.provider='aliyun_kms' AND c.ciphertext_sha256=p.ciphertext_sha256 AND c.created_at=p.created_at))
 LIMIT 1""").first() is not None


def upgrade():
    dialect = _preflight(True)
    _create_tables()
    if dialect == "postgresql":
        for source in _postgresql_functions().values():
            op.execute(source)
        for name in PG_TRIGGERS:
            if not name.endswith("_pins_claim_0180"):
                _create_pg_trigger(name)
        _apply_acl()
        _backfill()
        for name in PG_TRIGGERS:
            if name.endswith("_pins_claim_0180"):
                _create_pg_trigger(name)
        _replace_readiness(True)
    else:
        _sqlite_triggers(True)
        _backfill()
        _sqlite_triggers(False)


def downgrade():
    dialect = _preflight(False)
    if _downgrade_blocked():
        raise RuntimeError(DOWNGRADE_BLOCKER)
    if dialect == "postgresql":
        _replace_readiness(False)
        for name, (table, _, _, _) in PG_TRIGGERS.items():
            op.execute(f"DROP TRIGGER {name} ON public.{table}")
        for function in FUNCTION_NAMES:
            op.execute(f"DROP FUNCTION public.{function}()")
    else:
        for table in (PIN_TABLE, CLAIM_TABLE):
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER trg_{table}_immutable_{action}_0180")
        op.execute("DROP TRIGGER trg_application_key_version_claims_insert_0180")
        for table in (LEGACY_TABLE, PIN_TABLE):
            op.execute(f"DROP TRIGGER trg_{table}_claim_0180")
            op.execute(f"DROP TRIGGER trg_{table}_no_replace_0180")
    op.drop_table(CLAIM_TABLE)
    op.drop_table(PIN_TABLE)
