"""Harden material-request approval current-step and return causality.

Revision ID: 20260901_0030
Revises: 20260831_0029
Create Date: 2026-09-01

This revision is deliberately structural.  It grants no runtime DML access and
does not mount an HTTP route.  Existing 0029 approval data is accepted only
when its current step and route graph can be reconstructed uniquely; otherwise
the migration fails closed instead of inventing approval history.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260901_0030"
down_revision: Union[str, Sequence[str], None] = "20260831_0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


GUARD_ERROR = "formal approval causality invariant violated"
UPGRADE_BLOCKER = (
    "0030 approval causality upgrade failed: 0029 graph is not uniquely reconstructable"
)
DOWNGRADE_BLOCKER = "cannot downgrade 0030 while approval return/rework facts exist"
CURRENT_STATUSES = (
    "'open', 'awaiting_external_evidence', 'evidence_pending_verification'"
)
TERMINAL_INSTANCE_STATUSES = (
    "'returned', 'completed', 'rejected', 'withdrawn', 'cancelled', 'superseded'"
)

PG_VALIDATE_FUNCTION = "rsc_validate_approval_instance_causality_0030"
PG_DISPATCH_FUNCTION = "rsc_dispatch_approval_causality_0030"
PG_STEP_GUARD_FUNCTION = "rsc_guard_approval_step_write_0030"
PG_CANDIDATE_GUARD_FUNCTION = "rsc_guard_approval_candidate_write_0030"
PG_ACTION_GUARD_FUNCTION = "rsc_guard_approval_action_write_0030"
PG_DECISION_GUARD_FUNCTION = "rsc_guard_approval_decision_write_0030"
PG_RETURN_GUARD_FUNCTION = "rsc_guard_approval_return_fact_0030"
PG_IMMUTABLE_FUNCTION = "rsc_guard_approval_return_fact_immutable_0030"
PG_EXTERNAL_REGISTRATION_FUNCTION_0029 = "rsc_guard_external_registration_core_0029"
PG_EXTERNAL_LINE_FUNCTION_0029 = "rsc_guard_external_registration_quantity_0029"


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0030 supports only PostgreSQL and SQLite test databases")
    if dialect == "postgresql":
        _upgrade_postgresql()
    else:
        if context.is_offline_mode():
            raise RuntimeError("0030 SQLite migration requires an online connection")
        _upgrade_sqlite()


def downgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0030 supports only PostgreSQL and SQLite test databases")
    if context.is_offline_mode() and dialect != "postgresql":
        raise RuntimeError("0030 SQLite downgrade requires an online connection")
    _require_safe_downgrade(dialect)
    if dialect == "postgresql":
        _drop_postgresql_guards()
        op.drop_table("approval_return_line_facts")
        op.drop_constraint(
            "fk_approval_actions_step_instance_0030",
            "approval_actions",
            type_="foreignkey",
        )
        op.drop_constraint(
            "uq_approval_actions_causal_identity_0030",
            "approval_actions",
            type_="unique",
        )
        op.drop_constraint(
            "fk_approval_instances_current_step_0030",
            "approval_instances",
            type_="foreignkey",
        )
        op.drop_constraint(
            "ck_approval_instances_current_pointer_0030",
            "approval_instances",
            type_="check",
        )
        op.drop_index(
            "uq_approval_steps_one_current_0030", table_name="approval_steps"
        )
        op.drop_constraint(
            "fk_approval_steps_reopened_from_instance_0030",
            "approval_steps",
            type_="foreignkey",
        )
        op.drop_constraint(
            "fk_approval_steps_supersedes_instance_0030",
            "approval_steps",
            type_="foreignkey",
        )
        op.drop_constraint(
            "ck_approval_steps_rework_pointer_0030",
            "approval_steps",
            type_="check",
        )
        op.drop_constraint(
            "uq_approval_steps_causal_identity_0030",
            "approval_steps",
            type_="unique",
        )
        op.drop_column("approval_instances", "current_step_id")
        op.drop_column("approval_steps", "reopened_from_step_id")
        op.drop_column("approval_steps", "supersedes_step_id")
        return
    _downgrade_sqlite()


def _upgrade_postgresql() -> None:
    op.execute("LOCK TABLE public.approval_instances IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE public.approval_steps IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE public.approval_actions IN ACCESS EXCLUSIVE MODE")
    _postgresql_preflight()
    op.add_column(
        "approval_steps", sa.Column("supersedes_step_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "approval_steps", sa.Column("reopened_from_step_id", sa.Uuid(), nullable=True)
    )
    # 0029 reused the request submission attempt on steps.  Because 0029 had no
    # same-step rework support, a unique row per (instance, step_no) can be
    # normalized losslessly to the new per-step attempt sequence starting at 1.
    op.execute("UPDATE public.approval_steps SET attempt_no = 1")
    _add_step_constraints()
    op.add_column(
        "approval_instances", sa.Column("current_step_id", sa.Uuid(), nullable=True)
    )
    op.execute(
        f"""
UPDATE public.approval_instances AS instance
   SET current_step_id = step.id
  FROM public.approval_steps AS step
 WHERE instance.status = 'active'
   AND step.instance_id = instance.id
   AND step.step_no = instance.current_step_no
   AND step.status IN ({CURRENT_STATUSES})
"""
    )
    _add_instance_constraints()
    _add_action_constraint()
    _create_return_fact_table()
    _create_postgresql_guards()


def _postgresql_preflight() -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.approval_steps
         GROUP BY instance_id, step_no
        HAVING count(*) <> 1
    ) OR EXISTS (
        SELECT 1
          FROM public.approval_instances AS instance
         WHERE (instance.status = 'active' AND (
                   instance.current_step_no IS NULL
                   OR (SELECT count(*)
                         FROM public.approval_steps AS step
                        WHERE step.instance_id = instance.id
                          AND step.status IN ({CURRENT_STATUSES})) <> 1
                   OR (SELECT count(*)
                         FROM public.approval_steps AS step
                        WHERE step.instance_id = instance.id
                          AND step.step_no = instance.current_step_no
                          AND step.status IN ({CURRENT_STATUSES})) <> 1
               ))
            OR (instance.status <> 'active' AND instance.current_step_no IS NOT NULL)
            OR NOT EXISTS (
                   SELECT 1 FROM public.material_request_lines AS line
                    WHERE line.revision_id = instance.request_revision_id
                      AND line.request_id = instance.request_id
               )
    ) OR EXISTS (
        SELECT 1
          FROM public.approval_steps AS step
          JOIN public.approval_instances AS instance ON instance.id = step.instance_id
          LEFT JOIN public.approval_route_step_defs AS definition
            ON definition.route_version_id = instance.route_version_id
           AND definition.step_no = step.step_no
         WHERE definition.id IS NULL OR definition.source_mode <> step.source_mode
    ) OR EXISTS (
        SELECT 1
          FROM public.approval_instances AS instance
          JOIN public.approval_route_step_defs AS definition
            ON definition.route_version_id = instance.route_version_id
         WHERE NOT EXISTS (
             SELECT 1 FROM public.approval_steps AS step
              WHERE step.instance_id = instance.id
                AND step.step_no = definition.step_no
         )
    ) OR EXISTS (
        SELECT 1
          FROM public.approval_steps AS step
          LEFT JOIN public.approval_steps AS predecessor
            ON predecessor.id = step.predecessor_step_id
         WHERE (step.step_no = 1 AND step.predecessor_step_id IS NOT NULL)
            OR (step.step_no > 1 AND (
                   predecessor.id IS NULL
                   OR predecessor.instance_id <> step.instance_id
                   OR predecessor.step_no <> step.step_no - 1
               ))
    ) OR EXISTS (
        SELECT 1
          FROM public.approval_actions AS action
          JOIN public.approval_steps AS step ON step.id = action.step_id
         WHERE step.instance_id <> action.instance_id
    ) OR EXISTS (
        SELECT 1
          FROM public.approval_steps AS step
          JOIN public.approval_instances AS instance ON instance.id = step.instance_id
         WHERE step.status = 'rejected'
           AND (
               (SELECT count(*) FROM public.approval_step_line_decisions AS decision
                 WHERE decision.step_id = step.id) = 0
               OR (SELECT count(*) FROM public.approval_step_line_decisions AS decision
                    WHERE decision.step_id = step.id)
                  <>
                  (SELECT count(*) FROM (
                      SELECT line.id AS request_line_id
                        FROM public.material_request_lines AS line
                       WHERE step.step_no = 1
                         AND line.request_id = instance.request_id
                         AND line.revision_id = instance.request_revision_id
                      UNION ALL
                      SELECT predecessor.request_line_id
                        FROM public.approval_step_line_decisions AS predecessor
                       WHERE step.step_no > 1
                         AND predecessor.step_id = step.predecessor_step_id
                         AND predecessor.approved_qty > 0
                  ) AS expected)
               OR EXISTS (
                   SELECT 1 FROM public.approval_step_line_decisions AS decision
                    WHERE decision.step_id = step.id
                      AND (decision.approved_qty <> 0
                           OR decision.rejected_qty <> decision.input_qty
                           OR btrim(decision.reason) = '')
               )
               OR (SELECT count(*) FROM public.approval_actions AS action
                    WHERE action.instance_id = step.instance_id
                      AND action.step_id = step.id
                      AND ((step.source_mode = 'external_registration'
                            AND action.action = 'verify_external_accept')
                           OR (step.source_mode <> 'external_registration'
                               AND action.action = 'reject'))) <> 1
           )
    ) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END $$
"""
    )


