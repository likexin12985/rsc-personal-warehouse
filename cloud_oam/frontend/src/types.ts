export type User = {
  id: string;
  mobile: string;
  name: string;
  role: string;
  province: string | null;
  require_password_change: boolean;
  employee_no?: string;
  organization_code?: string;
  organization_name?: string;
};

export type FormalUser = User & {
  account_status: string;
  employment_status: string;
  access_mode: "active" | "restricted_handover";
  authorization_version: number;
  role_codes: string[];
};

export type AuthenticatedUser = Partial<FormalUser> & {
  person_id?: string;
  name: string;
  employee_no?: string;
  organization_code?: string;
  organization_name?: string;
  account_status?: string;
  employment_status?: string;
  access_mode?: "active" | "restricted_handover";
  authorization_version?: number;
  role_codes?: string[];
};

export type InventoryScope = {
  scope_type: "national" | "organization" | "person";
  scope_id: string;
};

export type InventorySummary = {
  schema_version: "1.0";
  projection_status: "not_initialized" | "ready";
  opening_balance_status: "not_established" | "established";
  projected_at: string | null;
  ledger_cursor: number;
  scopes: InventoryScope[];
  quantity_status: "opening_not_established" | "material_filter_required";
  physical_in_stock_qty: string | null;
  available_qty: string | null;
  reserved_qty: string | null;
  committed_qty: string | null;
  frozen_qty: string | null;
  physical_in_transit_qty: string | null;
  expected_supply_qty: null;
  expected_supply_status: "not_available";
};

export type InventoryAccount = {
  stock_account_id: string;
  owner_org_id: string;
  owner_org_code: string;
  owner_org_name: string;
  location_owner_org_id: string;
  location_owner_org_code: string;
  location_owner_org_name: string;
  location_id: string;
  location_code: string;
  location_name: string;
  location_type: "headquarters" | "region" | "personal" | "transit" | "quarantine";
  location_parent_id: string | null;
  custodian_person_id: string | null;
  custodian_person_name: string | null;
  material_id: string;
  sku_code: string;
  material_name: string;
  base_unit: string;
  tracking_mode: "none" | "lot" | "serial" | "lot_and_serial";
  condition_code: "new" | "used" | "damaged" | "scrapped";
  availability_bucket: "available" | "reserved" | "picking" | "outbound" | "in_transit" | "arrived_pending" | "frozen" | "return_pending" | "scrap_pending";
  lot_id: string | null;
  lot_no: string | null;
  quantity_status: "opening_not_established" | "available";
  quantity: string | null;
  balance_version: number;
  ledger_cursor: number;
};

export type InventoryAccountPage = {
  schema_version: "1.0";
  projection_status: "not_initialized" | "ready";
  opening_balance_status: "not_established" | "established";
  projected_at: string | null;
  ledger_cursor: number;
  items: InventoryAccount[];
  next_after_id: string | null;
};

export type AccessContext = {
  person_id: string;
  account_status: string;
  employment_status: string;
  authorization_version: number;
  access_mode: "active" | "restricted_handover";
  role_codes: string[];
  assignments: Array<{
    assignment_id: string;
    role_code: string;
    scope_type: string;
    scope_id: string;
    valid_from: string;
    valid_to: string | null;
  }>;
  permissions: Array<{
    resource: string;
    action: string;
    field_code: string;
  }>;
};

export type ProvincialRegionOption = {
  organization_id: string;
  organization_code: string;
  organization_name: string;
  province_code: string | null;
};

export type ProvincialManagerCandidate = {
  person_id: string;
  person_name: string;
  employee_no: string;
  organization_id: string;
  organization_code: string;
  organization_name: string;
  authorization_version: number;
};

export type ProvincialManagerAssignment = {
  assignment_id: string;
  person_id: string;
  person_name: string;
  employee_no: string;
  organization_id: string;
  organization_code: string;
  organization_name: string;
  valid_from: string;
  valid_to: string | null;
  status: string;
  authorization_version: number;
};

export type ProvincialManagerMutation = {
  assignment_id: string;
  person_id: string | null;
  organization_id: string;
  role_code: "provincial_manager";
  scope_type: "organization";
  status: "active" | "revoked";
  valid_from: string;
  valid_to: string | null;
  authorization_version: number;
  audit_event_id: string;
  state_transition_event_id: string;
  replayed: boolean;
};

