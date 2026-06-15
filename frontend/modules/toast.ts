import type { AppState, Toast } from '../types';

// Method context includes state properties
type ToastMethodContext = AppState & {
  removeToast: (id: number) => void;
};

/**
 * Toast notification module.
 * NOTE: Methods use `this` (the Alpine proxy) for state changes to trigger reactivity.
 */
export function createToastMethods(_state: AppState) {
  return {
    /**
     * Show a toast notification.
     */
    toast(
      this: ToastMethodContext,
      message: string,
      type: Toast['type'] = 'success',
    ) {
      const id = ++this.toastId;
      this.toasts.push({ id, message, type, visible: true });
      setTimeout(() => this.removeToast(id), 5000);
    },

    /**
     * Remove a toast by ID.
     */
    removeToast(this: ToastMethodContext, id: number) {
      const toast = this.toasts.find((t) => t.id === id);
      if (toast) {
        toast.visible = false;
        setTimeout(() => {
          this.toasts = this.toasts.filter((t) => t.id !== id);
        }, 300);
      }
    },
  };
}
