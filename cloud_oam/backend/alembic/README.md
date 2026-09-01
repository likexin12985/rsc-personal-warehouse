# cloud_oam database migrations

This directory versions the database schema.  The initial revision is a frozen,
explicit representation of the `cloud_oam v0.9.0` ORM schema; it does not call
`Base.metadata.create_all()` and it does not import the current ORM at upgrade
time.

Run commands from the `cloud_oam` directory:

```bash
OAM_ENVIRONMENT=production \
OAM_DATABASE_EXPECTED_MIGRATION_ROLE=star_oam_migrator \
OAM_DATABASE_EXPECTED_RUNTIME_ROLE=star_oam_api \
OAM_DATABASE_URL='postgresql+psycopg://star_oam_migrator:...@.../star_oam' \
alembic -c alembic.ini upgrade head
```

No default database URL is embedded in `alembic.ini`.  A command without either
`OAM_DATABASE_URL` or a programmatically supplied `sqlalchemy.url` fails closed.

## Safe stamp and structural fingerprint for an existing v0.9.0 database

Do **not** run the initial upgrade against a populated v0.9.0 database: the
revision intentionally creates every table and will fail when a table already
exists.  Stamping records history only; it does not validate or alter schema.

Use this controlled adoption procedure:

1. Stop application writes and take a verified database backup.
2. Export schema only from the candidate database.  Do not include table data.
3. In a disposable empty PostgreSQL 16 database, run
   `upgrade 20260830_0001` (not `head`), export its schema, and normalize both
   schema exports to table, column, type, nullability, primary key, foreign key,
   unique/check constraint, and index definitions.
   Sort that representation and calculate SHA-256 for both files.  Require both
   identical hashes **and** an empty structural diff.  Ignore only owner/ACL
   comments and generated constraint names; do not ignore missing columns,
   indexes, constraints, defaults, or extra application tables.
4. Confirm that the only application tables are the 21 listed below and that
   the candidate has no `alembic_version` row for another lineage.
5. If and only if the structural comparison is exact, run
   `alembic -c alembic.ini stamp 20260830_0001`, then reread with `current`.
   If it differs, stop and create a reviewed reconciliation migration instead
   of stamping or retrying blindly.
6. Restore a schema-only clone into an isolated pre-production database and run
   `upgrade head` there. Require the migration tests, structural checks, role
   seed reread, and application smoke tests to pass before scheduling the same
   upgrade for production. Never stamp `head` to skip revisions 0002 through
   0010.

Expected v0.9.0 application tables:

```text
audit_logs
auth_sessions
external_sync_batches
external_sync_current_records
external_sync_records
external_sync_snapshot_batches
external_sync_snapshot_records
external_sync_snapshots
inventory_balances
materials
media_attachments
oam_personnel_bindings
sms_login_challenges
stocktake_items
stocktake_tasks
transfer_items
transfers
users
warehouses
wechat_identities
work_order_materials
```

The v0.9.0 schema remains a prototype baseline: string UUIDs, integer
quantities, and several unconstrained status strings do not satisfy the formal
production design's PostgreSQL UUID, `numeric(18,3)`, controlled-state, and
immutable-ledger requirements.  Those gaps require later, reviewed migrations;
stamping this revision is not production acceptance.

## Revision 0002: additive V1.0 stage-one foundation

Revision `20260830_0002` adds 33 organization/identity/RBAC, synchronization,
migration, reconciliation, file, notification, Outbox, audit, and system
parameter tables. It seeds only the four fixed role definitions and creates no
user, mobile identity, permission assignment, inventory fact, or external
mapping. It does not alter the 21 legacy v0.9 tables and does not execute any
external synchronization.

The revision is therefore a compatibility foundation, not completion of V1.0:
legacy `users`, `materials`, integer inventory balances, transfers, stocktakes,
and work-order material tables remain quarantined prototypes until their
separate reviewed migrations land. Run downgrades only against disposable
development/test databases; a production rollback must use the approved
backup/restore plan because dropping the foundation tables destroys any data
written to them after upgrade.

## Revision 0003: formal identity and stage-one RBAC activation schema

Revision `20260830_0003` appends the formal identity boundary to the legacy
`users` table without removing or interpreting any v0.9 field. Existing rows
receive `account_status=pending_identity`, `person_id=NULL`, and no role
assignment or authentication identity. This is deliberate: a later reviewed
provisioning step must bind one exact person, verified identity, fixed role, and
data scope. Never infer that binding from a legacy phone, province, or role.

The revision also adds identity hash versioning and status consistency,
provider/client metadata for login challenges, role-assignment revocation
evidence, single-use refresh-token history, and audit-chain heads. It seeds a
fixed, minimal stage-one permission catalog and only the corresponding role
permission matrix. It creates no user, phone/OpenID identity, person mapping,
role assignment, session, or audit event, and it does not contact any external
system.

