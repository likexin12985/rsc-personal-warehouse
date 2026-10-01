"""Names shared with immutable migration 0159; no DDL at request time."""
TABLE = 'stock_loss_request_key_bindings'
REGISTER = 'rsc_register_loss_request_binding_0159'
KINDS = {'inverse': ('stock_loss_disposition_reversals', 'reverse_loss', 'inverse_id'), 'approval': ('stock_loss_correction_decisions', 'approve_loss_correction', 'approval_id'), 'correction': ('stock_loss_correction_executions', 'correct_loss', 'correction_id'), 'inverse_seal': ('stock_loss_inverse_request_seals', 'reverse_loss', 'seal_id')}

KINDS.update({
    "approval_seal": ("stock_loss_correction_approval_seals", "approve_loss_correction", "approval_seal_id"),
    "correction_seal": ("stock_loss_correction_execution_seals", "correct_loss", "correction_seal_id"),
})
