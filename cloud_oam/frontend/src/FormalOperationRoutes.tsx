import { lazy, useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, apiNoReplay } from './api';
import type { AccessContext } from './types';
import { lossReviewStages, scrapRecoveryStages } from './formalOperationScopes';
import { createFormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
import { createFormalStocktakeAdapter } from "./formalStocktakeAdapter";
import { createAdapter as createScrapOriginalAdapter } from "./scrapOriginalAdapter";
import { createAdapter as createScrapRecoveryAdapter } from "./scrapRecoveryAdapter";
import { createAdapter as createScrapCorrectionAdapter } from "./scrapCorrectionAdapter";
import { createAdapter as createLossCorrectionAdapter } from "./lossCorrectionAdapter";
import { createAdapter as createLossExecutionAdapter } from "./lossExecutionAdapter";
import { createAdapter as createLossSendingAdapter } from "./lossSenderAdapter";
import { createAdapter as createLossSubmissionAdapter } from "./lossSubmissionAdapter";
import { createAdapter as createReturnReceivingAdapter } from "./returnReceivingAdapter";
import { createAdapter as createLossReviewAdapter } from "./lossReviewAdapter";
import { createConditionAdapter } from './returnConditionAdapter';
import { createAdapter as createRejectionWarehouseAdapter } from './rejectionWarehouseAdapter';

const FormalMaterialRequestsPage = lazy(() => import("./pages/FormalMaterialRequests"));
const FormalStocktakesPage = lazy(() => import("./pages/FormalStocktakes"));
const FormalLossReviews = lazy(() => import("./FormalLossReviews"));
const FormalLossExecution = lazy(() => import("./FormalLossExecution"));
const FormalOriginalScrap = lazy(() => import("./FormalOriginalScrap"));
const FormalCorrectionScrap = lazy(() => import("./FormalCorrectionScrap"));
const FormalScrapRecovery = lazy(() => import("./FormalScrapRecoveryPage"));
const FormalLossCorrection = lazy(() => import("./FormalLossCorrection"));
const FormalReturnReceiving = lazy(() => import("./FormalReturnReceivingPage"));
const FormalLossSubmissionPage = lazy(() => import("./FormalLossSubmissionPage"));
const FormalLossSendingPage = lazy(() => import("./FormalLossSendingPage"));
const FormalReturnConditionPage = lazy(() => import('./FormalReturnConditionPage'));
const FormalReturnConditionInbox = lazy(() => import('./FormalReturnConditionInbox'));
const FormalRejectionWarehouse = lazy(() => import('./FormalRejectionWarehousePage'));

export function FormalReturnConditionInboxRoute({ access }: { access: AccessContext }) {
  const navigate = useNavigate();
  const adapter = useMemo(() => createConditionAdapter(access.person_id, apiNoReplay), [access.person_id]);
  return <FormalReturnConditionInbox identity={{ person_id: access.person_id, authorization_version: access.authorization_version }}
    adapter={adapter} onOpen={inbound => navigate(`/return-condition-corrections/${inbound}`)} />;
}

export function FormalReturnConditionRoute({ access }: { access: AccessContext }) {
  const { inboundLineId = '' } = useParams();
  const navigate = useNavigate();
  const adapter = useMemo(() => createConditionAdapter(access.person_id, apiNoReplay), [access.person_id]);
  return <FormalReturnConditionPage identity={{ person_id: access.person_id, authorization_version: access.authorization_version }}
    inboundId={inboundLineId} adapter={adapter} onBack={() => navigate(-1)} />;
}

export function FormalMaterialRequestsRoute({ access }: { access: AccessContext }) {
  const adapter = useMemo(() => createFormalMaterialRequestAdapter({
    person_id: access.person_id,
    authorization_version: access.authorization_version,
  }), [access.person_id, access.authorization_version]);
  return <FormalMaterialRequestsPage adapter={adapter} />;
}

export function FormalStocktakesRoute({ access }: { access: AccessContext }) {
  const adapter = useMemo(() => createFormalStocktakeAdapter({
    person_id: access.person_id,
    authorization_version: access.authorization_version,
  }, api, apiNoReplay, apiNoReplay), [access.person_id, access.authorization_version]);
  return <FormalStocktakesPage adapter={adapter} />;
}

export function FormalLossExecutionRoute({ access }: { access: AccessContext }) {
  const navigate = useNavigate();
  const adapter = useMemo(() => createLossExecutionAdapter(access.person_id, apiNoReplay), [access.person_id]);
  return <FormalLossExecution identity={{ person_id: access.person_id, authorization_version: access.authorization_version }} adapter={adapter}
    onOpenCorrection={root => navigate(`/loss-corrections/${root}`)}
    onOpenScrap={(operation, decision) => navigate(operation && decision ? `/loss-scraps/${operation}/${decision}` : "/loss-scraps")} />;
}

export function FormalScrapRecoveryRoute({ access }: { access: AccessContext }) {
  const adapter = useMemo(() => createScrapRecoveryAdapter(access.person_id, apiNoReplay), [access.person_id]);
  return <FormalScrapRecovery identity={{ person_id: access.person_id, authorization_version: access.authorization_version }}
    adapter={adapter} stages={scrapRecoveryStages(access)} />;
}

export function FormalOriginalScrapRoute({ access }: { access: AccessContext }) {
  const { operationId, decisionId } = useParams();
  const navigate = useNavigate();
  const adapter = useMemo(() => createScrapOriginalAdapter(access.person_id, apiNoReplay), [access.person_id]);
  return <FormalOriginalScrap identity={{ person_id: access.person_id, authorization_version: access.authorization_version }}
    adapter={adapter} operationId={operationId} decisionId={decisionId} onBack={() => navigate('/loss-execution')} />;
}

export function FormalCorrectionScrapRoute({ access }: { access: AccessContext }) {
  const { rootId, decisionId } = useParams();
  const navigate = useNavigate();
  const adapter = useMemo(() => createScrapCorrectionAdapter(access.person_id, apiNoReplay), [access.person_id]);
  return <FormalCorrectionScrap identity={{ person_id: access.person_id, authorization_version: access.authorization_version }}
    adapter={adapter} rootId={rootId} decisionId={decisionId}
    onBack={() => navigate(rootId ? `/loss-corrections/${rootId}` : '/loss-execution')} />;
}

export function FormalLossCorrectionRoute({ access }: { access: AccessContext }) {
  const { rootId = '' } = useParams();
  const navigate = useNavigate();
  const adapter = useMemo(() => createLossCorrectionAdapter(access.person_id, apiNoReplay), [access.person_id]);
  return <FormalLossCorrection identity={{ person_id: access.person_id, authorization_version: access.authorization_version }}
    rootId={rootId} adapter={adapter} onBack={() => navigate('/loss-execution')}
    onOpenCondition={inbound => navigate(`/return-condition-corrections/${inbound}`)}
    onOpenScrap={decision => navigate(decision ? `/loss-correction-scraps/${rootId}/${decision}` : '/loss-correction-scraps')} />;
}

export function FormalLossReviewsRoute({ access }: { access: AccessContext }) {
  const adapter = useMemo(() => createLossReviewAdapter(access.person_id, apiNoReplay), [access.person_id]);
  return <FormalLossReviews identity={{ person_id: access.person_id, authorization_version: access.authorization_version }}
    stages={lossReviewStages(access)} adapter={adapter} />;
}

export function FormalLossSubmissionRoute({ access }: { access: AccessContext }) {
  const adapter = useMemo(() => createLossSubmissionAdapter(access.person_id, apiNoReplay), [access.person_id]);
  return <FormalLossSubmissionPage identity={{ person_id: access.person_id, authorization_version: access.authorization_version }} adapter={adapter} />;
}

export function FormalLossSendingRoute({ access }: { access: AccessContext }) {
  const adapters = useMemo(() => ({
    outbound_return: createLossSendingAdapter(access.person_id, "outbound_return", apiNoReplay),
    ship_return: createLossSendingAdapter(access.person_id, "ship_return", apiNoReplay),
  }), [access.person_id]);
  return <FormalLossSendingPage identity={{ person_id: access.person_id, authorization_version: access.authorization_version }} adapters={adapters} />;
}

export function FormalReturnReceivingRoute({ access }: { access: AccessContext }) {
  const navigate = useNavigate();
  const adapter = useMemo(() => createReturnReceivingAdapter(access.person_id, apiNoReplay), [access.person_id]);
  const condition = useMemo(() => createConditionAdapter(access.person_id, apiNoReplay), [access.person_id]);
  return <>{access.permissions.some(p => p.resource === 'inventory' && p.action === 'read' && p.field_code === '') &&
    <nav aria-label="退回来源"><button onClick={() => navigate('/rejection-return-receiving')}>需求拒收退回：来源仓验收与入账</button></nav>}
    <FormalReturnReceiving identity={{ person_id: access.person_id, authorization_version: access.authorization_version }} adapter={adapter}
      readConditionReceipt={condition.receipt} onOpenCondition={inbound => navigate(`/return-condition-corrections/${inbound}`)} /></>;
}

export function FormalRejectionWarehouseRoute({ access }: { access: AccessContext }) {
  const navigate = useNavigate();
  const adapter = useMemo(() => createRejectionWarehouseAdapter(access.person_id, apiNoReplay), [access.person_id, access.authorization_version]);
  return <FormalRejectionWarehouse identity={{ person_id: access.person_id, authorization_version: access.authorization_version }} adapter={adapter}
    onBack={() => navigate('/return-receiving')} />;
}