Legacy `users.mobile`, password, role, and province columns remain compatibility
data only. The application now loads a formal Principal with deny-first,
role-specific scope evaluation; production session creation, refresh, and every
protected request revalidate that graph, and production does not mount v0.9
business routes. Those runtime guards do not turn this schema migration into an
identity provisioning migration. Provider-specific passwordless identity
matching, an approved person/account activation manifest, field-level external
approval permissions, and the immutable authentication audit writer must still
land before any account is changed from `pending_identity` to `active`.

## Revision 0004: reviewed role-assignment administration boundary

Revision `20260830_0004` adds the fixed `people/read_minimal` and
`role_assignment/manage_provincial` permissions. Only the fixed `admin` role is
granted these permissions. They are the minimum database-side capabilities for
the formal administrator workflow that selects a uniquely identified person
and assigns the regional-manager role with one explicit organization scope.

The revision also creates the partial unique index
`uq_role_assignments_current_scope` over
`user_id + role_id + scope_type + scope_id` for rows whose status is
`scheduled` or `active`. Both PostgreSQL and SQLite definitions are explicit.
The predicate intentionally does not inspect `valid_to`: if an expiry worker
has not transitioned a stale row to `expired`, the database fails closed and
rejects a concurrent replacement. An operator must record the reviewed status
transition or revocation before retrying; do not delete authorization history.
Before creating the index, the online migration explicitly scans for duplicate
current scopes and stops before seeding any 0004 row when a conflict exists.
The PostgreSQL offline SQL contains the equivalent `DO`-block guard.

Revision 0004 also pre-seeds one empty `authorization` audit-chain head. The
runtime writer never creates security-critical heads on first use. Downgrade
may remove this row only while it is still the exact unused version-0 seed; an
advanced or altered head blocks downgrade so audit history cannot be detached
and silently restarted.

This revision creates no user, person, authentication identity, or role
assignment. The stated policy that uniquely matched personnel receive an
engineer assignment by default, that the four named NIO headquarters users
receive administrator assignments, and that provincial-manager assignments
start empty is implemented by audited application services, not migration
seeds. The default-role service requires an already valid nationwide formal
administrator and cannot bootstrap the first administrator. Names alone are
not unique identities and remain absent from this migration; the first admin
still requires a separately reviewed stable-person-id activation manifest.

## Revision 0005: formal authentication runtime foundation

Revision `20260830_0005` pre-seeds one empty `authentication` audit-chain head
with a fixed UUID. Authentication services must append challenge, verification,
login, refresh, revocation, and failure evidence through that reviewed stream;
they must never create a chain head on first use. The revision also adds the
composite `login_challenges(requested_ip_hash, created_at)` index required for
hashed-IP challenge rate-limit queries without storing a plaintext client IP.

The revision makes challenge verification evidence explicit without inventing
evidence that a provider did not return. `verification_mode=local_hash` requires
a non-empty `code_hash`; `verification_mode=provider_managed` requires
`code_hash=NULL`, while `provider_reference` remains nullable so it can be
recorded after a successful provider send. Existing rows with a non-empty hash
are deterministically classified as `local_hash`. Existing rows with an empty
or null hash are classified as `legacy_unknown`; they are never guessed to be
provider-managed. `legacy_unknown` has no database default and is a migration
compatibility value only: formal runtime code must choose `local_hash` or
`provider_managed` explicitly and must not create new `legacy_unknown` rows.

Downgrade inspects the exact authentication head before dropping the index or
deleting any row. It proceeds only when exactly one head has the fixed ID and
stream key with `version=0`, `last_event_id=NULL`, and `last_hash=NULL`.
Missing, ambiguous, renamed, altered, or advanced heads fail closed before any
0005-owned change, preventing authentication evidence from being detached and
silently restarted.

Revision 0004 cannot represent a null `code_hash`. Downgrade therefore also
preflights all challenge rows and fails before dropping the index, deleting the
empty chain head, or changing the schema if any null hash exists. It never
fabricates a hash to make the downgrade succeed. SQLite uses Alembic batch table
rebuilds for the nullable-column and named-check-constraint changes; the local
round-trip tests cover that compatibility path, while production PostgreSQL
uses native `ALTER TABLE` statements.

This revision creates no user, person binding, authentication identity, login
challenge, session, role assignment, or audit event. It does not activate any
legacy account. Its only existing-row update is the deterministic
`verification_mode` classification described above; it does not rewrite
challenge evidence. The production runtime now consumes these structures with
provider-scoped, versioned HMAC identity matching, provider-managed challenge
transitions and single-use refresh-token families. The stable-person-id,
dual-signature initial-administrator manifest and future identity-key rotation
procedure remain separately reviewed work; this migration itself still
activates no identity or account.

