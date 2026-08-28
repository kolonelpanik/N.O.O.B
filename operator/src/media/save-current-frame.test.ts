import { afterEach, describe, expect, it, vi } from "vitest";
import { saveCurrentGatewayFrame } from "./save-current-frame";

const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xd9]);

afterEach(() => vi.restoreAllMocks());

describe("saveCurrentGatewayFrame", () => {
  it("downloads the current environment gateway frame without requiring camera storage", async () => {
    const readFrame = vi.fn(async () => ({
      bytes: JPEG,
      contentType: "image/jpeg" as const,
      sequence: null,
    }));
    const link = { href: "", download: "", click: vi.fn() };
    const revokeObjectURL = vi.fn();
    const defer = vi.fn((callback: () => void) => callback());
    const filename = await saveCurrentGatewayFrame(
      "environment",
      readFrame,
      {
        createObjectURL: vi.fn(() => "blob:noob-environment"),
        revokeObjectURL,
        createLink: () => link,
        defer,
      },
      new Date("2026-08-27T21:22:23.000Z"),
    );

    expect(readFrame).toHaveBeenCalledWith("environment");
    expect(filename).toBe("noob-environment-2026-08-27T21-22-23.000Z.jpg");
    expect(link).toMatchObject({
      href: "blob:noob-environment",
      download: filename,
    });
    expect(link.click).toHaveBeenCalledOnce();
    expect(defer).toHaveBeenCalledWith(expect.any(Function), 1_000);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:noob-environment");
  });

  it("keeps target and environment filenames source-specific", async () => {
    const link = { href: "", download: "", click: vi.fn() };
    const filename = await saveCurrentGatewayFrame(
      "target",
      async () => ({ bytes: JPEG, contentType: "image/jpeg", sequence: null }),
      {
        createObjectURL: () => "blob:noob-target",
        revokeObjectURL: vi.fn(),
        createLink: () => link,
        defer: vi.fn(),
      },
      new Date("2026-08-27T21:22:23.000Z"),
    );

    expect(filename).toMatch(/^noob-target-/);
    expect(link.download).toBe(filename);
  });
});
