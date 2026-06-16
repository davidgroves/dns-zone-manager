// DNS Record Types

export interface Zone {
  zone: string;
  from_catalog?: boolean;
  record_count?: number;
  zone_is_idn?: boolean;
  zone_utf8?: string | null;
}

export interface RRset {
  name: string;
  type: string;
  rdclass: string;
  ttl: number;
  records: string[];
  name_is_idn?: boolean;
  name_utf8?: string | null;
  records_is_utf8?: boolean;
  records_utf8?: string[] | null;
}

export interface SearchResultZone {
  zone: string;
  serial?: number;
  rrsets: RRset[];
  zone_is_idn?: boolean;
  zone_utf8?: string | null;
}

export interface FlattenedSearchResult {
  zone: string;
  name: string;
  type: string;
  rdclass: string;
  ttl: number;
  records: string[];
  zone_is_idn?: boolean;
  zone_utf8?: string | null;
  name_is_idn?: boolean;
  name_utf8?: string | null;
  records_is_utf8?: boolean;
  records_utf8?: string[] | null;
}

export interface AtomicOperation {
  action: 'add' | 'delete' | 'replace';
  zone: string;
  name: string;
  type: string;
  rdclass: string;
  ttl: number;
  records: string[] | null;
}

export interface AtomicResult {
  success: boolean;
  message: string;
  operations_count?: number;
}

export interface NsupdateResult {
  total_success: number;
  total_failed: number;
  transactions: NsupdateTransaction[];
}

export interface NsupdateTransaction {
  zone: string;
  success: boolean;
  message: string;
}

export interface ReversePtrCheckResult {
  ip: string;
  ptr_fqdn: string;
  reverse_zone: string | null;
  record_name: string | null;
  zone_managed: boolean;
  existing_ptrs: string[];
  can_create: boolean;
  error: string | null;
}

export interface ReversePtrCreateResult {
  created_count: number;
  skipped_count: number;
  error_count: number;
  results: Array<{
    ip: string;
    ptr_fqdn: string;
    reverse_zone: string | null;
    status: string;
    message: string;
  }>;
}

export interface Toast {
  id: number;
  message: string;
  type: 'success' | 'error' | 'warning';
  visible: boolean;
}

export interface CatalogStatus {
  enabled: boolean;
  zone?: string;
  last_sync?: string;
}

export interface RecordForm {
  name: string;
  type: string;
  rdclass: string;
  ttl: number;
  records: string;
  zone?: string;
  original?: RRset;
}

export interface DeleteTarget extends RRset {
  zone: string;
}

// Pagination state types
export type PageSizeMode = 'auto' | '25' | '50' | '100' | '250';

export interface PaginationState {
  mode: PageSizeMode;
  size: number | null;
  currentCursor: string | null;
  nextCursor: string | null;
  totalCount: number;
  hasMore: boolean;
  cursorHistory: (string | null)[];
}

// Sort types
export type SortField = 'name' | 'type' | 'rdclass' | 'ttl' | 'data' | 'zone';
export type SortDirection = 'asc' | 'desc';
export type SearchField = 'name' | 'data' | 'either';

// API Response types
export interface PaginatedResponse<T> {
  total_count?: number;
  next_cursor?: string | null;
  has_more?: boolean;
  page_size?: number;
  zones?: T[];
  rrsets?: T[];
  results?: T[];
}

export interface ZoneSearchResponse {
  zone: string;
  serial?: number;
  results: RRset[];
  total_count?: number;
  next_cursor?: string | null;
  has_more?: boolean;
}

// Zone History types
export interface HistoryChange {
  action: 'add' | 'delete';
  name: string;
  ttl: number;
  type: string;
  rdclass: string;
  records: string[];
}

export interface HistoryBatch {
  from_serial: number;
  to_serial: number;
  changes: HistoryChange[];
}

export interface ZoneHistory {
  zone: string;
  current_serial: number;
  history: HistoryBatch[];
  available_from_serial: number;
  is_full_axfr: boolean;
}