## Revision 0006: encrypted authentication idempotency ledger

Revision `20260830_0006` adds `auth_idempotency_operations`, the durable replay
boundary for the four formal authentication writes: SMS login, WeChat login,
session refresh, and session logout. The table contains no plaintext request or
response JSON. It stores a globally unique HMAC-derived idempotency-key hash, a
scope hash, a canonical request HMAC, and—only after a terminal result—an
AES-GCM ciphertext envelope with its 12-byte nonce, response digest, key
version, HTTP status, and completion time.

Database checks keep `pending`, `completed`, and `failed` states closed. A
pending row cannot contain any response evidence. A completed or failed row
must contain the entire encrypted response evidence set; partial envelopes,
invalid digest lengths, invalid key versions, completed non-2xx responses, and
failed responses outside 4xx/5xx are rejected. Expiration remains a timestamp
decision rather than a reusable `expired` state: the globally unique key hash
is never silently recycled into
a different authentication operation. Optional session and refresh-token
foreign keys preserve the exact token-family evidence without storing a raw
token.

The migration creates no operation row, user, identity, session, token, or
audit event and contacts no external system. A downgrade is allowed only while
the ledger is empty. Once any authentication operation exists, downgrade fails
before dropping an index or table so encrypted replay evidence cannot be
silently discarded. PostgreSQL takes an exclusive table lock before that
preflight so a stray writer cannot race the check and drop. Production rollback
must use the reviewed restore/forward fix procedure rather than deleting the
ledger.

## Revision 0007: one active session per device family

Revision `20260830_0007` adds the PostgreSQL/SQLite partial unique index
`uq_auth_sessions_active_device_family` over
`user_id + client_type + device_id` for rows whose `revoked_at IS NULL`. This
is the database invariant behind formal device-session replacement: a user may
retain revoked history and may have distinct Web and mini-program device
families, but cannot have two simultaneously unrevoked sessions for the same
client/device identity.

Before creating the index, the online migration explicitly groups existing
unrevoked sessions by the complete device-family key. Any duplicate fails the
upgrade before DDL with a `RuntimeError`; the migration never guesses a winning
session, changes `revoked_at`, or deletes authentication evidence. PostgreSQL
also locks `auth_sessions` for the migration transaction so authentication
writers cannot race the check and index creation. Offline PostgreSQL SQL emits
the equivalent reviewed `DO`-block gate.

Downgrade removes only the partial unique index and preserves every session
row. Because removing the invariant permits an older writer to create duplicate
active families, all authentication writers must be stopped for the complete
downgrade and application cutover window. The migration takes a PostgreSQL
exclusive DDL lock, but that transaction lock is not a substitute for the
operational stop-write gate. Before any later re-upgrade, rerun the duplicate
preflight and explicitly revoke reviewed obsolete sessions rather than editing
or deleting their history.

## Revision 0008: formal login rate-limit buckets

Revision `20260830_0008` creates `auth_login_rate_limit_buckets`. Rows contain
only operation/scope enums, an explicit HMAC hash version, a domain-separated
64-character scope HMAC, fixed-window coordinates, bounded counters, and
expiry/cleanup times. The unique window key includes the hash version. No raw
IP, mobile, WeChat code, openid, unionid, user, identity, audit, or idempotency
operation is inserted by the migration.

Runtime cleanup is deliberately later than the decision expiry: every bucket
is retained for one complete additional window, then deleted in an explicitly
bounded batch; PostgreSQL selection uses `FOR UPDATE SKIP LOCKED`. The local
entrypoint requires an exact database URL, timezone-aware cutoff and batch
size, and refuses PostgreSQL without an additional explicit acknowledgement.
Under the operation-level version guard, runtime also rejects any window-size
change while an old window is active; changing 60 seconds to 30 seconds cannot
create a second concurrent quota.

Downgrade is allowed only after formal login writers are stopped and the table
has been emptied by the safe cleanup path. A non-empty table fails before DDL.
Although these counters are disposable infrastructure rather than a business
ledger, silently dropping live protection during an application rollback would
create an unreviewed authentication gap; the stop-write/empty-table gate makes
that operational decision explicit.

## Revision 0009: formal inventory ledger foundation

Revision `20260830_0009` renames the v0.9 material prototype in place to
`legacy_v09_materials` and preserves every historical row. It creates empty
formal material, tracking policy, location/custody, account, lot/SN,
transaction/movement, balance and current-position tables, plus the independent
inventory ledger and audit chain heads. It never converts the legacy integer
balance table, replays historical documents, or creates an `opening` movement.

