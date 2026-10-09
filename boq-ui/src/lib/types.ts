// Mirrors the API's response schemas (see /docs on the API). Numbers arrive as JSON numbers;
// finance fields (rate/amount/value) are null for users without the finance.view permission.

export type ID = string;

export interface Ref {
  id: ID;
  code?: string | null;
  name: string;
}

export interface MaterialBrief {
  id: ID;
  code: string;
  name: string;
  uom: string;
}

export interface StoreBrief {
  id: ID;
  code: string;
  name: string;
}

export interface SiteBrief {
  id: ID;
  code: string;
  name: string;
  project_id: ID;
  project_name?: string | null;
}

export interface Approval {
  stage: string;
  action: string;
  actor_id: ID;
  actor_name?: string | null;
  comment?: string | null;
  acted_at: string;
}

export interface Me {
  id: ID;
  name: string;
  email: string;
  phone?: string | null;
  must_change_password: boolean;
  roles: string[];
  permissions: string[];
  show_finance: boolean;
  scopes: {
    all: boolean;
    projects: { id: ID; code: string; name: string }[];
    sites: { id: ID; code: string; name: string; project_id: ID }[];
    stores: { id: ID; code: string; name: string; type: string }[];
  };
  company_name: string;
  financial_year: string;
  today: string;
}

export interface Material {
  id: ID;
  code: string;
  name: string;
  category: Ref;
  base_uom: Ref;
  hsn_code?: string | null;
  gst_rate: number;
  specification?: string | null;
  reorder_level: number;
  is_active: boolean;
  version: number;
}

export interface Site {
  id: ID;
  code: string;
  name: string;
  project_id: ID;
  project: Ref;
  store_id?: ID | null;
  is_active: boolean;
}

export interface BoqItem {
  id: ID;
  line_no: number;
  material: MaterialBrief;
  planned_qty: number;
  estimated_rate: number | null;
  estimated_amount: number | null;
  benchmark_rate: number | null;
  remarks?: string | null;
  allocations: { id: ID; project_id: ID; project_name: string; site_id: ID | null; site_name: string | null; allocated_qty: number }[];
}

