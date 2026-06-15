import type { PageSizeMode } from '../types';

/**
 * Calculate optimal page size based on available screen height for records.
 */
export function calculatePageSize(): number {
  const rowHeight = 48; // Approximate row height in pixels
  const headerHeight = 280; // Header + table header + pagination controls
  const availableHeight = window.innerHeight - headerHeight;
  return Math.max(25, Math.floor(availableHeight / rowHeight));
}

/**
 * Calculate optimal zone page size based on sidebar height.
 */
export function calculateZonePageSize(): number {
  const itemHeight = 52; // Approximate zone item height in pixels
  const headerHeight = 180; // Sidebar header + filter + pagination controls
  const sidebarHeight = window.innerHeight - headerHeight;
  return Math.max(10, Math.floor(sidebarHeight / itemHeight));
}

/**
 * Get page size for a given mode.
 */
export function getPageSizeForMode(
  mode: PageSizeMode,
  type: 'records' | 'zones' = 'records',
): number {
  switch (mode) {
    case 'auto':
      return type === 'zones' ? calculateZonePageSize() : calculatePageSize();
    case '25':
      return 25;
    case '50':
      return 50;
    case '100':
      return 100;
    case '250':
      return 250;
    default:
      return calculatePageSize(); // Fallback to auto
  }
}
