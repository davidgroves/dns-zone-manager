import type { AppConfig, AppState } from '../types';

/**
 * Creates the initial state for the DNS app.
 */
export function createInitialState(config: AppConfig): AppState {
  return {
    // Auth state
    authenticated: false,
    apiKey: null,
    apiKeyInput: '',
    loginError: '',
    azureEnabled: config.azureEnabled ?? false,
    proxyAuthEnabled: config.proxyAuthEnabled ?? false,
    currentUser: null,
    appVersion: '',

    // UI state
    loadingZones: false,
    loadingRecords: false,
    refreshing: false,
    refreshingZone: false,
    saving: false,

    // Data
    zones: [],
    zoneFilter: '',
    selectedZone: null,
    records: [],
    catalogStatus: null,
    catalogZones: new Set(),
    syncingCatalog: false,

    // Record Pagination
    pageSizeMode: 'auto',
    pageSize: null,
    currentCursor: null,
    nextCursor: null,
    totalRecords: 0,
    hasMoreRecords: false,
    recordCursorHistory: [],

    // Zone Pagination
    zonePageSizeMode: 'auto',
    zonePageSize: null,
    zoneCurrentCursor: null,
    zoneNextCursor: null,
    zoneTotalCount: 0,
    zoneHasMore: false,
    zoneCursorHistory: [],

    // Search
    searchQuery: '',
    searchType: '',
    searchField: 'either',
    searchResults: [],
    isSearching: false,
    searchAllZones: false,

    // Search Pagination
    searchPageSizeMode: 'auto',
    searchPageSize: null,
    searchCurrentCursor: null,
    searchNextCursor: null,
    searchTotalCount: 0,
    searchHasMore: false,
    searchCursorHistory: [],

    // Sorting (zone records)
    sortField: 'name',
    sortDirection: 'asc',

    // Sorting (search results)
    searchSortField: 'name',
    searchSortDirection: 'asc',

    // Modals
    showAddZone: false,
    showAddRecord: false,
    showEditRecord: false,
    showDeleteConfirm: false,
    showNsupdate: false,
    showAtomicModal: false,
    showReversePtrModal: false,
    showScheduleModal: false,
    showScheduledView: false,
    showAuditView: false,

    // Atomic mode
    atomicMode: false,
    atomicQueue: [],
    atomicResult: null,

    // Scheduled changes
    scheduledChanges: [],
    scheduledLoading: false,
    scheduleForm: {
      name: '',
      description: '',
      applyNow: true,
      scheduledLocal: '',
      expiryHours: 1,
      autoPrerequisites: true,
    },
    scheduleMode: 'create',
    scheduleSource: 'new',
    editingChangeId: null,
    scheduleOps: [],
    scheduleZone: '',
    prereqRows: [],
    previewResult: null,
    previewLoading: false,
    selectedScheduledChange: null,
    scheduledStatusFilters: ['draft', 'scheduled', 'failed'],
    showScheduledStatusMenu: false,
    showRevertModal: false,
    revertPreview: null,

    // Audit log
    auditEvents: [],
    auditLoading: false,
    auditTotal: 0,
    auditLimit: 50,
    auditOffset: 0,
    auditEventFilters: [],
    showAuditEventMenu: false,
    auditActorFilter: '',
    auditZoneFilter: '',
    auditQuery: '',
    auditSince: '',
    auditUntil: '',

    // Reverse PTR
    reversePtrTarget: '',
    reversePtrTtl: 3600,
    reversePtrResults: [],
    reversePtrSelected: [],
    reversePtrMode: 'replace',
    reversePtrLoading: false,
    reversePtrCreateResult: null,

    // Zone History
    showHistoryModal: false,
    historyLoading: false,
    historyFromSerial: 1,
    zoneHistory: null,
    rollbackPreview: null,
    rollbackTargetSerial: null,
    rollbackLoading: false,
    expandedBatches: new Set(),

    // Forms
    newZoneName: '',
    recordForm: { name: '', type: 'A', rdclass: 'IN', ttl: 3600, records: '' },
    customTypeMode: false,
    customClassMode: false,
    deleteTarget: null,
    nsupdateText: '',
    nsupdateResult: null,

    // Toasts
    toasts: [],
    toastId: 0,

    // Router
    intendedRoute: null,
  };
}
