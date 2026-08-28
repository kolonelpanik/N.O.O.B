export interface SingleInstanceApp {
  requestSingleInstanceLock(): boolean;
  quit(): void;
  on(event: "second-instance", listener: () => void): void;
}

export interface FocusableOperatorWindow {
  isDestroyed(): boolean;
  isMinimized(): boolean;
  isVisible(): boolean;
  restore(): void;
  show(): void;
  focus(): void;
}

export function restoreOperatorWindow(window: FocusableOperatorWindow | null): void {
  if (window === null || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  if (!window.isVisible()) window.show();
  window.focus();
}

export function installSingleInstanceGuard(
  application: SingleInstanceApp,
  currentWindow: () => FocusableOperatorWindow | null,
): boolean {
  if (!application.requestSingleInstanceLock()) {
    application.quit();
    return false;
  }
  application.on("second-instance", () => restoreOperatorWindow(currentWindow()));
  return true;
}
