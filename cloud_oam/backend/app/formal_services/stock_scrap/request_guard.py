"""Locked admission for exact, unexecuted and unsealed scrap commands.

Current read rights and full request evidence precede any stock/stage check.
The underlying writer still requires current write rights and physical facts;
the database deferred fence remains authoritative at COMMIT.
"""
from app.formal_access import lock_formal_principal_graph
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.stock_scrap_schemas import ScrapExecute, ScrapRequestLookup, validated_scrap_request
from app.stock_scrap_recovery_schemas import (
    ScrapRecoveryExecute, ScrapRecoveryRequestLookup, validated_recovery_request,
)
from . import recovery_facts, request_lookup, recovery_lookup


def prepare(db, *, actor, request, kind):
    if type(request) is ScrapExecute:
        command = validated_scrap_request(request)
        actual_kind = command.source.kind
        model, lookup = ScrapRequestLookup, request_lookup.lookup
        prefix = 'stock_scrap'
    else:
        command = validated_recovery_request(request)
        actual_kind = next((k for k, m in recovery_facts.CONTRACTS.items() if type(command) is m), None)
        if type(command) is ScrapRecoveryExecute:
            actual_kind = 'execute'
        model, lookup = ScrapRecoveryRequestLookup, recovery_lookup.lookup
        prefix = 'stock_scrap_recovery'
    if actual_kind is None or actual_kind != kind:
        raise ValueError('an exact command for this scrap action is required')
    supplied = posting._validate_supplied_actor(actor)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (supplied.user_id,))
    current = posting._require_current_actor(db, supplied)
    answer = lookup(db, actor=current, request=model(operator_person_id=current.person_id, original=command))
    if answer['request_state'] == 'sealed':
        sources._fail(prefix + '_request_sealed', '原请求已永久关闭，不能再次执行；请查看原请求结果', 409)
    if answer['request_state'] == 'found':
        sources._fail(prefix + '_request_requires_recovery', '原请求已有事实，请只读回查，禁止重复执行', 409)
    if answer['request_state'] != 'not_found' or answer['retry_allowed'] is not False:
        sources._fail(prefix + '_request_outcome_unknown', '原请求结果无法确认，禁止执行', 503)
    # not_found alone is never a public retry permit. Only this locked write
    # transaction may continue into its current authority/stock validation.
    return current, command