The migration therefore establishes schema and lock coordinates only. A
successful upgrade is not evidence that any regional or personal warehouse has
an opening balance. Downgrade is fail-closed once formal inventory facts or
dependent evidence exist; production rollback must preserve the immutable
ledger rather than silently restoring prototype semantics.

## Revision 0010: opening-stocktake evidence foundation

Revision `20260830_0010` renames the v0.9 stocktake prototype in place to
`legacy_v09_stocktake_tasks` and `legacy_v09_stocktake_items`, preserving its
rows, integer quantities, foreign keys and legacy index names. It creates 14
empty formal tables for regional opening tasks, exact owner/location scopes,
OAM control snapshots, freezes, cutoff snapshots, count rounds and serials,
differences, independent regional/headquarters reviews, opening postings and
final establishment facts. It adds six stocktake permissions but creates no
user, role assignment, task, count, review, transaction or establishment.

No v0.9 row or OAM control quantity is backfilled into formal inventory. SQLite
and PostgreSQL triggers seal scope/control/snapshot/freeze facts before the first
round, seal counts at submission, prevent differences, review items and posting
items from being appended after their downstream stage, require ordered regional
and headquarters approval before opening posting, and require every exact scope
to have an establishment before an opening task can become `posted`. Final facts
are immutable. The downgrade refuses to run after formal stocktake data, `opening` inventory
transactions, generic evidence references or mutated permission seeds exist.
An upgraded schema alone must never make PC or mini-program clients report that
opening is established.

## Revision 0011: opening count observations and submission seals

Revision `20260830_0011` adds immutable evidence for physical dimensions that
had no exact stock account at the opening cutoff. Raw material, lot and serial
identifiers retain their input type and may remain `pending_verification`; the
migration never creates an account, balance, movement, master-data mapping or
personal-stock fact. Verified dimensions that already had a cutoff account are
rejected and must use that snapshot account's count line instead.

Every scope records one immutable completion manifest. It must cover each
cutoff snapshot account with exactly one count line, including explicit zero
counts; a zero-count confirmation is allowed only when the scope has neither a
snapshot account nor any physical evidence. The completion freezes the actor's
person, role assignment, authorization version, role/scope snapshot and
authorization hash. One immutable round submission then reconciles all scope
completion totals before the mutable round header may transition to
`submitted`. Unresolved observations may be submitted as pending differences,
but are not thereby mapped, accepted or authorized for inventory posting.

SQLite and PostgreSQL triggers make count lines, count serials, observations,
scope completions and round submissions append-only, block post-completion
children, and validate observation-backed difference bindings. Downgrade
preserves 0010 only while every new evidence table is empty and no difference
references an observation; otherwise it fails before discarding evidence.

## Revision 0012: resolved observation serial uniqueness

Revision `20260831_0012` adds a partial unique index on
`stocktake_count_observations(round_id, serial_id)` for rows whose resolved
`serial_id` is present. This closes the remaining alias gap where one physical
serial could otherwise be preserved twice in the same round under different
raw identifier types. It does not resolve pending raw identifiers, rewrite an
observation, create serial master data or create inventory facts.

Before the index is created, PostgreSQL and SQLite upgrades fail closed when
duplicate resolved-serial evidence already exists. The error is fixed and
contains no round, serial or raw identifier value; PostgreSQL also holds a
write-blocking table lock across the check and index creation. Offline
PostgreSQL SQL includes the same explicit value-free preflight.

Downgrade requires an online evidence check and refuses to remove the index
while any observation contains a resolved `serial_id`. This prevents a
downgrade from silently weakening the immutable per-round physical-serial
boundary after the guard has become active.

## Revision 0013: identifier-aware resolved serial observations

Revision `20260831_0013` replaces the 0011 observation-insert validation guard
without changing any table or existing immutable fact. A resolved observation
whose `serial_identifier_type=serial_no` must match the referenced
`inventory_serials.serial_no`. A resolved QR observation must instead match
`inventory_serials.qr_code` and exactly one active
`qr_codes(object_type=serial, code=raw, object_id=serial_id)` mapping. An
`unknown` serial identifier may retain raw physical evidence but can never
carry a guessed `serial_id`.

Before replacing the guard, online PostgreSQL and SQLite upgrades scan every
existing resolved observation and fail with a fixed, value-free error if any
row violates the new contract. PostgreSQL holds a write-blocking table lock
across that preflight and trigger replacement; offline PostgreSQL SQL emits the
same lock and explicit preflight for review. The migration never maps a raw
identifier, rewrites evidence, creates a serial/QR master, or creates inventory.

