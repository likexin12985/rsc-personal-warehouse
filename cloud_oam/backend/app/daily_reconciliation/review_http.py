"""HTTP DTO and transport checks; identities and scopes come from live DB reads."""
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field
from .review_core import Request,Receipt

class CommandInput(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    expected_authorization_version:Annotated[int,Field(strict=True,ge=1)]
    command:Request

class MissingReceipt(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    recorded:Literal[False]=False
    stock_written:Literal[False]=False

class CommandOutput(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    receipt:Receipt|MissingReceipt
