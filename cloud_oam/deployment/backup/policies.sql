-- Role memberships can change without waiting for our relation locks. Refuse
-- every restrictive SELECT/ALL policy, including policies for unrelated roles,
-- so a concurrent membership grant cannot turn a full export into filtered data.
SELECT NOT EXISTS (
 SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 JOIN pg_policy p ON p.polrelid=c.oid
 WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relrowsecurity
   AND p.polcmd IN ('r','*') AND NOT p.polpermissive
) AS restrictive_policies_absent
\gset
\if :restrictive_policies_absent
\else
DO $$BEGIN RAISE EXCEPTION 'RSC_BACKUP_RESTRICTIVE_POLICY_REFUSED'; END$$;
\endif
WITH applicable_policies AS (
 SELECT c.oid,p.polname,p.polcmd,p.polpermissive,pg_get_expr(p.polqual,p.polrelid) AS using_expression,
 p.polroles=ARRAY[r.oid]::oid[] AS backup_only,p.polwithcheck IS NULL AS no_check
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN pg_roles r
 LEFT JOIN pg_policy p ON p.polrelid=c.oid AND p.polcmd IN ('r','*')
   AND (0=ANY(p.polroles) OR r.oid=ANY(p.polroles))
 WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relrowsecurity
   AND r.rolname='star_oam_backup'
)
SELECT count(*)>0 AND bool_and(COALESCE(polname IS NOT NULL AND polcmd='r'
 AND polpermissive AND using_expression='true' AND backup_only AND no_check,false)) AS policies_complete
FROM applicable_policies
\gset
\if :policies_complete
\else
DO $$BEGIN RAISE EXCEPTION 'RSC_BACKUP_RLS_COMPLETENESS_REFUSED'; END$$;
\endif