Downgrade requires an online check and refuses while any observation has a
resolved `serial_id`. Restoring the identifier-blind 0011 guard after such
evidence exists could make valid QR evidence impossible to reproduce and would
weaken the explicit input-type contract, so rollback must use a reviewed
forward fix or database restore instead of deleting observations.

## Revision 0014: explicit round-sealing completion

Revision `20260831_0014` adds the nullable compatibility column
`stocktake_round_submissions.sealing_completion_id`, a unique index and an
insert guard that makes the value mandatory for every new submission and
requires it to identify a completion from the same task and round with the
same submitter, person, role assignment, authorization version and timestamp.
The compatibility column remains nullable at the raw schema level only because
SQLite cannot safely add a non-null column to an existing table without a
rebuild; the database trigger is the effective non-null/cross-table guard.

Existing submissions are backfilled only when exactly one historical
completion satisfies the full binding. A missing or ambiguous match stops the
upgrade before the column is added. No completion is selected by row order and
no timestamp collision is guessed. Because revision 0011 already makes round
submissions immutable, the migration holds the table lock, temporarily
suspends only the 0011 UPDATE guard for the deterministic backfill, and restores
that guard before creating the new index and insert validator. SQLite starts a
real immediate migration transaction and also recognizes a safely resumable
partial 0014 shape, so an interrupted local upgrade does not guess a completion
or fail by attempting to add the same column twice. Downgrade is refused once
any round submission exists.

## Revision 0015: immutable audit events

Revision `20260831_0015` first locks the chain-head and event tables and proves
that every persisted event is owned by exactly one seeded stream, is reachable
from that head at the declared version, reaches genesis without a cycle and
recomputes to the stored SHA-256 using the application's exact canonical JSON
document. Orphan, shared, broken or tampered evidence blocks the upgrade before
any immutability trigger is installed. PostgreSQL offline SQL refuses a
populated audit table because it cannot safely reproduce that serializer; a
populated database must use the normal online Alembic path.

Production completely refuses Alembic `--sql`/offline execution because an
offline file cannot prove the identity, ownership, role-membership and search
path of the connection that will eventually execute it. Production revisions
must use the online path and pass those live catalog checks before any DDL.

The revision then adds PostgreSQL row triggers rejecting every `UPDATE` and
`DELETE` plus a statement trigger rejecting `TRUNCATE`; SQLite rejects update
and delete. All three fixed chain heads are protected against deletion,
truncation, rewinding or arbitrary replacement: the only accepted update is a
single-version advance to an exact immutable event whose previous hash matches
the locked old head. All four guards are `ENABLE ALWAYS`; startup additionally
requires their exact table/function/event bindings, no `WHEN` clause, no
column-restricted `UPDATE OF`, and an `origin` session replication mode.

The same revision first revokes all table, sequence, explicit column and
function privileges
from the pre-provisioned `star_oam_api`, then installs an exact allowlist for
the production routes mounted at this revision. That includes
`SELECT/INSERT` on `audit_events`, `SELECT/UPDATE` on
`audit_chain_heads`, the narrowly required authentication/authorization table
operations, and no access to `alembic_version`. It intentionally grants no
write access to the internal opening-stocktake or inventory-posting tables;
activating those services requires a later reviewed ACL migration in the same
transaction as their routes. New tables, sequences and functions receive no
runtime default privileges. The API role is not an owner and production
startup independently rereads the complete PostgreSQL table, sequence and
function catalog evidence before accepting traffic. SQLite
revisions 0013 through 0015 keep their preflight and multi-step DDL in one
`BEGIN IMMEDIATE` transaction and safely resume the known partial trigger/index
shapes from older local attempts.

The audit-chain head remains mutable so the reviewed append service can advance
it atomically after inserting an event. Application replay additionally walks
the complete hash path from the locked stream head to prove that an expected
event is still a member of that stream; a self-consistent isolated row is not
sufficient.

Downgrade is refused while any audit event exists. Removing the database
immutability boundary after audit evidence has been recorded would silently
weaken all authorization, authentication and inventory audit streams.

## Revision 0016: independent count manifest and sealed review evidence

Revision `20260831_0016` separates the count-line/SN posting manifest from the
scope-completion/observation round manifest on the immutable round-submission
fact. The round transition now compares
`stocktake_round_submissions.count_manifest_sha256` with
`stocktake_rounds.count_manifest_sha256`; it never equates either value with
`round_manifest_sha256`.

The revision also adds append-only `stocktake_observation_dispositions` and
`stocktake_difference_set_completions`. A disposition can only handle an
original pending physical observation after round submission and before
review. A resolved disposition may bind only existing active material, lot and
serial masters using the original identifier type, cutoff tracking policy and
exact raw identifier; it cannot name or create an inventory account. Pending
and recount dispositions preserve the unresolved evidence without guessed
identifiers. Provincial managers are restricted to the observation owner
organization, while an existing-master resolution requires national admin
authority.

