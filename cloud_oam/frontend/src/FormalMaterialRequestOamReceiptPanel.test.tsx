// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import FormalMaterialRequestOamReceiptPanel from "./FormalMaterialRequestOamReceiptPanel";
import { afterOutbound } from "./materialRequestOutboundTestFixtures";

afterEach(cleanup);

it("renders OAM evidence as a read-only history", async () => {
  const adapter = { listOamReceiptEvidence: vi.fn(async () => [{
    schema_version: "1.0", evidence_id: "11111111-1111-4111-8111-000000000001",
    external_object_id: "11111111-1111-4111-8111-000000000002",
    shipment_id: "11111111-1111-4111-8111-000000000003", status: "synced",
    source_time: "2026-09-11T08:00:00Z", source_version: "oam-receipt-v1", payload_sha256: "a".repeat(64),
  }]) } as any;
  render(<FormalMaterialRequestOamReceiptPanel adapter={adapter} detail={afterOutbound()} />);
  expect(await screen.findByText("OAM 收货证据")).toBeTruthy();
  expect(await screen.findByText("已同步")).toBeTruthy();
  expect(screen.getByText("oam-receipt-v1")).toBeTruthy();
  expect(adapter.listOamReceiptEvidence).toHaveBeenCalledTimes(1);
});
