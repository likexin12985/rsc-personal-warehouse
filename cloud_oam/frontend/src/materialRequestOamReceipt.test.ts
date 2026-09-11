import { expect, it } from "vitest";
import { validateOamReceiptEvidence } from "./materialRequestOamReceipt";

const id = (n: number) => `11111111-1111-4111-8111-${String(n).padStart(12, "0")}`;
const row = () => ({ schema_version: "1.0", evidence_id: id(1), external_object_id: id(2), shipment_id: id(3), status: "synced", source_time: "2026-09-11T08:00:00Z", source_version: "oam-receipt-v1", payload_sha256: "a".repeat(64) });

it("validates immutable OAM receipt evidence", () => {
  expect(validateOamReceiptEvidence(row()).status).toBe("synced");
  expect(() => validateOamReceiptEvidence({ ...row(), source_time: "2026-09-11 08:00:00" })).toThrow();
  expect(() => validateOamReceiptEvidence({ ...row(), payload_sha256: "A".repeat(64) })).toThrow();
  expect(() => validateOamReceiptEvidence({ ...row(), extra: true })).toThrow();
});
