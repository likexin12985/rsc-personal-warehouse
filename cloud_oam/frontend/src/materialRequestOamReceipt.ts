const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const SHA256 = /^[0-9a-f]{64}$/;

export type OamReceiptEvidenceResult = Readonly<{
  schema_version: "1.0";
  evidence_id: string;
  external_object_id: string;
  shipment_id: string;
  status: "synced" | "exception";
  source_time: string;
  source_version: string;
  payload_sha256: string;
}>;

function exact(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) {
    throw new Error("OAM收货证据字段无效");
  }
  return value as Record<string, unknown>;
}

function id(value: unknown, field: string): string {
  if (typeof value !== "string" || !UUID.test(value) || /^0{8}-0{4}-0{4}-0{4}-0{12}$/i.test(value)) {
    throw new Error(`OAM收货证据${field}无效`);
  }
  return value;
}

function text(value: unknown, field: string, max: number): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max || value.trim() !== value
      || /[\x00-\x1f\x7f]/.test(value)) throw new Error(`OAM收货证据${field}无效`);
  return value;
}

export function validateOamReceiptEvidence(value: unknown): OamReceiptEvidenceResult {
  const row = exact(value, ["schema_version", "evidence_id", "external_object_id", "shipment_id", "status", "source_time", "source_version", "payload_sha256"]);
  if (row.schema_version !== "1.0" || (row.status !== "synced" && row.status !== "exception")
      || typeof row.source_time !== "string" || !AWARE_TIMESTAMP.test(row.source_time)
      || !Number.isFinite(Date.parse(row.source_time)) || typeof row.payload_sha256 !== "string" || !SHA256.test(row.payload_sha256)) {
    throw new Error("OAM收货证据响应无效");
  }
  return Object.freeze({
    schema_version: "1.0",
    evidence_id: id(row.evidence_id, "证据 ID"),
    external_object_id: id(row.external_object_id, "外部对象 ID"),
    shipment_id: id(row.shipment_id, "发运单 ID"),
    status: row.status,
    source_time: row.source_time,
    source_version: text(row.source_version, "来源版本", 160),
    payload_sha256: row.payload_sha256,
  });
}
