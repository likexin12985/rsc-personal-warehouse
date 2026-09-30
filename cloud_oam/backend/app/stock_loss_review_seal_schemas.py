"""Exact original approval commands for independent permanent request seals.

Shared by approval and seal write routes. Persistence must additionally
prove current authority, original facts, immutable audit and late-write
exclusion at PostgreSQL COMMIT before any seal can be acknowledged.
"""
import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .stock_loss_schemas import StockLossRegionalReviewIn, StockLossHeadquartersReviewIn
from .work_order_material_schemas import StrictInput


class _ReviewCommandInput(StrictInput):
    operator_person_id: UUID
    request_hash: str = Field(pattern=r'^[a-f0-9]{64}$')

    def command_intent(self):
        # Original request/key identify the command but are not part of the
        # existing approval intent hash. Do not create a new hash namespace.
        return self.original.model_dump(mode='json', exclude={'request_id', 'idempotency_key'})

    @model_validator(mode='after')
    def original_content_matches_hash(self):
        digest = hashlib.sha256(json.dumps(self.command_intent(), sort_keys=True,
            ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
        if self.request_hash != digest:
            raise ValueError('提交内容与原审批请求摘要不一致，请保留原请求核验')
        return self


class StockLossRegionalReviewCommandIn(_ReviewCommandInput):
    stage: Literal['regional'] = 'regional'
    original: StockLossRegionalReviewIn


class StockLossHeadquartersReviewCommandIn(_ReviewCommandInput):
    stage: Literal['headquarters'] = 'headquarters'
    original: StockLossHeadquartersReviewIn


class StockLossRegionalReviewSealIn(StockLossRegionalReviewCommandIn):
    """Permanently close an unresolved original regional approval request."""


class StockLossHeadquartersReviewSealIn(StockLossHeadquartersReviewCommandIn):
    """Permanently close an unresolved original headquarters approval request."""
