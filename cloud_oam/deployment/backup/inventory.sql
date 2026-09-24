WITH relations AS (
 SELECT c.oid,n.oid AS namespace_oid,n.nspname,c.relname,c.relkind
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind IN ('r','p','m','f') AND n.nspname !~ '^pg_'
   AND n.nspname <> 'information_schema'
)
SELECT count(*)>0 AND bool_and(nspname='public' AND relkind IN ('r','p')) AS inventory_supported,
 encode(sha256(convert_to(COALESCE(jsonb_agg(jsonb_build_array(oid,namespace_oid,nspname,relname,relkind)
 ORDER BY nspname,relname,oid),'[]'::jsonb)::text,'UTF8')),'hex') AS inventory_hash
FROM relations
\gset
\if :inventory_supported
\else
DO $$BEGIN RAISE EXCEPTION 'RSC_BACKUP_RELATION_SCOPE_REFUSED'; END$$;
\endif