The difference completion is a mechanical post-child seal bound to the round
submission and its explicit sealing scope completion. Its actor, assignment,
authorization version and timestamp must match those two facts exactly, and
the database recomputes physical/control/pending counts, affected quantity and
contiguous numbering. No difference may be added after the seal. Either review
stage requires the sealed difference set and exactly one disposition for every
pending observation; regional and headquarters reviews remain independent.
OAM control-only differences remain reconciliation evidence and are never a
source of inventory.

Because the two historical manifests cannot be inferred from each other, the
upgrade takes a write-blocking preflight lock and refuses with a fixed,
value-free error when any round submission, review or difference evidence
already exists. It never guesses a backfill. PostgreSQL and SQLite install the
same immutable and sequencing boundaries. Every new PostgreSQL trigger
function explicitly revokes execution from `PUBLIC` and `star_oam_api`, and
the new tables receive no API privileges. Downgrade is online-only and is
permitted only while every affected evidence table remains empty.

## Revision 0017: persisted audit stream binding

Revision `20260831_0017` derives and persists `audit_events.stream_key` and
`stream_version` from the only canonical source: a complete backward walk of
the three fixed immutable heads. The online migration blocks audit writers,
recomputes every existing v1 hash with a frozen serializer, rejects every
orphan/shared/cyclic/tampered event, and assigns sequence numbers from genesis
without using timestamps or row order. The v1 hash document is unchanged;
`stream_version` is structural membership metadata and is not added to the
historical digest.

PostgreSQL enforces a fixed stream set, positive version, unique
`(stream_key, stream_version)` and a foreign key to the seeded head. An
additional forward-head trigger requires each single step to point to the event
with that exact stream and version. A `DEFERRABLE INITIALLY DEFERRED` constraint
trigger then refuses commit unless the matching head has consumed the inserted
version. The comparison is `head.version >= event.stream_version`, because one
business transaction may legitimately append several consecutive events to a
single stream; uniqueness plus mandatory one-step head binding proves every
intermediate event. Both new triggers are `ENABLE ALWAYS`, their functions are
not executable by `PUBLIC` or `star_oam_api`, and production startup verifies
their exact type/deferred state together with the new columns and constraints.

SQLite has no deferred user-defined constraint trigger. Its local/test schema
therefore enforces the same columns, fixed/unique coordinates and exact insert
and head-step validation, while the application writer verifies the final
event/head pair in the same transaction. This is intentionally not represented
as PostgreSQL-equivalent commit binding. A populated SQLite rebuild refuses to
silently disable an already-enabled `PRAGMA foreign_keys`; use the controlled
Alembic migration connection. Downgrade is online-only and refused after any
audit event exists.

## Revision 0018: append-only recount causality

Revision `20260831_0018` adds immutable `stocktake_recount_cases` and
`stocktake_recount_scope_assignments`. Every round above one must bind exactly
one case whose source is the immediately preceding submitted round, its exact
round submission and sealed difference completion, and an immutable review
whose explicit decision is `recount`. The old submitted round is never updated
to `superseded`; the successor edge is the supersession fact.

Each case persists the next round number, complete task-scope count and scope,
assignment, recount, request, idempotency and authorization hashes. Its
assignment rows must cover every original task scope exactly once and retain
the selected assignee's person, role assignment, authorization version and
scope snapshot for that round. PostgreSQL guards the case, assignment, task
advance and successor round with `ENABLE ALWAYS` triggers, a per-task partial
unique index for the sole `counting` round, and deferred constraint triggers
that refuse commit for gaps, branches, missing cases or incomplete scope
coverage. All trigger functions are non-executable by `PUBLIC` and
`star_oam_api`; both new tables intentionally receive zero API privileges.

Upgrade locks the stocktake graph and fails before DDL if any historical round
above one, `superseded` row, current-round drift, gap or branch already exists;
none of those prototype shapes is guessed or backfilled. Downgrade is online
only and refuses any case, assignment, successor round or current-round value
above one. SQLite implements the immediate row and transition checks, but it
cannot emulate a PostgreSQL deferred commit trigger and remains local/test
only.

This revision deliberately does not add `stocktake_tasks.current_round_id`.
Adding the circular task-to-round foreign key would force a broad SQLite table
rebuild that can discard historical triggers. The production-equivalent proof
is instead the immutable case edge plus `current_round_no`, the unique
`(task_id, round_no)` key and the deferred complete-graph trigger. A future
PostgreSQL-only pointer may be added only with a separately tested deferred
composite foreign key and a safe SQLite compatibility plan.

