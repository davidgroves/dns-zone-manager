import { API_BASE, api, formatDNSError } from '../api/client';
import type { AppState, CatalogStatus, PageSizeMode, Zone } from '../types';
import { calculateZonePageSize, getPageSizeForMode } from '../utils/pagination';
import { syncUrlFromState } from './router';

// Method context type - includes state and other methods
type ZoneMethodContext = AppState & {
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
  loadRecords: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  clearSearch: () => void;
  loadZones: (cursor?: string | null, resetHistory?: boolean) => Promise<void>;
  loadCatalogStatus: () => Promise<void>;
  selectZone: (zone: string) => Promise<void>;
  loadZonesFirstPage: () => Promise<void>;
  loadZonesWithOffset: (offset: number) => Promise<void>;
};

/**
 * Zone management module.
 * NOTE: Methods use `this` (the Alpine proxy) for state changes to trigger reactivity.
 */
export function createZoneMethods(_state: AppState) {
  return {
    /**
     * Load zones with pagination.
     */
    async loadZones(
      this: ZoneMethodContext,
      cursor: string | null = null,
      resetHistory = true,
    ) {
      this.loadingZones = true;
      this.zoneCurrentCursor = cursor;

      if (this.zonePageSizeMode === 'auto') {
        this.zonePageSize = calculateZonePageSize();
      }

      try {
        let url = `${API_BASE}/zones`;
        const params = new URLSearchParams();
        if (this.zonePageSize) {
          params.set('limit', String(this.zonePageSize));
        }
        if (cursor) {
          params.set('after', cursor);
        }
        if (params.toString()) {
          url += `?${params.toString()}`;
        }

        const response = await api(url, this.apiKey);

        if (response.ok) {
          const data = await response.json();
          this.zones = (data.zones || []) as Zone[];

          if ('total_count' in data) {
            this.zoneTotalCount = data.total_count;
            this.zoneNextCursor = data.next_cursor;
            this.zoneHasMore = data.has_more;
          } else {
            this.zoneTotalCount = this.zones.length;
            this.zoneNextCursor = null;
            this.zoneHasMore = false;
          }

          if (resetHistory) {
            this.zoneCursorHistory = [];
          }
        }

        await this.loadCatalogStatus();
      } catch (e) {
        this.toast(`Failed to load zones: ${(e as Error).message}`, 'error');
      }

      this.loadingZones = false;
    },

    /**
     * Load next page of zones.
     */
    async loadZonesNextPage(this: ZoneMethodContext) {
      if (!this.zoneHasMore || !this.zoneNextCursor) return;
      this.zoneCursorHistory.push(this.zoneCurrentCursor);
      await this.loadZones(this.zoneNextCursor, false);
      syncUrlFromState(this);
    },

    /**
     * Load previous page of zones.
     */
    async loadZonesPrevPage(this: ZoneMethodContext) {
      if (this.zoneCursorHistory.length === 0) return;
      const prevCursor = this.zoneCursorHistory.pop();
      if (prevCursor !== undefined) {
        await this.loadZones(prevCursor, false);
        syncUrlFromState(this);
      }
    },

    /**
     * Load first page of zones.
     */
    async loadZonesFirstPage(this: ZoneMethodContext) {
      this.zoneCursorHistory = [];
      await this.loadZones(null, true);
      syncUrlFromState(this);
    },

    /**
     * Load last page of zones.
     */
    async loadZonesLastPage(this: ZoneMethodContext) {
      while (this.zoneHasMore && this.zoneNextCursor) {
        this.zoneCursorHistory.push(this.zoneCurrentCursor);
        await this.loadZones(this.zoneNextCursor, false);
      }
      syncUrlFromState(this);
    },

    /**
     * Go to a specific zone page using offset-based pagination.
     */
    async goToZonePage(this: ZoneMethodContext, targetPage: number) {
      const pageSize = this.zonePageSize || 25;
      const totalPages = Math.ceil(this.zoneTotalCount / pageSize);

      // Validate target page
      let page = targetPage;
      if (Number.isNaN(page) || page < 1) page = 1;
      if (page > totalPages) page = totalPages;

      // Calculate offset and load directly
      const offset = (page - 1) * pageSize;

      // Update cursor history to reflect the page position
      // Build synthetic history so prev/next still work
      this.zoneCursorHistory = [];
      for (let i = 0; i < page - 1; i++) {
        this.zoneCursorHistory.push(`__page_${i}__`);
      }

      // Load with offset
      await this.loadZonesWithOffset(offset);

      // Update URL with current page
      syncUrlFromState(this);
    },

    /**
     * Load zones with a specific offset.
     */
    async loadZonesWithOffset(this: ZoneMethodContext, offset: number) {
      try {
        const params = new URLSearchParams();
        params.set('offset', offset.toString());
        if (this.zonePageSize) {
          params.set('limit', this.zonePageSize.toString());
        }

        const response = await api(`${API_BASE}/zones?${params}`, this.apiKey);
        if (response.ok) {
          const data = await response.json();
          if ('zones' in data && 'total_count' in data) {
            this.zones = data.zones;
            this.zoneTotalCount = data.total_count;
            this.zoneHasMore = data.has_more || false;
            this.zoneNextCursor = data.next_cursor || null;
            this.zoneCurrentCursor = `__offset_${offset}__`;
          }
        }
      } catch (e) {
        this.toast(`Failed to load zones: ${(e as Error).message}`, 'error');
      }
    },

    /**
     * Change zone page size mode.
     */
    changeZonePageSizeMode(this: ZoneMethodContext, newMode: PageSizeMode) {
      this.zonePageSizeMode = newMode;
      this.zonePageSize = getPageSizeForMode(newMode, 'zones');
      this.zoneCursorHistory = [];
      this.loadZones(null, true);
    },

    /**
     * Load catalog status.
     */
    async loadCatalogStatus(this: ZoneMethodContext) {
      try {
        const response = await api(`${API_BASE}/catalog/status`, this.apiKey);
        if (response.ok) {
          this.catalogStatus = (await response.json()) as CatalogStatus;
        }

        const zonesResponse = await api(
          `${API_BASE}/catalog/zones`,
          this.apiKey,
        );
        if (zonesResponse.ok) {
          const data = await zonesResponse.json();
          this.catalogZones = new Set(
            ((data.zones || []) as Zone[])
              .filter((z) => z.from_catalog)
              .map((z) =>
                z.zone.toLowerCase().endsWith('.')
                  ? z.zone.toLowerCase()
                  : `${z.zone.toLowerCase()}.`,
              ),
          );
        }
      } catch {
        this.catalogStatus = null;
      }
    },

    /**
     * Sync catalog zones.
     */
    async syncCatalog(this: ZoneMethodContext) {
      if (this.syncingCatalog) return;

      this.syncingCatalog = true;
      try {
        const response = await api(`${API_BASE}/catalog/sync`, this.apiKey, {
          method: 'POST',
        });
        if (response.ok) {
          const result = await response.json();
          this.toast(
            `Catalog sync: ${result.added} added, ${result.exists} existing`,
            'success',
          );
          await this.loadZones();
        } else {
          const err = await response.json();
          this.toast(`Catalog sync failed: ${formatDNSError(err)}`, 'error');
        }
      } catch (e) {
        this.toast(`Catalog sync failed: ${(e as Error).message}`, 'error');
      }
      this.syncingCatalog = false;
    },

    /**
     * Select a zone.
     */
    async selectZone(this: ZoneMethodContext, zone: string) {
      this.selectedZone = zone;
      this.showScheduledView = false;
      this.showAuditView = false;
      this.clearSearch();
      await this.loadRecords();
      // Update URL to reflect selected zone
      syncUrlFromState(this);
    },

    /**
     * Add a new zone.
     */
    async addZone(this: ZoneMethodContext) {
      if (!this.newZoneName) return;

      const zone = this.newZoneName.endsWith('.')
        ? this.newZoneName
        : `${this.newZoneName}.`;

      try {
        const response = await api(
          `${API_BASE}/zones/${encodeURIComponent(zone)}/refresh`,
          this.apiKey,
          { method: 'POST' },
        );
        if (response.ok) {
          this.toast('Zone added successfully', 'success');
          this.showAddZone = false;
          this.newZoneName = '';
          await this.loadZones();
          this.selectZone(zone);
        } else {
          const err = await response.json();
          this.toast(`Failed to add zone: ${formatDNSError(err)}`, 'error');
        }
      } catch (e) {
        this.toast(`Failed to add zone: ${(e as Error).message}`, 'error');
      }
    },

    /**
     * Refresh current zone.
     */
    async refreshCurrentZone(this: ZoneMethodContext) {
      if (!this.selectedZone) {
        await this.loadZones();
        return;
      }

      this.refreshing = true;
      try {
        await api(
          `${API_BASE}/zones/${encodeURIComponent(this.selectedZone)}/refresh`,
          this.apiKey,
          { method: 'POST' },
        );
        await this.loadRecords();
        await this.loadZones();
        this.toast('Zone refreshed', 'success');
      } catch (e) {
        this.toast(`Refresh failed: ${(e as Error).message}`, 'error');
      }
      this.refreshing = false;
    },

    /**
     * Refresh zone with dual behavior:
     * - Click: Reload records from cache (fast)
     * - Shift+Click: Force refresh from BIND via AXFR (slower)
     */
    async refreshZone(this: ZoneMethodContext, event: MouseEvent) {
      if (!this.selectedZone || this.refreshingZone) return;

      this.refreshingZone = true;
      try {
        if (event.shiftKey) {
          // Shift+click: Force refresh from BIND via AXFR
          await api(
            `${API_BASE}/zones/${encodeURIComponent(this.selectedZone)}/refresh`,
            this.apiKey,
            { method: 'POST' },
          );
          this.toast('Zone refreshed from DNS server', 'success');
        } else {
          // Normal click: Just reload from cache
          this.toast('Records reloaded', 'success');
        }
        await this.loadRecords();
        await this.loadZones(null, false);
      } catch (e) {
        this.toast(`Refresh failed: ${(e as Error).message}`, 'error');
      }
      this.refreshingZone = false;
    },

    /**
     * Export zone as BIND master format zone file.
     */
    async exportZone(this: ZoneMethodContext) {
      if (!this.selectedZone) return;

      try {
        const response = await api(
          `${API_BASE}/zones/${encodeURIComponent(this.selectedZone)}/export`,
          this.apiKey,
        );

        if (response.ok) {
          const content = await response.text();

          // Get filename from Content-Disposition header or generate from zone name
          const contentDisposition = response.headers.get(
            'Content-Disposition',
          );
          let filename = `${this.selectedZone.replace(/\.$/, '')}.zone`;
          if (contentDisposition) {
            const match = contentDisposition.match(/filename="?([^"]+)"?/);
            if (match) {
              filename = match[1];
            }
          }

          // Create blob and trigger download
          const blob = new Blob([content], { type: 'text/dns' });
          const url = URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.href = url;
          link.download = filename;
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          URL.revokeObjectURL(url);

          this.toast('Zone exported successfully', 'success');
        } else {
          const err = await response.json();
          this.toast(`Export failed: ${formatDNSError(err)}`, 'error');
        }
      } catch (e) {
        this.toast(`Export failed: ${(e as Error).message}`, 'error');
      }
    },
  };
}
