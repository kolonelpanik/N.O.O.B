import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LocalInputStatus } from "../../shared/gateway-contract";
import { LocalInputControl } from "./LocalInputControl";

const READY: LocalInputStatus = {
  enabled: true,
  ready: true,
  armed: false,
  exclusive_grab: false,
  keyboard_ready: true,
  pointer_ready: true,
  last_event_age_ms: null,
  last_error: null,
  disarm_reason: "operator",
  dropped_events: 0,
};

afterEach(cleanup);

function renderControl(overrides: Partial<React.ComponentProps<typeof LocalInputControl>> = {}) {
  const onArm = vi.fn();
  const onDisarm = vi.fn();
  render(
    <LocalInputControl
      authenticated
      connection="live"
      status={READY}
      controlActive={false}
      electronClaimed={false}
      busy={false}
      actionError={null}
      onArm={onArm}
      onDisarm={onDisarm}
      {...overrides}
    />,
  );
  return { onArm, onDisarm };
}

describe("uConsole local input control", () => {
  it("shows ready and requires an explicit arm action", () => {
    const { onArm } = renderControl();
    expect(screen.getByText("Ready")).toBeVisible();
    const action = screen.getByRole("button", { name: "Arm keyboard + trackball" });
    expect(action).toBeEnabled();
    expect(onArm).not.toHaveBeenCalled();
    fireEvent.click(action);
    expect(onArm).toHaveBeenCalledOnce();
  });

  it("shows disabled configuration and unavailable devices distinctly", () => {
    const { rerender } = render(
      <LocalInputControl
        authenticated
        connection="live"
        status={{ ...READY, enabled: false }}
        controlActive={false}
        electronClaimed={false}
        busy={false}
        actionError={null}
        onArm={vi.fn()}
        onDisarm={vi.fn()}
      />,
    );
    expect(screen.getByText("Disabled")).toBeVisible();
    expect(screen.getByRole("button", { name: "Arm keyboard + trackball" })).toBeDisabled();

    rerender(
      <LocalInputControl
        authenticated
        connection="live"
        status={{ ...READY, ready: false, pointer_ready: false }}
        controlActive={false}
        electronClaimed={false}
        busy={false}
        actionError={null}
        onArm={vi.fn()}
        onDisarm={vi.fn()}
      />,
    );
    expect(screen.getByText("Unavailable")).toBeVisible();
    expect(screen.getByText("Trackball unavailable")).toBeVisible();
  });

  it("makes arming unavailable while this Electron session holds control", () => {
    const { onArm } = renderControl({ electronClaimed: true, controlActive: true });
    expect(screen.getByText("Unavailable")).toBeVisible();
    expect(screen.getByText("Release Electron control before arming built-in controls.")).toBeVisible();
    const action = screen.getByRole("button", { name: "Arm keyboard + trackball" });
    expect(action).toBeDisabled();
    fireEvent.click(action);
    expect(onArm).not.toHaveBeenCalled();
  });

  it("keeps disarm available for an armed or inconsistent exclusive grab", () => {
    const onDisarm = vi.fn();
    const { rerender } = render(
      <LocalInputControl
        authenticated
        connection="live"
        status={{ ...READY, armed: true, exclusive_grab: true, disarm_reason: null }}
        controlActive
        electronClaimed
        busy={false}
        actionError={null}
        onArm={vi.fn()}
        onDisarm={onDisarm}
      />,
    );
    expect(screen.getByText("Armed")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Disarm keyboard + trackball" }));
    expect(onDisarm).toHaveBeenCalledOnce();

    rerender(
      <LocalInputControl
        authenticated
        connection="live"
        status={{ ...READY, exclusive_grab: true }}
        controlActive={false}
        electronClaimed={false}
        busy={false}
        actionError={null}
        onArm={vi.fn()}
        onDisarm={onDisarm}
      />,
    );
    expect(screen.getByText("Error")).toBeVisible();
    expect(screen.getByRole("button", { name: "Disarm keyboard + trackball" })).toBeEnabled();
  });

  it("shows bounded errors and stale status without presenting cached state as live", () => {
    const { rerender } = render(
      <LocalInputControl
        authenticated
        connection="live"
        status={{ ...READY, last_error: "release_unconfirmed" }}
        controlActive={false}
        electronClaimed={false}
        busy={false}
        actionError={null}
        onArm={vi.fn()}
        onDisarm={vi.fn()}
      />,
    );
    expect(screen.getByText("Error")).toBeVisible();
    expect(screen.getByText(/target input release is unconfirmed/i)).toBeVisible();

    rerender(
      <LocalInputControl
        authenticated
        connection="degraded"
        status={{ ...READY, armed: true, exclusive_grab: true }}
        controlActive={false}
        electronClaimed={false}
        busy={false}
        actionError={null}
        onArm={vi.fn()}
        onDisarm={vi.fn()}
      />,
    );
    expect(screen.getByText("Unavailable")).toBeVisible();
    expect(screen.getByText("Gateway status is not current.")).toBeVisible();
  });

  it("renders the pending transition as busy and non-interactive", () => {
    renderControl({ busy: true });
    const panel = screen.getByRole("region", { name: "uConsole built-in controls" });
    expect(panel).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Arming…" })).toBeDisabled();
  });
});