export interface RollbackPreview {
  zone: string;
  current_serial: number;
  target_serial: number;
  changes: HistoryChange[];
  change_count: number;
  can_rollback: boolean;
  warning: string | null;
}

export interface RollbackResult {
  success: boolean;
  zone: string;
  from_serial: number;
  to_serial: number;
  new_serial: number;
  changes_applied: number;
  message: string;
}

// Main application state interface
export interface AppState {
  // Auth state
  authenticated: boolean;
  apiKey: string | null;
  apiKeyInput: string;
  loginError: string;
  azureEnabled: boolean;
  proxyAuthEnabled: boolean;
  currentUser: string | null;
  appVersion: string;

  // UI state
  loadingZones: boolean;
  loadingRecords: boolean;
  refreshing: boolean;
  refreshingZone: boolean;
  saving: boolean;

  // Data
  zones: Zone[];
  zoneFilter: string;
  selectedZone: string | null;
  records: RRset[];
  catalogStatus: CatalogStatus | null;
  catalogZones: Set<string>;
  syncingCatalog: boolean;

  // Record Pagination
  pageSizeMode: PageSizeMode;
  pageSize: number | null;
  currentCursor: string | null;
  nextCursor: string | null;
  totalRecords: number;
  hasMoreRecords: boolean;
  recordCursorHistory: (string | null)[];

  // Zone Pagination
  zonePageSizeMode: PageSizeMode;
  zonePageSize: number | null;
  zoneCurrentCursor: string | null;
  zoneNextCursor: string | null;
  zoneTotalCount: number;
  zoneHasMore: boolean;
  zoneCursorHistory: (string | null)[];

  // Search
  searchQuery: string;
  searchType: string;
  searchField: SearchField;
  searchResults: SearchResultZone[];
  isSearching: boolean;
  searchAllZones: boolean;

  // Search Pagination
  searchPageSizeMode: PageSizeMode;
  searchPageSize: number | null;
  searchCurrentCursor: string | null;
  searchNextCursor: string | null;
  searchTotalCount: number;
  searchHasMore: boolean;
  searchCursorHistory: (string | null)[];

  // Sorting (zone records)
  sortField: SortField;
  sortDirection: SortDirection;

  // Sorting (search results)
  searchSortField: SortField;
  searchSortDirection: SortDirection;

  // Modals
  showAddZone: boolean;
  showAddRecord: boolean;
  showEditRecord: boolean;
  showDeleteConfirm: boolean;
  showNsupdate: boolean;
  showAtomicModal: boolean;
  showReversePtrModal: boolean;

  // Atomic mode
  atomicMode: boolean;
  atomicQueue: AtomicOperation[];
  atomicResult: AtomicResult | null;

  // Reverse PTR
  reversePtrTarget: string;
  reversePtrTtl: number;
  reversePtrResults: ReversePtrCheckResult[];
  reversePtrSelected: string[];
  reversePtrMode: 'replace' | 'add_roundrobin';
  reversePtrLoading: boolean;
  reversePtrCreateResult: ReversePtrCreateResult | null;

  // Zone History
  showHistoryModal: boolean;
  historyLoading: boolean;
  historyFromSerial: number;
  zoneHistory: ZoneHistory | null;
  rollbackPreview: RollbackPreview | null;
  rollbackTargetSerial: number | null;
  rollbackLoading: boolean;
  expandedBatches: Set<number>;

  // Forms
  newZoneName: string;
  recordForm: RecordForm;
  customTypeMode: boolean;
  customClassMode: boolean;
  deleteTarget: DeleteTarget | null;
  nsupdateText: string;
  nsupdateResult: NsupdateResult | null;

  // Toasts
  toasts: Toast[];
  toastId: number;

  // Router
  intendedRoute: RouteParams | null;
}

// Config passed at init or fetched from /ui/config API
export interface AppConfig {
  azureEnabled?: boolean;
  proxyAuthEnabled?: boolean;
  version?: string;
}

// Route parameters for URL-based navigation
export interface RouteParams {
  zone: string | null;
  page: number;
  searchQuery: string | null;
  searchType: string | null;
  searchField: SearchField;
  searchAllZones: boolean;
}
