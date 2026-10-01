"""Complete original-command seal contracts; no HTTP route is activated."""
from uuid import UUID
from pydantic import BaseModel, ConfigDict

from .stock_loss_schemas import StockLossDispositionExecuteIn
from .stock_loss_return_schemas import StockLossReturnExecuteIn
from .formal_services import stock_loss_disposition_plan, stock_loss_return_plan
from .formal_services import inventory_posting as posting, stock_loss_sources as sources


def command_document(command, flow):
    if flow == 'disposition':
        schema, service = StockLossDispositionExecuteIn, stock_loss_disposition_plan
    elif flow == 'return':
        schema, service = StockLossReturnExecuteIn, stock_loss_return_plan
    else:
        raise ValueError('explicit execution flow required')
    command = schema.model_validate(command.model_dump())
    document = dict(intent=service.intent(command), request_id=command.request_id,
        expected_plan_hash=command.expected_plan_hash)
    raw_key = posting._require_idempotency_key(command.idempotency_key)
    return dict(command=command, command_jsonb=document, request_hash=sources._hash(document),
        request_reference=posting._request_reference(command.request_id),
        disposition_key_hash=posting._storage_hash('stock-loss-disposition:' + raw_key),
        return_key_hash=posting._storage_hash('stock-loss-return:' + raw_key))


class StockLossDispositionSealIn(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    operator_person_id: UUID
    original: StockLossDispositionExecuteIn

    def proof(self):
        return command_document(self.original, 'disposition')


class StockLossDerivedReturnSealIn(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    operator_person_id: UUID
    original: StockLossReturnExecuteIn

    def proof(self):
        return command_document(self.original, 'return')
