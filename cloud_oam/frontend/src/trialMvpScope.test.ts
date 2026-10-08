import { describe, expect, it } from "vitest";

import { TRIAL_MVP_UI_SCOPE } from "./trialMvpScope";

describe("trial MVP UI scope", () => {
  it("keeps post-fulfillment expansion closed while retaining the minimum shipment path", () => {
    expect(TRIAL_MVP_UI_SCOPE).toEqual({
      allowSupplyPlanning: false,
      allowLogisticsEvents: false,
      showOamReceipt: false,
      showReturnOperations: false,
      showReleaseOperations: false,
      showComplexLifecycle: false,
    });
    expect(Object.isFrozen(TRIAL_MVP_UI_SCOPE)).toBe(true);
  });
});