export interface BoqSummary {
  id: ID;
  boq_no: string;
  title: string;
  financial_year: string;
  revision: number;
  parent_boq_id?: ID | null;
  status: string;
  total_estimated_value: number | null;
  item_count: number;
  remarks?: string | null;
  version: number;
  created_by_name?: string | null;
  approved_by_name?: string | null;
  approved_at?: string | null;
  submitted_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Boq extends BoqSummary {
  items: BoqItem[];
  approvals: Approval[];
}

export interface ProgressLine {
  material_id: ID;
  material_code: string;
  material_name: string;
  uom: string;
  planned_qty: number;
  accepted_inward: number;
  pending_to_procure: number;
  excess_inward: number;
  issued_qty: number;
  balance_to_issue: number;
  central_stock: number;
  procurement_pct: number | null;
  utilisation_pct: number | null;
  estimated_rate: number | null;
  actual_avg_rate: number | null;
  benchmark_rate: number | null;
  rate_variance_pct: number | null;
  estimated_value: number | null;
  procured_value: number | null;
  issued_value: number | null;
  stock_value: number | null;
}

export interface GrnItem {
  id: ID;
  line_no: number;
  material: MaterialBrief;
  received_qty: number;
  rejected_qty: number;
  accepted_qty: number;
  rate: number | null;
  gst_rate: number;
  amount: number | null;
  gst_amount: number | null;
  rejection_reason?: string | null;
}

export interface GrnSummary {
  id: ID;
  grn_no: string;
  status: string;
  version: number;
  store: StoreBrief;
  vendor_id: ID;
  vendor_name: string;
  boq_id: ID;
  boq_no: string;
  received_date: string;
  invoice_no?: string | null;
  invoice_date?: string | null;
  challan_no?: string | null;
  vehicle_no?: string | null;
  remarks?: string | null;
  total_amount: number | null;
  total_gst: number | null;
  item_count: number;
  created_by_id?: ID | null;
  created_by_name?: string | null;
  posted_by_name?: string | null;
  posted_at?: string | null;
  cancelled_at?: string | null;
  cancel_reason?: string | null;
  created_at: string;
}

export interface Grn extends GrnSummary {
  items: GrnItem[];
}

export interface RfqItem {
  id: ID;
  line_no: number;
  material: MaterialBrief;
  requested_qty: number;
  approved_qty: number | null;
  issued_qty: number;
  pending_issue_qty: number;
  central_stock: number;
  remarks?: string | null;
}

export interface RfqSummary {
  id: ID;
  request_no: string;
  status: string;
  version: number;
  priority: string;
  project_id: ID;
  project_name: string;
  site: SiteBrief;
  store: StoreBrief;
  request_date: string;
  required_by?: string | null;
  purpose?: string | null;
  item_count: number;
  submitted_at?: string | null;
  first_issued_at?: string | null;
  created_by_id?: ID | null;
  created_by_name?: string | null;
  created_at: string;
}

export interface Rfq extends RfqSummary {
  items: RfqItem[];
  approvals: Approval[];
  issue_notes: { id: ID; issue_no: string; status: string; issue_date: string }[];
}

export interface IssueItem {
  id: ID;
  line_no: number;
  request_item_id: ID;
  material: MaterialBrief;
  issued_qty: number;
  approved_qty: number | null;
  rate: number | null;
  amount: number | null;
  received_qty: number | null;
  shortage_qty: number;
  damaged_qty: number;
  central_stock: number;
}

export interface IssueSummary {
  id: ID;
  issue_no: string;
  status: string;
  version: number;
  request_id: ID;
  request_no: string;
  project_name: string;
  site: SiteBrief;
  from_store: StoreBrief;
  to_store: StoreBrief;
  issue_date: string;
  vehicle_no?: string | null;
  driver_name?: string | null;
  driver_phone?: string | null;
  remarks?: string | null;
  total_value: number | null;
  item_count: number;
  created_by_id?: ID | null;
  created_by_name?: string | null;
  issued_by_name?: string | null;
  issued_at?: string | null;
  received_by_name?: string | null;
  received_at?: string | null;
  receipt_remarks?: string | null;
  cancel_reason?: string | null;
  created_at: string;
}

export interface IssueNote extends IssueSummary {
  items: IssueItem[];
}

export interface SiteStockLine {
  material: MaterialBrief;
  qty: number;
  avg_rate: number | null;
  value: number | null;
  consumed_7d: number;
  consumed_30d: number;
  avg_daily_7d: number;
  avg_daily_30d: number;
  days_of_cover: number | null;
  in_transit: number;
}

export interface Consumption {
  id: ID;
  site: SiteBrief;
  entry_date: string;
  material: MaterialBrief;
  qty: number;
  rate: number | null;
  amount: number | null;
  activity?: string | null;
  remarks?: string | null;
  status: string;
  created_by_name?: string | null;
  created_at: string;
}

export interface ReturnNote {
  id: ID;
  return_no: string;
  status: string;
  version: number;
  site: SiteBrief;
  return_date: string;
  reason?: string | null;
  vehicle_no?: string | null;
  remarks?: string | null;
  created_by_name?: string | null;
  dispatched_at?: string | null;
  received_at?: string | null;
  created_at: string;
  items: { id: ID; line_no: number; material: MaterialBrief; qty: number; rate: number | null; received_qty: number | null }[];
}

export interface Balance {
  store: StoreBrief;
  store_type: string;
  material: MaterialBrief;
  category: string;
  qty: number;
  avg_rate: number | null;
  value: number | null;
  reorder_level: number;
  below_reorder: boolean;
  last_movement_at?: string | null;
}

export interface LedgerRow {
  id: number;
  store_id: ID;
  material: MaterialBrief;
  txn_date: string;
  txn_type: string;
  ref_type: string;
  ref_id: ID;
  ref_no?: string | null;
  qty_in: number;
  qty_out: number;
  rate: number | null;
  value: number | null;
  balance_qty: number;
  balance_value: number | null;
  avg_rate_after: number | null;
  created_by_name?: string | null;
  created_at: string;
}

export interface Adjustment {
  id: ID;
  adj_no: string;
  status: string;
  version: number;
  store: StoreBrief;
  adj_date: string;
  reason: string;
  remarks?: string | null;
  created_by_id?: ID | null;
  created_by_name?: string | null;
  approved_by_name?: string | null;
  submitted_at?: string | null;
  approved_at?: string | null;
  created_at: string;
  items: {
    id: ID;
    line_no: number;
    material: MaterialBrief;
    physical_qty: number | null;
    system_qty: number | null;
    qty_delta: number | null;
    rate: number | null;
    remarks?: string | null;
  }[];
  approvals: Approval[];
}

export interface MarketRate {
  id: ID;
  material: MaterialBrief;
  vendor_id: ID | null;
  vendor_name: string | null;
  source: string;
  rate: number | null;
  quoted_on: string;
  valid_till?: string | null;
  location?: string | null;
  remarks?: string | null;
  version: number;
}

export interface ReportMeta {
  key: string;
  title: string;
  level: string;
  description: string;
  filters: string[];
  required_filters: string[];
  columns: { key: string; label: string; type: string }[];
}

export interface ReportData extends ReportMeta {
  params: Record<string, unknown>;
  rows: Record<string, unknown>[];
  totals: Record<string, number>;
  row_count: number;
}

export interface Dashboard {
  financial_year: string;
  boq_no: string | null;
  kpis: { key: string; label: string; value: number | null; format: string; hint?: string | null }[];
  tasks: { key: string; label: string; count: number; link: string }[];
  boq_chart: { label: string; values: Record<string, number | null> }[];
  project_chart: { label: string; values: Record<string, number | null> }[];
  low_stock: { material: string; uom: string; qty: number; reorder_level: number; store: string }[];
}

export interface Notification {
  id: ID;
  type: string;
  title: string;
  body?: string | null;
  entity_type?: string | null;
  entity_id?: ID | null;
  read_at?: string | null;
  created_at: string;
}

export interface Attachment {
  id: ID;
  filename: string;
  mime: string;
  size: number;
  uploaded_by_name?: string | null;
  created_at: string;
}