export type AuthSession = {
  id: string;
  user_id: string;
  user_name: string;
  mobile: string;
  client_type: "web" | "miniprogram";
  device_name: string;
  ip_address: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  is_current: boolean;
};

export type OamPersonnel = {
  id: string;
  oamAccountId: string;
  oamEmployeeId: string;
  account: string;
  jobNo: string;
  name: string;
  mobile: string;
  sourcePresent: boolean;
  sourceActive: boolean;
  loginEligible: boolean;
  eligibilityReason: string;
  loginEnabled: boolean;
  user: { id: string; role: string; province: string | null; isActive: boolean } | null;
  updatedAt: string;
};

export type OamOrderLine = {
  id?: string;
  materialApplyId: string;
  materialCode?: string;
  materialName?: string;
  materialModel?: string;
  applyNum?: number;
  deliveryNum?: number;
  receivedNum?: number;
  unitName?: string;
  remark?: string;
};

export type OamOrder = {
  materialApplyId: string;
  type: number;
  status: string;
  transferStatus?: string;
  applicantName?: string;
  principalName?: string;
  sourceCompanyName?: string;
  targetCompanyName?: string;
  warehouseLocationName?: string;
  warehousePositionName?: string;
  createTime?: string;
  updateTime?: string;
  info?: string;
  lines: OamOrderLine[];
};

export type Warehouse = {
  id: string;
  code: string;
  name: string;
  province: string;
  city: string;
  warehouse_type: string;
  condition_scope: string;
  warehouse_level: string;
  ownership_type: string;
  position_scope: string;
  parent_warehouse_id: string | null;
};

export type Material = {
  id: string;
  code: string;
  name: string;
  specification: string;
  category: string;
  aliases: string;
  unit: string;
};

export type InventoryRow = {
  id: string;
  warehouse: { id: string; name: string; province: string };
  holder: { id: string; name: string } | null;
  material: { id: string; code: string; name: string; unit: string };
  condition: string;
  onHand: number;
  occupied: number;
  inTransit: number;
  available: number;
  updatedAt: string;
};

export type TransferItem = {
  id: string;
  materialId: string;
  code: string;
  name: string;
  quantity: number;
  condition: string;
  remark: string;
  unit: string;
};

export type Transfer = {
  id: string;
  number: string;
  transferType: string;
  status: string;
  sourceWarehouse: { id: string; name: string } | null;
  sourceHolder: { id: string; name: string } | null;
  targetWarehouse: { id: string; name: string; province: string };
  recipient: { id: string; name: string } | null;
  requester: { id: string; name: string } | null;
  approvedBy: string | null;
  workOrderNumber: string;
  logisticsCompany: string;
  trackingNumber: string;
  externalReference: string;
  note: string;
  createdBy: string;
  createdById: string;
  createdAt: string;
  dispatchedAt: string | null;
  receivedAt: string | null;
  approvedAt: string | null;
  items: TransferItem[];
};

export type WorkOrderMaterial = {
  id: string;
  workOrderNumber: string;
  status: string;
  warehouse: { id: string; name: string; province: string };
  user: { id: string; name: string };
  material: { id: string; code: string; name: string; unit: string };
  condition: string;
  quantity: number;
  recoveryCondition: string;
  note: string;
  createdBy: string;
  createdAt: string;
  settledAt: string | null;
};

export type WorkOrderListRow = {
  id: string;
  code: string;
  serviceCode: string;
  type: string;
  status: string;
  statusCode: string;
  title: string;
  executor: string;
  executorId?: string;
  executorPhone?: string;
  company: string;
  province: string;
  city: string;
  area: string;
  brandCode: string;
  brandName: string;
  createTime: string;
  updateTime: string;
  deviceCodes: string[];
  deviceModels: string[];
  detailAvailable: boolean;
  warranty: string;
};

export type WorkOrderSnapshot = {
  id: string;
  at: string;
  completedAt: string;
  scope: string;
};