def _sqlite_preflight() -> None:
    connection = op.get_bind()
    checks = (
        """
SELECT 1 FROM approval_steps
 GROUP BY instance_id, step_no HAVING count(*) <> 1 LIMIT 1
""",
        f"""
SELECT 1 FROM approval_instances AS instance
 WHERE (instance.status = 'active' AND (
            instance.current_step_no IS NULL
            OR (SELECT count(*) FROM approval_steps AS step
                 WHERE step.instance_id = instance.id
                   AND step.status IN ({CURRENT_STATUSES})) <> 1
            OR (SELECT count(*) FROM approval_steps AS step
                 WHERE step.instance_id = instance.id
                   AND step.step_no = instance.current_step_no
                   AND step.status IN ({CURRENT_STATUSES})) <> 1
       ))
    OR (instance.status <> 'active' AND instance.current_step_no IS NOT NULL)
    OR NOT EXISTS (
           SELECT 1 FROM material_request_lines AS line
            WHERE line.revision_id = instance.request_revision_id
              AND line.request_id = instance.request_id
       )
 LIMIT 1
""",
        """
SELECT 1
  FROM approval_steps AS step
  JOIN approval_instances AS instance ON instance.id = step.instance_id
  LEFT JOIN approval_route_step_defs AS definition
    ON definition.route_version_id = instance.route_version_id
   AND definition.step_no = step.step_no
 WHERE definition.id IS NULL OR definition.source_mode <> step.source_mode
 LIMIT 1
""",
        """
SELECT 1
  FROM approval_instances AS instance
  JOIN approval_route_step_defs AS definition
    ON definition.route_version_id = instance.route_version_id
 WHERE NOT EXISTS (
       SELECT 1 FROM approval_steps AS step
        WHERE step.instance_id = instance.id
          AND step.step_no = definition.step_no
 )
 LIMIT 1
""",
        """
SELECT 1
  FROM approval_steps AS step
  LEFT JOIN approval_steps AS predecessor ON predecessor.id = step.predecessor_step_id
 WHERE (step.step_no = 1 AND step.predecessor_step_id IS NOT NULL)
    OR (step.step_no > 1 AND (
           predecessor.id IS NULL
           OR predecessor.instance_id <> step.instance_id
           OR predecessor.step_no <> step.step_no - 1
       ))
 LIMIT 1
""",
        """
SELECT 1 FROM approval_actions AS action
 JOIN approval_steps AS step ON step.id = action.step_id
 WHERE step.instance_id <> action.instance_id LIMIT 1
""",
        """
SELECT 1
  FROM approval_steps AS step
  JOIN approval_instances AS instance ON instance.id = step.instance_id
 WHERE step.status = 'rejected'
   AND (
       (SELECT count(*) FROM approval_step_line_decisions AS decision
         WHERE decision.step_id = step.id) = 0
       OR (SELECT count(*) FROM approval_step_line_decisions AS decision
            WHERE decision.step_id = step.id)
          <>
          (SELECT count(*) FROM (
              SELECT line.id AS request_line_id
                FROM material_request_lines AS line
               WHERE step.step_no = 1
                 AND line.request_id = instance.request_id
                 AND line.revision_id = instance.request_revision_id
              UNION ALL
              SELECT predecessor.request_line_id
                FROM approval_step_line_decisions AS predecessor
               WHERE step.step_no > 1
                 AND predecessor.step_id = step.predecessor_step_id
                 AND predecessor.approved_qty > 0
          ) AS expected)
       OR EXISTS (
           SELECT 1 FROM approval_step_line_decisions AS decision
            WHERE decision.step_id = step.id
              AND (decision.approved_qty <> 0
                   OR decision.rejected_qty <> decision.input_qty
                   OR trim(decision.reason) = '')
       )
       OR (SELECT count(*) FROM approval_actions AS action
            WHERE action.instance_id = step.instance_id
              AND action.step_id = step.id
              AND ((step.source_mode = 'external_registration'
                    AND action.action = 'verify_external_accept')
                   OR (step.source_mode <> 'external_registration'
                       AND action.action = 'reject'))) <> 1
   )
 LIMIT 1
""",
    )
    for statement in checks:
        if connection.exec_driver_sql(statement).first() is not None:
            raise RuntimeError(UPGRADE_BLOCKER)


