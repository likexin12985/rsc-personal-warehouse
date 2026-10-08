"""Private dispatch at the existing unified posting authority boundary.

This staged module is not yet connected to inventory_posting. Neither a
preparation result nor a caller-provided object grants settlement authority.
"""
from . import return_condition_posting_authority as initial
from . import return_condition_settlement_permit as settlement


def require(db, **arguments):
    permit = arguments.get('permit')
    if type(permit) is settlement.SettlementPermit:
        return settlement.require(db, **arguments)
    return initial.require(db, **arguments)
