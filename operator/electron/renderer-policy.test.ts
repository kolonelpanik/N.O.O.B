import { describe, expect, it } from "vitest";
import { developmentRendererUrl, isTrustedIpcSource } from "./renderer-policy.js";

describe("development renderer policy", () => {
  it("allows explicit-port loopback HTTP only in an unpackaged build", () => {
    expect(developmentRendererUrl("http://127.0.0.1:5173/", false)).toBe(
      "http://127.0.0.1:5173/",
    );
    expect(developmentRendererUrl("http://localhost:4173/dev", false)).toBe(
      "http://localhost:4173/dev",
    );
    expect(developmentRendererUrl("http://[::1]:5173/", false)).toBe(
      "http://[::1]:5173/",
    );
    expect(developmentRendererUrl("http://127.0.0.1:80/", false)).toBe(
      "http://127.0.0.1/",
    );
  });

  it("rejects packaged, remote, implicit-port, credentialed, and decorated URLs", () => {
    expect(developmentRendererUrl("http://127.0.0.1:5173/", true)).toBeNull();
    expect(developmentRendererUrl("http://example.test:5173/", false)).toBeNull();
    expect(developmentRendererUrl("http://127.0.0.1/", false)).toBeNull();
    expect(developmentRendererUrl("http://127.0.0.1:0/", false)).toBeNull();
    expect(developmentRendererUrl("https://127.0.0.1:5173/", false)).toBeNull();
    expect(developmentRendererUrl("http://user:pass@127.0.0.1:5173/", false)).toBeNull();
    expect(developmentRendererUrl("http://127.0.0.1:5173/?token=no", false)).toBeNull();
    expect(developmentRendererUrl("http://127.0.0.1:5173/#other", false)).toBeNull();
    expect(developmentRendererUrl("not a URL", false)).toBeNull();
  });
});

describe("privileged IPC source policy", () => {
  it("accepts only the trusted webContents and its main frame", () => {
    const trustedSender = {};
    const otherSender = {};
    const mainFrame = {};
    const subframe = {};

    expect(isTrustedIpcSource(trustedSender, trustedSender, mainFrame, mainFrame)).toBe(true);
    expect(isTrustedIpcSource(trustedSender, otherSender, mainFrame, mainFrame)).toBe(false);
    expect(isTrustedIpcSource(trustedSender, trustedSender, subframe, mainFrame)).toBe(false);
    expect(isTrustedIpcSource(trustedSender, trustedSender, null, mainFrame)).toBe(false);
    expect(isTrustedIpcSource(null, trustedSender, mainFrame, mainFrame)).toBe(false);
  });
});