def _upgrade_sqlite() -> None:
    _sqlite_preflight()
    preserved_triggers = _capture_and_drop_sqlite_dependency_triggers()
    # Recreating approval_steps also removes its 0029 delete trigger; 0030
    # installs a stronger replacement below.
    op.execute("UPDATE approval_steps SET attempt_no = 1")
    with op.batch_alter_table("approval_steps", recreate="always") as batch:
        batch.add_column(sa.Column("supersedes_step_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("reopened_from_step_id", sa.Uuid(), nullable=True))
        batch.create_unique_constraint(
            "uq_approval_steps_causal_identity_0030",
            ["id", "instance_id", "step_no"],
        )
        batch.create_check_constraint(
            "ck_approval_steps_rework_pointer_0030",
            "(attempt_no = 1 AND supersedes_step_id IS NULL "
            "AND reopened_from_step_id IS NULL) OR "
            "(attempt_no > 1 AND supersedes_step_id IS NOT NULL)",
        )
        batch.create_foreign_key(
            "fk_approval_steps_supersedes_instance_0030",
            "approval_steps",
            ["supersedes_step_id", "instance_id"],
            ["id", "instance_id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_approval_steps_reopened_from_instance_0030",
            "approval_steps",
            ["reopened_from_step_id", "instance_id"],
            ["id", "instance_id"],
            ondelete="RESTRICT",
        )
    op.add_column(
        "approval_instances", sa.Column("current_step_id", sa.Uuid(), nullable=True)
    )
    op.execute(
        f"""
UPDATE approval_instances
   SET current_step_id = (
       SELECT step.id FROM approval_steps AS step
        WHERE step.instance_id = approval_instances.id
          AND step.step_no = approval_instances.current_step_no
          AND step.status IN ({CURRENT_STATUSES})
   )
 WHERE status = 'active'
"""
    )
    with op.batch_alter_table("approval_instances", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_approval_instances_current_pointer_0030",
            "(status = 'active' AND current_step_no IS NOT NULL "
            "AND current_step_id IS NOT NULL) OR "
            f"(status IN ({TERMINAL_INSTANCE_STATUSES}) "
            "AND current_step_no IS NULL AND current_step_id IS NULL)",
        )
        batch.create_foreign_key(
            "fk_approval_instances_current_step_0030",
            "approval_steps",
            ["current_step_id", "id", "current_step_no"],
            ["id", "instance_id", "step_no"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        )
    with op.batch_alter_table("approval_actions", recreate="always") as batch:
        batch.create_unique_constraint(
            "uq_approval_actions_causal_identity_0030", ["id", "instance_id", "step_id"]
        )
        batch.create_foreign_key(
            "fk_approval_actions_step_instance_0030",
            "approval_steps",
            ["step_id", "instance_id"],
            ["id", "instance_id"],
            ondelete="RESTRICT",
        )
    _recreate_sqlite_triggers(preserved_triggers)
    _create_current_index()
    _create_return_fact_table()
    _create_sqlite_guards()


def _add_step_constraints() -> None:
    op.create_unique_constraint(
        "uq_approval_steps_causal_identity_0030",
        "approval_steps",
        ["id", "instance_id", "step_no"],
    )
    op.create_check_constraint(
        "ck_approval_steps_rework_pointer_0030",
        "approval_steps",
        "(attempt_no = 1 AND supersedes_step_id IS NULL "
        "AND reopened_from_step_id IS NULL) OR "
        "(attempt_no > 1 AND supersedes_step_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_approval_steps_supersedes_instance_0030",
        "approval_steps",
        "approval_steps",
        ["supersedes_step_id", "instance_id"],
        ["id", "instance_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_approval_steps_reopened_from_instance_0030",
        "approval_steps",
        "approval_steps",
        ["reopened_from_step_id", "instance_id"],
        ["id", "instance_id"],
        ondelete="RESTRICT",
    )
    _create_current_index()


def _create_current_index() -> None:
    predicate = sa.text(
        "status IN ('open', 'awaiting_external_evidence', "
        "'evidence_pending_verification')"
    )
    op.create_index(
        "uq_approval_steps_one_current_0030",
        "approval_steps",
        ["instance_id"],
        unique=True,
        postgresql_where=predicate,
        sqlite_where=predicate,
    )


def _add_instance_constraints() -> None:
    op.create_check_constraint(
        "ck_approval_instances_current_pointer_0030",
        "approval_instances",
        "(status = 'active' AND current_step_no IS NOT NULL "
        "AND current_step_id IS NOT NULL) OR "
        f"(status IN ({TERMINAL_INSTANCE_STATUSES}) "
        "AND current_step_no IS NULL AND current_step_id IS NULL)",
    )
    op.create_foreign_key(
        "fk_approval_instances_current_step_0030",
        "approval_instances",
        "approval_steps",
        ["current_step_id", "id", "current_step_no"],
        ["id", "instance_id", "step_no"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )


def _add_action_constraint() -> None:
    op.create_unique_constraint(
        "uq_approval_actions_causal_identity_0030",
        "approval_actions",
        ["id", "instance_id", "step_id"],
    )
    op.create_foreign_key(
        "fk_approval_actions_step_instance_0030",
        "approval_actions",
        "approval_steps",
        ["step_id", "instance_id"],
        ["id", "instance_id"],
        ondelete="RESTRICT",
    )


def _create_return_fact_table() -> None:
    op.create_table(
        "approval_return_line_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("return_action_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("returned_from_step_id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(24), nullable=False),
        sa.Column("target_step_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_revision_id", sa.Uuid(), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=False),
        sa.Column("returned_step_input_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("target_step_max_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("required_review_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(target_kind = 'requester_revision' AND target_step_id IS NULL) OR "
            "(target_kind = 'approval_step' AND target_step_id IS NOT NULL)",
            name="ck_approval_return_line_facts_target_0030",
        ),
        sa.CheckConstraint(
            "returned_step_input_qty > 0 AND target_step_max_qty > 0 "
            "AND required_review_qty > 0 "
            "AND required_review_qty <= target_step_max_qty",
            name="ck_approval_return_line_facts_quantities_0030",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_approval_return_line_facts_reason_0030",
        ),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_approval_return_line_facts_authorization_0030",
        ),
        sa.CheckConstraint(
            "occurred_at = created_at",
            name="ck_approval_return_line_facts_time_0030",
        ),
        sa.ForeignKeyConstraint(
            ["return_action_id", "instance_id", "returned_from_step_id"],
            ["approval_actions.id", "approval_actions.instance_id", "approval_actions.step_id"],
            name="fk_approval_return_line_facts_action_0030",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["returned_from_step_id", "instance_id"],
            ["approval_steps.id", "approval_steps.instance_id"],
            name="fk_approval_return_line_facts_source_0030",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_step_id", "instance_id"],
            ["approval_steps.id", "approval_steps.instance_id"],
            name="fk_approval_return_line_facts_target_step_0030",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_line_id", "request_id", "request_revision_id"],
            ["material_request_lines.id", "material_request_lines.request_id", "material_request_lines.revision_id"],
            name="fk_approval_return_line_facts_request_line_0030",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["actor_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "return_action_id",
            "request_line_id",
            name="uq_approval_return_line_facts_action_line_0030",
        ),
    )
    op.create_index(
        "ix_approval_return_line_facts_instance_0030",
        "approval_return_line_facts",
        ["instance_id", "occurred_at"],
    )
    op.create_index(
        "ix_approval_return_line_facts_target_0030",
        "approval_return_line_facts",
        ["target_step_id"],
    )
    op.create_index(
        "ix_approval_return_line_facts_request_line_0030",
        "approval_return_line_facts",
        ["request_line_id"],
    )


def _create_postgresql_guards() -> None:
    _replace_postgresql_external_reject_guards(allow_reject_lines=True)
    _create_postgresql_validator()
    _create_postgresql_row_guards()
    _create_postgresql_triggers()


def _replace_postgresql_external_reject_guards(*, allow_reject_lines: bool) -> None:
    accepted_actions = (
        "('approve', 'partial_approve', 'reject')"
        if allow_reject_lines
        else "('approve', 'partial_approve')"
    )
    reject_validation = (
        """
           OR (NEW.external_action = 'reject' AND EXISTS (
               SELECT 1 FROM public.approval_external_registration_lines AS line
                WHERE line.registration_id = NEW.id
                  AND (line.approved_qty <> 0
                       OR line.rejected_qty <> line.input_qty
                       OR btrim(line.reason) = '')
           ))
"""
        if allow_reject_lines
        else ""
    )
    no_line_actions = "('return')" if allow_reject_lines else "('reject', 'return')"
    op.execute(
        f"""
CREATE OR REPLACE FUNCTION public.{PG_EXTERNAL_REGISTRATION_FUNCTION_0029}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    predecessor_id uuid;
    requester_user text;
    requester_person uuid;
    expected_count bigint;
    actual_count bigint;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT step.predecessor_step_id, request.requester_user_id,
           request.requester_person_id
      INTO predecessor_id, requester_user, requester_person
      FROM public.approval_steps AS step
      JOIN public.approval_instances AS instance ON instance.id = step.instance_id
      JOIN public.material_requests AS request ON request.id = instance.request_id
     WHERE step.id = COALESCE(NEW.step_id, OLD.step_id)
       AND step.step_no = 3
       AND step.source_mode = 'external_registration';
    IF NOT FOUND THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'pending_verification'
           OR NEW.registered_by_user_id = requester_user
           OR NEW.registered_by_person_id = requester_person
           OR NOT EXISTS (SELECT 1 FROM public.files AS file
                           WHERE file.id = NEW.evidence_file_id
                             AND file.status = 'available')
           OR EXISTS (SELECT 1 FROM public.material_request_files AS binding
                       WHERE binding.file_id = NEW.evidence_file_id)
           OR NOT EXISTS (
               SELECT 1 FROM public.approval_step_candidates AS candidate
                WHERE candidate.step_id = NEW.step_id
                  AND candidate.user_id = NEW.registered_by_user_id
                  AND candidate.person_id = NEW.registered_by_person_id
                  AND candidate.role_assignment_id = NEW.registered_role_assignment_id
                  AND candidate.authorization_version = NEW.authorization_version
                  AND candidate.candidate_kind = 'registrar'
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.step_id IS DISTINCT FROM OLD.step_id
       OR NEW.registration_no IS DISTINCT FROM OLD.registration_no
       OR NEW.external_action IS DISTINCT FROM OLD.external_action
       OR NEW.evidence_file_id IS DISTINCT FROM OLD.evidence_file_id
       OR NEW.external_approver_snapshot_jsonb IS DISTINCT FROM OLD.external_approver_snapshot_jsonb
       OR NEW.external_decided_at IS DISTINCT FROM OLD.external_decided_at
       OR NEW.decision_manifest_sha256 IS DISTINCT FROM OLD.decision_manifest_sha256
       OR NEW.registered_by_user_id IS DISTINCT FROM OLD.registered_by_user_id
       OR NEW.registered_by_person_id IS DISTINCT FROM OLD.registered_by_person_id
       OR NEW.registered_role_assignment_id IS DISTINCT FROM OLD.registered_role_assignment_id
       OR NEW.authorization_version IS DISTINCT FROM OLD.authorization_version
       OR NEW.registered_at IS DISTINCT FROM OLD.registered_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR OLD.status <> 'pending_verification'
       OR NEW.status NOT IN ('accepted', 'rejected', 'superseded')
       OR NEW.verified_by_user_id = requester_user
       OR NEW.verified_by_person_id = requester_person
       OR NOT EXISTS (
           SELECT 1 FROM public.approval_step_candidates AS candidate
            WHERE candidate.step_id = NEW.step_id
              AND candidate.user_id = NEW.verified_by_user_id
              AND candidate.person_id = NEW.verified_by_person_id
              AND candidate.role_assignment_id = NEW.verified_role_assignment_id
              AND candidate.authorization_version = NEW.verified_authorization_version
              AND candidate.candidate_kind = 'verifier'
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT count(*) INTO expected_count
      FROM public.approval_step_line_decisions AS decision
     WHERE decision.step_id = predecessor_id AND decision.approved_qty > 0;
    SELECT count(*) INTO actual_count
      FROM public.approval_external_registration_lines AS line
     WHERE line.registration_id = NEW.id;
    IF NEW.external_action IN {accepted_actions} THEN
        IF expected_count = 0 OR actual_count <> expected_count
           OR EXISTS (
               SELECT 1 FROM public.approval_step_line_decisions AS predecessor
                WHERE predecessor.step_id = predecessor_id
                  AND predecessor.approved_qty > 0
                  AND NOT EXISTS (
                      SELECT 1 FROM public.approval_external_registration_lines AS line
                       WHERE line.registration_id = NEW.id
                         AND line.request_line_id = predecessor.request_line_id
                  )
           )
           OR (NEW.external_action = 'approve' AND EXISTS (
               SELECT 1 FROM public.approval_external_registration_lines AS line
                WHERE line.registration_id = NEW.id AND line.rejected_qty > 0
           ))
           OR (NEW.external_action = 'partial_approve' AND (
               NOT EXISTS (SELECT 1 FROM public.approval_external_registration_lines AS line
                            WHERE line.registration_id = NEW.id AND line.approved_qty > 0)
               OR NOT EXISTS (SELECT 1 FROM public.approval_external_registration_lines AS line
                              WHERE line.registration_id = NEW.id AND line.rejected_qty > 0)
           ))
           {reject_validation}
        THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSIF NEW.external_action IN {no_line_actions} AND actual_count <> 0 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE OR REPLACE FUNCTION public.{PG_EXTERNAL_LINE_FUNCTION_0029}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    predecessor_id uuid;
    expected_input numeric(18,3);
    registration_action text;
BEGIN
    SELECT step.predecessor_step_id, registration.external_action
      INTO predecessor_id, registration_action
      FROM public.approval_external_registrations AS registration
      JOIN public.approval_steps AS step ON step.id = registration.step_id
     WHERE registration.id = NEW.registration_id
       AND registration.status = 'pending_verification'
       AND step.step_no = 3
       AND step.source_mode = 'external_registration';
    IF NOT FOUND OR registration_action NOT IN {accepted_actions} THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT decision.approved_qty INTO expected_input
      FROM public.approval_step_line_decisions AS decision
      JOIN public.approval_steps AS step ON step.id = decision.step_id
     WHERE decision.step_id = predecessor_id
       AND decision.request_line_id = NEW.request_line_id
       AND step.status IN ('approved', 'partially_approved');
    IF NOT FOUND OR expected_input <= 0 OR NEW.input_qty <> expected_input
       OR (NEW.rejected_qty > 0 AND btrim(NEW.reason) = '')
       OR (registration_action = 'reject'
           AND (NEW.approved_qty <> 0 OR NEW.rejected_qty <> NEW.input_qty)) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )


def _create_postgresql_validator() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_VALIDATE_FUNCTION}(checked_instance_id uuid)
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    instance_row public.approval_instances%ROWTYPE;
BEGIN
    SELECT * INTO instance_row
      FROM public.approval_instances
     WHERE id = checked_instance_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.material_request_lines AS line
         WHERE line.request_id = instance_row.request_id
           AND line.revision_id = instance_row.request_revision_id
    ) OR EXISTS (
        SELECT 1
          FROM public.approval_steps AS step
          LEFT JOIN public.approval_route_step_defs AS definition
            ON definition.route_version_id = instance_row.route_version_id
           AND definition.step_no = step.step_no
         WHERE step.instance_id = checked_instance_id
           AND (definition.id IS NULL OR definition.source_mode <> step.source_mode)
    ) OR EXISTS (
        SELECT 1 FROM public.approval_route_step_defs AS definition
         WHERE definition.route_version_id = instance_row.route_version_id
           AND NOT EXISTS (
               SELECT 1 FROM public.approval_steps AS step
                WHERE step.instance_id = checked_instance_id
                  AND step.step_no = definition.step_no
                  AND step.attempt_no = 1
           )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF instance_row.status = 'active' THEN
        IF instance_row.current_step_id IS NULL OR instance_row.current_step_no IS NULL
           OR (SELECT count(*) FROM public.approval_steps AS step
                WHERE step.instance_id = checked_instance_id
                  AND step.id = instance_row.current_step_id
                  AND step.step_no = instance_row.current_step_no
                  AND step.status IN ({CURRENT_STATUSES})) <> 1
           OR (SELECT count(*) FROM public.approval_steps AS step
                WHERE step.instance_id = checked_instance_id
                  AND step.status IN ({CURRENT_STATUSES})) <> 1 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSIF instance_row.current_step_id IS NOT NULL
       OR instance_row.current_step_no IS NOT NULL
       OR EXISTS (
           SELECT 1 FROM public.approval_steps AS step
            WHERE step.instance_id = checked_instance_id
              AND step.status IN ({CURRENT_STATUSES})
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.approval_steps AS step
          LEFT JOIN public.approval_steps AS predecessor
            ON predecessor.id = step.predecessor_step_id
          LEFT JOIN public.approval_steps AS superseded
            ON superseded.id = step.supersedes_step_id
          LEFT JOIN public.approval_steps AS reopened
            ON reopened.id = step.reopened_from_step_id
         WHERE step.instance_id = checked_instance_id
           AND (
               (step.step_no = 1 AND step.predecessor_step_id IS NOT NULL)
               OR (step.step_no > 1 AND (
                   predecessor.id IS NULL
                   OR predecessor.instance_id <> step.instance_id
                   OR predecessor.step_no <> step.step_no - 1
               ))
               OR (step.attempt_no = 1 AND step.supersedes_step_id IS NOT NULL)
               OR (step.attempt_no > 1 AND (
                   superseded.id IS NULL
                   OR superseded.instance_id <> step.instance_id
                   OR superseded.step_no <> step.step_no
                   OR superseded.attempt_no <> step.attempt_no - 1
               ))
               OR (step.reopened_from_step_id IS NOT NULL AND (
                   reopened.instance_id <> step.instance_id
                   OR reopened.step_no <> step.step_no + 1
                   OR reopened.status <> 'returned'
                   OR step.attempt_no <= 1
               ))
           )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF instance_row.status = 'active' AND instance_row.current_step_no > 1
       AND NOT EXISTS (
           SELECT 1
             FROM public.approval_steps AS current_step
             JOIN public.approval_steps AS predecessor
               ON predecessor.id = current_step.predecessor_step_id
            WHERE current_step.id = instance_row.current_step_id
              AND predecessor.status IN ('approved', 'partially_approved')
              AND EXISTS (
                  SELECT 1 FROM public.approval_step_line_decisions AS decision
                   WHERE decision.step_id = predecessor.id
                     AND decision.approved_qty > 0
              )
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.approval_steps AS step
         WHERE step.instance_id = checked_instance_id
           AND step.status IN ('approved', 'partially_approved', 'rejected')
           AND (
               NOT EXISTS (
                   SELECT 1 FROM public.approval_step_line_decisions AS decision
                    WHERE decision.step_id = step.id
               )
               OR EXISTS (
                   SELECT 1
                     FROM (
                         SELECT line.id AS request_line_id
                           FROM public.material_request_lines AS line
                          WHERE step.step_no = 1
                            AND line.request_id = instance_row.request_id
                            AND line.revision_id = instance_row.request_revision_id
                         UNION ALL
                         SELECT predecessor.request_line_id
                           FROM public.approval_step_line_decisions AS predecessor
                          WHERE step.step_no > 1
                            AND predecessor.step_id = step.predecessor_step_id
                            AND predecessor.approved_qty > 0
                     ) AS expected
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.approval_step_line_decisions AS actual
                         WHERE actual.step_id = step.id
                           AND actual.request_line_id = expected.request_line_id
                    )
               )
               OR EXISTS (
                   SELECT 1 FROM public.approval_step_line_decisions AS actual
                    WHERE actual.step_id = step.id
                      AND NOT EXISTS (
                          SELECT 1
                            FROM (
                                SELECT line.id AS request_line_id
                                  FROM public.material_request_lines AS line
                                 WHERE step.step_no = 1
                                   AND line.request_id = instance_row.request_id
                                   AND line.revision_id = instance_row.request_revision_id
                                UNION ALL
                                SELECT predecessor.request_line_id
                                  FROM public.approval_step_line_decisions AS predecessor
                                 WHERE step.step_no > 1
                                   AND predecessor.step_id = step.predecessor_step_id
                                   AND predecessor.approved_qty > 0
                            ) AS expected
                           WHERE expected.request_line_id = actual.request_line_id
                      )
               )
               OR (SELECT count(*)
                     FROM public.approval_actions AS action
                    WHERE action.instance_id = checked_instance_id
                      AND action.step_id = step.id
                      AND (
                          (step.source_mode <> 'external_registration'
                           AND action.action = CASE step.status
                               WHEN 'approved' THEN 'approve'
                               WHEN 'partially_approved' THEN 'partial_approve'
                               ELSE 'reject' END)
                          OR (step.source_mode = 'external_registration'
                              AND action.action = 'verify_external_accept')
                      )) <> 1
               OR (step.status = 'approved' AND EXISTS (
                   SELECT 1 FROM public.approval_step_line_decisions AS decision
                    WHERE decision.step_id = step.id AND decision.rejected_qty > 0
               ))
               OR (step.status = 'partially_approved' AND (
                   NOT EXISTS (SELECT 1 FROM public.approval_step_line_decisions AS decision
                                WHERE decision.step_id = step.id AND decision.approved_qty > 0)
                   OR NOT EXISTS (SELECT 1 FROM public.approval_step_line_decisions AS decision
                                  WHERE decision.step_id = step.id AND decision.rejected_qty > 0)
               ))
               OR (step.status = 'rejected' AND EXISTS (
                   SELECT 1 FROM public.approval_step_line_decisions AS decision
                    WHERE decision.step_id = step.id
                      AND (decision.approved_qty <> 0
                           OR decision.rejected_qty <> decision.input_qty
                           OR btrim(decision.reason) = '')
               ))
           )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.approval_steps AS step
         WHERE step.instance_id = checked_instance_id
           AND step.status = 'returned'
           AND (
               (step.source_mode = 'external_registration' AND NOT EXISTS (
                   SELECT 1 FROM public.approval_external_registrations AS registration
                    WHERE registration.step_id = step.id
                      AND registration.status = 'accepted'
                      AND registration.external_action = 'return'
               ))
               OR
               (SELECT count(*) FROM public.approval_actions AS action
                 WHERE action.instance_id = checked_instance_id
                   AND action.step_id = step.id
                   AND (action.action = 'return'
                        OR (step.source_mode = 'external_registration'
                            AND action.action = 'verify_external_accept'))) <> 1
               OR NOT EXISTS (
                   SELECT 1 FROM public.approval_return_line_facts AS fact
                    WHERE fact.instance_id = checked_instance_id
                      AND fact.returned_from_step_id = step.id
               )
               OR (SELECT count(*) FROM public.approval_return_line_facts AS fact
                    WHERE fact.instance_id = checked_instance_id
                      AND fact.returned_from_step_id = step.id)
                  <>
                  (SELECT count(*) FROM (
                      SELECT line.id AS request_line_id
                        FROM public.material_request_lines AS line
                       WHERE step.step_no = 1
                         AND line.request_id = instance_row.request_id
                         AND line.revision_id = instance_row.request_revision_id
                      UNION ALL
                      SELECT predecessor.request_line_id
                        FROM public.approval_step_line_decisions AS predecessor
                       WHERE step.step_no > 1
                         AND predecessor.step_id = step.predecessor_step_id
                         AND predecessor.approved_qty > 0
                  ) AS expected_return)
               OR EXISTS (
                   SELECT 1 FROM (
                       SELECT line.id AS request_line_id
                         FROM public.material_request_lines AS line
                        WHERE step.step_no = 1
                          AND line.request_id = instance_row.request_id
                          AND line.revision_id = instance_row.request_revision_id
                       UNION ALL
                       SELECT predecessor.request_line_id
                         FROM public.approval_step_line_decisions AS predecessor
                        WHERE step.step_no > 1
                          AND predecessor.step_id = step.predecessor_step_id
                          AND predecessor.approved_qty > 0
                   ) AS expected_return
                   WHERE NOT EXISTS (
                       SELECT 1 FROM public.approval_return_line_facts AS fact
                        WHERE fact.instance_id = checked_instance_id
                          AND fact.returned_from_step_id = step.id
                          AND fact.request_line_id = expected_return.request_line_id
                   )
               )
               OR (step.step_no = 1 AND EXISTS (
                   SELECT 1 FROM public.approval_return_line_facts AS fact
                    WHERE fact.instance_id = checked_instance_id
                      AND fact.returned_from_step_id = step.id
                      AND (fact.target_kind <> 'requester_revision'
                           OR fact.target_step_id IS NOT NULL)
               ))
               OR (step.step_no > 1 AND EXISTS (
                   SELECT 1 FROM public.approval_return_line_facts AS fact
                    LEFT JOIN public.approval_steps AS target
                      ON target.id = fact.target_step_id
                   WHERE fact.instance_id = checked_instance_id
                     AND fact.returned_from_step_id = step.id
                     AND (fact.target_kind <> 'approval_step'
                          OR target.id IS NULL
                          OR target.instance_id <> checked_instance_id
                          OR target.step_no <> step.step_no - 1
                          OR target.reopened_from_step_id <> step.id)
               ))
           )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_DISPATCH_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    checked_instance_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'approval_instances' THEN
        checked_instance_id := COALESCE(NEW.id, OLD.id);
    ELSIF TG_TABLE_NAME IN ('approval_steps', 'approval_actions',
                            'approval_return_line_facts') THEN
        checked_instance_id := COALESCE(NEW.instance_id, OLD.instance_id);
    ELSIF TG_TABLE_NAME = 'approval_step_candidates' THEN
        SELECT instance_id INTO checked_instance_id FROM public.approval_steps
         WHERE id = COALESCE(NEW.step_id, OLD.step_id);
    ELSIF TG_TABLE_NAME = 'approval_step_line_decisions' THEN
        SELECT instance_id INTO checked_instance_id FROM public.approval_steps
         WHERE id = COALESCE(NEW.step_id, OLD.step_id);
    END IF;
    IF checked_instance_id IS NOT NULL THEN
        PERFORM public.{PG_VALIDATE_FUNCTION}(checked_instance_id);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$
"""
    )


def _create_postgresql_row_guards() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_STEP_GUARD_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION '{GUARD_ERROR}'; END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.id IS DISTINCT FROM OLD.id OR NEW.instance_id IS DISTINCT FROM OLD.instance_id
        OR NEW.step_no IS DISTINCT FROM OLD.step_no OR NEW.attempt_no IS DISTINCT FROM OLD.attempt_no
        OR NEW.predecessor_step_id IS DISTINCT FROM OLD.predecessor_step_id
        OR NEW.supersedes_step_id IS DISTINCT FROM OLD.supersedes_step_id
        OR NEW.reopened_from_step_id IS DISTINCT FROM OLD.reopened_from_step_id
        OR NEW.source_mode IS DISTINCT FROM OLD.source_mode
        OR NEW.assignee_user_id IS DISTINCT FROM OLD.assignee_user_id
        OR NEW.assignee_snapshot_jsonb IS DISTINCT FROM OLD.assignee_snapshot_jsonb
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR OLD.status IN ('approved','partially_approved','rejected','returned','cancelled','superseded')
    ) THEN RAISE EXCEPTION '{GUARD_ERROR}'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.approval_instances AS instance
        JOIN public.approval_route_step_defs AS definition
          ON definition.route_version_id = instance.route_version_id
         AND definition.step_no = NEW.step_no
        WHERE instance.id = NEW.instance_id AND definition.source_mode = NEW.source_mode
    ) THEN RAISE EXCEPTION '{GUARD_ERROR}'; END IF;
    RETURN NEW;
END $$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_CANDIDATE_GUARD_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.approval_steps AS step
         WHERE step.id = NEW.step_id
           AND step.status IN ('pending', {CURRENT_STATUSES})
           AND NOT EXISTS (SELECT 1 FROM public.approval_step_line_decisions d WHERE d.step_id=step.id)
           AND NOT EXISTS (SELECT 1 FROM public.approval_external_registrations r WHERE r.step_id=step.id)
           AND NOT EXISTS (SELECT 1 FROM public.approval_actions a WHERE a.step_id=step.id AND a.action <> 'submit')
    ) THEN RAISE EXCEPTION '{GUARD_ERROR}'; END IF;
    RETURN NEW;
END $$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_ACTION_GUARD_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF NEW.step_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.approval_steps AS step
        JOIN public.material_request_commands AS command
          ON command.id = NEW.command_id
        JOIN public.approval_instances AS instance ON instance.id = NEW.instance_id
        WHERE step.id = NEW.step_id AND step.instance_id = NEW.instance_id
          AND command.request_id = instance.request_id
          AND (
            (NEW.action='submit' AND command.operation='submit') OR
            (NEW.action IN ('approve','partial_approve','reject','return')
             AND command.operation IN ('region_decide','headquarters_decide')) OR
            (NEW.action='register_external_evidence' AND command.operation='register_external') OR
            (NEW.action IN ('verify_external_accept','verify_external_reject')
             AND command.operation='verify_external')
          )
    ) THEN RAISE EXCEPTION '{GUARD_ERROR}'; END IF;
    RETURN NEW;
END $$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_DECISION_GUARD_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.approval_steps AS step
        JOIN public.approval_instances AS instance ON instance.id=step.instance_id
        WHERE step.id=NEW.step_id AND instance.status='active'
          AND instance.current_step_id=step.id
          AND step.status IN ({CURRENT_STATUSES})
    ) THEN RAISE EXCEPTION '{GUARD_ERROR}'; END IF;
    RETURN NEW;
END $$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_RETURN_GUARD_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
    source_no integer;
    expected_input numeric(18,3);
    expected_target_max numeric(18,3);
BEGIN
    SELECT source.step_no,
           CASE WHEN source.step_no=1 THEN line.requested_qty ELSE predecessor.approved_qty END,
           CASE
             WHEN source.step_no IN (1,2) THEN line.requested_qty
             ELSE target_predecessor.approved_qty
           END
      INTO source_no, expected_input, expected_target_max
      FROM public.approval_steps AS source
      JOIN public.approval_instances AS instance ON instance.id=source.instance_id
      JOIN public.material_request_lines AS line
        ON line.id=NEW.request_line_id AND line.request_id=instance.request_id
       AND line.revision_id=instance.request_revision_id
      LEFT JOIN public.approval_step_line_decisions AS predecessor
        ON predecessor.step_id=source.predecessor_step_id
       AND predecessor.request_line_id=line.id
      LEFT JOIN public.approval_steps AS target ON target.id=NEW.target_step_id
      LEFT JOIN public.approval_step_line_decisions AS target_predecessor
        ON target_predecessor.step_id=target.predecessor_step_id
       AND target_predecessor.request_line_id=line.id
     WHERE source.id=NEW.returned_from_step_id AND source.instance_id=NEW.instance_id;
    IF NOT FOUND OR expected_input IS NULL OR expected_input<=0
       OR NEW.request_id<>(SELECT request_id FROM public.approval_instances WHERE id=NEW.instance_id)
       OR NEW.request_revision_id<>(SELECT request_revision_id FROM public.approval_instances WHERE id=NEW.instance_id)
       OR NEW.returned_step_input_qty<>expected_input
       OR NEW.target_step_max_qty<>expected_target_max
       OR NOT EXISTS (
           SELECT 1 FROM public.approval_actions AS action
            WHERE action.id=NEW.return_action_id AND action.instance_id=NEW.instance_id
              AND action.step_id=NEW.returned_from_step_id
              AND (action.action='return' OR action.action='verify_external_accept')
              AND action.actor_user_id=NEW.actor_user_id
              AND action.actor_person_id=NEW.actor_person_id
              AND action.actor_role_assignment_id=NEW.actor_role_assignment_id
              AND action.authorization_version=NEW.authorization_version
              AND action.occurred_at=NEW.occurred_at
       )
       OR (source_no=1 AND (NEW.target_kind<>'requester_revision' OR NEW.target_step_id IS NOT NULL))
       OR (source_no>1 AND NOT EXISTS (
           SELECT 1 FROM public.approval_steps AS target
            WHERE target.id=NEW.target_step_id AND target.instance_id=NEW.instance_id
              AND target.step_no=source_no-1
              AND target.reopened_from_step_id=NEW.returned_from_step_id
       )) THEN RAISE EXCEPTION '{GUARD_ERROR}'; END IF;
    RETURN NEW;
END $$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_IMMUTABLE_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN RAISE EXCEPTION '{GUARD_ERROR}'; END $$
"""
    )


def _create_postgresql_triggers() -> None:
    op.execute(
        f"CREATE TRIGGER trg_approval_steps_write_guard_0030 BEFORE INSERT OR UPDATE OR DELETE "
        f"ON public.approval_steps FOR EACH ROW EXECUTE FUNCTION public.{PG_STEP_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_step_candidates_write_guard_0030 BEFORE INSERT "
        f"ON public.approval_step_candidates FOR EACH ROW EXECUTE FUNCTION public.{PG_CANDIDATE_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_actions_write_guard_0030 BEFORE INSERT "
        f"ON public.approval_actions FOR EACH ROW EXECUTE FUNCTION public.{PG_ACTION_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_step_line_decisions_current_guard_0030 BEFORE INSERT "
        f"ON public.approval_step_line_decisions FOR EACH ROW EXECUTE FUNCTION public.{PG_DECISION_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_return_line_facts_write_guard_0030 BEFORE INSERT "
        f"ON public.approval_return_line_facts FOR EACH ROW EXECUTE FUNCTION public.{PG_RETURN_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_return_line_facts_immutable_0030 BEFORE UPDATE OR DELETE "
        f"ON public.approval_return_line_facts FOR EACH ROW EXECUTE FUNCTION public.{PG_IMMUTABLE_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_return_line_facts_no_truncate_0030 BEFORE TRUNCATE "
        f"ON public.approval_return_line_facts FOR EACH STATEMENT EXECUTE FUNCTION public.{PG_IMMUTABLE_FUNCTION}()"
    )
    for table_name in (
        "approval_instances",
        "approval_steps",
        "approval_step_candidates",
        "approval_actions",
        "approval_step_line_decisions",
        "approval_return_line_facts",
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_{table_name}_causality_0030 "
            f"AFTER INSERT OR UPDATE ON public.{table_name} DEFERRABLE INITIALLY DEFERRED "
            f"FOR EACH ROW EXECUTE FUNCTION public.{PG_DISPATCH_FUNCTION}()"
        )
    for function_name in (
        PG_VALIDATE_FUNCTION,
        PG_DISPATCH_FUNCTION,
        PG_STEP_GUARD_FUNCTION,
        PG_CANDIDATE_GUARD_FUNCTION,
        PG_ACTION_GUARD_FUNCTION,
        PG_DECISION_GUARD_FUNCTION,
        PG_RETURN_GUARD_FUNCTION,
        PG_IMMUTABLE_FUNCTION,
    ):
        signature = "uuid" if function_name == PG_VALIDATE_FUNCTION else ""
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION public.{function_name}({signature}) FROM PUBLIC"
        )


def _create_sqlite_guards() -> None:
    _replace_sqlite_external_reject_guards(allow_reject_lines=True)
    # SQLite has deferred foreign keys but no deferred constraint triggers.
    # The future current-step UUID therefore supplies commit-time existence;
    # the following immediate triggers conservatively require all causal facts
    # before a step can become terminal.
    op.execute(
        f"""
CREATE TRIGGER trg_approval_instances_insert_guard_0030
BEFORE INSERT ON approval_instances
WHEN NOT EXISTS (
    SELECT 1 FROM material_request_lines AS line
     WHERE line.request_id=NEW.request_id AND line.revision_id=NEW.request_revision_id
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_instances_update_guard_0030
BEFORE UPDATE ON approval_instances
WHEN NEW.id IS NOT OLD.id OR NEW.request_id IS NOT OLD.request_id
  OR NEW.request_revision_id IS NOT OLD.request_revision_id
  OR NEW.revision_no IS NOT OLD.revision_no OR NEW.route_version_id IS NOT OLD.route_version_id
  OR NEW.attempt_no IS NOT OLD.attempt_no OR NEW.created_at IS NOT OLD.created_at
  OR OLD.status IN ('completed','rejected','withdrawn','cancelled','superseded')
  OR (OLD.status='returned' AND NEW.status<>'superseded')
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_instances_delete_guard_0030 BEFORE DELETE ON approval_instances
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_steps_insert_guard_0030
BEFORE INSERT ON approval_steps
WHEN NOT EXISTS (
    SELECT 1 FROM approval_instances AS instance
    JOIN approval_route_step_defs AS definition
      ON definition.route_version_id=instance.route_version_id AND definition.step_no=NEW.step_no
    WHERE instance.id=NEW.instance_id AND definition.source_mode=NEW.source_mode
)
OR (NEW.step_no=1 AND NEW.predecessor_step_id IS NOT NULL)
OR (NEW.step_no>1 AND NOT EXISTS (
    SELECT 1 FROM approval_steps AS predecessor
     WHERE predecessor.id=NEW.predecessor_step_id AND predecessor.instance_id=NEW.instance_id
       AND predecessor.step_no=NEW.step_no-1
))
OR (NEW.attempt_no>1 AND NOT EXISTS (
    SELECT 1 FROM approval_steps AS prior
     WHERE prior.id=NEW.supersedes_step_id AND prior.instance_id=NEW.instance_id
       AND prior.step_no=NEW.step_no AND prior.attempt_no=NEW.attempt_no-1
))
OR (NEW.reopened_from_step_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM approval_steps AS source
     WHERE source.id=NEW.reopened_from_step_id AND source.instance_id=NEW.instance_id
       AND source.step_no=NEW.step_no+1
       AND source.status IN ('open','awaiting_external_evidence',
                             'evidence_pending_verification','returned')
))
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_steps_update_guard_0030
BEFORE UPDATE ON approval_steps
WHEN NEW.id IS NOT OLD.id OR NEW.instance_id IS NOT OLD.instance_id
  OR NEW.step_no IS NOT OLD.step_no OR NEW.attempt_no IS NOT OLD.attempt_no
  OR NEW.predecessor_step_id IS NOT OLD.predecessor_step_id
  OR NEW.supersedes_step_id IS NOT OLD.supersedes_step_id
  OR NEW.reopened_from_step_id IS NOT OLD.reopened_from_step_id
  OR NEW.source_mode IS NOT OLD.source_mode OR NEW.assignee_user_id IS NOT OLD.assignee_user_id
  OR NEW.assignee_snapshot_jsonb IS NOT OLD.assignee_snapshot_jsonb
  OR NEW.created_at IS NOT OLD.created_at
  OR OLD.status IN ('approved','partially_approved','rejected','returned','cancelled','superseded')
  OR (NEW.status IN ('approved','partially_approved') AND (
      NOT EXISTS (SELECT 1 FROM approval_step_line_decisions d WHERE d.step_id=NEW.id)
      OR NOT EXISTS (SELECT 1 FROM approval_actions a WHERE a.instance_id=NEW.instance_id
                      AND a.step_id=NEW.id AND a.action IN ('approve','partial_approve','verify_external_accept'))
  ))
  OR (NEW.status='rejected' AND (
      (SELECT count(*) FROM approval_step_line_decisions d WHERE d.step_id=NEW.id)=0
      OR (SELECT count(*) FROM approval_step_line_decisions d WHERE d.step_id=NEW.id)
         <>
         (SELECT count(*) FROM (
             SELECT line.id AS request_line_id
               FROM material_request_lines line
              WHERE NEW.step_no=1
                AND line.request_id=(SELECT request_id FROM approval_instances WHERE id=NEW.instance_id)
                AND line.revision_id=(SELECT request_revision_id FROM approval_instances WHERE id=NEW.instance_id)
             UNION ALL
             SELECT predecessor.request_line_id
               FROM approval_step_line_decisions predecessor
              WHERE NEW.step_no>1 AND predecessor.step_id=NEW.predecessor_step_id
                AND predecessor.approved_qty>0
         ))
      OR EXISTS (SELECT 1 FROM approval_step_line_decisions d
                  WHERE d.step_id=NEW.id
                    AND (d.approved_qty<>0 OR d.rejected_qty<>d.input_qty
                         OR trim(d.reason)=''))
      OR (SELECT count(*) FROM approval_actions a
           WHERE a.instance_id=NEW.instance_id AND a.step_id=NEW.id
             AND ((NEW.source_mode='external_registration'
                   AND a.action='verify_external_accept')
                  OR (NEW.source_mode<>'external_registration'
                      AND a.action='reject')))<>1
  ))
  OR (NEW.status='returned' AND (
      (NEW.source_mode='external_registration' AND NOT EXISTS (
          SELECT 1 FROM approval_external_registrations r
           WHERE r.step_id=NEW.id AND r.status='accepted' AND r.external_action='return'
      ))
      OR
      NOT EXISTS (SELECT 1 FROM approval_actions a WHERE a.instance_id=NEW.instance_id
                   AND a.step_id=NEW.id AND a.action IN ('return','verify_external_accept'))
      OR NOT EXISTS (SELECT 1 FROM approval_return_line_facts f
                      WHERE f.instance_id=NEW.instance_id AND f.returned_from_step_id=NEW.id)
      OR (SELECT count(*) FROM approval_return_line_facts f
           WHERE f.instance_id=NEW.instance_id AND f.returned_from_step_id=NEW.id)
         <>
         (SELECT count(*) FROM (
             SELECT line.id AS request_line_id
               FROM material_request_lines AS line
              WHERE NEW.step_no=1
                AND line.request_id=(SELECT request_id FROM approval_instances WHERE id=NEW.instance_id)
                AND line.revision_id=(SELECT request_revision_id FROM approval_instances WHERE id=NEW.instance_id)
             UNION ALL
             SELECT predecessor.request_line_id
               FROM approval_step_line_decisions AS predecessor
              WHERE NEW.step_no>1 AND predecessor.step_id=NEW.predecessor_step_id
                AND predecessor.approved_qty>0
         ))
      OR EXISTS (
          SELECT 1 FROM (
              SELECT line.id AS request_line_id
                FROM material_request_lines AS line
               WHERE NEW.step_no=1
                 AND line.request_id=(SELECT request_id FROM approval_instances WHERE id=NEW.instance_id)
                 AND line.revision_id=(SELECT request_revision_id FROM approval_instances WHERE id=NEW.instance_id)
              UNION ALL
              SELECT predecessor.request_line_id
                FROM approval_step_line_decisions AS predecessor
               WHERE NEW.step_no>1 AND predecessor.step_id=NEW.predecessor_step_id
                 AND predecessor.approved_qty>0
          ) AS expected
          WHERE NOT EXISTS (
              SELECT 1 FROM approval_return_line_facts f
               WHERE f.instance_id=NEW.instance_id AND f.returned_from_step_id=NEW.id
                 AND f.request_line_id=expected.request_line_id
          )
      )
  ))
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_steps_delete_guard_0030 BEFORE DELETE ON approval_steps
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_step_candidates_insert_guard_0030
BEFORE INSERT ON approval_step_candidates
WHEN NOT EXISTS (
    SELECT 1 FROM approval_steps AS step WHERE step.id=NEW.step_id
      AND step.status IN ('pending',{CURRENT_STATUSES})
      AND NOT EXISTS (SELECT 1 FROM approval_step_line_decisions d WHERE d.step_id=step.id)
      AND NOT EXISTS (SELECT 1 FROM approval_external_registrations r WHERE r.step_id=step.id)
      AND NOT EXISTS (SELECT 1 FROM approval_actions a WHERE a.step_id=step.id AND a.action<>'submit')
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_actions_insert_guard_0030
BEFORE INSERT ON approval_actions
WHEN NEW.step_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM approval_steps AS step
    JOIN approval_instances AS instance ON instance.id=NEW.instance_id
    JOIN material_request_commands AS command ON command.id=NEW.command_id
    WHERE step.id=NEW.step_id AND step.instance_id=NEW.instance_id
      AND command.request_id=instance.request_id
      AND ((NEW.action='submit' AND command.operation='submit')
        OR (NEW.action IN ('approve','partial_approve','reject','return')
            AND command.operation IN ('region_decide','headquarters_decide'))
        OR (NEW.action='register_external_evidence' AND command.operation='register_external')
        OR (NEW.action IN ('verify_external_accept','verify_external_reject')
            AND command.operation='verify_external'))
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_step_line_decisions_current_guard_0030
BEFORE INSERT ON approval_step_line_decisions
WHEN NOT EXISTS (
    SELECT 1 FROM approval_steps AS step JOIN approval_instances AS instance ON instance.id=step.instance_id
     WHERE step.id=NEW.step_id AND instance.status='active' AND instance.current_step_id=step.id
       AND step.status IN ({CURRENT_STATUSES})
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_return_line_facts_insert_guard_0030
BEFORE INSERT ON approval_return_line_facts
WHEN NOT EXISTS (
    SELECT 1 FROM approval_steps AS source
    JOIN approval_instances AS instance ON instance.id=source.instance_id
    JOIN material_request_lines AS line ON line.id=NEW.request_line_id
      AND line.request_id=instance.request_id AND line.revision_id=instance.request_revision_id
    JOIN approval_actions AS action ON action.id=NEW.return_action_id
      AND action.instance_id=instance.id AND action.step_id=source.id
    LEFT JOIN approval_step_line_decisions AS predecessor
      ON predecessor.step_id=source.predecessor_step_id AND predecessor.request_line_id=line.id
    WHERE source.id=NEW.returned_from_step_id AND source.instance_id=NEW.instance_id
      AND NEW.request_id=instance.request_id AND NEW.request_revision_id=instance.request_revision_id
      AND NEW.returned_step_input_qty=CASE WHEN source.step_no=1
                                           THEN line.requested_qty ELSE predecessor.approved_qty END
      AND NEW.target_step_max_qty=CASE
          WHEN source.step_no IN (1,2) THEN line.requested_qty
          ELSE (SELECT target_predecessor.approved_qty
                  FROM approval_steps AS target
                  JOIN approval_step_line_decisions AS target_predecessor
                    ON target_predecessor.step_id=target.predecessor_step_id
                   AND target_predecessor.request_line_id=line.id
                 WHERE target.id=NEW.target_step_id)
          END
      AND ((source.step_no=1
            AND NEW.target_kind='requester_revision' AND NEW.target_step_id IS NULL)
        OR (source.step_no>1 AND predecessor.approved_qty>0 AND NEW.target_kind='approval_step'
            AND EXISTS (SELECT 1 FROM approval_steps AS target
                         WHERE target.id=NEW.target_step_id AND target.instance_id=NEW.instance_id
                           AND target.step_no=source.step_no-1
                           AND target.reopened_from_step_id=source.id)))
      AND action.action IN ('return','verify_external_accept')
      AND action.actor_user_id=NEW.actor_user_id AND action.actor_person_id=NEW.actor_person_id
      AND action.actor_role_assignment_id=NEW.actor_role_assignment_id
      AND action.authorization_version=NEW.authorization_version AND action.occurred_at=NEW.occurred_at
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"""
CREATE TRIGGER trg_approval_return_line_facts_immutable_{operation.lower()}_0030
BEFORE {operation} ON approval_return_line_facts
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
        )


def _replace_sqlite_external_reject_guards(*, allow_reject_lines: bool) -> None:
    accepted_actions = (
        "('approve', 'partial_approve', 'reject')"
        if allow_reject_lines
        else "('approve', 'partial_approve')"
    )
    no_line_actions = "('return')" if allow_reject_lines else "('reject', 'return')"
    reject_validation = (
        """
      OR (NEW.external_action = 'reject' AND EXISTS (
          SELECT 1 FROM approval_external_registration_lines AS line
           WHERE line.registration_id = NEW.id
             AND (line.approved_qty <> 0
                  OR line.rejected_qty <> line.input_qty
                  OR trim(line.reason) = '')
      ))
"""
        if allow_reject_lines
        else ""
    )
    op.execute("DROP TRIGGER IF EXISTS trg_approval_external_registrations_update_guard_0029")
    op.execute("DROP TRIGGER IF EXISTS trg_approval_external_registration_lines_quantity_0029")
    op.execute(
        f"""
CREATE TRIGGER trg_approval_external_registrations_update_guard_0029
BEFORE UPDATE ON approval_external_registrations
WHEN NEW.id IS NOT OLD.id
  OR NEW.step_id IS NOT OLD.step_id
  OR NEW.registration_no IS NOT OLD.registration_no
  OR NEW.external_action IS NOT OLD.external_action
  OR NEW.evidence_file_id IS NOT OLD.evidence_file_id
  OR NEW.external_approver_snapshot_jsonb IS NOT OLD.external_approver_snapshot_jsonb
  OR NEW.external_decided_at IS NOT OLD.external_decided_at
  OR NEW.decision_manifest_sha256 IS NOT OLD.decision_manifest_sha256
  OR NEW.registered_by_user_id IS NOT OLD.registered_by_user_id
  OR NEW.registered_by_person_id IS NOT OLD.registered_by_person_id
  OR NEW.registered_role_assignment_id IS NOT OLD.registered_role_assignment_id
  OR NEW.authorization_version IS NOT OLD.authorization_version
  OR NEW.registered_at IS NOT OLD.registered_at
  OR NEW.created_at IS NOT OLD.created_at
  OR OLD.status <> 'pending_verification'
  OR NEW.status NOT IN ('accepted', 'rejected', 'superseded')
  OR NOT EXISTS (
      SELECT 1
        FROM approval_steps AS step
        JOIN approval_instances AS instance ON instance.id = step.instance_id
        JOIN material_requests AS request ON request.id = instance.request_id
       WHERE step.id = NEW.step_id
         AND request.requester_user_id <> NEW.verified_by_user_id
         AND request.requester_person_id <> NEW.verified_by_person_id
  )
  OR NOT EXISTS (
      SELECT 1 FROM approval_step_candidates AS candidate
       WHERE candidate.step_id = NEW.step_id
         AND candidate.user_id = NEW.verified_by_user_id
         AND candidate.person_id = NEW.verified_by_person_id
         AND candidate.role_assignment_id = NEW.verified_role_assignment_id
         AND candidate.authorization_version = NEW.verified_authorization_version
         AND candidate.candidate_kind = 'verifier'
  )
  OR (NEW.external_action IN {accepted_actions} AND (
      (SELECT count(*) FROM approval_external_registration_lines AS line
        WHERE line.registration_id = NEW.id)
      <>
      (SELECT count(*)
         FROM approval_step_line_decisions AS decision
         JOIN approval_steps AS step ON step.id = NEW.step_id
        WHERE decision.step_id = step.predecessor_step_id
          AND decision.approved_qty > 0)
      OR EXISTS (
          SELECT 1
            FROM approval_step_line_decisions AS predecessor
            JOIN approval_steps AS step ON step.id = NEW.step_id
           WHERE predecessor.step_id = step.predecessor_step_id
             AND predecessor.approved_qty > 0
             AND NOT EXISTS (
                 SELECT 1 FROM approval_external_registration_lines AS line
                  WHERE line.registration_id = NEW.id
                    AND line.request_line_id = predecessor.request_line_id
             )
      )
      OR (NEW.external_action = 'approve' AND EXISTS (
          SELECT 1 FROM approval_external_registration_lines AS line
           WHERE line.registration_id = NEW.id AND line.rejected_qty > 0
      ))
      OR (NEW.external_action = 'partial_approve' AND (
          NOT EXISTS (SELECT 1 FROM approval_external_registration_lines AS line
                       WHERE line.registration_id = NEW.id AND line.approved_qty > 0)
          OR NOT EXISTS (SELECT 1 FROM approval_external_registration_lines AS line
                         WHERE line.registration_id = NEW.id AND line.rejected_qty > 0)
      ))
      {reject_validation}
  ))
  OR (NEW.external_action IN {no_line_actions} AND EXISTS (
      SELECT 1 FROM approval_external_registration_lines AS line
       WHERE line.registration_id = NEW.id
  ))
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_external_registration_lines_quantity_0029
BEFORE INSERT ON approval_external_registration_lines
WHEN NOT EXISTS (
    SELECT 1
      FROM approval_external_registrations AS registration
      JOIN approval_steps AS step ON step.id = registration.step_id
      JOIN approval_steps AS predecessor_step
        ON predecessor_step.id = step.predecessor_step_id
      JOIN approval_step_line_decisions AS predecessor
        ON predecessor.step_id = predecessor_step.id
       AND predecessor.request_line_id = NEW.request_line_id
     WHERE registration.id = NEW.registration_id
       AND registration.status = 'pending_verification'
       AND registration.external_action IN {accepted_actions}
       AND step.step_no = 3
       AND step.source_mode = 'external_registration'
       AND predecessor_step.status IN ('approved', 'partially_approved')
       AND predecessor.approved_qty > 0
       AND predecessor.approved_qty = NEW.input_qty
)
  OR (NEW.rejected_qty > 0 AND trim(NEW.reason) = '')
  OR EXISTS (
      SELECT 1 FROM approval_external_registrations AS registration
       WHERE registration.id = NEW.registration_id
         AND registration.external_action = 'reject'
         AND (NEW.approved_qty <> 0 OR NEW.rejected_qty <> NEW.input_qty)
  )
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )


def _require_safe_downgrade(dialect: str) -> None:
    if context.is_offline_mode():
        op.execute(
            f"""
DO $$ BEGIN
IF EXISTS (SELECT 1 FROM public.approval_return_line_facts)
   OR EXISTS (SELECT 1 FROM public.approval_steps
               WHERE supersedes_step_id IS NOT NULL OR reopened_from_step_id IS NOT NULL)
   OR EXISTS (
       SELECT 1 FROM public.approval_external_registration_lines AS line
       JOIN public.approval_external_registrations AS registration
         ON registration.id = line.registration_id
       WHERE registration.external_action = 'reject'
   )
THEN RAISE EXCEPTION '{DOWNGRADE_BLOCKER}'; END IF;
END $$
"""
        )
        return
    connection = op.get_bind()
    prefix = "public." if dialect == "postgresql" else ""
    count = connection.execute(
        sa.text(
            f"SELECT (SELECT count(*) FROM {prefix}approval_return_line_facts) + "
            f"(SELECT count(*) FROM {prefix}approval_steps WHERE "
            "supersedes_step_id IS NOT NULL OR reopened_from_step_id IS NOT NULL) + "
            f"(SELECT count(*) FROM {prefix}approval_external_registration_lines AS line "
            f"JOIN {prefix}approval_external_registrations AS registration "
            "ON registration.id = line.registration_id "
            "WHERE registration.external_action = 'reject')"
        )
    ).scalar_one()
    if count:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _drop_postgresql_guards() -> None:
    for table_name in (
        "approval_instances",
        "approval_steps",
        "approval_step_candidates",
        "approval_actions",
        "approval_step_line_decisions",
        "approval_return_line_facts",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_causality_0030 ON public.{table_name}"
        )
    for trigger_name, table_name in (
        ("trg_approval_steps_write_guard_0030", "approval_steps"),
        ("trg_approval_step_candidates_write_guard_0030", "approval_step_candidates"),
        ("trg_approval_actions_write_guard_0030", "approval_actions"),
        ("trg_approval_step_line_decisions_current_guard_0030", "approval_step_line_decisions"),
        ("trg_approval_return_line_facts_write_guard_0030", "approval_return_line_facts"),
        ("trg_approval_return_line_facts_immutable_0030", "approval_return_line_facts"),
        ("trg_approval_return_line_facts_no_truncate_0030", "approval_return_line_facts"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}")
    for function_name, signature in (
        (PG_DISPATCH_FUNCTION, ""),
        (PG_STEP_GUARD_FUNCTION, ""),
        (PG_CANDIDATE_GUARD_FUNCTION, ""),
        (PG_ACTION_GUARD_FUNCTION, ""),
        (PG_DECISION_GUARD_FUNCTION, ""),
        (PG_RETURN_GUARD_FUNCTION, ""),
        (PG_IMMUTABLE_FUNCTION, ""),
        (PG_VALIDATE_FUNCTION, "uuid"),
    ):
        op.execute(f"DROP FUNCTION IF EXISTS public.{function_name}({signature})")
    _replace_postgresql_external_reject_guards(allow_reject_lines=False)


def _downgrade_sqlite() -> None:
    for trigger_name in (
        "trg_approval_instances_insert_guard_0030",
        "trg_approval_instances_update_guard_0030",
        "trg_approval_instances_delete_guard_0030",
        "trg_approval_steps_insert_guard_0030",
        "trg_approval_steps_update_guard_0030",
        "trg_approval_steps_delete_guard_0030",
        "trg_approval_step_candidates_insert_guard_0030",
        "trg_approval_actions_insert_guard_0030",
        "trg_approval_step_line_decisions_current_guard_0030",
        "trg_approval_return_line_facts_insert_guard_0030",
        "trg_approval_return_line_facts_immutable_update_0030",
        "trg_approval_return_line_facts_immutable_delete_0030",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    preserved_triggers = _capture_and_drop_sqlite_dependency_triggers()
    op.drop_table("approval_return_line_facts")
    op.drop_index("uq_approval_steps_one_current_0030", table_name="approval_steps")
    with op.batch_alter_table("approval_actions", recreate="always") as batch:
        batch.drop_constraint("fk_approval_actions_step_instance_0030", type_="foreignkey")
        batch.drop_constraint("uq_approval_actions_causal_identity_0030", type_="unique")
    with op.batch_alter_table("approval_instances", recreate="always") as batch:
        batch.drop_constraint("fk_approval_instances_current_step_0030", type_="foreignkey")
        batch.drop_constraint("ck_approval_instances_current_pointer_0030", type_="check")
        batch.drop_column("current_step_id")
    with op.batch_alter_table("approval_steps", recreate="always") as batch:
        batch.drop_constraint("fk_approval_steps_reopened_from_instance_0030", type_="foreignkey")
        batch.drop_constraint("fk_approval_steps_supersedes_instance_0030", type_="foreignkey")
        batch.drop_constraint("ck_approval_steps_rework_pointer_0030", type_="check")
        batch.drop_constraint("uq_approval_steps_causal_identity_0030", type_="unique")
        batch.drop_column("reopened_from_step_id")
        batch.drop_column("supersedes_step_id")
    _recreate_sqlite_triggers(preserved_triggers)
    _replace_sqlite_external_reject_guards(allow_reject_lines=False)


def _capture_and_drop_sqlite_dependency_triggers() -> list[str]:
    rows = op.get_bind().execute(
        sa.text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='trigger' AND sql IS NOT NULL AND ("
            "lower(sql) LIKE '%approval_steps%' OR "
            "lower(sql) LIKE '%approval_instances%' OR "
            "tbl_name='approval_actions') ORDER BY name"
        )
    ).mappings().all()
    statements = [str(row["sql"]) for row in rows]
    for row in rows:
        name = str(row["name"]).replace('"', '""')
        op.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    return statements


def _recreate_sqlite_triggers(statements: list[str]) -> None:
    for statement in statements:
        op.execute(statement)


def _restore_sqlite_0029_guards() -> None:
    op.execute(
        f"""
CREATE TRIGGER trg_approval_instances_insert_guard_0029 BEFORE INSERT ON approval_instances
WHEN NOT EXISTS (
 SELECT 1 FROM material_request_revisions revision JOIN material_requests request ON request.id=revision.request_id
 WHERE revision.id=NEW.request_revision_id AND revision.request_id=NEW.request_id
   AND revision.revision_no=NEW.revision_no AND revision.status='sealed'
   AND request.revision_no=NEW.revision_no
   AND request.status IN ('draft','returned','submitted','approval_in_progress')
) OR (NEW.attempt_no>1 AND NOT EXISTS (
 SELECT 1 FROM approval_instances previous WHERE previous.request_id=NEW.request_id
  AND previous.attempt_no=NEW.attempt_no-1 AND previous.status IN ('returned','superseded')
)) BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_instances_update_guard_0029 BEFORE UPDATE ON approval_instances
WHEN NEW.id IS NOT OLD.id OR NEW.request_id IS NOT OLD.request_id
 OR NEW.request_revision_id IS NOT OLD.request_revision_id OR NEW.revision_no IS NOT OLD.revision_no
 OR NEW.route_version_id IS NOT OLD.route_version_id OR NEW.attempt_no IS NOT OLD.attempt_no
 OR NEW.created_at IS NOT OLD.created_at
 OR OLD.status IN ('completed','rejected','withdrawn','cancelled','superseded')
 OR (OLD.status='returned' AND NEW.status<>'superseded')
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_instances_delete_guard_0029 BEFORE DELETE ON approval_instances
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_steps_no_delete_0029 BEFORE DELETE ON approval_steps
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
