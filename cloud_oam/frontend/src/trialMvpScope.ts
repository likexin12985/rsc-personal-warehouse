/**
 * First-release UI boundary for the personal-warehouse pilot.
 *
 * These flags only control presentation/capability exposure. The underlying
 * routes, state axes, migrations, audit facts and recovery stores remain
 * available for a later full-V1 release.
 */
export const TRIAL_MVP_UI_SCOPE = Object.freeze({
  allowSupplyPlanning: false,
  allowLogisticsEvents: false,
  showOamReceipt: false,
  showReturnOperations: false,
  showReleaseOperations: false,
  showComplexLifecycle: false,
} as const);

export type TrialMvpUiScope = typeof TRIAL_MVP_UI_SCOPE;
