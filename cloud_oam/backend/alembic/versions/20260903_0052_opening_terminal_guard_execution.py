"""Repair opening terminal trigger execution and legal task transitions.

Revision ID: 20260903_0052
Revises: 20260903_0051
Create Date: 2026-09-03

The 0022 deferred opening-terminal caller was SECURITY INVOKER while its
graph helper had a deliberately closed ACL.  Valid API transactions therefore
could not execute the nested graph proof.  Its stocktake-task branch also
treated the recount pointer and round-submission timestamp as permanently
immutable, blocking the two legitimate transitions that write those fields.
The 0023 observation-account caller had the same nested-call problem; simply
making it SECURITY DEFINER without changing its principal test would instead
silently bypass its API-only proof.

This forward repair locks every table read by the opening graph boundary and
fail-closed verifies the exact function identities, shapes, owners, ACLs,
search paths, body hashes, security modes and twenty unique trigger
bindings.  It then:

* admits ``recount_required -> counting`` only when ``current_round_no``
  advances by exactly one;
* admits ``counting -> submitted`` changes to ``submitted_at`` only when the
  current submitted round and its immutable submission agree on task, round,
  timestamp and manifests;
* restricts subsequent nonterminal updates to the formal region-review,
  headquarters-review and recount transitions, each backed by its unique
  review/round facts and exact state-transition event;
* seals count, round, review, disposition and opening-owned event facts with
  ``ALWAYS`` deferred graph-closure triggers so a partial immutable graph
  cannot be committed;
* validates each runtime audit event canonically in constant time while
  proving the complete inventory audit chain once under the migration lock;
* makes the 0022 trigger caller SECURITY DEFINER while leaving its 0023 graph
  helper SECURITY INVOKER with a closed ACL; and
* makes the 0023 account caller SECURITY DEFINER while changing its API
  principal check from ``current_user`` to ``session_user::text``.

No business row or table privilege is changed.  Downgrade restores the exact
0051 definitions only when no opening task or reserved opening evidence
remains; even closed history is rejected because 0051 cannot preserve the
0052 event-ownership guarantees.
SQLite is an explicit schema no-op for local migration-chain compatibility;
it is not PostgreSQL security evidence.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0052"
down_revision: Union[str, Sequence[str], None] = "20260903_0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
OAM_RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
PREVIOUS_SCHEMA_REVISION = "20260903_0051"
FIXED_SEARCH_PATH = "search_path=pg_catalog, public"

GRAPH_FUNCTION = "rsc_opening_terminal_graph_complete_0022"
COMMIT_FUNCTION = "rsc_require_opening_terminal_graph_0022"
ACCOUNT_FUNCTION = "rsc_require_opening_observation_account_0023"
ACTOR_ASSIGNMENT_FUNCTION = "rsc_stocktake_actor_assignment_valid_0011"
REVIEW_COMPLETION_FUNCTION = "rsc_require_stocktake_difference_completion_0016"
REVIEW_IMMUTABLE_FUNCTION = "rsc_block_stocktake_review_fact_mutation_0016"
START_GRAPH_FUNCTION = "rsc_opening_start_graph_complete_0052"
ROUND_SUBMISSION_FUNCTION = "rsc_opening_round_submission_complete_0052"
SCOPE_COMPLETION_FUNCTION = "rsc_opening_scope_count_complete_0052"
REVIEW_GRAPH_FUNCTION = "rsc_opening_review_complete_0052"
RECOUNT_GRAPH_FUNCTION = "rsc_opening_recount_complete_0052"
DISPOSITION_GRAPH_FUNCTION = "rsc_opening_observation_disposition_complete_0052"
TERMINAL_GRAPH_FUNCTION = "rsc_opening_terminal_side_effects_complete_0052"
CANONICAL_JSON_FUNCTION = "rsc_canonical_reconciliation_json_0026"
RECONCILIATION_EVENT_KEY_FUNCTION = "rsc_reconciliation_event_key_0026"
RECONCILIATION_EFFECT_FUNCTION = "rsc_guard_reconciliation_effect_0026"

GRAPH_SIGNATURE = f"public.{GRAPH_FUNCTION}(uuid, uuid)"
COMMIT_SIGNATURE = f"public.{COMMIT_FUNCTION}()"
ACCOUNT_SIGNATURE = f"public.{ACCOUNT_FUNCTION}()"
ACTOR_ASSIGNMENT_SIGNATURE = (
    f"public.{ACTOR_ASSIGNMENT_FUNCTION}(text, uuid, uuid, bigint, "
    "timestamptz, text, text, text)"
)
REVIEW_COMPLETION_SIGNATURE = f"public.{REVIEW_COMPLETION_FUNCTION}()"
REVIEW_IMMUTABLE_SIGNATURE = f"public.{REVIEW_IMMUTABLE_FUNCTION}()"
START_GRAPH_SIGNATURE = f"public.{START_GRAPH_FUNCTION}(uuid, boolean)"
ROUND_SUBMISSION_SIGNATURE = (
    f"public.{ROUND_SUBMISSION_FUNCTION}(uuid, uuid, boolean)"
)
SCOPE_COMPLETION_SIGNATURE = (
    f"public.{SCOPE_COMPLETION_FUNCTION}(uuid, uuid, uuid, boolean)"
)
REVIEW_GRAPH_SIGNATURE = f"public.{REVIEW_GRAPH_FUNCTION}(uuid, boolean)"
RECOUNT_GRAPH_SIGNATURE = f"public.{RECOUNT_GRAPH_FUNCTION}(uuid, boolean)"
DISPOSITION_GRAPH_SIGNATURE = (
    f"public.{DISPOSITION_GRAPH_FUNCTION}(uuid, boolean)"
)
TERMINAL_GRAPH_SIGNATURE = f"public.{TERMINAL_GRAPH_FUNCTION}(uuid, boolean)"
CANONICAL_JSON_SIGNATURE = f"public.{CANONICAL_JSON_FUNCTION}(jsonb)"
RECONCILIATION_EVENT_KEY_SIGNATURE = (
    f"public.{RECONCILIATION_EVENT_KEY_FUNCTION}(text, text, text)"
)
RECONCILIATION_EFFECT_SIGNATURE = (
    f"public.{RECONCILIATION_EFFECT_FUNCTION}()"
)
NEW_HELPER_SIGNATURES = (
    START_GRAPH_SIGNATURE,
    ROUND_SUBMISSION_SIGNATURE,
    SCOPE_COMPLETION_SIGNATURE,
    REVIEW_GRAPH_SIGNATURE,
    RECOUNT_GRAPH_SIGNATURE,
    DISPOSITION_GRAPH_SIGNATURE,
    TERMINAL_GRAPH_SIGNATURE,
)
PERSISTENT_FUNCTION_SIGNATURES = (
    ACTOR_ASSIGNMENT_SIGNATURE,
    REVIEW_COMPLETION_SIGNATURE,
    REVIEW_IMMUTABLE_SIGNATURE,
    GRAPH_SIGNATURE,
    COMMIT_SIGNATURE,
    ACCOUNT_SIGNATURE,
)
CALLER_SIGNATURES = (COMMIT_SIGNATURE, ACCOUNT_SIGNATURE)

GRAPH_BODY_SHA256 = (
    "1eaf4e9bae4bac31f821ba1470d70059d9249d5459b7463e89683b03f0dc73d2"
)
LEGACY_COMMIT_BODY_SHA256 = (
    "7f7e740c0faffaa61910adddef31fc7aaa469972ca5b264aa842e05ff298acba"
)
FIXED_COMMIT_BODY_SHA256 = (
    "a74e28ac1b09a7f92ef0179b93e3357d23fd1d7afb7d6c859b1e333ff3699a84"
)
LEGACY_ACCOUNT_BODY_SHA256 = (
    "2e52965c8086e01fc242e1ddb0205eb961304faf588b04f28182588cbdb4728a"
)
FIXED_ACCOUNT_BODY_SHA256 = (
    "c0079cfdaf15a4e9f9b66d76596828c0901b322d32ff4ea63fbbadc3acff163b"
)
ACTOR_ASSIGNMENT_BODY_SHA256 = (
    "09720a289e550a66f2ea400fdb0541d1646916d661538af0d2706f9fe5c326d1"
)
REVIEW_COMPLETION_BODY_SHA256 = (
    "13dbc1a56efe49c1cd7065b65374c05f824f779a7e7d5c7f1434dd5047e973ac"
)
REVIEW_IMMUTABLE_BODY_SHA256 = (
    "abb2ae9087445ec056fdd8c7e3b5dae2a598af6f0fc1e61d469646e8aa2d1726"
)
CANONICAL_JSON_BODY_SHA256 = (
    "35a956052a13a94d1c6b57f252273f46fa806b2e9c596531205a148114b7dc53"
)
RECONCILIATION_EVENT_KEY_BODY_SHA256 = (
    "9ec2f326f040fa1cd223e570b83ae6d6ff3dad7eb81f56e55e3342b45d9e5446"
)
RECONCILIATION_EFFECT_BODY_SHA256 = (
    "6e7e845ac518f378139b6f79218da0f08a55f9398b63429686af47f8f10024fe"
)

# 0052 deliberately delegates the exact reconciliation event graph to 0026.
# Pin the complete transitive execution boundary rather than trusting a name.
INHERITED_RECONCILIATION_FUNCTION_CATALOG_FIELDS = (
    "signature",
    "name",
    "return_type",
    "language",
    "volatility",
    "argument_types",
    "argument_names",
    "security_definer",
    "is_strict",
    "search_path",
    "body_sha256",
    "api_execute",
)
INHERITED_RECONCILIATION_FUNCTION_CATALOG = (
    (
        CANONICAL_JSON_SIGNATURE,
        CANONICAL_JSON_FUNCTION,
        "text",
        "plpgsql",
        "i",
        ("jsonb",),
        ("document",),
        False,
        True,
        (FIXED_SEARCH_PATH,),
        CANONICAL_JSON_BODY_SHA256,
        True,
    ),
    (
        RECONCILIATION_EVENT_KEY_SIGNATURE,
        RECONCILIATION_EVENT_KEY_FUNCTION,
        "text",
        "sql",
        "i",
        ("text", "text", "text"),
        ("operation_name", "anchor", "suffix"),
        False,
        True,
        (FIXED_SEARCH_PATH,),
        RECONCILIATION_EVENT_KEY_BODY_SHA256,
        True,
    ),
    (
        RECONCILIATION_EFFECT_SIGNATURE,
        RECONCILIATION_EFFECT_FUNCTION,
        "trigger",
        "plpgsql",
        "v",
        (),
        (),
        False,
        False,
        (FIXED_SEARCH_PATH,),
        RECONCILIATION_EFFECT_BODY_SHA256,
        False,
    ),
)
INHERITED_RECONCILIATION_TRIGGER_CATALOG = (
    (
        "state_transition_events",
        "trg_reconciliation_state_effect_guard_0026",
        RECONCILIATION_EFFECT_SIGNATURE,
        31,
    ),
    (
        "outbox_events",
        "trg_reconciliation_outbox_effect_guard_0026",
        RECONCILIATION_EFFECT_SIGNATURE,
        31,
    ),
    (
        "audit_events",
        "trg_reconciliation_audit_effect_guard_0026",
        RECONCILIATION_EFFECT_SIGNATURE,
        31,
    ),
    (
        "state_transition_events",
        "trg_reconciliation_state_effect_no_truncate_0026",
        RECONCILIATION_EFFECT_SIGNATURE,
        34,
    ),
    (
        "outbox_events",
        "trg_reconciliation_outbox_effect_no_truncate_0026",
        RECONCILIATION_EFFECT_SIGNATURE,
        34,
    ),
    (
        "audit_events",
        "trg_reconciliation_audit_effect_no_truncate_0026",
        RECONCILIATION_EFFECT_SIGNATURE,
        34,
    ),
)

LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT = (
    "    IF current_user <> 'star_oam_api' THEN"
)
FIXED_ACCOUNT_PRINCIPAL_FRAGMENT = (
    "    IF session_user::text <> 'star_oam_api' THEN"
)

LEGACY_TASK_BRANCH = """    ELSIF TG_TABLE_NAME = 'stocktake_tasks' THEN
        IF OLD.task_type <> 'opening' AND NEW.task_type <> 'opening' THEN
            RETURN NEW;
        END IF;
        IF OLD.id IS DISTINCT FROM NEW.id
           OR OLD.task_no IS DISTINCT FROM NEW.task_no
           OR OLD.task_type IS DISTINCT FROM NEW.task_type
           OR OLD.region_org_id IS DISTINCT FROM NEW.region_org_id
           OR OLD.blind_count IS DISTINCT FROM NEW.blind_count
           OR OLD.cutoff_ledger_cursor IS DISTINCT FROM
              NEW.cutoff_ledger_cursor
           OR OLD.cutoff_at IS DISTINCT FROM NEW.cutoff_at
           OR OLD.scope_manifest_sha256 IS DISTINCT FROM
              NEW.scope_manifest_sha256
           OR OLD.snapshot_manifest_sha256 IS DISTINCT FROM
              NEW.snapshot_manifest_sha256
           OR OLD.control_source_system_id IS DISTINCT FROM
              NEW.control_source_system_id
           OR OLD.control_sync_run_id IS DISTINCT FROM
              NEW.control_sync_run_id
           OR OLD.control_snapshot_at IS DISTINCT FROM
              NEW.control_snapshot_at
           OR OLD.control_manifest_sha256 IS DISTINCT FROM
              NEW.control_manifest_sha256
           OR OLD.current_round_no IS DISTINCT FROM NEW.current_round_no
           OR OLD.created_by_user_id IS DISTINCT FROM
              NEW.created_by_user_id
           OR OLD.deadline IS DISTINCT FROM NEW.deadline
           OR OLD.issued_at IS DISTINCT FROM NEW.issued_at
           OR OLD.frozen_at IS DISTINCT FROM NEW.frozen_at
           OR OLD.submitted_at IS DISTINCT FROM NEW.submitted_at
           OR OLD.cancelled_at IS DISTINCT FROM NEW.cancelled_at
           OR OLD.note IS DISTINCT FROM NEW.note
           OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'opening terminal task binding is immutable'
                USING ERRCODE = '55000';
        END IF;
        IF OLD.status = 'approved' AND NEW.status = 'posted' THEN
            IF OLD.task_type <> 'opening'
               OR NEW.task_type <> 'opening'
               OR OLD.posted_at IS NOT NULL
               OR NEW.posted_at IS NULL
               OR NEW.closed_at IS NOT NULL
               OR NEW.version <> OLD.version + 1
               OR NEW.updated_at IS DISTINCT FROM NEW.posted_at THEN
                RAISE EXCEPTION 'opening task post transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.status = 'posted' AND NEW.status = 'closed' THEN
            IF OLD.task_type <> 'opening'
               OR NEW.task_type <> 'opening'
               OR OLD.posted_at IS NULL
               OR NEW.posted_at IS DISTINCT FROM OLD.posted_at
               OR OLD.closed_at IS NOT NULL
               OR NEW.closed_at IS NULL
               OR NEW.closed_at <= NEW.posted_at
               OR NEW.version <> OLD.version + 1
               OR NEW.updated_at IS DISTINCT FROM NEW.closed_at THEN
                RAISE EXCEPTION 'opening task close transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.status IN ('posted', 'closed')
              OR NEW.status IN ('posted', 'closed') THEN
            RAISE EXCEPTION 'opening terminal task facts are immutable'
                USING ERRCODE = '55000';
        ELSE
            RETURN NEW;
        END IF;
        SELECT task.status
          INTO current_task_status
          FROM public.stocktake_tasks AS task
         WHERE task.id = NEW.id;
        IF current_task_status IS DISTINCT FROM NEW.status THEN
            RAISE EXCEPTION
                'opening post and close require independent transactions'
                USING ERRCODE = '23514';
        END IF;
        opening_task_id := NEW.id;
        SELECT posting.inventory_transaction_id
          INTO opening_transaction_id
          FROM public.stocktake_postings AS posting
         WHERE posting.task_id = NEW.id
           AND posting.posting_kind = 'opening';
"""


def _opening_review_item_rules_sql(
    review_alias: str,
    round_alias: str,
    *,
    historical: bool,
) -> str:
    """Return the persisted opening-review item decision matrix.

    The review manifest has no SQL-side canonical hash helper yet, so the
    migration pins its digest shape separately and proves the complete
    persisted per-difference semantics here instead of trusting the digest.
    """

    expected_top_level_decision = f"""CASE {review_alias}.decision
        WHEN 'approve' THEN 'accept_for_posting'
        WHEN 'recount' THEN 'recount'
        WHEN 'reject' THEN 'reject'
        ELSE NULL
    END"""
    decision_manifest = _review_decision_manifest_sha256_sql(
        f"{round_alias}.task_id",
        round_alias,
        review_alias,
    )
    side_effects = _review_side_effect_proof_sql(
        f"{round_alias}.task_id",
        round_alias,
        review_alias,
        historical=historical,
    )
    return f"""(
        (
            {review_alias}.decision = 'approve'
            OR pg_catalog.btrim({review_alias}.comment) <> ''
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_review_items AS decision_item
              JOIN public.stocktake_differences AS decision_difference
                ON decision_difference.id = decision_item.difference_id
               AND decision_difference.task_id = decision_item.task_id
               AND decision_difference.round_id = decision_item.round_id
              LEFT JOIN public.stocktake_count_observations
                   AS decision_observation
                ON decision_observation.id =
                   decision_difference.observed_line_id
               AND decision_observation.task_id =
                   decision_difference.task_id
               AND decision_observation.round_id =
                   decision_difference.round_id
               AND decision_observation.scope_id =
                   decision_difference.scope_id
              LEFT JOIN public.stocktake_observation_dispositions
                   AS decision_disposition
                ON decision_disposition.observation_id =
                   decision_observation.id
               AND decision_disposition.task_id =
                   decision_difference.task_id
               AND decision_disposition.round_id =
                   decision_difference.round_id
               AND decision_disposition.scope_id =
                   decision_difference.scope_id
               AND decision_disposition.decided_at <=
                   {review_alias}.reviewed_at
             WHERE decision_item.review_id = {review_alias}.id
               AND (
                   decision_item.decision IS DISTINCT FROM CASE
                       WHEN decision_difference.difference_type =
                            'control_unassigned'
                       THEN 'pending_verification'
                       WHEN decision_difference.observed_line_id IS NULL
                       THEN {expected_top_level_decision}
                       WHEN {round_alias}.round_no > 1
                            AND {round_alias}.round_type = 'recount'
                            AND decision_observation.verification_status =
                                'verified'
                            AND decision_disposition.id IS NULL
                       THEN {expected_top_level_decision}
                       WHEN {review_alias}.review_stage = 'region'
                            AND decision_observation.verification_status =
                                'pending_verification'
                            AND decision_disposition.disposition =
                                'pending_verification'
                            AND {review_alias}.decision IN (
                                'recount',
                                'reject'
                            )
                       THEN 'pending_verification'
                       WHEN {review_alias}.review_stage = 'region'
                            AND {review_alias}.decision = 'recount'
                            AND (
                                (
                                    decision_observation.verification_status =
                                        'pending_verification'
                                    AND decision_disposition.disposition IN (
                                        'requires_recount',
                                        'resolved_existing_master'
                                    )
                                )
                                OR (
                                    decision_observation.verification_status =
                                        'verified'
                                    AND decision_disposition.id IS NULL
                                )
                            )
                       THEN 'recount'
                       ELSE NULL
                   END
                   OR (
                       decision_item.decision = 'pending_verification'
                       AND pg_catalog.btrim(decision_item.comment) = ''
                   )
               )
        )
        AND {review_alias}.decision_manifest_sha256 =
            {decision_manifest}
        AND {side_effects}
    )"""


def _historical_review_actor_sql(
    review_alias: str,
    task_alias: str,
    *,
    headquarters: bool,
) -> str:
    """Prove authorization at review time without rejecting later rotations."""

    role_code = "admin" if headquarters else "provincial_manager"
    scope_type = "national" if headquarters else "organization"
    scope_id = "'*'" if headquarters else f"{task_alias}.region_org_id::text"
    return f"""EXISTS (
        SELECT 1
          FROM public.role_assignments AS historical_assignment
          JOIN public.roles AS historical_role
            ON historical_role.id = historical_assignment.role_id
          JOIN public.users AS historical_user
            ON historical_user.id = historical_assignment.user_id
         WHERE historical_assignment.id =
               {review_alias}.reviewer_role_assignment_id
           AND historical_assignment.user_id =
               {review_alias}.reviewer_user_id
           AND historical_user.person_id = {review_alias}.reviewer_person_id
           AND historical_user.authorization_version >=
               {review_alias}.authorization_version
           AND historical_assignment.status IN (
               'active',
               'expired',
               'revoked'
           )
           AND historical_assignment.valid_from <= {review_alias}.reviewed_at
           AND (
               historical_assignment.valid_to IS NULL
               OR {review_alias}.reviewed_at < historical_assignment.valid_to
           )
           AND (
               historical_assignment.revoked_at IS NULL
               OR {review_alias}.reviewed_at < historical_assignment.revoked_at
           )
           AND NOT historical_role.is_external
           AND historical_role.code = '{role_code}'
           AND historical_assignment.scope_type = '{scope_type}'
           AND historical_assignment.scope_id = {scope_id}
    )"""


def _historical_review_item_coverage_sql(
    review_alias: str,
    task_alias: str,
    round_alias: str,
) -> str:
    """Prove one persisted review covers exactly its round differences."""

    return f"""(
        NOT EXISTS (
            SELECT 1
              FROM public.stocktake_differences AS covered_difference
             WHERE covered_difference.task_id = {task_alias}.id
               AND covered_difference.round_id = {round_alias}.id
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_review_items AS covered_item
                    WHERE covered_item.review_id = {review_alias}.id
                      AND covered_item.difference_id = covered_difference.id
                      AND covered_item.task_id = {task_alias}.id
                      AND covered_item.round_id = {round_alias}.id
                      AND covered_item.created_at =
                          {review_alias}.reviewed_at
               )
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_review_items AS covered_item
              LEFT JOIN public.stocktake_differences AS covered_difference
                ON covered_difference.id = covered_item.difference_id
               AND covered_difference.task_id = {task_alias}.id
               AND covered_difference.round_id = {round_alias}.id
             WHERE covered_item.review_id = {review_alias}.id
               AND covered_difference.id IS NULL
        )
    )"""


def _review_item_coverage_by_id_sql(
    review_alias: str,
    task_id_sql: str,
    round_alias: str,
) -> str:
    """Prove exact one-to-one review item coverage for an arbitrary task id."""

    return f"""(
        NOT EXISTS (
            SELECT 1
              FROM public.stocktake_differences AS covered_difference
             WHERE covered_difference.task_id = {task_id_sql}
               AND covered_difference.round_id = {round_alias}.id
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_review_items AS covered_item
                    WHERE covered_item.review_id = {review_alias}.id
                      AND covered_item.difference_id = covered_difference.id
                      AND covered_item.task_id = {task_id_sql}
                      AND covered_item.round_id = {round_alias}.id
                      AND covered_item.created_at =
                          {review_alias}.reviewed_at
               )
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_review_items AS covered_item
              LEFT JOIN public.stocktake_differences AS covered_difference
                ON covered_difference.id = covered_item.difference_id
               AND covered_difference.task_id = {task_id_sql}
               AND covered_difference.round_id = {round_alias}.id
             WHERE covered_item.review_id = {review_alias}.id
               AND covered_difference.id IS NULL
        )
    )"""


def _round_submission_event_metadata_sql(
    round_alias: str,
    task_id_sql: str,
) -> str:
    return f"""CASE
        WHEN {round_alias}.round_no = 1
             AND {round_alias}.round_type = 'initial'
        THEN pg_catalog.jsonb_build_object(
            'round_id',
            {round_alias}.id::text
        )
        WHEN {round_alias}.round_no > 1
             AND {round_alias}.round_type = 'recount'
        THEN pg_catalog.jsonb_build_object(
            'recount_case_id',
            {round_alias}.recount_case_id::text,
            'round_id',
            {round_alias}.id::text,
            'round_no',
            {round_alias}.round_no,
            'round_type',
            {round_alias}.round_type,
            'task_id',
            {task_id_sql}::text
        )
        ELSE NULL
    END"""


def _review_event_metadata_sql(
    review_alias: str,
    round_alias: str,
) -> str:
    return f"""pg_catalog.jsonb_build_object(
        'decision',
        {review_alias}.decision,
        'decision_manifest_sha256',
        {review_alias}.decision_manifest_sha256,
        'pending_control_count',
        (
            SELECT pg_catalog.count(*)
              FROM public.stocktake_differences AS control_difference
             WHERE control_difference.task_id = {review_alias}.task_id
               AND control_difference.round_id = {review_alias}.round_id
               AND control_difference.difference_type = 'control_unassigned'
        ),
        'review_id',
        {review_alias}.id::text,
        'round_id',
        {round_alias}.id::text,
        'stage',
        {review_alias}.review_stage
    )"""


def _opening_review_event_key_sql(kind: str, review_id_sql: str) -> str:
    if kind not in {"state", "outbox"}:
        raise ValueError("unsupported opening review event kind")
    return f"""'opening-review-{kind}-' || pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                'cloud_oam.opening_stocktake.review.{kind}.v1',
                'UTF8'
            ) || pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to(({review_id_sql})::text, 'UTF8')
        ),
        'hex'
    )"""


def _review_side_effect_proof_sql(
    task_id_sql: str,
    round_alias: str,
    review_alias: str,
    *,
    historical: bool,
) -> str:
    metadata = _review_event_metadata_sql(review_alias, round_alias)
    state_key = _opening_review_event_key_sql("state", f"{review_alias}.id")
    outbox_key = _opening_review_event_key_sql("outbox", f"{review_alias}.id")
    fresh_outbox = "TRUE" if historical else f"""(
        review_outbox.status = 'pending'
        AND review_outbox.attempts = 0
        AND review_outbox.locked_at IS NULL
        AND review_outbox.locked_by IS NULL
        AND review_outbox.published_at IS NULL
        AND review_outbox.last_error IS NULL
        AND review_outbox.updated_at = review_outbox.created_at
    )"""
    return f"""(
        (SELECT pg_catalog.count(*)
           FROM public.state_transition_events AS review_state
          WHERE review_state.aggregate_type = 'stocktake_task'
            AND review_state.aggregate_id = ({task_id_sql})::text
            AND review_state.from_status = CASE {review_alias}.review_stage
                WHEN 'region' THEN 'submitted'
                WHEN 'headquarters' THEN 'hq_review'
                ELSE NULL
            END
            AND review_state.to_status = CASE
                WHEN {review_alias}.review_stage = 'region'
                     AND {review_alias}.decision = 'approve'
                THEN 'hq_review'
                WHEN {review_alias}.review_stage = 'headquarters'
                     AND {review_alias}.decision = 'approve'
                THEN 'approved'
                ELSE 'recount_required'
            END
            AND review_state.reason =
                'opening_' || {review_alias}.review_stage ||
                '_review_' || {review_alias}.decision
            AND review_state.actor_id = {review_alias}.reviewer_user_id
            AND review_state.idempotency_key = {state_key}
            AND review_state.occurred_at = {review_alias}.reviewed_at
            AND review_state.created_at = {review_alias}.reviewed_at
            AND review_state.metadata_jsonb = {metadata}
        ) = 1
        AND NOT EXISTS (
            SELECT 1
              FROM public.state_transition_events AS review_candidate
             WHERE review_candidate.aggregate_type = 'stocktake_task'
               AND review_candidate.aggregate_id = ({task_id_sql})::text
               AND review_candidate.reason IN (
                   'opening_' || {review_alias}.review_stage ||
                       '_review_approve',
                   'opening_' || {review_alias}.review_stage ||
                       '_review_recount',
                   'opening_' || {review_alias}.review_stage ||
                       '_review_reject'
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_reviews AS candidate_owner
                    WHERE candidate_owner.task_id = ({task_id_sql})
                      AND candidate_owner.id::text =
                          review_candidate.metadata_jsonb ->> 'review_id'
                      AND candidate_owner.round_id::text =
                          review_candidate.metadata_jsonb ->> 'round_id'
                      AND candidate_owner.review_stage =
                          review_candidate.metadata_jsonb ->> 'stage'
                      AND candidate_owner.decision =
                          review_candidate.metadata_jsonb ->> 'decision'
                      AND review_candidate.reason =
                          'opening_' || candidate_owner.review_stage ||
                          '_review_' || candidate_owner.decision
                      AND review_candidate.idempotency_key =
                          {_opening_review_event_key_sql('state', 'candidate_owner.id')}
               )
        )
        AND (SELECT pg_catalog.count(*)
               FROM public.outbox_events AS review_outbox
              WHERE review_outbox.event_type =
                    'stocktake.opening.' || {review_alias}.review_stage ||
                    '_reviewed'
                AND review_outbox.aggregate_type = 'stocktake_task'
                AND review_outbox.aggregate_id = ({task_id_sql})::text
                AND review_outbox.idempotency_key = {outbox_key}
                AND review_outbox.payload_jsonb = {metadata}
                AND review_outbox.available_at = {review_alias}.reviewed_at
                AND review_outbox.created_at = {review_alias}.reviewed_at
                AND review_outbox.updated_at >= {review_alias}.reviewed_at
                AND (
                    review_outbox.locked_at IS NULL
                    OR review_outbox.locked_at >= {review_alias}.reviewed_at
                )
                AND (
                    review_outbox.published_at IS NULL
                    OR review_outbox.published_at >= {review_alias}.reviewed_at
                )
                AND {fresh_outbox}
        ) = 1
        AND NOT EXISTS (
            SELECT 1
              FROM public.outbox_events AS review_candidate
             WHERE review_candidate.aggregate_type = 'stocktake_task'
               AND review_candidate.aggregate_id = ({task_id_sql})::text
               AND review_candidate.event_type =
                   'stocktake.opening.' || {review_alias}.review_stage ||
                   '_reviewed'
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_reviews AS candidate_owner
                    WHERE candidate_owner.task_id = ({task_id_sql})
                      AND candidate_owner.id::text =
                          review_candidate.payload_jsonb ->> 'review_id'
                      AND candidate_owner.round_id::text =
                          review_candidate.payload_jsonb ->> 'round_id'
                      AND candidate_owner.review_stage =
                          review_candidate.payload_jsonb ->> 'stage'
                      AND candidate_owner.decision =
                          review_candidate.payload_jsonb ->> 'decision'
                      AND review_candidate.event_type =
                          'stocktake.opening.' ||
                          candidate_owner.review_stage || '_reviewed'
                      AND review_candidate.idempotency_key =
                          {_opening_review_event_key_sql('outbox', 'candidate_owner.id')}
               )
        )
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS review_audit
              WHERE review_audit.stream_key = 'inventory'
                AND review_audit.actor_user_id =
                    {review_alias}.reviewer_user_id
                AND review_audit.action =
                    'stocktake.opening.' || {review_alias}.review_stage ||
                    '_reviewed'
                AND review_audit.aggregate_type = 'stocktake_review'
                AND review_audit.aggregate_id = {review_alias}.id::text
                AND review_audit.before_jsonb IS NULL
                AND review_audit.after_jsonb = ({metadata}) ||
                    pg_catalog.jsonb_build_object(
                        'authorization_version',
                        {review_alias}.authorization_version,
                        'reviewer_person_id',
                        {review_alias}.reviewer_person_id::text,
                        'reviewer_role_assignment_id',
                        {review_alias}.reviewer_role_assignment_id::text,
                        'reviewer_user_id',
                        {review_alias}.reviewer_user_id
                    )
                AND review_audit.request_id ~
                    '^opening-review-request-[0-9a-f]{{64}}$'
                AND review_audit.occurred_at = {review_alias}.reviewed_at
                AND review_audit.created_at >= {review_alias}.reviewed_at
                AND review_audit.event_hash ~ '^[0-9a-f]{{64}}$'
                AND ({_audit_event_chain_binding_sql(
                    'review_audit',
                    require_head=not historical,
                )})
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS review_audit_candidate
              WHERE review_audit_candidate.aggregate_type =
                    'stocktake_review'
                AND review_audit_candidate.aggregate_id =
                    {review_alias}.id::text
        ) = 1
    )"""


def _recount_event_metadata_sql(
    recount_case_alias: str,
    round_alias: str,
) -> str:
    return f"""pg_catalog.jsonb_build_object(
        'assignment_manifest_sha256',
        {recount_case_alias}.assignment_manifest_sha256,
        'next_round_id',
        {round_alias}.id::text,
        'next_round_no',
        {recount_case_alias}.next_round_no,
        'recount_case_id',
        {recount_case_alias}.id::text,
        'recount_manifest_sha256',
        {recount_case_alias}.recount_manifest_sha256,
        'scope_count',
        {recount_case_alias}.scope_count,
        'source_round_id',
        {recount_case_alias}.source_round_id::text
    )"""


def _opening_recount_event_key_sql(kind: str, case_id_sql: str) -> str:
    if kind not in {"state", "outbox"}:
        raise ValueError("unsupported opening recount event kind")
    return f"""'opening-recount-{kind}-' || pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                'cloud_oam.opening_stocktake.recount.{kind}.v1',
                'UTF8'
            ) || pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to(({case_id_sql})::text, 'UTF8')
        ),
        'hex'
    )"""


def _recount_side_effect_proof_sql(
    task_id_sql: str,
    case_alias: str,
    round_alias: str,
    *,
    historical: bool,
) -> str:
    """Prove the canonical recount state/outbox/audit set and ownership."""

    metadata = _recount_event_metadata_sql(case_alias, round_alias)
    state_key = _opening_recount_event_key_sql("state", f"{case_alias}.id")
    outbox_key = _opening_recount_event_key_sql("outbox", f"{case_alias}.id")
    fresh_outbox = "TRUE" if historical else """(
        recount_outbox.status = 'pending'
        AND recount_outbox.attempts = 0
        AND recount_outbox.locked_at IS NULL
        AND recount_outbox.locked_by IS NULL
        AND recount_outbox.published_at IS NULL
        AND recount_outbox.last_error IS NULL
        AND recount_outbox.updated_at = recount_outbox.created_at
    )"""
    candidate_state_key = _opening_recount_event_key_sql(
        "state", "candidate_case.id"
    )
    candidate_outbox_key = _opening_recount_event_key_sql(
        "outbox", "candidate_case.id"
    )
    candidate_metadata = _recount_event_metadata_sql(
        "candidate_case", "candidate_round"
    )
    audit_payload = f"""({metadata}) || pg_catalog.jsonb_build_object(
        'authorization_sha256', {case_alias}.authorization_sha256,
        'authorization_version', {case_alias}.authorization_version,
        'opened_by_person_id', {case_alias}.opened_by_person_id::text,
        'opened_by_role_assignment_id',
            {case_alias}.opened_role_assignment_id::text,
        'opened_by_user_id', {case_alias}.opened_by_user_id,
        'role_code', {case_alias}.role_code,
        'scope_id_snapshot', {case_alias}.scope_id_snapshot,
        'scope_type', {case_alias}.scope_type
    )"""
    return f"""(
        (SELECT pg_catalog.count(*)
           FROM public.state_transition_events AS recount_state
          WHERE recount_state.aggregate_type = 'stocktake_task'
            AND recount_state.aggregate_id = ({task_id_sql})::text
            AND recount_state.from_status = 'recount_required'
            AND recount_state.to_status = 'counting'
            AND recount_state.reason = 'opening_recount_opened'
            AND recount_state.actor_id = {case_alias}.opened_by_user_id
            AND recount_state.idempotency_key = {state_key}
            AND recount_state.occurred_at = {case_alias}.opened_at
            AND recount_state.created_at = {case_alias}.opened_at
            AND recount_state.metadata_jsonb = {metadata}
        ) = 1
        AND NOT EXISTS (
            SELECT 1
              FROM public.state_transition_events AS recount_candidate
             WHERE recount_candidate.aggregate_type = 'stocktake_task'
               AND recount_candidate.aggregate_id = ({task_id_sql})::text
               AND recount_candidate.reason = 'opening_recount_opened'
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_recount_cases AS candidate_case
                     JOIN public.stocktake_rounds AS candidate_round
                       ON candidate_round.recount_case_id = candidate_case.id
                      AND candidate_round.task_id = candidate_case.task_id
                      AND candidate_round.round_no =
                          candidate_case.next_round_no
                    WHERE candidate_case.task_id = ({task_id_sql})
                      AND recount_candidate.idempotency_key =
                          {candidate_state_key}
                      AND recount_candidate.actor_id =
                          candidate_case.opened_by_user_id
                      AND recount_candidate.occurred_at =
                          candidate_case.opened_at
                      AND recount_candidate.created_at =
                          candidate_case.opened_at
                      AND recount_candidate.from_status =
                          'recount_required'
                      AND recount_candidate.to_status = 'counting'
                      AND recount_candidate.metadata_jsonb =
                          {candidate_metadata}
               )
        )
        AND (SELECT pg_catalog.count(*)
               FROM public.outbox_events AS recount_outbox
              WHERE recount_outbox.event_type =
                    'stocktake.opening.recount_opened'
                AND recount_outbox.aggregate_type = 'stocktake_task'
                AND recount_outbox.aggregate_id = ({task_id_sql})::text
                AND recount_outbox.idempotency_key = {outbox_key}
                AND recount_outbox.payload_jsonb = {metadata}
                AND recount_outbox.available_at = {case_alias}.opened_at
                AND recount_outbox.created_at = {case_alias}.opened_at
                AND recount_outbox.updated_at >= {case_alias}.opened_at
                AND (
                    recount_outbox.locked_at IS NULL
                    OR recount_outbox.locked_at >= {case_alias}.opened_at
                )
                AND (
                    recount_outbox.published_at IS NULL
                    OR recount_outbox.published_at >= {case_alias}.opened_at
                )
                AND {fresh_outbox}
        ) = 1
        AND NOT EXISTS (
            SELECT 1
              FROM public.outbox_events AS recount_candidate
             WHERE recount_candidate.aggregate_type = 'stocktake_task'
               AND recount_candidate.aggregate_id = ({task_id_sql})::text
               AND recount_candidate.event_type =
                   'stocktake.opening.recount_opened'
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_recount_cases AS candidate_case
                     JOIN public.stocktake_rounds AS candidate_round
                       ON candidate_round.recount_case_id = candidate_case.id
                      AND candidate_round.task_id = candidate_case.task_id
                      AND candidate_round.round_no =
                          candidate_case.next_round_no
                    WHERE candidate_case.task_id = ({task_id_sql})
                      AND recount_candidate.idempotency_key =
                          {candidate_outbox_key}
                      AND recount_candidate.available_at =
                          candidate_case.opened_at
                      AND recount_candidate.created_at =
                          candidate_case.opened_at
                      AND recount_candidate.payload_jsonb =
                          {candidate_metadata}
               )
        )
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS recount_audit
              WHERE recount_audit.stream_key = 'inventory'
                AND recount_audit.actor_user_id =
                    {case_alias}.opened_by_user_id
                AND recount_audit.action =
                    'stocktake.opening.recount_opened'
                AND recount_audit.aggregate_type =
                    'stocktake_recount_case'
                AND recount_audit.aggregate_id = {case_alias}.id::text
                AND recount_audit.before_jsonb IS NULL
                AND recount_audit.after_jsonb = {audit_payload}
                AND recount_audit.request_id ~
                    '^opening-recount-request-[0-9a-f]{{64}}$'
                AND recount_audit.occurred_at = {case_alias}.opened_at
                AND recount_audit.created_at >= {case_alias}.opened_at
                AND recount_audit.event_hash ~ '^[0-9a-f]{{64}}$'
                AND ({_audit_event_chain_binding_sql(
                    'recount_audit',
                    require_head=not historical,
                )})
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS recount_audit_candidate
              WHERE recount_audit_candidate.aggregate_type =
                    'stocktake_recount_case'
                AND recount_audit_candidate.aggregate_id =
                    {case_alias}.id::text
        ) = 1
    )"""


def _json_text_sql(expression: str) -> str:
    """Render a non-null scalar exactly as Python's compact UTF-8 JSON."""

    return f"pg_catalog.to_json(({expression})::text)::text"


def _timestamp_json_sql(expression: str) -> str:
    return _json_text_sql(
        "pg_catalog.to_char("
        f"pg_catalog.timezone('UTC', {expression}), "
        "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'"
        ")"
    )


def _canonical_text_sha256_sql(document_sql: str) -> str:
    """Hash an already key-sorted, compact canonical JSON document."""

    return (
        "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(("
        f"{document_sql}), 'UTF8')), 'hex')"
    )


def _canonical_jsonb_sha256_sql(document_sql: str) -> str:
    """Hash an arbitrary recursively canonical JSON document."""

    return _canonical_text_sha256_sql(
        f"public.{CANONICAL_JSON_FUNCTION}(({document_sql})::jsonb)"
    )


def _canonical_flat_jsonb_text_sql(
    expression: str,
    *,
    alias_suffix: str,
) -> str:
    """Render the flat JSON objects used by opening audit facts canonically.

    Python's audit writer sorts object keys and omits whitespace.  Every
    opening audit payload proved in this migration is a flat JSON object, so
    sorting ``jsonb_object_keys`` exactly reproduces that representation while
    preserving JSON scalar quoting and null/boolean/number spellings.
    """

    key_alias = f"canonical_key_{alias_suffix}"
    return f"""CASE
        WHEN {expression} IS NULL THEN 'null'
        ELSE (
            SELECT '{{' || COALESCE(
                       pg_catalog.string_agg(
                           pg_catalog.to_json({key_alias}.key_name)::text ||
                           ':' ||
                           ({expression} -> {key_alias}.key_name)::text,
                           ',' ORDER BY {key_alias}.key_name
                       ),
                       ''
                   ) || '}}'
              FROM pg_catalog.jsonb_object_keys({expression})
                   AS {key_alias}(key_name)
        )
    END"""


def _audit_event_sha256_sql(audit_alias: str) -> str:
    """Recompute one persisted audit event with the production v1 contract."""

    document = f"""public.{CANONICAL_JSON_FUNCTION}(
        pg_catalog.jsonb_build_object(
            'action', {audit_alias}.action,
            'actor_user_id', {audit_alias}.actor_user_id,
            'after_jsonb', {audit_alias}.after_jsonb,
            'aggregate_id', {audit_alias}.aggregate_id,
            'aggregate_type', {audit_alias}.aggregate_type,
            'before_jsonb', {audit_alias}.before_jsonb,
            'event_id', {audit_alias}.id::text,
            'occurred_at', pg_catalog.to_char(
                pg_catalog.timezone('UTC', {audit_alias}.occurred_at),
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            ),
            'previous_hash', {audit_alias}.previous_hash,
            'request_id', {audit_alias}.request_id,
            'stream_key', {audit_alias}.stream_key
        )
    )"""
    return _canonical_text_sha256_sql(document)


def _audit_event_chain_binding_sql(
    audit_alias: str,
    *,
    require_head: bool,
) -> str:
    """Bind one canonical row to its immutable chain in constant time.

    0052 performs one full-chain proof while holding the migration locks.  At
    runtime the immutable 0015/0017 guards plus this predecessor/successor and
    head binding form the induction step, avoiding an O(N) history scan for
    every business event (and O(N squared) behavior for batched appends).
    """

    head_relation = (
        f"""audit_head.version = {audit_alias}.stream_version
            AND audit_head.last_event_id = {audit_alias}.id
            AND audit_head.last_hash = {audit_alias}.event_hash"""
        if require_head
        else f"""audit_head.version >= {audit_alias}.stream_version
            AND (
                (
                    audit_head.version = {audit_alias}.stream_version
                    AND audit_head.last_event_id = {audit_alias}.id
                    AND audit_head.last_hash = {audit_alias}.event_hash
                )
                OR (
                    audit_head.version > {audit_alias}.stream_version
                    AND EXISTS (
                        SELECT 1
                          FROM public.audit_events AS audit_successor
                         WHERE audit_successor.stream_key =
                               {audit_alias}.stream_key
                           AND audit_successor.stream_version =
                               {audit_alias}.stream_version + 1
                           AND audit_successor.previous_hash =
                               {audit_alias}.event_hash
                    )
                )
            )"""
    )
    return f"""(
        {audit_alias}.event_hash = ({_audit_event_sha256_sql(audit_alias)})
        AND {audit_alias}.stream_version > 0
        AND (
            (
                {audit_alias}.stream_version = 1
                AND {audit_alias}.previous_hash IS NULL
            )
            OR (
                {audit_alias}.stream_version > 1
                AND EXISTS (
                    SELECT 1
                      FROM public.audit_events AS audit_predecessor
                     WHERE audit_predecessor.stream_key =
                           {audit_alias}.stream_key
                       AND audit_predecessor.stream_version =
                           {audit_alias}.stream_version - 1
                       AND audit_predecessor.event_hash =
                           {audit_alias}.previous_hash
                )
            )
        )
        AND EXISTS (
            SELECT 1
              FROM public.audit_chain_heads AS audit_head
             WHERE audit_head.stream_key = {audit_alias}.stream_key
               AND {head_relation}
        )
    )"""


def _audit_stream_full_chain_binding_sql(head_alias: str) -> str:
    """Prove one locked stream from its head to canonical genesis."""

    return f"""(
        (
            {head_alias}.version = 0
            AND {head_alias}.last_event_id IS NULL
            AND {head_alias}.last_hash IS NULL
            AND NOT EXISTS (
                SELECT 1
                  FROM public.audit_events AS empty_stream_event
                 WHERE empty_stream_event.stream_key = {head_alias}.stream_key
            )
        )
        OR (
            {head_alias}.version > 0
            AND EXISTS (
                WITH RECURSIVE audit_walk(
                    id,
                    event_hash,
                    previous_hash,
                    stream_version,
                    canonical_valid,
                    visited
                ) AS (
                    SELECT head_event.id,
                           head_event.event_hash,
                           head_event.previous_hash,
                           head_event.stream_version,
                           head_event.event_hash =
                               ({_audit_event_sha256_sql('head_event')}),
                           ARRAY[head_event.id]::uuid[]
                      FROM public.audit_events AS head_event
                     WHERE head_event.id = {head_alias}.last_event_id
                       AND head_event.event_hash = {head_alias}.last_hash
                       AND head_event.stream_key = {head_alias}.stream_key
                       AND head_event.stream_version = {head_alias}.version
                    UNION ALL
                    SELECT predecessor.id,
                           predecessor.event_hash,
                           predecessor.previous_hash,
                           predecessor.stream_version,
                           predecessor.event_hash =
                               ({_audit_event_sha256_sql('predecessor')}),
                           audit_walk.visited || predecessor.id
                      FROM audit_walk
                      JOIN public.audit_events AS predecessor
                        ON predecessor.event_hash = audit_walk.previous_hash
                       AND predecessor.stream_key = {head_alias}.stream_key
                       AND predecessor.stream_version =
                           audit_walk.stream_version - 1
                     WHERE NOT predecessor.id = ANY(audit_walk.visited)
                )
                SELECT 1
                  FROM audit_walk
                 HAVING pg_catalog.count(*) = {head_alias}.version
                    AND pg_catalog.bool_and(audit_walk.canonical_valid)
                    AND pg_catalog.bool_or(
                        audit_walk.stream_version = 1
                        AND audit_walk.previous_hash IS NULL
                    )
            )
            AND (
                SELECT pg_catalog.count(*)
                  FROM public.audit_events AS counted_stream_event
                 WHERE counted_stream_event.stream_key = {head_alias}.stream_key
            ) = {head_alias}.version
        )
    )"""


def _recount_authorization_sha256_sql(
    alias: str,
    *,
    authorization_kind: str,
    assignee: bool,
) -> str:
    if authorization_kind not in {"opener", "assignee"}:
        raise ValueError("unsupported recount authorization kind")
    if assignee:
        user_id = f"{alias}.assignee_user_id"
        person_id = f"{alias}.assignee_person_id"
        assignment_id = f"{alias}.assignee_role_assignment_id"
        occurred_at = f"{alias}.assigned_at"
    else:
        user_id = f"{alias}.opened_by_user_id"
        person_id = f"{alias}.opened_by_person_id"
        assignment_id = f"{alias}.opened_role_assignment_id"
        occurred_at = f"{alias}.opened_at"
    document = f"""(
        '{{"assignment_id":' ||
        {_json_text_sql(f'{assignment_id}::text')} ||
        ',"authorization_kind":"{authorization_kind}"' ||
        ',"authorization_version":' ||
        {alias}.authorization_version::text ||
        ',"occurred_at":' ||
        {_timestamp_json_sql(occurred_at)} ||
        ',"person_id":' ||
        {_json_text_sql(f'{person_id}::text')} ||
        ',"role_code":' || {_json_text_sql(f'{alias}.role_code')} ||
        ',"schema":"cloud_oam.opening_stocktake.recount_authorization.v1"' ||
        ',"scope_id":' || {_json_text_sql(f'{alias}.scope_id_snapshot')} ||
        ',"scope_type":' || {_json_text_sql(f'{alias}.scope_type')} ||
        ',"user_id":' || {_json_text_sql(user_id)} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _recount_assignment_sha256_sql(alias: str) -> str:
    document = f"""(
        '{{"assigned_at":' || {_timestamp_json_sql(f'{alias}.assigned_at')} ||
        ',"assignee_person_id":' ||
        {_json_text_sql(f'{alias}.assignee_person_id::text')} ||
        ',"assignee_role_assignment_id":' ||
        {_json_text_sql(f'{alias}.assignee_role_assignment_id::text')} ||
        ',"assignee_user_id":' ||
        {_json_text_sql(f'{alias}.assignee_user_id')} ||
        ',"authorization_sha256":' ||
        {_json_text_sql(f'{alias}.authorization_sha256')} ||
        ',"authorization_version":' ||
        {alias}.authorization_version::text ||
        ',"recount_case_id":' ||
        {_json_text_sql(f'{alias}.recount_case_id::text')} ||
        ',"role_code":' || {_json_text_sql(f'{alias}.role_code')} ||
        ',"schema":"cloud_oam.opening_stocktake.recount_scope_assignment.v1"' ||
        ',"scope_id":' || {_json_text_sql(f'{alias}.scope_id::text')} ||
        ',"scope_id_snapshot":' ||
        {_json_text_sql(f'{alias}.scope_id_snapshot')} ||
        ',"scope_type":' || {_json_text_sql(f'{alias}.scope_type')} ||
        ',"source_round_id":' ||
        {_json_text_sql(f'{alias}.source_round_id::text')} ||
        ',"task_id":' || {_json_text_sql(f'{alias}.task_id::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _historical_authorization_sql(
    *,
    user_id_sql: str,
    person_id_sql: str,
    assignment_id_sql: str,
    authorization_version_sql: str,
    occurred_at_sql: str,
    role_code_sql: str,
    scope_type_sql: str,
    scope_id_sql: str,
    alias_suffix: str,
) -> str:
    """Validate one persisted authorization without rejecting later rotation."""

    assignment = f"historical_assignment_{alias_suffix}"
    role = f"historical_role_{alias_suffix}"
    user = f"historical_user_{alias_suffix}"
    return f"""EXISTS (
        SELECT 1
          FROM public.role_assignments AS {assignment}
          JOIN public.roles AS {role}
            ON {role}.id = {assignment}.role_id
          JOIN public.users AS {user}
            ON {user}.id = {assignment}.user_id
         WHERE {assignment}.id = {assignment_id_sql}
           AND {assignment}.user_id = {user_id_sql}
           AND {user}.person_id = {person_id_sql}
           AND {user}.authorization_version >= {authorization_version_sql}
           AND {assignment}.status IN (
               'scheduled', 'active', 'expired', 'revoked'
           )
           AND {assignment}.valid_from <= {occurred_at_sql}
           AND (
               {assignment}.valid_to IS NULL
               OR {occurred_at_sql} < {assignment}.valid_to
           )
           AND (
               {assignment}.revoked_at IS NULL
               OR {occurred_at_sql} < {assignment}.revoked_at
           )
           AND NOT {role}.is_external
           AND {role}.code = {role_code_sql}
           AND {assignment}.scope_type = {scope_type_sql}
           AND {assignment}.scope_id = {scope_id_sql}
    )"""


def _current_authorization_sql(
    *,
    user_id_sql: str,
    person_id_sql: str,
    assignment_id_sql: str,
    authorization_version_sql: str,
    occurred_at_sql: str,
    role_code_sql: str,
    scope_type_sql: str,
    scope_id_sql: str,
    permission_resource: str,
    permission_action: str,
    allow_scheduled: bool,
    alias_suffix: str,
) -> str:
    """Validate a current principal and a fact created in this transaction.

    The formal services persist a value returned by ``clock_timestamp()`` in
    a statement preceding the guarded write.  The inclusive transaction/
    clock window therefore rejects historical backdating without requiring
    the impossible equality between that value and ``transaction_timestamp``.
    """

    assignment = f"current_assignment_{alias_suffix}"
    role = f"current_role_{alias_suffix}"
    user = f"current_user_{alias_suffix}"
    person = f"current_person_{alias_suffix}"
    person_org = f"current_person_org_{alias_suffix}"
    identity = f"current_identity_{alias_suffix}"
    permission_binding = f"current_permission_binding_{alias_suffix}"
    permission = f"current_permission_{alias_suffix}"
    if permission_resource != "stocktake" or permission_action not in {
        "count",
        "manage",
        "post_opening",
        "review_region",
        "review_headquarters",
    }:
        raise ValueError("unsupported current authorization permission")
    assignment_status = (
        f"{assignment}.status IN ('scheduled', 'active')"
        if allow_scheduled
        else f"{assignment}.status = 'active'"
    )
    deny_target_organization = f"""CASE
        WHEN {scope_type_sql} = 'organization'
        THEN ({scope_id_sql})::uuid
        WHEN {scope_type_sql} = 'person'
        THEN (
            SELECT denied_target_person.organization_id
              FROM public.people AS denied_target_person
             WHERE denied_target_person.id = ({scope_id_sql})::uuid
        )
        ELSE NULL::uuid
    END"""
    denied_organization_coverage = _organization_descends_sql(
        deny_target_organization,
        "denied_assignment.scope_id::uuid",
        require_active=False,
        alias_suffix=f"deny_{alias_suffix}",
    )
    return f"""EXISTS (
        SELECT 1
          FROM public.role_assignments AS {assignment}
          JOIN public.roles AS {role}
            ON {role}.id = {assignment}.role_id
          JOIN public.users AS {user}
            ON {user}.id = {assignment}.user_id
          JOIN public.people AS {person}
            ON {person}.id = {user}.person_id
          JOIN public.organizations AS {person_org}
            ON {person_org}.id = {person}.organization_id
         WHERE {assignment}.id = {assignment_id_sql}
           AND {assignment}.user_id = {user_id_sql}
           AND {user}.person_id = {person_id_sql}
           AND {user}.authorization_version = {authorization_version_sql}
           AND {user}.is_active
           AND {user}.account_status = 'active'
           AND {person}.employment_status = 'active'
           AND {person_org}.status = 'active'
           AND {assignment_status}
           AND {assignment}.revoked_at IS NULL
           AND {assignment}.valid_from <= pg_catalog.transaction_timestamp()
           AND (
               {assignment}.valid_to IS NULL
               OR pg_catalog.transaction_timestamp() < {assignment}.valid_to
           )
           AND {assignment}.valid_from <= {occurred_at_sql}
           AND (
               {assignment}.valid_to IS NULL
               OR {occurred_at_sql} < {assignment}.valid_to
           )
           AND {occurred_at_sql} >= pg_catalog.transaction_timestamp()
           AND {occurred_at_sql} <= pg_catalog.clock_timestamp()
           AND {role}.status = 'active'
           AND NOT {role}.is_external
           AND {role}.code = {role_code_sql}
           AND {assignment}.scope_type = {scope_type_sql}
           AND {assignment}.scope_id = {scope_id_sql}
           AND (
               (
                   {role}.code = 'admin'
                   AND {assignment}.scope_type = 'national'
                   AND {assignment}.scope_id = '*'
                   AND {person_org}.org_type = 'headquarters'
               )
               OR (
                   {role}.code = 'provincial_manager'
                   AND {assignment}.scope_type = 'organization'
                   AND {person_org}.org_type IN (
                       'headquarters', 'region_company', 'department'
                   )
                   AND EXISTS (
                       SELECT 1
                         FROM public.organizations AS target_org
                        WHERE target_org.id::text = {assignment}.scope_id
                          AND target_org.status = 'active'
                          AND target_org.org_type = 'region_company'
                   )
               )
               OR (
                   {role}.code = 'technician'
                   AND {assignment}.scope_type = 'person'
                   AND {assignment}.scope_id = {person}.id::text
                   AND {person_org}.org_type IN (
                       'headquarters', 'region_company', 'department'
                   )
               )
           )
           AND EXISTS (
               SELECT 1
                 FROM public.role_permissions AS {permission_binding}
                 JOIN public.permissions AS {permission}
                   ON {permission}.id = {permission_binding}.permission_id
                WHERE {permission_binding}.role_id = {role}.id
                  AND {permission_binding}.effect = 'allow'
                  AND {permission}.resource = '{permission_resource}'
                  AND {permission}.action = '{permission_action}'
                  AND {permission}.field_code = ''
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM public.role_assignments AS denied_assignment
                 JOIN public.roles AS denied_role
                   ON denied_role.id = denied_assignment.role_id
                 JOIN public.role_permissions AS denied_binding
                   ON denied_binding.role_id = denied_role.id
                 JOIN public.permissions AS denied_permission
                   ON denied_permission.id = denied_binding.permission_id
                WHERE denied_assignment.user_id = {user}.id
                  AND denied_assignment.status IN ('scheduled', 'active')
                  AND denied_assignment.revoked_at IS NULL
                  AND denied_assignment.valid_from <=
                      pg_catalog.transaction_timestamp()
                  AND (
                      denied_assignment.valid_to IS NULL
                      OR pg_catalog.transaction_timestamp() <
                         denied_assignment.valid_to
                  )
                  AND denied_role.status = 'active'
                  AND NOT denied_role.is_external
                  AND denied_binding.effect = 'deny'
                  AND denied_permission.resource = '{permission_resource}'
                  AND denied_permission.action = '{permission_action}'
                  AND denied_permission.field_code = ''
                  AND (
                      (
                          denied_assignment.scope_type = 'national'
                          AND denied_assignment.scope_id = '*'
                      )
                      OR (
                          denied_assignment.scope_type = {scope_type_sql}
                          AND denied_assignment.scope_id = {scope_id_sql}
                      )
                      OR (
                          denied_assignment.scope_type = 'organization'
                          AND {scope_type_sql} IN ('organization', 'person')
                          AND ({denied_organization_coverage})
                      )
                  )
           )
           AND EXISTS (
               SELECT 1
                 FROM public.auth_identities AS {identity}
                WHERE {identity}.user_id = {user}.id
                  AND {identity}.status = 'active'
                  AND {identity}.verified_at IS NOT NULL
                  AND {identity}.revoked_at IS NULL
           )
    )"""


def _current_entitlement_target_sql(
    *,
    user_id_sql: str,
    assignment_id_sql: str,
    permission_resource: str,
    permission_action: str,
    target_scope_type_sql: str,
    target_scope_id_sql: str,
    alias_suffix: str,
) -> str:
    """Require selected allow plus the principal-wide deny-first decision."""

    if permission_resource != "stocktake" or permission_action not in {
        "count",
        "manage",
        "review_region",
        "review_headquarters",
        "post_opening",
    }:
        raise ValueError("unsupported current entitlement target")

    def coverage(assignment: str, suffix: str) -> str:
        target_organization = f"""CASE
            WHEN {target_scope_type_sql} = 'organization'
            THEN ({target_scope_id_sql})::uuid
            WHEN {target_scope_type_sql} = 'person'
            THEN (
                SELECT target_person_{suffix}.organization_id
                  FROM public.people AS target_person_{suffix}
                 WHERE target_person_{suffix}.id =
                       ({target_scope_id_sql})::uuid
            )
            ELSE NULL::uuid
        END"""
        organization_coverage = _organization_descends_sql(
            target_organization,
            f"{assignment}.scope_id::uuid",
            require_active=False,
            alias_suffix=f"target_{suffix}",
        )
        return f"""(
            (
                {assignment}.scope_type = 'national'
                AND {assignment}.scope_id = '*'
            )
            OR (
                {assignment}.scope_type = {target_scope_type_sql}
                AND {assignment}.scope_id = {target_scope_id_sql}
            )
            OR (
                {assignment}.scope_type = 'organization'
                AND {target_scope_type_sql} IN ('organization', 'person')
                AND ({organization_coverage})
            )
        )"""

    selected_coverage = coverage(
        f"selected_assignment_{alias_suffix}",
        f"selected_{alias_suffix}",
    )
    denied_coverage = coverage(
        f"denied_assignment_{alias_suffix}",
        f"denied_{alias_suffix}",
    )
    return f"""(
        EXISTS (
            SELECT 1
              FROM public.role_assignments AS selected_assignment_{alias_suffix}
              JOIN public.roles AS selected_role_{alias_suffix}
                ON selected_role_{alias_suffix}.id =
                   selected_assignment_{alias_suffix}.role_id
              JOIN public.role_permissions AS selected_allow_{alias_suffix}
                ON selected_allow_{alias_suffix}.role_id =
                   selected_role_{alias_suffix}.id
               AND selected_allow_{alias_suffix}.effect = 'allow'
              JOIN public.permissions AS selected_permission_{alias_suffix}
                ON selected_permission_{alias_suffix}.id =
                   selected_allow_{alias_suffix}.permission_id
             WHERE selected_assignment_{alias_suffix}.id =
                   {assignment_id_sql}
               AND selected_assignment_{alias_suffix}.user_id = {user_id_sql}
               AND selected_permission_{alias_suffix}.resource =
                   '{permission_resource}'
               AND selected_permission_{alias_suffix}.action =
                   '{permission_action}'
               AND selected_permission_{alias_suffix}.field_code = ''
               AND ({selected_coverage})
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.role_assignments AS denied_assignment_{alias_suffix}
              JOIN public.roles AS denied_role_{alias_suffix}
                ON denied_role_{alias_suffix}.id =
                   denied_assignment_{alias_suffix}.role_id
              JOIN public.role_permissions AS denied_binding_{alias_suffix}
                ON denied_binding_{alias_suffix}.role_id =
                   denied_role_{alias_suffix}.id
               AND denied_binding_{alias_suffix}.effect = 'deny'
              JOIN public.permissions AS denied_permission_{alias_suffix}
                ON denied_permission_{alias_suffix}.id =
                   denied_binding_{alias_suffix}.permission_id
             WHERE denied_assignment_{alias_suffix}.user_id = {user_id_sql}
               AND denied_assignment_{alias_suffix}.status IN (
                   'scheduled', 'active'
               )
               AND denied_assignment_{alias_suffix}.revoked_at IS NULL
               AND denied_assignment_{alias_suffix}.valid_from <=
                   pg_catalog.transaction_timestamp()
               AND (
                   denied_assignment_{alias_suffix}.valid_to IS NULL
                   OR pg_catalog.transaction_timestamp() <
                      denied_assignment_{alias_suffix}.valid_to
               )
               AND denied_role_{alias_suffix}.status = 'active'
               AND NOT denied_role_{alias_suffix}.is_external
               AND denied_permission_{alias_suffix}.resource =
                   '{permission_resource}'
               AND denied_permission_{alias_suffix}.action =
                   '{permission_action}'
               AND denied_permission_{alias_suffix}.field_code = ''
               AND ({denied_coverage})
        )
    )"""


def _scope_completion_authorization_sha256_sql(
    completion_alias: str,
) -> str:
    document = f"""(
        '{{"assignment_id":' ||
        {_json_text_sql(f'{completion_alias}.completed_role_assignment_id::text')} ||
        ',"authorization_version":' ||
        {completion_alias}.authorization_version::text ||
        ',"completed_at":' ||
        {_timestamp_json_sql(f'{completion_alias}.completed_at')} ||
        ',"person_id":' ||
        {_json_text_sql(f'{completion_alias}.completed_by_person_id::text')} ||
        ',"role_code":' ||
        {_json_text_sql(f'{completion_alias}.role_code')} ||
        ',"schema":"cloud_oam.opening_stocktake.scope_authorization.v1"' ||
        ',"scope_id":' ||
        {_json_text_sql(f'{completion_alias}.scope_id_snapshot')} ||
        ',"scope_type":' ||
        {_json_text_sql(f'{completion_alias}.scope_type')} ||
        ',"user_id":' ||
        {_json_text_sql(f'{completion_alias}.completed_by_user_id')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _observation_dimension_sha256_sql(
    observation_alias: str,
    scope_alias: str,
) -> str:
    raw_material = f"""CASE
        WHEN {observation_alias}.verification_status = 'pending_verification'
             AND {observation_alias}.material_id IS NULL
        THEN ',"material_identifier_raw":' ||
             {_json_text_sql(f'{observation_alias}.material_identifier_raw')} ||
             ',"material_identifier_type":' ||
             {_json_text_sql(f'{observation_alias}.material_identifier_type')}
        ELSE ''
    END"""
    raw_lot = f"""CASE
        WHEN {observation_alias}.verification_status = 'pending_verification'
             AND {observation_alias}.lot_no_raw IS NOT NULL
             AND {observation_alias}.lot_id IS NULL
        THEN ',"lot_no_raw":' ||
             {_json_text_sql(f'{observation_alias}.lot_no_raw')}
        ELSE ''
    END"""
    raw_serial = f"""CASE
        WHEN {observation_alias}.verification_status = 'pending_verification'
             AND {observation_alias}.serial_no_raw IS NOT NULL
             AND {observation_alias}.serial_id IS NULL
        THEN ',"serial_identifier_type":' ||
             {_json_text_sql(f'{observation_alias}.serial_identifier_type')} ||
             ',"serial_no_raw":' ||
             {_json_text_sql(f'{observation_alias}.serial_no_raw')}
        ELSE ''
    END"""
    document = f"""(
        '{{"availability_bucket":' ||
        {_json_text_sql(f'{observation_alias}.availability_bucket')} ||
        ',"condition_code":' ||
        {_json_text_sql(f'{observation_alias}.condition_code')} ||
        ',"custodian_person_id":' ||
        {_nullable_json_text_sql(f'{scope_alias}.custodian_person_id_snapshot::text')} ||
        ',"location_id":' ||
        {_json_text_sql(f'{scope_alias}.location_id::text')} ||
        ',"lot_id":' ||
        {_nullable_json_text_sql(f'{observation_alias}.lot_id::text')} ||
        {raw_lot} ||
        ',"material_id":' ||
        {_nullable_json_text_sql(f'{observation_alias}.material_id::text')} ||
        {raw_material} ||
        ',"owner_org_id":' ||
        {_json_text_sql(f'{scope_alias}.owner_org_id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.observation_dimension.v1"' ||
        ',"serial_id":' ||
        {_nullable_json_text_sql(f'{observation_alias}.serial_id::text')} ||
        {raw_serial} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _scope_count_request_sha256_sql(
    task_id_sql: str,
    round_alias: str,
    scope_alias: str,
    completion_alias: str,
) -> str:
    observation = f"""(
        '{{"availability_bucket":' ||
        {_json_text_sql('request_observation.availability_bucket')} ||
        ',"condition_code":' ||
        {_json_text_sql('request_observation.condition_code')} ||
        ',"count_method":' ||
        {_json_text_sql('request_observation.count_method')} ||
        ',"counted_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('request_observation.counted_qty'))} ||
        ',"lot_id":' ||
        {_nullable_json_text_sql('request_observation.lot_id::text')} ||
        ',"lot_no_raw":' ||
        {_nullable_json_text_sql('request_observation.lot_no_raw')} ||
        ',"material_id":' ||
        {_nullable_json_text_sql('request_observation.material_id::text')} ||
        ',"material_identifier_raw":' ||
        {_json_text_sql('request_observation.material_identifier_raw')} ||
        ',"material_identifier_type":' ||
        {_json_text_sql('request_observation.material_identifier_type')} ||
        ',"reason_code":' ||
        {_nullable_json_text_sql('request_observation.reason_code')} ||
        ',"remark":' || {_json_text_sql('request_observation.remark')} ||
        ',"serial_id":' ||
        {_nullable_json_text_sql('request_observation.serial_id::text')} ||
        ',"serial_identifier_type":' ||
        {_nullable_json_text_sql('request_observation.serial_identifier_type')} ||
        ',"serial_no_raw":' ||
        {_nullable_json_text_sql('request_observation.serial_no_raw')} ||
        '}}'
    )"""
    document = f"""(
        '{{"actor_person_id":' ||
        {_json_text_sql(f'{completion_alias}.completed_by_person_id::text')} ||
        ',"actor_user_id":' ||
        {_json_text_sql(f'{completion_alias}.completed_by_user_id')} ||
        ',"physical_observations":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       request_observation_document.document,
                       ',' ORDER BY request_observation_document.document
                   )
              FROM public.stocktake_count_observations
                   AS request_observation
              CROSS JOIN LATERAL (
                  SELECT {observation} AS document
              ) AS request_observation_document
             WHERE request_observation.task_id = {task_id_sql}
               AND request_observation.round_id = {round_alias}.id
               AND request_observation.scope_id = {scope_alias}.id
        ), '') ||
        '],"round_id":' || {_json_text_sql(f'{round_alias}.id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.scope_count_request.v1"' ||
        ',"scope_id":' || {_json_text_sql(f'{scope_alias}.id::text')} ||
        ',"task_id":' || {_json_text_sql(f'{task_id_sql}::text')} ||
        ',"zero_confirmed":' || {completion_alias}.zero_confirmed::text ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _observation_child_idempotency_sha256_sql(
    completion_alias: str,
    observation_alias: str,
) -> str:
    return f"""pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                'cloud_oam.opening_stocktake.observation.idempotency.v1',
                'UTF8'
            ) || pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to(
                {completion_alias}.idempotency_key_hash,
                'UTF8'
            ) || pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to({observation_alias}.dimension_sha256, 'UTF8')
        ),
        'hex'
    )"""


def _scope_evidence_manifest_sha256_sql(
    task_id_sql: str,
    round_alias: str,
    scope_alias: str,
    completion_alias: str,
) -> str:
    count_serial = f"""(
        '{{"created_at":' ||
        {_timestamp_json_sql('manifest_serial.created_at')} ||
        ',"result":' || {_json_text_sql('manifest_serial.result')} ||
        ',"serial_id":' ||
        {_json_text_sql('manifest_serial.serial_id::text')} ||
        '}}'
    )"""
    count_line = f"""(
        '{{"count_line_id":' ||
        {_json_text_sql('manifest_line.id::text')} ||
        ',"count_method":' || {_json_text_sql('manifest_line.count_method')} ||
        ',"counted_at":' || {_timestamp_json_sql('manifest_line.counted_at')} ||
        ',"counted_by_user_id":' ||
        {_json_text_sql('manifest_line.counted_by_user_id')} ||
        ',"counted_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('manifest_line.counted_qty'))} ||
        ',"created_at":' || {_timestamp_json_sql('manifest_line.created_at')} ||
        ',"reason_code":' ||
        {_nullable_json_text_sql('manifest_line.reason_code')} ||
        ',"remark":' || {_json_text_sql('manifest_line.remark')} ||
        ',"serials":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {count_serial},
                       ',' ORDER BY manifest_serial.serial_id::text
                   )
              FROM public.stocktake_count_serials AS manifest_serial
             WHERE manifest_serial.count_line_id = manifest_line.id
               AND manifest_serial.round_id = {round_alias}.id
        ), '') ||
        '],"stock_account_id":' ||
        {_json_text_sql('manifest_line.stock_account_id::text')} ||
        ',"updated_at":' || {_timestamp_json_sql('manifest_line.updated_at')} ||
        '}}'
    )"""
    observation = f"""(
        '{{"availability_bucket":' ||
        {_json_text_sql('manifest_observation.availability_bucket')} ||
        ',"condition_code":' ||
        {_json_text_sql('manifest_observation.condition_code')} ||
        ',"count_method":' ||
        {_json_text_sql('manifest_observation.count_method')} ||
        ',"counted_at":' ||
        {_timestamp_json_sql('manifest_observation.counted_at')} ||
        ',"counted_by_user_id":' ||
        {_json_text_sql('manifest_observation.counted_by_user_id')} ||
        ',"counted_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('manifest_observation.counted_qty'))} ||
        ',"created_at":' ||
        {_timestamp_json_sql('manifest_observation.created_at')} ||
        ',"dimension_sha256":' ||
        {_json_text_sql('manifest_observation.dimension_sha256')} ||
        ',"idempotency_key_hash":' ||
        {_json_text_sql('manifest_observation.idempotency_key_hash')} ||
        ',"lot_id":' ||
        {_nullable_json_text_sql('manifest_observation.lot_id::text')} ||
        ',"lot_no_raw":' ||
        {_nullable_json_text_sql('manifest_observation.lot_no_raw')} ||
        ',"material_id":' ||
        {_nullable_json_text_sql('manifest_observation.material_id::text')} ||
        ',"material_identifier_raw":' ||
        {_json_text_sql('manifest_observation.material_identifier_raw')} ||
        ',"material_identifier_type":' ||
        {_json_text_sql('manifest_observation.material_identifier_type')} ||
        ',"observation_id":' ||
        {_json_text_sql('manifest_observation.id::text')} ||
        ',"observation_no":' || manifest_observation.observation_no::text ||
        ',"reason_code":' ||
        {_nullable_json_text_sql('manifest_observation.reason_code')} ||
        ',"remark":' || {_json_text_sql('manifest_observation.remark')} ||
        ',"request_sha256":' ||
        {_json_text_sql('manifest_observation.request_sha256')} ||
        ',"serial_id":' ||
        {_nullable_json_text_sql('manifest_observation.serial_id::text')} ||
        ',"serial_identifier_type":' ||
        {_nullable_json_text_sql('manifest_observation.serial_identifier_type')} ||
        ',"serial_no_raw":' ||
        {_nullable_json_text_sql('manifest_observation.serial_no_raw')} ||
        ',"verification_status":' ||
        {_json_text_sql('manifest_observation.verification_status')} ||
        '}}'
    )"""
    document = f"""(
        '{{"authorization_sha256":' ||
        {_json_text_sql(f'{completion_alias}.authorization_sha256')} ||
        ',"count_lines":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {count_line},
                       ',' ORDER BY manifest_line.stock_account_id::text
                   )
              FROM public.stocktake_count_lines AS manifest_line
             WHERE manifest_line.task_id = {task_id_sql}
               AND manifest_line.round_id = {round_alias}.id
               AND manifest_line.scope_id = {scope_alias}.id
        ), '') ||
        '],"observations":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {observation},
                       ',' ORDER BY manifest_observation.dimension_sha256
                   )
              FROM public.stocktake_count_observations
                   AS manifest_observation
             WHERE manifest_observation.task_id = {task_id_sql}
               AND manifest_observation.round_id = {round_alias}.id
               AND manifest_observation.scope_id = {scope_alias}.id
        ), '') ||
        '],"round_id":' || {_json_text_sql(f'{round_alias}.id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.scope_evidence_manifest.v1"' ||
        ',"scope_id":' || {_json_text_sql(f'{scope_alias}.id::text')} ||
        ',"task_id":' || {_json_text_sql(f'{task_id_sql}::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _opening_count_manifest_sha256_sql(
    task_alias: str,
    round_alias: str,
) -> str:
    serial = f"""(
        '{{"result":' || {_json_text_sql('count_serial.result')} ||
        ',"serial_id":' || {_json_text_sql('count_serial.serial_id::text')} ||
        '}}'
    )"""
    line = f"""(
        '{{"count_line_id":' || {_json_text_sql('count_line.id::text')} ||
        ',"count_method":' || {_json_text_sql('count_line.count_method')} ||
        ',"counted_at":' || {_timestamp_json_sql('count_line.counted_at')} ||
        ',"counted_by_user_id":' ||
        {_json_text_sql('count_line.counted_by_user_id')} ||
        ',"counted_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('count_line.counted_qty'))} ||
        ',"reason_code":' || {_nullable_json_text_sql('count_line.reason_code')} ||
        ',"remark":' || {_json_text_sql('count_line.remark')} ||
        ',"scope_id":' || {_json_text_sql('count_line.scope_id::text')} ||
        ',"serials":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {serial},
                       ',' ORDER BY count_serial.serial_id::text
                   )
              FROM public.stocktake_count_serials AS count_serial
             WHERE count_serial.count_line_id = count_line.id
               AND count_serial.round_id = {round_alias}.id
        ), '') ||
        '],"stock_account_id":' ||
        {_json_text_sql('count_line.stock_account_id::text')} ||
        '}}'
    )"""
    document = f"""(
        '{{"control_manifest_sha256":' ||
        {_json_text_sql(f'{task_alias}.control_manifest_sha256')} ||
        ',"control_snapshot_at":' ||
        {_timestamp_json_sql(f'{task_alias}.control_snapshot_at')} ||
        ',"control_source_system_id":' ||
        {_json_text_sql(f'{task_alias}.control_source_system_id::text')} ||
        ',"control_sync_run_id":' ||
        {_json_text_sql(f'{task_alias}.control_sync_run_id::text')} ||
        ',"cutoff_at":' || {_timestamp_json_sql(f'{task_alias}.cutoff_at')} ||
        ',"cutoff_ledger_cursor":' ||
        {task_alias}.cutoff_ledger_cursor::text ||
        ',"lines":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {line},
                       ',' ORDER BY count_line.stock_account_id::text,
                                    count_line.id::text
                   )
              FROM public.stocktake_count_lines AS count_line
             WHERE count_line.task_id = {task_alias}.id
               AND count_line.round_id = {round_alias}.id
        ), '') ||
        '],"round_id":' || {_json_text_sql(f'{round_alias}.id::text')} ||
        ',"round_no":' || {round_alias}.round_no::text ||
        ',"schema":"cloud_oam.opening_stocktake.count_manifest.v1"' ||
        ',"scope_manifest_sha256":' ||
        {_json_text_sql(f'{task_alias}.scope_manifest_sha256')} ||
        ',"snapshot_manifest_sha256":' ||
        {_json_text_sql(f'{task_alias}.snapshot_manifest_sha256')} ||
        ',"task_id":' || {_json_text_sql(f'{task_alias}.id::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _round_manifest_sha256_sql(
    task_id_sql: str,
    round_alias: str,
    submission_alias: str,
) -> str:
    completion_item = """(
        '{"authorization_sha256":' ||
        pg_catalog.to_json(completion.authorization_sha256::text)::text ||
        ',"evidence_manifest_sha256":' ||
        pg_catalog.to_json(completion.evidence_manifest_sha256::text)::text ||
        ',"scope_id":' ||
        pg_catalog.to_json(completion.scope_id::text)::text ||
        ',"zero_confirmed":' || completion.zero_confirmed::text ||
        '}'
    )"""
    document = f"""(
        '{{"completions":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {completion_item},
                       ',' ORDER BY completion.scope_id::text
                   )
              FROM public.stocktake_scope_count_completions AS completion
             WHERE completion.task_id = {task_id_sql}
               AND completion.round_id = {round_alias}.id
        ), '') ||
        '],"round_id":' || {_json_text_sql(f'{round_alias}.id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.round_manifest.v2"' ||
        ',"sealing_completion_id":' ||
        {_json_text_sql(f'{submission_alias}.sealing_completion_id::text')} ||
        ',"task_id":' || {_json_text_sql(f'{task_id_sql}::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _round_submission_request_sha256_sql(
    task_id_sql: str,
    round_alias: str,
    submission_alias: str,
) -> str:
    round_manifest = _round_manifest_sha256_sql(
        task_id_sql,
        round_alias,
        submission_alias,
    )
    document = f"""(
        '{{"count_manifest_sha256":' ||
        {_json_text_sql(f'{submission_alias}.count_manifest_sha256')} ||
        ',"round_id":' || {_json_text_sql(f'{round_alias}.id::text')} ||
        ',"round_manifest_sha256":' || {_json_text_sql(round_manifest)} ||
        ',"schema":"cloud_oam.opening_stocktake.round_submission_request.v3"' ||
        ',"sealing_completion_id":' ||
        {_json_text_sql(f'{submission_alias}.sealing_completion_id::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _round_submission_idempotency_sha256_sql(
    task_id_sql: str,
    round_alias: str,
) -> str:
    return f"""pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                'cloud_oam.opening_stocktake.round-submission.v1',
                'UTF8'
            ) || pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to({round_alias}.id::text, 'UTF8') ||
            pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to(({task_id_sql})::text, 'UTF8')
        ),
        'hex'
    )"""


def _opening_count_event_key_sql(
    kind: str,
    round_id_sql: str,
    right_id_sql: str,
) -> str:
    if kind not in {
        "scope-state",
        "scope-outbox",
        "round-state",
        "round-outbox",
        "task-state",
    }:
        raise ValueError("unsupported opening count event kind")
    return f"""'opening-count-{kind}-' || pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                'cloud_oam.opening_stocktake.{kind}.v1',
                'UTF8'
            ) || pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to(({round_id_sql})::text, 'UTF8') ||
            pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to(({right_id_sql})::text, 'UTF8')
        ),
        'hex'
    )"""


def _round_submission_side_effect_proof_sql(
    task_id_sql: str,
    round_alias: str,
    submission_alias: str,
    *,
    historical: bool,
) -> str:
    scope_state_key = _opening_count_event_key_sql(
        "scope-state", f"{round_alias}.id", "side_completion.scope_id"
    )
    scope_outbox_key = _opening_count_event_key_sql(
        "scope-outbox", f"{round_alias}.id", "side_completion.scope_id"
    )
    round_state_key = _opening_count_event_key_sql(
        "round-state", f"{round_alias}.id", task_id_sql
    )
    round_outbox_key = _opening_count_event_key_sql(
        "round-outbox", f"{round_alias}.id", task_id_sql
    )
    task_state_key = _opening_count_event_key_sql(
        "task-state", f"{round_alias}.id", task_id_sql
    )
    candidate_scope_state_key = _opening_count_event_key_sql(
        "scope-state",
        "candidate_completion.round_id",
        "candidate_completion.scope_id",
    )
    candidate_scope_outbox_key = _opening_count_event_key_sql(
        "scope-outbox",
        "candidate_completion.round_id",
        "candidate_completion.scope_id",
    )
    context = f"""CASE
        WHEN {round_alias}.round_no = 1
             AND {round_alias}.round_type = 'initial'
        THEN pg_catalog.jsonb_build_object(
            'round_id', {round_alias}.id::text,
            'task_id', ({task_id_sql})::text
        )
        ELSE pg_catalog.jsonb_build_object(
            'recount_case_id', {round_alias}.recount_case_id::text,
            'round_id', {round_alias}.id::text,
            'round_no', {round_alias}.round_no,
            'round_type', {round_alias}.round_type,
            'task_id', ({task_id_sql})::text
        )
    END"""
    scope_reason = f"""CASE
        WHEN {round_alias}.round_no = 1
             AND {round_alias}.round_type = 'initial'
        THEN 'opening_initial_scope_count_completed'
        ELSE 'opening_recount_scope_count_completed'
    END"""
    round_reason = f"""CASE
        WHEN {round_alias}.round_no = 1
             AND {round_alias}.round_type = 'initial'
        THEN 'opening_initial_round_submitted'
        ELSE 'opening_recount_round_submitted'
    END"""
    round_state_metadata = f"""CASE
        WHEN {round_alias}.round_no = 1
             AND {round_alias}.round_type = 'initial'
        THEN pg_catalog.jsonb_build_object('task_id', ({task_id_sql})::text)
        ELSE {context}
    END"""
    task_state_metadata = f"""CASE
        WHEN {round_alias}.round_no = 1
             AND {round_alias}.round_type = 'initial'
        THEN pg_catalog.jsonb_build_object('round_id', {round_alias}.id::text)
        ELSE {context}
    END"""
    sealing_scope_outbox_fresh = (
        "TRUE"
        if historical
        else f"""(
            side_completion.id <> {submission_alias}.sealing_completion_id
            OR (
                side_outbox.status = 'pending'
                AND side_outbox.attempts = 0
                AND side_outbox.locked_at IS NULL
                AND side_outbox.locked_by IS NULL
                AND side_outbox.published_at IS NULL
                AND side_outbox.last_error IS NULL
                AND side_outbox.updated_at = side_outbox.created_at
            )
        )"""
    )
    round_outbox_fresh = "TRUE" if historical else """(
        round_outbox.status = 'pending'
        AND round_outbox.attempts = 0
        AND round_outbox.locked_at IS NULL
        AND round_outbox.locked_by IS NULL
        AND round_outbox.published_at IS NULL
        AND round_outbox.last_error IS NULL
        AND round_outbox.updated_at = round_outbox.created_at
    )"""
    return f"""(
        NOT EXISTS (
            SELECT 1
              FROM public.stocktake_scope_count_completions AS side_completion
             WHERE side_completion.task_id = {task_id_sql}
               AND side_completion.round_id = {round_alias}.id
               AND (
                   (SELECT pg_catalog.count(*)
                      FROM public.state_transition_events AS side_state
                     WHERE side_state.aggregate_type = 'stocktake_scope'
                       AND side_state.aggregate_id =
                           side_completion.scope_id::text
                       AND side_state.reason = {scope_reason}
                       AND side_state.idempotency_key = {scope_state_key}
                       AND side_state.from_status = 'counting'
                       AND side_state.to_status = 'completed'
                       AND side_state.actor_id =
                           side_completion.completed_by_user_id
                       AND side_state.occurred_at =
                           side_completion.completed_at
                       AND side_state.created_at =
                           side_completion.completed_at
                       AND side_state.metadata_jsonb =
                           ({context}) || pg_catalog.jsonb_build_object(
                               'zero_confirmed',
                               side_completion.zero_confirmed
                           )
                   ) <> 1
                   OR (SELECT pg_catalog.count(*)
                         FROM public.outbox_events AS side_outbox
                        WHERE side_outbox.aggregate_type = 'stocktake_scope'
                          AND side_outbox.aggregate_id =
                              side_completion.scope_id::text
                          AND side_outbox.event_type =
                              'stocktake.opening.scope_count_completed'
                          AND side_outbox.idempotency_key = {scope_outbox_key}
                          AND side_outbox.available_at =
                              side_completion.completed_at
                          AND side_outbox.created_at =
                              side_completion.completed_at
                          AND side_outbox.updated_at >=
                              side_completion.completed_at
                          AND side_outbox.payload_jsonb =
                              ({context}) || pg_catalog.jsonb_build_object(
                                  'round_sealed',
                                  side_completion.id =
                                      {submission_alias}.sealing_completion_id,
                                  'scope_id', side_completion.scope_id::text
                              )
                          AND {sealing_scope_outbox_fresh}
                   ) <> 1
                   OR (SELECT pg_catalog.count(*)
                         FROM public.audit_events AS side_audit
                        WHERE side_audit.stream_key = 'inventory'
                          AND side_audit.aggregate_type = 'stocktake_scope'
                          AND side_audit.aggregate_id =
                              side_completion.scope_id::text
                          AND side_audit.action =
                              'stocktake.opening.scope_count_completed'
                          AND side_audit.actor_user_id =
                              side_completion.completed_by_user_id
                          AND side_audit.before_jsonb IS NULL
                          AND side_audit.occurred_at =
                              side_completion.completed_at
                          AND side_audit.created_at >=
                              side_completion.completed_at
                          AND side_audit.request_id ~
                              '^opening-count-request-[0-9a-f]{{64}}$'
                          AND side_audit.event_hash ~ '^[0-9a-f]{{64}}$'
                          AND side_audit.event_hash =
                              ({_audit_event_sha256_sql('side_audit')})
                          AND side_audit.after_jsonb =
                              ({context}) || pg_catalog.jsonb_build_object(
                                  'has_pending_verification', EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_count_observations
                                             AS side_observation
                                       WHERE side_observation.task_id =
                                           {task_id_sql}
                                         AND side_observation.round_id =
                                           {round_alias}.id
                                         AND side_observation.scope_id =
                                           side_completion.scope_id
                                         AND side_observation.verification_status =
                                             'pending_verification'
                                  ),
                                  'round_sealed',
                                  side_completion.id =
                                      {submission_alias}.sealing_completion_id,
                                  'zero_confirmed',
                                  side_completion.zero_confirmed
                              )
                   ) <> 1
               )
        )
        AND (SELECT pg_catalog.count(*)
               FROM public.state_transition_events AS round_state
              WHERE round_state.aggregate_type = 'stocktake_round'
                AND round_state.aggregate_id = {round_alias}.id::text
                AND round_state.from_status = 'counting'
                AND round_state.to_status = 'submitted'
                AND round_state.reason = {round_reason}
                AND round_state.actor_id =
                    {submission_alias}.submitted_by_user_id
                AND round_state.idempotency_key = {round_state_key}
                AND round_state.occurred_at =
                    {submission_alias}.submitted_at
                AND round_state.created_at =
                    {submission_alias}.submitted_at
                AND round_state.metadata_jsonb = {round_state_metadata}
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.state_transition_events AS task_state
              WHERE task_state.aggregate_type = 'stocktake_task'
                AND task_state.aggregate_id = ({task_id_sql})::text
                AND task_state.from_status = 'counting'
                AND task_state.to_status = 'submitted'
                AND task_state.reason = {round_reason}
                AND task_state.actor_id =
                    {submission_alias}.submitted_by_user_id
                AND task_state.idempotency_key = {task_state_key}
                AND task_state.occurred_at =
                    {submission_alias}.submitted_at
                AND task_state.created_at =
                    {submission_alias}.submitted_at
                AND task_state.metadata_jsonb = {task_state_metadata}
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.outbox_events AS round_outbox
              WHERE round_outbox.aggregate_type = 'stocktake_round'
                AND round_outbox.aggregate_id = {round_alias}.id::text
                AND round_outbox.event_type =
                    'stocktake.opening.round_submitted'
                AND round_outbox.idempotency_key = {round_outbox_key}
                AND round_outbox.available_at =
                    {submission_alias}.submitted_at
                AND round_outbox.created_at =
                    {submission_alias}.submitted_at
                AND round_outbox.updated_at >=
                    {submission_alias}.submitted_at
                AND round_outbox.payload_jsonb = {context}
                AND {round_outbox_fresh}
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS round_audit
              WHERE round_audit.stream_key = 'inventory'
                AND round_audit.aggregate_type = 'stocktake_round'
                AND round_audit.aggregate_id = {round_alias}.id::text
                AND round_audit.action = 'stocktake.opening.round_submitted'
                AND round_audit.actor_user_id =
                    {submission_alias}.submitted_by_user_id
                AND round_audit.before_jsonb IS NULL
                AND round_audit.after_jsonb = {context}
                AND round_audit.request_id ~
                    '^opening-count-request-[0-9a-f]{{64}}$'
                AND round_audit.occurred_at =
                    {submission_alias}.submitted_at
                AND round_audit.created_at >=
                    {submission_alias}.submitted_at
                AND round_audit.event_hash ~ '^[0-9a-f]{{64}}$'
                AND ({_audit_event_chain_binding_sql(
                    'round_audit',
                    require_head=not historical,
                )})
        ) = 1
        AND NOT EXISTS (
            SELECT 1
              FROM public.state_transition_events AS scope_state_candidate
             WHERE scope_state_candidate.aggregate_type = 'stocktake_scope'
               AND scope_state_candidate.aggregate_id IN (
                   SELECT owned_scope.id::text
                     FROM public.stocktake_scopes AS owned_scope
                    WHERE owned_scope.task_id = {task_id_sql}
               )
               AND scope_state_candidate.reason IN (
                   'opening_initial_scope_count_completed',
                   'opening_recount_scope_count_completed'
               )
               AND (
                   scope_state_candidate.metadata_jsonb ->> 'round_id' =
                       {round_alias}.id::text
                   OR NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_rounds AS owner_round
                        WHERE owner_round.task_id = {task_id_sql}
                          AND owner_round.id::text =
                              scope_state_candidate.metadata_jsonb ->>
                                  'round_id'
                   )
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_scope_count_completions
                          AS candidate_completion
                    WHERE candidate_completion.task_id = {task_id_sql}
                      AND candidate_completion.round_id = {round_alias}.id
                      AND candidate_completion.scope_id::text =
                          scope_state_candidate.aggregate_id
                      AND scope_state_candidate.idempotency_key =
                          {candidate_scope_state_key}
                      AND scope_state_candidate.reason = {scope_reason}
               )
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.outbox_events AS scope_outbox_candidate
             WHERE scope_outbox_candidate.aggregate_type = 'stocktake_scope'
               AND scope_outbox_candidate.aggregate_id IN (
                   SELECT owned_scope.id::text
                     FROM public.stocktake_scopes AS owned_scope
                    WHERE owned_scope.task_id = {task_id_sql}
               )
               AND scope_outbox_candidate.event_type =
                   'stocktake.opening.scope_count_completed'
               AND (
                   scope_outbox_candidate.payload_jsonb ->> 'round_id' =
                       {round_alias}.id::text
                   OR NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_rounds AS owner_round
                        WHERE owner_round.task_id = {task_id_sql}
                          AND owner_round.id::text =
                              scope_outbox_candidate.payload_jsonb ->>
                                  'round_id'
                   )
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_scope_count_completions
                          AS candidate_completion
                    WHERE candidate_completion.task_id = {task_id_sql}
                      AND candidate_completion.round_id = {round_alias}.id
                      AND candidate_completion.scope_id::text =
                          scope_outbox_candidate.aggregate_id
                      AND scope_outbox_candidate.idempotency_key =
                          {candidate_scope_outbox_key}
               )
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.audit_events AS scope_audit_candidate
             WHERE scope_audit_candidate.aggregate_type = 'stocktake_scope'
               AND scope_audit_candidate.aggregate_id IN (
                   SELECT owned_scope.id::text
                     FROM public.stocktake_scopes AS owned_scope
                    WHERE owned_scope.task_id = {task_id_sql}
               )
               AND scope_audit_candidate.action =
                   'stocktake.opening.scope_count_completed'
               AND (
                   scope_audit_candidate.after_jsonb ->> 'round_id' =
                       {round_alias}.id::text
                   OR NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_rounds AS owner_round
                        WHERE owner_round.task_id = {task_id_sql}
                          AND owner_round.id::text =
                              scope_audit_candidate.after_jsonb ->> 'round_id'
                   )
               )
               AND (
                   SELECT pg_catalog.count(*)
                     FROM public.stocktake_scope_count_completions
                          AS candidate_completion
                    WHERE candidate_completion.task_id = {task_id_sql}
                      AND candidate_completion.round_id = {round_alias}.id
                      AND candidate_completion.scope_id::text =
                          scope_audit_candidate.aggregate_id
               ) <> 1
        )
        AND (
            SELECT pg_catalog.count(*)
              FROM public.audit_events AS scope_audit_candidate
             WHERE scope_audit_candidate.aggregate_type = 'stocktake_scope'
               AND scope_audit_candidate.aggregate_id IN (
                   SELECT owned_scope.id::text
                     FROM public.stocktake_scopes AS owned_scope
                    WHERE owned_scope.task_id = {task_id_sql}
               )
               AND scope_audit_candidate.action =
                   'stocktake.opening.scope_count_completed'
               AND scope_audit_candidate.after_jsonb ->> 'round_id' =
                   {round_alias}.id::text
        ) = {submission_alias}.scope_count
        AND (
            SELECT pg_catalog.count(*)
              FROM public.state_transition_events AS round_state_candidate
             WHERE round_state_candidate.aggregate_type = 'stocktake_round'
               AND round_state_candidate.aggregate_id =
                   {round_alias}.id::text
        ) = 1
        AND (
            SELECT pg_catalog.count(*)
              FROM public.state_transition_events AS task_state_candidate
             WHERE task_state_candidate.aggregate_type = 'stocktake_task'
               AND task_state_candidate.aggregate_id = ({task_id_sql})::text
               AND task_state_candidate.reason IN (
                   'opening_initial_round_submitted',
                   'opening_recount_round_submitted'
               )
               AND task_state_candidate.metadata_jsonb ->> 'round_id' =
                   {round_alias}.id::text
        ) = 1
        AND (
            SELECT pg_catalog.count(*)
              FROM public.outbox_events AS round_outbox_candidate
             WHERE round_outbox_candidate.aggregate_type = 'stocktake_round'
               AND round_outbox_candidate.aggregate_id =
                   {round_alias}.id::text
        ) = 1
        AND (
            SELECT pg_catalog.count(*)
              FROM public.audit_events AS round_audit_candidate
             WHERE round_audit_candidate.aggregate_type = 'stocktake_round'
               AND round_audit_candidate.aggregate_id =
                   {round_alias}.id::text
        ) = 1
    )"""


def _round_submission_proof_sql(
    task_id_sql: str,
    round_alias: str,
    submission_alias: str,
    *,
    historical: bool,
) -> str:
    """Reprove the complete opening round seal without trusting guard 0034."""

    round_manifest = _round_manifest_sha256_sql(
        task_id_sql,
        round_alias,
        submission_alias,
    )
    submission_request = _round_submission_request_sha256_sql(
        task_id_sql,
        round_alias,
        submission_alias,
    )
    submission_idempotency = _round_submission_idempotency_sha256_sql(
        task_id_sql,
        round_alias,
    )
    submission_side_effects = _round_submission_side_effect_proof_sql(
        task_id_sql,
        round_alias,
        submission_alias,
        historical=historical,
    )
    difference_completion = _review_difference_completion_proof_sql(
        task_id_sql,
        round_alias,
        submission_alias,
        None,
    )
    completion_authorization = _scope_completion_authorization_sha256_sql(
        "sealed_completion"
    )
    completion_request = _scope_count_request_sha256_sql(
        task_id_sql,
        round_alias,
        "sealed_scope",
        "sealed_completion",
    )
    completion_evidence = _scope_evidence_manifest_sha256_sql(
        task_id_sql,
        round_alias,
        "sealed_scope",
        "sealed_completion",
    )
    observation_dimension = _observation_dimension_sha256_sql(
        "sealed_observation",
        "sealed_scope",
    )
    observation_idempotency = _observation_child_idempotency_sha256_sql(
        "sealed_completion",
        "sealed_observation",
    )
    count_manifest = f"""(
        SELECT {_opening_count_manifest_sha256_sql('count_task', round_alias)}
          FROM public.stocktake_tasks AS count_task
         WHERE count_task.id = {task_id_sql}
           AND count_task.task_type = 'opening'
    )"""
    completion_actor = _historical_authorization_sql(
        user_id_sql="sealed_completion.completed_by_user_id",
        person_id_sql="sealed_completion.completed_by_person_id",
        assignment_id_sql=(
            "sealed_completion.completed_role_assignment_id"
        ),
        authorization_version_sql=(
            "sealed_completion.authorization_version"
        ),
        occurred_at_sql="sealed_completion.completed_at",
        role_code_sql="sealed_completion.role_code",
        scope_type_sql="sealed_completion.scope_type",
        scope_id_sql="sealed_completion.scope_id_snapshot",
        alias_suffix="scope_counter",
    )
    if historical:
        submitter_actor = _historical_authorization_sql(
            user_id_sql=f"{submission_alias}.submitted_by_user_id",
            person_id_sql=f"{submission_alias}.submitted_by_person_id",
            assignment_id_sql=(
                f"{submission_alias}.submitted_role_assignment_id"
            ),
            authorization_version_sql=(
                f"{submission_alias}.authorization_version"
            ),
            occurred_at_sql=f"{submission_alias}.submitted_at",
            role_code_sql="sealing.role_code",
            scope_type_sql="sealing.scope_type",
            scope_id_sql="sealing.scope_id_snapshot",
            alias_suffix="round_submitter",
        )
    else:
        submitter_actor = _current_authorization_sql(
            user_id_sql=f"{submission_alias}.submitted_by_user_id",
            person_id_sql=f"{submission_alias}.submitted_by_person_id",
            assignment_id_sql=(
                f"{submission_alias}.submitted_role_assignment_id"
            ),
            authorization_version_sql=(
                f"{submission_alias}.authorization_version"
            ),
            occurred_at_sql=f"{submission_alias}.submitted_at",
            role_code_sql="sealing.role_code",
            scope_type_sql="sealing.scope_type",
            scope_id_sql="sealing.scope_id_snapshot",
            permission_resource="stocktake",
            permission_action="count",
            allow_scheduled=False,
            alias_suffix="round_submitter",
        )
    return f"""(
        {round_alias}.status = 'submitted'
        AND {round_alias}.submitted_by_user_id =
            {submission_alias}.submitted_by_user_id
        AND {round_alias}.submitted_at = {submission_alias}.submitted_at
        AND {round_alias}.updated_at = {submission_alias}.submitted_at
        AND {round_alias}.started_at <= {submission_alias}.submitted_at
        AND {round_alias}.created_at <= {submission_alias}.submitted_at
        AND {round_alias}.count_manifest_sha256 =
            {submission_alias}.count_manifest_sha256
        AND {submission_alias}.created_at = {submission_alias}.submitted_at
        AND {submission_alias}.scope_count > 0
        AND {submission_alias}.scope_count = (
            SELECT pg_catalog.count(*)
              FROM public.stocktake_scopes AS sealed_scope
             WHERE sealed_scope.task_id = {task_id_sql}
        )
        AND {submission_alias}.scope_count = (
            SELECT pg_catalog.count(*)
              FROM public.stocktake_scope_count_completions
                   AS sealed_completion
             WHERE sealed_completion.task_id = {task_id_sql}
               AND sealed_completion.round_id = {round_alias}.id
        )
        AND {submission_alias}.zero_scope_count = (
            SELECT pg_catalog.count(*) FILTER (
                       WHERE sealed_completion.zero_confirmed
                   )
              FROM public.stocktake_scope_count_completions
                   AS sealed_completion
             WHERE sealed_completion.task_id = {task_id_sql}
               AND sealed_completion.round_id = {round_alias}.id
        )
        AND {submission_alias}.count_line_count = (
            SELECT COALESCE(pg_catalog.sum(
                       sealed_completion.count_line_count
                   ), 0)
              FROM public.stocktake_scope_count_completions
                   AS sealed_completion
             WHERE sealed_completion.task_id = {task_id_sql}
               AND sealed_completion.round_id = {round_alias}.id
        )
        AND {submission_alias}.observation_line_count = (
            SELECT COALESCE(pg_catalog.sum(
                       sealed_completion.observation_line_count
                   ), 0)
              FROM public.stocktake_scope_count_completions
                   AS sealed_completion
             WHERE sealed_completion.task_id = {task_id_sql}
               AND sealed_completion.round_id = {round_alias}.id
        )
        AND {submission_alias}.serial_count = (
            SELECT COALESCE(pg_catalog.sum(
                       sealed_completion.serial_count
                   ), 0)
              FROM public.stocktake_scope_count_completions
                   AS sealed_completion
             WHERE sealed_completion.task_id = {task_id_sql}
               AND sealed_completion.round_id = {round_alias}.id
        )
        AND {submission_alias}.total_counted_qty = (
            SELECT COALESCE(pg_catalog.sum(
                       sealed_completion.total_counted_qty
                   ), 0)::numeric(18, 3)
              FROM public.stocktake_scope_count_completions
                   AS sealed_completion
             WHERE sealed_completion.task_id = {task_id_sql}
               AND sealed_completion.round_id = {round_alias}.id
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_scopes AS sealed_scope
             WHERE sealed_scope.task_id = {task_id_sql}
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_scope_count_completions
                          AS sealed_completion
                    WHERE sealed_completion.task_id = {task_id_sql}
                      AND sealed_completion.round_id = {round_alias}.id
                      AND sealed_completion.scope_id = sealed_scope.id
               )
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_scope_count_completions
                   AS sealed_completion
              LEFT JOIN public.stocktake_scopes AS sealed_scope
                ON sealed_scope.id = sealed_completion.scope_id
               AND sealed_scope.task_id = {task_id_sql}
             WHERE sealed_completion.task_id = {task_id_sql}
               AND sealed_completion.round_id = {round_alias}.id
               AND sealed_scope.id IS NULL
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_scope_count_completions
                   AS sealed_completion
              JOIN public.stocktake_scopes AS sealed_scope
                ON sealed_scope.id = sealed_completion.scope_id
               AND sealed_scope.task_id = sealed_completion.task_id
             WHERE sealed_completion.task_id = {task_id_sql}
               AND sealed_completion.round_id = {round_alias}.id
               AND (
                   sealed_completion.created_at <>
                       sealed_completion.completed_at
                   OR sealed_completion.completed_at >
                       {submission_alias}.submitted_at
                   OR sealed_completion.count_line_count <> (
                       SELECT pg_catalog.count(*)
                         FROM public.stocktake_count_lines AS sealed_line
                        WHERE sealed_line.task_id = {task_id_sql}
                          AND sealed_line.round_id = {round_alias}.id
                          AND sealed_line.scope_id =
                              sealed_completion.scope_id
                   )
                   OR sealed_completion.observation_line_count <> (
                       SELECT pg_catalog.count(*)
                         FROM public.stocktake_count_observations
                              AS sealed_observation
                        WHERE sealed_observation.task_id = {task_id_sql}
                          AND sealed_observation.round_id = {round_alias}.id
                          AND sealed_observation.scope_id =
                              sealed_completion.scope_id
                   )
                   OR sealed_completion.serial_count <> (
                       SELECT (
                           SELECT pg_catalog.count(*)
                             FROM public.stocktake_count_serials
                                  AS sealed_serial
                             JOIN public.stocktake_count_lines AS serial_line
                               ON serial_line.id =
                                  sealed_serial.count_line_id
                              AND serial_line.round_id =
                                  sealed_serial.round_id
                            WHERE serial_line.task_id = {task_id_sql}
                              AND serial_line.round_id = {round_alias}.id
                              AND serial_line.scope_id =
                                  sealed_completion.scope_id
                       ) + (
                           SELECT pg_catalog.count(*)
                             FROM public.stocktake_count_observations
                                  AS serial_observation
                            WHERE serial_observation.task_id = {task_id_sql}
                              AND serial_observation.round_id =
                                  {round_alias}.id
                              AND serial_observation.scope_id =
                                  sealed_completion.scope_id
                              AND serial_observation.serial_no_raw IS NOT NULL
                       )
                   )
                   OR sealed_completion.total_counted_qty <> (
                       (
                           SELECT COALESCE(
                                      pg_catalog.sum(sealed_line.counted_qty),
                                      0
                                  )
                             FROM public.stocktake_count_lines AS sealed_line
                            WHERE sealed_line.task_id = {task_id_sql}
                              AND sealed_line.round_id = {round_alias}.id
                              AND sealed_line.scope_id =
                                  sealed_completion.scope_id
                       ) + (
                           SELECT COALESCE(
                                      pg_catalog.sum(
                                          sealed_observation.counted_qty
                                      ),
                                      0
                                  )
                             FROM public.stocktake_count_observations
                                  AS sealed_observation
                            WHERE sealed_observation.task_id = {task_id_sql}
                              AND sealed_observation.round_id =
                                  {round_alias}.id
                              AND sealed_observation.scope_id =
                                  sealed_completion.scope_id
                       )
                   )::numeric(18, 3)
                   OR sealed_completion.zero_confirmed IS DISTINCT FROM (
                       NOT EXISTS (
                           SELECT 1
                             FROM public.stocktake_snapshot_lines
                                  AS zero_snapshot
                            WHERE zero_snapshot.task_id = {task_id_sql}
                              AND zero_snapshot.scope_id =
                                  sealed_completion.scope_id
                       )
                       AND
                       sealed_completion.count_line_count = 0
                       AND sealed_completion.observation_line_count = 0
                   )
                   OR sealed_completion.authorization_sha256 IS DISTINCT FROM
                       ({completion_authorization})
                   OR sealed_completion.request_sha256 IS DISTINCT FROM
                       ({completion_request})
                   OR sealed_completion.evidence_manifest_sha256
                       IS DISTINCT FROM ({completion_evidence})
                   OR NOT ({completion_actor})
                   OR NOT (
                       (
                           {round_alias}.round_type = 'initial'
                           AND sealed_completion.completed_by_user_id =
                               sealed_scope.assignee_user_id
                           AND (
                               (
                                   sealed_completion.role_code = 'admin'
                                   AND sealed_completion.scope_type = 'national'
                                   AND sealed_completion.scope_id_snapshot = '*'
                               )
                               OR (
                                   sealed_completion.role_code =
                                       'provincial_manager'
                                   AND sealed_completion.scope_type =
                                       'organization'
                                   AND sealed_completion.scope_id_snapshot = (
                                       SELECT initial_task.region_org_id::text
                                         FROM public.stocktake_tasks
                                              AS initial_task
                                        WHERE initial_task.id = {task_id_sql}
                                   )
                                   AND sealed_scope.owner_org_id = (
                                       SELECT initial_task.region_org_id
                                         FROM public.stocktake_tasks
                                              AS initial_task
                                        WHERE initial_task.id = {task_id_sql}
                                   )
                               )
                               OR (
                                   sealed_completion.role_code = 'technician'
                                   AND sealed_completion.scope_type = 'person'
                                   AND sealed_scope.custodian_person_id_snapshot
                                       IS NOT NULL
                                   AND sealed_completion.completed_by_person_id =
                                       sealed_scope.custodian_person_id_snapshot
                                   AND sealed_completion.scope_id_snapshot =
                                       sealed_scope.custodian_person_id_snapshot::text
                                   AND EXISTS (
                                       SELECT 1
                                         FROM public.stock_locations
                                              AS initial_location
                                        WHERE initial_location.id =
                                              sealed_scope.location_id
                                          AND initial_location.location_type =
                                              'personal'
                                   )
                               )
                           )
                       )
                       OR {round_alias}.round_type = 'recount'
                   )
                   OR EXISTS (
                       SELECT 1
                         FROM public.stocktake_count_lines AS sealed_line
                        WHERE sealed_line.task_id = {task_id_sql}
                          AND sealed_line.round_id = {round_alias}.id
                          AND sealed_line.scope_id = sealed_completion.scope_id
                          AND (
                              sealed_line.counted_by_user_id <>
                                  sealed_completion.completed_by_user_id
                              OR sealed_line.counted_at <>
                                  sealed_completion.completed_at
                              OR sealed_line.created_at <>
                                  sealed_completion.completed_at
                              OR sealed_line.updated_at <>
                                  sealed_completion.completed_at
                          )
                   )
                   OR EXISTS (
                       SELECT 1
                         FROM public.stocktake_count_serials AS sealed_serial
                         JOIN public.stocktake_count_lines AS sealed_line
                           ON sealed_line.id = sealed_serial.count_line_id
                          AND sealed_line.round_id = sealed_serial.round_id
                        WHERE sealed_line.task_id = {task_id_sql}
                          AND sealed_line.round_id = {round_alias}.id
                          AND sealed_line.scope_id = sealed_completion.scope_id
                          AND sealed_serial.created_at <>
                              sealed_completion.completed_at
                   )
                   OR EXISTS (
                       SELECT 1
                         FROM public.stocktake_count_observations
                              AS sealed_observation
                        WHERE sealed_observation.task_id = {task_id_sql}
                          AND sealed_observation.round_id = {round_alias}.id
                          AND sealed_observation.scope_id =
                              sealed_completion.scope_id
                          AND (
                              sealed_observation.owner_org_id <>
                                  sealed_scope.owner_org_id
                              OR sealed_observation.location_id <>
                                  sealed_scope.location_id
                              OR sealed_observation.custodian_person_id_snapshot
                                  IS DISTINCT FROM
                                  sealed_scope.custodian_person_id_snapshot
                              OR sealed_observation.counted_by_user_id <>
                                  sealed_completion.completed_by_user_id
                              OR sealed_observation.counted_at <>
                                  sealed_completion.completed_at
                              OR sealed_observation.created_at <>
                                  sealed_completion.completed_at
                              OR sealed_observation.dimension_sha256
                                  IS DISTINCT FROM ({observation_dimension})
                              OR sealed_observation.request_sha256 <>
                                  sealed_completion.request_sha256
                              OR sealed_observation.idempotency_key_hash
                                  IS DISTINCT FROM ({observation_idempotency})
                          )
                   )
                   OR EXISTS (
                       (
                           SELECT snapshot.stock_account_id
                             FROM public.stocktake_snapshot_lines AS snapshot
                            WHERE snapshot.task_id = {task_id_sql}
                              AND snapshot.scope_id = sealed_completion.scope_id
                       )
                       EXCEPT
                       (
                           SELECT sealed_line.stock_account_id
                             FROM public.stocktake_count_lines AS sealed_line
                            WHERE sealed_line.task_id = {task_id_sql}
                              AND sealed_line.round_id = {round_alias}.id
                              AND sealed_line.scope_id =
                                  sealed_completion.scope_id
                       )
                   )
                   OR EXISTS (
                       (
                           SELECT sealed_line.stock_account_id
                             FROM public.stocktake_count_lines AS sealed_line
                            WHERE sealed_line.task_id = {task_id_sql}
                              AND sealed_line.round_id = {round_alias}.id
                              AND sealed_line.scope_id =
                                  sealed_completion.scope_id
                       )
                       EXCEPT
                       (
                           SELECT snapshot.stock_account_id
                             FROM public.stocktake_snapshot_lines AS snapshot
                            WHERE snapshot.task_id = {task_id_sql}
                              AND snapshot.scope_id = sealed_completion.scope_id
                       )
                   )
                   OR (
                       {round_alias}.round_type = 'initial'
                       AND EXISTS (
                           SELECT 1
                             FROM public.stocktake_recount_scope_assignments
                                  AS unexpected_assignment
                            WHERE unexpected_assignment.task_id = {task_id_sql}
                              AND unexpected_assignment.source_round_id =
                                  {round_alias}.id
                              AND unexpected_assignment.scope_id =
                                  sealed_completion.scope_id
                       )
                   )
                   OR (
                       {round_alias}.round_type = 'recount'
                       AND (
                           SELECT pg_catalog.count(*)
                             FROM public.stocktake_recount_scope_assignments
                                  AS count_assignment
                            WHERE count_assignment.recount_case_id =
                                  {round_alias}.recount_case_id
                              AND count_assignment.task_id = {task_id_sql}
                              AND count_assignment.scope_id =
                                  sealed_completion.scope_id
                              AND count_assignment.assignee_user_id =
                                  sealed_completion.completed_by_user_id
                              AND count_assignment.assignee_person_id =
                                  sealed_completion.completed_by_person_id
                              AND count_assignment.assignee_role_assignment_id =
                                  sealed_completion.completed_role_assignment_id
                              AND sealed_completion.authorization_version >=
                                  count_assignment.authorization_version
                              AND count_assignment.role_code =
                                  sealed_completion.role_code
                              AND count_assignment.scope_type =
                                  sealed_completion.scope_type
                              AND count_assignment.scope_id_snapshot =
                                  sealed_completion.scope_id_snapshot
                              AND sealed_completion.completed_at >=
                                  count_assignment.assigned_at
                       ) <> 1
                   )
               )
        )
        AND EXISTS (
            SELECT 1
              FROM public.stocktake_scope_count_completions AS sealing
             WHERE sealing.id = {submission_alias}.sealing_completion_id
               AND sealing.task_id = {task_id_sql}
               AND sealing.round_id = {round_alias}.id
               AND sealing.completed_by_user_id =
                   {submission_alias}.submitted_by_user_id
               AND sealing.completed_by_person_id =
                   {submission_alias}.submitted_by_person_id
               AND sealing.completed_role_assignment_id =
                   {submission_alias}.submitted_role_assignment_id
               AND sealing.authorization_version =
                   {submission_alias}.authorization_version
               AND sealing.completed_at = {submission_alias}.submitted_at
               AND {submitter_actor}
        )
        AND {submission_alias}.round_manifest_sha256 = {round_manifest}
        AND {submission_alias}.count_manifest_sha256 = {count_manifest}
        AND {round_alias}.count_manifest_sha256 = {count_manifest}
        AND {submission_alias}.request_sha256 = {submission_request}
        AND {submission_alias}.idempotency_key_hash = {submission_idempotency}
        AND {difference_completion}
        AND {submission_side_effects}
    )"""


def _nullable_json_text_sql(expression: str) -> str:
    return (
        f"CASE WHEN {expression} IS NULL THEN 'null' "
        f"ELSE {_json_text_sql(expression)} END"
    )


def _canonical_quantity_sql(expression: str) -> str:
    return f"pg_catalog.trim_scale(({expression})::numeric)::text"


def _difference_manifest_sha256_sql(
    task_id_sql: str,
    round_alias: str,
    submission_alias: str,
) -> str:
    item = f"""(
        '{{"affected_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('manifest_difference.affected_qty'))} ||
        ',"book_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('manifest_difference.book_qty'))} ||
        ',"control_snapshot_line_id":' ||
        {_nullable_json_text_sql('manifest_difference.control_snapshot_line_id::text')} ||
        ',"counted_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('manifest_difference.counted_qty'))} ||
        ',"created_at":' ||
        {_timestamp_json_sql('manifest_difference.created_at')} ||
        ',"difference_id":' ||
        {_json_text_sql('manifest_difference.id::text')} ||
        ',"difference_no":' || manifest_difference.difference_no::text ||
        ',"difference_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('manifest_difference.difference_qty'))} ||
        ',"difference_type":' ||
        {_json_text_sql('manifest_difference.difference_type')} ||
        ',"evidence_required":' ||
        manifest_difference.evidence_required::text ||
        ',"expected_account_id":' ||
        {_nullable_json_text_sql('manifest_difference.expected_account_id::text')} ||
        ',"material_id":' ||
        {_nullable_json_text_sql('manifest_difference.material_id::text')} ||
        ',"observed_account_id":' ||
        {_nullable_json_text_sql('manifest_difference.observed_account_id::text')} ||
        ',"observed_line_id":' ||
        {_nullable_json_text_sql('manifest_difference.observed_line_id::text')} ||
        ',"reason_code":' ||
        {_nullable_json_text_sql('manifest_difference.reason_code')} ||
        ',"reason_text":' ||
        {_json_text_sql('manifest_difference.reason_text')} ||
        ',"scope_id":' ||
        {_nullable_json_text_sql('manifest_difference.scope_id::text')} ||
        ',"serial_id":' ||
        {_nullable_json_text_sql('manifest_difference.serial_id::text')} ||
        '}}'
    )"""
    document = f"""(
        '{{"differences":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {item},
                       ',' ORDER BY manifest_difference.difference_no
                   )
              FROM public.stocktake_differences AS manifest_difference
             WHERE manifest_difference.task_id = {task_id_sql}
               AND manifest_difference.round_id = {round_alias}.id
        ), '') ||
        '],"round_id":' || {_json_text_sql(f'{round_alias}.id::text')} ||
        ',"round_submission_id":' ||
        {_json_text_sql(f'{submission_alias}.id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.difference_set.v1"' ||
        ',"task_id":' || {_json_text_sql(f'{task_id_sql}::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _review_decision_manifest_sha256_sql(
    task_id_sql: str,
    round_alias: str,
    review_alias: str,
) -> str:
    """Recompute the exact persisted opening review decision manifest."""

    item = f"""(
        '{{"affected_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('decision_difference.affected_qty'))} ||
        ',"book_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('decision_difference.book_qty'))} ||
        ',"control_snapshot_line_id":' ||
        {_nullable_json_text_sql('decision_difference.control_snapshot_line_id::text')} ||
        ',"counted_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('decision_difference.counted_qty'))} ||
        ',"decision":' || {_json_text_sql('decision_item.decision')} ||
        ',"difference_id":' ||
        {_json_text_sql('decision_difference.id::text')} ||
        ',"difference_no":' || decision_difference.difference_no::text ||
        ',"difference_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('decision_difference.difference_qty'))} ||
        ',"difference_type":' ||
        {_json_text_sql('decision_difference.difference_type')} ||
        ',"evidence_required":' ||
        decision_difference.evidence_required::text ||
        ',"expected_account_id":' ||
        {_nullable_json_text_sql('decision_difference.expected_account_id::text')} ||
        ',"material_id":' ||
        {_nullable_json_text_sql('decision_difference.material_id::text')} ||
        ',"observed_account_id":' ||
        {_nullable_json_text_sql('decision_difference.observed_account_id::text')} ||
        ',"reason_code":' ||
        {_nullable_json_text_sql('decision_difference.reason_code')} ||
        ',"reason_text":' ||
        {_json_text_sql('decision_difference.reason_text')} ||
        ',"scope_id":' ||
        {_nullable_json_text_sql('decision_difference.scope_id::text')} ||
        ',"serial_id":' ||
        {_nullable_json_text_sql('decision_difference.serial_id::text')} ||
        '}}'
    )"""
    document = f"""(
        '{{"items":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {item},
                       ',' ORDER BY decision_difference.difference_no,
                                    decision_difference.id::text
                   )
              FROM public.stocktake_differences AS decision_difference
              JOIN public.stocktake_review_items AS decision_item
                ON decision_item.review_id = {review_alias}.id
               AND decision_item.difference_id = decision_difference.id
               AND decision_item.task_id = {task_id_sql}
               AND decision_item.round_id = {round_alias}.id
             WHERE decision_difference.task_id = {task_id_sql}
               AND decision_difference.round_id = {round_alias}.id
        ), '') ||
        '],"round_id":' || {_json_text_sql(f'{round_alias}.id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.decision_manifest.v1"' ||
        ',"task_id":' || {_json_text_sql(f'{task_id_sql}::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _difference_completion_request_sha256_sql(
    task_id_sql: str,
    round_alias: str,
    submission_alias: str,
) -> str:
    difference_manifest = _difference_manifest_sha256_sql(
        task_id_sql,
        round_alias,
        submission_alias,
    )
    document = f"""(
        '{{"control_difference_count":' || (
            SELECT pg_catalog.count(*) FILTER (
                       WHERE request_difference.difference_type =
                           'control_unassigned'
                   )::text
              FROM public.stocktake_differences AS request_difference
             WHERE request_difference.task_id = {task_id_sql}
               AND request_difference.round_id = {round_alias}.id
        ) ||
        ',"difference_count":' || (
            SELECT pg_catalog.count(*)::text
              FROM public.stocktake_differences AS request_difference
             WHERE request_difference.task_id = {task_id_sql}
               AND request_difference.round_id = {round_alias}.id
        ) ||
        ',"difference_manifest_sha256":' ||
        {_json_text_sql(difference_manifest)} ||
        ',"pending_observation_difference_count":' || (
            SELECT pg_catalog.count(*) FILTER (
                       WHERE request_difference.observed_line_id IS NOT NULL
                         AND request_difference.reason_code =
                             'opening_pending_verification'
                   )::text
              FROM public.stocktake_differences AS request_difference
             WHERE request_difference.task_id = {task_id_sql}
               AND request_difference.round_id = {round_alias}.id
        ) ||
        ',"physical_difference_count":' || (
            SELECT pg_catalog.count(*) FILTER (
                       WHERE request_difference.difference_type <>
                           'control_unassigned'
                   )::text
              FROM public.stocktake_differences AS request_difference
             WHERE request_difference.task_id = {task_id_sql}
               AND request_difference.round_id = {round_alias}.id
        ) ||
        ',"round_id":' || {_json_text_sql(f'{round_alias}.id::text')} ||
        ',"round_submission_id":' ||
        {_json_text_sql(f'{submission_alias}.id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.difference_set_completion_request.v1"' ||
        ',"task_id":' || {_json_text_sql(f'{task_id_sql}::text')} ||
        ',"total_affected_qty":' || {_json_text_sql(
            _canonical_quantity_sql(
                f"(SELECT COALESCE(pg_catalog.sum(request_difference.affected_qty), 0) "
                "FROM public.stocktake_differences AS request_difference "
                f"WHERE request_difference.task_id = {task_id_sql} "
                f"AND request_difference.round_id = {round_alias}.id)"
            )
        )} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _difference_completion_idempotency_sha256_sql(
    round_alias: str,
    submission_alias: str,
) -> str:
    return f"""pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                'cloud_oam.opening_stocktake.difference-set-completion.v1',
                'UTF8'
            ) || pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to({round_alias}.id::text, 'UTF8') ||
            pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to({submission_alias}.id::text, 'UTF8')
        ),
        'hex'
    )"""


def _expected_difference_set_proof_sql(
    task_id_sql: str,
    round_alias: str,
) -> str:
    """Rebuild the deterministic opening difference rows from sealed facts."""

    return f"""(
        NOT EXISTS (
            SELECT 1
              FROM (
                  SELECT control.material_id,
                         control.condition_code
                    FROM public.stocktake_control_snapshot_lines AS control
                   WHERE control.task_id = {task_id_sql}
                     AND control.mapping_status = 'resolved'
                     AND control.material_id IS NOT NULL
                     AND control.condition_code IS NOT NULL
                   GROUP BY control.material_id, control.condition_code
                  HAVING pg_catalog.count(*) <> 1
                     AND COALESCE(pg_catalog.sum(control.control_qty), 0)
                         IS DISTINCT FROM (
                             COALESCE((
                                 SELECT pg_catalog.sum(line.counted_qty)
                                   FROM public.stocktake_count_lines AS line
                                   JOIN public.stock_accounts AS account
                                     ON account.id = line.stock_account_id
                                  WHERE line.task_id = {task_id_sql}
                                    AND line.round_id = {round_alias}.id
                                    AND account.material_id = control.material_id
                                    AND account.condition_code =
                                        control.condition_code
                             ), 0) + COALESCE((
                                 SELECT pg_catalog.sum(observation.counted_qty)
                                   FROM public.stocktake_count_observations
                                        AS observation
                                  WHERE observation.task_id = {task_id_sql}
                                    AND observation.round_id = {round_alias}.id
                                    AND observation.material_id =
                                        control.material_id
                                    AND observation.condition_code =
                                        control.condition_code
                             ), 0)
                         )::numeric(18, 3)
              ) AS ambiguous_control
        )
        AND NOT EXISTS (
            WITH expected_base AS (
                SELECT 1::integer AS group_no,
                       scope.scope_no AS sort_scope_no,
                       0::bigint AS sort_no,
                       account.id::text AS sort_text_one,
                       ''::text AS sort_text_two,
                       line.scope_id,
                       NULL::uuid AS control_snapshot_line_id,
                       'excess'::text AS difference_type,
                       account.material_id,
                       NULL::uuid AS expected_account_id,
                       account.id AS observed_account_id,
                       NULL::uuid AS observed_line_id,
                       NULL::uuid AS serial_id,
                       0::numeric(18, 3) AS book_qty,
                       line.counted_qty::numeric(18, 3) AS counted_qty,
                       line.counted_qty::numeric(18, 3) AS difference_qty,
                       line.counted_qty::numeric(18, 3) AS affected_qty,
                       'opening_physical_excess'::text AS reason_code,
                       '期初实物盘点数量，仅待复核后建立个人仓库存'::text
                           AS reason_text,
                       TRUE AS evidence_required
                  FROM public.stocktake_count_lines AS line
                  JOIN public.stocktake_scopes AS scope
                    ON scope.id = line.scope_id
                   AND scope.task_id = {task_id_sql}
                  JOIN public.stock_accounts AS account
                    ON account.id = line.stock_account_id
                 WHERE line.task_id = {task_id_sql}
                   AND line.round_id = {round_alias}.id
                   AND line.counted_qty > 0
                UNION ALL
                SELECT 2::integer,
                       scope.scope_no,
                       observation.observation_no::bigint,
                       ''::text,
                       ''::text,
                       observation.scope_id,
                       NULL::uuid,
                       'excess'::text,
                       observation.material_id,
                       NULL::uuid,
                       NULL::uuid,
                       observation.id,
                       observation.serial_id,
                       0::numeric(18, 3),
                       observation.counted_qty::numeric(18, 3),
                       observation.counted_qty::numeric(18, 3),
                       observation.counted_qty::numeric(18, 3),
                       CASE observation.verification_status
                           WHEN 'pending_verification'
                           THEN 'opening_pending_verification'
                           ELSE 'opening_unexpected_dimension'
                       END,
                       CASE observation.verification_status
                           WHEN 'pending_verification'
                           THEN '现场实物标识尚未唯一解析，保留为不可过账待核实差异'
                           ELSE '现场实物维度在截止快照中不存在，待复核后处理'
                       END,
                       TRUE
                  FROM public.stocktake_count_observations AS observation
                  JOIN public.stocktake_scopes AS scope
                    ON scope.id = observation.scope_id
                   AND scope.task_id = {task_id_sql}
                 WHERE observation.task_id = {task_id_sql}
                   AND observation.round_id = {round_alias}.id
                UNION ALL
                SELECT 3::integer,
                       0::integer,
                       control.line_no::bigint,
                       ''::text,
                       ''::text,
                       NULL::uuid,
                       control.id,
                       'control_unassigned'::text,
                       control.material_id,
                       NULL::uuid,
                       NULL::uuid,
                       NULL::uuid,
                       NULL::uuid,
                       control.control_qty::numeric(18, 3),
                       0::numeric(18, 3),
                       (-control.control_qty)::numeric(18, 3),
                       control.control_qty::numeric(18, 3),
                       'opening_control_reconciliation'::text,
                       'OAM 控制行无法唯一映射到本地实物维度'::text,
                       TRUE
                  FROM public.stocktake_control_snapshot_lines AS control
                 WHERE control.task_id = {task_id_sql}
                   AND (
                       control.mapping_status <> 'resolved'
                       OR control.material_id IS NULL
                       OR control.condition_code IS NULL
                   )
                   AND control.control_qty > 0
                UNION ALL
                SELECT 4::integer,
                       0::integer,
                       0::bigint,
                       control.material_id::text,
                       control.condition_code,
                       NULL::uuid,
                       pg_catalog.min(control.id::text)::uuid,
                       'control_unassigned'::text,
                       control.material_id,
                       NULL::uuid,
                       NULL::uuid,
                       NULL::uuid,
                       NULL::uuid,
                       pg_catalog.sum(control.control_qty)::numeric(18, 3),
                       (
                           COALESCE((
                               SELECT pg_catalog.sum(line.counted_qty)
                                 FROM public.stocktake_count_lines AS line
                                 JOIN public.stock_accounts AS account
                                   ON account.id = line.stock_account_id
                                WHERE line.task_id = {task_id_sql}
                                  AND line.round_id = {round_alias}.id
                                  AND account.material_id = control.material_id
                                  AND account.condition_code =
                                      control.condition_code
                           ), 0) + COALESCE((
                               SELECT pg_catalog.sum(observation.counted_qty)
                                 FROM public.stocktake_count_observations
                                      AS observation
                                WHERE observation.task_id = {task_id_sql}
                                  AND observation.round_id = {round_alias}.id
                                  AND observation.material_id =
                                      control.material_id
                                  AND observation.condition_code =
                                      control.condition_code
                           ), 0)
                       )::numeric(18, 3),
                       (
                           COALESCE((
                               SELECT pg_catalog.sum(line.counted_qty)
                                 FROM public.stocktake_count_lines AS line
                                 JOIN public.stock_accounts AS account
                                   ON account.id = line.stock_account_id
                                WHERE line.task_id = {task_id_sql}
                                  AND line.round_id = {round_alias}.id
                                  AND account.material_id = control.material_id
                                  AND account.condition_code =
                                      control.condition_code
                           ), 0) + COALESCE((
                               SELECT pg_catalog.sum(observation.counted_qty)
                                 FROM public.stocktake_count_observations
                                      AS observation
                                WHERE observation.task_id = {task_id_sql}
                                  AND observation.round_id = {round_alias}.id
                                  AND observation.material_id =
                                      control.material_id
                                  AND observation.condition_code =
                                      control.condition_code
                           ), 0) - pg_catalog.sum(control.control_qty)
                       )::numeric(18, 3),
                       pg_catalog.abs((
                           COALESCE((
                               SELECT pg_catalog.sum(line.counted_qty)
                                 FROM public.stocktake_count_lines AS line
                                 JOIN public.stock_accounts AS account
                                   ON account.id = line.stock_account_id
                                WHERE line.task_id = {task_id_sql}
                                  AND line.round_id = {round_alias}.id
                                  AND account.material_id = control.material_id
                                  AND account.condition_code =
                                      control.condition_code
                           ), 0) + COALESCE((
                               SELECT pg_catalog.sum(observation.counted_qty)
                                 FROM public.stocktake_count_observations
                                      AS observation
                                WHERE observation.task_id = {task_id_sql}
                                  AND observation.round_id = {round_alias}.id
                                  AND observation.material_id =
                                      control.material_id
                                  AND observation.condition_code =
                                      control.condition_code
                           ), 0) - pg_catalog.sum(control.control_qty)
                       ))::numeric(18, 3),
                       'opening_control_reconciliation'::text,
                       'OAM 省级控制数量与期初实物汇总不一致，仅用于对账'::text,
                       TRUE
                  FROM public.stocktake_control_snapshot_lines AS control
                 WHERE control.task_id = {task_id_sql}
                   AND control.mapping_status = 'resolved'
                   AND control.material_id IS NOT NULL
                   AND control.condition_code IS NOT NULL
                 GROUP BY control.material_id, control.condition_code
                HAVING pg_catalog.count(*) = 1
                   AND pg_catalog.sum(control.control_qty) IS DISTINCT FROM (
                       COALESCE((
                           SELECT pg_catalog.sum(line.counted_qty)
                             FROM public.stocktake_count_lines AS line
                             JOIN public.stock_accounts AS account
                               ON account.id = line.stock_account_id
                            WHERE line.task_id = {task_id_sql}
                              AND line.round_id = {round_alias}.id
                              AND account.material_id = control.material_id
                              AND account.condition_code = control.condition_code
                       ), 0) + COALESCE((
                           SELECT pg_catalog.sum(observation.counted_qty)
                             FROM public.stocktake_count_observations
                                  AS observation
                            WHERE observation.task_id = {task_id_sql}
                              AND observation.round_id = {round_alias}.id
                              AND observation.material_id = control.material_id
                              AND observation.condition_code =
                                  control.condition_code
                       ), 0)
                   )::numeric(18, 3)
            ), expected AS (
                SELECT pg_catalog.row_number() OVER (
                           ORDER BY group_no, sort_scope_no, sort_no,
                                    sort_text_one, sort_text_two
                       )::integer AS difference_no,
                       expected_base.*
                  FROM expected_base
            ), actual AS (
                SELECT difference.*
                  FROM public.stocktake_differences AS difference
                 WHERE difference.task_id = {task_id_sql}
                   AND difference.round_id = {round_alias}.id
            )
            SELECT 1
              FROM expected
              FULL OUTER JOIN actual
                ON actual.difference_no = expected.difference_no
             WHERE expected.difference_no IS NULL
                OR actual.id IS NULL
                OR actual.scope_id IS DISTINCT FROM expected.scope_id
                OR actual.control_snapshot_line_id IS DISTINCT FROM
                   expected.control_snapshot_line_id
                OR actual.difference_type IS DISTINCT FROM
                   expected.difference_type
                OR actual.material_id IS DISTINCT FROM expected.material_id
                OR actual.expected_account_id IS DISTINCT FROM
                   expected.expected_account_id
                OR actual.observed_account_id IS DISTINCT FROM
                   expected.observed_account_id
                OR actual.observed_line_id IS DISTINCT FROM
                   expected.observed_line_id
                OR actual.serial_id IS DISTINCT FROM expected.serial_id
                OR actual.book_qty IS DISTINCT FROM expected.book_qty
                OR actual.counted_qty IS DISTINCT FROM expected.counted_qty
                OR actual.difference_qty IS DISTINCT FROM
                   expected.difference_qty
                OR actual.affected_qty IS DISTINCT FROM expected.affected_qty
                OR actual.reason_code IS DISTINCT FROM expected.reason_code
                OR actual.reason_text IS DISTINCT FROM expected.reason_text
                OR actual.evidence_required IS DISTINCT FROM
                   expected.evidence_required
                OR actual.created_at IS DISTINCT FROM {round_alias}.submitted_at
        )
    )"""


def _review_difference_completion_proof_sql(
    task_id_sql: str,
    round_alias: str,
    submission_alias: str,
    review_alias: str | None,
) -> str:
    """Bind a round/review to its unique deterministic difference set."""

    difference_manifest = _difference_manifest_sha256_sql(
        task_id_sql,
        round_alias,
        submission_alias,
    )
    difference_request = _difference_completion_request_sha256_sql(
        task_id_sql,
        round_alias,
        submission_alias,
    )
    difference_idempotency = _difference_completion_idempotency_sha256_sql(
        round_alias,
        submission_alias,
    )
    expected_difference_set = _expected_difference_set_proof_sql(
        task_id_sql,
        round_alias,
    )
    completion_chronology = (
        f"review_completion.completed_at <= {review_alias}.reviewed_at"
        if review_alias is not None
        else (
            "review_completion.completed_at = "
            f"{submission_alias}.submitted_at"
        )
    )
    return f"""(
        SELECT pg_catalog.count(*)
          FROM public.stocktake_difference_set_completions
               AS review_completion
          JOIN public.stocktake_scope_count_completions AS review_sealing
            ON review_sealing.id = {submission_alias}.sealing_completion_id
           AND review_sealing.task_id = {task_id_sql}
           AND review_sealing.round_id = {round_alias}.id
         WHERE review_completion.task_id = {task_id_sql}
           AND review_completion.round_id = {round_alias}.id
           AND review_completion.round_submission_id = {submission_alias}.id
           AND {completion_chronology}
           AND review_completion.created_at = review_completion.completed_at
           AND review_completion.completed_by_user_id =
               {submission_alias}.submitted_by_user_id
           AND review_completion.completed_by_person_id =
               {submission_alias}.submitted_by_person_id
           AND review_completion.completed_role_assignment_id =
               {submission_alias}.submitted_role_assignment_id
           AND review_completion.authorization_version =
               {submission_alias}.authorization_version
           AND review_completion.role_code = review_sealing.role_code
           AND review_completion.scope_type = review_sealing.scope_type
           AND review_completion.scope_id_snapshot =
               review_sealing.scope_id_snapshot
           AND review_completion.authorization_sha256 =
               review_sealing.authorization_sha256
           AND review_completion.difference_count = (
               SELECT pg_catalog.count(*)
                 FROM public.stocktake_differences AS reviewed_difference
                WHERE reviewed_difference.task_id = {task_id_sql}
                  AND reviewed_difference.round_id = {round_alias}.id
           )
           AND review_completion.physical_difference_count = (
               SELECT pg_catalog.count(*) FILTER (
                          WHERE reviewed_difference.difference_type <>
                              'control_unassigned'
                      )
                 FROM public.stocktake_differences AS reviewed_difference
                WHERE reviewed_difference.task_id = {task_id_sql}
                  AND reviewed_difference.round_id = {round_alias}.id
           )
           AND review_completion.control_difference_count = (
               SELECT pg_catalog.count(*) FILTER (
                          WHERE reviewed_difference.difference_type =
                              'control_unassigned'
                      )
                 FROM public.stocktake_differences AS reviewed_difference
                WHERE reviewed_difference.task_id = {task_id_sql}
                  AND reviewed_difference.round_id = {round_alias}.id
           )
           AND review_completion.pending_observation_difference_count = (
               SELECT pg_catalog.count(*) FILTER (
                          WHERE reviewed_difference.observed_line_id IS NOT NULL
                            AND reviewed_difference.reason_code =
                                'opening_pending_verification'
                      )
                 FROM public.stocktake_differences AS reviewed_difference
                WHERE reviewed_difference.task_id = {task_id_sql}
                  AND reviewed_difference.round_id = {round_alias}.id
           )
           AND review_completion.total_affected_qty = (
               SELECT COALESCE(
                          pg_catalog.sum(reviewed_difference.affected_qty),
                          0
                      )::numeric(18, 3)
                 FROM public.stocktake_differences AS reviewed_difference
                WHERE reviewed_difference.task_id = {task_id_sql}
                  AND reviewed_difference.round_id = {round_alias}.id
           )
           AND review_completion.difference_manifest_sha256 =
               {difference_manifest}
           AND review_completion.request_sha256 = {difference_request}
           AND review_completion.idempotency_key_hash =
               {difference_idempotency}
           AND {expected_difference_set}
    ) = 1"""


def _recount_assignment_manifest_sha256_sql(case_alias: str) -> str:
    item = """(
        '{"assignment_sha256":' ||
        pg_catalog.to_json(manifest_assignment.assignment_sha256::text)::text ||
        ',"authorization_sha256":' ||
        pg_catalog.to_json(manifest_assignment.authorization_sha256::text)::text ||
        ',"scope_id":' ||
        pg_catalog.to_json(manifest_assignment.scope_id::text)::text ||
        '}'
    )"""
    document = f"""(
        '{{"assignments":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {item},
                       ',' ORDER BY manifest_assignment.scope_id::text
                   )
              FROM public.stocktake_recount_scope_assignments
                   AS manifest_assignment
             WHERE manifest_assignment.recount_case_id = {case_alias}.id
        ), '') ||
        '],"schema":"cloud_oam.opening_stocktake.recount_assignment_manifest.v1"}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _recount_request_sha256_sql(case_alias: str) -> str:
    item = """(
        '{"assignee_user_id":' ||
        pg_catalog.to_json(request_assignment.assignee_user_id::text)::text ||
        ',"scope_id":' ||
        pg_catalog.to_json(request_assignment.scope_id::text)::text ||
        '}'
    )"""
    document = f"""(
        '{{"actor_person_id":' ||
        {_json_text_sql(f'{case_alias}.opened_by_person_id::text')} ||
        ',"actor_user_id":' ||
        {_json_text_sql(f'{case_alias}.opened_by_user_id')} ||
        ',"assignments":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {item},
                       ',' ORDER BY request_assignment.scope_id::text
                   )
              FROM public.stocktake_recount_scope_assignments
                   AS request_assignment
             WHERE request_assignment.recount_case_id = {case_alias}.id
        ), '') ||
        '],"reason":' || {_json_text_sql(f'{case_alias}.reason')} ||
        ',"schema":"cloud_oam.opening_stocktake.recount_request.v1"' ||
        ',"source_round_id":' ||
        {_json_text_sql(f'{case_alias}.source_round_id::text')} ||
        ',"task_id":' || {_json_text_sql(f'{case_alias}.task_id::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _recount_manifest_sha256_sql(
    case_alias: str,
    source_round_alias: str,
    submission_alias: str,
    completion_alias: str,
    trigger_review_alias: str,
) -> str:
    assignment_manifest = _recount_assignment_manifest_sha256_sql(case_alias)
    request_sha256 = _recount_request_sha256_sql(case_alias)
    opener_authorization = _recount_authorization_sha256_sql(
        case_alias,
        authorization_kind="opener",
        assignee=False,
    )
    document = f"""(
        '{{"assignment_manifest_sha256":' ||
        {_json_text_sql(assignment_manifest)} ||
        ',"authorization_sha256":' || {_json_text_sql(opener_authorization)} ||
        ',"next_round_no":' || {case_alias}.next_round_no::text ||
        ',"opened_at":' || {_timestamp_json_sql(f'{case_alias}.opened_at')} ||
        ',"reason":' || {_json_text_sql(f'{case_alias}.reason')} ||
        ',"recount_case_id":' || {_json_text_sql(f'{case_alias}.id::text')} ||
        ',"request_sha256":' || {_json_text_sql(request_sha256)} ||
        ',"schema":"cloud_oam.opening_stocktake.recount_manifest.v1"' ||
        ',"scope_count":' || {case_alias}.scope_count::text ||
        ',"scope_manifest_sha256":' ||
        {_json_text_sql(f'{case_alias}.scope_manifest_sha256')} ||
        ',"source_count_manifest_sha256":' ||
        {_json_text_sql(f'{source_round_alias}.count_manifest_sha256')} ||
        ',"source_difference_completion_id":' ||
        {_json_text_sql(f'{completion_alias}.id::text')} ||
        ',"source_difference_manifest_sha256":' ||
        {_json_text_sql(f'{completion_alias}.difference_manifest_sha256')} ||
        ',"source_round_id":' ||
        {_json_text_sql(f'{source_round_alias}.id::text')} ||
        ',"source_round_manifest_sha256":' ||
        {_json_text_sql(f'{submission_alias}.round_manifest_sha256')} ||
        ',"source_round_submission_id":' ||
        {_json_text_sql(f'{submission_alias}.id::text')} ||
        ',"task_id":' || {_json_text_sql(f'{case_alias}.task_id::text')} ||
        ',"trigger_decision_manifest_sha256":' ||
        {_json_text_sql(f'{trigger_review_alias}.decision_manifest_sha256')} ||
        ',"trigger_review_id":' ||
        {_json_text_sql(f'{trigger_review_alias}.id::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _recount_trigger_review_chain_proof_sql(
    *,
    task_id_sql: str,
    region_org_id_sql: str,
    case_alias: str,
    source_round_alias: str,
    submission_alias: str,
    trigger_review_alias: str,
) -> str:
    """Prove the complete immutable review chain that caused a recount."""

    trigger_region_actor = _historical_authorization_sql(
        user_id_sql=f"{trigger_review_alias}.reviewer_user_id",
        person_id_sql=f"{trigger_review_alias}.reviewer_person_id",
        assignment_id_sql=(
            f"{trigger_review_alias}.reviewer_role_assignment_id"
        ),
        authorization_version_sql=(
            f"{trigger_review_alias}.authorization_version"
        ),
        occurred_at_sql=f"{trigger_review_alias}.reviewed_at",
        role_code_sql="'provincial_manager'",
        scope_type_sql="'organization'",
        scope_id_sql=f"({region_org_id_sql})::text",
        alias_suffix="trigger_region_reviewer",
    )
    trigger_headquarters_actor = _historical_authorization_sql(
        user_id_sql=f"{trigger_review_alias}.reviewer_user_id",
        person_id_sql=f"{trigger_review_alias}.reviewer_person_id",
        assignment_id_sql=(
            f"{trigger_review_alias}.reviewer_role_assignment_id"
        ),
        authorization_version_sql=(
            f"{trigger_review_alias}.authorization_version"
        ),
        occurred_at_sql=f"{trigger_review_alias}.reviewed_at",
        role_code_sql="'admin'",
        scope_type_sql="'national'",
        scope_id_sql="'*'",
        alias_suffix="trigger_headquarters_reviewer",
    )
    source_region_actor = _historical_authorization_sql(
        user_id_sql="source_region_review.reviewer_user_id",
        person_id_sql="source_region_review.reviewer_person_id",
        assignment_id_sql=(
            "source_region_review.reviewer_role_assignment_id"
        ),
        authorization_version_sql=(
            "source_region_review.authorization_version"
        ),
        occurred_at_sql="source_region_review.reviewed_at",
        role_code_sql="'provincial_manager'",
        scope_type_sql="'organization'",
        scope_id_sql=f"({region_org_id_sql})::text",
        alias_suffix="source_region_reviewer",
    )
    trigger_coverage = _review_item_coverage_by_id_sql(
        trigger_review_alias,
        task_id_sql,
        source_round_alias,
    )
    trigger_items = _opening_review_item_rules_sql(
        trigger_review_alias,
        source_round_alias,
        historical=True,
    )
    trigger_completion = _review_difference_completion_proof_sql(
        task_id_sql,
        source_round_alias,
        submission_alias,
        trigger_review_alias,
    )
    source_region_coverage = _review_item_coverage_by_id_sql(
        "source_region_review",
        task_id_sql,
        source_round_alias,
    )
    source_region_items = _opening_review_item_rules_sql(
        "source_region_review",
        source_round_alias,
        historical=True,
    )
    source_region_completion = _review_difference_completion_proof_sql(
        task_id_sql,
        source_round_alias,
        submission_alias,
        "source_region_review",
    )
    return f"""(
        {trigger_review_alias}.task_id = {task_id_sql}
        AND {trigger_review_alias}.round_id = {source_round_alias}.id
        AND {trigger_review_alias}.created_at =
            {trigger_review_alias}.reviewed_at
        AND {trigger_review_alias}.reviewed_at >=
            {submission_alias}.submitted_at
        AND {trigger_review_alias}.reviewed_at <= {case_alias}.opened_at
        AND {trigger_completion}
        AND {trigger_coverage}
        AND {trigger_items}
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_reviews AS later_review
             WHERE later_review.task_id = {task_id_sql}
               AND later_review.round_id = {source_round_alias}.id
               AND later_review.reviewed_at >
                   {trigger_review_alias}.reviewed_at
        )
        AND (
            (
                {trigger_review_alias}.review_stage = 'region'
                AND {trigger_review_alias}.decision IN ('recount', 'reject')
                AND {trigger_region_actor}
                AND NOT EXISTS (
                    SELECT 1
                      FROM public.stocktake_reviews
                           AS unexpected_headquarters_review
                     WHERE unexpected_headquarters_review.task_id =
                           {task_id_sql}
                       AND unexpected_headquarters_review.round_id =
                           {source_round_alias}.id
                       AND unexpected_headquarters_review.review_stage =
                           'headquarters'
                )
            )
            OR (
                {trigger_review_alias}.review_stage = 'headquarters'
                AND {trigger_review_alias}.decision = 'reject'
                AND {trigger_headquarters_actor}
                AND (
                    SELECT pg_catalog.count(*)
                      FROM public.stocktake_reviews AS source_region_review
                     WHERE source_region_review.task_id = {task_id_sql}
                       AND source_region_review.round_id =
                           {source_round_alias}.id
                       AND source_region_review.review_stage = 'region'
                       AND source_region_review.decision = 'approve'
                       AND source_region_review.created_at =
                           source_region_review.reviewed_at
                       AND source_region_review.reviewed_at >=
                           {submission_alias}.submitted_at
                       AND source_region_review.reviewed_at <
                           {trigger_review_alias}.reviewed_at
                       AND source_region_review.reviewer_user_id <>
                           {trigger_review_alias}.reviewer_user_id
                       AND source_region_review.reviewer_person_id <>
                           {trigger_review_alias}.reviewer_person_id
                       AND source_region_review.reviewer_role_assignment_id <>
                           {trigger_review_alias}.reviewer_role_assignment_id
                       AND {source_region_actor}
                       AND {source_region_completion}
                       AND {source_region_coverage}
                       AND {source_region_items}
                ) = 1
            )
        )
    )"""


def _recount_case_replay_proof_sql(
    *,
    task_id_sql: str,
    region_org_id_sql: str,
    task_scope_manifest_sql: str,
    case_alias: str,
    source_round_alias: str,
    submission_alias: str,
    sealing_alias: str,
    completion_alias: str,
    trigger_review_alias: str,
    historical: bool,
) -> str:
    """Recompute one opening recount edge, its actors and all assignments."""

    opener_hash = _recount_authorization_sha256_sql(
        case_alias,
        authorization_kind="opener",
        assignee=False,
    )
    assignment_hash = _recount_assignment_sha256_sql("replay_assignment")
    assignment_auth_hash = _recount_authorization_sha256_sql(
        "replay_assignment",
        authorization_kind="assignee",
        assignee=True,
    )
    assignment_manifest = _recount_assignment_manifest_sha256_sql(case_alias)
    request_hash = _recount_request_sha256_sql(case_alias)
    recount_manifest = _recount_manifest_sha256_sql(
        case_alias,
        source_round_alias,
        submission_alias,
        completion_alias,
        trigger_review_alias,
    )
    difference_manifest = _difference_manifest_sha256_sql(
        task_id_sql,
        source_round_alias,
        submission_alias,
    )
    difference_request = _difference_completion_request_sha256_sql(
        task_id_sql,
        source_round_alias,
        submission_alias,
    )
    difference_idempotency = _difference_completion_idempotency_sha256_sql(
        source_round_alias,
        submission_alias,
    )
    expected_difference_set = _expected_difference_set_proof_sql(
        task_id_sql,
        source_round_alias,
    )
    trigger_review_chain = _recount_trigger_review_chain_proof_sql(
        task_id_sql=task_id_sql,
        region_org_id_sql=region_org_id_sql,
        case_alias=case_alias,
        source_round_alias=source_round_alias,
        submission_alias=submission_alias,
        trigger_review_alias=trigger_review_alias,
    )
    if historical:
        opener_actor = _historical_authorization_sql(
            user_id_sql=f"{case_alias}.opened_by_user_id",
            person_id_sql=f"{case_alias}.opened_by_person_id",
            assignment_id_sql=f"{case_alias}.opened_role_assignment_id",
            authorization_version_sql=f"{case_alias}.authorization_version",
            occurred_at_sql=f"{case_alias}.opened_at",
            role_code_sql=f"{case_alias}.role_code",
            scope_type_sql=f"{case_alias}.scope_type",
            scope_id_sql=f"{case_alias}.scope_id_snapshot",
            alias_suffix="opener",
        )
        assignment_actor = _historical_authorization_sql(
            user_id_sql="replay_assignment.assignee_user_id",
            person_id_sql="replay_assignment.assignee_person_id",
            assignment_id_sql=(
                "replay_assignment.assignee_role_assignment_id"
            ),
            authorization_version_sql=(
                "replay_assignment.authorization_version"
            ),
            occurred_at_sql="replay_assignment.assigned_at",
            role_code_sql="replay_assignment.role_code",
            scope_type_sql="replay_assignment.scope_type",
            scope_id_sql="replay_assignment.scope_id_snapshot",
            alias_suffix="assignee",
        )
    else:
        opener_actor = _current_authorization_sql(
            user_id_sql=f"{case_alias}.opened_by_user_id",
            person_id_sql=f"{case_alias}.opened_by_person_id",
            assignment_id_sql=f"{case_alias}.opened_role_assignment_id",
            authorization_version_sql=f"{case_alias}.authorization_version",
            occurred_at_sql=f"{case_alias}.opened_at",
            role_code_sql=f"{case_alias}.role_code",
            scope_type_sql=f"{case_alias}.scope_type",
            scope_id_sql=f"{case_alias}.scope_id_snapshot",
            permission_resource="stocktake",
            permission_action="manage",
            allow_scheduled=False,
            alias_suffix="opener",
        )
        assignment_actor = _current_authorization_sql(
            user_id_sql="replay_assignment.assignee_user_id",
            person_id_sql="replay_assignment.assignee_person_id",
            assignment_id_sql=(
                "replay_assignment.assignee_role_assignment_id"
            ),
            authorization_version_sql=(
                "replay_assignment.authorization_version"
            ),
            occurred_at_sql="replay_assignment.assigned_at",
            role_code_sql="replay_assignment.role_code",
            scope_type_sql="replay_assignment.scope_type",
            scope_id_sql="replay_assignment.scope_id_snapshot",
            permission_resource="stocktake",
            permission_action="count",
            allow_scheduled=False,
            alias_suffix="assignee",
        )
    return f"""(
        {case_alias}.task_id = {task_id_sql}
        AND {case_alias}.source_round_id = {source_round_alias}.id
        AND {case_alias}.source_round_submission_id = {submission_alias}.id
        AND {case_alias}.source_difference_completion_id =
            {completion_alias}.id
        AND {case_alias}.trigger_review_id = {trigger_review_alias}.id
        AND {case_alias}.next_round_no = {source_round_alias}.round_no + 1
        AND {case_alias}.scope_manifest_sha256 = {task_scope_manifest_sql}
        AND {case_alias}.scope_count > 0
        AND {case_alias}.scope_count = (
            SELECT pg_catalog.count(*)
              FROM public.stocktake_scopes AS replay_scope
             WHERE replay_scope.task_id = {task_id_sql}
        )
        AND {case_alias}.scope_count = (
            SELECT pg_catalog.count(*)
              FROM public.stocktake_recount_scope_assignments
                   AS replay_assignment
             WHERE replay_assignment.recount_case_id = {case_alias}.id
        )
        AND {case_alias}.created_at = {case_alias}.opened_at
        AND {trigger_review_alias}.task_id = {task_id_sql}
        AND {trigger_review_alias}.round_id = {source_round_alias}.id
        AND {trigger_review_chain}
        AND {completion_alias}.task_id = {task_id_sql}
        AND {completion_alias}.round_id = {source_round_alias}.id
        AND {completion_alias}.round_submission_id = {submission_alias}.id
        AND {completion_alias}.completed_at = {submission_alias}.submitted_at
        AND {completion_alias}.created_at = {completion_alias}.completed_at
        AND {completion_alias}.completed_at <=
            {trigger_review_alias}.reviewed_at
        AND {trigger_review_alias}.reviewed_at <= {case_alias}.opened_at
        AND {completion_alias}.completed_by_user_id =
            {submission_alias}.submitted_by_user_id
        AND {completion_alias}.completed_by_person_id =
            {submission_alias}.submitted_by_person_id
        AND {completion_alias}.completed_role_assignment_id =
            {submission_alias}.submitted_role_assignment_id
        AND {completion_alias}.authorization_version =
            {submission_alias}.authorization_version
        AND {completion_alias}.role_code = {sealing_alias}.role_code
        AND {completion_alias}.scope_type = {sealing_alias}.scope_type
        AND {completion_alias}.scope_id_snapshot =
            {sealing_alias}.scope_id_snapshot
        AND {completion_alias}.authorization_sha256 =
            {sealing_alias}.authorization_sha256
        AND {completion_alias}.difference_manifest_sha256 =
            {difference_manifest}
        AND {completion_alias}.request_sha256 = {difference_request}
        AND {completion_alias}.idempotency_key_hash = {difference_idempotency}
        AND {expected_difference_set}
        AND {completion_alias}.difference_count = (
            SELECT pg_catalog.count(*)
              FROM public.stocktake_differences AS replay_difference
             WHERE replay_difference.task_id = {task_id_sql}
               AND replay_difference.round_id = {source_round_alias}.id
        )
        AND {completion_alias}.physical_difference_count = (
            SELECT pg_catalog.count(*) FILTER (
                       WHERE replay_difference.difference_type <>
                           'control_unassigned'
                   )
              FROM public.stocktake_differences AS replay_difference
             WHERE replay_difference.task_id = {task_id_sql}
               AND replay_difference.round_id = {source_round_alias}.id
        )
        AND {completion_alias}.control_difference_count = (
            SELECT pg_catalog.count(*) FILTER (
                       WHERE replay_difference.difference_type =
                           'control_unassigned'
                   )
              FROM public.stocktake_differences AS replay_difference
             WHERE replay_difference.task_id = {task_id_sql}
               AND replay_difference.round_id = {source_round_alias}.id
        )
        AND {completion_alias}.pending_observation_difference_count = (
            SELECT pg_catalog.count(*) FILTER (
                       WHERE replay_difference.observed_line_id IS NOT NULL
                         AND replay_difference.reason_code =
                             'opening_pending_verification'
                   )
              FROM public.stocktake_differences AS replay_difference
             WHERE replay_difference.task_id = {task_id_sql}
               AND replay_difference.round_id = {source_round_alias}.id
        )
        AND {completion_alias}.total_affected_qty = (
            SELECT COALESCE(
                       pg_catalog.sum(replay_difference.affected_qty),
                       0
                   )::numeric(18, 3)
              FROM public.stocktake_differences AS replay_difference
             WHERE replay_difference.task_id = {task_id_sql}
               AND replay_difference.round_id = {source_round_alias}.id
        )
        AND NOT EXISTS (
            SELECT 1
              FROM (
                  SELECT pg_catalog.count(*) AS actual_count,
                         pg_catalog.min(replay_difference.difference_no)
                             AS min_number,
                         pg_catalog.max(replay_difference.difference_no)
                             AS max_number
                    FROM public.stocktake_differences AS replay_difference
                   WHERE replay_difference.task_id = {task_id_sql}
                     AND replay_difference.round_id = {source_round_alias}.id
              ) AS difference_numbering
             WHERE difference_numbering.actual_count > 0
               AND (
                   difference_numbering.min_number <> 1
                   OR difference_numbering.max_number <>
                       difference_numbering.actual_count
               )
        )
        AND {opener_actor}
        AND (
            (
                {case_alias}.role_code = 'admin'
                AND {case_alias}.scope_type = 'national'
                AND {case_alias}.scope_id_snapshot = '*'
            )
            OR (
                {case_alias}.role_code = 'provincial_manager'
                AND {case_alias}.scope_type = 'organization'
                AND {case_alias}.scope_id_snapshot =
                    ({region_org_id_sql})::text
            )
        )
        AND {case_alias}.authorization_sha256 = {opener_hash}
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_scopes AS replay_scope
             WHERE replay_scope.task_id = {task_id_sql}
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_recount_scope_assignments
                          AS replay_assignment
                    WHERE replay_assignment.recount_case_id = {case_alias}.id
                      AND replay_assignment.task_id = {task_id_sql}
                      AND replay_assignment.source_round_id =
                          {source_round_alias}.id
                      AND replay_assignment.scope_id = replay_scope.id
               )
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_recount_scope_assignments
                   AS replay_assignment
              LEFT JOIN public.stocktake_scopes AS replay_scope
                ON replay_scope.id = replay_assignment.scope_id
               AND replay_scope.task_id = {task_id_sql}
              LEFT JOIN public.stock_locations AS replay_location
                ON replay_location.id = replay_scope.location_id
             WHERE replay_assignment.recount_case_id = {case_alias}.id
               AND (
                   replay_scope.id IS NULL
                   OR replay_location.id IS NULL
                   OR replay_assignment.task_id <> {task_id_sql}
                   OR replay_assignment.source_round_id <>
                       {source_round_alias}.id
                   OR replay_assignment.assigned_at <>
                       {case_alias}.opened_at
                   OR replay_assignment.created_at <>
                       replay_assignment.assigned_at
                   OR NOT ({assignment_actor})
                   OR NOT (
                       (
                           replay_assignment.role_code = 'admin'
                           AND replay_assignment.scope_type = 'national'
                           AND replay_assignment.scope_id_snapshot = '*'
                       )
                       OR (
                           replay_assignment.role_code =
                               'provincial_manager'
                           AND replay_assignment.scope_type = 'organization'
                           AND replay_assignment.scope_id_snapshot =
                               replay_scope.owner_org_id::text
                       )
                       OR (
                           replay_assignment.role_code = 'technician'
                           AND replay_assignment.scope_type = 'person'
                           AND replay_location.location_type = 'personal'
                           AND replay_scope.custodian_person_id_snapshot
                               IS NOT NULL
                           AND replay_location.custodian_person_id =
                               replay_scope.custodian_person_id_snapshot
                           AND replay_assignment.assignee_person_id =
                               replay_scope.custodian_person_id_snapshot
                           AND replay_assignment.scope_id_snapshot =
                               replay_scope.custodian_person_id_snapshot::text
                       )
                   )
                   OR replay_assignment.authorization_sha256 IS DISTINCT FROM
                       ({assignment_auth_hash})
                   OR replay_assignment.assignment_sha256 IS DISTINCT FROM
                       ({assignment_hash})
               )
        )
        AND {case_alias}.assignment_manifest_sha256 = {assignment_manifest}
        AND {case_alias}.request_sha256 = {request_hash}
        AND {case_alias}.recount_manifest_sha256 = {recount_manifest}
    )"""


def _opening_evidence_key_sql(
    *,
    kind: str,
    task_id_sql: str,
    suffix_sql: str,
) -> str:
    if kind not in {"state", "outbox"}:
        raise ValueError("unsupported opening evidence key kind")
    return f"""'opening-{kind}-' || pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                'cloud_oam.opening_stocktake.{kind}.v1',
                'UTF8'
            ) || pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to(({task_id_sql})::text, 'UTF8') ||
            pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to(({suffix_sql})::text, 'UTF8')
        ),
        'hex'
    )"""


def _nullable_timestamp_json_sql(expression: str) -> str:
    return (
        f"CASE WHEN {expression} IS NULL THEN 'null' "
        f"ELSE {_timestamp_json_sql(expression)} END"
    )


def _opening_scope_line_sha256_sql(scope_alias: str) -> str:
    document = f"""(
        '{{"assignee_user_id":' ||
        {_json_text_sql(f'{scope_alias}.assignee_user_id')} ||
        ',"custodian_person_id_snapshot":' ||
        {_nullable_json_text_sql(f'{scope_alias}.custodian_person_id_snapshot::text')} ||
        ',"location_id":' ||
        {_json_text_sql(f'{scope_alias}.location_id::text')} ||
        ',"owner_org_id":' ||
        {_json_text_sql(f'{scope_alias}.owner_org_id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.scope_line.v1"' ||
        ',"scope_mode":"location_all"}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _opening_scope_manifest_sha256_sql(task_alias: str) -> str:
    item = """(
        '{"assignee_user_id":' ||
        pg_catalog.to_json(manifest_scope.assignee_user_id::text)::text ||
        ',"custodian_person_id_snapshot":' || CASE
            WHEN manifest_scope.custodian_person_id_snapshot IS NULL
            THEN 'null'
            ELSE pg_catalog.to_json(
                manifest_scope.custodian_person_id_snapshot::text
            )::text
        END ||
        ',"freeze_mode":' ||
        pg_catalog.to_json(manifest_freeze.freeze_mode::text)::text ||
        ',"location_id":' ||
        pg_catalog.to_json(manifest_scope.location_id::text)::text ||
        ',"owner_org_id":' ||
        pg_catalog.to_json(manifest_scope.owner_org_id::text)::text ||
        ',"scope_key":' ||
        pg_catalog.to_json(manifest_scope.scope_key::text)::text ||
        ',"scope_mode":"location_all"' ||
        ',"scope_no":' || manifest_scope.scope_no::text ||
        ',"scope_sha256":' ||
        pg_catalog.to_json(manifest_scope.scope_sha256::text)::text ||
        '}'
    )"""
    document = f"""(
        '{{"region_org_id":' ||
        {_json_text_sql(f'{task_alias}.region_org_id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.scope_manifest.v1"' ||
        ',"scopes":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {item},
                       ',' ORDER BY manifest_scope.scope_no
                   )
              FROM public.stocktake_scopes AS manifest_scope
              JOIN public.inventory_freezes AS manifest_freeze
                ON manifest_freeze.task_id = manifest_scope.task_id
               AND manifest_freeze.stocktake_scope_id = manifest_scope.id
             WHERE manifest_scope.task_id = {task_alias}.id
        ), '') || ']}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _opening_control_payload_sha256_sql(
    control_alias: str,
    task_alias: str,
) -> str:
    document = f"""(
        '{{"condition_code":' ||
        {_nullable_json_text_sql(f'{control_alias}.condition_code')} ||
        ',"control_qty":' ||
        {_json_text_sql(_canonical_quantity_sql(f'{control_alias}.control_qty'))} ||
        ',"external_business_key":' ||
        {_json_text_sql(f'{control_alias}.external_business_key')} ||
        ',"mapping_note":' ||
        {_json_text_sql(f'{control_alias}.mapping_note')} ||
        ',"mapping_status":' ||
        {_json_text_sql(f'{control_alias}.mapping_status')} ||
        ',"material_id":' ||
        {_nullable_json_text_sql(f'{control_alias}.material_id::text')} ||
        ',"region_org_id":' ||
        {_json_text_sql(f'{task_alias}.region_org_id::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _opening_control_manifest_sha256_sql(
    task_alias: str,
    sync_alias: str,
) -> str:
    item = f"""(
        '{{"condition_code":' ||
        {_nullable_json_text_sql('manifest_control.condition_code')} ||
        ',"control_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('manifest_control.control_qty'))} ||
        ',"external_business_key":' ||
        {_json_text_sql('manifest_control.external_business_key')} ||
        ',"external_object_version_id":' ||
        {_json_text_sql('manifest_control.external_object_version_id::text')} ||
        ',"line_no":' || manifest_control.line_no::text ||
        ',"mapping_note":' ||
        {_json_text_sql('manifest_control.mapping_note')} ||
        ',"mapping_status":' ||
        {_json_text_sql('manifest_control.mapping_status')} ||
        ',"material_id":' ||
        {_nullable_json_text_sql('manifest_control.material_id::text')} ||
        ',"payload_sha256":' ||
        {_json_text_sql('manifest_control.payload_sha256')} ||
        ',"source_updated_at":' ||
        {_nullable_timestamp_json_sql('manifest_control.source_updated_at')} ||
        '}}'
    )"""
    document = f"""(
        '{{"lines":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {item},
                       ',' ORDER BY manifest_control.line_no
                   )
              FROM public.stocktake_control_snapshot_lines
                   AS manifest_control
             WHERE manifest_control.task_id = {task_alias}.id
        ), '') ||
        '],"region_org_id":' ||
        {_json_text_sql(f'{task_alias}.region_org_id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.control_manifest.v1"' ||
        ',"source_system_id":' ||
        {_json_text_sql(f'{task_alias}.control_source_system_id::text')} ||
        ',"sync_run_id":' ||
        {_json_text_sql(f'{task_alias}.control_sync_run_id::text')} ||
        ',"sync_scope_key":' || {_json_text_sql(f'{sync_alias}.scope_key')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _opening_control_batch_sha256_sql(batch_alias: str) -> str:
    item = f"""(
        '{{"external_event_id":' ||
        {_json_text_sql('batch_event.external_event_id')} ||
        ',"external_id":' || {_json_text_sql('batch_event.external_id')} ||
        ',"payload_sha256":' ||
        {_json_text_sql('batch_event.payload_sha256')} ||
        ',"source_updated_at":' ||
        {_nullable_timestamp_json_sql('batch_event.source_updated_at')} ||
        ',"source_version":' ||
        {_nullable_json_text_sql('batch_event.source_version')} ||
        '}}'
    )"""
    document = f"""(
        '{{"events":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {item},
                       ',' ORDER BY batch_event.id::text
                   )
              FROM public.sync_inbox_events AS batch_event
             WHERE batch_event.batch_id = {batch_alias}.id
               AND batch_event.entity_type = 'oam_inventory_control'
        ), '') ||
        '],"schema":"cloud_oam.opening_stocktake.control_batch.v1"' ||
        ',"sequence":' || {batch_alias}.sequence::text ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _opening_account_dimension_sha256_sql(
    account_alias: str,
) -> str:
    document = f"""(
        '{{"availability_bucket":' ||
        {_json_text_sql(f'{account_alias}.availability_bucket')} ||
        ',"condition_code":' ||
        {_json_text_sql(f'{account_alias}.condition_code')} ||
        ',"custodian_person_id":' ||
        {_nullable_json_text_sql(f'{account_alias}.custodian_person_id::text')} ||
        ',"location_id":' ||
        {_json_text_sql(f'{account_alias}.location_id::text')} ||
        ',"lot_id":' ||
        {_nullable_json_text_sql(f'{account_alias}.lot_id::text')} ||
        ',"material_id":' ||
        {_json_text_sql(f'{account_alias}.material_id::text')} ||
        ',"owner_org_id":' ||
        {_json_text_sql(f'{account_alias}.owner_org_id::text')} ||
        ',"schema":"cloud_oam.opening_stocktake.account_dimension.v1"' ||
        ',"stock_account_id":' ||
        {_json_text_sql(f'{account_alias}.id::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _opening_serial_snapshot_sha256_sql(snapshot_alias: str) -> str:
    document = f"""(
        '{{"schema":"cloud_oam.opening_stocktake.serial_snapshot.v1"' ||
        ',"serials":[]' ||
        ',"stock_account_id":' ||
        {_json_text_sql(f'{snapshot_alias}.stock_account_id::text')} ||
        '}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _opening_snapshot_manifest_sha256_sql(task_alias: str) -> str:
    line = f"""(
        '{{"account_dimension_sha256":' ||
        {_json_text_sql('manifest_snapshot.account_dimension_sha256')} ||
        ',"book_qty":' ||
        {_json_text_sql(_canonical_quantity_sql('manifest_snapshot.book_qty'))} ||
        ',"ledger_cursor":' || manifest_snapshot.ledger_cursor::text ||
        ',"serial_count":' || manifest_snapshot.serial_count::text ||
        ',"serial_snapshot_sha256":' ||
        {_json_text_sql('manifest_snapshot.serial_snapshot_sha256')} ||
        ',"stock_account_id":' ||
        {_json_text_sql('manifest_snapshot.stock_account_id::text')} ||
        '}}'
    )"""
    scope = f"""(
        '{{"lines":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {line},
                       ',' ORDER BY manifest_snapshot.stock_account_id::text
                   )
              FROM public.stocktake_snapshot_lines AS manifest_snapshot
             WHERE manifest_snapshot.task_id = {task_alias}.id
               AND manifest_snapshot.scope_id = manifest_scope.id
        ), '') ||
        '],"location_id":' ||
        {_json_text_sql('manifest_scope.location_id::text')} ||
        ',"owner_org_id":' ||
        {_json_text_sql('manifest_scope.owner_org_id::text')} ||
        ',"scope_key":' || {_json_text_sql('manifest_scope.scope_key')} ||
        '}}'
    )"""
    document = f"""(
        '{{"cutoff_ledger_cursor":' ||
        {task_alias}.cutoff_ledger_cursor::text ||
        ',"schema":"cloud_oam.opening_stocktake.snapshot_manifest.v1"' ||
        ',"scopes":[' || COALESCE((
            SELECT pg_catalog.string_agg(
                       {scope},
                       ',' ORDER BY manifest_scope.scope_no
                   )
              FROM public.stocktake_scopes AS manifest_scope
             WHERE manifest_scope.task_id = {task_alias}.id
        ), '') || ']}}'
    )"""
    return _canonical_text_sha256_sql(document)


def _organization_descends_sql(
    child_id_sql: str,
    ancestor_id_sql: str,
    *,
    require_active: bool,
    alias_suffix: str,
) -> str:
    active = "AND lineage_org.status = 'active'" if require_active else ""
    parent_active = (
        "AND lineage_parent.status = 'active'" if require_active else ""
    )
    lineage = f"""WITH RECURSIVE organization_lineage_{alias_suffix}(
            id,
            parent_id,
            visited
        ) AS (
            SELECT lineage_org.id,
                   lineage_org.parent_id,
                   ARRAY[lineage_org.id]::uuid[]
              FROM public.organizations AS lineage_org
             WHERE lineage_org.id = {child_id_sql}
               {active}
            UNION ALL
            SELECT lineage_parent.id,
                   lineage_parent.parent_id,
                   lineage.visited || lineage_parent.id
              FROM organization_lineage_{alias_suffix} AS lineage
              JOIN public.organizations AS lineage_parent
                ON lineage_parent.id = lineage.parent_id
               {parent_active}
             WHERE NOT lineage_parent.id = ANY(lineage.visited)
        )"""
    return f"""(
        EXISTS (
        {lineage}
        SELECT 1
          FROM organization_lineage_{alias_suffix} AS lineage
         WHERE lineage.id = {ancestor_id_sql}
        )
        AND NOT EXISTS (
        {lineage}
        SELECT 1
          FROM organization_lineage_{alias_suffix} AS lineage
         WHERE lineage.parent_id = ANY(lineage.visited)
        )
    )"""


def _location_tree_in_region_sql(
    location_id_sql: str,
    region_id_sql: str,
    *,
    alias_suffix: str,
) -> str:
    return f"""(
        EXISTS (
            SELECT 1
              FROM public.stock_locations AS target_location
             WHERE target_location.id = {location_id_sql}
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stock_locations AS personal_location
              LEFT JOIN public.stock_locations AS regional_parent
                ON regional_parent.id = personal_location.parent_id
              LEFT JOIN public.organizations AS personal_owner
                ON personal_owner.id = personal_location.owner_org_id
             WHERE personal_location.id = {location_id_sql}
               AND personal_location.location_type = 'personal'
               AND (
                   personal_owner.id IS NULL
                   OR personal_owner.status <> 'active'
                   OR personal_owner.org_type <> 'region_company'
                   OR regional_parent.id IS NULL
                   OR regional_parent.status <> 'active'
                   OR regional_parent.location_type <> 'region'
                   OR regional_parent.owner_org_id <>
                      personal_location.owner_org_id
                   OR EXISTS (
                       SELECT 1
                         FROM public.stock_locations AS personal_child
                        WHERE personal_child.parent_id = personal_location.id
                   )
               )
        )
        AND EXISTS (
            WITH RECURSIVE location_lineage_{alias_suffix}(
                id,
                parent_id,
                visited
            ) AS (
                SELECT lineage_location.id,
                       lineage_location.parent_id,
                       ARRAY[lineage_location.id]::uuid[]
                  FROM public.stock_locations AS lineage_location
                 WHERE lineage_location.id = {location_id_sql}
                UNION ALL
                SELECT lineage_parent.id,
                       lineage_parent.parent_id,
                       lineage.visited || lineage_parent.id
                  FROM location_lineage_{alias_suffix} AS lineage
                  JOIN public.stock_locations AS lineage_parent
                    ON lineage_parent.id = lineage.parent_id
                 WHERE NOT lineage_parent.id = ANY(lineage.visited)
            )
            SELECT 1
              FROM location_lineage_{alias_suffix} AS lineage
             WHERE lineage.parent_id IS NULL
        )
        AND NOT EXISTS (
        WITH RECURSIVE location_lineage_{alias_suffix}(
            id,
            parent_id,
            owner_org_id,
            status,
            visited
        ) AS (
            SELECT lineage_location.id,
                   lineage_location.parent_id,
                   lineage_location.owner_org_id,
                   lineage_location.status,
                   ARRAY[lineage_location.id]::uuid[]
              FROM public.stock_locations AS lineage_location
             WHERE lineage_location.id = {location_id_sql}
            UNION ALL
            SELECT lineage_parent.id,
                   lineage_parent.parent_id,
                   lineage_parent.owner_org_id,
                   lineage_parent.status,
                   lineage.visited || lineage_parent.id
              FROM location_lineage_{alias_suffix} AS lineage
              JOIN public.stock_locations AS lineage_parent
                ON lineage_parent.id = lineage.parent_id
             WHERE NOT lineage_parent.id = ANY(lineage.visited)
        )
        SELECT 1
         FROM location_lineage_{alias_suffix} AS lineage
         WHERE lineage.status <> 'active'
            OR lineage.parent_id = ANY(lineage.visited)
            OR NOT ({_organization_descends_sql(
                'lineage.owner_org_id',
                region_id_sql,
                require_active=True,
                alias_suffix=f'location_{alias_suffix}',
            )})
        )
    )"""


def _current_scope_master_proof_sql(
    task_id_sql: str,
    region_org_id_sql: str,
    *,
    alias_suffix: str,
) -> str:
    owner_tree = _organization_descends_sql(
        "current_scope.owner_org_id",
        region_org_id_sql,
        require_active=True,
        alias_suffix=f"scope_owner_{alias_suffix}",
    )
    location_owner_tree = _organization_descends_sql(
        "current_location.owner_org_id",
        region_org_id_sql,
        require_active=True,
        alias_suffix=f"location_owner_{alias_suffix}",
    )
    location_tree = _location_tree_in_region_sql(
        "current_scope.location_id",
        region_org_id_sql,
        alias_suffix=f"scope_{alias_suffix}",
    )
    return f"""(
        EXISTS (
            SELECT 1
              FROM public.organizations AS current_region
             WHERE current_region.id = {region_org_id_sql}
               AND current_region.status = 'active'
               AND current_region.org_type = 'region_company'
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_scopes AS current_scope
              LEFT JOIN public.organizations AS current_owner
                ON current_owner.id = current_scope.owner_org_id
              LEFT JOIN public.stock_locations AS current_location
                ON current_location.id = current_scope.location_id
             WHERE current_scope.task_id = {task_id_sql}
               AND (
                   current_owner.id IS NULL
                   OR current_owner.status <> 'active'
                   OR current_owner.org_type <> 'region_company'
                   OR current_location.id IS NULL
                   OR current_location.status <> 'active'
                   OR current_location.location_type NOT IN (
                       'region', 'personal'
                   )
                   OR NOT ({owner_tree})
                   OR NOT ({location_owner_tree})
                   OR NOT ({location_tree})
                   OR (
                       SELECT pg_catalog.count(*)
                         FROM public.custody_assignments AS current_custody
                        WHERE current_custody.location_id =
                              current_scope.location_id
                          AND current_custody.valid_from <=
                              pg_catalog.transaction_timestamp()
                          AND (
                              current_custody.valid_to IS NULL
                              OR pg_catalog.transaction_timestamp() <
                                 current_custody.valid_to
                          )
                   ) > 1
                   OR current_scope.custodian_person_id_snapshot
                      IS DISTINCT FROM (
                          SELECT pg_catalog.min(
                                     current_custody.custodian_person_id::text
                                 )::uuid
                            FROM public.custody_assignments AS current_custody
                           WHERE current_custody.location_id =
                                 current_scope.location_id
                             AND current_custody.valid_from <=
                                 pg_catalog.transaction_timestamp()
                             AND (
                                 current_custody.valid_to IS NULL
                                 OR pg_catalog.transaction_timestamp() <
                                    current_custody.valid_to
                             )
                      )
                   OR (
                       current_location.location_type = 'personal'
                       AND (
                           current_scope.custodian_person_id_snapshot IS NULL
                           OR current_location.custodian_person_id <>
                              current_scope.custodian_person_id_snapshot
                       )
                   )
                   OR (
                       current_location.location_type = 'region'
                       AND current_location.custodian_person_id IS NOT NULL
                       AND current_location.custodian_person_id <>
                           current_scope.custodian_person_id_snapshot
                   )
               )
        )
    )"""


def _start_user_entitlement_sql(
    *,
    user_id_sql: str,
    task_alias: str,
    scope_alias: str | None,
    location_alias: str | None,
    occurred_at_sql: str,
    action: str,
    alias_suffix: str,
    assignment_id_sql: str | None = None,
) -> str:
    """Prove the current formal principal and one exact selected grant."""

    if action not in {"manage", "count"}:
        raise ValueError("unsupported opening start entitlement")
    if scope_alias is None:
        if location_alias is not None:
            raise ValueError("manager entitlement cannot bind a location")
        role_shape = f"""(
            (
                entitlement_role.code = 'admin'
                AND entitlement_assignment.scope_type = 'national'
                AND entitlement_assignment.scope_id = '*'
                AND entitlement_person_org.org_type = 'headquarters'
            )
            OR (
                entitlement_role.code = 'provincial_manager'
                AND entitlement_assignment.scope_type = 'organization'
                AND entitlement_assignment.scope_id =
                    {task_alias}.region_org_id::text
                AND entitlement_person_org.org_type IN (
                    'headquarters', 'region_company', 'department'
                )
            )
        )"""
        target_proof = f"""(
            {_current_entitlement_target_sql(
                user_id_sql=user_id_sql,
                assignment_id_sql='entitlement_assignment.id',
                permission_resource='stocktake',
                permission_action=action,
                target_scope_type_sql="'organization'",
                target_scope_id_sql=f'{task_alias}.region_org_id::text',
                alias_suffix=f'{alias_suffix}_task_region',
            )}
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_scopes AS entitlement_scope_target
                  JOIN public.stock_locations AS entitlement_location_target
                    ON entitlement_location_target.id =
                       entitlement_scope_target.location_id
                 WHERE entitlement_scope_target.task_id = {task_alias}.id
                   AND (
                       NOT ({_current_entitlement_target_sql(
                           user_id_sql=user_id_sql,
                           assignment_id_sql='entitlement_assignment.id',
                           permission_resource='stocktake',
                           permission_action=action,
                           target_scope_type_sql="'organization'",
                           target_scope_id_sql=(
                               'entitlement_scope_target.owner_org_id::text'
                           ),
                           alias_suffix=f'{alias_suffix}_scope_owner',
                       )})
                       OR NOT ({_current_entitlement_target_sql(
                           user_id_sql=user_id_sql,
                           assignment_id_sql='entitlement_assignment.id',
                           permission_resource='stocktake',
                           permission_action=action,
                           target_scope_type_sql="'organization'",
                           target_scope_id_sql=(
                               'entitlement_location_target.owner_org_id::text'
                           ),
                           alias_suffix=f'{alias_suffix}_location_owner',
                       )})
                   )
            )
        )"""
    else:
        if location_alias is None:
            raise ValueError("scope entitlement requires a location")
        role_shape = f"""(
            (
                {location_alias}.location_type = 'personal'
                AND
                {scope_alias}.custodian_person_id_snapshot IS NOT NULL
                AND entitlement_role.code = 'technician'
                AND entitlement_assignment.scope_type = 'person'
                AND entitlement_assignment.scope_id =
                    {scope_alias}.custodian_person_id_snapshot::text
                AND entitlement_person.id =
                    {scope_alias}.custodian_person_id_snapshot
                AND entitlement_person_org.org_type IN (
                    'headquarters', 'region_company', 'department'
                )
            )
            OR (
                {location_alias}.location_type = 'region'
                AND
                entitlement_role.code = 'admin'
                AND entitlement_assignment.scope_type = 'national'
                AND entitlement_assignment.scope_id = '*'
                AND entitlement_person_org.org_type = 'headquarters'
            )
            OR (
                {location_alias}.location_type = 'region'
                AND
                entitlement_role.code = 'provincial_manager'
                AND entitlement_assignment.scope_type = 'organization'
                AND entitlement_assignment.scope_id =
                    {task_alias}.region_org_id::text
                AND entitlement_person_org.org_type IN (
                    'headquarters', 'region_company', 'department'
                )
            )
        )"""
        personal_target = _current_entitlement_target_sql(
            user_id_sql=user_id_sql,
            assignment_id_sql="entitlement_assignment.id",
            permission_resource="stocktake",
            permission_action=action,
            target_scope_type_sql="'person'",
            target_scope_id_sql=(
                f"{scope_alias}.custodian_person_id_snapshot::text"
            ),
            alias_suffix=f"{alias_suffix}_personal",
        )
        regional_task_target = _current_entitlement_target_sql(
            user_id_sql=user_id_sql,
            assignment_id_sql="entitlement_assignment.id",
            permission_resource="stocktake",
            permission_action=action,
            target_scope_type_sql="'organization'",
            target_scope_id_sql=f"{task_alias}.region_org_id::text",
            alias_suffix=f"{alias_suffix}_region",
        )
        regional_owner_target = _current_entitlement_target_sql(
            user_id_sql=user_id_sql,
            assignment_id_sql="entitlement_assignment.id",
            permission_resource="stocktake",
            permission_action=action,
            target_scope_type_sql="'organization'",
            target_scope_id_sql=f"{scope_alias}.owner_org_id::text",
            alias_suffix=f"{alias_suffix}_owner",
        )
        target_proof = f"""(
            (
                {location_alias}.location_type = 'personal'
                AND ({personal_target})
            )
            OR (
                {location_alias}.location_type = 'region'
                AND ({regional_task_target})
                AND ({regional_owner_target})
            )
        )"""
    return f"""EXISTS (
        SELECT 1
          FROM public.users AS entitlement_user
          JOIN public.people AS entitlement_person
            ON entitlement_person.id = entitlement_user.person_id
          JOIN public.organizations AS entitlement_person_org
            ON entitlement_person_org.id = entitlement_person.organization_id
          JOIN public.auth_identities AS entitlement_identity
            ON entitlement_identity.user_id = entitlement_user.id
           AND entitlement_identity.status = 'active'
           AND entitlement_identity.verified_at IS NOT NULL
           AND entitlement_identity.revoked_at IS NULL
          JOIN public.role_assignments AS entitlement_assignment
            ON entitlement_assignment.user_id = entitlement_user.id
          JOIN public.roles AS entitlement_role
            ON entitlement_role.id = entitlement_assignment.role_id
          JOIN public.role_permissions AS entitlement_allow
            ON entitlement_allow.role_id = entitlement_role.id
           AND entitlement_allow.effect = 'allow'
          JOIN public.permissions AS entitlement_allow_permission
            ON entitlement_allow_permission.id =
               entitlement_allow.permission_id
           AND entitlement_allow_permission.resource = 'stocktake'
           AND entitlement_allow_permission.action = '{action}'
           AND entitlement_allow_permission.field_code = ''
         WHERE entitlement_user.id = {user_id_sql}
           AND entitlement_user.account_status = 'active'
           AND entitlement_user.is_active
           AND entitlement_person.employment_status = 'active'
           AND entitlement_person_org.status = 'active'
           AND entitlement_role.status = 'active'
           AND NOT entitlement_role.is_external
           AND entitlement_assignment.status = 'active'
           AND entitlement_assignment.revoked_at IS NULL
           AND entitlement_assignment.valid_from <=
               pg_catalog.transaction_timestamp()
           AND (
               entitlement_assignment.valid_to IS NULL
               OR pg_catalog.transaction_timestamp() <
                  entitlement_assignment.valid_to
           )
           AND entitlement_assignment.valid_from <= {occurred_at_sql}
           AND (
               entitlement_assignment.valid_to IS NULL
               OR {occurred_at_sql} < entitlement_assignment.valid_to
           )
           AND {occurred_at_sql} >= pg_catalog.transaction_timestamp()
           AND {occurred_at_sql} <= pg_catalog.clock_timestamp()
           {f'AND entitlement_assignment.id = {assignment_id_sql}' if assignment_id_sql is not None else ''}
           AND {role_shape}
           AND {target_proof}
    )"""


def _historical_start_role_sql(
    *,
    user_id_sql: str,
    task_alias: str,
    scope_alias: str | None,
    location_alias: str | None,
    alias_suffix: str,
) -> str:
    """Prove the minimum role/scope facts available for a legacy start.

    Opening start did not persist a selected assignment snapshot before 0052,
    so history can only prove that at least one eligible assignment existed at
    the cutoff.  Later user/role deactivation and assignment expiry/revocation
    must not erase that historical fact.
    """

    assignment = f"historical_start_assignment_{alias_suffix}"
    role = f"historical_start_role_{alias_suffix}"
    user = f"historical_start_user_{alias_suffix}"
    person = f"historical_start_person_{alias_suffix}"
    if scope_alias is None:
        if location_alias is not None:
            raise ValueError("historical start manager cannot bind location")
        role_shape = f"""(
            (
                {role}.code = 'admin'
                AND {assignment}.scope_type = 'national'
                AND {assignment}.scope_id = '*'
            )
            OR (
                {role}.code = 'provincial_manager'
                AND {assignment}.scope_type = 'organization'
                AND {assignment}.scope_id =
                    {task_alias}.region_org_id::text
            )
        )"""
    else:
        if location_alias is None:
            raise ValueError("historical start assignee requires location")
        role_shape = f"""(
            (
                {location_alias}.location_type = 'personal'
                AND {scope_alias}.custodian_person_id_snapshot IS NOT NULL
                AND {role}.code = 'technician'
                AND {assignment}.scope_type = 'person'
                AND {assignment}.scope_id =
                    {scope_alias}.custodian_person_id_snapshot::text
                AND {person}.id =
                    {scope_alias}.custodian_person_id_snapshot
            )
            OR (
                {location_alias}.location_type = 'region'
                AND {role}.code = 'admin'
                AND {assignment}.scope_type = 'national'
                AND {assignment}.scope_id = '*'
            )
            OR (
                {location_alias}.location_type = 'region'
                AND {role}.code = 'provincial_manager'
                AND {assignment}.scope_type = 'organization'
                AND {assignment}.scope_id =
                    {task_alias}.region_org_id::text
            )
        )"""
    return f"""EXISTS (
        SELECT 1
          FROM public.users AS {user}
          JOIN public.people AS {person}
            ON {person}.id = {user}.person_id
          JOIN public.role_assignments AS {assignment}
            ON {assignment}.user_id = {user}.id
          JOIN public.roles AS {role}
            ON {role}.id = {assignment}.role_id
         WHERE {user}.id = {user_id_sql}
           AND {assignment}.status IN (
               'scheduled', 'active', 'expired', 'revoked'
           )
           AND {assignment}.valid_from <= {task_alias}.cutoff_at
           AND (
               {assignment}.valid_to IS NULL
               OR {task_alias}.cutoff_at < {assignment}.valid_to
           )
           AND (
               {assignment}.revoked_at IS NULL
               OR {task_alias}.cutoff_at < {assignment}.revoked_at
           )
           AND NOT {role}.is_external
           AND {role_shape}
    )"""


def _opening_start_graph_helper_body() -> str:
    """Return the canonical persisted opening-start proof.

    ``p_historical`` controls only facts that can legitimately evolve after
    start (task/round/freeze/outbox lifecycle and current authorization/master
    state).  Immutable scope, control, snapshot, state-event and audit facts
    are always recomputed from their persisted sources.
    """

    scope_hash = _opening_scope_line_sha256_sql("start_scope")
    scope_manifest = _opening_scope_manifest_sha256_sql("start_task")
    control_manifest = _opening_control_manifest_sha256_sql(
        "start_task",
        "control_sync",
    )
    control_payload = _opening_control_payload_sha256_sql(
        "control_line",
        "start_task",
    )
    control_batch = _opening_control_batch_sha256_sql("control_batch")
    account_dimension = _opening_account_dimension_sha256_sql(
        "snapshot_account"
    )
    serial_snapshot = _opening_serial_snapshot_sha256_sql("snapshot_line")
    snapshot_manifest = _opening_snapshot_manifest_sha256_sql("start_task")
    manager_entitlement = _start_user_entitlement_sql(
        user_id_sql="start_task.created_by_user_id",
        task_alias="start_task",
        scope_alias=None,
        location_alias=None,
        occurred_at_sql="start_task.cutoff_at",
        action="manage",
        alias_suffix="start_manager",
    )
    assignee_entitlement = _start_user_entitlement_sql(
        user_id_sql="start_scope.assignee_user_id",
        task_alias="start_task",
        scope_alias="start_scope",
        location_alias="start_location",
        occurred_at_sql="start_task.cutoff_at",
        action="count",
        alias_suffix="start_assignee",
    )
    historical_manager_role = _historical_start_role_sql(
        user_id_sql="start_task.created_by_user_id",
        task_alias="start_task",
        scope_alias=None,
        location_alias=None,
        alias_suffix="manager",
    )
    historical_assignee_role = _historical_start_role_sql(
        user_id_sql="start_scope.assignee_user_id",
        task_alias="start_task",
        scope_alias="start_scope",
        location_alias="start_location",
        alias_suffix="assignee",
    )
    owner_in_region = _organization_descends_sql(
        "start_scope.owner_org_id",
        "start_task.region_org_id",
        require_active=True,
        alias_suffix="start_owner",
    )
    location_owner_in_region = _organization_descends_sql(
        "start_location.owner_org_id",
        "start_task.region_org_id",
        require_active=True,
        alias_suffix="start_location_owner",
    )
    location_tree = _location_tree_in_region_sql(
        "start_scope.location_id",
        "start_task.region_org_id",
        alias_suffix="start_location",
    )
    request_reference = """(
        SELECT pg_catalog.min(
                   start_created_event.metadata_jsonb ->> 'request_reference'
               )
          FROM public.state_transition_events AS start_created_event
         WHERE start_created_event.aggregate_type = 'stocktake_task'
           AND start_created_event.aggregate_id = start_task.id::text
           AND start_created_event.reason = 'opening_stocktake_created'
    )"""
    state_metadata = f"""pg_catalog.jsonb_build_object(
        'control_manifest_sha256', start_task.control_manifest_sha256,
        'cutoff_ledger_cursor', start_task.cutoff_ledger_cursor,
        'request_reference', {request_reference},
        'scope_manifest_sha256', start_task.scope_manifest_sha256,
        'snapshot_manifest_sha256', start_task.snapshot_manifest_sha256
    )"""
    state_key = _opening_evidence_key_sql(
        kind="state",
        task_id_sql="start_task.id",
        suffix_sql="expected_transition.sequence_no",
    )
    outbox_key = _opening_evidence_key_sql(
        kind="outbox",
        task_id_sql="start_task.id",
        suffix_sql="'started'",
    )
    return f"""
SELECT COALESCE((
    SELECT
        start_task.task_type = 'opening'
        AND start_task.cutoff_ledger_cursor IS NOT NULL
        AND start_task.cutoff_ledger_cursor >= 0
        AND start_task.cutoff_at IS NOT NULL
        AND start_task.issued_at = start_task.cutoff_at
        AND start_task.frozen_at = start_task.cutoff_at
        AND start_task.created_at = start_task.cutoff_at
        AND start_task.scope_manifest_sha256 = {scope_manifest}
        AND start_task.control_manifest_sha256 = {control_manifest}
        AND start_task.control_manifest_sha256 = control_sync.manifest_sha256
        AND start_task.snapshot_manifest_sha256 = {snapshot_manifest}
        AND control_source.code = 'oam'
        AND control_source.mode IN ('read_only', 'mirror_only')
        AND control_sync.source_system_id = control_source.id
        AND control_sync.status = 'completed'
        AND control_sync.completed_at IS NOT NULL
        AND control_sync.completed_at = start_task.control_snapshot_at
        AND control_sync.completed_at <= start_task.cutoff_at
        AND control_sync.scope_key =
            'oam_inventory_control:region:' || start_task.region_org_id::text
        AND control_sync.manifest_sha256 ~ '^[0-9a-f]{{64}}$'
        AND (
            (
                p_historical
                AND {historical_manager_role}
            )
            OR (
                NOT p_historical
                AND start_task.status = 'counting'
                AND start_task.current_round_no = 1
                AND start_task.submitted_at IS NULL
                AND start_task.posted_at IS NULL
                AND start_task.closed_at IS NULL
                AND start_task.cancelled_at IS NULL
                AND start_task.version = 0
                AND start_task.updated_at = start_task.cutoff_at
                AND start_task.cutoff_at >=
                    pg_catalog.transaction_timestamp()
                AND start_task.cutoff_at <= pg_catalog.clock_timestamp()
                AND control_source.enabled
                AND {manager_entitlement}
                AND EXISTS (
                    SELECT 1
                      FROM public.inventory_ledger_heads AS start_ledger_head
                     WHERE start_ledger_head.id =
                           '40000000-0000-4000-8000-000000000001'::uuid
                       AND start_ledger_head.stream_key = 'inventory'
                       AND start_ledger_head.next_cursor =
                           start_task.cutoff_ledger_cursor + 1
                )
            )
        )
        AND (
            SELECT pg_catalog.count(*)
              FROM public.stocktake_rounds AS initial_round
             WHERE initial_round.task_id = start_task.id
               AND initial_round.round_no = 1
               AND initial_round.round_type = 'initial'
               AND initial_round.recount_case_id IS NULL
               AND initial_round.started_at = start_task.cutoff_at
               AND initial_round.created_at = start_task.cutoff_at
               AND initial_round.idempotency_key_hash ~ '^[0-9a-f]{{64}}$'
               AND (
                   p_historical
                   OR (
                       initial_round.status = 'counting'
                       AND initial_round.submitted_by_user_id IS NULL
                       AND initial_round.submitted_at IS NULL
                       AND initial_round.count_manifest_sha256 IS NULL
                       AND initial_round.updated_at = start_task.cutoff_at
                   )
               )
        ) = 1
        AND (
            p_historical
            OR (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_rounds AS current_start_round
                 WHERE current_start_round.task_id = start_task.id
            ) = 1
        )
        AND (
            SELECT pg_catalog.count(*)
              FROM public.stocktake_scopes AS numbered_scope
             WHERE numbered_scope.task_id = start_task.id
        ) > 0
        AND NOT EXISTS (
            SELECT 1
              FROM (
                  SELECT pg_catalog.count(*) AS scope_count,
                         pg_catalog.min(numbered_scope.scope_no) AS min_no,
                         pg_catalog.max(numbered_scope.scope_no) AS max_no
                    FROM public.stocktake_scopes AS numbered_scope
                   WHERE numbered_scope.task_id = start_task.id
              ) AS scope_numbering
             WHERE scope_numbering.min_no <> 1
                OR scope_numbering.max_no <> scope_numbering.scope_count
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_scopes AS start_scope
              LEFT JOIN public.stock_locations AS start_location
                ON start_location.id = start_scope.location_id
              LEFT JOIN public.organizations AS start_owner
                ON start_owner.id = start_scope.owner_org_id
             WHERE start_scope.task_id = start_task.id
               AND (
                   start_location.id IS NULL
                   OR start_owner.id IS NULL
                   OR start_scope.scope_mode <> 'location_all'
                   OR start_scope.material_id IS NOT NULL
                   OR start_scope.condition_code IS NOT NULL
                   OR start_scope.availability_bucket IS NOT NULL
                   OR start_scope.scope_key <>
                      'opening:' || start_scope.owner_org_id::text || ':' ||
                      start_scope.location_id::text
                   OR start_scope.scope_sha256 <> {scope_hash}
                   OR start_scope.created_at <> start_task.cutoff_at
                   OR (
                       SELECT pg_catalog.count(*)
                         FROM public.inventory_freezes AS start_freeze
                        WHERE start_freeze.task_id = start_task.id
                          AND start_freeze.stocktake_scope_id = start_scope.id
                          AND start_freeze.scope_key = start_scope.scope_key
                          AND start_freeze.freeze_mode IN (
                              'hard', 'cutoff_replay'
                          )
                          AND start_freeze.valid_from = start_task.cutoff_at
                          AND start_freeze.created_by_user_id =
                              start_task.created_by_user_id
                          AND start_freeze.created_at = start_task.cutoff_at
                          AND (
                              (
                                  start_freeze.status = 'active'
                                  AND start_freeze.valid_to IS NULL
                                  AND start_freeze.released_by_user_id IS NULL
                                  AND start_freeze.release_reason = ''
                                  AND start_freeze.version = 0
                                  AND start_freeze.updated_at =
                                      start_freeze.created_at
                              )
                              OR (
                                  p_historical
                                  AND start_freeze.status IN (
                                      'released', 'cancelled'
                                  )
                                  AND start_freeze.valid_to >
                                      start_freeze.valid_from
                                  AND start_freeze.released_by_user_id
                                      IS NOT NULL
                                  AND pg_catalog.btrim(
                                      start_freeze.release_reason
                                  ) <> ''
                                  AND start_freeze.version = 1
                                  AND start_freeze.updated_at >=
                                      start_freeze.valid_to
                              )
                          )
                   ) <> 1
                   OR (
                       p_historical
                       AND NOT ({historical_assignee_role})
                   )
                   OR (
                       NOT p_historical
                       AND (
                           start_owner.status <> 'active'
                           OR start_owner.org_type <> 'region_company'
                           OR start_location.status <> 'active'
                           OR start_location.location_type NOT IN (
                               'region', 'personal'
                           )
                           OR NOT ({owner_in_region})
                           OR NOT ({location_owner_in_region})
                           OR NOT ({location_tree})
                           OR NOT ({assignee_entitlement})
                           OR (
                               SELECT pg_catalog.count(*)
                                 FROM public.custody_assignments
                                      AS start_custody
                                WHERE start_custody.location_id =
                                      start_scope.location_id
                                  AND start_custody.valid_from <=
                                      start_task.cutoff_at
                                  AND (
                                      start_custody.valid_to IS NULL
                                      OR start_task.cutoff_at <
                                         start_custody.valid_to
                                  )
                           ) > 1
                           OR start_scope.custodian_person_id_snapshot
                              IS DISTINCT FROM (
                                  SELECT pg_catalog.min(
                                             start_custody.custodian_person_id::text
                                         )::uuid
                                    FROM public.custody_assignments
                                         AS start_custody
                                   WHERE start_custody.location_id =
                                         start_scope.location_id
                                     AND start_custody.valid_from <=
                                         start_task.cutoff_at
                                     AND (
                                         start_custody.valid_to IS NULL
                                         OR start_task.cutoff_at <
                                            start_custody.valid_to
                                     )
                              )
                           OR (
                               start_location.location_type = 'personal'
                               AND (
                                   start_scope.custodian_person_id_snapshot
                                       IS NULL
                                   OR start_location.custodian_person_id <>
                                      start_scope.custodian_person_id_snapshot
                               )
                           )
                           OR (
                               start_location.location_type = 'region'
                               AND start_location.custodian_person_id
                                   IS DISTINCT FROM
                                   start_scope.custodian_person_id_snapshot
                               AND start_location.custodian_person_id
                                   IS NOT NULL
                           )
                       )
                   )
               )
        )
        AND (
            SELECT pg_catalog.count(*)
              FROM public.inventory_freezes AS start_freeze
             WHERE start_freeze.task_id = start_task.id
        ) = (
            SELECT pg_catalog.count(*)
              FROM public.stocktake_scopes AS start_scope
             WHERE start_scope.task_id = start_task.id
        )
        AND EXISTS (
            SELECT 1
              FROM public.organizations AS start_region
             WHERE start_region.id = start_task.region_org_id
               AND (
                   p_historical
                   OR (
                       start_region.status = 'active'
                       AND start_region.org_type = 'region_company'
                   )
               )
        )
        AND EXISTS (
            SELECT 1
              FROM public.sync_batches AS control_batch
             WHERE control_batch.run_id = control_sync.id
               AND control_batch.entity_type = 'oam_inventory_control'
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.sync_batches AS control_batch
             WHERE control_batch.run_id = control_sync.id
               AND control_batch.entity_type = 'oam_inventory_control'
               AND (
                   control_batch.status <> 'applied'
                   OR control_batch.record_count <> (
                       SELECT pg_catalog.count(*)
                         FROM public.sync_inbox_events AS batch_event
                        WHERE batch_event.batch_id = control_batch.id
                          AND batch_event.entity_type =
                              'oam_inventory_control'
                   )
                   OR control_batch.body_sha256 <> {control_batch}
               )
        )
        AND (
            SELECT pg_catalog.count(*)
              FROM public.sync_inbox_events AS control_event
              JOIN public.sync_batches AS event_batch
                ON event_batch.id = control_event.batch_id
               AND event_batch.run_id = control_sync.id
               AND event_batch.entity_type = 'oam_inventory_control'
             WHERE control_event.entity_type = 'oam_inventory_control'
        ) = (
            SELECT pg_catalog.count(*)
              FROM public.stocktake_control_snapshot_lines AS control_line
             WHERE control_line.task_id = start_task.id
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_control_snapshot_lines AS control_line
             WHERE control_line.task_id = start_task.id
               AND (
                   control_line.created_at <> start_task.cutoff_at
                   OR control_line.payload_sha256 <> {control_payload}
                   OR control_line.external_object_version_id IS NULL
                   OR (
                       SELECT pg_catalog.count(*)
                         FROM public.external_object_versions AS control_version
                         JOIN public.external_objects AS control_object
                           ON control_object.id =
                              control_version.external_object_id
                         JOIN public.sync_inbox_events AS control_event
                           ON control_event.source_system_id =
                              control_source.id
                          AND control_event.entity_type =
                              'oam_inventory_control'
                          AND control_event.external_id =
                              control_object.external_id
                          AND control_event.source_version =
                              control_version.source_version
                          AND control_event.source_updated_at IS NOT DISTINCT
                              FROM control_version.source_updated_at
                          AND control_event.payload_jsonb =
                              control_version.payload_jsonb
                          AND control_event.payload_sha256 =
                              control_version.payload_sha256
                         JOIN public.sync_batches AS control_batch
                           ON control_batch.id = control_event.batch_id
                          AND control_batch.run_id = control_sync.id
                          AND control_batch.entity_type =
                              'oam_inventory_control'
                          AND control_batch.status = 'applied'
                        WHERE control_version.id =
                              control_line.external_object_version_id
                          AND control_object.source_system_id =
                              control_source.id
                          AND control_object.entity_type =
                              'oam_inventory_control'
                          AND control_object.external_id =
                              control_line.external_business_key
                          AND control_event.status = 'applied'
                          AND control_event.source_updated_at IS NOT DISTINCT
                              FROM control_line.source_updated_at
                          AND control_event.payload_jsonb =
                              pg_catalog.jsonb_build_object(
                                  'condition_code',
                                      control_line.condition_code,
                                  'control_qty',
                                      {_canonical_quantity_sql('control_line.control_qty')},
                                  'external_business_key',
                                      control_line.external_business_key,
                                  'mapping_note', control_line.mapping_note,
                                  'mapping_status',
                                      control_line.mapping_status,
                                  'material_id',
                                      control_line.material_id::text,
                                  'region_org_id',
                                      start_task.region_org_id::text
                              )
                          AND control_event.payload_sha256 =
                              control_line.payload_sha256
                          AND control_version.payload_sha256 =
                              control_line.payload_sha256
                          AND control_version.valid_from <=
                              start_task.control_snapshot_at
                          AND (
                              control_version.valid_to IS NULL
                              OR start_task.control_snapshot_at <
                                 control_version.valid_to
                          )
                          AND (
                              control_object.deleted_at IS NULL
                              OR start_task.control_snapshot_at <
                                 control_object.deleted_at
                          )
                          AND (
                              p_historical
                              OR (
                                  control_object.deleted_at IS NULL
                                  AND control_object.current_version_id =
                                      control_version.id
                                  AND control_version.is_current
                                  AND control_version.valid_to IS NULL
                              )
                          )
                   ) <> 1
               )
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_snapshot_lines AS snapshot_line
              LEFT JOIN public.stocktake_scopes AS snapshot_scope
                ON snapshot_scope.id = snapshot_line.scope_id
               AND snapshot_scope.task_id = start_task.id
              LEFT JOIN public.stock_accounts AS snapshot_account
                ON snapshot_account.id = snapshot_line.stock_account_id
             WHERE snapshot_line.task_id = start_task.id
               AND (
                   snapshot_scope.id IS NULL
                   OR snapshot_account.id IS NULL
                   OR snapshot_account.created_at IS NULL
                   OR snapshot_account.created_at > start_task.cutoff_at
                   OR snapshot_account.owner_org_id <>
                      snapshot_scope.owner_org_id
                   OR snapshot_account.location_id <>
                      snapshot_scope.location_id
                   OR (
                       snapshot_scope.custodian_person_id_snapshot IS NOT NULL
                       AND snapshot_account.custodian_person_id IS DISTINCT
                           FROM snapshot_scope.custodian_person_id_snapshot
                   )
                   OR snapshot_line.book_qty <> 0
                   OR snapshot_line.ledger_cursor <>
                      start_task.cutoff_ledger_cursor
                   OR snapshot_line.serial_snapshot_jsonb <> '[]'::jsonb
                   OR snapshot_line.serial_count <> 0
                   OR snapshot_line.account_dimension_sha256 <>
                      {account_dimension}
                   OR snapshot_line.serial_snapshot_sha256 <>
                      {serial_snapshot}
                   OR snapshot_line.created_at <> start_task.cutoff_at
               )
        )
        AND NOT EXISTS (
            SELECT 1
             FROM public.stock_accounts AS scoped_account
             WHERE EXISTS (
                 SELECT 1
                   FROM public.stocktake_scopes AS scoped_scope
                  WHERE scoped_scope.task_id = start_task.id
                    AND scoped_scope.owner_org_id =
                        scoped_account.owner_org_id
                    AND scoped_scope.location_id = scoped_account.location_id
             )
               AND (
                   scoped_account.created_at IS NULL
                   OR scoped_account.created_at <= start_task.cutoff_at
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.stocktake_snapshot_lines AS snapshot_line
                    WHERE snapshot_line.task_id = start_task.id
                      AND snapshot_line.stock_account_id = scoped_account.id
               )
        )
        AND (
            p_historical
            OR NOT EXISTS (
                SELECT 1
                  FROM public.stock_accounts AS baseline_account
                 WHERE EXISTS (
                     SELECT 1
                       FROM public.stocktake_scopes AS baseline_scope
                      WHERE baseline_scope.task_id = start_task.id
                        AND baseline_scope.owner_org_id =
                            baseline_account.owner_org_id
                        AND baseline_scope.location_id =
                            baseline_account.location_id
                 )
                   AND (
                       EXISTS (
                           SELECT 1
                             FROM public.stock_balances AS baseline_balance
                            WHERE baseline_balance.stock_account_id =
                                  baseline_account.id
                              AND baseline_balance.quantity <> 0
                       )
                       OR EXISTS (
                           SELECT 1
                             FROM public.inventory_movements
                                  AS baseline_movement
                            WHERE baseline_movement.from_account_id =
                                      baseline_account.id
                               OR baseline_movement.to_account_id =
                                      baseline_account.id
                       )
                       OR EXISTS (
                           SELECT 1
                             FROM public.serial_current_positions
                                  AS baseline_serial
                            WHERE baseline_serial.stock_account_id =
                                  baseline_account.id
                       )
                   )
            )
        )
        AND NOT EXISTS (
            SELECT 1
              FROM (
                  SELECT snapshot_account.material_id
                    FROM public.stocktake_snapshot_lines AS material_snapshot
                    JOIN public.stock_accounts AS snapshot_account
                      ON snapshot_account.id =
                         material_snapshot.stock_account_id
                   WHERE material_snapshot.task_id = start_task.id
                  UNION
                  SELECT control_line.material_id
                    FROM public.stocktake_control_snapshot_lines
                         AS control_line
                   WHERE control_line.task_id = start_task.id
                     AND control_line.material_id IS NOT NULL
              ) AS referenced_material
             WHERE NOT EXISTS (
                       SELECT 1
                         FROM public.materials AS start_material
                        WHERE start_material.id =
                              referenced_material.material_id
                          AND (p_historical OR start_material.status = 'active')
                   )
                OR (
                       SELECT pg_catalog.count(*)
                         FROM public.material_inventory_policies
                              AS start_policy
                        WHERE start_policy.material_id =
                              referenced_material.material_id
                          AND start_policy.effective_from <=
                              start_task.cutoff_at
                          AND (
                              start_policy.effective_to IS NULL
                              OR start_task.cutoff_at <
                                 start_policy.effective_to
                          )
                          AND start_policy.tracking_mode IN (
                              'none', 'lot', 'serial', 'lot_and_serial'
                          )
                   ) <> 1
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_snapshot_lines AS policy_snapshot
              JOIN public.stock_accounts AS policy_account
                ON policy_account.id = policy_snapshot.stock_account_id
              JOIN public.material_inventory_policies AS policy_row
                ON policy_row.material_id = policy_account.material_id
               AND policy_row.effective_from <= start_task.cutoff_at
               AND (
                   policy_row.effective_to IS NULL
                   OR start_task.cutoff_at < policy_row.effective_to
               )
              LEFT JOIN public.inventory_lots AS policy_lot
                ON policy_lot.id = policy_account.lot_id
               AND policy_lot.material_id = policy_account.material_id
             WHERE policy_snapshot.task_id = start_task.id
               AND (
                   (
                       policy_row.tracking_mode IN ('none', 'serial')
                       AND policy_account.lot_id IS NOT NULL
                   )
                   OR (
                       policy_row.tracking_mode IN ('lot', 'lot_and_serial')
                       AND policy_lot.id IS NULL
                   )
               )
        )
        AND {request_reference} ~ '^opening-request-[0-9a-f]{{64}}$'
        AND (
            SELECT pg_catalog.count(*)
              FROM public.state_transition_events AS start_event
             WHERE start_event.aggregate_type = 'stocktake_task'
               AND start_event.aggregate_id = start_task.id::text
               AND start_event.reason IN (
                   'opening_stocktake_created',
                   'opening_stocktake_issued',
                   'opening_stocktake_frozen',
                   'opening_stocktake_initial_round_started'
               )
        ) = 4
        AND NOT EXISTS (
            SELECT 1
              FROM (VALUES
                  (1, NULL::text, 'draft', 'opening_stocktake_created'),
                  (2, 'draft', 'issued', 'opening_stocktake_issued'),
                  (3, 'issued', 'frozen', 'opening_stocktake_frozen'),
                  (
                      4,
                      'frozen',
                      'counting',
                      'opening_stocktake_initial_round_started'
                  )
              ) AS expected_transition(
                  sequence_no,
                  from_status,
                  to_status,
                  reason
              )
             WHERE (
                 SELECT pg_catalog.count(*)
                   FROM public.state_transition_events AS start_event
                  WHERE start_event.aggregate_type = 'stocktake_task'
                    AND start_event.aggregate_id = start_task.id::text
                    AND start_event.from_status IS NOT DISTINCT FROM
                        expected_transition.from_status
                    AND start_event.to_status = expected_transition.to_status
                    AND start_event.reason = expected_transition.reason
                    AND start_event.actor_id =
                        start_task.created_by_user_id
                    AND start_event.idempotency_key = {state_key}
                    AND start_event.occurred_at = start_task.cutoff_at
                    AND start_event.created_at = start_task.cutoff_at
                    AND start_event.metadata_jsonb = {state_metadata}
             ) <> 1
        )
        AND (
            p_historical
            OR (
                SELECT pg_catalog.count(*)
                  FROM public.state_transition_events AS any_start_event
                 WHERE any_start_event.aggregate_type = 'stocktake_task'
                   AND any_start_event.aggregate_id = start_task.id::text
            ) = 4
        )
        AND (
            SELECT pg_catalog.count(*)
              FROM public.outbox_events AS start_outbox
              JOIN public.stocktake_rounds AS initial_round
                ON initial_round.task_id = start_task.id
               AND initial_round.round_no = 1
             WHERE start_outbox.aggregate_type = 'stocktake_task'
               AND start_outbox.aggregate_id = start_task.id::text
               AND start_outbox.event_type = 'stocktake.opening.started'
               AND start_outbox.payload_jsonb =
                   pg_catalog.jsonb_build_object(
                       'control_line_count', (
                           SELECT pg_catalog.count(*)
                             FROM public.stocktake_control_snapshot_lines
                                  AS counted_control
                            WHERE counted_control.task_id = start_task.id
                       ),
                       'cutoff_ledger_cursor',
                           start_task.cutoff_ledger_cursor,
                       'initial_round_id', initial_round.id::text,
                       'region_org_id', start_task.region_org_id::text,
                       'scope_count', (
                           SELECT pg_catalog.count(*)
                             FROM public.stocktake_scopes AS counted_scope
                            WHERE counted_scope.task_id = start_task.id
                       ),
                       'snapshot_line_count', (
                           SELECT pg_catalog.count(*)
                             FROM public.stocktake_snapshot_lines
                                  AS counted_snapshot
                            WHERE counted_snapshot.task_id = start_task.id
                       ),
                       'task_id', start_task.id::text,
                       'task_no', start_task.task_no
                   )
               AND start_outbox.idempotency_key = {outbox_key}
               AND start_outbox.available_at = start_task.cutoff_at
               AND start_outbox.created_at = start_task.cutoff_at
               AND start_outbox.updated_at >= start_task.cutoff_at
               AND (
                   start_outbox.locked_at IS NULL
                   OR start_outbox.locked_at >= start_task.cutoff_at
               )
               AND (
                   start_outbox.published_at IS NULL
                   OR start_outbox.published_at >= start_task.cutoff_at
               )
               AND (
                   p_historical
                   OR (
                       start_outbox.status = 'pending'
                       AND start_outbox.attempts = 0
                       AND start_outbox.locked_at IS NULL
                       AND start_outbox.locked_by IS NULL
                       AND start_outbox.published_at IS NULL
                       AND start_outbox.last_error IS NULL
                       AND start_outbox.updated_at = start_outbox.created_at
                   )
               )
        ) = 1
        AND (
            SELECT pg_catalog.count(*)
              FROM public.outbox_events AS start_outbox_candidate
             WHERE start_outbox_candidate.aggregate_type = 'stocktake_task'
               AND start_outbox_candidate.aggregate_id = start_task.id::text
               AND start_outbox_candidate.event_type =
                   'stocktake.opening.started'
        ) = 1
        AND (
            p_historical
            OR (
                SELECT pg_catalog.count(*)
                  FROM public.outbox_events AS current_start_outbox_candidate
                 WHERE current_start_outbox_candidate.aggregate_type =
                       'stocktake_task'
                   AND current_start_outbox_candidate.aggregate_id =
                       start_task.id::text
            ) = 1
        )
        AND (
            SELECT pg_catalog.count(*)
              FROM public.audit_events AS start_audit
              JOIN public.stocktake_rounds AS initial_round
                ON initial_round.task_id = start_task.id
               AND initial_round.round_no = 1
             WHERE start_audit.stream_key = 'inventory'
               AND start_audit.actor_user_id =
                   start_task.created_by_user_id
               AND start_audit.action = 'stocktake.opening.started'
               AND start_audit.aggregate_type = 'stocktake_task'
               AND start_audit.aggregate_id = start_task.id::text
               AND start_audit.before_jsonb IS NULL
               AND start_audit.after_jsonb =
                   pg_catalog.jsonb_build_object(
                       'control_line_count', (
                           SELECT pg_catalog.count(*)
                             FROM public.stocktake_control_snapshot_lines
                                  AS counted_control
                            WHERE counted_control.task_id = start_task.id
                       ),
                       'control_manifest_sha256',
                           start_task.control_manifest_sha256,
                       'cutoff_ledger_cursor',
                           start_task.cutoff_ledger_cursor,
                       'initial_round_id', initial_round.id::text,
                       'region_org_id', start_task.region_org_id::text,
                       'scope_count', (
                           SELECT pg_catalog.count(*)
                             FROM public.stocktake_scopes AS counted_scope
                            WHERE counted_scope.task_id = start_task.id
                       ),
                       'scope_manifest_sha256',
                           start_task.scope_manifest_sha256,
                       'snapshot_line_count', (
                           SELECT pg_catalog.count(*)
                             FROM public.stocktake_snapshot_lines
                                  AS counted_snapshot
                            WHERE counted_snapshot.task_id = start_task.id
                       ),
                       'snapshot_manifest_sha256',
                           start_task.snapshot_manifest_sha256,
                       'status', 'counting',
                       'task_no', start_task.task_no
                   )
               AND start_audit.request_id = {request_reference}
               AND start_audit.occurred_at = start_task.cutoff_at
               AND start_audit.event_hash ~ '^[0-9a-f]{{64}}$'
               AND start_audit.created_at >= start_task.cutoff_at
               AND ({_audit_event_chain_binding_sql(
                   'start_audit',
                   require_head=False,
               )})
               AND (
                   p_historical
                   OR EXISTS (
                       SELECT 1
                         FROM public.audit_chain_heads AS start_audit_head
                        WHERE start_audit_head.stream_key =
                              start_audit.stream_key
                          AND start_audit_head.last_event_id = start_audit.id
                          AND start_audit_head.last_hash =
                              start_audit.event_hash
                          AND start_audit_head.version =
                              start_audit.stream_version
                   )
               )
        ) = 1
        AND (
            SELECT pg_catalog.count(*)
              FROM public.audit_events AS start_audit_candidate
             WHERE start_audit_candidate.aggregate_type = 'stocktake_task'
               AND start_audit_candidate.aggregate_id = start_task.id::text
               AND start_audit_candidate.action =
                   'stocktake.opening.started'
        ) = 1
        AND (
            p_historical
            OR (
                SELECT pg_catalog.count(*)
                  FROM public.audit_events AS current_start_audit_candidate
                 WHERE current_start_audit_candidate.aggregate_type =
                       'stocktake_task'
                   AND current_start_audit_candidate.aggregate_id =
                       start_task.id::text
            ) = 1
        )
      FROM public.stocktake_tasks AS start_task
      JOIN public.source_systems AS control_source
        ON control_source.id = start_task.control_source_system_id
      JOIN public.sync_runs AS control_sync
        ON control_sync.id = start_task.control_sync_run_id
     WHERE start_task.id = p_task_id
       AND p_task_id IS NOT NULL
       AND p_historical IS NOT NULL
), FALSE)
""".strip()


def _round_submission_helper_body() -> str:
    historical = _round_submission_proof_sql(
        "p_task_id",
        "helper_round",
        "helper_submission",
        historical=True,
    )
    current = _round_submission_proof_sql(
        "p_task_id",
        "helper_round",
        "helper_submission",
        historical=False,
    )
    return f"""
SELECT CASE
    WHEN p_task_id IS NULL
         OR p_round_id IS NULL
         OR p_historical IS NULL
    THEN FALSE
    WHEN p_historical THEN COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_rounds AS helper_round
          JOIN public.stocktake_round_submissions AS helper_submission
            ON helper_submission.task_id = p_task_id
           AND helper_submission.round_id = helper_round.id
         WHERE helper_round.id = p_round_id
           AND helper_round.task_id = p_task_id
           AND {historical}
    ), FALSE)
    ELSE COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_rounds AS helper_round
          JOIN public.stocktake_round_submissions AS helper_submission
            ON helper_submission.task_id = p_task_id
           AND helper_submission.round_id = helper_round.id
         WHERE helper_round.id = p_round_id
           AND helper_round.task_id = p_task_id
           AND {current}
    ), FALSE)
END
""".strip()


def _scope_completion_side_effect_proof_sql(
    task_alias: str,
    round_alias: str,
    scope_alias: str,
    completion_alias: str,
    *,
    historical: bool,
) -> str:
    context = f"""CASE
        WHEN {round_alias}.round_type = 'initial'
        THEN pg_catalog.jsonb_build_object(
            'round_id', {round_alias}.id::text,
            'task_id', {task_alias}.id::text
        )
        ELSE pg_catalog.jsonb_build_object(
            'recount_case_id', {round_alias}.recount_case_id::text,
            'round_id', {round_alias}.id::text,
            'round_no', {round_alias}.round_no,
            'round_type', {round_alias}.round_type,
            'task_id', {task_alias}.id::text
        )
    END"""
    reason = f"""CASE
        WHEN {round_alias}.round_type = 'initial'
        THEN 'opening_initial_scope_count_completed'
        ELSE 'opening_recount_scope_count_completed'
    END"""
    state_key = _opening_count_event_key_sql(
        "scope-state", f"{round_alias}.id", f"{scope_alias}.id"
    )
    outbox_key = _opening_count_event_key_sql(
        "scope-outbox", f"{round_alias}.id", f"{scope_alias}.id"
    )
    sealed = f"""EXISTS (
        SELECT 1
          FROM public.stocktake_round_submissions AS scope_submission
         WHERE scope_submission.task_id = {task_alias}.id
           AND scope_submission.round_id = {round_alias}.id
           AND scope_submission.sealing_completion_id = {completion_alias}.id
    )"""
    fresh_outbox = "TRUE" if historical else """(
        scope_outbox.status = 'pending'
        AND scope_outbox.attempts = 0
        AND scope_outbox.locked_at IS NULL
        AND scope_outbox.locked_by IS NULL
        AND scope_outbox.published_at IS NULL
        AND scope_outbox.last_error IS NULL
        AND scope_outbox.updated_at = scope_outbox.created_at
    )"""
    return f"""(
        (SELECT pg_catalog.count(*)
           FROM public.state_transition_events AS scope_state
          WHERE scope_state.aggregate_type = 'stocktake_scope'
            AND scope_state.aggregate_id = {scope_alias}.id::text
            AND scope_state.from_status = 'counting'
            AND scope_state.to_status = 'completed'
            AND scope_state.reason = {reason}
            AND scope_state.actor_id =
                {completion_alias}.completed_by_user_id
            AND scope_state.idempotency_key = {state_key}
            AND scope_state.occurred_at = {completion_alias}.completed_at
            AND scope_state.created_at = {completion_alias}.completed_at
            AND scope_state.metadata_jsonb = ({context}) ||
                pg_catalog.jsonb_build_object(
                    'zero_confirmed', {completion_alias}.zero_confirmed
                )
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.state_transition_events AS scope_state_candidate
              WHERE scope_state_candidate.aggregate_type = 'stocktake_scope'
                AND scope_state_candidate.aggregate_id = {scope_alias}.id::text
                AND scope_state_candidate.reason IN (
                    'opening_initial_scope_count_completed',
                    'opening_recount_scope_count_completed'
                )
                AND scope_state_candidate.metadata_jsonb ->> 'round_id' =
                    {round_alias}.id::text
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.outbox_events AS scope_outbox
              WHERE scope_outbox.aggregate_type = 'stocktake_scope'
                AND scope_outbox.aggregate_id = {scope_alias}.id::text
                AND scope_outbox.event_type =
                    'stocktake.opening.scope_count_completed'
                AND scope_outbox.idempotency_key = {outbox_key}
                AND scope_outbox.payload_jsonb = ({context}) ||
                    pg_catalog.jsonb_build_object(
                        'round_sealed', {sealed},
                        'scope_id', {scope_alias}.id::text
                    )
                AND scope_outbox.available_at =
                    {completion_alias}.completed_at
                AND scope_outbox.created_at =
                    {completion_alias}.completed_at
                AND scope_outbox.updated_at >= scope_outbox.created_at
                AND {fresh_outbox}
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.outbox_events AS scope_outbox_candidate
              WHERE scope_outbox_candidate.aggregate_type = 'stocktake_scope'
                AND scope_outbox_candidate.aggregate_id =
                    {scope_alias}.id::text
                AND scope_outbox_candidate.event_type =
                    'stocktake.opening.scope_count_completed'
                AND scope_outbox_candidate.payload_jsonb ->> 'round_id' =
                    {round_alias}.id::text
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS scope_audit
              WHERE scope_audit.stream_key = 'inventory'
                AND scope_audit.aggregate_type = 'stocktake_scope'
                AND scope_audit.aggregate_id = {scope_alias}.id::text
                AND scope_audit.action =
                    'stocktake.opening.scope_count_completed'
                AND scope_audit.actor_user_id =
                    {completion_alias}.completed_by_user_id
                AND scope_audit.before_jsonb IS NULL
                AND scope_audit.after_jsonb = ({context}) ||
                    pg_catalog.jsonb_build_object(
                        'has_pending_verification', EXISTS (
                            SELECT 1
                              FROM public.stocktake_count_observations
                                   AS scope_observation
                             WHERE scope_observation.task_id = {task_alias}.id
                               AND scope_observation.round_id = {round_alias}.id
                               AND scope_observation.scope_id = {scope_alias}.id
                               AND scope_observation.verification_status =
                                   'pending_verification'
                        ),
                        'round_sealed', {sealed},
                        'zero_confirmed', {completion_alias}.zero_confirmed
                    )
                AND scope_audit.request_id ~
                    '^opening-count-request-[0-9a-f]{{64}}$'
                AND scope_audit.occurred_at =
                    {completion_alias}.completed_at
                AND scope_audit.created_at >=
                    {completion_alias}.completed_at
                AND ({_audit_event_chain_binding_sql(
                    'scope_audit',
                    require_head=False,
                )})
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS scope_audit_candidate
              WHERE scope_audit_candidate.aggregate_type = 'stocktake_scope'
                AND scope_audit_candidate.aggregate_id = {scope_alias}.id::text
                AND scope_audit_candidate.action =
                    'stocktake.opening.scope_count_completed'
                AND scope_audit_candidate.after_jsonb ->> 'round_id' =
                    {round_alias}.id::text
        ) = 1
    )"""


def _scope_completion_helper_body() -> str:
    def proof(*, historical: bool) -> str:
        actor = (
            _historical_authorization_sql(
                user_id_sql="scope_completion.completed_by_user_id",
                person_id_sql="scope_completion.completed_by_person_id",
                assignment_id_sql=(
                    "scope_completion.completed_role_assignment_id"
                ),
                authorization_version_sql=(
                    "scope_completion.authorization_version"
                ),
                occurred_at_sql="scope_completion.completed_at",
                role_code_sql="scope_completion.role_code",
                scope_type_sql="scope_completion.scope_type",
                scope_id_sql="scope_completion.scope_id_snapshot",
                alias_suffix=(
                    "scope_completion_historical"
                    if historical
                    else "scope_completion_current_history"
                ),
            )
            if historical
            else _current_authorization_sql(
                user_id_sql="scope_completion.completed_by_user_id",
                person_id_sql="scope_completion.completed_by_person_id",
                assignment_id_sql=(
                    "scope_completion.completed_role_assignment_id"
                ),
                authorization_version_sql=(
                    "scope_completion.authorization_version"
                ),
                occurred_at_sql="scope_completion.completed_at",
                role_code_sql="scope_completion.role_code",
                scope_type_sql="scope_completion.scope_type",
                scope_id_sql="scope_completion.scope_id_snapshot",
                permission_resource="stocktake",
                permission_action="count",
                allow_scheduled=False,
                alias_suffix="scope_completion_current",
            )
        )
        authorization = _scope_completion_authorization_sha256_sql(
            "scope_completion"
        )
        request = _scope_count_request_sha256_sql(
            "scope_task.id", "scope_round", "scope_row", "scope_completion"
        )
        evidence = _scope_evidence_manifest_sha256_sql(
            "scope_task.id", "scope_round", "scope_row", "scope_completion"
        )
        observation_dimension = _observation_dimension_sha256_sql(
            "scope_observation", "scope_row"
        )
        observation_idempotency = _observation_child_idempotency_sha256_sql(
            "scope_completion", "scope_observation"
        )
        side_effects = _scope_completion_side_effect_proof_sql(
            "scope_task",
            "scope_round",
            "scope_row",
            "scope_completion",
            historical=historical,
        )
        current_master = _current_scope_master_proof_sql(
            "scope_task.id",
            "scope_task.region_org_id",
            alias_suffix="scope_completion_current",
        )
        region_target = _current_entitlement_target_sql(
            user_id_sql="scope_completion.completed_by_user_id",
            assignment_id_sql=(
                "scope_completion.completed_role_assignment_id"
            ),
            permission_resource="stocktake",
            permission_action="count",
            target_scope_type_sql="'organization'",
            target_scope_id_sql="scope_task.region_org_id::text",
            alias_suffix="scope_completion_region",
        )
        owner_target = _current_entitlement_target_sql(
            user_id_sql="scope_completion.completed_by_user_id",
            assignment_id_sql=(
                "scope_completion.completed_role_assignment_id"
            ),
            permission_resource="stocktake",
            permission_action="count",
            target_scope_type_sql="'organization'",
            target_scope_id_sql="scope_row.owner_org_id::text",
            alias_suffix="scope_completion_owner",
        )
        location_owner_target = _current_entitlement_target_sql(
            user_id_sql="scope_completion.completed_by_user_id",
            assignment_id_sql=(
                "scope_completion.completed_role_assignment_id"
            ),
            permission_resource="stocktake",
            permission_action="count",
            target_scope_type_sql="'organization'",
            target_scope_id_sql="scope_location.owner_org_id::text",
            alias_suffix="scope_completion_location_owner",
        )
        current_only = "TRUE" if historical else f"""(
            ({current_master})
            AND (
                (
                    NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_round_submissions
                               AS current_scope_submission
                         WHERE current_scope_submission.task_id = scope_task.id
                           AND current_scope_submission.round_id = scope_round.id
                    )
                    AND scope_task.status = 'counting'
                    AND scope_task.current_round_no = scope_round.round_no
                    AND scope_round.status = 'counting'
                )
                OR (
                    scope_task.status = 'submitted'
                    AND scope_task.current_round_no = scope_round.round_no
                    AND public.{ROUND_SUBMISSION_FUNCTION}(
                        scope_task.id,
                        scope_round.id,
                        FALSE
                    )
                )
            )
            AND (
                scope_location.location_type = 'personal'
                OR (({region_target}) AND ({owner_target})
                    AND ({location_owner_target}))
            )
        )"""
        return f"""(
            scope_completion.created_at = scope_completion.completed_at
            AND scope_completion.count_line_count = (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_count_lines AS scope_line
                 WHERE scope_line.task_id = scope_task.id
                   AND scope_line.round_id = scope_round.id
                   AND scope_line.scope_id = scope_row.id
            )
            AND scope_completion.observation_line_count = (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_count_observations AS scope_observation
                 WHERE scope_observation.task_id = scope_task.id
                   AND scope_observation.round_id = scope_round.id
                   AND scope_observation.scope_id = scope_row.id
            )
            AND scope_completion.serial_count = (
                (SELECT pg_catalog.count(*)
                   FROM public.stocktake_count_serials AS scope_serial
                   JOIN public.stocktake_count_lines AS serial_line
                     ON serial_line.id = scope_serial.count_line_id
                    AND serial_line.round_id = scope_serial.round_id
                  WHERE serial_line.task_id = scope_task.id
                    AND serial_line.round_id = scope_round.id
                    AND serial_line.scope_id = scope_row.id)
                +
                (SELECT pg_catalog.count(*)
                   FROM public.stocktake_count_observations
                        AS serial_observation
                  WHERE serial_observation.task_id = scope_task.id
                    AND serial_observation.round_id = scope_round.id
                    AND serial_observation.scope_id = scope_row.id
                    AND serial_observation.serial_no_raw IS NOT NULL)
            )
            AND scope_completion.total_counted_qty = (
                (SELECT COALESCE(pg_catalog.sum(scope_line.counted_qty), 0)
                   FROM public.stocktake_count_lines AS scope_line
                  WHERE scope_line.task_id = scope_task.id
                    AND scope_line.round_id = scope_round.id
                    AND scope_line.scope_id = scope_row.id)
                +
                (SELECT COALESCE(
                            pg_catalog.sum(scope_observation.counted_qty), 0
                        )
                   FROM public.stocktake_count_observations AS scope_observation
                  WHERE scope_observation.task_id = scope_task.id
                    AND scope_observation.round_id = scope_round.id
                    AND scope_observation.scope_id = scope_row.id)
            )::numeric(18, 3)
            AND scope_completion.zero_confirmed IS NOT DISTINCT FROM (
                NOT EXISTS (
                    SELECT 1
                      FROM public.stocktake_snapshot_lines AS zero_snapshot
                     WHERE zero_snapshot.task_id = scope_task.id
                       AND zero_snapshot.scope_id = scope_row.id
                )
                AND scope_completion.count_line_count = 0
                AND scope_completion.observation_line_count = 0
            )
            AND scope_completion.authorization_sha256 = ({authorization})
            AND scope_completion.request_sha256 = ({request})
            AND scope_completion.evidence_manifest_sha256 = ({evidence})
            AND scope_completion.idempotency_key_hash ~ '^[0-9a-f]{{64}}$'
            AND ({actor})
            AND ({current_only})
            AND (
                (scope_round.round_type = 'initial'
                 AND scope_round.round_no = 1
                 AND scope_round.recount_case_id IS NULL
                 AND scope_completion.completed_by_user_id =
                     scope_row.assignee_user_id
                 AND (
                     (scope_completion.role_code = 'admin'
                      AND scope_completion.scope_type = 'national'
                      AND scope_completion.scope_id_snapshot = '*'
                      AND scope_location.location_type = 'region')
                     OR
                     (scope_completion.role_code = 'provincial_manager'
                      AND scope_completion.scope_type = 'organization'
                      AND scope_completion.scope_id_snapshot =
                          scope_task.region_org_id::text
                      AND scope_row.owner_org_id = scope_task.region_org_id
                      AND scope_location.location_type = 'region')
                     OR
                     (scope_completion.role_code = 'technician'
                      AND scope_completion.scope_type = 'person'
                      AND scope_location.location_type = 'personal'
                      AND scope_row.custodian_person_id_snapshot IS NOT NULL
                      AND scope_completion.completed_by_person_id =
                          scope_row.custodian_person_id_snapshot
                      AND scope_completion.scope_id_snapshot =
                          scope_row.custodian_person_id_snapshot::text)
                 ))
                OR
                (scope_round.round_type = 'recount'
                 AND scope_round.round_no > 1
                 AND (SELECT pg_catalog.count(*)
                        FROM public.stocktake_recount_scope_assignments
                             AS scope_assignment
                       WHERE scope_assignment.recount_case_id =
                             scope_round.recount_case_id
                         AND scope_assignment.task_id = scope_task.id
                         AND scope_assignment.scope_id = scope_row.id
                         AND scope_assignment.assignee_user_id =
                             scope_completion.completed_by_user_id
                         AND scope_assignment.assignee_person_id =
                             scope_completion.completed_by_person_id
                         AND scope_assignment.assignee_role_assignment_id =
                             scope_completion.completed_role_assignment_id
                         AND scope_assignment.role_code =
                             scope_completion.role_code
                         AND scope_assignment.scope_type =
                             scope_completion.scope_type
                         AND scope_assignment.scope_id_snapshot =
                             scope_completion.scope_id_snapshot
                         AND scope_assignment.assigned_at <=
                             scope_completion.completed_at) = 1)
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_count_lines AS scope_line
                 WHERE scope_line.task_id = scope_task.id
                   AND scope_line.round_id = scope_round.id
                   AND scope_line.scope_id = scope_row.id
                   AND (scope_line.counted_by_user_id <>
                            scope_completion.completed_by_user_id
                        OR scope_line.counted_at <>
                            scope_completion.completed_at
                        OR scope_line.created_at <>
                            scope_completion.completed_at
                        OR scope_line.updated_at <>
                            scope_completion.completed_at)
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_count_serials AS scope_serial
                  JOIN public.stocktake_count_lines AS scope_line
                    ON scope_line.id = scope_serial.count_line_id
                   AND scope_line.round_id = scope_serial.round_id
                 WHERE scope_line.task_id = scope_task.id
                   AND scope_line.round_id = scope_round.id
                   AND scope_line.scope_id = scope_row.id
                   AND scope_serial.created_at <>
                       scope_completion.completed_at
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_count_observations AS scope_observation
                 WHERE scope_observation.task_id = scope_task.id
                   AND scope_observation.round_id = scope_round.id
                   AND scope_observation.scope_id = scope_row.id
                   AND (scope_observation.owner_org_id <> scope_row.owner_org_id
                        OR scope_observation.location_id <> scope_row.location_id
                        OR scope_observation.custodian_person_id_snapshot
                            IS DISTINCT FROM
                            scope_row.custodian_person_id_snapshot
                        OR scope_observation.counted_by_user_id <>
                            scope_completion.completed_by_user_id
                        OR scope_observation.counted_at <>
                            scope_completion.completed_at
                        OR scope_observation.created_at <>
                            scope_completion.completed_at
                        OR scope_observation.dimension_sha256 <>
                            ({observation_dimension})
                        OR scope_observation.request_sha256 <>
                            scope_completion.request_sha256
                        OR scope_observation.idempotency_key_hash <>
                            ({observation_idempotency}))
            )
            AND NOT EXISTS (
                (SELECT snapshot.stock_account_id
                   FROM public.stocktake_snapshot_lines AS snapshot
                  WHERE snapshot.task_id = scope_task.id
                    AND snapshot.scope_id = scope_row.id)
                EXCEPT
                (SELECT scope_line.stock_account_id
                   FROM public.stocktake_count_lines AS scope_line
                  WHERE scope_line.task_id = scope_task.id
                    AND scope_line.round_id = scope_round.id
                    AND scope_line.scope_id = scope_row.id)
            )
            AND NOT EXISTS (
                (SELECT scope_line.stock_account_id
                   FROM public.stocktake_count_lines AS scope_line
                  WHERE scope_line.task_id = scope_task.id
                    AND scope_line.round_id = scope_round.id
                    AND scope_line.scope_id = scope_row.id)
                EXCEPT
                (SELECT snapshot.stock_account_id
                   FROM public.stocktake_snapshot_lines AS snapshot
                  WHERE snapshot.task_id = scope_task.id
                    AND snapshot.scope_id = scope_row.id)
            )
            AND ({side_effects})
        )"""

    historical = proof(historical=True)
    current = proof(historical=False)
    return f"""
SELECT CASE
    WHEN p_task_id IS NULL OR p_round_id IS NULL OR p_scope_id IS NULL
         OR p_historical IS NULL
    THEN FALSE
    WHEN p_historical THEN COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_tasks AS scope_task
          JOIN public.stocktake_rounds AS scope_round
            ON scope_round.id = p_round_id
           AND scope_round.task_id = scope_task.id
          JOIN public.stocktake_scopes AS scope_row
            ON scope_row.id = p_scope_id
           AND scope_row.task_id = scope_task.id
          JOIN public.stock_locations AS scope_location
            ON scope_location.id = scope_row.location_id
          JOIN public.stocktake_scope_count_completions AS scope_completion
            ON scope_completion.task_id = scope_task.id
           AND scope_completion.round_id = scope_round.id
           AND scope_completion.scope_id = scope_row.id
         WHERE scope_task.id = p_task_id
           AND scope_task.task_type = 'opening'
           AND {historical}
    ), FALSE)
    ELSE COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_tasks AS scope_task
          JOIN public.stocktake_rounds AS scope_round
            ON scope_round.id = p_round_id
           AND scope_round.task_id = scope_task.id
          JOIN public.stocktake_scopes AS scope_row
            ON scope_row.id = p_scope_id
           AND scope_row.task_id = scope_task.id
          JOIN public.stock_locations AS scope_location
            ON scope_location.id = scope_row.location_id
          JOIN public.stocktake_scope_count_completions AS scope_completion
            ON scope_completion.task_id = scope_task.id
           AND scope_completion.round_id = scope_round.id
           AND scope_completion.scope_id = scope_row.id
         WHERE scope_task.id = p_task_id
           AND scope_task.task_type = 'opening'
           AND {current}
    ), FALSE)
END
""".strip()


def _review_graph_helper_body() -> str:
    def proof(*, historical: bool) -> str:
        item_coverage = _review_item_coverage_by_id_sql(
            "review_row", "review_task.id", "review_round"
        )
        item_rules = _opening_review_item_rules_sql(
            "review_row", "review_round", historical=historical
        )
        difference_completion = _review_difference_completion_proof_sql(
            "review_task.id",
            "review_round",
            "review_submission",
            "review_row",
        )
        region_actor = (
            _historical_review_actor_sql(
                "review_row", "review_task", headquarters=False
            )
            if historical
            else _current_authorization_sql(
                user_id_sql="review_row.reviewer_user_id",
                person_id_sql="review_row.reviewer_person_id",
                assignment_id_sql="review_row.reviewer_role_assignment_id",
                authorization_version_sql="review_row.authorization_version",
                occurred_at_sql="review_row.reviewed_at",
                role_code_sql="'provincial_manager'",
                scope_type_sql="'organization'",
                scope_id_sql="review_task.region_org_id::text",
                permission_resource="stocktake",
                permission_action="review_region",
                allow_scheduled=True,
                alias_suffix="review_helper_region",
            )
        )
        headquarters_actor = (
            _historical_review_actor_sql(
                "review_row", "review_task", headquarters=True
            )
            if historical
            else _current_authorization_sql(
                user_id_sql="review_row.reviewer_user_id",
                person_id_sql="review_row.reviewer_person_id",
                assignment_id_sql="review_row.reviewer_role_assignment_id",
                authorization_version_sql="review_row.authorization_version",
                occurred_at_sql="review_row.reviewed_at",
                role_code_sql="'admin'",
                scope_type_sql="'national'",
                scope_id_sql="'*'",
                permission_resource="stocktake",
                permission_action="review_headquarters",
                allow_scheduled=True,
                alias_suffix="review_helper_headquarters",
            )
        )
        current_master = _current_scope_master_proof_sql(
            "review_task.id",
            "review_task.region_org_id",
            alias_suffix="review_helper",
        )
        region_target = _current_entitlement_target_sql(
            user_id_sql="review_row.reviewer_user_id",
            assignment_id_sql="review_row.reviewer_role_assignment_id",
            permission_resource="stocktake",
            permission_action="review_region",
            target_scope_type_sql="'organization'",
            target_scope_id_sql="review_task.region_org_id::text",
            alias_suffix="review_helper_region_target",
        )
        region_scope_targets = f"""NOT EXISTS (
            SELECT 1
              FROM public.stocktake_scopes AS review_target_scope
              JOIN public.stock_locations AS review_target_location
                ON review_target_location.id = review_target_scope.location_id
             WHERE review_target_scope.task_id = review_task.id
               AND (
                   NOT ({_current_entitlement_target_sql(
                       user_id_sql='review_row.reviewer_user_id',
                       assignment_id_sql=(
                           'review_row.reviewer_role_assignment_id'
                       ),
                       permission_resource='stocktake',
                       permission_action='review_region',
                       target_scope_type_sql="'organization'",
                       target_scope_id_sql=(
                           'review_target_scope.owner_org_id::text'
                       ),
                       alias_suffix='review_helper_scope_owner',
                   )})
                   OR NOT ({_current_entitlement_target_sql(
                       user_id_sql='review_row.reviewer_user_id',
                       assignment_id_sql=(
                           'review_row.reviewer_role_assignment_id'
                       ),
                       permission_resource='stocktake',
                       permission_action='review_region',
                       target_scope_type_sql="'organization'",
                       target_scope_id_sql=(
                           'review_target_location.owner_org_id::text'
                       ),
                       alias_suffix='review_helper_location_owner',
                   )})
               )
        )"""
        current_only = "TRUE" if historical else f"""(
            ({current_master})
            AND review_task.current_round_no = review_round.round_no
            AND review_task.updated_at = review_row.reviewed_at
            AND (
                (review_row.review_stage = 'region'
                 AND review_task.status = CASE review_row.decision
                     WHEN 'approve' THEN 'hq_review'
                     ELSE 'recount_required'
                 END
                 AND ({region_target})
                 AND ({region_scope_targets}))
                OR
                (review_row.review_stage = 'headquarters'
                 AND review_task.status = CASE review_row.decision
                     WHEN 'approve' THEN 'approved'
                     ELSE 'recount_required'
                 END)
            )
        )"""
        return f"""(
            review_row.created_at = review_row.reviewed_at
            AND NOT (
                review_row.review_stage = 'headquarters'
                AND review_row.decision = 'recount'
            )
            AND review_round.status = 'submitted'
            AND review_round.submitted_at IS NOT NULL
            AND review_round.updated_at = review_round.submitted_at
            AND review_row.reviewed_at >= review_round.submitted_at
            AND public.{ROUND_SUBMISSION_FUNCTION}(
                review_task.id,
                review_round.id,
                TRUE
            )
            AND ({difference_completion})
            AND ({item_coverage})
            AND ({item_rules})
            AND ({current_only})
            AND (
                (review_row.review_stage = 'region'
                 AND ({region_actor})
                 AND NOT EXISTS (
                     SELECT 1
                       FROM public.stocktake_reviews AS prior_review
                      WHERE prior_review.task_id = review_task.id
                        AND prior_review.round_id = review_round.id
                        AND prior_review.id <> review_row.id
                 ))
                OR
                (review_row.review_stage = 'headquarters'
                 AND ({headquarters_actor})
                 AND (SELECT pg_catalog.count(*)
                        FROM public.stocktake_reviews AS prior_region
                       WHERE prior_region.task_id = review_task.id
                         AND prior_region.round_id = review_round.id
                         AND prior_region.review_stage = 'region'
                         AND prior_region.decision = 'approve'
                         AND prior_region.reviewed_at < review_row.reviewed_at
                         AND prior_region.reviewer_user_id <>
                             review_row.reviewer_user_id
                         AND prior_region.reviewer_person_id <>
                             review_row.reviewer_person_id
                         AND prior_region.reviewer_role_assignment_id <>
                             review_row.reviewer_role_assignment_id) = 1
                 AND NOT EXISTS (
                     SELECT 1
                       FROM public.stocktake_reviews AS other_review
                      WHERE other_review.task_id = review_task.id
                        AND other_review.round_id = review_round.id
                        AND other_review.id <> review_row.id
                        AND other_review.review_stage <> 'region'
                 ))
            )
        )"""

    historical = proof(historical=True)
    current = proof(historical=False)
    return f"""
SELECT CASE
    WHEN p_review_id IS NULL OR p_historical IS NULL THEN FALSE
    WHEN p_historical THEN COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_reviews AS review_row
          JOIN public.stocktake_tasks AS review_task
            ON review_task.id = review_row.task_id
           AND review_task.task_type = 'opening'
          JOIN public.stocktake_rounds AS review_round
            ON review_round.id = review_row.round_id
           AND review_round.task_id = review_task.id
          JOIN public.stocktake_round_submissions AS review_submission
            ON review_submission.task_id = review_task.id
           AND review_submission.round_id = review_round.id
         WHERE review_row.id = p_review_id
           AND {historical}
    ), FALSE)
    ELSE COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_reviews AS review_row
          JOIN public.stocktake_tasks AS review_task
            ON review_task.id = review_row.task_id
           AND review_task.task_type = 'opening'
          JOIN public.stocktake_rounds AS review_round
            ON review_round.id = review_row.round_id
           AND review_round.task_id = review_task.id
          JOIN public.stocktake_round_submissions AS review_submission
            ON review_submission.task_id = review_task.id
           AND review_submission.round_id = review_round.id
         WHERE review_row.id = p_review_id
           AND {current}
    ), FALSE)
END
""".strip()


def _disposition_authorization_sha256_sql(disposition_alias: str) -> str:
    return _canonical_jsonb_sha256_sql(
        f"""pg_catalog.jsonb_build_object(
            'assignment_id',
                {disposition_alias}.decided_role_assignment_id::text,
            'authorization_version',
                {disposition_alias}.authorization_version,
            'decided_at', pg_catalog.to_char(
                pg_catalog.timezone('UTC', {disposition_alias}.decided_at),
                'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'
            ),
            'person_id', {disposition_alias}.decided_by_person_id::text,
            'role_code', {disposition_alias}.role_code,
            'schema',
                'cloud_oam.opening_stocktake.observation_disposition_authorization.v1',
            'scope_id', {disposition_alias}.scope_id_snapshot,
            'scope_type', {disposition_alias}.scope_type,
            'user_id', {disposition_alias}.decided_by_user_id
        )"""
    )


def _disposition_request_sha256_sql(disposition_alias: str) -> str:
    return _canonical_jsonb_sha256_sql(
        f"""pg_catalog.jsonb_build_object(
            'actor_person_id',
                {disposition_alias}.decided_by_person_id::text,
            'actor_user_id', {disposition_alias}.decided_by_user_id,
            'comment', {disposition_alias}.comment,
            'disposition', {disposition_alias}.disposition,
            'observation_id', {disposition_alias}.observation_id::text,
            'reason_code', {disposition_alias}.reason_code,
            'resolved_lot_id', {disposition_alias}.resolved_lot_id::text,
            'resolved_material_id',
                {disposition_alias}.resolved_material_id::text,
            'resolved_serial_id',
                {disposition_alias}.resolved_serial_id::text,
            'round_id', {disposition_alias}.round_id::text,
            'schema',
                'cloud_oam.opening_stocktake.observation_disposition_request.v1',
            'task_id', {disposition_alias}.task_id::text
        )"""
    )


def _disposition_observation_document_sql(observation_alias: str) -> str:
    return f"""pg_catalog.jsonb_build_object(
        'availability_bucket', {observation_alias}.availability_bucket,
        'condition_code', {observation_alias}.condition_code,
        'count_method', {observation_alias}.count_method,
        'counted_at', pg_catalog.to_char(
            pg_catalog.timezone('UTC', {observation_alias}.counted_at),
            'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'
        ),
        'counted_by_user_id', {observation_alias}.counted_by_user_id,
        'counted_qty', {_canonical_quantity_sql(f'{observation_alias}.counted_qty')},
        'created_at', pg_catalog.to_char(
            pg_catalog.timezone('UTC', {observation_alias}.created_at),
            'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'
        ),
        'custodian_person_id_snapshot',
            {observation_alias}.custodian_person_id_snapshot::text,
        'dimension_sha256', {observation_alias}.dimension_sha256,
        'id', {observation_alias}.id::text,
        'idempotency_key_hash', {observation_alias}.idempotency_key_hash,
        'location_id', {observation_alias}.location_id::text,
        'lot_id', {observation_alias}.lot_id::text,
        'lot_no_raw', {observation_alias}.lot_no_raw,
        'material_id', {observation_alias}.material_id::text,
        'material_identifier_raw',
            {observation_alias}.material_identifier_raw,
        'material_identifier_type',
            {observation_alias}.material_identifier_type,
        'observation_no', {observation_alias}.observation_no,
        'owner_org_id', {observation_alias}.owner_org_id::text,
        'reason_code', {observation_alias}.reason_code,
        'remark', {observation_alias}.remark,
        'request_sha256', {observation_alias}.request_sha256,
        'serial_id', {observation_alias}.serial_id::text,
        'serial_identifier_type',
            {observation_alias}.serial_identifier_type,
        'serial_no_raw', {observation_alias}.serial_no_raw,
        'verification_status', {observation_alias}.verification_status
    )"""


def _disposition_manifest_sha256_sql(
    disposition_alias: str,
    observation_alias: str,
    difference_alias: str,
    submission_alias: str,
    completion_alias: str,
) -> str:
    observation = _disposition_observation_document_sql(observation_alias)
    return _canonical_jsonb_sha256_sql(
        f"""pg_catalog.jsonb_build_object(
            'authorization_sha256',
                {disposition_alias}.authorization_sha256,
            'decided_at', pg_catalog.to_char(
                pg_catalog.timezone('UTC', {disposition_alias}.decided_at),
                'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'
            ),
            'difference', pg_catalog.jsonb_build_object(
                'affected_qty',
                    {_canonical_quantity_sql(f'{difference_alias}.affected_qty')},
                'counted_qty',
                    {_canonical_quantity_sql(f'{difference_alias}.counted_qty')},
                'difference_id', {difference_alias}.id::text,
                'difference_no', {difference_alias}.difference_no,
                'difference_type', {difference_alias}.difference_type,
                'reason_code', {difference_alias}.reason_code
            ),
            'difference_set_completion', pg_catalog.jsonb_build_object(
                'difference_manifest_sha256',
                    {completion_alias}.difference_manifest_sha256,
                'id', {completion_alias}.id::text,
                'request_sha256', {completion_alias}.request_sha256
            ),
            'disposition', {disposition_alias}.disposition,
            'disposition_id', {disposition_alias}.id::text,
            'idempotency_key_hash',
                {disposition_alias}.idempotency_key_hash,
            'observation', {observation},
            'reason_code', {disposition_alias}.reason_code,
            'comment', {disposition_alias}.comment,
            'request_sha256', {disposition_alias}.request_sha256,
            'resolution', pg_catalog.jsonb_build_object(
                'lot_id', {disposition_alias}.resolved_lot_id::text,
                'material_id',
                    {disposition_alias}.resolved_material_id::text,
                'serial_id', {disposition_alias}.resolved_serial_id::text
            ),
            'round_submission', pg_catalog.jsonb_build_object(
                'count_manifest_sha256',
                    {submission_alias}.count_manifest_sha256,
                'id', {submission_alias}.id::text,
                'round_manifest_sha256',
                    {submission_alias}.round_manifest_sha256
            ),
            'schema',
                'cloud_oam.opening_stocktake.observation_disposition.v1',
            'scope_id', {observation_alias}.scope_id::text,
            'round_id', {observation_alias}.round_id::text,
            'task_id', {observation_alias}.task_id::text
        )"""
    )


def _disposition_resolution_proof_sql(
    task_alias: str,
    observation_alias: str,
    disposition_alias: str,
) -> str:
    """Re-prove the evidence-only resolved-master boundary."""

    return f"""(
        (
            {disposition_alias}.disposition IN (
                'pending_verification', 'requires_recount'
            )
            AND {disposition_alias}.resolved_material_id IS NULL
            AND {disposition_alias}.resolved_lot_id IS NULL
            AND {disposition_alias}.resolved_serial_id IS NULL
        )
        OR
        (
            {disposition_alias}.disposition = 'resolved_existing_master'
            AND {disposition_alias}.role_code = 'admin'
            AND EXISTS (
                SELECT 1
                  FROM public.materials AS resolved_material
                 WHERE resolved_material.id =
                       {disposition_alias}.resolved_material_id
                   AND resolved_material.status = 'active'
            )
            AND (
                (
                    {observation_alias}.material_id IS NOT NULL
                    AND {observation_alias}.material_id =
                        {disposition_alias}.resolved_material_id
                )
                OR (
                    {observation_alias}.material_id IS NULL
                    AND {observation_alias}.material_identifier_type =
                        'sku_code'
                    AND (
                        SELECT pg_catalog.count(*)
                          FROM public.materials AS resolved_material
                         WHERE resolved_material.status = 'active'
                           AND resolved_material.sku_code =
                               {observation_alias}.material_identifier_raw
                    ) = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.materials AS resolved_material
                         WHERE resolved_material.id =
                               {disposition_alias}.resolved_material_id
                           AND resolved_material.status = 'active'
                           AND resolved_material.sku_code =
                               {observation_alias}.material_identifier_raw
                    )
                )
                OR (
                    {observation_alias}.material_id IS NULL
                    AND {observation_alias}.material_identifier_type =
                        'qr_code'
                    AND (
                        SELECT pg_catalog.count(*)
                          FROM public.qr_codes AS resolved_mapping
                         WHERE resolved_mapping.code =
                               {observation_alias}.material_identifier_raw
                           AND resolved_mapping.object_type = 'material'
                           AND resolved_mapping.status = 'active'
                    ) = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.qr_codes AS resolved_mapping
                         WHERE resolved_mapping.code =
                               {observation_alias}.material_identifier_raw
                           AND resolved_mapping.object_type = 'material'
                           AND resolved_mapping.object_id =
                               {disposition_alias}.resolved_material_id
                           AND resolved_mapping.status = 'active'
                    )
                )
            )
            AND (
                (
                    {observation_alias}.lot_no_raw IS NULL
                    AND {disposition_alias}.resolved_lot_id IS NULL
                )
                OR (
                    {observation_alias}.lot_no_raw IS NOT NULL
                    AND (
                        SELECT pg_catalog.count(*)
                          FROM public.inventory_lots AS resolved_lot
                         WHERE resolved_lot.material_id =
                               {disposition_alias}.resolved_material_id
                           AND resolved_lot.lot_no =
                               {observation_alias}.lot_no_raw
                    ) = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.inventory_lots AS resolved_lot
                         WHERE resolved_lot.id =
                               {disposition_alias}.resolved_lot_id
                           AND resolved_lot.material_id =
                               {disposition_alias}.resolved_material_id
                           AND resolved_lot.lot_no =
                               {observation_alias}.lot_no_raw
                    )
                )
            )
            AND (
                (
                    {observation_alias}.serial_no_raw IS NULL
                    AND {disposition_alias}.resolved_serial_id IS NULL
                )
                OR (
                    {observation_alias}.serial_identifier_type = 'serial_no'
                    AND (
                        SELECT pg_catalog.count(*)
                          FROM public.inventory_serials AS resolved_serial
                         WHERE resolved_serial.material_id =
                               {disposition_alias}.resolved_material_id
                           AND resolved_serial.serial_no =
                               {observation_alias}.serial_no_raw
                           AND resolved_serial.lifecycle_status = 'active'
                    ) = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.inventory_serials AS resolved_serial
                         WHERE resolved_serial.id =
                               {disposition_alias}.resolved_serial_id
                           AND resolved_serial.material_id =
                               {disposition_alias}.resolved_material_id
                           AND resolved_serial.lot_id IS NOT DISTINCT FROM
                               {disposition_alias}.resolved_lot_id
                           AND resolved_serial.serial_no =
                               {observation_alias}.serial_no_raw
                           AND resolved_serial.lifecycle_status = 'active'
                    )
                )
                OR (
                    {observation_alias}.serial_identifier_type = 'qr_code'
                    AND (
                        SELECT pg_catalog.count(*)
                          FROM public.inventory_serials AS resolved_serial
                         WHERE resolved_serial.material_id =
                               {disposition_alias}.resolved_material_id
                           AND resolved_serial.qr_code =
                               {observation_alias}.serial_no_raw
                           AND resolved_serial.lifecycle_status = 'active'
                    ) = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.inventory_serials AS resolved_serial
                         WHERE resolved_serial.id =
                               {disposition_alias}.resolved_serial_id
                           AND resolved_serial.material_id =
                               {disposition_alias}.resolved_material_id
                           AND resolved_serial.lot_id IS NOT DISTINCT FROM
                               {disposition_alias}.resolved_lot_id
                           AND resolved_serial.qr_code =
                               {observation_alias}.serial_no_raw
                           AND resolved_serial.lifecycle_status = 'active'
                    )
                    AND (
                        SELECT pg_catalog.count(*)
                          FROM public.qr_codes AS resolved_mapping
                         WHERE resolved_mapping.code =
                               {observation_alias}.serial_no_raw
                           AND resolved_mapping.object_type = 'serial'
                           AND resolved_mapping.object_id =
                               {disposition_alias}.resolved_serial_id
                           AND resolved_mapping.status = 'active'
                    ) = 1
                )
            )
            AND (
                SELECT pg_catalog.count(*)
                  FROM public.material_inventory_policies AS resolved_policy
                 WHERE resolved_policy.material_id =
                       {disposition_alias}.resolved_material_id
                   AND resolved_policy.effective_from <= {task_alias}.cutoff_at
                   AND (
                       resolved_policy.effective_to IS NULL
                       OR {task_alias}.cutoff_at < resolved_policy.effective_to
                   )
            ) = 1
            AND NOT EXISTS (
                SELECT 1
                  FROM public.material_inventory_policies AS resolved_policy
                 WHERE resolved_policy.material_id =
                       {disposition_alias}.resolved_material_id
                   AND resolved_policy.effective_from <= {task_alias}.cutoff_at
                   AND (
                       resolved_policy.effective_to IS NULL
                       OR {task_alias}.cutoff_at < resolved_policy.effective_to
                   )
                   AND (
                       {observation_alias}.counted_qty < 0
                       OR {observation_alias}.counted_qty >= 1000000000000
                       OR {observation_alias}.counted_qty <>
                          pg_catalog.round(
                              {observation_alias}.counted_qty,
                              resolved_policy.quantity_scale
                          )
                       OR (
                           NOT resolved_policy.allow_fraction
                           AND {observation_alias}.counted_qty <>
                               pg_catalog.round({observation_alias}.counted_qty, 0)
                       )
                       OR (
                           resolved_policy.tracking_mode = 'none'
                           AND (
                               {disposition_alias}.resolved_lot_id IS NOT NULL
                               OR {disposition_alias}.resolved_serial_id IS NOT NULL
                           )
                       )
                       OR (
                           resolved_policy.tracking_mode = 'lot'
                           AND (
                               {disposition_alias}.resolved_lot_id IS NULL
                               OR {disposition_alias}.resolved_serial_id IS NOT NULL
                           )
                       )
                       OR (
                           resolved_policy.tracking_mode = 'serial'
                           AND (
                               {disposition_alias}.resolved_lot_id IS NOT NULL
                               OR {disposition_alias}.resolved_serial_id IS NULL
                               OR {observation_alias}.counted_qty <> 1
                           )
                       )
                       OR (
                           resolved_policy.tracking_mode = 'lot_and_serial'
                           AND (
                               {disposition_alias}.resolved_lot_id IS NULL
                               OR {disposition_alias}.resolved_serial_id IS NULL
                               OR {observation_alias}.counted_qty <> 1
                           )
                       )
                       OR resolved_policy.tracking_mode NOT IN (
                           'none', 'lot', 'serial', 'lot_and_serial'
                       )
                   )
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stock_accounts AS resolved_account
                 WHERE resolved_account.owner_org_id =
                       {observation_alias}.owner_org_id
                   AND resolved_account.location_id =
                       {observation_alias}.location_id
                   AND resolved_account.custodian_person_id IS NOT DISTINCT FROM
                       {observation_alias}.custodian_person_id_snapshot
                   AND resolved_account.material_id =
                       {disposition_alias}.resolved_material_id
                   AND resolved_account.condition_code =
                       {observation_alias}.condition_code
                   AND resolved_account.availability_bucket =
                       {observation_alias}.availability_bucket
                   AND resolved_account.lot_id IS NOT DISTINCT FROM
                       {disposition_alias}.resolved_lot_id
                   AND resolved_account.created_at <= {task_alias}.cutoff_at
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_snapshot_lines AS resolved_snapshot
                  JOIN public.stock_accounts AS resolved_account
                    ON resolved_account.id = resolved_snapshot.stock_account_id
                 WHERE resolved_snapshot.task_id = {task_alias}.id
                   AND resolved_snapshot.scope_id =
                       {observation_alias}.scope_id
                   AND resolved_account.owner_org_id =
                       {observation_alias}.owner_org_id
                   AND resolved_account.location_id =
                       {observation_alias}.location_id
                   AND resolved_account.custodian_person_id IS NOT DISTINCT FROM
                       {observation_alias}.custodian_person_id_snapshot
                   AND resolved_account.material_id =
                       {disposition_alias}.resolved_material_id
                   AND resolved_account.condition_code =
                       {observation_alias}.condition_code
                   AND resolved_account.availability_bucket =
                       {observation_alias}.availability_bucket
                   AND resolved_account.lot_id IS NOT DISTINCT FROM
                       {disposition_alias}.resolved_lot_id
            )
        )
    )"""


def _disposition_audit_proof_sql(
    disposition_alias: str,
    completion_alias: str,
    *,
    historical: bool,
) -> str:
    after_json = f"""pg_catalog.jsonb_build_object(
        'authorization_sha256', {disposition_alias}.authorization_sha256,
        'authorization_version', {disposition_alias}.authorization_version,
        'decided_by_person_id',
            {disposition_alias}.decided_by_person_id::text,
        'decided_role_assignment_id',
            {disposition_alias}.decided_role_assignment_id::text,
        'difference_manifest_sha256',
            {completion_alias}.difference_manifest_sha256,
        'difference_set_completion_id', {completion_alias}.id::text,
        'disposition', {disposition_alias}.disposition,
        'disposition_manifest_sha256',
            {disposition_alias}.disposition_manifest_sha256,
        'observation_id', {disposition_alias}.observation_id::text,
        'request_sha256', {disposition_alias}.request_sha256,
        'resolved_lot_id', {disposition_alias}.resolved_lot_id::text,
        'resolved_material_id',
            {disposition_alias}.resolved_material_id::text,
        'resolved_serial_id', {disposition_alias}.resolved_serial_id::text,
        'role_code', {disposition_alias}.role_code,
        'round_id', {disposition_alias}.round_id::text,
        'scope_id', {disposition_alias}.scope_id::text,
        'scope_id_snapshot', {disposition_alias}.scope_id_snapshot,
        'scope_type', {disposition_alias}.scope_type,
        'task_id', {disposition_alias}.task_id::text
    )"""
    return f"""(
        (SELECT pg_catalog.count(*)
           FROM public.audit_events AS disposition_audit
          WHERE disposition_audit.stream_key = 'inventory'
            AND disposition_audit.aggregate_type =
                'stocktake_observation_disposition'
            AND disposition_audit.aggregate_id = {disposition_alias}.id::text
            AND disposition_audit.action =
                'stocktake.opening.observation_disposed'
            AND disposition_audit.actor_user_id =
                {disposition_alias}.decided_by_user_id
            AND disposition_audit.before_jsonb IS NULL
            AND disposition_audit.after_jsonb = {after_json}
            AND disposition_audit.request_id ~
                '^opening-observation-disposition-request-[0-9a-f]{{64}}$'
            AND disposition_audit.occurred_at =
                {disposition_alias}.decided_at
            AND disposition_audit.created_at >=
                {disposition_alias}.decided_at
            AND ({_audit_event_chain_binding_sql(
                'disposition_audit',
                require_head=not historical,
            )})
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS disposition_audit_candidate
              WHERE disposition_audit_candidate.aggregate_type =
                    'stocktake_observation_disposition'
                AND disposition_audit_candidate.aggregate_id =
                    {disposition_alias}.id::text
        ) = 1
    )"""


def _disposition_graph_helper_body() -> str:
    def proof(*, historical: bool) -> str:
        authorization = _disposition_authorization_sha256_sql(
            "disposition_row"
        )
        request = _disposition_request_sha256_sql("disposition_row")
        manifest = _disposition_manifest_sha256_sql(
            "disposition_row",
            "disposition_observation",
            "disposition_difference",
            "disposition_submission",
            "disposition_completion",
        )
        resolution = _disposition_resolution_proof_sql(
            "disposition_task",
            "disposition_observation",
            "disposition_row",
        )
        actor = (
            _historical_authorization_sql(
                user_id_sql="disposition_row.decided_by_user_id",
                person_id_sql="disposition_row.decided_by_person_id",
                assignment_id_sql=(
                    "disposition_row.decided_role_assignment_id"
                ),
                authorization_version_sql=(
                    "disposition_row.authorization_version"
                ),
                occurred_at_sql="disposition_row.decided_at",
                role_code_sql="disposition_row.role_code",
                scope_type_sql="disposition_row.scope_type",
                scope_id_sql="disposition_row.scope_id_snapshot",
                alias_suffix="disposition_history",
            )
            if historical
            else _current_authorization_sql(
                user_id_sql="disposition_row.decided_by_user_id",
                person_id_sql="disposition_row.decided_by_person_id",
                assignment_id_sql=(
                    "disposition_row.decided_role_assignment_id"
                ),
                authorization_version_sql=(
                    "disposition_row.authorization_version"
                ),
                occurred_at_sql="disposition_row.decided_at",
                role_code_sql="disposition_row.role_code",
                scope_type_sql="disposition_row.scope_type",
                scope_id_sql="disposition_row.scope_id_snapshot",
                permission_resource="stocktake",
                permission_action="manage",
                allow_scheduled=False,
                alias_suffix="disposition_current",
            )
        )
        current_scope = _current_scope_master_proof_sql(
            "disposition_task.id",
            "disposition_task.region_org_id",
            alias_suffix="disposition",
        )
        target_proofs = " AND ".join(
            f"({_current_entitlement_target_sql(
                user_id_sql='disposition_row.decided_by_user_id',
                assignment_id_sql=(
                    'disposition_row.decided_role_assignment_id'
                ),
                permission_resource='stocktake',
                permission_action='manage',
                target_scope_type_sql="'organization'",
                target_scope_id_sql=target,
                alias_suffix=suffix,
            )})"
            for target, suffix in (
                ("disposition_task.region_org_id::text", "disp_region"),
                ("disposition_scope.owner_org_id::text", "disp_owner"),
                ("disposition_location.owner_org_id::text", "disp_location"),
            )
        )
        current_only = "TRUE" if historical else f"""(
            disposition_task.status = 'submitted'
            AND disposition_task.current_round_no = disposition_round.round_no
            AND disposition_task.submitted_at =
                disposition_submission.submitted_at
            AND disposition_round.status = 'submitted'
            AND disposition_round.submitted_at =
                disposition_submission.submitted_at
            AND disposition_row.decided_at >=
                disposition_submission.submitted_at
            AND ({current_scope})
            AND ({target_proofs})
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_reviews AS disposition_downstream_review
                 WHERE disposition_downstream_review.task_id =
                       disposition_task.id
                   AND disposition_downstream_review.round_id =
                       disposition_round.id
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_postings AS disposition_downstream_posting
                 WHERE disposition_downstream_posting.task_id =
                       disposition_task.id
                   AND disposition_downstream_posting.round_id =
                       disposition_round.id
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.inventory_opening_establishments
                       AS disposition_downstream_establishment
                 WHERE disposition_downstream_establishment.task_id =
                       disposition_task.id
                   AND disposition_downstream_establishment.round_id =
                       disposition_round.id
            )
        )"""
        audit = _disposition_audit_proof_sql(
            "disposition_row",
            "disposition_completion",
            historical=historical,
        )
        return f"""(
            disposition_task.task_type = 'opening'
            AND disposition_task.cutoff_at IS NOT NULL
            AND disposition_row.task_id = disposition_task.id
            AND disposition_row.round_id = disposition_round.id
            AND disposition_row.scope_id = disposition_scope.id
            AND disposition_row.observation_id = disposition_observation.id
            AND disposition_observation.task_id = disposition_task.id
            AND disposition_observation.round_id = disposition_round.id
            AND disposition_observation.scope_id = disposition_scope.id
            AND disposition_observation.owner_org_id =
                disposition_scope.owner_org_id
            AND disposition_observation.location_id =
                disposition_scope.location_id
            AND disposition_observation.custodian_person_id_snapshot
                IS NOT DISTINCT FROM
                disposition_scope.custodian_person_id_snapshot
            AND disposition_observation.verification_status =
                'pending_verification'
            AND disposition_row.created_at = disposition_row.decided_at
            AND disposition_row.decided_at >=
                disposition_submission.submitted_at
            AND public.{ROUND_SUBMISSION_FUNCTION}(
                disposition_task.id,
                disposition_round.id,
                TRUE
            )
            AND disposition_completion.task_id = disposition_task.id
            AND disposition_completion.round_id = disposition_round.id
            AND disposition_completion.round_submission_id =
                disposition_submission.id
            AND disposition_difference.task_id = disposition_task.id
            AND disposition_difference.round_id = disposition_round.id
            AND disposition_difference.scope_id = disposition_scope.id
            AND disposition_difference.observed_line_id =
                disposition_observation.id
            AND disposition_difference.difference_type = 'excess'
            AND disposition_difference.observed_account_id IS NULL
            AND disposition_difference.reason_code =
                'opening_pending_verification'
            AND (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_differences AS exact_disposition_difference
                 WHERE exact_disposition_difference.task_id =
                       disposition_task.id
                   AND exact_disposition_difference.round_id =
                       disposition_round.id
                   AND exact_disposition_difference.observed_line_id =
                       disposition_observation.id
            ) = 1
            AND ({actor})
            AND (
                (
                    disposition_row.role_code = 'admin'
                    AND disposition_row.scope_type = 'national'
                    AND disposition_row.scope_id_snapshot = '*'
                )
                OR (
                    disposition_row.disposition <>
                        'resolved_existing_master'
                    AND disposition_row.role_code = 'provincial_manager'
                    AND disposition_row.scope_type = 'organization'
                    AND disposition_row.scope_id_snapshot =
                        disposition_task.region_org_id::text
                )
            )
            AND disposition_row.authorization_sha256 = ({authorization})
            AND disposition_row.request_sha256 = ({request})
            AND disposition_row.disposition_manifest_sha256 = ({manifest})
            AND ({resolution})
            AND ({current_only})
            AND ({audit})
        )"""

    historical = proof(historical=True)
    current = proof(historical=False)
    return f"""
SELECT CASE
    WHEN p_disposition_id IS NULL OR p_historical IS NULL THEN FALSE
    WHEN p_historical THEN COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_observation_dispositions AS disposition_row
          JOIN public.stocktake_tasks AS disposition_task
            ON disposition_task.id = disposition_row.task_id
          JOIN public.stocktake_rounds AS disposition_round
            ON disposition_round.id = disposition_row.round_id
           AND disposition_round.task_id = disposition_task.id
          JOIN public.stocktake_scopes AS disposition_scope
            ON disposition_scope.id = disposition_row.scope_id
           AND disposition_scope.task_id = disposition_task.id
          JOIN public.stock_locations AS disposition_location
            ON disposition_location.id = disposition_scope.location_id
          JOIN public.stocktake_count_observations AS disposition_observation
            ON disposition_observation.id = disposition_row.observation_id
          JOIN public.stocktake_round_submissions AS disposition_submission
            ON disposition_submission.task_id = disposition_task.id
           AND disposition_submission.round_id = disposition_round.id
          JOIN public.stocktake_difference_set_completions
               AS disposition_completion
            ON disposition_completion.task_id = disposition_task.id
           AND disposition_completion.round_id = disposition_round.id
          JOIN public.stocktake_differences AS disposition_difference
            ON disposition_difference.task_id = disposition_task.id
           AND disposition_difference.round_id = disposition_round.id
           AND disposition_difference.observed_line_id =
               disposition_observation.id
         WHERE disposition_row.id = p_disposition_id
           AND {historical}
    ), FALSE)
    ELSE COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_observation_dispositions AS disposition_row
          JOIN public.stocktake_tasks AS disposition_task
            ON disposition_task.id = disposition_row.task_id
          JOIN public.stocktake_rounds AS disposition_round
            ON disposition_round.id = disposition_row.round_id
           AND disposition_round.task_id = disposition_task.id
          JOIN public.stocktake_scopes AS disposition_scope
            ON disposition_scope.id = disposition_row.scope_id
           AND disposition_scope.task_id = disposition_task.id
          JOIN public.stock_locations AS disposition_location
            ON disposition_location.id = disposition_scope.location_id
          JOIN public.stocktake_count_observations AS disposition_observation
            ON disposition_observation.id = disposition_row.observation_id
          JOIN public.stocktake_round_submissions AS disposition_submission
            ON disposition_submission.task_id = disposition_task.id
           AND disposition_submission.round_id = disposition_round.id
          JOIN public.stocktake_difference_set_completions
               AS disposition_completion
            ON disposition_completion.task_id = disposition_task.id
           AND disposition_completion.round_id = disposition_round.id
          JOIN public.stocktake_differences AS disposition_difference
            ON disposition_difference.task_id = disposition_task.id
           AND disposition_difference.round_id = disposition_round.id
           AND disposition_difference.observed_line_id =
               disposition_observation.id
         WHERE disposition_row.id = p_disposition_id
           AND {current}
    ), FALSE)
END
""".strip()


def _recount_graph_helper_body() -> str:
    def proof(*, historical: bool) -> str:
        replay = _recount_case_replay_proof_sql(
            task_id_sql="recount_task.id",
            region_org_id_sql="recount_task.region_org_id",
            task_scope_manifest_sql="recount_task.scope_manifest_sha256",
            case_alias="recount_case",
            source_round_alias="recount_source_round",
            submission_alias="recount_submission",
            sealing_alias="recount_sealing",
            completion_alias="recount_difference_completion",
            trigger_review_alias="recount_trigger_review",
            historical=historical,
        )
        effects = _recount_side_effect_proof_sql(
            "recount_task.id",
            "recount_case",
            "recount_round",
            historical=historical,
        )
        current_master = _current_scope_master_proof_sql(
            "recount_task.id",
            "recount_task.region_org_id",
            alias_suffix="recount_helper",
        )
        current_only = "TRUE" if historical else f"""(
            recount_task.status = 'counting'
            AND recount_task.current_round_no = recount_case.next_round_no
            AND recount_task.updated_at = recount_case.opened_at
            AND recount_round.status = 'counting'
            AND recount_round.started_at = recount_case.opened_at
            AND recount_round.created_at = recount_case.opened_at
            AND recount_round.updated_at = recount_case.opened_at
            AND recount_round.submitted_by_user_id IS NULL
            AND recount_round.submitted_at IS NULL
            AND recount_round.count_manifest_sha256 IS NULL
            AND ({current_master})
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_round_submissions
                       AS premature_recount_submission
                 WHERE premature_recount_submission.task_id = recount_task.id
                   AND premature_recount_submission.round_id = recount_round.id
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_scope_count_completions
                       AS premature_recount_completion
                 WHERE premature_recount_completion.task_id = recount_task.id
                   AND premature_recount_completion.round_id = recount_round.id
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_count_lines AS premature_recount_line
                 WHERE premature_recount_line.task_id = recount_task.id
                   AND premature_recount_line.round_id = recount_round.id
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_count_observations
                       AS premature_recount_observation
                 WHERE premature_recount_observation.task_id = recount_task.id
                   AND premature_recount_observation.round_id = recount_round.id
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_differences
                       AS premature_recount_difference
                 WHERE premature_recount_difference.task_id = recount_task.id
                   AND premature_recount_difference.round_id = recount_round.id
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_reviews AS premature_recount_review
                 WHERE premature_recount_review.task_id = recount_task.id
                   AND premature_recount_review.round_id = recount_round.id
            )
        )"""
        return f"""(
            recount_task.task_type = 'opening'
            AND recount_case.created_at = recount_case.opened_at
            AND recount_source_round.task_id = recount_task.id
            AND recount_source_round.status = 'submitted'
            AND recount_source_round.submitted_at IS NOT NULL
            AND recount_source_round.updated_at =
                recount_source_round.submitted_at
            AND recount_submission.task_id = recount_task.id
            AND recount_submission.round_id = recount_source_round.id
            AND recount_sealing.id = recount_submission.sealing_completion_id
            AND recount_sealing.task_id = recount_task.id
            AND recount_sealing.round_id = recount_source_round.id
            AND recount_difference_completion.id =
                recount_case.source_difference_completion_id
            AND recount_trigger_review.id = recount_case.trigger_review_id
            AND recount_round.task_id = recount_task.id
            AND recount_round.recount_case_id = recount_case.id
            AND recount_round.round_no = recount_case.next_round_no
            AND recount_round.round_type = 'recount'
            AND (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_rounds AS exact_recount_round
                 WHERE exact_recount_round.task_id = recount_task.id
                   AND exact_recount_round.recount_case_id = recount_case.id
                   AND exact_recount_round.round_no =
                       recount_case.next_round_no
            ) = 1
            AND public.{ROUND_SUBMISSION_FUNCTION}(
                recount_task.id,
                recount_source_round.id,
                TRUE
            )
            AND ({replay})
            AND ({effects})
            AND ({current_only})
        )"""

    historical = proof(historical=True)
    current = proof(historical=False)
    return f"""
SELECT CASE
    WHEN p_recount_case_id IS NULL OR p_historical IS NULL THEN FALSE
    WHEN p_historical THEN COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_recount_cases AS recount_case
          JOIN public.stocktake_tasks AS recount_task
            ON recount_task.id = recount_case.task_id
          JOIN public.stocktake_rounds AS recount_source_round
            ON recount_source_round.id = recount_case.source_round_id
          JOIN public.stocktake_round_submissions AS recount_submission
            ON recount_submission.id =
               recount_case.source_round_submission_id
          JOIN public.stocktake_scope_count_completions AS recount_sealing
            ON recount_sealing.id = recount_submission.sealing_completion_id
          JOIN public.stocktake_difference_set_completions
               AS recount_difference_completion
            ON recount_difference_completion.id =
               recount_case.source_difference_completion_id
          JOIN public.stocktake_reviews AS recount_trigger_review
            ON recount_trigger_review.id = recount_case.trigger_review_id
          JOIN public.stocktake_rounds AS recount_round
            ON recount_round.recount_case_id = recount_case.id
           AND recount_round.task_id = recount_task.id
           AND recount_round.round_no = recount_case.next_round_no
         WHERE recount_case.id = p_recount_case_id
           AND {historical}
    ), FALSE)
    ELSE COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_recount_cases AS recount_case
          JOIN public.stocktake_tasks AS recount_task
            ON recount_task.id = recount_case.task_id
          JOIN public.stocktake_rounds AS recount_source_round
            ON recount_source_round.id = recount_case.source_round_id
          JOIN public.stocktake_round_submissions AS recount_submission
            ON recount_submission.id =
               recount_case.source_round_submission_id
          JOIN public.stocktake_scope_count_completions AS recount_sealing
            ON recount_sealing.id = recount_submission.sealing_completion_id
          JOIN public.stocktake_difference_set_completions
               AS recount_difference_completion
            ON recount_difference_completion.id =
               recount_case.source_difference_completion_id
          JOIN public.stocktake_reviews AS recount_trigger_review
            ON recount_trigger_review.id = recount_case.trigger_review_id
          JOIN public.stocktake_rounds AS recount_round
            ON recount_round.recount_case_id = recount_case.id
           AND recount_round.task_id = recount_task.id
           AND recount_round.round_no = recount_case.next_round_no
         WHERE recount_case.id = p_recount_case_id
           AND {current}
    ), FALSE)
END
""".strip()


def _opening_finalize_event_key_sql(kind: str, anchor_sql: str) -> str:
    if kind not in {"post-state", "post-outbox", "close-state", "close-outbox"}:
        raise ValueError("unsupported opening finalize event kind")
    return f"""'opening-finalize-{kind}-' || pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                'cloud_oam.opening_stocktake.finalize.event.{kind}.v1',
                'UTF8'
            ) || pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to(({anchor_sql})::text, 'UTF8')
        ),
        'hex'
    )"""


def _inventory_event_key_sql(
    kind: str,
    transaction_id_sql: str,
    suffix: str,
) -> str:
    if kind not in {"state", "outbox"} or suffix != "posted":
        raise ValueError("unsupported inventory event key")
    return f"""'inventory-{kind}-' || pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                'cloud_oam.inventory.{kind}.v1',
                'UTF8'
            ) || pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to(({transaction_id_sql})::text, 'UTF8') ||
            pg_catalog.decode('00', 'hex') ||
            pg_catalog.convert_to('{suffix}', 'UTF8')
        ),
        'hex'
    )"""


def _terminal_admin_snapshot_sql(
    audit_alias: str,
    *,
    historical: bool,
    alias_suffix: str,
) -> str:
    after = f"{audit_alias}.after_jsonb"
    base = f"""(
        {audit_alias}.actor_user_id = {after} ->> 'finalizer_user_id'
        AND ({after} ->> 'finalizer_role_assignment_id') ~
            '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[1-5][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
        AND ({after} ->> 'finalizer_person_id') ~
            '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[1-5][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
        AND ({after} ->> 'finalizer_organization_id') ~
            '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[1-5][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
        AND pg_catalog.jsonb_typeof(
                {after} -> 'authorization_version'
            ) = 'number'
        AND ({after} ->> 'authorization_version')::bigint > 0
        AND {after} ->> 'finalizer_role_code' = 'admin'
        AND {after} -> 'finalizer_role_is_external' = 'false'::jsonb
        AND {after} ->> 'assignment_scope_type' = 'national'
        AND {after} ->> 'assignment_scope_id' = '*'
        AND {after} ->> 'finalizer_organization_type' = 'headquarters'
        AND {after} ->> 'assignment_valid_from' ~
            '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}\\.[0-9]{{6}}Z$'
        AND ({after} ->> 'assignment_valid_from')::timestamptz <=
            {audit_alias}.occurred_at
        AND (
            {after} -> 'assignment_valid_to' = 'null'::jsonb
            OR (
                {after} ->> 'assignment_valid_to' ~
                    '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}\\.[0-9]{{6}}Z$'
                AND {audit_alias}.occurred_at <
                    ({after} ->> 'assignment_valid_to')::timestamptz
            )
        )
        AND (
            {after} -> 'assignment_revoked_at' = 'null'::jsonb
            OR (
                {after} ->> 'assignment_revoked_at' ~
                    '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}\\.[0-9]{{6}}Z$'
                AND {audit_alias}.occurred_at <
                    ({after} ->> 'assignment_revoked_at')::timestamptz
            )
        )
    )"""
    if historical:
        return base
    current = _current_authorization_sql(
        user_id_sql=f"{audit_alias}.actor_user_id",
        person_id_sql=(
            f"({after} ->> 'finalizer_person_id')::uuid"
        ),
        assignment_id_sql=(
            f"({after} ->> 'finalizer_role_assignment_id')::uuid"
        ),
        authorization_version_sql=(
            f"({after} ->> 'authorization_version')::bigint"
        ),
        occurred_at_sql=f"{audit_alias}.occurred_at",
        role_code_sql="'admin'",
        scope_type_sql="'national'",
        scope_id_sql="'*'",
        permission_resource="stocktake",
        permission_action="post_opening",
        allow_scheduled=True,
        alias_suffix=alias_suffix,
    )
    current_snapshot = f"""EXISTS (
        SELECT 1
          FROM public.role_assignments AS terminal_assignment_{alias_suffix}
          JOIN public.roles AS terminal_role_{alias_suffix}
            ON terminal_role_{alias_suffix}.id =
               terminal_assignment_{alias_suffix}.role_id
          JOIN public.users AS terminal_user_{alias_suffix}
            ON terminal_user_{alias_suffix}.id =
               terminal_assignment_{alias_suffix}.user_id
          JOIN public.people AS terminal_person_{alias_suffix}
            ON terminal_person_{alias_suffix}.id =
               terminal_user_{alias_suffix}.person_id
          JOIN public.organizations AS terminal_org_{alias_suffix}
            ON terminal_org_{alias_suffix}.id =
               terminal_person_{alias_suffix}.organization_id
         WHERE terminal_assignment_{alias_suffix}.id::text =
               {after} ->> 'finalizer_role_assignment_id'
           AND terminal_assignment_{alias_suffix}.user_id =
               {audit_alias}.actor_user_id
           AND terminal_person_{alias_suffix}.id::text =
               {after} ->> 'finalizer_person_id'
           AND terminal_org_{alias_suffix}.id::text =
               {after} ->> 'finalizer_organization_id'
           AND terminal_org_{alias_suffix}.org_type = 'headquarters'
           AND terminal_org_{alias_suffix}.status = 'active'
           AND terminal_role_{alias_suffix}.code = 'admin'
           AND terminal_role_{alias_suffix}.status = 'active'
           AND NOT terminal_role_{alias_suffix}.is_external
           AND terminal_assignment_{alias_suffix}.scope_type = 'national'
           AND terminal_assignment_{alias_suffix}.scope_id = '*'
           AND pg_catalog.to_char(
                   pg_catalog.timezone(
                       'UTC', terminal_assignment_{alias_suffix}.valid_from
                   ),
                   'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'
               ) = {after} ->> 'assignment_valid_from'
           AND CASE
                   WHEN terminal_assignment_{alias_suffix}.valid_to IS NULL
                   THEN {after} -> 'assignment_valid_to' = 'null'::jsonb
                   ELSE pg_catalog.to_char(
                       pg_catalog.timezone(
                           'UTC', terminal_assignment_{alias_suffix}.valid_to
                       ),
                       'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'
                   ) = {after} ->> 'assignment_valid_to'
               END
           AND CASE
                   WHEN terminal_assignment_{alias_suffix}.revoked_at IS NULL
                   THEN {after} -> 'assignment_revoked_at' = 'null'::jsonb
                   ELSE pg_catalog.to_char(
                       pg_catalog.timezone(
                           'UTC', terminal_assignment_{alias_suffix}.revoked_at
                       ),
                       'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'
                   ) = {after} ->> 'assignment_revoked_at'
               END
    )"""
    return f"(({base}) AND ({current}) AND ({current_snapshot}))"


def _terminal_task_effect_proof_sql(
    task_alias: str,
    posting_alias: str,
    *,
    operation: str,
    historical: bool,
) -> str:
    if operation not in {"post", "close"}:
        raise ValueError("unsupported terminal operation")
    is_post = operation == "post"
    event_type = f"stocktake.opening.{'posted' if is_post else 'closed'}"
    reason = f"opening_stocktake_{'posted' if is_post else 'closed'}"
    from_status = "approved" if is_post else "posted"
    to_status = "posted" if is_post else "closed"
    occurred_at = (
        f"{posting_alias}.posted_at" if is_post else f"{task_alias}.closed_at"
    )
    aggregate_type = "stocktake_posting" if is_post else "stocktake_task"
    aggregate_id = (
        f"{posting_alias}.id::text" if is_post else f"{task_alias}.id::text"
    )
    task_version = (
        f"CASE WHEN {task_alias}.status = 'closed' "
        f"THEN {task_alias}.version - 1 ELSE {task_alias}.version END"
        if is_post
        else f"{task_alias}.version"
    )
    if is_post:
        anchor = f"{posting_alias}.id::text"
        metadata = f"""pg_catalog.jsonb_build_object(
            'established_scope_count', (
                SELECT pg_catalog.count(*)
                  FROM public.inventory_opening_establishments
                       AS terminal_establishment
                 WHERE terminal_establishment.posting_id = {posting_alias}.id
            ),
            'inventory_transaction_id',
                {posting_alias}.inventory_transaction_id::text,
            'ledger_cursor', COALESCE(
                (
                    SELECT terminal_transaction.ledger_cursor
                      FROM public.inventory_transactions AS terminal_transaction
                     WHERE terminal_transaction.id =
                           {posting_alias}.inventory_transaction_id
                ),
                {task_alias}.cutoff_ledger_cursor,
                0
            ),
            'pending_control_difference_count', (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_differences
                       AS terminal_control_difference
                 WHERE terminal_control_difference.task_id = {task_alias}.id
                   AND terminal_control_difference.round_id =
                       {posting_alias}.round_id
                   AND terminal_control_difference.difference_type =
                       'control_unassigned'
            ),
            'posting_id', {posting_alias}.id::text,
            'round_id', {posting_alias}.round_id::text,
            'task_version', {task_version},
            'total_quantity',
                {_canonical_quantity_sql(f'{posting_alias}.total_quantity')}
        )"""
    else:
        anchor = (
            "terminal_state.metadata_jsonb ->> 'idempotency_key_hash'"
        )
        metadata = f"""pg_catalog.jsonb_build_object(
            'idempotency_key_hash',
                terminal_state.metadata_jsonb ->> 'idempotency_key_hash',
            'inventory_transaction_id',
                {posting_alias}.inventory_transaction_id::text,
            'posting_id', {posting_alias}.id::text,
            'request_hash',
                terminal_state.metadata_jsonb ->> 'request_hash',
            'task_version', {task_version}
        )"""
    state_key = _opening_finalize_event_key_sql(
        f"{operation}-state", anchor
    )
    outbox_key = _opening_finalize_event_key_sql(
        f"{operation}-outbox", anchor
    )
    fresh_outbox = "TRUE" if historical else """(
        terminal_outbox.status = 'pending'
        AND terminal_outbox.attempts = 0
        AND terminal_outbox.locked_at IS NULL
        AND terminal_outbox.locked_by IS NULL
        AND terminal_outbox.published_at IS NULL
        AND terminal_outbox.last_error IS NULL
        AND terminal_outbox.updated_at = terminal_outbox.created_at
    )"""
    snapshot = _terminal_admin_snapshot_sql(
        "terminal_audit",
        historical=historical,
        alias_suffix=f"terminal_{operation}",
    )
    head = _audit_event_chain_binding_sql(
        "terminal_audit",
        require_head=not historical,
    )
    close_hashes = (
        "TRUE"
        if is_post
        else """(
            terminal_state.metadata_jsonb ->> 'idempotency_key_hash' ~
                '^[0-9a-f]{64}$'
            AND terminal_state.metadata_jsonb ->> 'request_hash' ~
                '^[0-9a-f]{64}$'
        )"""
    )
    audit_after = f"""({metadata}) || pg_catalog.jsonb_build_object(
        'assignment_revoked_at',
            terminal_audit.after_jsonb -> 'assignment_revoked_at',
        'assignment_scope_id',
            terminal_audit.after_jsonb ->> 'assignment_scope_id',
        'assignment_scope_type',
            terminal_audit.after_jsonb ->> 'assignment_scope_type',
        'assignment_valid_from',
            terminal_audit.after_jsonb ->> 'assignment_valid_from',
        'assignment_valid_to',
            terminal_audit.after_jsonb -> 'assignment_valid_to',
        'authorization_version',
            (terminal_audit.after_jsonb ->> 'authorization_version')::bigint,
        'finalizer_organization_id',
            terminal_audit.after_jsonb ->> 'finalizer_organization_id',
        'finalizer_organization_type',
            terminal_audit.after_jsonb ->> 'finalizer_organization_type',
        'finalizer_person_id',
            terminal_audit.after_jsonb ->> 'finalizer_person_id',
        'finalizer_role_assignment_id',
            terminal_audit.after_jsonb ->> 'finalizer_role_assignment_id',
        'finalizer_role_code',
            terminal_audit.after_jsonb ->> 'finalizer_role_code',
        'finalizer_role_is_external',
            terminal_audit.after_jsonb -> 'finalizer_role_is_external',
        'finalizer_user_id',
            terminal_audit.after_jsonb ->> 'finalizer_user_id',
        'status', '{to_status}'
    )"""
    return f"""(
        (SELECT pg_catalog.count(*)
           FROM public.state_transition_events AS terminal_state
          WHERE terminal_state.aggregate_type = 'stocktake_task'
            AND terminal_state.aggregate_id = {task_alias}.id::text
            AND terminal_state.from_status = '{from_status}'
            AND terminal_state.to_status = '{to_status}'
            AND terminal_state.reason = '{reason}'
            AND terminal_state.actor_id IS NOT NULL
            AND terminal_state.idempotency_key = {state_key}
            AND terminal_state.metadata_jsonb = {metadata}
            AND terminal_state.occurred_at = {occurred_at}
            AND terminal_state.created_at = {occurred_at}
            AND ({close_hashes})
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.state_transition_events AS terminal_state_candidate
              WHERE terminal_state_candidate.aggregate_type = 'stocktake_task'
                AND terminal_state_candidate.aggregate_id = {task_alias}.id::text
                AND terminal_state_candidate.reason = '{reason}'
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.outbox_events AS terminal_outbox
              JOIN public.state_transition_events AS terminal_state
                ON terminal_state.aggregate_type = 'stocktake_task'
               AND terminal_state.aggregate_id = {task_alias}.id::text
               AND terminal_state.reason = '{reason}'
             WHERE terminal_outbox.aggregate_type = 'stocktake_task'
               AND terminal_outbox.aggregate_id = {task_alias}.id::text
               AND terminal_outbox.event_type = '{event_type}'
               AND terminal_outbox.idempotency_key = {outbox_key}
               AND terminal_outbox.payload_jsonb = {metadata}
               AND terminal_outbox.available_at = {occurred_at}
               AND terminal_outbox.created_at = {occurred_at}
               AND terminal_outbox.updated_at >= terminal_outbox.created_at
               AND (
                   terminal_outbox.locked_at IS NULL
                   OR terminal_outbox.locked_at >= terminal_outbox.created_at
               )
               AND (
                   terminal_outbox.published_at IS NULL
                   OR terminal_outbox.published_at >= terminal_outbox.created_at
               )
               AND ({fresh_outbox})
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.outbox_events AS terminal_outbox_candidate
              WHERE terminal_outbox_candidate.aggregate_type = 'stocktake_task'
                AND terminal_outbox_candidate.aggregate_id = {task_alias}.id::text
                AND terminal_outbox_candidate.event_type = '{event_type}'
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS terminal_audit
              JOIN public.state_transition_events AS terminal_state
                ON terminal_state.aggregate_type = 'stocktake_task'
               AND terminal_state.aggregate_id = {task_alias}.id::text
               AND terminal_state.reason = '{reason}'
             WHERE terminal_audit.stream_key = 'inventory'
               AND terminal_audit.aggregate_type = '{aggregate_type}'
               AND terminal_audit.aggregate_id = {aggregate_id}
               AND terminal_audit.action = '{event_type}'
               AND terminal_audit.actor_user_id = terminal_state.actor_id
               AND terminal_audit.before_jsonb =
                   pg_catalog.jsonb_build_object(
                       'status', '{from_status}',
                       'version', ({task_version}) - 1
                   )
               AND terminal_audit.after_jsonb = {audit_after}
               AND terminal_audit.request_id ~
                   '^opening-finalize-request-[0-9a-f]{{64}}$'
               AND terminal_audit.occurred_at = {occurred_at}
               AND terminal_audit.created_at >= {occurred_at}
               AND ({snapshot})
               AND ({head})
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS terminal_audit_candidate
              WHERE terminal_audit_candidate.aggregate_type = '{aggregate_type}'
                AND terminal_audit_candidate.aggregate_id = {aggregate_id}
                {"" if is_post else "AND terminal_audit_candidate.action = 'stocktake.opening.closed'"}
        ) = 1
    )"""


def _opening_inventory_transaction_effect_proof_sql(
    transaction_alias: str,
    *,
    historical: bool,
) -> str:
    state_key = _inventory_event_key_sql(
        "state", f"{transaction_alias}.id", "posted"
    )
    outbox_key = _inventory_event_key_sql(
        "outbox", f"{transaction_alias}.id", "posted"
    )
    fresh_outbox = "TRUE" if historical else """(
        inventory_outbox.status = 'pending'
        AND inventory_outbox.attempts = 0
        AND inventory_outbox.locked_at IS NULL
        AND inventory_outbox.locked_by IS NULL
        AND inventory_outbox.published_at IS NULL
        AND inventory_outbox.last_error IS NULL
        AND inventory_outbox.updated_at = inventory_outbox.created_at
    )"""
    audit_chain = _audit_event_chain_binding_sql(
        "inventory_audit",
        require_head=False,
    )
    return f"""(
        {transaction_alias}.movement_type = 'opening'
        AND (SELECT pg_catalog.count(*)
               FROM public.state_transition_events AS inventory_state
              WHERE inventory_state.aggregate_type = 'inventory_transaction'
                AND inventory_state.aggregate_id = {transaction_alias}.id::text
                AND inventory_state.from_status IS NULL
                AND inventory_state.to_status = 'posted'
                AND inventory_state.reason = 'inventory_transaction_posted'
                AND inventory_state.actor_id = {transaction_alias}.actor_user_id
                AND inventory_state.idempotency_key = {state_key}
                AND inventory_state.metadata_jsonb =
                    pg_catalog.jsonb_build_object(
                        'ledger_cursor', {transaction_alias}.ledger_cursor,
                        'movement_type', 'opening',
                        'request_reference',
                            inventory_state.metadata_jsonb ->> 'request_reference'
                    )
                AND inventory_state.metadata_jsonb ->> 'request_reference' ~
                    '^inventory-request-[0-9a-f]{{64}}$'
                AND inventory_state.occurred_at = {transaction_alias}.posted_at
                AND inventory_state.created_at = {transaction_alias}.posted_at
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.outbox_events AS inventory_outbox
              WHERE inventory_outbox.aggregate_type = 'inventory_transaction'
                AND inventory_outbox.aggregate_id = {transaction_alias}.id::text
                AND inventory_outbox.event_type = 'inventory.transaction.posted'
                AND inventory_outbox.idempotency_key = {outbox_key}
                AND inventory_outbox.payload_jsonb =
                    pg_catalog.jsonb_build_object(
                        'transaction_id', {transaction_alias}.id::text,
                        'transaction_no', {transaction_alias}.transaction_no,
                        'movement_type', 'opening',
                        'ledger_cursor', {transaction_alias}.ledger_cursor,
                        'reversed_transaction_id', NULL
                    )
                AND inventory_outbox.available_at = {transaction_alias}.posted_at
                AND inventory_outbox.created_at = {transaction_alias}.posted_at
                AND inventory_outbox.updated_at >= inventory_outbox.created_at
                AND ({fresh_outbox})
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS inventory_audit
              JOIN public.state_transition_events AS inventory_state
                ON inventory_state.aggregate_type = 'inventory_transaction'
               AND inventory_state.aggregate_id = {transaction_alias}.id::text
             WHERE inventory_audit.stream_key = 'inventory'
               AND inventory_audit.aggregate_type = 'inventory_transaction'
               AND inventory_audit.aggregate_id = {transaction_alias}.id::text
               AND inventory_audit.action = 'inventory.transaction.posted'
               AND inventory_audit.actor_user_id =
                   {transaction_alias}.actor_user_id
               AND inventory_audit.before_jsonb IS NULL
               AND inventory_audit.after_jsonb =
                   pg_catalog.jsonb_build_object(
                       'ledger_cursor', {transaction_alias}.ledger_cursor,
                       'movement_count', (
                           SELECT pg_catalog.count(*)
                             FROM public.inventory_movements
                                  AS opening_movement
                            WHERE opening_movement.transaction_id =
                                  {transaction_alias}.id
                       ),
                       'movement_type', 'opening',
                       'posting_key', {transaction_alias}.posting_key,
                       'reversed_transaction_id', NULL,
                       'status', 'posted'
                   )
               AND inventory_audit.request_id =
                   inventory_state.metadata_jsonb ->> 'request_reference'
               AND inventory_audit.occurred_at = {transaction_alias}.posted_at
               AND inventory_audit.created_at >= {transaction_alias}.posted_at
               AND ({audit_chain})
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.state_transition_events AS inventory_state_candidate
              WHERE inventory_state_candidate.aggregate_type =
                    'inventory_transaction'
                AND inventory_state_candidate.aggregate_id =
                    {transaction_alias}.id::text
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.outbox_events AS inventory_outbox_candidate
              WHERE inventory_outbox_candidate.aggregate_type =
                    'inventory_transaction'
                AND inventory_outbox_candidate.aggregate_id =
                    {transaction_alias}.id::text
        ) = 1
        AND (SELECT pg_catalog.count(*)
               FROM public.audit_events AS inventory_audit_candidate
              WHERE inventory_audit_candidate.aggregate_type =
                    'inventory_transaction'
                AND inventory_audit_candidate.aggregate_id =
                    {transaction_alias}.id::text
        ) = 1
    )"""


def _terminal_graph_helper_body() -> str:
    def proof(*, historical: bool) -> str:
        post_historical = _terminal_task_effect_proof_sql(
            "terminal_task",
            "terminal_posting",
            operation="post",
            historical=True,
        )
        post_current = _terminal_task_effect_proof_sql(
            "terminal_task",
            "terminal_posting",
            operation="post",
            historical=False,
        )
        close_historical = _terminal_task_effect_proof_sql(
            "terminal_task",
            "terminal_posting",
            operation="close",
            historical=True,
        )
        close_current = _terminal_task_effect_proof_sql(
            "terminal_task",
            "terminal_posting",
            operation="close",
            historical=False,
        )
        transaction_historical = _opening_inventory_transaction_effect_proof_sql(
            "terminal_transaction",
            historical=True,
        )
        transaction_current = _opening_inventory_transaction_effect_proof_sql(
            "terminal_transaction",
            historical=False,
        )
        current_master = _current_scope_master_proof_sql(
            "terminal_task.id",
            "terminal_task.region_org_id",
            alias_suffix="terminal_post",
        )
        if historical:
            effects = f"""(
                ({post_historical})
                AND (
                    terminal_task.status <> 'closed'
                    OR ({close_historical})
                )
                AND (
                    terminal_posting.inventory_transaction_id IS NULL
                    OR ({transaction_historical})
                )
            )"""
        else:
            effects = f"""(
                (
                    terminal_task.status = 'posted'
                    AND terminal_task.updated_at = terminal_task.posted_at
                    AND ({current_master})
                    AND ({post_current})
                    AND (
                        terminal_posting.inventory_transaction_id IS NULL
                        OR ({transaction_current})
                    )
                )
                OR
                (
                    terminal_task.status = 'closed'
                    AND terminal_task.updated_at = terminal_task.closed_at
                    AND ({post_historical})
                    AND ({close_current})
                    AND (
                        terminal_posting.inventory_transaction_id IS NULL
                        OR ({transaction_historical})
                    )
                )
            )"""
        return f"""(
            terminal_task.task_type = 'opening'
            AND terminal_task.status IN ('posted', 'closed')
            AND terminal_task.posted_at IS NOT NULL
            AND terminal_posting.task_id = terminal_task.id
            AND terminal_posting.posting_kind = 'opening'
            AND terminal_posting.posted_at = terminal_task.posted_at
            AND terminal_posting.created_at = terminal_posting.posted_at
            AND terminal_posting.round_id = terminal_round.id
            AND terminal_round.task_id = terminal_task.id
            AND terminal_round.round_no = terminal_task.current_round_no
            AND terminal_round.status = 'submitted'
            AND (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_postings AS exact_terminal_posting
                 WHERE exact_terminal_posting.task_id = terminal_task.id
                   AND exact_terminal_posting.posting_kind = 'opening'
            ) = 1
            AND public.{GRAPH_FUNCTION}(
                terminal_task.id,
                terminal_posting.inventory_transaction_id
            )
            AND (
                terminal_posting.inventory_transaction_id IS NULL
                OR terminal_transaction.id =
                   terminal_posting.inventory_transaction_id
            )
            AND (
                terminal_task.status = 'posted'
                AND terminal_task.closed_at IS NULL
                OR terminal_task.status = 'closed'
                AND terminal_task.closed_at > terminal_task.posted_at
            )
            AND ({effects})
        )"""

    historical = proof(historical=True)
    current = proof(historical=False)
    return f"""
SELECT CASE
    WHEN p_task_id IS NULL OR p_historical IS NULL THEN FALSE
    WHEN p_historical THEN COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_tasks AS terminal_task
          JOIN public.stocktake_postings AS terminal_posting
            ON terminal_posting.task_id = terminal_task.id
           AND terminal_posting.posting_kind = 'opening'
          JOIN public.stocktake_rounds AS terminal_round
            ON terminal_round.id = terminal_posting.round_id
          LEFT JOIN public.inventory_transactions AS terminal_transaction
            ON terminal_transaction.id =
               terminal_posting.inventory_transaction_id
         WHERE terminal_task.id = p_task_id
           AND {historical}
    ), FALSE)
    ELSE COALESCE((
        SELECT pg_catalog.count(*) = 1
          FROM public.stocktake_tasks AS terminal_task
          JOIN public.stocktake_postings AS terminal_posting
            ON terminal_posting.task_id = terminal_task.id
           AND terminal_posting.posting_kind = 'opening'
          JOIN public.stocktake_rounds AS terminal_round
            ON terminal_round.id = terminal_posting.round_id
          LEFT JOIN public.inventory_transactions AS terminal_transaction
            ON terminal_transaction.id =
               terminal_posting.inventory_transaction_id
         WHERE terminal_task.id = p_task_id
           AND {current}
    ), FALSE)
END
""".strip()


START_GRAPH_BODY = _opening_start_graph_helper_body()
ROUND_SUBMISSION_BODY = _round_submission_helper_body()
SCOPE_COMPLETION_BODY = _scope_completion_helper_body()
REVIEW_GRAPH_BODY = _review_graph_helper_body()
RECOUNT_GRAPH_BODY = _recount_graph_helper_body()
DISPOSITION_GRAPH_BODY = _disposition_graph_helper_body()
TERMINAL_GRAPH_BODY = _terminal_graph_helper_body()
# Updated mechanically after the SQL bodies are finalized.  Catalog checks
# fail closed if either source changes without its digest changing with it.
START_GRAPH_BODY_SHA256 = (
    "fe1874929ac02dbc1aa590dd0aa3fef03a853e0845849f06943be906aa55491a"
)
ROUND_SUBMISSION_BODY_SHA256 = (
    "29d1e2b9c3ed9cdec240c91134497469fb193e7b88d9ce63fcdec70c46008afa"
)
SCOPE_COMPLETION_BODY_SHA256 = (
    "b56ff329437860ba3f3011e695df0b56606368cbb33c61ae7b921b3b07a18681"
)
REVIEW_GRAPH_BODY_SHA256 = (
    "875ee69968300febafc15cace5b5df7d1f4520ecbff7d071d6132858f205e1ad"
)
RECOUNT_GRAPH_BODY_SHA256 = (
    "0372b5fe0a8c2316c6d73f9152495bfabc3b65a3c4e258b180c5df29188ed7a3"
)
DISPOSITION_GRAPH_BODY_SHA256 = (
    "a311f39beff3ec429129d14abf51ea0a1abb7d31971df5248978f44420ba8ccb"
)
TERMINAL_GRAPH_BODY_SHA256 = (
    "33b466605ce587ee7a3505d523e45f8bc6e877060e22a988169724a0fdbdec76"
)


def _opening_insert_guard_body() -> str:
    return f"""
BEGIN
    IF NEW.task_type <> 'opening' THEN
        RETURN NEW;
    END IF;
    IF NOT public.{START_GRAPH_FUNCTION}(NEW.id, FALSE) THEN
        RAISE EXCEPTION '{OPENING_INSERT_ERROR}' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
""".strip()


OPENING_INSERT_ERROR = "0052 opening stocktake insert graph is not canonical"
INSERT_GUARD_FUNCTION = "rsc_require_opening_task_insert_graph_0052"
INSERT_GUARD_SIGNATURE = f"public.{INSERT_GUARD_FUNCTION}()"
INSERT_GUARD_TRIGGER = "trg_stocktake_tasks_opening_insert_graph_0052"
INSERT_GUARD_BODY = _opening_insert_guard_body()
INSERT_GUARD_BODY_SHA256 = (
    "9241a81d1261fe3e3a81b81e631893b9e1b4f82112379338e09eedbb5f6e1668"
)


OPENING_COUNT_WRITE_ERROR = (
    "0052 opening count write is not current or authorized"
)
COUNT_WRITE_FUNCTION = "rsc_require_opening_count_write_current_0052"
COUNT_WRITE_SIGNATURE = f"public.{COUNT_WRITE_FUNCTION}()"
GRAPH_CLOSURE_FUNCTION = "rsc_require_opening_live_graph_0052"
GRAPH_CLOSURE_SIGNATURE = f"public.{GRAPH_CLOSURE_FUNCTION}()"
HEAD_ONLY_FUNCTION_SIGNATURES = (
    *NEW_HELPER_SIGNATURES,
    INSERT_GUARD_SIGNATURE,
    COUNT_WRITE_SIGNATURE,
    GRAPH_CLOSURE_SIGNATURE,
)
ALL_FUNCTION_SIGNATURES = (
    *PERSISTENT_FUNCTION_SIGNATURES,
    *HEAD_ONLY_FUNCTION_SIGNATURES,
)
COUNT_WRITE_TRIGGER_CATALOG = (
    (
        "stocktake_count_lines",
        "trg_stocktake_count_lines_current_0052",
    ),
    (
        "stocktake_count_serials",
        "trg_stocktake_count_serials_current_0052",
    ),
    (
        "stocktake_count_observations",
        "trg_stocktake_count_observations_current_0052",
    ),
    (
        "stocktake_scope_count_completions",
        "trg_stocktake_scope_count_completions_current_0052",
    ),
)

# Deferred graph ownership closes the gap between a valid individual INSERT
# and the immutable parent graph it must form before commit.
GRAPH_CLOSURE_TRIGGER_CATALOG = (
    ("stocktake_count_lines", "trg_stocktake_count_lines_graph_0052", "INSERT"),
    ("stocktake_count_serials", "trg_stocktake_count_serials_graph_0052", "INSERT"),
    (
        "stocktake_count_observations",
        "trg_stocktake_count_observations_graph_0052",
        "INSERT",
    ),
    (
        "stocktake_scope_count_completions",
        "trg_stocktake_scope_count_completions_graph_0052",
        "INSERT",
    ),
    (
        "stocktake_round_submissions",
        "trg_stocktake_round_submissions_graph_0052",
        "INSERT",
    ),
    ("stocktake_rounds", "trg_stocktake_rounds_graph_0052", "INSERT OR UPDATE"),
    ("stocktake_reviews", "trg_stocktake_reviews_graph_0052", "INSERT"),
    ("stocktake_review_items", "trg_stocktake_review_items_graph_0052", "INSERT"),
    ("stocktake_differences", "trg_stocktake_differences_graph_0052", "INSERT"),
    (
        "stocktake_difference_set_completions",
        "trg_stocktake_difference_set_completions_graph_0052",
        "INSERT",
    ),
    (
        "stocktake_observation_dispositions",
        "trg_stocktake_observation_dispositions_graph_0052",
        "INSERT",
    ),
    (
        "stocktake_postings",
        "trg_stocktake_postings_graph_0052",
        "INSERT",
    ),
    (
        "state_transition_events",
        "trg_state_transition_events_opening_graph_0052",
        "INSERT",
    ),
    (
        "outbox_events",
        "trg_outbox_events_opening_graph_0052",
        "INSERT",
    ),
    (
        "audit_events",
        "trg_audit_events_opening_graph_0052",
        "INSERT",
    ),
)


def _opening_count_write_guard_body() -> str:
    current_line_master = _current_scope_master_proof_sql(
        "count_task.id",
        "count_task.region_org_id",
        alias_suffix="count_line_write",
    )
    current_initial_line_actor = _start_user_entitlement_sql(
        user_id_sql="NEW.counted_by_user_id",
        task_alias="count_task",
        scope_alias="count_scope",
        location_alias="count_location",
        occurred_at_sql="NEW.counted_at",
        action="count",
        alias_suffix="count_line_actor",
    )
    current_recount_line_actor = _start_user_entitlement_sql(
        user_id_sql="NEW.counted_by_user_id",
        task_alias="count_task",
        scope_alias="count_scope",
        location_alias="count_location",
        occurred_at_sql="NEW.counted_at",
        action="count",
        alias_suffix="count_line_recount_actor",
        assignment_id_sql=(
            "count_assignment.assignee_role_assignment_id"
        ),
    )
    current_serial_master = _current_scope_master_proof_sql(
        "serial_task.id",
        "serial_task.region_org_id",
        alias_suffix="count_serial_write",
    )
    current_initial_serial_actor = _start_user_entitlement_sql(
        user_id_sql="serial_line.counted_by_user_id",
        task_alias="serial_task",
        scope_alias="serial_scope",
        location_alias="serial_location",
        occurred_at_sql="serial_line.counted_at",
        action="count",
        alias_suffix="count_serial_actor",
    )
    current_recount_serial_actor = _start_user_entitlement_sql(
        user_id_sql="serial_line.counted_by_user_id",
        task_alias="serial_task",
        scope_alias="serial_scope",
        location_alias="serial_location",
        occurred_at_sql="serial_line.counted_at",
        action="count",
        alias_suffix="count_serial_recount_actor",
        assignment_id_sql=(
            "serial_assignment.assignee_role_assignment_id"
        ),
    )
    current_actor = _current_authorization_sql(
        user_id_sql="NEW.completed_by_user_id",
        person_id_sql="NEW.completed_by_person_id",
        assignment_id_sql="NEW.completed_role_assignment_id",
        authorization_version_sql="NEW.authorization_version",
        occurred_at_sql="NEW.completed_at",
        role_code_sql="NEW.role_code",
        scope_type_sql="NEW.scope_type",
        scope_id_sql="NEW.scope_id_snapshot",
        permission_resource="stocktake",
        permission_action="count",
        allow_scheduled=False,
        alias_suffix="count_write",
    )
    current_master = _current_scope_master_proof_sql(
        "completion_task.id",
        "completion_task.region_org_id",
        alias_suffix="count_write",
    )
    count_region = _current_entitlement_target_sql(
        user_id_sql="NEW.completed_by_user_id",
        assignment_id_sql="NEW.completed_role_assignment_id",
        permission_resource="stocktake",
        permission_action="count",
        target_scope_type_sql="'organization'",
        target_scope_id_sql="completion_task.region_org_id::text",
        alias_suffix="count_write_region",
    )
    count_owner = _current_entitlement_target_sql(
        user_id_sql="NEW.completed_by_user_id",
        assignment_id_sql="NEW.completed_role_assignment_id",
        permission_resource="stocktake",
        permission_action="count",
        target_scope_type_sql="'organization'",
        target_scope_id_sql="completion_scope.owner_org_id::text",
        alias_suffix="count_write_owner",
    )
    count_location_owner = _current_entitlement_target_sql(
        user_id_sql="NEW.completed_by_user_id",
        assignment_id_sql="NEW.completed_role_assignment_id",
        permission_resource="stocktake",
        permission_action="count",
        target_scope_type_sql="'organization'",
        target_scope_id_sql="completion_location.owner_org_id::text",
        alias_suffix="count_write_location_owner",
    )
    return f"""
DECLARE
    opening_task boolean;
BEGIN
    IF TG_TABLE_NAME = 'stocktake_count_lines' THEN
        SELECT task.task_type = 'opening'
          INTO opening_task
          FROM public.stocktake_tasks AS task
         WHERE task.id = NEW.task_id;
        IF NOT COALESCE(opening_task, FALSE) THEN
            RETURN NEW;
        END IF;
        IF NEW.counted_at < pg_catalog.transaction_timestamp()
           OR NEW.counted_at > pg_catalog.clock_timestamp()
           OR NEW.created_at IS DISTINCT FROM NEW.counted_at
           OR NEW.updated_at IS DISTINCT FROM NEW.counted_at
           OR NOT EXISTS (
               SELECT 1
                 FROM public.stocktake_tasks AS count_task
                 JOIN public.stocktake_rounds AS count_round
                   ON count_round.id = NEW.round_id
                  AND count_round.task_id = count_task.id
                 JOIN public.stocktake_scopes AS count_scope
                   ON count_scope.id = NEW.scope_id
                  AND count_scope.task_id = count_task.id
                 JOIN public.stock_locations AS count_location
                   ON count_location.id = count_scope.location_id
                WHERE count_task.id = NEW.task_id
                  AND count_task.task_type = 'opening'
                  AND count_task.status = 'counting'
                  AND count_task.current_round_no = count_round.round_no
                  AND count_round.status = 'counting'
                  AND ({current_line_master})
                  AND (
                      (
                          count_round.round_type = 'initial'
                          AND count_round.round_no = 1
                          AND count_round.recount_case_id IS NULL
                          AND count_scope.assignee_user_id =
                              NEW.counted_by_user_id
                          AND ({current_initial_line_actor})
                      )
                      OR (
                          count_round.round_type = 'recount'
                          AND count_round.round_no > 1
                          AND (
                              SELECT pg_catalog.count(*)
                                FROM public.stocktake_recount_scope_assignments
                                     AS count_assignment
                               WHERE count_assignment.recount_case_id =
                                     count_round.recount_case_id
                                 AND count_assignment.task_id = count_task.id
                                 AND count_assignment.scope_id = count_scope.id
                                 AND count_assignment.assignee_user_id =
                                     NEW.counted_by_user_id
                                 AND count_assignment.assigned_at <=
                                     NEW.counted_at
                                 AND ({current_recount_line_actor})
                          ) = 1
                      )
                  )
           ) THEN
            RAISE EXCEPTION '{OPENING_COUNT_WRITE_ERROR}'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_NAME = 'stocktake_count_serials' THEN
        SELECT task.task_type = 'opening'
          INTO opening_task
          FROM public.stocktake_count_lines AS parent_line
          JOIN public.stocktake_tasks AS task
            ON task.id = parent_line.task_id
         WHERE parent_line.id = NEW.count_line_id
           AND parent_line.round_id = NEW.round_id;
        IF NOT COALESCE(opening_task, FALSE) THEN
            RETURN NEW;
        END IF;
        IF NEW.created_at < pg_catalog.transaction_timestamp()
           OR NEW.created_at > pg_catalog.clock_timestamp()
           OR NOT EXISTS (
               SELECT 1
                 FROM public.stocktake_count_lines AS serial_line
                 JOIN public.stocktake_tasks AS serial_task
                   ON serial_task.id = serial_line.task_id
                 JOIN public.stocktake_rounds AS serial_round
                   ON serial_round.id = serial_line.round_id
                  AND serial_round.task_id = serial_task.id
                 JOIN public.stocktake_scopes AS serial_scope
                   ON serial_scope.id = serial_line.scope_id
                  AND serial_scope.task_id = serial_task.id
                 JOIN public.stock_locations AS serial_location
                   ON serial_location.id = serial_scope.location_id
                WHERE serial_line.id = NEW.count_line_id
                  AND serial_line.round_id = NEW.round_id
                  AND NEW.created_at = serial_line.counted_at
                  AND serial_task.task_type = 'opening'
                  AND serial_task.status = 'counting'
                  AND serial_task.current_round_no = serial_round.round_no
                  AND serial_round.status = 'counting'
                  AND ({current_serial_master})
                  AND NOT EXISTS (
                      SELECT 1
                        FROM public.stocktake_scope_count_completions
                             AS serial_completion
                       WHERE serial_completion.task_id = serial_task.id
                         AND serial_completion.round_id = serial_round.id
                         AND serial_completion.scope_id = serial_scope.id
                  )
                  AND (
                      (serial_round.round_type = 'initial'
                       AND serial_round.round_no = 1
                       AND serial_round.recount_case_id IS NULL
                       AND serial_scope.assignee_user_id =
                           serial_line.counted_by_user_id
                       AND ({current_initial_serial_actor}))
                      OR
                      (serial_round.round_type = 'recount'
                       AND serial_round.round_no > 1
                       AND (SELECT pg_catalog.count(*)
                              FROM public.stocktake_recount_scope_assignments
                                   AS serial_assignment
                             WHERE serial_assignment.recount_case_id =
                                   serial_round.recount_case_id
                               AND serial_assignment.task_id = serial_task.id
                               AND serial_assignment.scope_id = serial_scope.id
                               AND serial_assignment.assignee_user_id =
                                   serial_line.counted_by_user_id
                               AND serial_assignment.assigned_at <=
                                   serial_line.counted_at
                               AND ({current_recount_serial_actor})) = 1)
                  )
           ) THEN
            RAISE EXCEPTION '{OPENING_COUNT_WRITE_ERROR}'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_NAME = 'stocktake_count_observations' THEN
        SELECT task.task_type = 'opening'
          INTO opening_task
          FROM public.stocktake_tasks AS task
         WHERE task.id = NEW.task_id;
        IF NOT COALESCE(opening_task, FALSE) THEN
            RETURN NEW;
        END IF;
        IF NEW.counted_at < pg_catalog.transaction_timestamp()
           OR NEW.counted_at > pg_catalog.clock_timestamp()
           OR NEW.created_at IS DISTINCT FROM NEW.counted_at
           OR NOT EXISTS (
               SELECT 1
                 FROM public.stocktake_tasks AS count_task
                 JOIN public.stocktake_rounds AS count_round
                   ON count_round.id = NEW.round_id
                  AND count_round.task_id = count_task.id
                 JOIN public.stocktake_scopes AS count_scope
                   ON count_scope.id = NEW.scope_id
                  AND count_scope.task_id = count_task.id
                 JOIN public.stock_locations AS count_location
                   ON count_location.id = count_scope.location_id
                WHERE count_task.id = NEW.task_id
                  AND count_task.task_type = 'opening'
                  AND count_task.status = 'counting'
                  AND count_task.current_round_no = count_round.round_no
                  AND count_round.status = 'counting'
                  AND ({current_line_master})
                  AND NEW.owner_org_id = count_scope.owner_org_id
                  AND NEW.location_id = count_scope.location_id
                  AND NEW.custodian_person_id_snapshot IS NOT DISTINCT FROM
                      count_scope.custodian_person_id_snapshot
                  AND (
                      (
                          count_round.round_type = 'initial'
                          AND count_round.round_no = 1
                          AND count_round.recount_case_id IS NULL
                          AND count_scope.assignee_user_id =
                              NEW.counted_by_user_id
                          AND ({current_initial_line_actor})
                      )
                      OR (
                          count_round.round_type = 'recount'
                          AND count_round.round_no > 1
                          AND (
                              SELECT pg_catalog.count(*)
                                FROM public.stocktake_recount_scope_assignments
                                     AS count_assignment
                               WHERE count_assignment.recount_case_id =
                                     count_round.recount_case_id
                                 AND count_assignment.task_id = count_task.id
                                 AND count_assignment.scope_id = count_scope.id
                                 AND count_assignment.assignee_user_id =
                                     NEW.counted_by_user_id
                                 AND count_assignment.assigned_at <=
                                     NEW.counted_at
                                 AND ({current_recount_line_actor})
                          ) = 1
                      )
                  )
           ) THEN
            RAISE EXCEPTION '{OPENING_COUNT_WRITE_ERROR}'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_NAME = 'stocktake_scope_count_completions' THEN
        SELECT task.task_type = 'opening'
          INTO opening_task
          FROM public.stocktake_tasks AS task
         WHERE task.id = NEW.task_id;
        IF NOT COALESCE(opening_task, FALSE) THEN
            RETURN NEW;
        END IF;
        IF NEW.completed_at < pg_catalog.transaction_timestamp()
           OR NEW.completed_at > pg_catalog.clock_timestamp()
           OR NEW.created_at IS DISTINCT FROM NEW.completed_at
           OR NOT EXISTS (
               SELECT 1
                 FROM public.stocktake_tasks AS completion_task
                 JOIN public.stocktake_rounds AS completion_round
                   ON completion_round.id = NEW.round_id
                  AND completion_round.task_id = completion_task.id
                 JOIN public.stocktake_scopes AS completion_scope
                   ON completion_scope.id = NEW.scope_id
                  AND completion_scope.task_id = completion_task.id
                 JOIN public.stock_locations AS completion_location
                   ON completion_location.id = completion_scope.location_id
                WHERE completion_task.id = NEW.task_id
                  AND completion_task.task_type = 'opening'
                  AND completion_task.status = 'counting'
                  AND completion_task.current_round_no =
                      completion_round.round_no
                  AND completion_round.status = 'counting'
                  AND ({current_master})
                  AND ({current_actor})
                  AND (
                      (
                          completion_round.round_type = 'initial'
                          AND completion_round.round_no = 1
                          AND completion_round.recount_case_id IS NULL
                          AND completion_scope.assignee_user_id =
                              NEW.completed_by_user_id
                          AND (
                              (
                                  completion_location.location_type =
                                      'personal'
                                  AND NEW.role_code = 'technician'
                                  AND NEW.scope_type = 'person'
                                  AND completion_scope.custodian_person_id_snapshot
                                      IS NOT NULL
                                  AND NEW.completed_by_person_id =
                                      completion_scope.custodian_person_id_snapshot
                                  AND NEW.scope_id_snapshot =
                                      completion_scope.custodian_person_id_snapshot::text
                              )
                              OR (
                                  completion_location.location_type = 'region'
                                  AND NEW.role_code IN (
                                      'admin', 'provincial_manager'
                                  )
                                  AND ({count_region})
                                  AND ({count_owner})
                                  AND ({count_location_owner})
                              )
                          )
                      )
                      OR (
                          completion_round.round_type = 'recount'
                          AND completion_round.round_no > 1
                          AND (
                              SELECT pg_catalog.count(*)
                                FROM public.stocktake_recount_scope_assignments
                                     AS completion_assignment
                               WHERE completion_assignment.recount_case_id =
                                     completion_round.recount_case_id
                                 AND completion_assignment.task_id =
                                     completion_task.id
                                 AND completion_assignment.scope_id =
                                     completion_scope.id
                                 AND completion_assignment.assignee_user_id =
                                     NEW.completed_by_user_id
                                 AND completion_assignment.assignee_person_id =
                                     NEW.completed_by_person_id
                                 AND completion_assignment.assignee_role_assignment_id =
                                     NEW.completed_role_assignment_id
                                 AND NEW.authorization_version >=
                                     completion_assignment.authorization_version
                                 AND completion_assignment.role_code =
                                     NEW.role_code
                                 AND completion_assignment.scope_type =
                                     NEW.scope_type
                                 AND completion_assignment.scope_id_snapshot =
                                     NEW.scope_id_snapshot
                          ) = 1
                          AND (
                              NEW.role_code = 'technician'
                              OR (
                                  ({count_region})
                                  AND ({count_owner})
                                  AND ({count_location_owner})
                              )
                          )
                      )
                  )
                  AND NOT EXISTS (
                      SELECT 1
                        FROM public.stocktake_count_lines AS completion_line
                       WHERE completion_line.task_id = completion_task.id
                         AND completion_line.round_id = completion_round.id
                         AND completion_line.scope_id = completion_scope.id
                         AND (
                             completion_line.counted_by_user_id <>
                                 NEW.completed_by_user_id
                             OR completion_line.counted_at <>
                                 NEW.completed_at
                             OR completion_line.created_at <>
                                 NEW.completed_at
                             OR completion_line.updated_at <>
                                 NEW.completed_at
                         )
                  )
                  AND NOT EXISTS (
                      SELECT 1
                        FROM public.stocktake_count_observations
                             AS completion_observation
                       WHERE completion_observation.task_id =
                             completion_task.id
                         AND completion_observation.round_id =
                             completion_round.id
                         AND completion_observation.scope_id =
                             completion_scope.id
                         AND (
                             completion_observation.counted_by_user_id <>
                                 NEW.completed_by_user_id
                             OR completion_observation.counted_at <>
                                 NEW.completed_at
                             OR completion_observation.created_at <>
                                 NEW.completed_at
                         )
                  )
           ) THEN
            RAISE EXCEPTION '{OPENING_COUNT_WRITE_ERROR}'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION '{OPENING_COUNT_WRITE_ERROR}' USING ERRCODE = '23514';
END;
""".strip()


COUNT_WRITE_BODY = _opening_count_write_guard_body()
COUNT_WRITE_BODY_SHA256 = (
    "762841d77fc3ff57bb61b42c473aa5a5c46cc2aa0125e8d6abe0642882070b07"
)


GRAPH_CLOSURE_ERROR = "0052 opening immutable child graph is incomplete"


def _opening_graph_closure_body() -> str:
    return f"""
DECLARE
    opening_task boolean;
    resolved_disposition_id uuid;
    resolved_recount_case_id uuid;
    resolved_review_id uuid;
    resolved_review_stage text;
    resolved_round_id uuid;
    resolved_scope_id uuid;
    resolved_task_id uuid;
    resolved_transaction_id uuid;
    resolved_transaction_opening boolean;
BEGIN
    IF TG_TABLE_NAME = 'stocktake_count_lines' THEN
        resolved_task_id := NEW.task_id;
        resolved_round_id := NEW.round_id;
        resolved_scope_id := NEW.scope_id;
    ELSIF TG_TABLE_NAME = 'stocktake_count_serials' THEN
        SELECT count_line.task_id, count_line.round_id, count_line.scope_id
          INTO resolved_task_id, resolved_round_id, resolved_scope_id
          FROM public.stocktake_count_lines AS count_line
         WHERE count_line.id = NEW.count_line_id
           AND count_line.round_id = NEW.round_id;
    ELSIF TG_TABLE_NAME = 'stocktake_count_observations' THEN
        resolved_task_id := NEW.task_id;
        resolved_round_id := NEW.round_id;
        resolved_scope_id := NEW.scope_id;
    ELSIF TG_TABLE_NAME = 'stocktake_scope_count_completions' THEN
        resolved_task_id := NEW.task_id;
        resolved_round_id := NEW.round_id;
        resolved_scope_id := NEW.scope_id;
    ELSIF TG_TABLE_NAME = 'stocktake_round_submissions' THEN
        resolved_task_id := NEW.task_id;
        resolved_round_id := NEW.round_id;
    ELSIF TG_TABLE_NAME = 'stocktake_rounds' THEN
        resolved_task_id := NEW.task_id;
        resolved_round_id := NEW.id;
    ELSIF TG_TABLE_NAME = 'stocktake_reviews' THEN
        resolved_task_id := NEW.task_id;
        resolved_round_id := NEW.round_id;
        resolved_review_id := NEW.id;
    ELSIF TG_TABLE_NAME = 'stocktake_review_items' THEN
        resolved_task_id := NEW.task_id;
        resolved_round_id := NEW.round_id;
        resolved_review_id := NEW.review_id;
    ELSIF TG_TABLE_NAME = 'stocktake_differences' THEN
        resolved_task_id := NEW.task_id;
        resolved_round_id := NEW.round_id;
    ELSIF TG_TABLE_NAME = 'stocktake_difference_set_completions' THEN
        resolved_task_id := NEW.task_id;
        resolved_round_id := NEW.round_id;
    ELSIF TG_TABLE_NAME = 'stocktake_observation_dispositions' THEN
        resolved_task_id := NEW.task_id;
        resolved_round_id := NEW.round_id;
        resolved_scope_id := NEW.scope_id;
        resolved_disposition_id := NEW.id;
    ELSIF TG_TABLE_NAME = 'stocktake_postings' THEN
        SELECT task.task_type = 'opening'
          INTO opening_task
          FROM public.stocktake_tasks AS task
         WHERE task.id = NEW.task_id;
        IF NOT COALESCE(opening_task, FALSE) THEN
            RETURN NEW;
        END IF;
        IF NEW.posting_kind <> 'opening'
           OR NOT public.{TERMINAL_GRAPH_FUNCTION}(NEW.task_id, FALSE) THEN
            RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_NAME = 'state_transition_events' THEN
        IF NEW.reason IN (
               'reconciliation.opening.create',
               'reconciliation.opening.explain',
               'reconciliation.opening.approve'
           )
           AND pg_catalog.left(NEW.idempotency_key, 23) =
               'opening-reconciliation-' THEN
            RETURN NEW;
        END IF;
        IF NEW.aggregate_type = 'stocktake_task' THEN
            SELECT task.id
              INTO resolved_task_id
              FROM public.stocktake_tasks AS task
             WHERE task.id::text = NEW.aggregate_id
               AND task.task_type = 'opening';
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.reason IN (
                    'opening_stocktake_created',
                    'opening_stocktake_issued',
                    'opening_stocktake_frozen',
                    'opening_stocktake_initial_round_started'
                ) THEN
                    IF NOT public.{START_GRAPH_FUNCTION}(
                        resolved_task_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.reason IN (
                    'opening_initial_round_submitted',
                    'opening_recount_round_submitted'
                ) THEN
                    SELECT round_row.id
                      INTO resolved_round_id
                      FROM public.stocktake_rounds AS round_row
                     WHERE round_row.task_id = resolved_task_id
                       AND round_row.id::text =
                           NEW.metadata_jsonb ->> 'round_id';
                    IF resolved_round_id IS NULL OR NOT public.{ROUND_SUBMISSION_FUNCTION}(
                        resolved_task_id,
                        resolved_round_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.reason IN (
                    'opening_region_review_approve',
                    'opening_region_review_recount',
                    'opening_region_review_reject',
                    'opening_headquarters_review_approve',
                    'opening_headquarters_review_reject'
                ) THEN
                    SELECT review_row.id
                      INTO resolved_review_id
                     FROM public.stocktake_reviews AS review_row
                     WHERE review_row.task_id = resolved_task_id
                       AND review_row.id::text =
                           NEW.metadata_jsonb ->> 'review_id'
                       AND NEW.reason =
                           'opening_' || review_row.review_stage ||
                           '_review_' || review_row.decision;
                    IF resolved_review_id IS NULL OR NOT public.{REVIEW_GRAPH_FUNCTION}(
                        resolved_review_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.reason = 'opening_recount_opened' THEN
                    SELECT recount_case.id
                      INTO resolved_recount_case_id
                      FROM public.stocktake_recount_cases AS recount_case
                     WHERE recount_case.task_id = resolved_task_id
                       AND recount_case.id::text =
                           NEW.metadata_jsonb ->> 'recount_case_id';
                    IF resolved_recount_case_id IS NULL OR NOT public.{RECOUNT_GRAPH_FUNCTION}(
                        resolved_recount_case_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.reason IN (
                    'opening_stocktake_posted',
                    'opening_stocktake_closed'
                ) THEN
                    IF NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_tasks AS terminal_task
                         WHERE terminal_task.id = resolved_task_id
                           AND terminal_task.status = CASE NEW.reason
                               WHEN 'opening_stocktake_posted' THEN 'posted'
                               WHEN 'opening_stocktake_closed' THEN 'closed'
                               ELSE NULL
                           END
                    ) OR NOT public.{TERMINAL_GRAPH_FUNCTION}(
                        resolved_task_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSE
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'stocktake_round' THEN
            SELECT task.id, round_row.id
              INTO resolved_task_id, resolved_round_id
              FROM public.stocktake_rounds AS round_row
              JOIN public.stocktake_tasks AS task
                ON task.id = round_row.task_id
               AND task.task_type = 'opening'
             WHERE round_row.id::text = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.reason NOT IN (
                    'opening_initial_round_submitted',
                    'opening_recount_round_submitted'
                ) OR NOT public.{ROUND_SUBMISSION_FUNCTION}(
                    resolved_task_id,
                    resolved_round_id,
                    FALSE
                ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'stocktake_scope' THEN
            SELECT task.id, scope_row.id
              INTO resolved_task_id, resolved_scope_id
              FROM public.stocktake_scopes AS scope_row
              JOIN public.stocktake_tasks AS task
                ON task.id = scope_row.task_id
               AND task.task_type = 'opening'
             WHERE scope_row.id::text = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.reason NOT IN (
                    'opening_initial_scope_count_completed',
                    'opening_recount_scope_count_completed'
                ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                SELECT completion.round_id
                  INTO resolved_round_id
                  FROM public.stocktake_scope_count_completions AS completion
                 WHERE completion.task_id = resolved_task_id
                   AND completion.scope_id = resolved_scope_id
                   AND completion.round_id::text =
                       NEW.metadata_jsonb ->> 'round_id';
                IF resolved_round_id IS NULL OR NOT public.{SCOPE_COMPLETION_FUNCTION}(
                    resolved_task_id,
                    resolved_round_id,
                    resolved_scope_id,
                    FALSE
                ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type IN (
            'stocktake_review',
            'stocktake_recount_case',
            'stocktake_observation_disposition',
            'stocktake_posting'
        ) THEN
            SELECT forbidden_owner.task_id
              INTO resolved_task_id
              FROM (
                  SELECT 'stocktake_review'::text AS aggregate_type,
                         review_row.id::text AS aggregate_id,
                         review_row.task_id
                    FROM public.stocktake_reviews AS review_row
                  UNION ALL
                  SELECT 'stocktake_recount_case',
                         recount_case.id::text,
                         recount_case.task_id
                    FROM public.stocktake_recount_cases AS recount_case
                  UNION ALL
                  SELECT 'stocktake_observation_disposition',
                         disposition.id::text,
                         disposition.task_id
                    FROM public.stocktake_observation_dispositions
                         AS disposition
                  UNION ALL
                  SELECT 'stocktake_posting',
                         posting.id::text,
                         posting.task_id
                    FROM public.stocktake_postings AS posting
              ) AS forbidden_owner
              JOIN public.stocktake_tasks AS forbidden_task
                ON forbidden_task.id = forbidden_owner.task_id
               AND forbidden_task.task_type = 'opening'
             WHERE forbidden_owner.aggregate_type = NEW.aggregate_type
               AND forbidden_owner.aggregate_id = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.aggregate_type = 'inventory_transaction' THEN
            SELECT inventory_transaction.id,
                   inventory_transaction.movement_type = 'opening',
                   opening_task_row.id
              INTO resolved_transaction_id,
                   resolved_transaction_opening,
                   resolved_task_id
              FROM public.inventory_transactions AS inventory_transaction
              LEFT JOIN public.stocktake_postings AS opening_posting
                ON opening_posting.inventory_transaction_id =
                   inventory_transaction.id
               AND opening_posting.posting_kind = 'opening'
              LEFT JOIN public.stocktake_tasks AS opening_task_row
                ON opening_task_row.id = opening_posting.task_id
               AND opening_task_row.task_type = 'opening'
            WHERE inventory_transaction.id::text = NEW.aggregate_id;
            IF resolved_transaction_id IS NOT NULL THEN
                IF resolved_transaction_opening THEN
                    IF resolved_task_id IS NULL
                       OR NOT EXISTS (
                           SELECT 1
                             FROM public.stocktake_tasks AS terminal_task
                            WHERE terminal_task.id = resolved_task_id
                              AND terminal_task.status = 'posted'
                       )
                       OR NEW.idempotency_key <> {_inventory_event_key_sql(
                           'state', 'resolved_transaction_id', 'posted'
                       )}
                       OR NOT public.{TERMINAL_GRAPH_FUNCTION}(
                        resolved_task_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                RETURN NEW;
            END IF;
        END IF;
        IF pg_catalog.left(NEW.reason, 8) = 'opening_'
           OR pg_catalog.left(NEW.idempotency_key, 8) = 'opening-'
           OR pg_catalog.left(NEW.idempotency_key, 16) =
              'inventory-state-' THEN
            RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_NAME = 'outbox_events' THEN
        IF NEW.event_type IN (
               'reconciliation.opening.create',
               'reconciliation.opening.explain',
               'reconciliation.opening.approve'
           )
           AND pg_catalog.left(NEW.idempotency_key, 23) =
               'opening-reconciliation-' THEN
            RETURN NEW;
        END IF;
        IF NEW.aggregate_type = 'stocktake_task' THEN
            SELECT task.id
              INTO resolved_task_id
              FROM public.stocktake_tasks AS task
             WHERE task.id::text = NEW.aggregate_id
               AND task.task_type = 'opening';
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.event_type = 'stocktake.opening.started' THEN
                    IF NOT public.{START_GRAPH_FUNCTION}(
                        resolved_task_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.event_type IN (
                    'stocktake.opening.region_reviewed',
                    'stocktake.opening.headquarters_reviewed'
                ) THEN
                    SELECT review_row.id
                      INTO resolved_review_id
                     FROM public.stocktake_reviews AS review_row
                     WHERE review_row.task_id = resolved_task_id
                       AND review_row.id::text =
                           NEW.payload_jsonb ->> 'review_id'
                       AND NEW.event_type =
                           'stocktake.opening.' ||
                           review_row.review_stage || '_reviewed';
                    IF resolved_review_id IS NULL OR NOT public.{REVIEW_GRAPH_FUNCTION}(
                        resolved_review_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.event_type =
                      'stocktake.opening.recount_opened' THEN
                    SELECT recount_case.id
                      INTO resolved_recount_case_id
                      FROM public.stocktake_recount_cases AS recount_case
                     WHERE recount_case.task_id = resolved_task_id
                       AND recount_case.id::text =
                           NEW.payload_jsonb ->> 'recount_case_id';
                    IF resolved_recount_case_id IS NULL OR NOT public.{RECOUNT_GRAPH_FUNCTION}(
                        resolved_recount_case_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.event_type IN (
                    'stocktake.opening.posted',
                    'stocktake.opening.closed'
                ) THEN
                    IF NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_tasks AS terminal_task
                         WHERE terminal_task.id = resolved_task_id
                           AND terminal_task.status = CASE NEW.event_type
                               WHEN 'stocktake.opening.posted' THEN 'posted'
                               WHEN 'stocktake.opening.closed' THEN 'closed'
                               ELSE NULL
                           END
                    ) OR NOT public.{TERMINAL_GRAPH_FUNCTION}(
                        resolved_task_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSE
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'stocktake_round' THEN
            SELECT task.id, round_row.id
              INTO resolved_task_id, resolved_round_id
              FROM public.stocktake_rounds AS round_row
              JOIN public.stocktake_tasks AS task
                ON task.id = round_row.task_id
               AND task.task_type = 'opening'
             WHERE round_row.id::text = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.event_type <>
                   'stocktake.opening.round_submitted'
                   OR NOT public.{ROUND_SUBMISSION_FUNCTION}(
                       resolved_task_id,
                       resolved_round_id,
                       FALSE
                   ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'stocktake_scope' THEN
            SELECT task.id, scope_row.id
              INTO resolved_task_id, resolved_scope_id
              FROM public.stocktake_scopes AS scope_row
              JOIN public.stocktake_tasks AS task
                ON task.id = scope_row.task_id
               AND task.task_type = 'opening'
             WHERE scope_row.id::text = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.event_type <>
                   'stocktake.opening.scope_count_completed' THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                SELECT completion.round_id
                  INTO resolved_round_id
                  FROM public.stocktake_scope_count_completions AS completion
                 WHERE completion.task_id = resolved_task_id
                   AND completion.scope_id = resolved_scope_id
                   AND completion.round_id::text =
                       NEW.payload_jsonb ->> 'round_id';
                IF resolved_round_id IS NULL OR NOT public.{SCOPE_COMPLETION_FUNCTION}(
                    resolved_task_id,
                    resolved_round_id,
                    resolved_scope_id,
                    FALSE
                ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type IN (
            'stocktake_review',
            'stocktake_recount_case',
            'stocktake_observation_disposition',
            'stocktake_posting'
        ) THEN
            SELECT forbidden_owner.task_id
              INTO resolved_task_id
              FROM (
                  SELECT 'stocktake_review'::text AS aggregate_type,
                         review_row.id::text AS aggregate_id,
                         review_row.task_id
                    FROM public.stocktake_reviews AS review_row
                  UNION ALL
                  SELECT 'stocktake_recount_case',
                         recount_case.id::text,
                         recount_case.task_id
                    FROM public.stocktake_recount_cases AS recount_case
                  UNION ALL
                  SELECT 'stocktake_observation_disposition',
                         disposition.id::text,
                         disposition.task_id
                    FROM public.stocktake_observation_dispositions
                         AS disposition
                  UNION ALL
                  SELECT 'stocktake_posting',
                         posting.id::text,
                         posting.task_id
                    FROM public.stocktake_postings AS posting
              ) AS forbidden_owner
              JOIN public.stocktake_tasks AS forbidden_task
                ON forbidden_task.id = forbidden_owner.task_id
               AND forbidden_task.task_type = 'opening'
             WHERE forbidden_owner.aggregate_type = NEW.aggregate_type
               AND forbidden_owner.aggregate_id = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.aggregate_type = 'inventory_transaction' THEN
            SELECT inventory_transaction.id,
                   inventory_transaction.movement_type = 'opening',
                   opening_task_row.id
              INTO resolved_transaction_id,
                   resolved_transaction_opening,
                   resolved_task_id
              FROM public.inventory_transactions AS inventory_transaction
              LEFT JOIN public.stocktake_postings AS opening_posting
                ON opening_posting.inventory_transaction_id =
                   inventory_transaction.id
               AND opening_posting.posting_kind = 'opening'
              LEFT JOIN public.stocktake_tasks AS opening_task_row
                ON opening_task_row.id = opening_posting.task_id
               AND opening_task_row.task_type = 'opening'
            WHERE inventory_transaction.id::text = NEW.aggregate_id;
            IF resolved_transaction_id IS NOT NULL THEN
                IF resolved_transaction_opening THEN
                    IF resolved_task_id IS NULL
                       OR NOT EXISTS (
                           SELECT 1
                             FROM public.stocktake_tasks AS terminal_task
                            WHERE terminal_task.id = resolved_task_id
                              AND terminal_task.status = 'posted'
                       )
                       OR NEW.idempotency_key <> {_inventory_event_key_sql(
                           'outbox', 'resolved_transaction_id', 'posted'
                       )}
                       OR NOT public.{TERMINAL_GRAPH_FUNCTION}(
                        resolved_task_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                RETURN NEW;
            END IF;
        END IF;
        IF pg_catalog.left(NEW.event_type, 18) = 'stocktake.opening.'
           OR pg_catalog.left(NEW.idempotency_key, 8) = 'opening-'
           OR pg_catalog.left(NEW.idempotency_key, 17) =
              'inventory-outbox-' THEN
            RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_NAME = 'audit_events' THEN
        IF NEW.stream_key = 'inventory'
           AND NOT ({_audit_event_chain_binding_sql(
               'NEW',
               require_head=False,
           )}) THEN
            RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.aggregate_type = 'stocktake_task' THEN
            SELECT task.id
              INTO resolved_task_id
              FROM public.stocktake_tasks AS task
             WHERE task.id::text = NEW.aggregate_id
               AND task.task_type = 'opening';
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.action = 'stocktake.opening.started' THEN
                    IF NOT public.{START_GRAPH_FUNCTION}(
                        resolved_task_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.action = 'stocktake.opening.closed' THEN
                    IF NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_tasks AS terminal_task
                         WHERE terminal_task.id = resolved_task_id
                           AND terminal_task.status = 'closed'
                    ) OR NOT public.{TERMINAL_GRAPH_FUNCTION}(
                        resolved_task_id,
                        FALSE
                    ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                ELSE
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'stocktake_scope' THEN
            SELECT task.id, scope_row.id
              INTO resolved_task_id, resolved_scope_id
              FROM public.stocktake_scopes AS scope_row
              JOIN public.stocktake_tasks AS task
                ON task.id = scope_row.task_id
               AND task.task_type = 'opening'
             WHERE scope_row.id::text = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.action <>
                   'stocktake.opening.scope_count_completed' THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                SELECT completion.round_id
                  INTO resolved_round_id
                  FROM public.stocktake_scope_count_completions AS completion
                 WHERE completion.task_id = resolved_task_id
                   AND completion.scope_id = resolved_scope_id
                   AND completion.round_id::text =
                       NEW.after_jsonb ->> 'round_id';
                IF resolved_round_id IS NULL OR NOT public.{SCOPE_COMPLETION_FUNCTION}(
                    resolved_task_id,
                    resolved_round_id,
                    resolved_scope_id,
                    FALSE
                ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'stocktake_round' THEN
            SELECT task.id, round_row.id
              INTO resolved_task_id, resolved_round_id
              FROM public.stocktake_rounds AS round_row
              JOIN public.stocktake_tasks AS task
                ON task.id = round_row.task_id
               AND task.task_type = 'opening'
             WHERE round_row.id::text = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.action <> 'stocktake.opening.round_submitted'
                   OR NOT public.{ROUND_SUBMISSION_FUNCTION}(
                       resolved_task_id,
                       resolved_round_id,
                       FALSE
                   ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'stocktake_review' THEN
            SELECT review_row.task_id,
                   review_row.id,
                   review_row.review_stage
              INTO resolved_task_id,
                   resolved_review_id,
                   resolved_review_stage
             FROM public.stocktake_reviews AS review_row
              JOIN public.stocktake_tasks AS task
                ON task.id = review_row.task_id
               AND task.task_type = 'opening'
             WHERE review_row.id::text = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.action <>
                   'stocktake.opening.' || resolved_review_stage ||
                   '_reviewed'
                   OR NEW.action NOT IN (
                    'stocktake.opening.region_reviewed',
                    'stocktake.opening.headquarters_reviewed'
                ) OR NOT public.{REVIEW_GRAPH_FUNCTION}(
                    resolved_review_id,
                    FALSE
                ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'stocktake_recount_case' THEN
            SELECT recount_case.task_id, recount_case.id
              INTO resolved_task_id, resolved_recount_case_id
              FROM public.stocktake_recount_cases AS recount_case
              JOIN public.stocktake_tasks AS task
                ON task.id = recount_case.task_id
               AND task.task_type = 'opening'
             WHERE recount_case.id::text = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.action <> 'stocktake.opening.recount_opened'
                   OR NOT public.{RECOUNT_GRAPH_FUNCTION}(
                       resolved_recount_case_id,
                       FALSE
                   ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'stocktake_observation_disposition' THEN
            SELECT disposition.task_id, disposition.id
              INTO resolved_task_id, resolved_disposition_id
              FROM public.stocktake_observation_dispositions AS disposition
              JOIN public.stocktake_tasks AS task
                ON task.id = disposition.task_id
               AND task.task_type = 'opening'
             WHERE disposition.id::text = NEW.aggregate_id;
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.action <>
                   'stocktake.opening.observation_disposed'
                   OR NOT public.{DISPOSITION_GRAPH_FUNCTION}(
                       resolved_disposition_id,
                       FALSE
                   ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'stocktake_posting' THEN
            SELECT posting.task_id
              INTO resolved_task_id
              FROM public.stocktake_postings AS posting
              JOIN public.stocktake_tasks AS task
                ON task.id = posting.task_id
               AND task.task_type = 'opening'
             WHERE posting.id::text = NEW.aggregate_id
               AND posting.posting_kind = 'opening';
            IF resolved_task_id IS NOT NULL THEN
                IF NEW.action <> 'stocktake.opening.posted'
                   OR NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_tasks AS terminal_task
                        WHERE terminal_task.id = resolved_task_id
                          AND terminal_task.status = 'posted'
                   )
                   OR NOT public.{TERMINAL_GRAPH_FUNCTION}(
                       resolved_task_id,
                       FALSE
                   ) THEN
                    RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
        ELSIF NEW.aggregate_type = 'inventory_transaction' THEN
            SELECT inventory_transaction.id,
                   inventory_transaction.movement_type = 'opening',
                   opening_task_row.id
              INTO resolved_transaction_id,
                   resolved_transaction_opening,
                   resolved_task_id
              FROM public.inventory_transactions AS inventory_transaction
              LEFT JOIN public.stocktake_postings AS opening_posting
                ON opening_posting.inventory_transaction_id =
                   inventory_transaction.id
               AND opening_posting.posting_kind = 'opening'
              LEFT JOIN public.stocktake_tasks AS opening_task_row
                ON opening_task_row.id = opening_posting.task_id
               AND opening_task_row.task_type = 'opening'
             WHERE inventory_transaction.id::text = NEW.aggregate_id;
            IF resolved_transaction_id IS NOT NULL THEN
                IF resolved_transaction_opening THEN
                    IF resolved_task_id IS NULL
                       OR NOT EXISTS (
                           SELECT 1
                             FROM public.stocktake_tasks AS terminal_task
                            WHERE terminal_task.id = resolved_task_id
                              AND terminal_task.status = 'posted'
                       )
                       OR NEW.action <> 'inventory.transaction.posted'
                       OR NOT public.{TERMINAL_GRAPH_FUNCTION}(
                           resolved_task_id,
                           FALSE
                       ) THEN
                        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                RETURN NEW;
            END IF;
        END IF;
        IF pg_catalog.left(NEW.action, 18) = 'stocktake.opening.' THEN
            RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    ELSE
        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}' USING ERRCODE = '55000';
    END IF;

    SELECT task.task_type = 'opening'
      INTO opening_task
      FROM public.stocktake_tasks AS task
     WHERE task.id = resolved_task_id;
    IF NOT COALESCE(opening_task, FALSE) THEN
        RETURN NEW;
    END IF;

    IF resolved_scope_id IS NOT NULL THEN
        IF resolved_disposition_id IS NOT NULL THEN
            IF NOT public.{DISPOSITION_GRAPH_FUNCTION}(
                resolved_disposition_id,
                FALSE
            ) THEN
                RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF NOT public.{SCOPE_COMPLETION_FUNCTION}(
            resolved_task_id,
            resolved_round_id,
            resolved_scope_id,
            FALSE
        ) THEN
            RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF resolved_review_id IS NOT NULL THEN
        IF NOT public.{REVIEW_GRAPH_FUNCTION}(
            resolved_review_id,
            FALSE
        ) THEN
            RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME = 'stocktake_rounds' AND NEW.status = 'counting' THEN
        IF (
            NEW.round_type = 'initial'
            AND NEW.round_no = 1
            AND NEW.recount_case_id IS NULL
            AND NOT public.{START_GRAPH_FUNCTION}(resolved_task_id, FALSE)
        ) OR (
            NEW.round_type = 'recount'
            AND NEW.round_no > 1
            AND (
                NEW.recount_case_id IS NULL
                OR NOT public.{RECOUNT_GRAPH_FUNCTION}(
                    NEW.recount_case_id,
                    FALSE
                )
            )
        ) OR NEW.round_type NOT IN ('initial', 'recount') THEN
            RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NOT public.{ROUND_SUBMISSION_FUNCTION}(
        resolved_task_id,
        resolved_round_id,
        FALSE
    ) OR NOT EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS submitted_task
          JOIN public.stocktake_rounds AS submitted_round
            ON submitted_round.id = resolved_round_id
           AND submitted_round.task_id = submitted_task.id
          JOIN public.stocktake_round_submissions AS submitted_proof
            ON submitted_proof.task_id = submitted_task.id
           AND submitted_proof.round_id = submitted_round.id
         WHERE submitted_task.id = resolved_task_id
           AND submitted_task.status = 'submitted'
           AND submitted_task.current_round_no = submitted_round.round_no
           AND submitted_task.submitted_at = submitted_proof.submitted_at
           AND submitted_task.updated_at = submitted_proof.submitted_at
    ) THEN
        RAISE EXCEPTION '{GRAPH_CLOSURE_ERROR}' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
""".strip()


GRAPH_CLOSURE_BODY = _opening_graph_closure_body()
GRAPH_CLOSURE_BODY_SHA256 = (
    "5d9dc35f6ff5a98ded70055d629484bbc9c2aa420f5bd84f3b45070563dd3c24"
)

# Machine-readable head catalog consumed by static tests and the release/startup
# gates.  Tuple fields are intentionally explicit so downstream checks never
# have to recover security properties by parsing CREATE FUNCTION text.
HEAD_ONLY_FUNCTION_CATALOG_FIELDS = (
    "signature",
    "name",
    "return_type",
    "language",
    "volatility",
    "argument_types",
    "argument_names",
    "security_definer",
    "search_path",
    "body_sha256",
)
HEAD_ONLY_FUNCTION_CATALOG = (
    (
        START_GRAPH_SIGNATURE,
        START_GRAPH_FUNCTION,
        "boolean",
        "sql",
        "v",
        ("uuid", "boolean"),
        ("p_task_id", "p_historical"),
        False,
        (FIXED_SEARCH_PATH,),
        START_GRAPH_BODY_SHA256,
    ),
    (
        ROUND_SUBMISSION_SIGNATURE,
        ROUND_SUBMISSION_FUNCTION,
        "boolean",
        "sql",
        "v",
        ("uuid", "uuid", "boolean"),
        ("p_task_id", "p_round_id", "p_historical"),
        False,
        (FIXED_SEARCH_PATH,),
        ROUND_SUBMISSION_BODY_SHA256,
    ),
    (
        SCOPE_COMPLETION_SIGNATURE,
        SCOPE_COMPLETION_FUNCTION,
        "boolean",
        "sql",
        "v",
        ("uuid", "uuid", "uuid", "boolean"),
        ("p_task_id", "p_round_id", "p_scope_id", "p_historical"),
        False,
        (FIXED_SEARCH_PATH,),
        SCOPE_COMPLETION_BODY_SHA256,
    ),
    (
        REVIEW_GRAPH_SIGNATURE,
        REVIEW_GRAPH_FUNCTION,
        "boolean",
        "sql",
        "v",
        ("uuid", "boolean"),
        ("p_review_id", "p_historical"),
        False,
        (FIXED_SEARCH_PATH,),
        REVIEW_GRAPH_BODY_SHA256,
    ),
    (
        RECOUNT_GRAPH_SIGNATURE,
        RECOUNT_GRAPH_FUNCTION,
        "boolean",
        "sql",
        "v",
        ("uuid", "boolean"),
        ("p_recount_case_id", "p_historical"),
        False,
        (FIXED_SEARCH_PATH,),
        RECOUNT_GRAPH_BODY_SHA256,
    ),
    (
        DISPOSITION_GRAPH_SIGNATURE,
        DISPOSITION_GRAPH_FUNCTION,
        "boolean",
        "sql",
        "v",
        ("uuid", "boolean"),
        ("p_disposition_id", "p_historical"),
        False,
        (FIXED_SEARCH_PATH,),
        DISPOSITION_GRAPH_BODY_SHA256,
    ),
    (
        TERMINAL_GRAPH_SIGNATURE,
        TERMINAL_GRAPH_FUNCTION,
        "boolean",
        "sql",
        "v",
        ("uuid", "boolean"),
        ("p_task_id", "p_historical"),
        False,
        (FIXED_SEARCH_PATH,),
        TERMINAL_GRAPH_BODY_SHA256,
    ),
    (
        INSERT_GUARD_SIGNATURE,
        INSERT_GUARD_FUNCTION,
        "trigger",
        "plpgsql",
        "v",
        (),
        (),
        True,
        (FIXED_SEARCH_PATH,),
        INSERT_GUARD_BODY_SHA256,
    ),
    (
        COUNT_WRITE_SIGNATURE,
        COUNT_WRITE_FUNCTION,
        "trigger",
        "plpgsql",
        "v",
        (),
        (),
        True,
        (FIXED_SEARCH_PATH,),
        COUNT_WRITE_BODY_SHA256,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        GRAPH_CLOSURE_FUNCTION,
        "trigger",
        "plpgsql",
        "v",
        (),
        (),
        True,
        (FIXED_SEARCH_PATH,),
        GRAPH_CLOSURE_BODY_SHA256,
    ),
)

OPENING_0052_TRIGGER_CATALOG_FIELDS = (
    "table",
    "name",
    "function_signature",
    "tgtype",
    "constraint",
    "deferrable",
    "initially_deferred",
    "enabled",
)
OPENING_0052_TRIGGER_CATALOG = (
    (
        "stocktake_tasks",
        INSERT_GUARD_TRIGGER,
        INSERT_GUARD_SIGNATURE,
        5,
        True,
        True,
        True,
        "A",
    ),
    *tuple(
        (
            table_name,
            trigger_name,
            COUNT_WRITE_SIGNATURE,
            7,
            False,
            False,
            False,
            "O",
        )
        for table_name, trigger_name in COUNT_WRITE_TRIGGER_CATALOG
    ),
    *tuple(
        (
            table_name,
            trigger_name,
            GRAPH_CLOSURE_SIGNATURE,
            21 if operations == "INSERT OR UPDATE" else 5,
            True,
            True,
            True,
            "A",
        )
        for table_name, trigger_name, operations
        in GRAPH_CLOSURE_TRIGGER_CATALOG
    ),
)


FIXED_TASK_BRANCH = """    ELSIF TG_TABLE_NAME = 'stocktake_tasks' THEN
        IF OLD.task_type <> 'opening' AND NEW.task_type <> 'opening' THEN
            RETURN NEW;
        END IF;
        IF OLD.id IS DISTINCT FROM NEW.id
           OR OLD.task_no IS DISTINCT FROM NEW.task_no
           OR OLD.task_type IS DISTINCT FROM NEW.task_type
           OR OLD.region_org_id IS DISTINCT FROM NEW.region_org_id
           OR OLD.blind_count IS DISTINCT FROM NEW.blind_count
           OR OLD.cutoff_ledger_cursor IS DISTINCT FROM
              NEW.cutoff_ledger_cursor
           OR OLD.cutoff_at IS DISTINCT FROM NEW.cutoff_at
           OR OLD.scope_manifest_sha256 IS DISTINCT FROM
              NEW.scope_manifest_sha256
           OR OLD.snapshot_manifest_sha256 IS DISTINCT FROM
              NEW.snapshot_manifest_sha256
           OR OLD.control_source_system_id IS DISTINCT FROM
              NEW.control_source_system_id
           OR OLD.control_sync_run_id IS DISTINCT FROM
              NEW.control_sync_run_id
           OR OLD.control_snapshot_at IS DISTINCT FROM
              NEW.control_snapshot_at
           OR OLD.control_manifest_sha256 IS DISTINCT FROM
              NEW.control_manifest_sha256
           OR OLD.created_by_user_id IS DISTINCT FROM
              NEW.created_by_user_id
           OR OLD.deadline IS DISTINCT FROM NEW.deadline
           OR OLD.issued_at IS DISTINCT FROM NEW.issued_at
           OR OLD.frozen_at IS DISTINCT FROM NEW.frozen_at
           OR OLD.cancelled_at IS DISTINCT FROM NEW.cancelled_at
           OR OLD.note IS DISTINCT FROM NEW.note
           OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'opening terminal task binding is immutable'
                USING ERRCODE = '55000';
        END IF;
        IF OLD.status NOT IN ('posted', 'closed')
           AND NOT (__RSC_0052_CURRENT_SCOPE_MASTER_PROOF__) THEN
            RAISE EXCEPTION 'opening task scope master graph is not current'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status NOT IN ('posted', 'closed')
           AND (
               EXISTS (
                   SELECT 1
                     FROM public.stocktake_postings AS premature_posting
                    WHERE premature_posting.task_id = NEW.id
               )
               OR EXISTS (
                   SELECT 1
                     FROM public.inventory_opening_establishments
                          AS premature_establishment
                    WHERE premature_establishment.task_id = NEW.id
               )
           ) THEN
            RAISE EXCEPTION 'opening task contains premature terminal facts'
                USING ERRCODE = '23514';
        END IF;
        IF OLD.status = 'recount_required' AND NEW.status = 'counting' THEN
            IF NEW.current_round_no <> OLD.current_round_no + 1
               OR OLD.submitted_at IS DISTINCT FROM NEW.submitted_at
               OR OLD.submitted_at IS NULL
               OR NEW.version <> OLD.version + 1
               OR NEW.updated_at <= OLD.updated_at THEN
                RAISE EXCEPTION 'opening recount task transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_rounds AS current_round
                  JOIN public.stocktake_recount_cases AS recount_case
                    ON recount_case.id = current_round.recount_case_id
                   AND recount_case.task_id = NEW.id
                   AND recount_case.next_round_no =
                       current_round.round_no
                   AND recount_case.opened_at = NEW.updated_at
                  JOIN public.stocktake_rounds AS source_round
                    ON source_round.id = recount_case.source_round_id
                   AND source_round.task_id = NEW.id
                   AND source_round.round_no = OLD.current_round_no
                   AND source_round.status = 'submitted'
                   AND source_round.submitted_at = OLD.submitted_at
                   AND source_round.updated_at = source_round.submitted_at
                   AND source_round.count_manifest_sha256 ~ '^[0-9a-f]{64}$'
                  JOIN public.stocktake_round_submissions AS submission
                    ON submission.id = recount_case.source_round_submission_id
                   AND submission.task_id = NEW.id
                   AND submission.round_id = source_round.id
                   AND submission.submitted_at = OLD.submitted_at
                   AND submission.created_at = submission.submitted_at
                   AND submission.submitted_by_user_id =
                       source_round.submitted_by_user_id
                   AND submission.count_manifest_sha256 =
                       source_round.count_manifest_sha256
                   AND submission.round_manifest_sha256 ~ '^[0-9a-f]{64}$'
                   AND submission.request_sha256 ~ '^[0-9a-f]{64}$'
                   AND submission.idempotency_key_hash ~ '^[0-9a-f]{64}$'
                  JOIN public.stocktake_scope_count_completions AS sealing
                    ON sealing.id = submission.sealing_completion_id
                   AND sealing.task_id = submission.task_id
                   AND sealing.round_id = submission.round_id
                   AND sealing.completed_by_user_id =
                       submission.submitted_by_user_id
                   AND sealing.completed_by_person_id =
                       submission.submitted_by_person_id
                   AND sealing.completed_role_assignment_id =
                       submission.submitted_role_assignment_id
                   AND sealing.authorization_version =
                       submission.authorization_version
                   AND sealing.completed_at = submission.submitted_at
                  JOIN public.stocktake_difference_set_completions
                       AS difference_completion
                    ON difference_completion.id =
                       recount_case.source_difference_completion_id
                   AND difference_completion.task_id = NEW.id
                   AND difference_completion.round_id = source_round.id
                   AND difference_completion.round_submission_id =
                       submission.id
                  JOIN public.stocktake_reviews AS trigger_review
                    ON trigger_review.id = recount_case.trigger_review_id
                   AND trigger_review.task_id = NEW.id
                   AND trigger_review.round_id = source_round.id
                  JOIN public.state_transition_events
                       AS source_submission_event
                    ON source_submission_event.aggregate_type =
                       'stocktake_task'
                   AND source_submission_event.aggregate_id = NEW.id::text
                   AND source_submission_event.from_status = 'counting'
                   AND source_submission_event.to_status = 'submitted'
                   AND source_submission_event.reason = CASE
                       WHEN source_round.round_no = 1
                            AND source_round.round_type = 'initial'
                       THEN 'opening_initial_round_submitted'
                       WHEN source_round.round_no > 1
                            AND source_round.round_type = 'recount'
                       THEN 'opening_recount_round_submitted'
                       ELSE NULL
                   END
                   AND source_submission_event.actor_id =
                       submission.submitted_by_user_id
                   AND source_submission_event.occurred_at =
                       submission.submitted_at
                   AND source_submission_event.created_at =
                       submission.submitted_at
                   AND source_submission_event.metadata_jsonb =
                       __RSC_0052_SOURCE_SUBMISSION_EVENT_METADATA__
                  JOIN public.state_transition_events AS transition_event
                    ON transition_event.aggregate_type = 'stocktake_task'
                   AND transition_event.aggregate_id = NEW.id::text
                   AND transition_event.from_status = 'recount_required'
                   AND transition_event.to_status = 'counting'
                   AND transition_event.reason = 'opening_recount_opened'
                   AND transition_event.actor_id =
                       recount_case.opened_by_user_id
                   AND transition_event.occurred_at = NEW.updated_at
                   AND transition_event.created_at = NEW.updated_at
                   AND transition_event.metadata_jsonb =
                       __RSC_0052_RECOUNT_EVENT_METADATA__
                 WHERE current_round.task_id = NEW.id
                   AND current_round.round_no = NEW.current_round_no
                   AND current_round.round_type = 'recount'
                   AND current_round.status = 'counting'
                   AND current_round.submitted_by_user_id IS NULL
                   AND current_round.submitted_at IS NULL
                   AND current_round.count_manifest_sha256 IS NULL
                   AND current_round.started_at = NEW.updated_at
                   AND current_round.created_at = NEW.updated_at
                   AND current_round.updated_at = NEW.updated_at
                   AND recount_case.created_at = recount_case.opened_at
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_round_submissions
                              AS premature_submission
                        WHERE premature_submission.task_id = NEW.id
                          AND premature_submission.round_id = current_round.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_scope_count_completions
                              AS premature_completion
                        WHERE premature_completion.task_id = NEW.id
                          AND premature_completion.round_id = current_round.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_count_lines AS premature_line
                        WHERE premature_line.task_id = NEW.id
                          AND premature_line.round_id = current_round.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_count_observations
                              AS premature_observation
                        WHERE premature_observation.task_id = NEW.id
                          AND premature_observation.round_id = current_round.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_differences
                              AS premature_difference
                        WHERE premature_difference.task_id = NEW.id
                          AND premature_difference.round_id = current_round.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_reviews AS premature_review
                        WHERE premature_review.task_id = NEW.id
                          AND premature_review.round_id = current_round.id
                   )
                   AND __RSC_0052_RECOUNT_SOURCE_SUBMISSION_PROOF__
                   AND __RSC_0052_RECOUNT_REPLAY_PROOF__
                   AND __RSC_0052_RECOUNT_SIDE_EFFECT_PROOF__
            ) <> 1 OR (
                SELECT task.status
                  FROM public.stocktake_tasks AS task
                 WHERE task.id = NEW.id
            ) IS DISTINCT FROM NEW.status THEN
                RAISE EXCEPTION 'opening recount task evidence is incomplete'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.current_round_no IS DISTINCT FROM NEW.current_round_no THEN
            RAISE EXCEPTION 'opening terminal task binding is immutable'
                USING ERRCODE = '55000';
        END IF;
        IF OLD.status = 'counting' AND NEW.status = 'submitted' THEN
            IF OLD.submitted_at IS NOT DISTINCT FROM NEW.submitted_at
               OR NEW.current_round_no IS DISTINCT FROM OLD.current_round_no
               OR NEW.submitted_at IS NULL
               OR (
                   OLD.submitted_at IS NOT NULL
                   AND NEW.submitted_at <= OLD.submitted_at
               )
               OR NEW.version <> OLD.version + 1
               OR NEW.updated_at IS DISTINCT FROM NEW.submitted_at THEN
                RAISE EXCEPTION 'opening task submission transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_rounds AS round_row
                  JOIN public.stocktake_round_submissions AS submission
                    ON submission.task_id = NEW.id
                   AND submission.round_id = round_row.id
                   AND submission.submitted_at = NEW.submitted_at
                   AND submission.created_at = submission.submitted_at
                   AND submission.submitted_by_user_id =
                       round_row.submitted_by_user_id
                   AND submission.count_manifest_sha256 =
                       round_row.count_manifest_sha256
                   AND submission.round_manifest_sha256 ~ '^[0-9a-f]{64}$'
                   AND submission.request_sha256 ~ '^[0-9a-f]{64}$'
                   AND submission.idempotency_key_hash ~ '^[0-9a-f]{64}$'
                  JOIN public.stocktake_scope_count_completions AS sealing
                    ON sealing.id = submission.sealing_completion_id
                   AND sealing.task_id = submission.task_id
                   AND sealing.round_id = submission.round_id
                   AND sealing.completed_by_user_id =
                       submission.submitted_by_user_id
                   AND sealing.completed_by_person_id =
                       submission.submitted_by_person_id
                   AND sealing.completed_role_assignment_id =
                       submission.submitted_role_assignment_id
                   AND sealing.authorization_version =
                       submission.authorization_version
                   AND sealing.completed_at = submission.submitted_at
                  JOIN public.state_transition_events AS submission_event
                    ON submission_event.aggregate_type = 'stocktake_task'
                   AND submission_event.aggregate_id = NEW.id::text
                   AND submission_event.from_status = 'counting'
                   AND submission_event.to_status = 'submitted'
                   AND submission_event.reason = CASE
                       WHEN round_row.round_no = 1
                            AND round_row.round_type = 'initial'
                       THEN 'opening_initial_round_submitted'
                       WHEN round_row.round_no > 1
                            AND round_row.round_type = 'recount'
                       THEN 'opening_recount_round_submitted'
                       ELSE NULL
                   END
                   AND submission_event.actor_id =
                       submission.submitted_by_user_id
                   AND submission_event.occurred_at = NEW.submitted_at
                   AND submission_event.created_at = NEW.submitted_at
                   AND submission_event.metadata_jsonb =
                       __RSC_0052_SUBMISSION_EVENT_METADATA__
                 WHERE round_row.task_id = NEW.id
                   AND round_row.round_no = NEW.current_round_no
                   AND round_row.status = 'submitted'
                   AND round_row.submitted_at = NEW.submitted_at
                   AND round_row.updated_at = round_row.submitted_at
                   AND round_row.started_at <= round_row.submitted_at
                   AND round_row.created_at <= round_row.submitted_at
                   AND round_row.count_manifest_sha256 ~ '^[0-9a-f]{64}$'
                   AND __RSC_0052_CURRENT_SUBMISSION_PROOF__
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_reviews AS premature_review
                        WHERE premature_review.task_id = NEW.id
                          AND premature_review.round_id = round_row.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_recount_cases
                              AS premature_recount
                        WHERE premature_recount.task_id = NEW.id
                          AND premature_recount.source_round_id = round_row.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_postings AS premature_posting
                        WHERE premature_posting.task_id = NEW.id
                   )
                   AND (
                       (
                           round_row.round_no = 1
                           AND round_row.round_type = 'initial'
                           AND round_row.recount_case_id IS NULL
                       )
                       OR
                       (
                           round_row.round_no > 1
                           AND round_row.round_type = 'recount'
                           AND round_row.recount_case_id IS NOT NULL
                       )
                   )
            ) <> 1 OR (
                SELECT task.status
                  FROM public.stocktake_tasks AS task
                 WHERE task.id = NEW.id
            ) IS DISTINCT FROM NEW.status THEN
                RAISE EXCEPTION 'opening task submission evidence is incomplete'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.submitted_at IS DISTINCT FROM NEW.submitted_at THEN
            RAISE EXCEPTION 'opening terminal task binding is immutable'
                USING ERRCODE = '55000';
        END IF;
        IF OLD.status = 'submitted'
           AND NEW.status IN ('hq_review', 'recount_required') THEN
            IF NEW.current_round_no IS DISTINCT FROM OLD.current_round_no
               OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
               OR NEW.posted_at IS DISTINCT FROM OLD.posted_at
               OR NEW.closed_at IS DISTINCT FROM OLD.closed_at
               OR NEW.version <> OLD.version + 1
               OR NEW.updated_at < OLD.updated_at THEN
                RAISE EXCEPTION 'opening region review transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_rounds AS round_row
                  JOIN public.stocktake_round_submissions AS submission
                    ON submission.task_id = NEW.id
                   AND submission.round_id = round_row.id
                   AND submission.submitted_at = NEW.submitted_at
                   AND submission.count_manifest_sha256 =
                       round_row.count_manifest_sha256
                  JOIN public.stocktake_reviews AS review
                    ON review.task_id = NEW.id
                   AND review.round_id = round_row.id
                   AND review.review_stage = 'region'
                   AND (
                       (
                           NEW.status = 'hq_review'
                           AND review.decision = 'approve'
                       )
                       OR
                       (
                           NEW.status = 'recount_required'
                           AND review.decision IN ('recount', 'reject')
                       )
                   )
                   AND review.created_at = review.reviewed_at
                   AND review.reviewed_at = NEW.updated_at
                   AND review.reviewed_at >= submission.submitted_at
                   AND review.decision_manifest_sha256 ~ '^[0-9a-f]{64}$'
                   AND review.idempotency_key_hash ~ '^[0-9a-f]{64}$'
                   AND __RSC_0052_CURRENT_REGION_REVIEW_ACTOR__
                   AND __RSC_0052_CURRENT_REGION_REVIEW_SCOPES__
                  JOIN public.state_transition_events AS transition_event
                    ON transition_event.aggregate_type = 'stocktake_task'
                   AND transition_event.aggregate_id = NEW.id::text
                   AND transition_event.from_status = 'submitted'
                   AND transition_event.to_status = NEW.status
                   AND transition_event.reason =
                       'opening_region_review_' || review.decision
                   AND transition_event.actor_id = review.reviewer_user_id
                   AND transition_event.occurred_at = review.reviewed_at
                   AND transition_event.created_at = review.reviewed_at
                   AND transition_event.metadata_jsonb =
                       __RSC_0052_REGION_REVIEW_EVENT_METADATA__
                 WHERE round_row.task_id = NEW.id
                   AND round_row.round_no = NEW.current_round_no
                   AND round_row.status = 'submitted'
                   AND round_row.submitted_at = NEW.submitted_at
                   AND round_row.count_manifest_sha256 ~ '^[0-9a-f]{64}$'
                   AND __RSC_0052_REGION_DIFFERENCE_COMPLETION_PROOF__
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_reviews
                              AS premature_headquarters_review
                        WHERE premature_headquarters_review.task_id = NEW.id
                          AND premature_headquarters_review.round_id =
                              round_row.id
                          AND premature_headquarters_review.review_stage =
                              'headquarters'
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_recount_cases
                              AS premature_recount
                        WHERE premature_recount.task_id = NEW.id
                          AND premature_recount.source_round_id = round_row.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_differences AS difference
                        WHERE difference.task_id = NEW.id
                          AND difference.round_id = round_row.id
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.stocktake_review_items AS item
                               WHERE item.review_id = review.id
                                 AND item.difference_id = difference.id
                                 AND item.task_id = NEW.id
                                 AND item.round_id = round_row.id
                                 AND item.created_at = review.reviewed_at
                          )
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_review_items AS item
                         LEFT JOIN public.stocktake_differences AS difference
                           ON difference.id = item.difference_id
                          AND difference.task_id = NEW.id
                          AND difference.round_id = round_row.id
                        WHERE item.review_id = review.id
                          AND difference.id IS NULL
                   )
                   AND __RSC_0052_REGION_ITEM_RULES__
            ) <> 1 OR (
                SELECT task.status
                  FROM public.stocktake_tasks AS task
                 WHERE task.id = NEW.id
            ) IS DISTINCT FROM NEW.status THEN
                RAISE EXCEPTION 'opening region review evidence is incomplete'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF OLD.status = 'hq_review'
           AND NEW.status IN ('approved', 'recount_required') THEN
            IF NEW.current_round_no IS DISTINCT FROM OLD.current_round_no
               OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
               OR NEW.posted_at IS DISTINCT FROM OLD.posted_at
               OR NEW.closed_at IS DISTINCT FROM OLD.closed_at
               OR NEW.version <> OLD.version + 1
               OR NEW.updated_at <= OLD.updated_at THEN
                RAISE EXCEPTION
                    'opening headquarters review transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF (
                SELECT pg_catalog.count(*)
                  FROM public.stocktake_rounds AS round_row
                  JOIN public.stocktake_round_submissions AS submission
                    ON submission.task_id = NEW.id
                   AND submission.round_id = round_row.id
                   AND submission.submitted_at = NEW.submitted_at
                   AND submission.count_manifest_sha256 =
                       round_row.count_manifest_sha256
                  JOIN public.stocktake_reviews AS region_review
                    ON region_review.task_id = NEW.id
                   AND region_review.round_id = round_row.id
                   AND region_review.review_stage = 'region'
                   AND region_review.decision = 'approve'
                   AND region_review.created_at = region_review.reviewed_at
                   AND region_review.reviewed_at = OLD.updated_at
                   AND region_review.decision_manifest_sha256 ~
                       '^[0-9a-f]{64}$'
                   AND region_review.idempotency_key_hash ~ '^[0-9a-f]{64}$'
                   AND __RSC_0052_CURRENT_PRIOR_REGION_REVIEW_ACTOR__
                  JOIN public.stocktake_reviews AS review
                    ON review.task_id = NEW.id
                   AND review.round_id = round_row.id
                   AND review.review_stage = 'headquarters'
                   AND review.decision = CASE NEW.status
                       WHEN 'approved' THEN 'approve'
                       ELSE 'reject'
                   END
                   AND review.created_at = review.reviewed_at
                   AND review.reviewed_at = NEW.updated_at
                   AND review.reviewed_at > region_review.reviewed_at
                   AND review.reviewer_user_id <>
                       region_review.reviewer_user_id
                   AND review.reviewer_person_id <>
                       region_review.reviewer_person_id
                   AND review.reviewer_role_assignment_id <>
                       region_review.reviewer_role_assignment_id
                   AND review.decision_manifest_sha256 ~ '^[0-9a-f]{64}$'
                   AND review.idempotency_key_hash ~ '^[0-9a-f]{64}$'
                   AND __RSC_0052_CURRENT_HEADQUARTERS_REVIEW_ACTOR__
                  JOIN public.state_transition_events
                       AS region_transition_event
                    ON region_transition_event.aggregate_type =
                       'stocktake_task'
                   AND region_transition_event.aggregate_id = NEW.id::text
                   AND region_transition_event.from_status = 'submitted'
                   AND region_transition_event.to_status = 'hq_review'
                   AND region_transition_event.reason =
                       'opening_region_review_approve'
                   AND region_transition_event.actor_id =
                       region_review.reviewer_user_id
                   AND region_transition_event.occurred_at =
                       region_review.reviewed_at
                   AND region_transition_event.created_at =
                       region_review.reviewed_at
                   AND region_transition_event.metadata_jsonb =
                       __RSC_0052_PRIOR_REGION_REVIEW_EVENT_METADATA__
                  JOIN public.state_transition_events AS transition_event
                    ON transition_event.aggregate_type = 'stocktake_task'
                   AND transition_event.aggregate_id = NEW.id::text
                   AND transition_event.from_status = 'hq_review'
                   AND transition_event.to_status = NEW.status
                   AND transition_event.reason =
                       'opening_headquarters_review_' || review.decision
                   AND transition_event.actor_id = review.reviewer_user_id
                   AND transition_event.occurred_at = review.reviewed_at
                   AND transition_event.created_at = review.reviewed_at
                   AND transition_event.metadata_jsonb =
                       __RSC_0052_HEADQUARTERS_REVIEW_EVENT_METADATA__
                 WHERE round_row.task_id = NEW.id
                   AND round_row.round_no = NEW.current_round_no
                   AND round_row.status = 'submitted'
                   AND round_row.submitted_at = NEW.submitted_at
                   AND round_row.count_manifest_sha256 ~ '^[0-9a-f]{64}$'
                   AND __RSC_0052_PRIOR_REGION_DIFFERENCE_COMPLETION_PROOF__
                   AND __RSC_0052_HEADQUARTERS_DIFFERENCE_COMPLETION_PROOF__
                   AND (
                       SELECT pg_catalog.count(*)
                         FROM public.stocktake_reviews AS exact_review
                        WHERE exact_review.task_id = NEW.id
                          AND exact_review.round_id = round_row.id
                   ) = 2
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_recount_cases
                              AS premature_recount
                        WHERE premature_recount.task_id = NEW.id
                          AND premature_recount.source_round_id = round_row.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_differences AS difference
                        WHERE difference.task_id = NEW.id
                          AND difference.round_id = round_row.id
                          AND (
                              NOT EXISTS (
                                  SELECT 1
                                    FROM public.stocktake_review_items AS item
                                   WHERE item.review_id = review.id
                                     AND item.difference_id = difference.id
                                     AND item.task_id = NEW.id
                                     AND item.round_id = round_row.id
                                     AND item.created_at = review.reviewed_at
                              )
                              OR NOT EXISTS (
                                  SELECT 1
                                    FROM public.stocktake_review_items AS item
                                   WHERE item.review_id = region_review.id
                                     AND item.difference_id = difference.id
                                     AND item.task_id = NEW.id
                                     AND item.round_id = round_row.id
                                     AND item.created_at =
                                         region_review.reviewed_at
                              )
                          )
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_review_items AS item
                         LEFT JOIN public.stocktake_differences AS difference
                           ON difference.id = item.difference_id
                          AND difference.task_id = NEW.id
                          AND difference.round_id = round_row.id
                        WHERE item.review_id IN (region_review.id, review.id)
                          AND difference.id IS NULL
                   )
                   AND __RSC_0052_PRIOR_REGION_ITEM_RULES__
                   AND __RSC_0052_HEADQUARTERS_ITEM_RULES__
            ) <> 1 OR (
                SELECT task.status
                  FROM public.stocktake_tasks AS task
                 WHERE task.id = NEW.id
            ) IS DISTINCT FROM NEW.status THEN
                RAISE EXCEPTION
                    'opening headquarters review evidence is incomplete'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF OLD.status = 'approved' AND NEW.status = 'posted' THEN
            IF OLD.task_type <> 'opening'
               OR NEW.task_type <> 'opening'
               OR NEW.current_round_no IS DISTINCT FROM OLD.current_round_no
               OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
               OR OLD.posted_at IS NOT NULL
               OR NEW.posted_at IS NULL
               OR NEW.closed_at IS NOT NULL
               OR NEW.version <> OLD.version + 1
               OR NEW.updated_at IS DISTINCT FROM NEW.posted_at THEN
                RAISE EXCEPTION 'opening task post transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.status = 'posted' AND NEW.status = 'closed' THEN
            IF OLD.task_type <> 'opening'
               OR NEW.task_type <> 'opening'
               OR NEW.current_round_no IS DISTINCT FROM OLD.current_round_no
               OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
               OR OLD.posted_at IS NULL
               OR NEW.posted_at IS DISTINCT FROM OLD.posted_at
               OR OLD.closed_at IS NOT NULL
               OR NEW.closed_at IS NULL
               OR NEW.closed_at <= NEW.posted_at
               OR NEW.version <> OLD.version + 1
               OR NEW.updated_at IS DISTINCT FROM NEW.closed_at THEN
                RAISE EXCEPTION 'opening task close transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.status IN ('posted', 'closed')
              OR NEW.status IN ('posted', 'closed') THEN
            RAISE EXCEPTION 'opening terminal task facts are immutable'
                USING ERRCODE = '55000';
        ELSIF OLD.status = NEW.status THEN
            IF OLD.current_round_no IS DISTINCT FROM NEW.current_round_no
               OR OLD.submitted_at IS DISTINCT FROM NEW.submitted_at
               OR OLD.posted_at IS DISTINCT FROM NEW.posted_at
               OR OLD.closed_at IS DISTINCT FROM NEW.closed_at
               OR OLD.version IS DISTINCT FROM NEW.version
               OR OLD.updated_at IS DISTINCT FROM NEW.updated_at THEN
                RAISE EXCEPTION 'opening terminal task facts are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        ELSIF (
            (OLD.status = 'counting' AND NEW.status = 'submitted')
            OR (
                OLD.status = 'submitted'
                AND NEW.status IN ('hq_review', 'recount_required')
            )
            OR (
                OLD.status = 'hq_review'
                AND NEW.status IN ('approved', 'recount_required')
            )
            OR (
                OLD.status = 'recount_required'
                AND NEW.status = 'counting'
            )
        ) THEN
            IF OLD.posted_at IS DISTINCT FROM NEW.posted_at
               OR OLD.closed_at IS DISTINCT FROM NEW.closed_at THEN
                RAISE EXCEPTION 'opening terminal task facts are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        ELSE
            RAISE EXCEPTION 'opening task status transition is invalid'
                USING ERRCODE = '23514';
        END IF;
        SELECT task.status
          INTO current_task_status
          FROM public.stocktake_tasks AS task
         WHERE task.id = NEW.id;
        IF current_task_status IS DISTINCT FROM NEW.status THEN
            RAISE EXCEPTION
                'opening post and close require independent transactions'
                USING ERRCODE = '23514';
        END IF;
        IF NOT __RSC_0052_TERMINAL_SIDE_EFFECT_PROOF__ THEN
            RAISE EXCEPTION
                'opening terminal side effects are not canonical'
                USING ERRCODE = '23514';
        END IF;
        opening_task_id := NEW.id;
        SELECT posting.inventory_transaction_id
          INTO opening_transaction_id
          FROM public.stocktake_postings AS posting
         WHERE posting.task_id = NEW.id
           AND posting.posting_kind = 'opening';
"""

FIXED_TASK_BRANCH = FIXED_TASK_BRANCH.replace(
    "__RSC_0052_CURRENT_SCOPE_MASTER_PROOF__",
    _current_scope_master_proof_sql(
        "NEW.id",
        "NEW.region_org_id",
        alias_suffix="task_transition",
    ),
).replace(
    "__RSC_0052_CURRENT_REGION_REVIEW_SCOPES__",
    f"""NOT EXISTS (
        SELECT 1
          FROM public.stocktake_scopes AS review_scope
          JOIN public.stock_locations AS review_location
            ON review_location.id = review_scope.location_id
         WHERE review_scope.task_id = NEW.id
           AND (
               NOT ({_current_entitlement_target_sql(
                   user_id_sql='review.reviewer_user_id',
                   assignment_id_sql='review.reviewer_role_assignment_id',
                   permission_resource='stocktake',
                   permission_action='review_region',
                   target_scope_type_sql="'organization'",
                   target_scope_id_sql='review_scope.owner_org_id::text',
                   alias_suffix='region_review_owner',
               )})
               OR NOT ({_current_entitlement_target_sql(
                   user_id_sql='review.reviewer_user_id',
                   assignment_id_sql='review.reviewer_role_assignment_id',
                   permission_resource='stocktake',
                   permission_action='review_region',
                   target_scope_type_sql="'organization'",
                   target_scope_id_sql='review_location.owner_org_id::text',
                   alias_suffix='region_review_location_owner',
               )})
           )
    )""",
).replace(
    "__RSC_0052_CURRENT_REGION_REVIEW_ACTOR__",
    _current_authorization_sql(
        user_id_sql="review.reviewer_user_id",
        person_id_sql="review.reviewer_person_id",
        assignment_id_sql="review.reviewer_role_assignment_id",
        authorization_version_sql="review.authorization_version",
        occurred_at_sql="review.reviewed_at",
        role_code_sql="'provincial_manager'",
        scope_type_sql="'organization'",
        scope_id_sql="NEW.region_org_id::text",
        permission_resource="stocktake",
        permission_action="review_region",
        allow_scheduled=True,
        alias_suffix="region_reviewer",
    ),
).replace(
    "__RSC_0052_CURRENT_PRIOR_REGION_REVIEW_ACTOR__",
    _historical_review_actor_sql(
        "region_review",
        "NEW",
        headquarters=False,
    ),
).replace(
    "__RSC_0052_CURRENT_HEADQUARTERS_REVIEW_ACTOR__",
    _current_authorization_sql(
        user_id_sql="review.reviewer_user_id",
        person_id_sql="review.reviewer_person_id",
        assignment_id_sql="review.reviewer_role_assignment_id",
        authorization_version_sql="review.authorization_version",
        occurred_at_sql="review.reviewed_at",
        role_code_sql="'admin'",
        scope_type_sql="'national'",
        scope_id_sql="'*'",
        permission_resource="stocktake",
        permission_action="review_headquarters",
        allow_scheduled=True,
        alias_suffix="headquarters_reviewer",
    ),
).replace(
    "__RSC_0052_REGION_ITEM_RULES__",
    _opening_review_item_rules_sql(
        "review", "round_row", historical=False
    ),
).replace(
    "__RSC_0052_PRIOR_REGION_ITEM_RULES__",
    _opening_review_item_rules_sql(
        "region_review", "round_row", historical=True
    ),
).replace(
    "__RSC_0052_HEADQUARTERS_ITEM_RULES__",
    _opening_review_item_rules_sql(
        "review", "round_row", historical=False
    ),
).replace(
    "__RSC_0052_SOURCE_SUBMISSION_EVENT_METADATA__",
    _round_submission_event_metadata_sql("source_round", "NEW.id"),
).replace(
    "__RSC_0052_SUBMISSION_EVENT_METADATA__",
    _round_submission_event_metadata_sql("round_row", "NEW.id"),
).replace(
    "__RSC_0052_REGION_REVIEW_EVENT_METADATA__",
    _review_event_metadata_sql("review", "round_row"),
).replace(
    "__RSC_0052_HEADQUARTERS_REVIEW_EVENT_METADATA__",
    _review_event_metadata_sql("review", "round_row"),
).replace(
    "__RSC_0052_PRIOR_REGION_REVIEW_EVENT_METADATA__",
    _review_event_metadata_sql("region_review", "round_row"),
).replace(
    "__RSC_0052_RECOUNT_EVENT_METADATA__",
    _recount_event_metadata_sql("recount_case", "current_round"),
).replace(
    "__RSC_0052_RECOUNT_SOURCE_SUBMISSION_PROOF__",
    f"public.{ROUND_SUBMISSION_FUNCTION}(NEW.id, source_round.id, TRUE)",
).replace(
    "__RSC_0052_CURRENT_SUBMISSION_PROOF__",
    f"public.{ROUND_SUBMISSION_FUNCTION}(NEW.id, round_row.id, FALSE)",
).replace(
    "__RSC_0052_RECOUNT_REPLAY_PROOF__",
    _recount_case_replay_proof_sql(
        task_id_sql="NEW.id",
        region_org_id_sql="NEW.region_org_id",
        task_scope_manifest_sql="NEW.scope_manifest_sha256",
        case_alias="recount_case",
        source_round_alias="source_round",
        submission_alias="submission",
        sealing_alias="sealing",
        completion_alias="difference_completion",
        trigger_review_alias="trigger_review",
        historical=False,
    ),
).replace(
    "__RSC_0052_RECOUNT_SIDE_EFFECT_PROOF__",
    _recount_side_effect_proof_sql(
        "NEW.id",
        "recount_case",
        "current_round",
        historical=False,
    ),
).replace(
    "__RSC_0052_REGION_DIFFERENCE_COMPLETION_PROOF__",
    _review_difference_completion_proof_sql(
        "NEW.id",
        "round_row",
        "submission",
        "review",
    ),
).replace(
    "__RSC_0052_PRIOR_REGION_DIFFERENCE_COMPLETION_PROOF__",
    _review_difference_completion_proof_sql(
        "NEW.id",
        "round_row",
        "submission",
        "region_review",
    ),
).replace(
    "__RSC_0052_HEADQUARTERS_DIFFERENCE_COMPLETION_PROOF__",
    _review_difference_completion_proof_sql(
        "NEW.id",
        "round_row",
        "submission",
        "review",
    ),
).replace(
    "__RSC_0052_TERMINAL_SIDE_EFFECT_PROOF__",
    f"public.{TERMINAL_GRAPH_FUNCTION}(NEW.id, FALSE)",
)


# All tables read by the graph helper, its two callers, or the new transition
# proofs are locked together so the verified catalog cannot race a live fact.
LOCK_TABLES = (
    "auth_identities",
    "audit_chain_heads",
    "audit_events",
    "custody_assignments",
    "external_object_versions",
    "external_objects",
    "inventory_freezes",
    "inventory_ledger_heads",
    "inventory_lots",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_serials",
    "inventory_transactions",
    "materials",
    "material_inventory_policies",
    "opening_control_reconciliation_command_consumptions",
    "opening_control_reconciliation_runs",
    "organizations",
    "outbox_events",
    "people",
    "permissions",
    "qr_codes",
    "reconciliation_commands",
    "reconciliation_items",
    "role_assignments",
    "role_permissions",
    "roles",
    "serial_current_positions",
    "state_transition_events",
    "stock_accounts",
    "stock_balances",
    "stock_locations",
    "stocktake_control_snapshot_lines",
    "stocktake_count_lines",
    "stocktake_count_observations",
    "stocktake_count_serials",
    "stocktake_difference_set_completions",
    "stocktake_differences",
    "stocktake_observation_dispositions",
    "stocktake_posting_items",
    "stocktake_postings",
    "stocktake_recount_cases",
    "stocktake_recount_scope_assignments",
    "stocktake_review_items",
    "stocktake_reviews",
    "stocktake_round_submissions",
    "stocktake_rounds",
    "stocktake_scope_count_completions",
    "stocktake_scopes",
    "stocktake_snapshot_lines",
    "stocktake_tasks",
    "source_systems",
    "sync_batches",
    "sync_inbox_events",
    "sync_runs",
    "users",
)

# table, trigger, caller signature, PostgreSQL tgtype
TRIGGER_CATALOG = (
    (
        "inventory_transactions",
        "trg_inventory_transactions_opening_commit_0022",
        COMMIT_SIGNATURE,
        5,
    ),
    (
        "inventory_movements",
        "trg_inventory_movements_opening_commit_0022",
        COMMIT_SIGNATURE,
        5,
    ),
    (
        "inventory_movement_serials",
        "trg_inventory_movement_serials_opening_commit_0022",
        COMMIT_SIGNATURE,
        5,
    ),
    (
        "stocktake_postings",
        "trg_stocktake_postings_opening_commit_0022",
        COMMIT_SIGNATURE,
        5,
    ),
    (
        "stocktake_posting_items",
        "trg_stocktake_posting_items_opening_commit_0022",
        COMMIT_SIGNATURE,
        5,
    ),
    (
        "inventory_opening_establishments",
        "trg_inventory_opening_establishments_commit_0022",
        COMMIT_SIGNATURE,
        5,
    ),
    (
        "inventory_freezes",
        "trg_inventory_freezes_opening_commit_0022",
        COMMIT_SIGNATURE,
        17,
    ),
    (
        "stocktake_tasks",
        "trg_stocktake_tasks_opening_commit_0022",
        COMMIT_SIGNATURE,
        17,
    ),
    (
        "state_transition_events",
        "trg_state_transition_events_opening_commit_0022",
        COMMIT_SIGNATURE,
        5,
    ),
    (
        "outbox_events",
        "trg_outbox_events_opening_commit_0022",
        COMMIT_SIGNATURE,
        5,
    ),
    (
        "audit_events",
        "trg_audit_events_opening_commit_0022",
        COMMIT_SIGNATURE,
        5,
    ),
    (
        "stock_accounts",
        "trg_stock_accounts_opening_observation_commit_0023",
        ACCOUNT_SIGNATURE,
        5,
    ),
)

# table, trigger, caller signature, PostgreSQL tgtype.  These 0016 bindings
# are ordinary immediate guards, not the deferred terminal constraint graph.
REVIEW_GUARD_TRIGGER_CATALOG = (
    (
        "stocktake_reviews",
        "trg_stocktake_reviews_difference_completion_0016",
        REVIEW_COMPLETION_SIGNATURE,
        7,
    ),
    (
        "stocktake_difference_set_completions",
        "trg_stocktake_difference_set_completions_immutable_0016",
        REVIEW_IMMUTABLE_SIGNATURE,
        27,
    ),
    (
        "stocktake_difference_set_completions",
        # The 0016 DDL token is 64 bytes; PostgreSQL stores 63 bytes.
        "trg_stocktake_difference_set_completions_immutable_truncate_001",
        REVIEW_IMMUTABLE_SIGNATURE,
        34,
    ),
    (
        "stocktake_observation_dispositions",
        "trg_stocktake_observation_dispositions_immutable_0016",
        REVIEW_IMMUTABLE_SIGNATURE,
        27,
    ),
    (
        "stocktake_observation_dispositions",
        "trg_stocktake_observation_dispositions_immutable_truncate_0016",
        REVIEW_IMMUTABLE_SIGNATURE,
        34,
    ),
)

CATALOG_ERROR = "0052 opening terminal guard catalog verification failed"
INSERT_GUARD_CATALOG_ERROR = (
    "0052 opening task insert guard catalog verification failed"
)
COUNT_WRITE_CATALOG_ERROR = (
    "0052 opening count write guard catalog verification failed"
)
GRAPH_CLOSURE_CATALOG_ERROR = (
    "0052 opening immutable graph closure catalog verification failed"
)
HELPER_CATALOG_ERROR = "0052 opening proof helper catalog verification failed"
CANONICAL_JSON_CATALOG_ERROR = (
    "0052 canonical JSON dependency catalog verification failed"
)
RECONCILIATION_DEPENDENCY_CATALOG_ERROR = (
    "0052 inherited reconciliation guard catalog verification failed"
)
REPLACEMENT_ERROR = "0052 opening terminal guard replacement failed"
EXISTING_ROWS_ERROR = (
    "0052 existing opening stocktake round binding is not canonical"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0052 while opening stocktake history or reserved "
    "evidence exists"
)


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0052 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_boundary_tables()
    _verify_canonical_json_catalog(phase="legacy upgrade preflight")
    _verify_reconciliation_dependency_catalog(
        phase="legacy upgrade preflight"
    )
    _verify_catalog(hardened=False, phase="legacy upgrade preflight")
    _verify_helper_catalog(present=False, phase="legacy upgrade preflight")
    _verify_insert_guard_catalog(present=False, phase="legacy upgrade preflight")
    _verify_count_write_guard_catalog(
        present=False,
        phase="legacy upgrade preflight",
    )
    _verify_graph_closure_catalog(
        present=False,
        phase="legacy upgrade preflight",
    )
    _create_opening_helpers()
    _verify_helper_catalog(present=True, phase="helper upgrade installation")
    _verify_existing_opening_rows()
    _verify_existing_graph_closure_rows()
    _create_opening_insert_guard()
    _create_opening_count_write_guard()
    _create_opening_graph_closure()
    _replace_function_body(
        signature=COMMIT_SIGNATURE,
        expected_body_sha256=LEGACY_COMMIT_BODY_SHA256,
        source_fragment=LEGACY_TASK_BRANCH,
        replacement_fragment=FIXED_TASK_BRANCH,
        phase="commit upgrade",
    )
    _replace_function_body(
        signature=ACCOUNT_SIGNATURE,
        expected_body_sha256=LEGACY_ACCOUNT_BODY_SHA256,
        source_fragment=LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT,
        replacement_fragment=FIXED_ACCOUNT_PRINCIPAL_FRAGMENT,
        phase="account upgrade",
    )
    _set_caller_security(security_definer=True)
    _set_review_completion_search_path(hardened=True)
    _verify_catalog(hardened=True, phase="hardened upgrade postflight")
    _verify_helper_catalog(present=True, phase="hardened upgrade postflight")
    _verify_insert_guard_catalog(present=True, phase="hardened upgrade postflight")
    _verify_count_write_guard_catalog(
        present=True,
        phase="hardened upgrade postflight",
    )
    _verify_graph_closure_catalog(
        present=True,
        phase="hardened upgrade postflight",
    )
    _verify_canonical_json_catalog(phase="hardened upgrade postflight")
    _verify_reconciliation_dependency_catalog(
        phase="hardened upgrade postflight"
    )
    _replace_oam_runtime_ready_function(revision)


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_boundary_tables()
    _verify_canonical_json_catalog(phase="hardened downgrade preflight")
    _verify_reconciliation_dependency_catalog(
        phase="hardened downgrade preflight"
    )
    _verify_catalog(hardened=True, phase="hardened downgrade preflight")
    _verify_helper_catalog(present=True, phase="hardened downgrade preflight")
    _verify_insert_guard_catalog(present=True, phase="hardened downgrade preflight")
    _verify_count_write_guard_catalog(
        present=True,
        phase="hardened downgrade preflight",
    )
    _verify_graph_closure_catalog(
        present=True,
        phase="hardened downgrade preflight",
    )
    _require_no_active_opening_task()
    _drop_opening_graph_closure()
    _drop_opening_count_write_guard()
    _drop_opening_insert_guard()
    _replace_function_body(
        signature=ACCOUNT_SIGNATURE,
        expected_body_sha256=FIXED_ACCOUNT_BODY_SHA256,
        source_fragment=FIXED_ACCOUNT_PRINCIPAL_FRAGMENT,
        replacement_fragment=LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT,
        phase="account downgrade",
    )
    _replace_function_body(
        signature=COMMIT_SIGNATURE,
        expected_body_sha256=FIXED_COMMIT_BODY_SHA256,
        source_fragment=FIXED_TASK_BRANCH,
        replacement_fragment=LEGACY_TASK_BRANCH,
        phase="commit downgrade",
    )
    _set_caller_security(security_definer=False)
    _set_review_completion_search_path(hardened=False)
    _drop_opening_helpers()
    _verify_catalog(hardened=False, phase="legacy downgrade postflight")
    _verify_helper_catalog(present=False, phase="legacy downgrade postflight")
    _verify_insert_guard_catalog(present=False, phase="legacy downgrade postflight")
    _verify_count_write_guard_catalog(
        present=False,
        phase="legacy downgrade postflight",
    )
    _verify_graph_closure_catalog(
        present=False,
        phase="legacy downgrade postflight",
    )
    _verify_canonical_json_catalog(phase="legacy downgrade postflight")
    _verify_reconciliation_dependency_catalog(
        phase="legacy downgrade postflight"
    )
    _replace_oam_runtime_ready_function(PREVIOUS_SCHEMA_REVISION)


def _lock_boundary_tables() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _set_review_completion_search_path(*, hardened: bool) -> None:
    if hardened:
        op.execute(
            f"ALTER FUNCTION {REVIEW_COMPLETION_SIGNATURE} "
            "SET search_path = pg_catalog, public"
        )
    else:
        op.execute(
            f"ALTER FUNCTION {REVIEW_COMPLETION_SIGNATURE} RESET search_path"
        )


def _create_opening_helpers() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{START_GRAPH_FUNCTION}(
    p_task_id uuid,
    p_historical boolean
)
RETURNS boolean
LANGUAGE sql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $rsc_0052_start_graph${START_GRAPH_BODY}$rsc_0052_start_graph$
"""
    )
    op.execute(
        f"ALTER FUNCTION {START_GRAPH_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {START_GRAPH_SIGNATURE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"""
CREATE FUNCTION public.{ROUND_SUBMISSION_FUNCTION}(
    p_task_id uuid,
    p_round_id uuid,
    p_historical boolean
)
RETURNS boolean
LANGUAGE sql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $rsc_0052_round_submission${ROUND_SUBMISSION_BODY}$rsc_0052_round_submission$
"""
    )
    op.execute(
        f"ALTER FUNCTION {ROUND_SUBMISSION_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {ROUND_SUBMISSION_SIGNATURE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"""
CREATE FUNCTION public.{SCOPE_COMPLETION_FUNCTION}(
    p_task_id uuid,
    p_round_id uuid,
    p_scope_id uuid,
    p_historical boolean
)
RETURNS boolean
LANGUAGE sql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $rsc_0052_scope_completion${SCOPE_COMPLETION_BODY}$rsc_0052_scope_completion$
"""
    )
    op.execute(
        f"ALTER FUNCTION {SCOPE_COMPLETION_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {SCOPE_COMPLETION_SIGNATURE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"""
CREATE FUNCTION public.{REVIEW_GRAPH_FUNCTION}(
    p_review_id uuid,
    p_historical boolean
)
RETURNS boolean
LANGUAGE sql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $rsc_0052_review_graph${REVIEW_GRAPH_BODY}$rsc_0052_review_graph$
"""
    )
    op.execute(
        f"ALTER FUNCTION {REVIEW_GRAPH_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {REVIEW_GRAPH_SIGNATURE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"""
CREATE FUNCTION public.{RECOUNT_GRAPH_FUNCTION}(
    p_recount_case_id uuid,
    p_historical boolean
)
RETURNS boolean
LANGUAGE sql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $rsc_0052_recount_graph${RECOUNT_GRAPH_BODY}$rsc_0052_recount_graph$
"""
    )
    op.execute(
        f"ALTER FUNCTION {RECOUNT_GRAPH_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {RECOUNT_GRAPH_SIGNATURE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"""
CREATE FUNCTION public.{DISPOSITION_GRAPH_FUNCTION}(
    p_disposition_id uuid,
    p_historical boolean
)
RETURNS boolean
LANGUAGE sql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $rsc_0052_disposition_graph${DISPOSITION_GRAPH_BODY}$rsc_0052_disposition_graph$
"""
    )
    op.execute(
        f"ALTER FUNCTION {DISPOSITION_GRAPH_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {DISPOSITION_GRAPH_SIGNATURE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"""
CREATE FUNCTION public.{TERMINAL_GRAPH_FUNCTION}(
    p_task_id uuid,
    p_historical boolean
)
RETURNS boolean
LANGUAGE sql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $rsc_0052_terminal_graph${TERMINAL_GRAPH_BODY}$rsc_0052_terminal_graph$
"""
    )
    op.execute(
        f"ALTER FUNCTION {TERMINAL_GRAPH_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {TERMINAL_GRAPH_SIGNATURE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )


def _drop_opening_helpers() -> None:
    op.execute(f"DROP FUNCTION {TERMINAL_GRAPH_SIGNATURE}")
    op.execute(f"DROP FUNCTION {DISPOSITION_GRAPH_SIGNATURE}")
    op.execute(f"DROP FUNCTION {RECOUNT_GRAPH_SIGNATURE}")
    op.execute(f"DROP FUNCTION {REVIEW_GRAPH_SIGNATURE}")
    op.execute(f"DROP FUNCTION {SCOPE_COMPLETION_SIGNATURE}")
    op.execute(f"DROP FUNCTION {ROUND_SUBMISSION_SIGNATURE}")
    op.execute(f"DROP FUNCTION {START_GRAPH_SIGNATURE}")


def _helper_catalog_values() -> str:
    rows = (
        (
            START_GRAPH_SIGNATURE,
            START_GRAPH_FUNCTION,
            2,
            "uuid, boolean",
            "p_task_id,p_historical",
            START_GRAPH_BODY_SHA256,
        ),
        (
            ROUND_SUBMISSION_SIGNATURE,
            ROUND_SUBMISSION_FUNCTION,
            3,
            "uuid, uuid, boolean",
            "p_task_id,p_round_id,p_historical",
            ROUND_SUBMISSION_BODY_SHA256,
        ),
        (
            SCOPE_COMPLETION_SIGNATURE,
            SCOPE_COMPLETION_FUNCTION,
            4,
            "uuid, uuid, uuid, boolean",
            "p_task_id,p_round_id,p_scope_id,p_historical",
            SCOPE_COMPLETION_BODY_SHA256,
        ),
        (
            REVIEW_GRAPH_SIGNATURE,
            REVIEW_GRAPH_FUNCTION,
            2,
            "uuid, boolean",
            "p_review_id,p_historical",
            REVIEW_GRAPH_BODY_SHA256,
        ),
        (
            RECOUNT_GRAPH_SIGNATURE,
            RECOUNT_GRAPH_FUNCTION,
            2,
            "uuid, boolean",
            "p_recount_case_id,p_historical",
            RECOUNT_GRAPH_BODY_SHA256,
        ),
        (
            DISPOSITION_GRAPH_SIGNATURE,
            DISPOSITION_GRAPH_FUNCTION,
            2,
            "uuid, boolean",
            "p_disposition_id,p_historical",
            DISPOSITION_GRAPH_BODY_SHA256,
        ),
        (
            TERMINAL_GRAPH_SIGNATURE,
            TERMINAL_GRAPH_FUNCTION,
            2,
            "uuid, boolean",
            "p_task_id,p_historical",
            TERMINAL_GRAPH_BODY_SHA256,
        ),
    )
    return ",\n        ".join(
        "("
        + ", ".join(
            (
                _sql_literal(signature),
                _sql_literal(function_name),
                str(argument_count),
                _sql_literal(argument_types),
                _sql_literal(argument_names),
                _sql_literal(body_sha256),
            )
        )
        + ")"
        for (
            signature,
            function_name,
            argument_count,
            argument_types,
            argument_names,
            body_sha256,
        ) in rows
    )


def _verify_helper_catalog(*, present: bool, phase: str) -> None:
    helper_values = _helper_catalog_values()
    escaped_phase = phase.replace("'", "''")
    expected_present = "TRUE" if present else "FALSE"
    op.execute(
        f"""
DO $rsc_0052_helper_catalog$
DECLARE
    api_oid oid;
    migrator_oid oid;
    helper_oid oid;
    expected_helper record;
BEGIN
    SELECT role_row.oid
      INTO migrator_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{MIGRATION_ROLE}';
    SELECT role_row.oid
      INTO api_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{PRODUCTION_API_ROLE}';
    IF migrator_oid IS NULL OR api_oid IS NULL THEN
        RAISE EXCEPTION
            '{HELPER_CATALOG_ERROR}: {escaped_phase}: required role is missing';
    END IF;

    FOR expected_helper IN
        SELECT *
          FROM (VALUES
        {helper_values}
          ) AS expected(
              signature,
              function_name,
              argument_count,
              argument_types,
              argument_names,
              body_sha256
          )
    LOOP
        helper_oid := pg_catalog.to_regprocedure(expected_helper.signature);
        IF NOT {expected_present} THEN
            IF helper_oid IS NOT NULL OR EXISTS (
                SELECT 1
                  FROM pg_catalog.pg_proc AS helper_row
                 WHERE helper_row.proname = expected_helper.function_name
            ) THEN
                RAISE EXCEPTION
                    '{HELPER_CATALOG_ERROR}: {escaped_phase}: unexpected helper alias';
            END IF;
            CONTINUE;
        END IF;

        IF helper_oid IS NULL OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_proc AS helper_row
             WHERE helper_row.proname = expected_helper.function_name
        ) <> 1 THEN
            RAISE EXCEPTION
                '{HELPER_CATALOG_ERROR}: {escaped_phase}: helper identity mismatch';
        END IF;
        IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS helper_row
              JOIN pg_catalog.pg_language AS language_row
                ON language_row.oid = helper_row.prolang
             WHERE helper_row.oid = helper_oid
               AND helper_row.proowner = migrator_oid
               AND helper_row.prokind = 'f'
               AND helper_row.prorettype = pg_catalog.to_regtype('boolean')
               AND NOT helper_row.proretset
               AND helper_row.pronargs = expected_helper.argument_count
               AND pg_catalog.oidvectortypes(helper_row.proargtypes) =
                   expected_helper.argument_types
               AND pg_catalog.array_to_string(
                       helper_row.proargnames,
                       ','
                   ) = expected_helper.argument_names
               AND helper_row.proallargtypes IS NULL
               AND helper_row.proargmodes IS NULL
               AND helper_row.pronargdefaults = 0
               AND helper_row.proargdefaults IS NULL
               AND helper_row.provariadic = 0
               AND language_row.lanname = 'sql'
               AND helper_row.provolatile = 'v'
               AND NOT helper_row.proisstrict
               AND NOT helper_row.proleakproof
               AND helper_row.proparallel = 'u'
               AND NOT helper_row.prosecdef
               AND helper_row.proconfig = ARRAY['{FIXED_SEARCH_PATH}']::text[]
               AND pg_catalog.encode(
                       pg_catalog.sha256(
                           pg_catalog.convert_to(helper_row.prosrc, 'UTF8')
                       ),
                       'hex'
                   ) = expected_helper.body_sha256
        ) THEN
            RAISE EXCEPTION
                '{HELPER_CATALOG_ERROR}: {escaped_phase}: helper definition mismatch';
        END IF;
        IF EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS helper_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     helper_row.proacl,
                     pg_catalog.acldefault('f', helper_row.proowner)
                 )
             ) AS helper_acl
             WHERE helper_row.oid = helper_oid
               AND (
                   helper_acl.privilege_type <> 'EXECUTE'
                   OR helper_acl.grantee <> migrator_oid
                   OR helper_acl.grantor <> migrator_oid
                   OR helper_acl.is_grantable
               )
        ) OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_proc AS helper_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     helper_row.proacl,
                     pg_catalog.acldefault('f', helper_row.proowner)
                 )
             ) AS helper_acl
             WHERE helper_row.oid = helper_oid
               AND helper_acl.privilege_type = 'EXECUTE'
               AND helper_acl.grantee = migrator_oid
               AND helper_acl.grantor = migrator_oid
               AND NOT helper_acl.is_grantable
        ) <> 1 OR pg_catalog.has_function_privilege(
            api_oid,
            helper_oid,
            'EXECUTE'
        ) THEN
            RAISE EXCEPTION
                '{HELPER_CATALOG_ERROR}: {escaped_phase}: helper ACL mismatch';
        END IF;
    END LOOP;
END
$rsc_0052_helper_catalog$
"""
    )


def _verify_canonical_json_catalog(*, phase: str) -> None:
    """Pin the recursive 0026 renderer used for every audit hash proof."""

    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0052_canonical_json_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    function_oid oid := pg_catalog.to_regprocedure(
        '{CANONICAL_JSON_SIGNATURE}'
    );
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL
       OR migrator_oid IS NULL
       OR function_oid IS NULL
       OR (
           SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.proname = '{CANONICAL_JSON_FUNCTION}'
       ) <> 1 THEN
        RAISE EXCEPTION
            '{CANONICAL_JSON_CATALOG_ERROR}: {escaped_phase}: identity mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS namespace_row
            ON namespace_row.oid = function_row.pronamespace
          JOIN pg_catalog.pg_language AS language_row
            ON language_row.oid = function_row.prolang
         WHERE function_row.oid = function_oid
           AND namespace_row.nspname = 'public'
           AND function_row.proowner = migrator_oid
           AND function_row.prokind = 'f'
           AND function_row.prorettype = 'text'::pg_catalog.regtype
           AND NOT function_row.proretset
           AND function_row.pronargs = 1
           AND pg_catalog.oidvectortypes(function_row.proargtypes) = 'jsonb'
           AND pg_catalog.array_to_string(
                   function_row.proargnames,
                   ','
               ) = 'document'
           AND function_row.proallargtypes IS NULL
           AND function_row.proargmodes IS NULL
           AND function_row.pronargdefaults = 0
           AND function_row.proargdefaults IS NULL
           AND function_row.provariadic = 0
           AND language_row.lanname = 'plpgsql'
           AND function_row.provolatile = 'i'
           AND function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND NOT function_row.prosecdef
           AND function_row.proconfig =
               ARRAY['{FIXED_SEARCH_PATH}']::text[]
           AND pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                   ),
                   'hex'
               ) = '{CANONICAL_JSON_BODY_SHA256}'
    ) THEN
        RAISE EXCEPTION
            '{CANONICAL_JSON_CATALOG_ERROR}: {escaped_phase}: definition mismatch';
    END IF;
    IF NOT pg_catalog.has_function_privilege(
           api_oid,
           function_oid,
           'EXECUTE'
       ) OR EXISTS (
           SELECT 1
             FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_row.proacl,
                    pg_catalog.acldefault('f', function_row.proowner)
                )
            ) AS function_acl
            WHERE function_row.oid = function_oid
              AND (
                  function_acl.privilege_type <> 'EXECUTE'
                  OR function_acl.grantor <> migrator_oid
                  OR function_acl.grantee NOT IN (migrator_oid, api_oid)
                  OR function_acl.is_grantable
              )
       ) OR (
           SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_row.proacl,
                    pg_catalog.acldefault('f', function_row.proowner)
                )
            ) AS function_acl
            WHERE function_row.oid = function_oid
              AND function_acl.privilege_type = 'EXECUTE'
              AND function_acl.grantor = migrator_oid
              AND function_acl.grantee IN (migrator_oid, api_oid)
              AND NOT function_acl.is_grantable
       ) <> 2 OR EXISTS (
           SELECT 1
             FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_row.proacl,
                    pg_catalog.acldefault('f', function_row.proowner)
                )
            ) AS function_acl
            WHERE function_row.oid = function_oid
              AND function_acl.grantee = 0
       ) THEN
        RAISE EXCEPTION
            '{CANONICAL_JSON_CATALOG_ERROR}: {escaped_phase}: ACL mismatch';
    END IF;
END
$rsc_0052_canonical_json_catalog$
"""
    )


def _reconciliation_dependency_catalog_values() -> str:
    """Render the non-canonical inherited function rows for SQL verification."""

    return ",\n        ".join(
        "("
        + ", ".join(
            (
                _sql_literal(signature),
                _sql_literal(function_name),
                _sql_literal(return_type),
                _sql_literal(language),
                _sql_literal(volatility),
                str(len(argument_types)),
                _sql_literal(", ".join(argument_types)),
                (
                    _sql_literal(",".join(argument_names))
                    if argument_names
                    else "NULL::text"
                ),
                "TRUE" if security_definer else "FALSE",
                "TRUE" if is_strict else "FALSE",
                _sql_literal(body_sha256),
                "TRUE" if api_execute else "FALSE",
            )
        )
        + ")"
        for (
            signature,
            function_name,
            return_type,
            language,
            volatility,
            argument_types,
            argument_names,
            security_definer,
            is_strict,
            _search_path,
            body_sha256,
            api_execute,
        ) in INHERITED_RECONCILIATION_FUNCTION_CATALOG[1:]
    )


def _verify_reconciliation_dependency_catalog(*, phase: str) -> None:
    """Pin the 0026 event-key/effect function and all delegated triggers."""

    escaped_phase = phase.replace("'", "''")
    function_values = _reconciliation_dependency_catalog_values()
    trigger_values = ",\n        ".join(
        "("
        + ", ".join(
            (
                _sql_literal(table_name),
                _sql_literal(trigger_name),
                str(trigger_type),
            )
        )
        + ")"
        for table_name, trigger_name, _, trigger_type
        in INHERITED_RECONCILIATION_TRIGGER_CATALOG
    )
    op.execute(
        f"""
DO $rsc_0052_reconciliation_dependency_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    expected_function record;
    expected_trigger record;
    function_oid oid;
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL
       OR migrator_oid IS NULL THEN
        RAISE EXCEPTION
            '{RECONCILIATION_DEPENDENCY_CATALOG_ERROR}: '
            '{escaped_phase}: required role mismatch';
    END IF;

    FOR expected_function IN
        SELECT *
          FROM (VALUES
        {function_values}
          ) AS expected(
              signature,
              function_name,
              return_type,
              language_name,
              volatility,
              argument_count,
              argument_types,
              argument_names,
              security_definer,
              is_strict,
              body_sha256,
              api_execute
          )
    LOOP
        function_oid := pg_catalog.to_regprocedure(
            expected_function.signature
        );
        IF function_oid IS NULL OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_proc AS function_row
             WHERE function_row.proname = expected_function.function_name
        ) <> 1 OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
              JOIN pg_catalog.pg_namespace AS namespace_row
                ON namespace_row.oid = function_row.pronamespace
              JOIN pg_catalog.pg_language AS language_row
                ON language_row.oid = function_row.prolang
             WHERE function_row.oid = function_oid
               AND namespace_row.nspname = 'public'
               AND function_row.proowner = migrator_oid
               AND function_row.prokind = 'f'
               AND function_row.prorettype = pg_catalog.to_regtype(
                   expected_function.return_type
               )
               AND NOT function_row.proretset
               AND function_row.pronargs = expected_function.argument_count
               AND pg_catalog.oidvectortypes(function_row.proargtypes) =
                   expected_function.argument_types
               AND (
                   (
                       expected_function.argument_names IS NULL
                       AND function_row.proargnames IS NULL
                   )
                   OR pg_catalog.array_to_string(
                       function_row.proargnames,
                       ','
                   ) = expected_function.argument_names
               )
               AND function_row.proallargtypes IS NULL
               AND function_row.proargmodes IS NULL
               AND function_row.pronargdefaults = 0
               AND function_row.proargdefaults IS NULL
               AND function_row.provariadic = 0
               AND language_row.lanname = expected_function.language_name
               AND function_row.provolatile = expected_function.volatility
               AND function_row.proisstrict = expected_function.is_strict
               AND NOT function_row.proleakproof
               AND function_row.proparallel = 'u'
               AND function_row.prosecdef =
                   expected_function.security_definer
               AND function_row.proconfig =
                   ARRAY['{FIXED_SEARCH_PATH}']::text[]
               AND pg_catalog.encode(
                       pg_catalog.sha256(
                           pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                       ),
                       'hex'
                   ) = expected_function.body_sha256
        ) THEN
            RAISE EXCEPTION
                '{RECONCILIATION_DEPENDENCY_CATALOG_ERROR}: '
                '{escaped_phase}: function mismatch';
        END IF;
        IF pg_catalog.has_function_privilege(
               api_oid,
               function_oid,
               'EXECUTE'
           ) IS DISTINCT FROM expected_function.api_execute
           OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(
                        function_row.proacl,
                        pg_catalog.acldefault('f', function_row.proowner)
                    )
                ) AS function_acl
                WHERE function_row.oid = function_oid
                  AND (
                      function_acl.privilege_type <> 'EXECUTE'
                      OR function_acl.grantor <> migrator_oid
                      OR function_acl.is_grantable
                      OR NOT (
                          function_acl.grantee = migrator_oid
                          OR (
                              expected_function.api_execute
                              AND function_acl.grantee = api_oid
                          )
                      )
                  )
           ) OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_proc AS function_row
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(
                        function_row.proacl,
                        pg_catalog.acldefault('f', function_row.proowner)
                    )
                ) AS function_acl
                WHERE function_row.oid = function_oid
                  AND function_acl.privilege_type = 'EXECUTE'
                  AND function_acl.grantor = migrator_oid
                  AND NOT function_acl.is_grantable
                  AND (
                      function_acl.grantee = migrator_oid
                      OR (
                          expected_function.api_execute
                          AND function_acl.grantee = api_oid
                      )
                  )
           ) <> (
               1 + CASE
                   WHEN expected_function.api_execute THEN 1 ELSE 0
               END
           ) THEN
            RAISE EXCEPTION
                '{RECONCILIATION_DEPENDENCY_CATALOG_ERROR}: '
                '{escaped_phase}: function ACL mismatch';
        END IF;
    END LOOP;

    function_oid := pg_catalog.to_regprocedure(
        '{RECONCILIATION_EFFECT_SIGNATURE}'
    );
    FOR expected_trigger IN
        SELECT *
          FROM (VALUES
        {trigger_values}
          ) AS expected(table_name, trigger_name, trigger_type)
    LOOP
        IF (SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_trigger AS trigger_row
              JOIN pg_catalog.pg_class AS table_row
                ON table_row.oid = trigger_row.tgrelid
              JOIN pg_catalog.pg_namespace AS namespace_row
                ON namespace_row.oid = table_row.relnamespace
             WHERE NOT trigger_row.tgisinternal
               AND trigger_row.tgname = expected_trigger.trigger_name
               AND table_row.relname = expected_trigger.table_name
               AND namespace_row.nspname = 'public'
               AND trigger_row.tgfoid = function_oid
               AND trigger_row.tgtype = expected_trigger.trigger_type
               AND trigger_row.tgenabled = 'A'
               AND trigger_row.tgconstraint = 0
               AND NOT trigger_row.tgdeferrable
               AND NOT trigger_row.tginitdeferred
               AND trigger_row.tgconstrrelid = 0
               AND trigger_row.tgconstrindid = 0
               AND trigger_row.tgparentid = 0
               AND trigger_row.tgqual IS NULL
               AND trigger_row.tgoldtable IS NULL
               AND trigger_row.tgnewtable IS NULL
               AND trigger_row.tgnargs = 0
               AND trigger_row.tgattr = ''::pg_catalog.int2vector) <> 1
           OR (SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_trigger AS trigger_row
                WHERE NOT trigger_row.tgisinternal
                  AND trigger_row.tgname = expected_trigger.trigger_name) <> 1
        THEN
            RAISE EXCEPTION
                '{RECONCILIATION_DEPENDENCY_CATALOG_ERROR}: '
                '{escaped_phase}: trigger mismatch';
        END IF;
    END LOOP;
    IF (SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid = function_oid) <>
       {len(INHERITED_RECONCILIATION_TRIGGER_CATALOG)} THEN
        RAISE EXCEPTION
            '{RECONCILIATION_DEPENDENCY_CATALOG_ERROR}: '
            '{escaped_phase}: trigger alias mismatch';
    END IF;
END;
$rsc_0052_reconciliation_dependency_catalog$
"""
    )


def _create_opening_insert_guard() -> None:
    op.execute(
        f"""
CREATE FUNCTION {INSERT_GUARD_SIGNATURE}
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $rsc_0052_insert_guard${INSERT_GUARD_BODY}$rsc_0052_insert_guard$
"""
    )
    op.execute(
        f"ALTER FUNCTION {INSERT_GUARD_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {INSERT_GUARD_SIGNATURE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"""
CREATE CONSTRAINT TRIGGER {INSERT_GUARD_TRIGGER}
AFTER INSERT ON public.stocktake_tasks
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION {INSERT_GUARD_SIGNATURE}
"""
    )
    op.execute(
        "ALTER TABLE public.stocktake_tasks ENABLE ALWAYS TRIGGER "
        f"{INSERT_GUARD_TRIGGER}"
    )


def _drop_opening_insert_guard() -> None:
    op.execute(
        f"DROP TRIGGER {INSERT_GUARD_TRIGGER} ON public.stocktake_tasks"
    )
    op.execute(f"DROP FUNCTION {INSERT_GUARD_SIGNATURE}")


def _verify_insert_guard_catalog(*, present: bool, phase: str) -> None:
    escaped_phase = phase.replace("'", "''")
    if not present:
        op.execute(
            f"""
DO $rsc_0052_insert_catalog_absent$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.proname = '{INSERT_GUARD_FUNCTION}'
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgname = '{INSERT_GUARD_TRIGGER}'
    ) THEN
        RAISE EXCEPTION
            '{INSERT_GUARD_CATALOG_ERROR}: {escaped_phase}: unexpected object';
    END IF;
END
$rsc_0052_insert_catalog_absent$
"""
        )
        return

    op.execute(
        f"""
DO $rsc_0052_insert_catalog$
DECLARE
    api_oid oid;
    function_oid oid;
    migrator_oid oid;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{INSERT_GUARD_CATALOG_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;
    SELECT role_row.oid
      INTO migrator_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{MIGRATION_ROLE}';
    SELECT role_row.oid
      INTO api_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{PRODUCTION_API_ROLE}';
    function_oid := pg_catalog.to_regprocedure('{INSERT_GUARD_SIGNATURE}');
    IF migrator_oid IS NULL OR api_oid IS NULL OR function_oid IS NULL
       OR (
           SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.proname = '{INSERT_GUARD_FUNCTION}'
       ) <> 1 THEN
        RAISE EXCEPTION
            '{INSERT_GUARD_CATALOG_ERROR}: {escaped_phase}: function identity mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = function_row.pronamespace
          JOIN pg_catalog.pg_language AS language_row
            ON language_row.oid = function_row.prolang
         WHERE function_row.oid = function_oid
           AND schema_row.nspname = 'public'
           AND function_row.proowner = migrator_oid
           AND function_row.prokind = 'f'
           AND function_row.prorettype = pg_catalog.to_regtype('trigger')
           AND NOT function_row.proretset
           AND function_row.pronargs = 0
           AND pg_catalog.oidvectortypes(function_row.proargtypes) = ''
           AND function_row.proargnames IS NULL
           AND function_row.proallargtypes IS NULL
           AND function_row.proargmodes IS NULL
           AND function_row.pronargdefaults = 0
           AND function_row.proargdefaults IS NULL
           AND function_row.provariadic = 0
           AND language_row.lanname = 'plpgsql'
           AND function_row.provolatile = 'v'
           AND NOT function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND function_row.prosecdef
           AND function_row.proconfig = ARRAY['{FIXED_SEARCH_PATH}']::text[]
           AND pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                   ),
                   'hex'
               ) = '{INSERT_GUARD_BODY_SHA256}'
    ) THEN
        RAISE EXCEPTION
            '{INSERT_GUARD_CATALOG_ERROR}: {escaped_phase}: function definition mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
             COALESCE(
                 function_row.proacl,
                 pg_catalog.acldefault('f', function_row.proowner)
             )
         ) AS function_acl
         WHERE function_row.oid = function_oid
           AND (
               function_acl.privilege_type <> 'EXECUTE'
               OR function_acl.grantee <> migrator_oid
               OR function_acl.grantor <> migrator_oid
               OR function_acl.is_grantable
           )
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
             COALESCE(
                 function_row.proacl,
                 pg_catalog.acldefault('f', function_row.proowner)
             )
         ) AS function_acl
         WHERE function_row.oid = function_oid
           AND function_acl.privilege_type = 'EXECUTE'
           AND function_acl.grantee = migrator_oid
           AND function_acl.grantor = migrator_oid
           AND NOT function_acl.is_grantable
    ) <> 1 OR pg_catalog.has_function_privilege(
        api_oid,
        function_oid,
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION
            '{INSERT_GUARD_CATALOG_ERROR}: {escaped_phase}: function ACL mismatch';
    END IF;
    IF (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgname = '{INSERT_GUARD_TRIGGER}'
           AND trigger_row.tgrelid =
               'public.stocktake_tasks'::pg_catalog.regclass
           AND trigger_row.tgfoid = function_oid
           AND trigger_row.tgenabled = 'A'
           AND trigger_row.tgtype = 5
           AND trigger_row.tgconstraint <> 0
           AND trigger_row.tgdeferrable
           AND trigger_row.tginitdeferred
           AND trigger_row.tgconstrrelid = 0
           AND trigger_row.tgconstrindid = 0
           AND trigger_row.tgparentid = 0
           AND trigger_row.tgqual IS NULL
           AND trigger_row.tgoldtable IS NULL
           AND trigger_row.tgnewtable IS NULL
           AND trigger_row.tgnargs = 0
           AND trigger_row.tgattr = ''::pg_catalog.int2vector
    ) <> 1 OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND (
               trigger_row.tgname = '{INSERT_GUARD_TRIGGER}'
               OR trigger_row.tgfoid = function_oid
           )
    ) <> 1 THEN
        RAISE EXCEPTION
            '{INSERT_GUARD_CATALOG_ERROR}: {escaped_phase}: trigger binding mismatch';
    END IF;
END
$rsc_0052_insert_catalog$
"""
    )


def _create_opening_count_write_guard() -> None:
    op.execute(
        f"""
CREATE FUNCTION {COUNT_WRITE_SIGNATURE}
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $rsc_0052_count_write${COUNT_WRITE_BODY}$rsc_0052_count_write$
"""
    )
    op.execute(
        f"ALTER FUNCTION {COUNT_WRITE_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {COUNT_WRITE_SIGNATURE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    for table_name, trigger_name in COUNT_WRITE_TRIGGER_CATALOG:
        op.execute(
            f"""
CREATE TRIGGER {trigger_name}
BEFORE INSERT ON public.{table_name}
FOR EACH ROW
EXECUTE FUNCTION {COUNT_WRITE_SIGNATURE}
"""
        )


def _drop_opening_count_write_guard() -> None:
    for table_name, trigger_name in reversed(COUNT_WRITE_TRIGGER_CATALOG):
        op.execute(f"DROP TRIGGER {trigger_name} ON public.{table_name}")
    op.execute(f"DROP FUNCTION {COUNT_WRITE_SIGNATURE}")


def _create_opening_graph_closure() -> None:
    op.execute(
        f"""
CREATE FUNCTION {GRAPH_CLOSURE_SIGNATURE}
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $rsc_0052_graph_closure${GRAPH_CLOSURE_BODY}$rsc_0052_graph_closure$
"""
    )
    op.execute(
        f"ALTER FUNCTION {GRAPH_CLOSURE_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {GRAPH_CLOSURE_SIGNATURE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    for table_name, trigger_name, operations in GRAPH_CLOSURE_TRIGGER_CATALOG:
        op.execute(
            f"""
CREATE CONSTRAINT TRIGGER {trigger_name}
AFTER {operations} ON public.{table_name}
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION {GRAPH_CLOSURE_SIGNATURE}
"""
        )
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
            f"{trigger_name}"
        )


def _drop_opening_graph_closure() -> None:
    for table_name, trigger_name, _ in reversed(
        GRAPH_CLOSURE_TRIGGER_CATALOG
    ):
        op.execute(f"DROP TRIGGER {trigger_name} ON public.{table_name}")
    op.execute(f"DROP FUNCTION {GRAPH_CLOSURE_SIGNATURE}")


def _verify_count_write_guard_catalog(*, present: bool, phase: str) -> None:
    escaped_phase = phase.replace("'", "''")
    trigger_values = ",\n                    ".join(
        f"('{table_name}', '{trigger_name}')"
        for table_name, trigger_name in COUNT_WRITE_TRIGGER_CATALOG
    )
    if not present:
        trigger_names = ", ".join(
            _sql_literal(trigger_name)
            for _, trigger_name in COUNT_WRITE_TRIGGER_CATALOG
        )
        op.execute(
            f"""
DO $rsc_0052_count_write_absent$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.proname = '{COUNT_WRITE_FUNCTION}'
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE trigger_row.tgname IN ({trigger_names})
           AND NOT trigger_row.tgisinternal
    ) THEN
        RAISE EXCEPTION
            '{COUNT_WRITE_CATALOG_ERROR}: {escaped_phase}: unexpected object';
    END IF;
END
$rsc_0052_count_write_absent$
"""
        )
        return

    op.execute(
        f"""
DO $rsc_0052_count_write_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    function_oid oid := pg_catalog.to_regprocedure('{COUNT_WRITE_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF api_oid IS NULL OR migrator_oid IS NULL OR function_oid IS NULL THEN
        RAISE EXCEPTION
            '{COUNT_WRITE_CATALOG_ERROR}: {escaped_phase}: identity mismatch';
    END IF;
    IF (SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.proname = '{COUNT_WRITE_FUNCTION}') <> 1
       OR NOT EXISTS (
           SELECT 1
             FROM pg_catalog.pg_proc AS function_row
             JOIN pg_catalog.pg_namespace AS namespace_row
               ON namespace_row.oid = function_row.pronamespace
             JOIN pg_catalog.pg_language AS language_row
               ON language_row.oid = function_row.prolang
            WHERE function_row.oid = function_oid
              AND namespace_row.nspname = 'public'
              AND function_row.proowner = migrator_oid
              AND function_row.prokind = 'f'
              AND NOT function_row.proretset
              AND function_row.prorettype = 'trigger'::pg_catalog.regtype
              AND language_row.lanname = 'plpgsql'
              AND function_row.provolatile = 'v'
              AND function_row.pronargs = 0
              AND function_row.pronargdefaults = 0
              AND function_row.proargdefaults IS NULL
              AND function_row.provariadic = 0
              AND function_row.proallargtypes IS NULL
              AND function_row.proargmodes IS NULL
              AND function_row.proargnames IS NULL
              AND NOT function_row.proisstrict
              AND NOT function_row.proleakproof
              AND function_row.proparallel = 'u'
              AND function_row.prosecdef
              AND function_row.proconfig =
                  ARRAY['{FIXED_SEARCH_PATH}']::text[]
              AND pg_catalog.encode(
                      pg_catalog.sha256(
                          pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                      ),
                      'hex'
                  ) = '{COUNT_WRITE_BODY_SHA256}'
       ) THEN
        RAISE EXCEPTION
            '{COUNT_WRITE_CATALOG_ERROR}: {escaped_phase}: function mismatch';
    END IF;
    IF pg_catalog.has_function_privilege(
           api_oid,
           function_oid,
           'EXECUTE'
       ) OR EXISTS (
           SELECT 1
             FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_row.proacl,
                    pg_catalog.acldefault('f', function_row.proowner)
                )
            ) AS function_acl
            WHERE function_row.oid = function_oid
              AND (
                  function_acl.privilege_type <> 'EXECUTE'
                  OR function_acl.grantee <> migrator_oid
                  OR function_acl.grantor <> migrator_oid
                  OR function_acl.is_grantable
              )
       ) OR (
           SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_row.proacl,
                    pg_catalog.acldefault('f', function_row.proowner)
                )
            ) AS function_acl
            WHERE function_row.oid = function_oid
              AND function_acl.privilege_type = 'EXECUTE'
              AND function_acl.grantee = migrator_oid
              AND function_acl.grantor = migrator_oid
              AND NOT function_acl.is_grantable
       ) <> 1 THEN
        RAISE EXCEPTION
            '{COUNT_WRITE_CATALOG_ERROR}: {escaped_phase}: ACL mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES
                    {trigger_values}
               ) AS expected_trigger(table_name, trigger_name)
         WHERE (
             SELECT pg_catalog.count(*)
               FROM pg_catalog.pg_trigger AS trigger_row
               JOIN pg_catalog.pg_class AS table_row
                 ON table_row.oid = trigger_row.tgrelid
               JOIN pg_catalog.pg_namespace AS namespace_row
                 ON namespace_row.oid = table_row.relnamespace
              WHERE trigger_row.tgname = expected_trigger.trigger_name
                AND table_row.relname = expected_trigger.table_name
                AND namespace_row.nspname = 'public'
                AND NOT trigger_row.tgisinternal
                AND trigger_row.tgfoid = function_oid
                AND trigger_row.tgtype = 7
                AND trigger_row.tgenabled = 'O'
                AND trigger_row.tgconstraint = 0
                AND NOT trigger_row.tgdeferrable
                AND NOT trigger_row.tginitdeferred
                AND trigger_row.tgconstrrelid = 0
                AND trigger_row.tgconstrindid = 0
                AND trigger_row.tgparentid = 0
                AND trigger_row.tgqual IS NULL
                AND trigger_row.tgoldtable IS NULL
                AND trigger_row.tgnewtable IS NULL
                AND trigger_row.tgnargs = 0
                AND trigger_row.tgattr = ''::pg_catalog.int2vector
         ) <> 1
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE trigger_row.tgname IN (
             SELECT expected_trigger.trigger_name
               FROM (VALUES
                        {trigger_values}
                    ) AS expected_trigger(table_name, trigger_name)
         )
           AND NOT trigger_row.tgisinternal
    ) <> {len(COUNT_WRITE_TRIGGER_CATALOG)} OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid = function_oid
    ) <> {len(COUNT_WRITE_TRIGGER_CATALOG)} THEN
        RAISE EXCEPTION
            '{COUNT_WRITE_CATALOG_ERROR}: {escaped_phase}: trigger mismatch';
    END IF;
END
$rsc_0052_count_write_catalog$
"""
    )


def _verify_graph_closure_catalog(*, present: bool, phase: str) -> None:
    escaped_phase = phase.replace("'", "''")
    trigger_values = ",\n                    ".join(
        "(" + ", ".join(
            (
                _sql_literal(table_name),
                _sql_literal(trigger_name),
                "21" if operations == "INSERT OR UPDATE" else "5",
            )
        ) + ")"
        for table_name, trigger_name, operations
        in GRAPH_CLOSURE_TRIGGER_CATALOG
    )
    trigger_names = ", ".join(
        _sql_literal(trigger_name)
        for _, trigger_name, _ in GRAPH_CLOSURE_TRIGGER_CATALOG
    )
    expected_present = "TRUE" if present else "FALSE"
    op.execute(
        f"""
DO $rsc_0052_graph_closure_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    function_oid oid := pg_catalog.to_regprocedure('{GRAPH_CLOSURE_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF NOT {expected_present} THEN
        IF function_oid IS NOT NULL OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
             WHERE function_row.proname = '{GRAPH_CLOSURE_FUNCTION}'
        ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_trigger AS trigger_row
             WHERE NOT trigger_row.tgisinternal
               AND trigger_row.tgname IN ({trigger_names})
        ) THEN
            RAISE EXCEPTION
                '{GRAPH_CLOSURE_CATALOG_ERROR}: {escaped_phase}: unexpected object';
        END IF;
        RETURN;
    END IF;

    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR migrator_oid IS NULL OR function_oid IS NULL
       OR (SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.proname = '{GRAPH_CLOSURE_FUNCTION}') <> 1 THEN
        RAISE EXCEPTION
            '{GRAPH_CLOSURE_CATALOG_ERROR}: {escaped_phase}: identity mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS namespace_row
            ON namespace_row.oid = function_row.pronamespace
          JOIN pg_catalog.pg_language AS language_row
            ON language_row.oid = function_row.prolang
         WHERE function_row.oid = function_oid
           AND namespace_row.nspname = 'public'
           AND function_row.proowner = migrator_oid
           AND function_row.prokind = 'f'
           AND function_row.prorettype = 'trigger'::pg_catalog.regtype
           AND NOT function_row.proretset
           AND function_row.pronargs = 0
           AND pg_catalog.oidvectortypes(function_row.proargtypes) = ''
           AND function_row.proargnames IS NULL
           AND function_row.proallargtypes IS NULL
           AND function_row.proargmodes IS NULL
           AND function_row.pronargdefaults = 0
           AND function_row.proargdefaults IS NULL
           AND function_row.provariadic = 0
           AND language_row.lanname = 'plpgsql'
           AND function_row.provolatile = 'v'
           AND NOT function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND function_row.prosecdef
           AND function_row.proconfig = ARRAY['{FIXED_SEARCH_PATH}']::text[]
           AND pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                   ),
                   'hex'
               ) = '{GRAPH_CLOSURE_BODY_SHA256}'
    ) THEN
        RAISE EXCEPTION
            '{GRAPH_CLOSURE_CATALOG_ERROR}: {escaped_phase}: function mismatch';
    END IF;
    IF pg_catalog.has_function_privilege(api_oid, function_oid, 'EXECUTE')
       OR EXISTS (
           SELECT 1
             FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_row.proacl,
                    pg_catalog.acldefault('f', function_row.proowner)
                )
            ) AS function_acl
            WHERE function_row.oid = function_oid
              AND (
                  function_acl.privilege_type <> 'EXECUTE'
                  OR function_acl.grantee <> migrator_oid
                  OR function_acl.grantor <> migrator_oid
                  OR function_acl.is_grantable
              )
       ) OR (
           SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_row.proacl,
                    pg_catalog.acldefault('f', function_row.proowner)
                )
            ) AS function_acl
            WHERE function_row.oid = function_oid
              AND function_acl.privilege_type = 'EXECUTE'
              AND function_acl.grantee = migrator_oid
              AND function_acl.grantor = migrator_oid
              AND NOT function_acl.is_grantable
       ) <> 1 THEN
        RAISE EXCEPTION
            '{GRAPH_CLOSURE_CATALOG_ERROR}: {escaped_phase}: ACL mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES
                    {trigger_values}
               ) AS expected_trigger(table_name, trigger_name, trigger_type)
         WHERE (SELECT pg_catalog.count(*)
                  FROM pg_catalog.pg_trigger AS trigger_row
                  JOIN pg_catalog.pg_class AS table_row
                    ON table_row.oid = trigger_row.tgrelid
                  JOIN pg_catalog.pg_namespace AS namespace_row
                    ON namespace_row.oid = table_row.relnamespace
                 WHERE NOT trigger_row.tgisinternal
                   AND trigger_row.tgname = expected_trigger.trigger_name
                   AND table_row.relname = expected_trigger.table_name
                   AND namespace_row.nspname = 'public'
                   AND trigger_row.tgfoid = function_oid
                   AND trigger_row.tgenabled = 'A'
                   AND trigger_row.tgtype = expected_trigger.trigger_type
                   AND trigger_row.tgconstraint <> 0
                   AND trigger_row.tgdeferrable
                   AND trigger_row.tginitdeferred
                   AND trigger_row.tgconstrrelid = 0
                   AND trigger_row.tgconstrindid = 0
                   AND trigger_row.tgparentid = 0
                   AND trigger_row.tgqual IS NULL
                   AND trigger_row.tgoldtable IS NULL
                   AND trigger_row.tgnewtable IS NULL
                   AND trigger_row.tgnargs = 0
                   AND trigger_row.tgattr = ''::pg_catalog.int2vector) <> 1
            OR (SELECT pg_catalog.count(*)
                  FROM pg_catalog.pg_trigger AS trigger_row
                 WHERE NOT trigger_row.tgisinternal
                   AND trigger_row.tgname = expected_trigger.trigger_name) <> 1
    ) OR (SELECT pg_catalog.count(*)
            FROM pg_catalog.pg_trigger AS trigger_row
           WHERE NOT trigger_row.tgisinternal
             AND trigger_row.tgfoid = function_oid) <>
             {len(GRAPH_CLOSURE_TRIGGER_CATALOG)} THEN
        RAISE EXCEPTION
            '{GRAPH_CLOSURE_CATALOG_ERROR}: {escaped_phase}: trigger mismatch';
    END IF;
END
$rsc_0052_graph_closure_catalog$
"""
    )


def _require_no_active_opening_task() -> None:
    op.execute(
        f"""
DO $rsc_0052_downgrade$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS task
         WHERE task.task_type = 'opening'
    ) OR EXISTS (
        SELECT 1
          FROM public.state_transition_events AS state_event
         WHERE pg_catalog.left(state_event.reason, 8) = 'opening_'
            OR (
                pg_catalog.left(state_event.idempotency_key, 8) = 'opening-'
                AND pg_catalog.left(state_event.idempotency_key, 23) <>
                    'opening-reconciliation-'
            )
    ) OR EXISTS (
        SELECT 1
          FROM public.outbox_events AS outbox_event
         WHERE pg_catalog.left(outbox_event.event_type, 18) =
               'stocktake.opening.'
            OR (
                pg_catalog.left(outbox_event.idempotency_key, 8) = 'opening-'
                AND pg_catalog.left(outbox_event.idempotency_key, 23) <>
                    'opening-reconciliation-'
            )
    ) OR EXISTS (
        SELECT 1
          FROM public.audit_events AS audit_event
         WHERE pg_catalog.left(audit_event.action, 18) =
               'stocktake.opening.'
    ) OR EXISTS (
        SELECT 1
          FROM public.inventory_transactions AS inventory_transaction
         WHERE inventory_transaction.movement_type = 'opening'
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_postings AS posting
         WHERE posting.posting_kind = 'opening'
    ) OR EXISTS (
        SELECT 1
          FROM public.inventory_opening_establishments
    ) THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END
$rsc_0052_downgrade$
"""
    )


def _verify_existing_graph_closure_rows() -> None:
    """Reject already-poisoned immutable children before adding closures."""

    inventory_head_chain = _audit_stream_full_chain_binding_sql(
        "inventory_head"
    )
    op.execute(
        f"""
DO $rsc_0052_existing_closure$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS task
         WHERE task.task_type = 'opening'
           AND NOT public.{START_GRAPH_FUNCTION}(task.id, TRUE)
    ) OR EXISTS (
        SELECT 1
          FROM (
              SELECT count_line.task_id,
                     count_line.round_id,
                     count_line.scope_id
                FROM public.stocktake_count_lines AS count_line
              UNION
              SELECT serial_line.task_id,
                     serial_line.round_id,
                     serial_line.scope_id
                FROM public.stocktake_count_serials AS count_serial
                JOIN public.stocktake_count_lines AS serial_line
                  ON serial_line.id = count_serial.count_line_id
                 AND serial_line.round_id = count_serial.round_id
              UNION
              SELECT observation.task_id,
                     observation.round_id,
                     observation.scope_id
                FROM public.stocktake_count_observations AS observation
              UNION
              SELECT completion.task_id,
                     completion.round_id,
                     completion.scope_id
                FROM public.stocktake_scope_count_completions AS completion
          ) AS scope_graph
          JOIN public.stocktake_tasks AS task
            ON task.id = scope_graph.task_id
           AND task.task_type = 'opening'
         WHERE NOT public.{SCOPE_COMPLETION_FUNCTION}(
             scope_graph.task_id,
             scope_graph.round_id,
             scope_graph.scope_id,
             TRUE
         )
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_rounds AS round_row
          JOIN public.stocktake_tasks AS task
            ON task.id = round_row.task_id
           AND task.task_type = 'opening'
         WHERE round_row.status = 'submitted'
           AND NOT public.{ROUND_SUBMISSION_FUNCTION}(
               task.id,
               round_row.id,
               TRUE
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_reviews AS review_row
          JOIN public.stocktake_tasks AS task
            ON task.id = review_row.task_id
           AND task.task_type = 'opening'
         WHERE NOT public.{REVIEW_GRAPH_FUNCTION}(review_row.id, TRUE)
    ) OR EXISTS (
        SELECT 1
          FROM (
              SELECT difference.task_id, difference.round_id
                FROM public.stocktake_differences AS difference
              UNION
              SELECT completion.task_id, completion.round_id
                FROM public.stocktake_difference_set_completions AS completion
          ) AS difference_graph
          JOIN public.stocktake_tasks AS task
            ON task.id = difference_graph.task_id
           AND task.task_type = 'opening'
         WHERE NOT public.{ROUND_SUBMISSION_FUNCTION}(
             difference_graph.task_id,
             difference_graph.round_id,
             TRUE
         )
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_observation_dispositions AS disposition
          JOIN public.stocktake_tasks AS task
            ON task.id = disposition.task_id
           AND task.task_type = 'opening'
         WHERE NOT public.{DISPOSITION_GRAPH_FUNCTION}(
             disposition.id,
             TRUE
         )
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_recount_cases AS recount_case
          JOIN public.stocktake_tasks AS task
            ON task.id = recount_case.task_id
           AND task.task_type = 'opening'
         WHERE NOT public.{RECOUNT_GRAPH_FUNCTION}(
             recount_case.id,
             TRUE
         )
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_postings AS posting
          JOIN public.stocktake_tasks AS task
            ON task.id = posting.task_id
           AND task.task_type = 'opening'
         WHERE posting.posting_kind <> 'opening'
            OR task.status NOT IN ('posted', 'closed')
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS task
         WHERE task.task_type = 'opening'
           AND task.status IN ('posted', 'closed')
           AND NOT public.{TERMINAL_GRAPH_FUNCTION}(task.id, TRUE)
    ) THEN
        RAISE EXCEPTION '{EXISTING_ROWS_ERROR}';
    END IF;

    IF (SELECT pg_catalog.count(*)
          FROM public.audit_chain_heads AS inventory_head
         WHERE inventory_head.stream_key = 'inventory') <> 1
       OR EXISTS (
           SELECT 1
             FROM public.audit_chain_heads AS inventory_head
             LEFT JOIN public.audit_events AS inventory_head_event
               ON inventory_head_event.id = inventory_head.last_event_id
              AND inventory_head_event.stream_key = inventory_head.stream_key
              AND inventory_head_event.event_hash = inventory_head.last_hash
              AND inventory_head_event.stream_version = inventory_head.version
            WHERE inventory_head.stream_key = 'inventory'
              AND (
                  (
                      inventory_head.version = 0
                      AND (
                          inventory_head.last_event_id IS NOT NULL
                          OR inventory_head.last_hash IS NOT NULL
                          OR EXISTS (
                              SELECT 1
                                FROM public.audit_events AS empty_head_event
                               WHERE empty_head_event.stream_key = 'inventory'
                          )
                      )
                  )
                  OR (
                      inventory_head.version > 0
                      AND (
                          inventory_head_event.id IS NULL
                          OR NOT ({inventory_head_chain})
                          OR inventory_head.version <>
                             (SELECT pg_catalog.count(*)
                                FROM public.audit_events AS counted_event
                               WHERE counted_event.stream_key = 'inventory')
                      )
                  )
              )
       ) THEN
        RAISE EXCEPTION '{EXISTING_ROWS_ERROR}';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.state_transition_events AS state_event
         WHERE (
             pg_catalog.left(state_event.reason, 8) = 'opening_'
             OR pg_catalog.left(state_event.idempotency_key, 8) = 'opening-'
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_tasks AS owned_task
                  WHERE state_event.aggregate_type = 'stocktake_task'
                    AND owned_task.id::text = state_event.aggregate_id
                    AND owned_task.task_type = 'opening'
             )
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_rounds AS owned_round
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = owned_round.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE state_event.aggregate_type = 'stocktake_round'
                    AND owned_round.id::text = state_event.aggregate_id
             )
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_scopes AS owned_scope
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = owned_scope.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE state_event.aggregate_type = 'stocktake_scope'
                    AND owned_scope.id::text = state_event.aggregate_id
             )
             OR EXISTS (
                 SELECT 1
                   FROM (
                       SELECT 'stocktake_review'::text AS aggregate_type,
                              review_row.id::text AS aggregate_id,
                              review_row.task_id
                         FROM public.stocktake_reviews AS review_row
                       UNION ALL
                       SELECT 'stocktake_recount_case',
                              recount_case.id::text,
                              recount_case.task_id
                         FROM public.stocktake_recount_cases AS recount_case
                       UNION ALL
                       SELECT 'stocktake_observation_disposition',
                              disposition.id::text,
                              disposition.task_id
                         FROM public.stocktake_observation_dispositions
                              AS disposition
                       UNION ALL
                       SELECT 'stocktake_posting',
                              posting.id::text,
                              posting.task_id
                         FROM public.stocktake_postings AS posting
                   ) AS audit_only_owner
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = audit_only_owner.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE audit_only_owner.aggregate_type =
                        state_event.aggregate_type
                    AND audit_only_owner.aggregate_id =
                        state_event.aggregate_id
             )
         )
           AND NOT (
               state_event.reason IN (
                   'reconciliation.opening.create',
                   'reconciliation.opening.explain',
                   'reconciliation.opening.approve'
               )
               AND pg_catalog.left(state_event.idempotency_key, 23) =
                   'opening-reconciliation-'
           )
           AND NOT (
               (
                   state_event.aggregate_type = 'stocktake_task'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_tasks AS state_task
                        WHERE state_task.id::text = state_event.aggregate_id
                          AND state_task.task_type = 'opening'
                          AND (
                              state_event.reason IN (
                                  'opening_stocktake_created',
                                  'opening_stocktake_issued',
                                  'opening_stocktake_frozen',
                                  'opening_stocktake_initial_round_started'
                              )
                              OR (
                                  state_event.reason =
                                      'opening_stocktake_posted'
                                  AND state_task.status IN (
                                      'posted', 'closed'
                                  )
                              )
                              OR (
                                  state_event.reason =
                                      'opening_stocktake_closed'
                                  AND state_task.status = 'closed'
                              )
                              OR (
                                  state_event.reason IN (
                                      'opening_initial_round_submitted',
                                      'opening_recount_round_submitted'
                                  )
                                  AND EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_rounds
                                             AS state_round
                                       WHERE state_round.task_id =
                                             state_task.id
                                         AND state_round.id::text =
                                             state_event.metadata_jsonb ->>
                                             'round_id'
                                  )
                              )
                              OR (
                                  state_event.reason IN (
                                      'opening_region_review_approve',
                                      'opening_region_review_recount',
                                      'opening_region_review_reject',
                                      'opening_headquarters_review_approve',
                                      'opening_headquarters_review_reject'
                                  )
                                  AND EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_reviews
                                             AS state_review
                                       WHERE state_review.task_id =
                                             state_task.id
                                         AND state_review.id::text =
                                             state_event.metadata_jsonb ->>
                                             'review_id'
                                         AND state_event.reason =
                                             'opening_' ||
                                             state_review.review_stage ||
                                             '_review_' ||
                                             state_review.decision
                                  )
                              )
                              OR (
                                  state_event.reason =
                                      'opening_recount_opened'
                                  AND EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_recount_cases
                                             AS state_recount
                                       WHERE state_recount.task_id =
                                             state_task.id
                                         AND state_recount.id::text =
                                             state_event.metadata_jsonb ->>
                                             'recount_case_id'
                                  )
                              )
                          )
                   )
               )
               OR (
                   state_event.aggregate_type = 'stocktake_round'
                   AND state_event.reason IN (
                       'opening_initial_round_submitted',
                       'opening_recount_round_submitted'
                   )
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_rounds AS state_round
                         JOIN public.stocktake_tasks AS state_task
                           ON state_task.id = state_round.task_id
                          AND state_task.task_type = 'opening'
                        WHERE state_round.id::text = state_event.aggregate_id
                   )
               )
               OR (
                   state_event.aggregate_type = 'stocktake_scope'
                   AND state_event.reason IN (
                       'opening_initial_scope_count_completed',
                       'opening_recount_scope_count_completed'
                   )
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_scopes AS state_scope
                         JOIN public.stocktake_tasks AS state_task
                           ON state_task.id = state_scope.task_id
                          AND state_task.task_type = 'opening'
                         JOIN public.stocktake_scope_count_completions
                              AS state_completion
                           ON state_completion.task_id = state_task.id
                          AND state_completion.scope_id = state_scope.id
                          AND state_completion.round_id::text =
                              state_event.metadata_jsonb ->> 'round_id'
                        WHERE state_scope.id::text = state_event.aggregate_id
                   )
               )
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.state_transition_events AS inventory_state
         WHERE pg_catalog.left(inventory_state.idempotency_key, 16) =
               'inventory-state-'
           AND NOT EXISTS (
               SELECT 1
                 FROM public.inventory_transactions AS inventory_owner
                WHERE inventory_owner.id::text = inventory_state.aggregate_id
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.outbox_events AS outbox_event
         WHERE (
             pg_catalog.left(outbox_event.event_type, 18) =
                 'stocktake.opening.'
             OR pg_catalog.left(outbox_event.idempotency_key, 8) = 'opening-'
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_tasks AS owned_task
                  WHERE outbox_event.aggregate_type = 'stocktake_task'
                    AND owned_task.id::text = outbox_event.aggregate_id
                    AND owned_task.task_type = 'opening'
             )
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_rounds AS owned_round
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = owned_round.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE outbox_event.aggregate_type = 'stocktake_round'
                    AND owned_round.id::text = outbox_event.aggregate_id
             )
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_scopes AS owned_scope
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = owned_scope.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE outbox_event.aggregate_type = 'stocktake_scope'
                    AND owned_scope.id::text = outbox_event.aggregate_id
             )
             OR EXISTS (
                 SELECT 1
                   FROM (
                       SELECT 'stocktake_review'::text AS aggregate_type,
                              review_row.id::text AS aggregate_id,
                              review_row.task_id
                         FROM public.stocktake_reviews AS review_row
                       UNION ALL
                       SELECT 'stocktake_recount_case',
                              recount_case.id::text,
                              recount_case.task_id
                         FROM public.stocktake_recount_cases AS recount_case
                       UNION ALL
                       SELECT 'stocktake_observation_disposition',
                              disposition.id::text,
                              disposition.task_id
                         FROM public.stocktake_observation_dispositions
                              AS disposition
                       UNION ALL
                       SELECT 'stocktake_posting',
                              posting.id::text,
                              posting.task_id
                         FROM public.stocktake_postings AS posting
                   ) AS audit_only_owner
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = audit_only_owner.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE audit_only_owner.aggregate_type =
                        outbox_event.aggregate_type
                    AND audit_only_owner.aggregate_id =
                        outbox_event.aggregate_id
             )
         )
           AND NOT (
               outbox_event.event_type IN (
                   'reconciliation.opening.create',
                   'reconciliation.opening.explain',
                   'reconciliation.opening.approve'
               )
               AND pg_catalog.left(outbox_event.idempotency_key, 23) =
                   'opening-reconciliation-'
           )
           AND NOT (
               (
                   outbox_event.aggregate_type = 'stocktake_task'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_tasks AS outbox_task
                        WHERE outbox_task.id::text = outbox_event.aggregate_id
                          AND outbox_task.task_type = 'opening'
                          AND (
                              outbox_event.event_type IN (
                                  'stocktake.opening.started'
                              )
                              OR (
                                  outbox_event.event_type =
                                      'stocktake.opening.posted'
                                  AND outbox_task.status IN (
                                      'posted', 'closed'
                                  )
                              )
                              OR (
                                  outbox_event.event_type =
                                      'stocktake.opening.closed'
                                  AND outbox_task.status = 'closed'
                              )
                              OR (
                                  outbox_event.event_type IN (
                                      'stocktake.opening.region_reviewed',
                                      'stocktake.opening.headquarters_reviewed'
                                  )
                                  AND EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_reviews
                                             AS outbox_review
                                       WHERE outbox_review.task_id =
                                             outbox_task.id
                                         AND outbox_review.id::text =
                                             outbox_event.payload_jsonb ->>
                                             'review_id'
                                         AND outbox_event.event_type =
                                             'stocktake.opening.' ||
                                             outbox_review.review_stage ||
                                             '_reviewed'
                                  )
                              )
                              OR (
                                  outbox_event.event_type =
                                      'stocktake.opening.recount_opened'
                                  AND EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_recount_cases
                                             AS outbox_recount
                                       WHERE outbox_recount.task_id =
                                             outbox_task.id
                                         AND outbox_recount.id::text =
                                             outbox_event.payload_jsonb ->>
                                             'recount_case_id'
                                  )
                              )
                          )
                   )
               )
               OR (
                   outbox_event.aggregate_type = 'stocktake_round'
                   AND outbox_event.event_type =
                       'stocktake.opening.round_submitted'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_rounds AS outbox_round
                         JOIN public.stocktake_tasks AS outbox_task
                           ON outbox_task.id = outbox_round.task_id
                          AND outbox_task.task_type = 'opening'
                        WHERE outbox_round.id::text = outbox_event.aggregate_id
                   )
               )
               OR (
                   outbox_event.aggregate_type = 'stocktake_scope'
                   AND outbox_event.event_type =
                       'stocktake.opening.scope_count_completed'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_scopes AS outbox_scope
                         JOIN public.stocktake_tasks AS outbox_task
                           ON outbox_task.id = outbox_scope.task_id
                          AND outbox_task.task_type = 'opening'
                         JOIN public.stocktake_scope_count_completions
                              AS outbox_completion
                           ON outbox_completion.task_id = outbox_task.id
                          AND outbox_completion.scope_id = outbox_scope.id
                          AND outbox_completion.round_id::text =
                              outbox_event.payload_jsonb ->> 'round_id'
                        WHERE outbox_scope.id::text = outbox_event.aggregate_id
                   )
               )
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.outbox_events AS inventory_outbox
         WHERE pg_catalog.left(inventory_outbox.idempotency_key, 17) =
               'inventory-outbox-'
           AND NOT EXISTS (
               SELECT 1
                 FROM public.inventory_transactions AS inventory_owner
                WHERE inventory_owner.id::text = inventory_outbox.aggregate_id
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.audit_events AS audit_event
         WHERE (
             pg_catalog.left(audit_event.action, 18) =
                 'stocktake.opening.'
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_tasks AS owned_task
                  WHERE audit_event.aggregate_type = 'stocktake_task'
                    AND owned_task.id::text = audit_event.aggregate_id
                    AND owned_task.task_type = 'opening'
             )
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_scopes AS owned_scope
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = owned_scope.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE audit_event.aggregate_type = 'stocktake_scope'
                    AND owned_scope.id::text = audit_event.aggregate_id
             )
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_rounds AS owned_round
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = owned_round.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE audit_event.aggregate_type = 'stocktake_round'
                    AND owned_round.id::text = audit_event.aggregate_id
             )
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_reviews AS owned_review
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = owned_review.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE audit_event.aggregate_type = 'stocktake_review'
                    AND owned_review.id::text = audit_event.aggregate_id
             )
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_recount_cases AS owned_recount
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = owned_recount.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE audit_event.aggregate_type =
                        'stocktake_recount_case'
                    AND owned_recount.id::text = audit_event.aggregate_id
             )
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_observation_dispositions
                        AS owned_disposition
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = owned_disposition.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE audit_event.aggregate_type =
                        'stocktake_observation_disposition'
                    AND owned_disposition.id::text = audit_event.aggregate_id
             )
             OR EXISTS (
                 SELECT 1
                   FROM public.stocktake_postings AS owned_posting
                   JOIN public.stocktake_tasks AS owned_task
                     ON owned_task.id = owned_posting.task_id
                    AND owned_task.task_type = 'opening'
                  WHERE audit_event.aggregate_type = 'stocktake_posting'
                    AND owned_posting.id::text = audit_event.aggregate_id
                    AND owned_posting.posting_kind = 'opening'
             )
         )
           AND NOT (
               (
                   (
                       audit_event.action = 'stocktake.opening.started'
                       OR (
                           audit_event.action = 'stocktake.opening.closed'
                           AND EXISTS (
                               SELECT 1
                                 FROM public.stocktake_tasks AS closed_task
                                WHERE closed_task.id::text =
                                      audit_event.aggregate_id
                                  AND closed_task.task_type = 'opening'
                                  AND closed_task.status = 'closed'
                           )
                       )
                   )
                   AND audit_event.aggregate_type = 'stocktake_task'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_tasks AS audit_task
                        WHERE audit_task.id::text = audit_event.aggregate_id
                          AND audit_task.task_type = 'opening'
                   )
               )
               OR (
                   audit_event.action =
                       'stocktake.opening.scope_count_completed'
                   AND audit_event.aggregate_type = 'stocktake_scope'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_scopes AS audit_scope
                         JOIN public.stocktake_tasks AS audit_task
                           ON audit_task.id = audit_scope.task_id
                          AND audit_task.task_type = 'opening'
                        WHERE audit_scope.id::text = audit_event.aggregate_id
                   )
               )
               OR (
                   audit_event.action = 'stocktake.opening.round_submitted'
                   AND audit_event.aggregate_type = 'stocktake_round'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_rounds AS audit_round
                         JOIN public.stocktake_tasks AS audit_task
                           ON audit_task.id = audit_round.task_id
                          AND audit_task.task_type = 'opening'
                        WHERE audit_round.id::text = audit_event.aggregate_id
                   )
               )
               OR (
                   audit_event.action IN (
                       'stocktake.opening.region_reviewed',
                       'stocktake.opening.headquarters_reviewed'
                   )
                   AND audit_event.aggregate_type = 'stocktake_review'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_reviews AS audit_review
                        JOIN public.stocktake_tasks AS audit_task
                           ON audit_task.id = audit_review.task_id
                          AND audit_task.task_type = 'opening'
                        WHERE audit_review.id::text = audit_event.aggregate_id
                          AND audit_event.action =
                              'stocktake.opening.' ||
                              audit_review.review_stage || '_reviewed'
                   )
               )
               OR (
                   audit_event.action = 'stocktake.opening.recount_opened'
                   AND audit_event.aggregate_type = 'stocktake_recount_case'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_recount_cases AS audit_recount
                         JOIN public.stocktake_tasks AS audit_task
                           ON audit_task.id = audit_recount.task_id
                          AND audit_task.task_type = 'opening'
                        WHERE audit_recount.id::text = audit_event.aggregate_id
                   )
               )
               OR (
                   audit_event.action =
                       'stocktake.opening.observation_disposed'
                   AND audit_event.aggregate_type =
                       'stocktake_observation_disposition'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_observation_dispositions
                              AS audit_disposition
                         JOIN public.stocktake_tasks AS audit_task
                           ON audit_task.id = audit_disposition.task_id
                          AND audit_task.task_type = 'opening'
                        WHERE audit_disposition.id::text =
                              audit_event.aggregate_id
                   )
               )
               OR (
                   audit_event.action = 'stocktake.opening.posted'
                   AND audit_event.aggregate_type = 'stocktake_posting'
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_postings AS audit_posting
                        JOIN public.stocktake_tasks AS audit_task
                           ON audit_task.id = audit_posting.task_id
                          AND audit_task.task_type = 'opening'
                        WHERE audit_posting.id::text = audit_event.aggregate_id
                          AND audit_posting.posting_kind = 'opening'
                          AND audit_task.status IN ('posted', 'closed')
                   )
               )
           )
    ) THEN
        RAISE EXCEPTION '{EXISTING_ROWS_ERROR}';
    END IF;
END
$rsc_0052_existing_closure$
"""
    )


def _verify_existing_opening_rows() -> None:
    """Reject noncanonical task/current-round bindings; never rewrite facts."""

    current_scope_master = _current_scope_master_proof_sql(
        "task.id",
        "task.region_org_id",
        alias_suffix="existing_nonterminal",
    )
    region_item_rules = _opening_review_item_rules_sql(
        "region_review",
        "review_round",
        historical=True,
    )
    headquarters_item_rules = _opening_review_item_rules_sql(
        "hq_review",
        "review_round",
        historical=True,
    )
    region_actor = _historical_review_actor_sql(
        "region_review",
        "task",
        headquarters=False,
    )
    headquarters_actor = _historical_review_actor_sql(
        "hq_review",
        "task",
        headquarters=True,
    )
    source_submission_event_metadata = _round_submission_event_metadata_sql(
        "source_round",
        "task.id",
    )
    submission_event_metadata = _round_submission_event_metadata_sql(
        "current_round",
        "task.id",
    )
    region_review_event_metadata = _review_event_metadata_sql(
        "region_review",
        "review_round",
    )
    headquarters_review_event_metadata = _review_event_metadata_sql(
        "hq_review",
        "review_round",
    )
    recount_event_metadata = _recount_event_metadata_sql(
        "recount_case",
        "current_round",
    )
    trigger_review_item_rules = _opening_review_item_rules_sql(
        "trigger_review",
        "source_round",
        historical=True,
    )
    source_region_item_rules = _opening_review_item_rules_sql(
        "source_region_review",
        "source_round",
        historical=True,
    )
    trigger_review_item_coverage = _historical_review_item_coverage_sql(
        "trigger_review",
        "recount_task",
        "source_round",
    )
    source_region_item_coverage = _historical_review_item_coverage_sql(
        "source_region_review",
        "recount_task",
        "source_round",
    )
    trigger_region_actor = _historical_review_actor_sql(
        "trigger_review",
        "recount_task",
        headquarters=False,
    )
    trigger_headquarters_actor = _historical_review_actor_sql(
        "trigger_review",
        "recount_task",
        headquarters=True,
    )
    source_region_actor = _historical_review_actor_sql(
        "source_region_review",
        "recount_task",
        headquarters=False,
    )
    trigger_review_event_metadata = _review_event_metadata_sql(
        "trigger_review",
        "source_round",
    )
    source_region_review_event_metadata = _review_event_metadata_sql(
        "source_region_review",
        "source_round",
    )
    historical_source_submission_event_metadata = (
        _round_submission_event_metadata_sql(
            "source_round",
            "recount_task.id",
        )
    )
    historical_recount_event_metadata = _recount_event_metadata_sql(
        "recount_case",
        "next_round",
    )
    current_submission_proof = (
        f"public.{ROUND_SUBMISSION_FUNCTION}("
        "task.id, current_round.id, TRUE)"
    )
    current_recount_source_submission_proof = (
        f"public.{ROUND_SUBMISSION_FUNCTION}("
        "task.id, source_round.id, TRUE)"
    )
    historical_recount_source_submission_proof = (
        f"public.{ROUND_SUBMISSION_FUNCTION}("
        "recount_task.id, source_round.id, TRUE)"
    )
    current_recount_replay_proof = _recount_case_replay_proof_sql(
        task_id_sql="task.id",
        region_org_id_sql="task.region_org_id",
        task_scope_manifest_sql="task.scope_manifest_sha256",
        case_alias="recount_case",
        source_round_alias="source_round",
        submission_alias="submission",
        sealing_alias="sealing",
        completion_alias="difference_completion",
        trigger_review_alias="trigger_review",
        historical=True,
    )
    current_recount_side_effect_proof = _recount_side_effect_proof_sql(
        "task.id",
        "recount_case",
        "current_round",
        historical=True,
    )
    historical_recount_replay_proof = _recount_case_replay_proof_sql(
        task_id_sql="recount_task.id",
        region_org_id_sql="recount_task.region_org_id",
        task_scope_manifest_sql="recount_task.scope_manifest_sha256",
        case_alias="recount_case",
        source_round_alias="source_round",
        submission_alias="source_submission",
        sealing_alias="source_sealing",
        completion_alias="source_difference_completion",
        trigger_review_alias="trigger_review",
        historical=True,
    )
    historical_recount_side_effect_proof = _recount_side_effect_proof_sql(
        "recount_task.id",
        "recount_case",
        "next_round",
        historical=True,
    )
    region_difference_completion_proof = (
        _review_difference_completion_proof_sql(
            "task.id",
            "review_round",
            "review_submission",
            "region_review",
        )
    )
    headquarters_difference_completion_proof = (
        _review_difference_completion_proof_sql(
            "task.id",
            "review_round",
            "review_submission",
            "hq_review",
        )
    )
    trigger_difference_completion_proof = (
        _review_difference_completion_proof_sql(
            "recount_task.id",
            "source_round",
            "source_submission",
            "trigger_review",
        )
    )
    source_region_difference_completion_proof = (
        _review_difference_completion_proof_sql(
            "recount_task.id",
            "source_round",
            "source_submission",
            "source_region_review",
        )
    )
    op.execute(
        f"""
DO $rsc_0052_existing_rows$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS task
         WHERE task.task_type = 'opening'
           AND (
               NOT public.rsc_opening_start_graph_complete_0052(
                   task.id,
                   TRUE
               )
               OR (
                   task.status NOT IN ('posted', 'closed')
                   AND NOT ({current_scope_master})
               )
               OR task.cancelled_at IS NOT NULL
               OR (
                   task.status NOT IN ('posted', 'closed')
                   AND (
                       task.posted_at IS NOT NULL
                       OR task.closed_at IS NOT NULL
                       OR EXISTS (
                           SELECT 1
                             FROM public.stocktake_postings
                                  AS premature_existing_posting
                            WHERE premature_existing_posting.task_id =
                                  task.id
                       )
                       OR EXISTS (
                           SELECT 1
                             FROM public.inventory_opening_establishments
                                  AS premature_existing_establishment
                            WHERE premature_existing_establishment.task_id =
                                  task.id
                       )
                   )
               )
               OR (
                   task.status = 'posted'
                   AND (
                       task.posted_at IS NULL
                       OR task.closed_at IS NOT NULL
                   )
               )
               OR (
                   task.status = 'closed'
                   AND (
                       task.posted_at IS NULL
                       OR task.closed_at IS NULL
                       OR task.closed_at <= task.posted_at
                   )
               )
               OR (
                       task.current_round_no < 1
                       OR (
                           SELECT pg_catalog.count(*)
                             FROM public.stocktake_rounds AS topology_round
                            WHERE topology_round.task_id = task.id
                       ) IS DISTINCT FROM task.current_round_no
                       OR EXISTS (
                           SELECT 1
                             FROM public.stocktake_rounds AS topology_round
                            WHERE topology_round.task_id = task.id
                              AND (
                                  topology_round.round_no >
                                      task.current_round_no
                                  OR (
                                      topology_round.round_no = 1
                                      AND (
                                          topology_round.round_type <>
                                              'initial'
                                          OR topology_round.recount_case_id
                                              IS NOT NULL
                                      )
                                  )
                                  OR (
                                      topology_round.round_no > 1
                                      AND (
                                          topology_round.round_type <>
                                              'recount'
                                          OR topology_round.recount_case_id
                                              IS NULL
                                          OR NOT EXISTS (
                                              SELECT 1
                                                FROM public.stocktake_recount_cases
                                                     AS topology_case
                                                JOIN public.stocktake_rounds
                                                     AS topology_source_round
                                                  ON topology_source_round.id =
                                                     topology_case.source_round_id
                                                 AND topology_source_round.task_id =
                                                     task.id
                                                 AND topology_source_round.round_no =
                                                     topology_round.round_no - 1
                                               WHERE topology_case.id =
                                                     topology_round.recount_case_id
                                                 AND topology_case.task_id =
                                                     task.id
                                                 AND topology_case.next_round_no =
                                                     topology_round.round_no
                                          )
                                      )
                                  )
                                  OR (
                                      topology_round.round_no <
                                          task.current_round_no
                                      AND topology_round.status <>
                                          'submitted'
                                  )
                              )
                       )
               )
               OR EXISTS (
                   SELECT 1
                     FROM public.stocktake_recount_cases AS future_case
                    WHERE future_case.task_id = task.id
                      AND future_case.next_round_no > task.current_round_no
               )
               OR
               (
                   SELECT pg_catalog.count(*)
                     FROM public.stocktake_recount_cases AS task_recount_case
                    WHERE task_recount_case.task_id = task.id
               ) IS DISTINCT FROM pg_catalog.greatest(
                   task.current_round_no - 1,
                   0
               )
               OR NOT (
               (
                   task.status = 'counting'
                   AND task.current_round_no = 1
                   AND task.submitted_at IS NULL
                   AND (
                       SELECT pg_catalog.count(*)
                         FROM public.stocktake_rounds AS current_round
                        WHERE current_round.task_id = task.id
                          AND current_round.round_no = 1
                          AND current_round.round_type = 'initial'
                          AND current_round.status = 'counting'
                          AND current_round.recount_case_id IS NULL
                          AND current_round.submitted_by_user_id IS NULL
                          AND current_round.submitted_at IS NULL
                          AND current_round.count_manifest_sha256 IS NULL
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.stocktake_reviews
                                     AS premature_review
                               WHERE premature_review.task_id = task.id
                                 AND premature_review.round_id =
                                     current_round.id
                          )
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.stocktake_round_submissions
                                     AS premature_submission
                               WHERE premature_submission.task_id = task.id
                                 AND premature_submission.round_id =
                                     current_round.id
                          )
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.stocktake_differences
                                     AS premature_difference
                               WHERE premature_difference.task_id = task.id
                                 AND premature_difference.round_id =
                                     current_round.id
                          )
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.stocktake_difference_set_completions
                                     AS premature_difference_completion
                               WHERE premature_difference_completion.task_id =
                                     task.id
                                 AND premature_difference_completion.round_id =
                                     current_round.id
                          )
                   ) = 1
               )
               OR (
                   task.status = 'counting'
                   AND task.current_round_no > 1
                   AND task.submitted_at IS NOT NULL
                   AND (
                       SELECT pg_catalog.count(*)
                         FROM public.stocktake_rounds AS current_round
                         JOIN public.stocktake_recount_cases AS recount_case
                           ON recount_case.id = current_round.recount_case_id
                          AND recount_case.task_id = task.id
                          AND recount_case.next_round_no =
                              current_round.round_no
                         JOIN public.stocktake_rounds AS source_round
                           ON source_round.id = recount_case.source_round_id
                          AND source_round.task_id = task.id
                          AND source_round.round_no =
                              current_round.round_no - 1
                         JOIN public.stocktake_round_submissions AS submission
                           ON submission.id =
                              recount_case.source_round_submission_id
                          AND submission.task_id = task.id
                          AND submission.round_id = source_round.id
                          AND submission.submitted_at = task.submitted_at
                          AND submission.created_at = submission.submitted_at
                          AND submission.submitted_by_user_id =
                              source_round.submitted_by_user_id
                          AND submission.count_manifest_sha256 =
                              source_round.count_manifest_sha256
                          AND submission.round_manifest_sha256 ~
                              '^[0-9a-f]{{64}}$'
                          AND submission.request_sha256 ~
                              '^[0-9a-f]{{64}}$'
                          AND submission.idempotency_key_hash ~
                              '^[0-9a-f]{{64}}$'
                         JOIN public.stocktake_scope_count_completions AS sealing
                           ON sealing.id = submission.sealing_completion_id
                          AND sealing.task_id = submission.task_id
                          AND sealing.round_id = submission.round_id
                          AND sealing.completed_by_user_id =
                              submission.submitted_by_user_id
                          AND sealing.completed_by_person_id =
                              submission.submitted_by_person_id
                          AND sealing.completed_role_assignment_id =
                              submission.submitted_role_assignment_id
                          AND sealing.authorization_version =
                              submission.authorization_version
                          AND sealing.completed_at = submission.submitted_at
                         JOIN public.stocktake_difference_set_completions
                              AS difference_completion
                           ON difference_completion.id =
                              recount_case.source_difference_completion_id
                          AND difference_completion.task_id = task.id
                          AND difference_completion.round_id = source_round.id
                          AND difference_completion.round_submission_id =
                              submission.id
                         JOIN public.stocktake_reviews AS trigger_review
                           ON trigger_review.id =
                              recount_case.trigger_review_id
                          AND trigger_review.task_id = task.id
                          AND trigger_review.round_id = source_round.id
                         JOIN public.state_transition_events
                              AS source_submission_event
                           ON source_submission_event.aggregate_type =
                              'stocktake_task'
                          AND source_submission_event.aggregate_id =
                              task.id::text
                          AND source_submission_event.from_status = 'counting'
                          AND source_submission_event.to_status = 'submitted'
                          AND source_submission_event.reason = CASE
                              WHEN source_round.round_no = 1
                                   AND source_round.round_type = 'initial'
                              THEN 'opening_initial_round_submitted'
                              WHEN source_round.round_no > 1
                                   AND source_round.round_type = 'recount'
                              THEN 'opening_recount_round_submitted'
                              ELSE NULL
                          END
                          AND source_submission_event.actor_id =
                              submission.submitted_by_user_id
                          AND source_submission_event.occurred_at =
                              submission.submitted_at
                          AND source_submission_event.created_at =
                              submission.submitted_at
                          AND source_submission_event.metadata_jsonb =
                              {source_submission_event_metadata}
                         JOIN public.state_transition_events AS recount_event
                           ON recount_event.aggregate_type = 'stocktake_task'
                          AND recount_event.aggregate_id = task.id::text
                          AND recount_event.from_status = 'recount_required'
                          AND recount_event.to_status = 'counting'
                          AND recount_event.reason = 'opening_recount_opened'
                          AND recount_event.actor_id =
                              recount_case.opened_by_user_id
                          AND recount_event.occurred_at =
                              recount_case.opened_at
                          AND recount_event.created_at = recount_case.opened_at
                          AND recount_event.metadata_jsonb =
                              {recount_event_metadata}
                        WHERE current_round.task_id = task.id
                          AND current_round.round_no = task.current_round_no
                          AND current_round.round_type = 'recount'
                          AND current_round.status = 'counting'
                          AND current_round.submitted_by_user_id IS NULL
                          AND current_round.submitted_at IS NULL
                          AND current_round.count_manifest_sha256 IS NULL
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.stocktake_reviews
                                     AS premature_review
                               WHERE premature_review.task_id = task.id
                                 AND premature_review.round_id =
                                     current_round.id
                          )
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.stocktake_round_submissions
                                     AS premature_current_submission
                               WHERE premature_current_submission.task_id =
                                     task.id
                                 AND premature_current_submission.round_id =
                                     current_round.id
                          )
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.stocktake_differences
                                     AS premature_current_difference
                               WHERE premature_current_difference.task_id =
                                     task.id
                                 AND premature_current_difference.round_id =
                                     current_round.id
                          )
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.stocktake_difference_set_completions
                                     AS premature_current_difference_completion
                               WHERE premature_current_difference_completion.task_id =
                                     task.id
                                 AND premature_current_difference_completion.round_id =
                                     current_round.id
                          )
                          AND current_round.started_at =
                              recount_case.opened_at
                          AND current_round.created_at =
                              recount_case.opened_at
                          AND current_round.updated_at =
                              recount_case.opened_at
                          AND task.updated_at = recount_case.opened_at
                          AND source_round.status = 'submitted'
                          AND source_round.submitted_at = task.submitted_at
                          AND source_round.updated_at =
                              source_round.submitted_at
                          AND source_round.count_manifest_sha256 ~
                              '^[0-9a-f]{{64}}$'
                          AND {current_recount_source_submission_proof}
                          AND {current_recount_replay_proof}
                          AND {current_recount_side_effect_proof}
                   ) = 1
               )
               OR (
                   task.status IN (
                       'submitted',
                       'hq_review',
                       'approved',
                       'recount_required',
                       'posted',
                       'closed'
                   )
                   AND task.current_round_no > 0
                   AND task.submitted_at IS NOT NULL
                   AND (
                       SELECT pg_catalog.count(*)
                         FROM public.stocktake_rounds AS current_round
                         JOIN public.stocktake_round_submissions AS submission
                           ON submission.task_id = task.id
                          AND submission.round_id = current_round.id
                          AND submission.submitted_at = task.submitted_at
                          AND submission.created_at = submission.submitted_at
                          AND submission.submitted_by_user_id =
                              current_round.submitted_by_user_id
                          AND submission.count_manifest_sha256 =
                              current_round.count_manifest_sha256
                          AND submission.round_manifest_sha256 ~
                              '^[0-9a-f]{{64}}$'
                          AND submission.request_sha256 ~
                              '^[0-9a-f]{{64}}$'
                          AND submission.idempotency_key_hash ~
                              '^[0-9a-f]{{64}}$'
                         JOIN public.stocktake_scope_count_completions AS sealing
                           ON sealing.id = submission.sealing_completion_id
                          AND sealing.task_id = submission.task_id
                          AND sealing.round_id = submission.round_id
                          AND sealing.completed_by_user_id =
                              submission.submitted_by_user_id
                          AND sealing.completed_by_person_id =
                              submission.submitted_by_person_id
                          AND sealing.completed_role_assignment_id =
                              submission.submitted_role_assignment_id
                          AND sealing.authorization_version =
                              submission.authorization_version
                          AND sealing.completed_at = submission.submitted_at
                         JOIN public.state_transition_events AS submission_event
                           ON submission_event.aggregate_type =
                              'stocktake_task'
                          AND submission_event.aggregate_id = task.id::text
                          AND submission_event.from_status = 'counting'
                          AND submission_event.to_status = 'submitted'
                          AND submission_event.reason = CASE
                              WHEN current_round.round_no = 1
                                   AND current_round.round_type = 'initial'
                              THEN 'opening_initial_round_submitted'
                              WHEN current_round.round_no > 1
                                   AND current_round.round_type = 'recount'
                              THEN 'opening_recount_round_submitted'
                              ELSE NULL
                          END
                          AND submission_event.actor_id =
                              submission.submitted_by_user_id
                          AND submission_event.occurred_at =
                              submission.submitted_at
                          AND submission_event.created_at =
                              submission.submitted_at
                          AND submission_event.metadata_jsonb =
                              {submission_event_metadata}
                        WHERE current_round.task_id = task.id
                          AND current_round.round_no = task.current_round_no
                          AND current_round.status = 'submitted'
                          AND current_round.submitted_at = task.submitted_at
                          AND current_round.updated_at =
                              current_round.submitted_at
                          AND current_round.count_manifest_sha256 ~
                              '^[0-9a-f]{{64}}$'
                          AND {current_submission_proof}
                          AND (
                              (
                                  current_round.round_no = 1
                                  AND current_round.round_type = 'initial'
                                  AND current_round.recount_case_id IS NULL
                              )
                              OR
                              (
                                  current_round.round_no > 1
                                  AND current_round.round_type = 'recount'
                                  AND current_round.recount_case_id IS NOT NULL
                              )
                          )
                   ) = 1
                   AND (
                       (
                           task.status = 'submitted'
                           AND task.updated_at = task.submitted_at
                           AND NOT EXISTS (
                               SELECT 1
                                 FROM public.stocktake_reviews
                                      AS premature_review
                                 JOIN public.stocktake_rounds
                                      AS submitted_round
                                   ON submitted_round.id =
                                      premature_review.round_id
                                  AND submitted_round.task_id = task.id
                                WHERE premature_review.task_id = task.id
                                  AND submitted_round.round_no =
                                      task.current_round_no
                           )
                       )
                       OR (
                           task.status = 'hq_review'
                           AND (
                               SELECT pg_catalog.count(*)
                                 FROM public.stocktake_rounds AS review_round
                                 JOIN public.stocktake_round_submissions
                                      AS review_submission
                                   ON review_submission.task_id = task.id
                                  AND review_submission.round_id =
                                      review_round.id
                                  AND review_submission.submitted_at =
                                      task.submitted_at
                                 JOIN public.stocktake_reviews AS region_review
                                   ON region_review.task_id = task.id
                                  AND region_review.round_id = review_round.id
                                  AND region_review.review_stage = 'region'
                                  AND region_review.decision = 'approve'
                                  AND region_review.created_at =
                                      region_review.reviewed_at
                                  AND region_review.reviewed_at =
                                      task.updated_at
                                  AND region_review.reviewed_at >=
                                      task.submitted_at
                                  AND region_review.decision_manifest_sha256 ~
                                      '^[0-9a-f]{{64}}$'
                                  AND region_review.idempotency_key_hash ~
                                      '^[0-9a-f]{{64}}$'
                                  AND {region_actor}
                                 JOIN public.state_transition_events
                                      AS region_event
                                   ON region_event.aggregate_type =
                                      'stocktake_task'
                                  AND region_event.aggregate_id = task.id::text
                                  AND region_event.from_status = 'submitted'
                                  AND region_event.to_status = 'hq_review'
                                  AND region_event.reason =
                                      'opening_region_review_approve'
                                  AND region_event.actor_id =
                                      region_review.reviewer_user_id
                                  AND region_event.occurred_at =
                                      region_review.reviewed_at
                                  AND region_event.created_at =
                                      region_review.reviewed_at
                                  AND region_event.metadata_jsonb =
                                      {region_review_event_metadata}
                                WHERE review_round.task_id = task.id
                                  AND review_round.round_no =
                                      task.current_round_no
                                  AND review_round.status = 'submitted'
                                  AND review_round.submitted_at =
                                      task.submitted_at
                                  AND NOT EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_reviews
                                             AS headquarters_review
                                       WHERE headquarters_review.task_id =
                                             task.id
                                         AND headquarters_review.round_id =
                                             review_round.id
                                         AND headquarters_review.review_stage =
                                             'headquarters'
                                  )
                                  AND NOT EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_differences
                                             AS difference
                                       WHERE difference.task_id = task.id
                                         AND difference.round_id =
                                             review_round.id
                                         AND NOT EXISTS (
                                             SELECT 1
                                               FROM public.stocktake_review_items
                                                    AS item
                                              WHERE item.review_id =
                                                    region_review.id
                                                AND item.difference_id =
                                                    difference.id
                                                AND item.task_id = task.id
                                                AND item.round_id =
                                                    review_round.id
                                                AND item.created_at =
                                                    region_review.reviewed_at
                                         )
                                  )
                                  AND NOT EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_review_items
                                             AS item
                                        LEFT JOIN public.stocktake_differences
                                             AS difference
                                          ON difference.id = item.difference_id
                                         AND difference.task_id = task.id
                                         AND difference.round_id =
                                             review_round.id
                                       WHERE item.review_id = region_review.id
                                         AND difference.id IS NULL
                                  )
                                  AND {region_difference_completion_proof}
                                  AND {region_item_rules}
                           ) = 1
                       )
                       OR (
                           task.status IN ('approved', 'posted', 'closed')
                           AND (
                               SELECT pg_catalog.count(*)
                                 FROM public.stocktake_rounds AS review_round
                                 JOIN public.stocktake_round_submissions
                                      AS review_submission
                                   ON review_submission.task_id = task.id
                                  AND review_submission.round_id =
                                      review_round.id
                                  AND review_submission.submitted_at =
                                      task.submitted_at
                                 JOIN public.stocktake_reviews AS region_review
                                   ON region_review.task_id = task.id
                                  AND region_review.round_id = review_round.id
                                  AND region_review.review_stage = 'region'
                                  AND region_review.decision = 'approve'
                                  AND region_review.created_at =
                                      region_review.reviewed_at
                                  AND region_review.reviewed_at >=
                                      task.submitted_at
                                  AND region_review.decision_manifest_sha256 ~
                                      '^[0-9a-f]{{64}}$'
                                  AND region_review.idempotency_key_hash ~
                                      '^[0-9a-f]{{64}}$'
                                  AND {region_actor}
                                 JOIN public.stocktake_reviews AS hq_review
                                   ON hq_review.task_id = task.id
                                  AND hq_review.round_id = review_round.id
                                  AND hq_review.review_stage = 'headquarters'
                                  AND hq_review.decision = 'approve'
                                  AND hq_review.created_at =
                                      hq_review.reviewed_at
                                  AND (
                                      (
                                          task.status = 'approved'
                                          AND hq_review.reviewed_at =
                                              task.updated_at
                                      )
                                      OR (
                                          task.status IN ('posted', 'closed')
                                          AND task.posted_at IS NOT NULL
                                          AND hq_review.reviewed_at <=
                                              task.posted_at
                                      )
                                  )
                                  AND hq_review.reviewed_at >
                                      region_review.reviewed_at
                                  AND hq_review.reviewer_user_id <>
                                      region_review.reviewer_user_id
                                  AND hq_review.reviewer_person_id <>
                                      region_review.reviewer_person_id
                                  AND hq_review.reviewer_role_assignment_id <>
                                      region_review.reviewer_role_assignment_id
                                  AND hq_review.decision_manifest_sha256 ~
                                      '^[0-9a-f]{{64}}$'
                                  AND hq_review.idempotency_key_hash ~
                                      '^[0-9a-f]{{64}}$'
                                  AND {headquarters_actor}
                                 JOIN public.state_transition_events
                                      AS region_event
                                   ON region_event.aggregate_type =
                                      'stocktake_task'
                                  AND region_event.aggregate_id = task.id::text
                                  AND region_event.from_status = 'submitted'
                                  AND region_event.to_status = 'hq_review'
                                  AND region_event.reason =
                                      'opening_region_review_approve'
                                  AND region_event.actor_id =
                                      region_review.reviewer_user_id
                                  AND region_event.occurred_at =
                                      region_review.reviewed_at
                                  AND region_event.created_at =
                                      region_review.reviewed_at
                                  AND region_event.metadata_jsonb =
                                      {region_review_event_metadata}
                                 JOIN public.state_transition_events AS hq_event
                                   ON hq_event.aggregate_type = 'stocktake_task'
                                  AND hq_event.aggregate_id = task.id::text
                                  AND hq_event.from_status = 'hq_review'
                                  AND hq_event.to_status = 'approved'
                                  AND hq_event.reason =
                                      'opening_headquarters_review_approve'
                                  AND hq_event.actor_id =
                                      hq_review.reviewer_user_id
                                  AND hq_event.occurred_at =
                                      hq_review.reviewed_at
                                  AND hq_event.created_at =
                                      hq_review.reviewed_at
                                  AND hq_event.metadata_jsonb =
                                      {headquarters_review_event_metadata}
                                WHERE review_round.task_id = task.id
                                  AND review_round.round_no =
                                      task.current_round_no
                                  AND review_round.status = 'submitted'
                                  AND review_round.submitted_at =
                                      task.submitted_at
                                  AND NOT EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_differences
                                             AS difference
                                       WHERE difference.task_id = task.id
                                         AND difference.round_id =
                                             review_round.id
                                         AND (
                                             NOT EXISTS (
                                                 SELECT 1
                                                   FROM public.stocktake_review_items
                                                        AS item
                                                  WHERE item.review_id =
                                                        region_review.id
                                                    AND item.difference_id =
                                                        difference.id
                                                    AND item.task_id = task.id
                                                    AND item.round_id =
                                                        review_round.id
                                                    AND item.created_at =
                                                        region_review.reviewed_at
                                             )
                                             OR NOT EXISTS (
                                                 SELECT 1
                                                   FROM public.stocktake_review_items
                                                        AS item
                                                  WHERE item.review_id =
                                                        hq_review.id
                                                    AND item.difference_id =
                                                        difference.id
                                                    AND item.task_id = task.id
                                                    AND item.round_id =
                                                        review_round.id
                                                    AND item.created_at =
                                                        hq_review.reviewed_at
                                             )
                                         )
                                  )
                                  AND NOT EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_review_items
                                             AS item
                                        LEFT JOIN public.stocktake_differences
                                             AS difference
                                          ON difference.id = item.difference_id
                                         AND difference.task_id = task.id
                                         AND difference.round_id =
                                             review_round.id
                                       WHERE item.review_id IN (
                                                 region_review.id,
                                                 hq_review.id
                                             )
                                         AND difference.id IS NULL
                                  )
                                  AND {region_difference_completion_proof}
                                  AND {headquarters_difference_completion_proof}
                                  AND {region_item_rules}
                                  AND {headquarters_item_rules}
                           ) = 1
                           AND (
                               task.status = 'approved'
                               OR (
                                   task.status IN ('posted', 'closed')
                                   AND (
                                       SELECT pg_catalog.count(*)
                                         FROM public.stocktake_postings
                                              AS posting
                                        WHERE posting.task_id = task.id
                                          AND posting.posting_kind = 'opening'
                                          AND public.{GRAPH_FUNCTION}(
                                              task.id,
                                              posting.inventory_transaction_id
                                          )
                                   ) = 1
                               )
                           )
                       )
                       OR (
                           task.status = 'recount_required'
                           AND (
                               SELECT pg_catalog.count(*)
                                 FROM (
                                     SELECT region_review.id
                                       FROM public.stocktake_rounds
                                            AS review_round
                                       JOIN public.stocktake_round_submissions
                                            AS review_submission
                                         ON review_submission.task_id = task.id
                                        AND review_submission.round_id =
                                            review_round.id
                                        AND review_submission.submitted_at =
                                            task.submitted_at
                                       JOIN public.stocktake_reviews
                                            AS region_review
                                         ON region_review.task_id = task.id
                                        AND region_review.round_id =
                                            review_round.id
                                        AND region_review.review_stage =
                                            'region'
                                        AND region_review.decision IN (
                                            'recount',
                                            'reject'
                                        )
                                        AND region_review.created_at =
                                            region_review.reviewed_at
                                        AND region_review.reviewed_at =
                                            task.updated_at
                                        AND region_review.reviewed_at >=
                                            task.submitted_at
                                        AND region_review.decision_manifest_sha256
                                            ~ '^[0-9a-f]{{64}}$'
                                        AND region_review.idempotency_key_hash
                                            ~ '^[0-9a-f]{{64}}$'
                                        AND {region_actor}
                                       JOIN public.state_transition_events
                                            AS region_event
                                         ON region_event.aggregate_type =
                                            'stocktake_task'
                                        AND region_event.aggregate_id =
                                            task.id::text
                                        AND region_event.from_status =
                                            'submitted'
                                        AND region_event.to_status =
                                            'recount_required'
                                        AND region_event.reason =
                                            'opening_region_review_' ||
                                            region_review.decision
                                        AND region_event.actor_id =
                                            region_review.reviewer_user_id
                                        AND region_event.occurred_at =
                                            region_review.reviewed_at
                                        AND region_event.created_at =
                                            region_review.reviewed_at
                                        AND region_event.metadata_jsonb =
                                            {region_review_event_metadata}
                                      WHERE review_round.task_id = task.id
                                        AND review_round.round_no =
                                            task.current_round_no
                                        AND review_round.status = 'submitted'
                                        AND review_round.submitted_at =
                                            task.submitted_at
                                        AND NOT EXISTS (
                                            SELECT 1
                                              FROM public.stocktake_reviews
                                                   AS headquarters_review
                                             WHERE headquarters_review.task_id =
                                                   task.id
                                               AND headquarters_review.round_id =
                                                   review_round.id
                                               AND headquarters_review.review_stage =
                                                   'headquarters'
                                        )
                                        AND NOT EXISTS (
                                            SELECT 1
                                              FROM public.stocktake_differences
                                                   AS difference
                                             WHERE difference.task_id = task.id
                                               AND difference.round_id =
                                                   review_round.id
                                               AND NOT EXISTS (
                                                   SELECT 1
                                                     FROM public.stocktake_review_items
                                                          AS item
                                                    WHERE item.review_id =
                                                          region_review.id
                                                      AND item.difference_id =
                                                          difference.id
                                                      AND item.task_id = task.id
                                                      AND item.round_id =
                                                          review_round.id
                                                      AND item.created_at =
                                                          region_review.reviewed_at
                                               )
                                        )
                                        AND NOT EXISTS (
                                            SELECT 1
                                              FROM public.stocktake_review_items
                                                   AS item
                                              LEFT JOIN public.stocktake_differences
                                                   AS difference
                                                ON difference.id =
                                                   item.difference_id
                                               AND difference.task_id = task.id
                                               AND difference.round_id =
                                                   review_round.id
                                             WHERE item.review_id =
                                                   region_review.id
                                               AND difference.id IS NULL
                                        )
                                        AND {region_difference_completion_proof}
                                        AND {region_item_rules}
                                     UNION ALL
                                     SELECT hq_review.id
                                       FROM public.stocktake_rounds
                                            AS review_round
                                       JOIN public.stocktake_round_submissions
                                            AS review_submission
                                         ON review_submission.task_id = task.id
                                        AND review_submission.round_id =
                                            review_round.id
                                        AND review_submission.submitted_at =
                                            task.submitted_at
                                       JOIN public.stocktake_reviews
                                            AS region_review
                                         ON region_review.task_id = task.id
                                        AND region_review.round_id =
                                            review_round.id
                                        AND region_review.review_stage =
                                            'region'
                                        AND region_review.decision = 'approve'
                                        AND region_review.created_at =
                                            region_review.reviewed_at
                                        AND region_review.reviewed_at >=
                                            task.submitted_at
                                        AND region_review.decision_manifest_sha256
                                            ~ '^[0-9a-f]{{64}}$'
                                        AND region_review.idempotency_key_hash
                                            ~ '^[0-9a-f]{{64}}$'
                                        AND {region_actor}
                                       JOIN public.stocktake_reviews AS hq_review
                                         ON hq_review.task_id = task.id
                                        AND hq_review.round_id = review_round.id
                                        AND hq_review.review_stage =
                                            'headquarters'
                                        AND hq_review.decision = 'reject'
                                        AND hq_review.created_at =
                                            hq_review.reviewed_at
                                        AND hq_review.reviewed_at =
                                            task.updated_at
                                        AND hq_review.reviewed_at >
                                            region_review.reviewed_at
                                        AND hq_review.reviewer_user_id <>
                                            region_review.reviewer_user_id
                                        AND hq_review.reviewer_person_id <>
                                            region_review.reviewer_person_id
                                        AND hq_review.reviewer_role_assignment_id <>
                                            region_review.reviewer_role_assignment_id
                                        AND hq_review.decision_manifest_sha256
                                            ~ '^[0-9a-f]{{64}}$'
                                        AND hq_review.idempotency_key_hash
                                            ~ '^[0-9a-f]{{64}}$'
                                        AND {headquarters_actor}
                                       JOIN public.state_transition_events
                                            AS region_event
                                         ON region_event.aggregate_type =
                                            'stocktake_task'
                                        AND region_event.aggregate_id =
                                            task.id::text
                                        AND region_event.from_status =
                                            'submitted'
                                        AND region_event.to_status =
                                            'hq_review'
                                        AND region_event.reason =
                                            'opening_region_review_approve'
                                        AND region_event.actor_id =
                                            region_review.reviewer_user_id
                                        AND region_event.occurred_at =
                                            region_review.reviewed_at
                                        AND region_event.created_at =
                                            region_review.reviewed_at
                                        AND region_event.metadata_jsonb =
                                            {region_review_event_metadata}
                                       JOIN public.state_transition_events
                                            AS hq_event
                                         ON hq_event.aggregate_type =
                                            'stocktake_task'
                                        AND hq_event.aggregate_id = task.id::text
                                        AND hq_event.from_status = 'hq_review'
                                        AND hq_event.to_status =
                                            'recount_required'
                                        AND hq_event.reason =
                                            'opening_headquarters_review_reject'
                                        AND hq_event.actor_id =
                                            hq_review.reviewer_user_id
                                        AND hq_event.occurred_at =
                                            hq_review.reviewed_at
                                        AND hq_event.created_at =
                                            hq_review.reviewed_at
                                        AND hq_event.metadata_jsonb =
                                            {headquarters_review_event_metadata}
                                      WHERE review_round.task_id = task.id
                                        AND review_round.round_no =
                                            task.current_round_no
                                        AND review_round.status = 'submitted'
                                        AND review_round.submitted_at =
                                            task.submitted_at
                                        AND NOT EXISTS (
                                            SELECT 1
                                              FROM public.stocktake_differences
                                                   AS difference
                                             WHERE difference.task_id = task.id
                                               AND difference.round_id =
                                                   review_round.id
                                               AND (
                                                   NOT EXISTS (
                                                       SELECT 1
                                                         FROM public.stocktake_review_items
                                                              AS item
                                                        WHERE item.review_id =
                                                              region_review.id
                                                          AND item.difference_id =
                                                              difference.id
                                                          AND item.task_id =
                                                              task.id
                                                          AND item.round_id =
                                                              review_round.id
                                                          AND item.created_at =
                                                              region_review.reviewed_at
                                                   )
                                                   OR NOT EXISTS (
                                                       SELECT 1
                                                         FROM public.stocktake_review_items
                                                              AS item
                                                        WHERE item.review_id =
                                                              hq_review.id
                                                          AND item.difference_id =
                                                              difference.id
                                                          AND item.task_id =
                                                              task.id
                                                          AND item.round_id =
                                                              review_round.id
                                                          AND item.created_at =
                                                              hq_review.reviewed_at
                                                   )
                                               )
                                        )
                                        AND NOT EXISTS (
                                            SELECT 1
                                              FROM public.stocktake_review_items
                                                   AS item
                                              LEFT JOIN public.stocktake_differences
                                                   AS difference
                                                ON difference.id =
                                                   item.difference_id
                                               AND difference.task_id = task.id
                                               AND difference.round_id =
                                                   review_round.id
                                             WHERE item.review_id IN (
                                                       region_review.id,
                                                       hq_review.id
                                                   )
                                               AND difference.id IS NULL
                                        )
                                        AND {region_difference_completion_proof}
                                        AND {headquarters_difference_completion_proof}
                                        AND {region_item_rules}
                                        AND {headquarters_item_rules}
                                 ) AS recount_proof
                           ) = 1
                       )
                   )
               )
           )
           )
    )
    OR EXISTS (
        SELECT 1
          FROM public.stocktake_recount_cases AS recount_case
          JOIN public.stocktake_tasks AS recount_task
            ON recount_task.id = recount_case.task_id
           AND recount_task.task_type = 'opening'
         WHERE (
               recount_case.next_round_no > recount_task.current_round_no
               OR (
                   SELECT pg_catalog.count(*)
                     FROM public.stocktake_rounds AS source_round
                     JOIN public.stocktake_round_submissions
                          AS source_submission
                       ON source_submission.id =
                          recount_case.source_round_submission_id
                      AND source_submission.task_id = recount_task.id
                      AND source_submission.round_id = source_round.id
                      AND source_submission.submitted_at =
                          source_round.submitted_at
                      AND source_submission.created_at =
                          source_submission.submitted_at
                      AND source_submission.submitted_by_user_id =
                          source_round.submitted_by_user_id
                      AND source_submission.count_manifest_sha256 =
                          source_round.count_manifest_sha256
                      AND source_submission.round_manifest_sha256 ~
                          '^[0-9a-f]{{64}}$'
                      AND source_submission.request_sha256 ~
                          '^[0-9a-f]{{64}}$'
                      AND source_submission.idempotency_key_hash ~
                          '^[0-9a-f]{{64}}$'
                     JOIN public.stocktake_scope_count_completions
                          AS source_sealing
                       ON source_sealing.id =
                          source_submission.sealing_completion_id
                      AND source_sealing.task_id =
                          source_submission.task_id
                      AND source_sealing.round_id =
                          source_submission.round_id
                      AND source_sealing.completed_by_user_id =
                          source_submission.submitted_by_user_id
                      AND source_sealing.completed_by_person_id =
                          source_submission.submitted_by_person_id
                      AND source_sealing.completed_role_assignment_id =
                          source_submission.submitted_role_assignment_id
                      AND source_sealing.authorization_version =
                          source_submission.authorization_version
                      AND source_sealing.completed_at =
                          source_submission.submitted_at
                     JOIN public.stocktake_difference_set_completions
                          AS source_difference_completion
                       ON source_difference_completion.id =
                          recount_case.source_difference_completion_id
                      AND source_difference_completion.task_id =
                          recount_task.id
                      AND source_difference_completion.round_id =
                          source_round.id
                      AND source_difference_completion.round_submission_id =
                          source_submission.id
                     JOIN public.state_transition_events
                          AS source_submission_event
                       ON source_submission_event.aggregate_type =
                          'stocktake_task'
                      AND source_submission_event.aggregate_id =
                          recount_task.id::text
                      AND source_submission_event.from_status = 'counting'
                      AND source_submission_event.to_status = 'submitted'
                      AND source_submission_event.reason = CASE
                          WHEN source_round.round_no = 1
                               AND source_round.round_type = 'initial'
                          THEN 'opening_initial_round_submitted'
                          WHEN source_round.round_no > 1
                               AND source_round.round_type = 'recount'
                          THEN 'opening_recount_round_submitted'
                          ELSE NULL
                      END
                      AND source_submission_event.actor_id =
                          source_submission.submitted_by_user_id
                      AND source_submission_event.occurred_at =
                          source_submission.submitted_at
                      AND source_submission_event.created_at =
                          source_submission.submitted_at
                      AND source_submission_event.metadata_jsonb =
                          {historical_source_submission_event_metadata}
                     JOIN public.stocktake_reviews AS trigger_review
                       ON trigger_review.id = recount_case.trigger_review_id
                      AND trigger_review.task_id = recount_task.id
                      AND trigger_review.round_id = source_round.id
                      AND trigger_review.created_at =
                          trigger_review.reviewed_at
                      AND trigger_review.reviewed_at >=
                          source_submission.submitted_at
                      AND trigger_review.reviewed_at <=
                          recount_case.opened_at
                      AND trigger_review.decision_manifest_sha256 ~
                          '^[0-9a-f]{{64}}$'
                      AND trigger_review.idempotency_key_hash ~
                          '^[0-9a-f]{{64}}$'
                     JOIN public.state_transition_events
                          AS trigger_review_event
                       ON trigger_review_event.aggregate_type =
                          'stocktake_task'
                      AND trigger_review_event.aggregate_id =
                          recount_task.id::text
                      AND trigger_review_event.from_status = CASE
                          WHEN trigger_review.review_stage = 'region'
                          THEN 'submitted'
                          WHEN trigger_review.review_stage = 'headquarters'
                          THEN 'hq_review'
                          ELSE NULL
                      END
                      AND trigger_review_event.to_status = 'recount_required'
                      AND trigger_review_event.reason = CASE
                          WHEN trigger_review.review_stage = 'region'
                          THEN 'opening_region_review_' ||
                               trigger_review.decision
                          WHEN trigger_review.review_stage = 'headquarters'
                          THEN 'opening_headquarters_review_' ||
                               trigger_review.decision
                          ELSE NULL
                      END
                      AND trigger_review_event.actor_id =
                          trigger_review.reviewer_user_id
                      AND trigger_review_event.occurred_at =
                          trigger_review.reviewed_at
                      AND trigger_review_event.created_at =
                          trigger_review.reviewed_at
                      AND trigger_review_event.metadata_jsonb =
                          {trigger_review_event_metadata}
                    WHERE source_round.id = recount_case.source_round_id
                      AND source_round.task_id = recount_task.id
                      AND source_round.round_no =
                          recount_case.next_round_no - 1
                      AND source_round.status = 'submitted'
                      AND source_round.submitted_at IS NOT NULL
                      AND source_round.updated_at = source_round.submitted_at
                      AND source_round.count_manifest_sha256 ~
                          '^[0-9a-f]{{64}}$'
                      AND {historical_recount_source_submission_proof}
                      AND {historical_recount_replay_proof}
                      AND recount_case.created_at = recount_case.opened_at
                      AND NOT EXISTS (
                          SELECT 1
                            FROM public.stocktake_reviews AS later_review
                           WHERE later_review.task_id = recount_task.id
                             AND later_review.round_id = source_round.id
                             AND later_review.reviewed_at >
                                 trigger_review.reviewed_at
                      )
                      AND {trigger_difference_completion_proof}
                      AND {trigger_review_item_coverage}
                      AND {trigger_review_item_rules}
                      AND (
                          (
                              trigger_review.review_stage = 'region'
                              AND trigger_review.decision IN (
                                  'recount',
                                  'reject'
                              )
                              AND {trigger_region_actor}
                              AND NOT EXISTS (
                                  SELECT 1
                                    FROM public.stocktake_reviews
                                         AS unexpected_headquarters_review
                                   WHERE unexpected_headquarters_review.task_id =
                                         recount_task.id
                                     AND unexpected_headquarters_review.round_id =
                                         source_round.id
                                     AND unexpected_headquarters_review.review_stage =
                                         'headquarters'
                              )
                          )
                          OR (
                              trigger_review.review_stage = 'headquarters'
                              AND trigger_review.decision = 'reject'
                              AND {trigger_headquarters_actor}
                              AND (
                                  SELECT pg_catalog.count(*)
                                    FROM public.stocktake_reviews
                                         AS source_region_review
                                    JOIN public.state_transition_events
                                         AS source_region_review_event
                                      ON source_region_review_event.aggregate_type =
                                         'stocktake_task'
                                     AND source_region_review_event.aggregate_id =
                                         recount_task.id::text
                                     AND source_region_review_event.from_status =
                                         'submitted'
                                     AND source_region_review_event.to_status =
                                         'hq_review'
                                     AND source_region_review_event.reason =
                                         'opening_region_review_approve'
                                     AND source_region_review_event.actor_id =
                                         source_region_review.reviewer_user_id
                                     AND source_region_review_event.occurred_at =
                                         source_region_review.reviewed_at
                                     AND source_region_review_event.created_at =
                                         source_region_review.reviewed_at
                                     AND source_region_review_event.metadata_jsonb =
                                         {source_region_review_event_metadata}
                                   WHERE source_region_review.task_id =
                                         recount_task.id
                                     AND source_region_review.round_id =
                                         source_round.id
                                     AND source_region_review.review_stage =
                                         'region'
                                     AND source_region_review.decision = 'approve'
                                     AND source_region_review.created_at =
                                         source_region_review.reviewed_at
                                     AND source_region_review.reviewed_at >=
                                         source_submission.submitted_at
                                     AND source_region_review.reviewed_at <
                                         trigger_review.reviewed_at
                                     AND source_region_review.reviewer_user_id <>
                                         trigger_review.reviewer_user_id
                                     AND source_region_review.reviewer_person_id <>
                                         trigger_review.reviewer_person_id
                                     AND source_region_review.reviewer_role_assignment_id <>
                                         trigger_review.reviewer_role_assignment_id
                                     AND source_region_review.decision_manifest_sha256 ~
                                         '^[0-9a-f]{{64}}$'
                                     AND source_region_review.idempotency_key_hash ~
                                         '^[0-9a-f]{{64}}$'
                                     AND {source_region_actor}
                                     AND {source_region_difference_completion_proof}
                                     AND {source_region_item_coverage}
                                     AND {source_region_item_rules}
                              ) = 1
                          )
                      )
               ) <> 1
               OR (
                   SELECT pg_catalog.count(*)
                     FROM public.stocktake_rounds AS next_round
                     JOIN public.state_transition_events AS recount_open_event
                       ON recount_open_event.aggregate_type = 'stocktake_task'
                      AND recount_open_event.aggregate_id =
                          recount_task.id::text
                      AND recount_open_event.from_status = 'recount_required'
                      AND recount_open_event.to_status = 'counting'
                      AND recount_open_event.reason = 'opening_recount_opened'
                      AND recount_open_event.actor_id =
                          recount_case.opened_by_user_id
                      AND recount_open_event.occurred_at =
                          recount_case.opened_at
                      AND recount_open_event.created_at =
                          recount_case.opened_at
                      AND recount_open_event.metadata_jsonb =
                          {historical_recount_event_metadata}
                    WHERE next_round.task_id = recount_task.id
                      AND next_round.round_no = recount_case.next_round_no
                      AND next_round.round_type = 'recount'
                      AND next_round.recount_case_id = recount_case.id
                      AND next_round.status IN ('counting', 'submitted')
                      AND next_round.started_at = recount_case.opened_at
                      AND next_round.created_at = recount_case.opened_at
                      AND {historical_recount_side_effect_proof}
               ) <> 1
               )
    ) THEN
        RAISE EXCEPTION '{EXISTING_ROWS_ERROR}';
    END IF;
END
$rsc_0052_existing_rows$
"""
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _function_catalog_values(*, hardened: bool) -> str:
    rows = (
        (
            ACTOR_ASSIGNMENT_SIGNATURE,
            ACTOR_ASSIGNMENT_FUNCTION,
            "boolean",
            "sql",
            "s",
            8,
            "text, uuid, uuid, bigint, timestamp with time zone, text, text, text",
            (
                "p_user_id,p_person_id,p_assignment_id,"
                "p_authorization_version,p_occurred_at,p_role_code,"
                "p_scope_type,p_scope_id"
            ),
            False,
            None,
            ACTOR_ASSIGNMENT_BODY_SHA256,
        ),
        (
            REVIEW_COMPLETION_SIGNATURE,
            REVIEW_COMPLETION_FUNCTION,
            "trigger",
            "plpgsql",
            "v",
            0,
            "",
            None,
            False,
            FIXED_SEARCH_PATH if hardened else None,
            REVIEW_COMPLETION_BODY_SHA256,
        ),
        (
            REVIEW_IMMUTABLE_SIGNATURE,
            REVIEW_IMMUTABLE_FUNCTION,
            "trigger",
            "plpgsql",
            "v",
            0,
            "",
            None,
            False,
            None,
            REVIEW_IMMUTABLE_BODY_SHA256,
        ),
        (
            GRAPH_SIGNATURE,
            GRAPH_FUNCTION,
            "boolean",
            "sql",
            "s",
            2,
            "uuid, uuid",
            "p_task_id,p_transaction_id",
            False,
            FIXED_SEARCH_PATH,
            GRAPH_BODY_SHA256,
        ),
        (
            COMMIT_SIGNATURE,
            COMMIT_FUNCTION,
            "trigger",
            "plpgsql",
            "v",
            0,
            "",
            None,
            hardened,
            FIXED_SEARCH_PATH,
            (
                FIXED_COMMIT_BODY_SHA256
                if hardened
                else LEGACY_COMMIT_BODY_SHA256
            ),
        ),
        (
            ACCOUNT_SIGNATURE,
            ACCOUNT_FUNCTION,
            "trigger",
            "plpgsql",
            "v",
            0,
            "",
            None,
            hardened,
            FIXED_SEARCH_PATH,
            (
                FIXED_ACCOUNT_BODY_SHA256
                if hardened
                else LEGACY_ACCOUNT_BODY_SHA256
            ),
        ),
    )
    rendered = []
    for (
        signature,
        function_name,
        return_type,
        language_name,
        volatility,
        argument_count,
        argument_types,
        argument_names,
        security_definer,
        search_path,
        body_sha256,
    ) in rows:
        rendered.append(
            "("
            + ", ".join(
                (
                    _sql_literal(signature),
                    _sql_literal(function_name),
                    _sql_literal(return_type),
                    _sql_literal(language_name),
                    _sql_literal(volatility),
                    str(argument_count),
                    _sql_literal(argument_types),
                    (
                        "NULL::text"
                        if argument_names is None
                        else f"{_sql_literal(argument_names)}::text"
                    ),
                    "TRUE" if security_definer else "FALSE",
                    (
                        "NULL::text"
                        if search_path is None
                        else f"{_sql_literal(search_path)}::text"
                    ),
                    _sql_literal(body_sha256),
                )
            )
            + ")"
        )
    return ",\n        ".join(rendered)


def _trigger_catalog_values() -> str:
    return ",\n        ".join(
        "("
        + ", ".join(
            (
                _sql_literal(table_name),
                _sql_literal(trigger_name),
                _sql_literal(function_signature),
                str(trigger_type),
            )
        )
        + ")"
        for table_name, trigger_name, function_signature, trigger_type
        in TRIGGER_CATALOG
    )


def _review_guard_trigger_catalog_values() -> str:
    return ",\n        ".join(
        "("
        + ", ".join(
            (
                _sql_literal(table_name),
                _sql_literal(trigger_name),
                _sql_literal(function_signature),
                str(trigger_type),
            )
        )
        + ")"
        for table_name, trigger_name, function_signature, trigger_type
        in REVIEW_GUARD_TRIGGER_CATALOG
    )


def _verify_catalog(*, hardened: bool, phase: str) -> None:
    function_values = _function_catalog_values(hardened=hardened)
    trigger_values = _trigger_catalog_values()
    review_guard_trigger_values = _review_guard_trigger_catalog_values()
    function_literals = ", ".join(
        f"{_sql_literal(signature)}::pg_catalog.regprocedure"
        for signature in (
            ACTOR_ASSIGNMENT_SIGNATURE,
            GRAPH_SIGNATURE,
            COMMIT_SIGNATURE,
            ACCOUNT_SIGNATURE,
        )
    )
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0052_catalog$
DECLARE
    api_oid oid;
    migrator_oid oid;
    function_oid oid;
    expected_function record;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;

    SELECT role_row.oid
      INTO migrator_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{MIGRATION_ROLE}';
    SELECT role_row.oid
      INTO api_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{PRODUCTION_API_ROLE}';
    IF migrator_oid IS NULL OR api_oid IS NULL THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: required role is missing';
    END IF;

    FOR expected_function IN
        SELECT *
          FROM (VALUES
        {function_values}
          ) AS expected(
              signature,
              function_name,
              return_type,
              language_name,
              volatility,
              argument_count,
              argument_types,
              argument_names,
              security_definer,
              search_path,
              body_sha256
          )
    LOOP
        function_oid := pg_catalog.to_regprocedure(expected_function.signature);
        IF function_oid IS NULL OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_proc AS function_row
              JOIN pg_catalog.pg_namespace AS schema_row
                ON schema_row.oid = function_row.pronamespace
             WHERE function_row.proname = expected_function.function_name
        ) <> 1 THEN
            RAISE EXCEPTION
                '{CATALOG_ERROR}: {escaped_phase}: function identity mismatch';
        END IF;

        IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
              JOIN pg_catalog.pg_language AS language_row
                ON language_row.oid = function_row.prolang
             WHERE function_row.oid = function_oid
               AND function_row.proowner = migrator_oid
               AND function_row.prokind = 'f'
               AND function_row.prorettype =
                   pg_catalog.to_regtype(expected_function.return_type)
               AND NOT function_row.proretset
               AND function_row.pronargs = expected_function.argument_count
               AND pg_catalog.oidvectortypes(function_row.proargtypes) =
                   expected_function.argument_types
               AND pg_catalog.array_to_string(
                       function_row.proargnames,
                       ','
                   ) IS NOT DISTINCT FROM expected_function.argument_names
               AND function_row.proallargtypes IS NULL
               AND function_row.proargmodes IS NULL
               AND function_row.pronargdefaults = 0
               AND function_row.proargdefaults IS NULL
               AND function_row.provariadic = 0
               AND language_row.lanname = expected_function.language_name
               AND function_row.provolatile = expected_function.volatility
               AND NOT function_row.proisstrict
               AND NOT function_row.proleakproof
               AND function_row.proparallel = 'u'
               AND function_row.prosecdef IS NOT DISTINCT FROM
                   expected_function.security_definer
               AND function_row.proconfig IS NOT DISTINCT FROM CASE
                   WHEN expected_function.search_path IS NULL
                   THEN NULL::text[]
                   ELSE ARRAY[expected_function.search_path]::text[]
               END
               AND pg_catalog.encode(
                       pg_catalog.sha256(
                           pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                       ),
                       'hex'
                   ) = expected_function.body_sha256
        ) THEN
            RAISE EXCEPTION
                '{CATALOG_ERROR}: {escaped_phase}: function definition mismatch';
        END IF;

        IF EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     function_row.proacl,
                     pg_catalog.acldefault('f', function_row.proowner)
                 )
             ) AS function_acl
             WHERE function_row.oid = function_oid
               AND (
                   function_acl.privilege_type <> 'EXECUTE'
                   OR function_acl.grantee <> migrator_oid
                   OR function_acl.grantor <> migrator_oid
                   OR function_acl.is_grantable
               )
        ) OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_proc AS function_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     function_row.proacl,
                     pg_catalog.acldefault('f', function_row.proowner)
                 )
             ) AS function_acl
             WHERE function_row.oid = function_oid
               AND function_acl.privilege_type = 'EXECUTE'
               AND function_acl.grantee = migrator_oid
               AND function_acl.grantor = migrator_oid
               AND NOT function_acl.is_grantable
        ) <> 1 OR pg_catalog.has_function_privilege(
            api_oid,
            function_oid,
            'EXECUTE'
        ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     function_row.proacl,
                     pg_catalog.acldefault('f', function_row.proowner)
                 )
             ) AS function_acl
             WHERE function_row.oid = function_oid
               AND function_acl.grantee = 0
        ) THEN
            RAISE EXCEPTION
                '{CATALOG_ERROR}: {escaped_phase}: function ACL mismatch';
        END IF;
    END LOOP;

    IF EXISTS (
        WITH expected_trigger(
            table_name,
            trigger_name,
            function_signature,
            trigger_type
        ) AS (VALUES
        {trigger_values}
        )
        SELECT 1
          FROM expected_trigger AS expected
         WHERE (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_trigger AS trigger_row
             WHERE NOT trigger_row.tgisinternal
               AND trigger_row.tgname = expected.trigger_name
               AND trigger_row.tgrelid = pg_catalog.to_regclass(
                   pg_catalog.format('public.%I', expected.table_name)
               )
               AND trigger_row.tgfoid = pg_catalog.to_regprocedure(
                   expected.function_signature
               )
               AND trigger_row.tgenabled = 'A'
               AND trigger_row.tgtype = expected.trigger_type
               AND trigger_row.tgconstraint <> 0
               AND trigger_row.tgdeferrable
               AND trigger_row.tginitdeferred
               AND trigger_row.tgconstrrelid = 0
               AND trigger_row.tgconstrindid = 0
               AND trigger_row.tgparentid = 0
               AND trigger_row.tgqual IS NULL
               AND trigger_row.tgoldtable IS NULL
               AND trigger_row.tgnewtable IS NULL
               AND trigger_row.tgnargs = 0
               AND trigger_row.tgattr = ''::pg_catalog.int2vector
         ) <> 1 OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_trigger AS trigger_row
             WHERE NOT trigger_row.tgisinternal
               AND trigger_row.tgname = expected.trigger_name
         ) <> 1
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid IN ({function_literals})
    ) <> {len(TRIGGER_CATALOG)} THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: trigger binding mismatch';
    END IF;

    IF EXISTS (
        WITH expected_trigger(
            table_name,
            trigger_name,
            function_signature,
            trigger_type
        ) AS (VALUES
        {review_guard_trigger_values}
        )
        SELECT 1
          FROM expected_trigger AS expected
         WHERE (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_trigger AS trigger_row
             WHERE NOT trigger_row.tgisinternal
               AND trigger_row.tgname = expected.trigger_name
               AND trigger_row.tgrelid = pg_catalog.to_regclass(
                   pg_catalog.format('public.%I', expected.table_name)
               )
               AND trigger_row.tgfoid = pg_catalog.to_regprocedure(
                   expected.function_signature
               )
               AND trigger_row.tgenabled = 'A'
               AND trigger_row.tgtype = expected.trigger_type
               AND trigger_row.tgconstraint = 0
               AND NOT trigger_row.tgdeferrable
               AND NOT trigger_row.tginitdeferred
               AND trigger_row.tgconstrrelid = 0
               AND trigger_row.tgconstrindid = 0
               AND trigger_row.tgparentid = 0
               AND trigger_row.tgqual IS NULL
               AND trigger_row.tgoldtable IS NULL
               AND trigger_row.tgnewtable IS NULL
               AND trigger_row.tgnargs = 0
               AND trigger_row.tgattr = ''::pg_catalog.int2vector
         ) <> 1 OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_trigger AS trigger_row
             WHERE NOT trigger_row.tgisinternal
               AND trigger_row.tgname = expected.trigger_name
         ) <> 1
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid = pg_catalog.to_regprocedure(
               '{REVIEW_COMPLETION_SIGNATURE}'
           )
    ) <> 1 OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid = pg_catalog.to_regprocedure(
               '{REVIEW_IMMUTABLE_SIGNATURE}'
           )
    ) <> 4 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: review guard binding mismatch';
    END IF;
END
$rsc_0052_catalog$
"""
    )


def _replace_function_body(
    *,
    signature: str,
    expected_body_sha256: str,
    source_fragment: str,
    replacement_fragment: str,
    phase: str,
) -> None:
    expected_replacements = {
        (
            COMMIT_SIGNATURE,
            LEGACY_COMMIT_BODY_SHA256,
            LEGACY_TASK_BRANCH,
            FIXED_TASK_BRANCH,
        ),
        (
            COMMIT_SIGNATURE,
            FIXED_COMMIT_BODY_SHA256,
            FIXED_TASK_BRANCH,
            LEGACY_TASK_BRANCH,
        ),
        (
            ACCOUNT_SIGNATURE,
            LEGACY_ACCOUNT_BODY_SHA256,
            LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT,
            FIXED_ACCOUNT_PRINCIPAL_FRAGMENT,
        ),
        (
            ACCOUNT_SIGNATURE,
            FIXED_ACCOUNT_BODY_SHA256,
            FIXED_ACCOUNT_PRINCIPAL_FRAGMENT,
            LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT,
        ),
    }
    if (
        signature,
        expected_body_sha256,
        source_fragment,
        replacement_fragment,
    ) not in expected_replacements:
        raise ValueError("unsupported opening terminal guard replacement")

    escaped_phase = phase.replace("'", "''")
    escaped_signature = signature.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0052_replace$
DECLARE
    function_oid oid;
    function_source text;
    function_definition text;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{REPLACEMENT_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;

    function_oid := pg_catalog.to_regprocedure('{escaped_signature}');
    SELECT function_row.prosrc
      INTO function_source
      FROM pg_catalog.pg_proc AS function_row
     WHERE function_row.oid = function_oid
       AND pg_catalog.encode(
               pg_catalog.sha256(
                   pg_catalog.convert_to(function_row.prosrc, 'UTF8')
               ),
               'hex'
           ) = '{expected_body_sha256}';
    IF function_source IS NULL OR (
        pg_catalog.length(function_source)
        - pg_catalog.length(
            pg_catalog.replace(
                function_source,
                $rsc_0052_source${source_fragment}$rsc_0052_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0052_source${source_fragment}$rsc_0052_source$
    ) <> 1 OR pg_catalog.strpos(
        function_source,
        $rsc_0052_replacement${replacement_fragment}$rsc_0052_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{REPLACEMENT_ERROR}: {escaped_phase}: source mismatch';
    END IF;

    function_definition := pg_catalog.pg_get_functiondef(function_oid);
    IF (
        pg_catalog.length(function_definition)
        - pg_catalog.length(
            pg_catalog.replace(
                function_definition,
                $rsc_0052_source${source_fragment}$rsc_0052_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0052_source${source_fragment}$rsc_0052_source$
    ) <> 1 OR pg_catalog.strpos(
        function_definition,
        $rsc_0052_replacement${replacement_fragment}$rsc_0052_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{REPLACEMENT_ERROR}: {escaped_phase}: definition mismatch';
    END IF;

    EXECUTE pg_catalog.replace(
        function_definition,
        $rsc_0052_source${source_fragment}$rsc_0052_source$,
        $rsc_0052_replacement${replacement_fragment}$rsc_0052_replacement$
    );
END
$rsc_0052_replace$
"""
    )


def _set_caller_security(*, security_definer: bool) -> None:
    security = "DEFINER" if security_definer else "INVOKER"
    for signature in CALLER_SIGNATURES:
        op.execute(f"ALTER FUNCTION {signature} SECURITY {security}")


def _replace_oam_runtime_ready_function(expected_revision: str) -> None:
    op.execute(_oam_runtime_ready_function_sql(expected_revision))


def _oam_runtime_ready_function_sql(expected_revision: str) -> str:
    if expected_revision not in {PREVIOUS_SCHEMA_REVISION, revision}:
        raise ValueError("unsupported OAM runtime readiness revision")
    return f"""
CREATE OR REPLACE FUNCTION public.{OAM_RUNTIME_READY_FUNCTION}()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT (
        SELECT pg_catalog.count(*) = 1
           AND pg_catalog.min(version_num) = '{expected_revision}'
          FROM public.alembic_version
    ) AND CASE session_user::text
        WHEN 'edge_inbox' THEN EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS ingress
             WHERE ingress.enabled
               AND ingress.principal_name = session_user::text
               AND ingress.capability = 'edge_ingress'
               AND (
                   ingress.entity_type <> 'work_order'
                   OR EXISTS (
                       SELECT 1
                         FROM public.source_systems AS source
                        WHERE source.code = ingress.source_system
                          AND source.mode = 'read_only'
                          AND source.enabled
                          AND source.configuration_jsonb =
                              pg_catalog.jsonb_build_object(
                                  'projection_schema',
                                  'rsc.oam_work_order_projection.v1',
                                  'edge_source_instance', ingress.source_instance,
                                  'work_order_company_id', ingress.company_id,
                                  'work_order_org_code', ingress.org_code,
                                  'work_order_scope_key', ingress.scope_key
                              )
                   )
               )
        )
        WHEN 'star_oam_projector' THEN EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS write_work_order
              JOIN public.oam_sync_scope_bindings AS read_work_order
                ON read_work_order.source_system = write_work_order.source_system
               AND read_work_order.source_instance = write_work_order.source_instance
               AND read_work_order.scope_key = write_work_order.scope_key
               AND read_work_order.company_id = write_work_order.company_id
               AND read_work_order.org_code = write_work_order.org_code
               AND read_work_order.enabled
               AND read_work_order.principal_name = write_work_order.principal_name
               AND read_work_order.capability = 'projector_read'
               AND read_work_order.entity_type = 'work_order'
              JOIN public.oam_sync_scope_bindings AS read_employee
                ON read_employee.source_system = write_work_order.source_system
               AND read_employee.source_instance = write_work_order.source_instance
               AND read_employee.scope_key = write_work_order.scope_key
               AND read_employee.company_id = write_work_order.company_id
               AND read_employee.org_code = write_work_order.org_code
               AND read_employee.enabled
               AND read_employee.principal_name = write_work_order.principal_name
               AND read_employee.capability = 'projector_read'
               AND read_employee.entity_type = 'employee'
              JOIN public.source_systems AS source
                ON source.code = write_work_order.source_system
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', write_work_order.source_instance,
                   'work_order_company_id', write_work_order.company_id,
                   'work_order_org_code', write_work_order.org_code,
                   'work_order_scope_key', write_work_order.scope_key
               )
             WHERE write_work_order.enabled
               AND write_work_order.principal_name = session_user::text
               AND write_work_order.capability = 'projector_write'
               AND write_work_order.entity_type = 'work_order'
        ) AND (
            SELECT pg_catalog.count(*) = 3
              FROM public.oam_sync_scope_bindings AS binding
             WHERE binding.enabled
               AND binding.principal_name = session_user::text
        )
        ELSE false
    END
$$
"""
