import { shipmentQuantity, shipmentUnits, type ReceiptInput, type ReceiptResult, type ShipmentResult } from "./materialRequestShipment";

type AvailableLine = { remaining: bigint; serials: ReadonlySet<string> };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const quantity = (value: unknown) => {
  if (typeof value !== "string" || !/^(?:0|[1-9]\d{0,14})\.\d{3}$/.test(value)) throw new Error("收货历史数量无效，请重新读取");
  return shipmentUnits(value);
};

/** UI preflight only: the server still locks and checks all facts at commit. */
export function receiptAvailability(shipments: readonly ShipmentResult[], receipts: readonly ReceiptResult[]): Map<string, AvailableLine> {
  const available = new Map<string, AvailableLine>(), owners = new Map<string, string>();
  for (const shipment of shipments) for (const line of shipment.lines) {
    if (available.has(line.shipment_line_id)) throw new Error("发运明细重复，请重新读取");
    const remaining = quantity(line.shipped_qty);
    if (remaining <= 0n || (line.serial_ids.length > 0 && remaining !== BigInt(line.serial_ids.length) * 1000n)) throw new Error("发运数量与 SN 不一致");
    owners.set(line.shipment_line_id, shipment.shipment_id);
    available.set(line.shipment_line_id, { remaining, serials: new Set(line.serial_ids) });
  }
  const receiptIds = new Set<string>(), receiptLines = new Set<string>();
  for (const receipt of receipts) {
    if (!receipt.lines.length || !["accepted", "exception"].includes(receipt.status) || receiptIds.has(receipt.receipt_id) || !shipments.some(row => row.shipment_id === receipt.shipment_id)) throw new Error("收货历史与发运记录不一致");
    receiptIds.add(receipt.receipt_id);
    for (const line of receipt.lines) {
      if (!line || typeof line.receipt_line_id !== "string" || !UUID.test(line.receipt_line_id) || receiptLines.has(line.receipt_line_id)
          || typeof line.shipment_line_id !== "string" || owners.get(line.shipment_line_id) !== receipt.shipment_id || !Array.isArray(line.serial_ids)) throw new Error("收货历史明细绑定无效");
      receiptLines.add(line.receipt_line_id);
      const current = available.get(line.shipment_line_id)!;
      const total = quantity(line.accepted_qty) + quantity(line.rejected_qty);
      const serials = line.serial_ids;
      if (total <= 0n || total > current.remaining) throw new Error("累计收货数量与发运记录不一致");
      if (new Set(serials).size !== serials.length || serials.some(value => typeof value !== "string" || !current.serials.has(value))
          || (current.serials.size > 0 && total !== BigInt(serials.length) * 1000n)) throw new Error("收货历史 SN 与发运记录不一致");
      available.set(line.shipment_line_id, { remaining: current.remaining - total, serials: new Set([...current.serials].filter(value => !serials.includes(value))) });
    }
  }
  return available;
}

export function remainingReceiptQuantity(value: bigint): string {
  return shipmentQuantity(`${value / 1000n}.${String(value % 1000n).padStart(3, "0")}`);
}

export function validateReceiptAvailability(input: ReceiptInput, available: ReadonlyMap<string, AvailableLine>): void {
  for (const line of input.lines) {
    const current = available.get(line.shipment_line_id), accepted = shipmentUnits(line.accepted_qty), rejected = shipmentUnits(line.rejected_qty), total = accepted + rejected;
    if (!current || total <= 0n || total > current.remaining) throw new Error("本次验收数量须大于零，且不能超过当前剩余可收数量");
    if (new Set(line.serial_ids).size !== line.serial_ids.length || line.serial_ids.some(value => !current.serials.has(value))
        || (current.serials.size > 0 && total !== BigInt(line.serial_ids.length) * 1000n)) throw new Error("SN 须属于本包尚未验收的明细，且与本次数量一致");
    if (current.serials.size > 0 && accepted > 0n && rejected > 0n) throw new Error("SN 合格与拒收请分次登记，分别选择对应 SN");
    const abnormal = rejected > 0n || line.condition !== "normal";
    if (line.condition === "normal" && rejected > 0n) throw new Error("有拒收数量时请选择相应异常验收条件");
    if (["damaged", "wrong_material", "wrong_serial", "rejected"].includes(line.condition) && accepted > 0n) throw new Error("破损、错料、错 SN 或拒收不能填写合格数量");
    if (abnormal && !line.exception_evidence_file_id) throw new Error("异常验收需要提供证据文件");
    if (!abnormal && line.exception_evidence_file_id) throw new Error("正常验收无需填写异常证据文件");
  }
}
