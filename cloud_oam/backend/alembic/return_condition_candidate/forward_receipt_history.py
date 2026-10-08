"""Preserve historical loss-receipt files after an identity version changes.

Forward candidate only. New receipts still use the original current-identity
binding. Historical reads retain the exact original uploader, person, version,
purpose, completion and receipt bindings, without reauthorizing the old upload.
The installer must verify the complete predecessor body before replacement.
"""
from hashlib import sha256
from pathlib import Path
import runpy

folder = Path(__file__).resolve().parents[1] / 'versions'
old = runpy.run_path(str(folder / '20261204_0155_stock_loss_return_receipts.py'))
SIGNATURE = 'public.rsc_check_loss_receipt_0155(uuid,boolean)'
EXPECTED_BODY = old['BODY']
EXPECTED_SHA256 = '13d4ffac2caf45c22be48e1a1441598cf58b0d5270f9c94420b9807577398ed4'
if sha256(EXPECTED_BODY.encode()).hexdigest() != EXPECTED_SHA256:
    raise ValueError('historical receipt predecessor drift')
CURRENT_BINDING = old['receipts']['_file_binding']()
files = runpy.run_path(str(folder / '20260926_0086_receipt_evidence_files.py'))['_files'](True)
HISTORICAL_BINDING = files['_postgresql_binding_file_sql'](
    file_expression='exception.evidence_file_id', purpose='receipt_exception_evidence',
    user_expression='fact.actor_user_id', person_expression='fact.operator_person_id',
    bound_at_expression='fact.created_at', require_current_identity=False)
if EXPECTED_BODY.count(CURRENT_BINDING) != 1:
    raise ValueError('historical receipt file binding anchor drift')
BODY = EXPECTED_BODY.replace(CURRENT_BINDING,
    '(CASE WHEN require_current IS FALSE THEN (' + HISTORICAL_BINDING
    + ') ELSE (' + CURRENT_BINDING + ') END)', 1)
BODY_SHA256 = sha256(BODY.encode()).hexdigest()
DEFINITION = ('CREATE OR REPLACE FUNCTION public.rsc_check_loss_receipt_0155('
    'checked_receipt uuid, require_current boolean) RETURNS void LANGUAGE plpgsql '
    'VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body$'
    + BODY + '$body$')


def statements():
    return [DEFINITION]
