import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourceTabs } from "./SourceTabs";

afterEach(cleanup);

describe("SourceTabs optional environment camera", () => {
  it("hides the optional camera surface when the gateway reports it unconfigured", () => {
    render(
      <SourceTabs
        source="target"
        busy={false}
        environmentConfigured={false}
        error={null}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("tab", { name: "Target" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Environment" })).not.toBeInTheDocument();
    expect(screen.queryByText(/camera not configured/i)).not.toBeInTheDocument();
  });

  it("retains the environment selector when the gateway reports it configured", () => {
    const onChange = vi.fn();
    render(
      <SourceTabs
        source="target"
        busy={false}
        environmentConfigured
        error={null}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Environment" }));
    expect(onChange).toHaveBeenCalledWith("environment");
  });

  it("presents Target as the selected tab after the optional camera is disabled", () => {
    const { rerender } = render(
      <SourceTabs
        source="environment"
        busy={false}
        environmentConfigured
        error={null}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("tab", { name: "Environment" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    rerender(
      <SourceTabs
        source="target"
        busy={false}
        environmentConfigured={false}
        error={null}
        onChange={vi.fn()}
      />,
    );

    expect(screen.queryByRole("tab", { name: "Environment" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Target" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