## Revision 0019: technician personal-location boundary

Revision `20260831_0019` closes the location-class seam left by the historical
custodian checks. A `technician` completion or recount scope assignment must
resolve through the exact `(task_id, scope_id)` to a stock location whose
current `location_type` is `personal`; merely placing a custodian on a region
location cannot grant technician counting authority.

Upgrade locks the two evidence tables plus scopes and locations, then fails
closed if either table already contains a technician fact outside that exact
personal-location graph. PostgreSQL installs two `ENABLE ALWAYS` insert
triggers backed by one non-executable function; SQLite installs equivalent
immediate insert guards for local migration tests. Existing immutable-update
guards remain authoritative after insertion. Downgrade removes only the 0019
function and triggers and does not weaken or rewrite revision 0018.

## Revision 0020: personal-location continuity after stocktake evidence

Revision `20260831_0020` makes the 0019 location-class rule a continuing
database invariant. Once an exact `(task_id, scope_id)` has a technician scope
completion or technician recount assignment, its referenced stock location
cannot be changed from `personal` to any other location type. Updates that keep
the location personal, non-type metadata updates, and unreferenced location
changes remain outside this guard.

Upgrade locks both evidence tables, scopes and locations, then repeats the full
technician graph scan before installing any object. Existing missing scope or
location edges and existing non-personal references fail closed. PostgreSQL
installs one unfiltered `BEFORE UPDATE` trigger as `ENABLE ALWAYS`; its function
compares old and new location types internally. On a real demotion it takes a
fixed-order `SHARE` lock on both evidence tables before checking references.
Those locks conflict with the `ROW EXCLUSIVE` lock held by INSERT statements,
so a location reclassification and new technician evidence cannot pass each
other concurrently. The function is non-executable by `PUBLIC` and
`star_oam_api`.

PostgreSQL truncates the two descriptive 0019 trigger identifiers to its
63-byte catalog limit. Production startup therefore verifies those two exact
catalog names, plus every 0018 and 0020 guard name, through a complete explicit
allowlist rather than a suffix query. SQLite retains and tests the original
untruncated 0019 identifiers.

SQLite supplies the equivalent immediate update check for local migration
tests; its serialized write model is not evidence of PostgreSQL concurrency
behavior. Downgrade removes only the 0020 trigger and function and leaves both
0018 and 0019 intact.

## Revision 0021: round-aware stocktake assignment guards

Revision `20260831_0021` replaces the frozen-first-round actor checks on count
lines, physical observations and scope completions with round-aware checks.
Round one still resolves the exact frozen task-scope assignee. Every later
round must instead resolve the actor through that round's immutable recount
scope assignment and revalidate the recorded role, authorization version and
scope at the fact time. The recount-case guard also accepts only the two
explicit terminal review paths: a regional `recount/reject` with no
headquarters review, or a headquarters `reject` after an earlier regional
`approve` by another person.

The migration rewrites no business row and adds no table. Upgrade locks and
scans the existing graph before replacing guards; assignment drift or an
invalid review edge blocks the migration. Downgrade is blocked once any
recount graph, later round or later-round fact exists. PostgreSQL uses the
production trigger/lock boundary. SQLite implements immediate, serialized
local validation only and cannot prove PostgreSQL deferred-constraint or
two-session concurrency behavior.

## Revision 0022: opening terminal graph and runtime ACL

Revision `20260831_0022` hardens the opening posting, posting items,
establishments and their inventory ledger facts as immutable terminal facts.
It changes opening-posting uniqueness from one per round to one per task and
adds PostgreSQL `ENABLE ALWAYS` row/TRUNCATE guards plus deferred commit-time
validation of the complete approved-round, posting, movement, establishment,
freeze release, task state, State/Outbox and audit graph. Existing incompatible
terminal facts fail the upgrade; no terminal fact is rewritten.

The same revision installs the exact minimum `star_oam_api` table, sequence and
column ACL required by the terminal opening-posting command/read boundary at
that revision. It grants no trigger control, arbitrary function execution,
`alembic_version` access or blanket future-table privilege. SQLite has no role
ACL, statement-level `TRUNCATE` trigger or deferred constraint trigger; it
checks local row immutability/uniqueness while the application performs an
in-transaction final graph re-proof. Those tests are compatibility evidence,
not a substitute for PostgreSQL 16 migration and concurrent-commit exercises.
Downgrade is online and fails closed once an opening posting, opening
transaction or establishment requires the 0022 graph.

## Revision 0023: verified recount-observation opening posting

