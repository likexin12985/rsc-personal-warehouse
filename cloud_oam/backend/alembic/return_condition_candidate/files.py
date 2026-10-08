"""Isolated PG16 condition upload guard, not an installed Alembic revision.

The caller must verify the current file function body before applying these
statements inside an owned candidate transaction. Retains all earlier purpose
guards; the eventual formal migration also needs retention/runtime catalog
transitions and business attachment binding/reverse-parent guards.
"""
import hashlib
from pathlib import Path
import runpy

old = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'versions'
                       / '20261122_0143_stock_loss_evidence_purpose.py'))
PURPOSE = 'return_condition_evidence'
FUNCTION = 'rsc_condition_file_authority'
COMMIT_FUNCTION = 'rsc_condition_file_commit'
SIGNATURE = 'public.rsc_guard_formal_file_object_0036()'
EXPECTED_BODY = old['NEW_BODY']
EXPECTED_SHA256 = old['NEW_FILE_HASH']

# Preserve the established principal graph lock and post-lock DB clock checks.
# This purpose admits only the new condition actions on their exact direct
# roles. A personal loss permission or a borrowed role cannot issue an upload.
AUTHORITY = old['AUTHORITY'].replace("r.code='technician'", "FALSE AND r.code='technician'")
AUTHORITY = AUTHORITY.replace("('submit_loss','review_loss_regional')",
    "('submit_return_condition','supplement_return_condition','review_return_condition_regional')")
AUTHORITY = AUTHORITY.replace("('submit_loss','finalize_loss','reverse_loss')",
    "('review_return_condition_headquarters','cancel_return_condition_approval')")
AUTHORITY = AUTHORITY.replace('NEW.uploaded_by', 'actor_id').replace(
    "(NEW.metadata_jsonb->>'authorization_version')::bigint", 'actor_version')
AUTHORITY = AUTHORITY.replace("WHERE u.id=actor_id AND u.is_active",
    "WHERE u.id=actor_id AND person.id=actor_person AND u.is_active")
AUTHORITY = AUTHORITY.replace('0143 current loss evidence authority required',
    'condition current file authority required')
AUTHORITY_BODY = '\nBEGIN\n' + AUTHORITY + '\nEND;\n'

marker = "'opening_count_import_error', 'stock_loss_evidence')"
assert EXPECTED_BODY.count(marker) == 3
BODY = EXPECTED_BODY.replace(marker, marker[:-1] + f", '{PURPOSE}')")
CALL = f"""PERFORM public.{FUNCTION}(NEW.uploaded_by,
    (NEW.metadata_jsonb->>'authorization_version')::bigint,
    (NEW.metadata_jsonb->>'uploader_person_id')::uuid);"""
BODY = BODY.replace('BEGIN\n', 'BEGIN\n' +
    f"IF TG_OP IN ('INSERT','UPDATE') AND NEW.metadata_jsonb->>'purpose'='{PURPOSE}' THEN\n"
    + CALL + '\nEND IF;\n', 1)
BODY_SHA256 = hashlib.sha256(BODY.encode()).hexdigest()


def statements():
    private = ('PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup')
    return [
        f"CREATE FUNCTION public.{FUNCTION}(actor_id text, actor_version bigint, actor_person uuid) "
        f"RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${AUTHORITY_BODY}$body$",
        f"CREATE FUNCTION public.{COMMIT_FUNCTION}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER "
        f"SET search_path=pg_catalog,public AS $body$ BEGIN IF NEW.metadata_jsonb->>'purpose'='{PURPOSE}' THEN "
        + CALL + ' END IF; RETURN NULL; END; $body$',
        f"REVOKE ALL ON FUNCTION public.{FUNCTION}(text,bigint,uuid), public.{COMMIT_FUNCTION}() FROM {private}",
        f"CREATE CONSTRAINT TRIGGER condition_file_authority_commit AFTER INSERT OR UPDATE ON public.files "
        f"DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.{COMMIT_FUNCTION}()",
        "ALTER TABLE public.files ENABLE ALWAYS TRIGGER condition_file_authority_commit",
        f"CREATE OR REPLACE FUNCTION {SIGNATURE} RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER "
        f"SET search_path=pg_catalog,public AS $body${BODY}$body$",
    ]