export type WorkOrderListResponse = {
  summary: {
    available: number;
    active: number;
    withDetail: number;
    statuses: Record<string, number>;
    provinces: Record<string, number>;
    types: Record<string, number>;
    warranties: Record<string, number>;
  };
  total: number;
  offset: number;
  limit: number;
  items: WorkOrderListRow[];
  snapshot: WorkOrderSnapshot | null;
};

export type WorkOrderTarget = {
  targetType: string;
  deviceCode: string;
  deviceSn: string;
  model: string;
  stationName: string;
  manufacturer: string;
  warranty: string;
  warrantyStart: string;
  warrantyEnd: string;
};

export type WorkOrderMaterialEvent = {
  kind: string;
  code: string;
  name: string;
  quantity: number;
  unit: string;
  warehouse: string;
  position: string;
  status: string;
  returnCode: string;
  time: string;
  operator: string;
};

export type WorkOrderFee = {
  id: string;
  kindCode: string;
  kind: string;
  title: string;
  materialCode: string;
  quantity: number;
  unit: string;
  unitPrice: number;
  totalPrice: number;
  status: string;
  remark: string;
  time: string;
  operator: string;
};

export type WorkOrderMedia = {
  url: string;
  name: string;
  type: string;
  size?: number;
};

export type WorkOrderCheckItem = {
  id: string;
  title: string;
  description: string;
  columnType: string;
  required: boolean;
  sequence: number;
  result: unknown;
  remark: string;
  rule: Record<string, unknown>;
  media: WorkOrderMedia[];
  targetId: string;
  targetSn: string;
  workItem: string;
};

export type WorkOrderCheckGroup = {
  id: string;
  workItem: string;
  targetId: string;
  targetSn: string;
  status: string;
  extra: Record<string, unknown>;
  items: WorkOrderCheckItem[];
};

export type WorkOrderQuotation = {
  id: string;
  code: string;
  type: string;
  totalPrice: number;
  originalTotalPrice: number;
  discount: number;
  statusCode: string;
  status: string;
  collectionStatusCode: string;
  collectionStatus: string;
  payer: string;
  payee: string;
  createTime: string;
  paymentTime: string;
  updateTime: string;
};

export type WorkOrderTimelineEvent = {
  category: string;
  time: string;
  title: string;
  actor: string;
  content: string;
  subtitle: string;
  source: string;
  tone: string;
  recordId: string;
};

export type WorkOrderDetail = {
  ok: boolean;
  queriedAt: string;
  source: string;
  summary: {
    id: string;
    code: string;
    serviceCode: string;
    externalCode: string;
    type: string;
    status: string;
    statusCode: string;
    title: string;
    overview: string;
    executor: string;
    executorPhone: string;
    company: string;
    province: string;
    city: string;
    area: string;
    address: string;
    customerName: string;
    customerPhone: string;
    brandCode: string;
    brandName: string;
    priority: string;
    warranty: string;
    warrantyMode: string;
    createTime: string;
    updateTime: string;
    completedAt: string;
    source: string;
    sourceSecondary: string;
  };
  counts: Record<string, number>;
  targets: WorkOrderTarget[];
  materials: WorkOrderMaterialEvent[];
  fees: WorkOrderFee[];
  workItems: WorkOrderCheckGroup[];
  quotations: WorkOrderQuotation[];
  relatedOrders: Array<{
    id: string;
    code: string;
    serviceCode: string;
    type: string;
    status: string;
    title: string;
    createTime: string;
  }>;
  timeline: WorkOrderTimelineEvent[];
  errors: Array<{ section?: string; message?: string }>;
  listRow: WorkOrderListRow;
  snapshot: WorkOrderSnapshot | null;
};

export type StocktakeItem = {
  id: string;
  materialId: string;
  code: string;
  name: string;
  unit: string;
  condition: string;
  expected: number;
  counted: number | null;
  difference: number | null;
  remark: string;
};

export type Stocktake = {
  id: string;
  number: string;
  status: string;
  warehouse: { id: string; name: string; province: string };
  assignee: { id: string; name: string };
  deadline: string | null;
  note: string;
  createdAt: string;
  submittedAt: string | null;
  closedAt: string | null;
  progress: { counted: number; total: number };
  difference: number;
  attachmentCount: number;
  items: StocktakeItem[];
};