Revision `20260831_0023` closes the narrow terminal gap for an exact physical
dimension first verified in a submitted recount round when no stock account
existed at the opening cutoff. An opening posting item keyed by
`difference_id` must bind that verified observation, its exact excess
difference and one opening movement as an item-by-item bijection. The stock
account is a separate canonical dimension: several eligible observations may
feed movement facts for the same canonical account, which is reused instead
of duplicated. The migration itself backfills or overwrites no balance.

PostgreSQL extends the 0022 deferred terminal graph and grants
`star_oam_api` only `INSERT` on `stock_accounts`, never `UPDATE` or `DELETE`.
A deferred account guard prevents that narrow grant from committing an orphan
empty account outside a valid observation-posting graph. SQLite replaces the
posting-item validation trigger for local sequential tests but has neither the
PostgreSQL role ACL nor an equivalent deferred orphan-account commit trigger;
the application service must retain its complete in-transaction re-proof.
Downgrade requires an online preflight and is blocked when observation-posting
facts depend on this path. Revision 0023 therefore remains a migration and
local-validation milestone, not proof of PostgreSQL 16 concurrency readiness
or production release approval.

## Revision 0024: complete formal opening workflow runtime ACL

Revision `20260831_0024` is ACL-only. Revision 0022 reset the production API
role to a terminal-posting manifest, while the subsequently mounted start,
count/seal, observation-disposition, two-stage-review and recount commands
also write their own append-only evidence. SQLite has no PostgreSQL roles and
therefore could not expose that mismatch.

PostgreSQL first revokes all API/Public table, sequence, explicit column and
function privileges and then re-applies a self-contained minimum manifest.
Compared with 0023, `qr_codes` gains `SELECT` only; the 18 exact opening
workflow fact tables gain `INSERT` only; `stocktake_rounds` gains column-level
update of `status`, submitter/time, count manifest and `updated_at`; and
`stocktake_tasks` additionally gains column-level update of `submitted_at` and
`current_round_no`. The revision grants no table-level update on either state
table, no delete/truncate/trigger/reference privilege on opening facts, no QR
or other master-data write, no sequence access and no public-function execute.

Downgrade performs the same full revoke and restores the exact 0023 manifest,
including its guarded `stock_accounts` insert but none of the 0024 workflow
expansion. SQLite upgrade and downgrade are deliberate no-ops. PostgreSQL 16
must still execute the API-role end-to-end workflow and negative ACL cases in
an isolated pre-production environment before release; offline SQL is review
evidence, not concurrency or live-role proof.

## Revision 0025: stocktake scope asset and physical-location region guard

Revision `20260831_0025` makes the task region a database-enforced boundary for
every formal stocktake scope. The task's `region_org_id` and the scope's asset
`owner_org_id` must both resolve to active `region_company` organizations. The
asset owner may equal the task region or be a descendant of it, but every node
from the owner through that region must be active. The scope target location
must exist, be active and have type `region` or `personal`. Its entire physical
parent-location chain must be active, unbroken and acyclic; every location in
that chain must independently resolve its `owner_org_id` through an active
organization path to the same task region. A sibling/cross-region asset owner,
cross-region target or parent location, inactive location/organization path,
missing location/parent, unsupported target type, or recursive organization or
location cycle is rejected. The task region's own ancestors are outside each
organization-tree decision and are not traversed after the region is reached,
matching the application authorization semantics.

Upgrade takes an exclusive migration lock over organizations, stock locations,
tasks and scopes and runs the same recursive pollution preflight before
installing any object. It never reparents, activates, deletes or guesses a
historical row. PostgreSQL installs an unfiltered `BEFORE INSERT` trigger as
`ENABLE ALWAYS`, locks the exact task, asset-organization path, physical
location path and each location-owner organization path while checking them,
and revokes direct execute on the trigger function from `PUBLIC` and
`star_oam_api`. Production startup queries every non-internal trigger on
`stocktake_scopes` and requires the exact 0010 immutable/sealing guards plus the
0025 dual asset/location guard; renamed, disabled, weakened, extra or duplicate
bindings fail closed. The retained function and trigger names keep the exact
startup catalog stable. Revision 0025 does not change the self-contained 0024
runtime ACL manifest.

SQLite supplies the equivalent bounded recursive asset, location and
location-owner INSERT checks plus online upgrade preflight for local sequential
tests; it is not PostgreSQL concurrency or role evidence. Downgrade is
permitted only while `stocktake_scopes` is empty, then removes only the 0025
trigger/function. A database with established scope facts must not silently
lose this authorization invariant.

For an intentional autogenerate comparison only, set
`ALEMBIC_LOAD_MODEL_METADATA=1` and provide all application settings required to
import the ORM.  Generated output must still be reviewed and made explicit.
