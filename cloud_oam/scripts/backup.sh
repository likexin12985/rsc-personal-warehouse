#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKUP_DIR=${BACKUP_DIR:-/opt/star-oam/backups}
STAMP=$(date +%Y%m%d_%H%M%S)_$$

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  . "$ROOT_DIR/.env"
  set +a
fi

umask 077
mkdir -p "$BACKUP_DIR"
TMP_DIR=$(mktemp -d "$BACKUP_DIR/.star-oam-backup.XXXXXX")
DATABASE_RAW="$TMP_DIR/database.sql"
DATABASE_TMP="$TMP_DIR/database.sql.gz"
UPLOADS_TMP="$TMP_DIR/uploads.tar.gz"
MANIFEST_TMP="$TMP_DIR/manifest.sha256"
BUNDLE_LIST="$TMP_DIR/bundle.list"
BUNDLE_TMP="$TMP_DIR/backup_$STAMP.tar"
BUNDLE_FINAL="$BACKUP_DIR/backup_$STAMP.tar"

cleanup() {
  rm -f \
    "$DATABASE_RAW" \
    "$DATABASE_TMP" \
    "$UPLOADS_TMP" \
    "$MANIFEST_TMP" \
    "$BUNDLE_LIST" \
    "$BUNDLE_TMP"
  rmdir "$TMP_DIR" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

cd "$ROOT_DIR"
BACKUP_ROLE_OK=$(
  docker compose exec -T \
    -e PGPASSWORD="$OAM_DB_BACKUP_PASSWORD" \
    db psql -X --set=ON_ERROR_STOP=1 --tuples-only --no-align \
    -U star_oam_backup --dbname "$POSTGRES_DB" <<'SQL'
SELECT CASE WHEN
    current_user = 'star_oam_backup'
    AND NOT role_row.rolsuper
    AND NOT role_row.rolcreatedb
    AND NOT role_row.rolcreaterole
    AND NOT role_row.rolreplication
    AND NOT role_row.rolbypassrls
    AND current_setting('session_replication_role') = 'origin'
    AND NOT has_parameter_privilege(
        current_user, 'session_replication_role', 'SET'
    )
    AND NOT has_database_privilege(
        current_user, current_database(), 'CREATE'
    )
    AND NOT has_database_privilege(
        current_user, current_database(), 'TEMPORARY'
    )
    AND NOT EXISTS (
        SELECT 1
          FROM pg_database AS database_row
          CROSS JOIN LATERAL aclexplode(database_row.datacl) AS database_acl
         WHERE database_row.datname = current_database()
           AND database_acl.grantee IN (0, role_row.oid)
           AND database_acl.is_grantable
    )
    AND current_schema() = 'public'
    AND current_schemas(FALSE) = ARRAY['public']::name[]
    AND has_schema_privilege(current_user, 'public', 'USAGE')
    AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
    AND NOT EXISTS (
        SELECT 1
          FROM pg_namespace AS namespace_row
          CROSS JOIN LATERAL aclexplode(namespace_row.nspacl) AS schema_acl
         WHERE namespace_row.nspname = 'public'
           AND schema_acl.grantee IN (0, role_row.oid)
           AND schema_acl.is_grantable
    )
    AND NOT EXISTS (
        SELECT 1 FROM pg_auth_members AS membership
         WHERE membership.member = role_row.oid
            OR membership.roleid = role_row.oid
    )
    AND NOT EXISTS (
        SELECT 1 FROM pg_namespace AS namespace_row
         WHERE pg_get_userbyid(namespace_row.nspowner) = current_user
            OR has_schema_privilege(
                current_user, namespace_row.oid, 'CREATE'
            )
            OR (
                namespace_row.nspname NOT LIKE 'pg_%'
                AND namespace_row.nspname <> 'information_schema'
                AND namespace_row.nspname <> 'public'
                AND has_schema_privilege(
                    current_user, namespace_row.oid, 'USAGE'
                )
            )
    )
    AND NOT EXISTS (
        SELECT 1
          FROM pg_class AS class_row
          JOIN pg_namespace AS namespace_row
            ON namespace_row.oid = class_row.relnamespace
         WHERE namespace_row.nspname = 'public'
           AND class_row.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND (
               pg_get_userbyid(class_row.relowner) = current_user
               OR NOT has_table_privilege(
                   current_user, class_row.oid, 'SELECT'
               )
               OR has_table_privilege(
                   current_user, class_row.oid, 'INSERT'
               )
               OR has_table_privilege(
                   current_user, class_row.oid, 'UPDATE'
               )
               OR has_table_privilege(
                   current_user, class_row.oid, 'DELETE'
               )
               OR has_table_privilege(
                   current_user, class_row.oid, 'TRUNCATE'
               )
               OR has_table_privilege(
                   current_user, class_row.oid, 'REFERENCES'
               )
               OR has_table_privilege(
                   current_user, class_row.oid, 'TRIGGER'
               )
               OR has_any_column_privilege(
                   current_user, class_row.oid, 'INSERT'
               )
               OR has_any_column_privilege(
                   current_user, class_row.oid, 'UPDATE'
               )
               OR has_any_column_privilege(
                   current_user, class_row.oid, 'REFERENCES'
               )
               OR EXISTS (
                   SELECT 1
                     FROM pg_attribute AS attribute_row
                     CROSS JOIN LATERAL aclexplode(
                         attribute_row.attacl
                     ) AS column_acl
                    WHERE attribute_row.attrelid = class_row.oid
                      AND attribute_row.attnum > 0
                      AND NOT attribute_row.attisdropped
                      AND column_acl.grantee IN (0, role_row.oid)
               )
               OR EXISTS (
                   SELECT 1
                     FROM aclexplode(class_row.relacl) AS table_acl
                    WHERE table_acl.grantee IN (0, role_row.oid)
                      AND table_acl.is_grantable
               )
           )
    )
    AND NOT EXISTS (
        SELECT 1
          FROM pg_class AS class_row
          JOIN pg_namespace AS namespace_row
            ON namespace_row.oid = class_row.relnamespace
         WHERE namespace_row.nspname = 'public'
           AND class_row.relkind = 'S'
           AND (
               pg_get_userbyid(class_row.relowner) = current_user
               OR NOT has_sequence_privilege(
                   current_user, class_row.oid, 'SELECT'
               )
               OR has_sequence_privilege(
                   current_user, class_row.oid, 'USAGE'
               )
               OR has_sequence_privilege(
                   current_user, class_row.oid, 'UPDATE'
               )
               OR EXISTS (
                   SELECT 1
                     FROM aclexplode(class_row.relacl) AS sequence_acl
                    WHERE sequence_acl.grantee IN (0, role_row.oid)
                      AND sequence_acl.is_grantable
               )
           )
    )
    AND NOT EXISTS (
        SELECT 1
          FROM pg_proc AS function_row
          JOIN pg_namespace AS namespace_row
            ON namespace_row.oid = function_row.pronamespace
         WHERE namespace_row.nspname = 'public'
           AND (
               pg_get_userbyid(function_row.proowner) = current_user
               OR has_function_privilege(
                   current_user, function_row.oid, 'EXECUTE'
               )
           )
    )
THEN 'ok' ELSE 'blocked' END
FROM pg_roles AS role_row
WHERE role_row.rolname = current_user;
SQL
)
if [ "$BACKUP_ROLE_OK" != "ok" ]; then
  printf '%s\n' 'backup role security boundary is not satisfied' >&2
  exit 1
fi

docker compose exec -T \
  -e PGPASSWORD="$OAM_DB_BACKUP_PASSWORD" \
  db pg_dump -U star_oam_backup "$POSTGRES_DB" > "$DATABASE_RAW"
test -s "$DATABASE_RAW"
gzip -c "$DATABASE_RAW" > "$DATABASE_TMP"
test -s "$DATABASE_TMP"
gzip -t "$DATABASE_TMP"

docker compose exec -T api tar -czf - -C /data/uploads . > "$UPLOADS_TMP"
test -s "$UPLOADS_TMP"
tar -tzf "$UPLOADS_TMP" >/dev/null

(
  cd "$TMP_DIR"
  sha256sum database.sql.gz uploads.tar.gz > manifest.sha256
  sha256sum -c manifest.sha256 >/dev/null
  tar -cf "$BUNDLE_TMP" database.sql.gz uploads.tar.gz manifest.sha256
)
tar -tf "$BUNDLE_TMP" > "$BUNDLE_LIST"
test "$(wc -l < "$BUNDLE_LIST" | tr -d ' ')" -eq 3
grep -qx 'database.sql.gz' "$BUNDLE_LIST"
grep -qx 'uploads.tar.gz' "$BUNDLE_LIST"
grep -qx 'manifest.sha256' "$BUNDLE_LIST"

# One same-filesystem rename is the only publication point.  A failed dump,
# archive, checksum, or rename leaves no discoverable final backup set.
mv "$BUNDLE_TMP" "$BUNDLE_FINAL"
find "$BACKUP_DIR" -type f -name 'backup_*.tar' -mtime +14 -delete
printf 'backup=%s\n' "$BUNDLE_FINAL"
