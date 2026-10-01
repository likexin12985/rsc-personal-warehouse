-- Run as the separately authorized postgres DBA after application role setup.
-- One transactional DO statement: repeat is read-only when the exact catalog
-- already exists; drift is refused, never repaired or relocated silently.
DO $loss_uuid_bootstrap_0159$
DECLARE namespace_id oid; extension_id oid; generator_id oid; migrator_id oid;
BEGIN
    IF current_user<>'postgres' OR session_user<>'postgres'
       OR NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
       OR current_setting('server_version_num')::integer/10000<>16
       OR current_setting('transaction_isolation')<>'read committed'
       OR current_database() IN ('postgres','template0','template1') THEN
        RAISE EXCEPTION '0159 explicit PostgreSQL16 database DBA bootstrap required' USING ERRCODE='42501';
    END IF;
    SELECT oid INTO migrator_id FROM pg_roles WHERE rolname='star_oam_migrator'
        AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls;
    IF migrator_id IS NULL OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='star_oam_api'
            AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls)
       OR NOT EXISTS(SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=migrator_id) THEN
        RAISE EXCEPTION '0159 preconfigured separate application database roles required' USING ERRCODE='42501';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('rsc.loss.uuid.bootstrap.0159',0));
    SELECT oid INTO namespace_id FROM pg_namespace WHERE nspname='rsc_loss_uuid_0159';
    SELECT oid INTO extension_id FROM pg_extension WHERE extname='uuid-ossp';
    IF namespace_id IS NULL AND extension_id IS NULL THEN
        CREATE SCHEMA rsc_loss_uuid_0159 AUTHORIZATION star_oam_migrator;
        REVOKE ALL ON SCHEMA rsc_loss_uuid_0159 FROM PUBLIC,star_oam_api;
        CREATE EXTENSION "uuid-ossp" WITH SCHEMA rsc_loss_uuid_0159 VERSION '1.1';
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA rsc_loss_uuid_0159 FROM PUBLIC,star_oam_api;
        GRANT EXECUTE ON FUNCTION rsc_loss_uuid_0159.uuid_generate_v5(uuid,text) TO star_oam_migrator;
        SELECT oid INTO namespace_id FROM pg_namespace WHERE nspname='rsc_loss_uuid_0159';
        SELECT oid INTO extension_id FROM pg_extension WHERE extname='uuid-ossp';
    ELSIF namespace_id IS NULL OR extension_id IS NULL THEN
        RAISE EXCEPTION '0159 partial or relocated UUID dependency requires review' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_namespace n WHERE n.oid=namespace_id AND n.nspowner=migrator_id
        AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a WHERE a.grantee<>n.nspowner))
       OR NOT EXISTS(SELECT 1 FROM pg_extension e WHERE e.oid=extension_id AND e.extnamespace=namespace_id
            AND e.extversion='1.1' AND e.extowner=(SELECT oid FROM pg_roles WHERE rolname='postgres')) THEN
        RAISE EXCEPTION '0159 private UUID schema or extension catalog drift' USING ERRCODE='23514';
    END IF;
    SELECT p.oid INTO generator_id FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
        WHERE p.pronamespace=namespace_id AND p.proname='uuid_generate_v5' AND p.pronargs=2
          AND p.proargtypes[0]='uuid'::regtype AND p.proargtypes[1]='text'::regtype
          AND p.prorettype='uuid'::regtype AND p.prosrc='uuid_generate_v5' AND p.probin='$libdir/uuid-ossp'
          AND l.lanname='c' AND p.provolatile='i' AND p.proisstrict AND NOT p.prosecdef
          AND p.proparallel='s' AND p.prokind='f' AND NOT p.proleakproof AND p.proconfig IS NULL
          AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='postgres')
          AND EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid
            AND d.refclassid='pg_extension'::regclass AND d.refobjid=extension_id AND d.deptype='e');
    IF generator_id IS NULL OR NOT has_function_privilege('star_oam_migrator',generator_id,'EXECUTE')
       OR has_schema_privilege('star_oam_api',namespace_id,'USAGE')
       OR has_function_privilege('star_oam_api',generator_id,'EXECUTE')
       OR EXISTS(SELECT 1 FROM pg_proc p,
          LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
          WHERE p.pronamespace=namespace_id AND a.grantee<>p.proowner
            AND NOT(p.oid=generator_id AND a.grantee=migrator_id AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)) THEN
        RAISE EXCEPTION '0159 private UUID function source or privilege drift' USING ERRCODE='23514';
    END IF;
END;
$loss_uuid_bootstrap_0159$;
