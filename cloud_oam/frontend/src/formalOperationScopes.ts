import { hasFormalPermission, hasFormalRole } from './clientPolicy';
import type { AccessContext } from './types';
import type { Stage as LossReviewStage } from './formalLossReview';
import type { Stage as ScrapRecoveryStage } from './scrapRecoverySources';

export function lossReviewStages(access: AccessContext): LossReviewStage[] {
  if (!hasFormalPermission(access, "stock_operation", "read")) return [];
  return [
    ...(hasFormalRole(access, "provincial_manager") ? ["regional" as const] : []),
    ...(hasFormalRole(access, "admin") ? ["headquarters" as const] : []),
  ];
}

export function scrapRecoveryStages(access: AccessContext): ScrapRecoveryStage[] {
  if (!hasFormalPermission(access, "stock_operation", "read")) return [];
  return [
    ...(hasFormalRole(access, "provincial_manager") ? ["regional" as const] : []),
    ...(hasFormalRole(access, "admin") ? ["headquarters" as const, "execute" as const] : []),
  ];
}
