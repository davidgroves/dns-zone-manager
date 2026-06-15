import Alpine from 'alpinejs';
import { createApp } from './app';
import type { AppConfig } from './types';
import './styles/app.css';

// Declare Alpine on window
declare global {
  interface Window {
    Alpine: typeof Alpine;
    createDnsApp: (config: AppConfig) => ReturnType<typeof createApp>;
  }
}

/**
 * Create the DNS app with the given configuration.
 * This is the main entry point for both production (IIFE) and dev (ES module) modes.
 */
export function createDnsApp(config: AppConfig) {
  return createApp(config);
}

// Expose to window for use by HTML
window.Alpine = Alpine;
window.createDnsApp = createDnsApp;

// Start Alpine.js
Alpine.start();

// Export for direct module usage
export { createApp };
export type { AppConfig } from './types';
