"""Single isolated condition schema for submission, decision and settlement.

The shared model is preparation for the forward migration; it does not register
tables in live Base or install SQL permissions, guards or business functions.
"""
from app.return_condition_seal_schema import build_schema as initial_schema
from app.return_condition_decision_seal_schema import define as decision_seal
from app.return_condition_settlement_input_schema import define as settlement_inputs


def build_schema():
    metadata, tables, parents = initial_schema()
    decision = decision_seal(metadata)
    inputs = settlement_inputs(metadata)
    return metadata, (*tables, decision, *inputs), parents
