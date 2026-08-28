import { describe, expect, it, vi } from "vitest";
import {
  installSingleInstanceGuard,
  restoreOperatorWindow,
  type FocusableOperatorWindow,
} from "./single-instance.js";

function operatorWindow(overrides: Partial<FocusableOperatorWindow> = {}): FocusableOperatorWindow {
  return {
    isDestroyed: () => false,
    isMinimized: () => false,
    isVisible: () => true,
    restore: vi.fn(),
    show: vi.fn(),
    focus: vi.fn(),
    ...overrides,
  };
}

describe("single-instance operator guard", () => {
  it("quits a duplicate before registering another operator runtime", () => {
    const quit = vi.fn();
    const on = vi.fn();
    const acquired = installSingleInstanceGuard(
      { requestSingleInstanceLock: () => false, quit, on },
      () => null,
    );

    expect(acquired).toBe(false);
    expect(quit).toHaveBeenCalledOnce();
    expect(on).not.toHaveBeenCalled();
  });

  it("restores, shows, and focuses the existing window on a second launch", () => {
    let secondInstance: (() => void) | null = null;
    const window = operatorWindow({
      isMinimized: () => true,
      isVisible: () => false,
    });
    const acquired = installSingleInstanceGuard(
      {
        requestSingleInstanceLock: () => true,
        quit: vi.fn(),
        on: (_event, listener) => { secondInstance = listener; },
      },
      () => window,
    );

    expect(acquired).toBe(true);
    expect(secondInstance).not.toBeNull();
    (secondInstance as () => void)();
    expect(window.restore).toHaveBeenCalledOnce();
    expect(window.show).toHaveBeenCalledOnce();
    expect(window.focus).toHaveBeenCalledOnce();
  });

  it("ignores a destroyed prior window", () => {
    const window = operatorWindow({ isDestroyed: () => true });
    restoreOperatorWindow(window);
    expect(window.restore).not.toHaveBeenCalled();
    expect(window.show).not.toHaveBeenCalled();
    expect(window.focus).not.toHaveBeenCalled();
  });
});
