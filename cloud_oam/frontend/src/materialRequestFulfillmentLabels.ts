const RECEIPT_STATUS: Readonly<Record<string, string>> = {
  draft: "草稿", partially_accepted: "部分验收", accepted: "已验收",
  exception: "验收异常", rejected: "已拒收",
};
const INBOUND_STATUS: Readonly<Record<string, string>> = {
  pending: "待入账", posted: "已过账", exception: "入账异常",
};
export const receiptStatusLabel = (status: string): string => Object.prototype.hasOwnProperty.call(RECEIPT_STATUS, status) ? RECEIPT_STATUS[status] : "未知收货状态";
export const inboundStatusLabel = (status: string): string => Object.prototype.hasOwnProperty.call(INBOUND_STATUS, status) ? INBOUND_STATUS[status] : "未知入账状态";
