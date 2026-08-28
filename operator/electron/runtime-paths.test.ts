import path from "node:path";
import { describe, expect, it } from "vitest";
import { operatorSupportDirectory } from "./runtime-paths.js";

describe("canonical operator support directory", () => {
  it("matches the MCP plugin namespace in a packaged macOS build", () => {
    expect(operatorSupportDirectory({
      platform: "darwin",
      homeDirectory: "/Users/operator",
    })).toBe("/Users/operator/Library/Application Support/N.O.O.B");
  });

  it("uses the shared XDG namespace outside macOS", () => {
    expect(operatorSupportDirectory({
      platform: "linux",
      homeDirectory: "/home/operator",
      xdgConfigHome: "/run/operator-config",
    })).toBe("/run/operator-config/noob");
  });

  it("retains an explicit absolute test or managed-deployment override", () => {
    expect(operatorSupportDirectory({
      configured: "/private/tmp/noob-support/../noob-support",
    })).toBe(path.normalize("/private/tmp/noob-support/../noob-support"));
    expect(() => operatorSupportDirectory({ configured: "relative/support" }))
      .toThrow("invalid_operator_support_directory");
  });
});
