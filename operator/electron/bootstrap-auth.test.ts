import { describe, expect, it } from "vitest";
import { readBearerFromStdin } from "./bootstrap-auth.js";

async function* pieces(...values: Array<string | Uint8Array>) {
  for (const value of values) yield value;
}

describe("stdin authentication bootstrap", () => {
  it("accepts one printable bearer value split across chunks", async () => {
    const value = "a".repeat(32) + "B".repeat(32);
    await expect(readBearerFromStdin(pieces(value.slice(0, 17), value.slice(17), "\n")))
      .resolves.toBe(value);
  });

  it("accepts a CRLF-terminated value", async () => {
    const value = "c".repeat(64);
    await expect(readBearerFromStdin(pieces(Buffer.from(`${value}\r\n`))))
      .resolves.toBe(value);
  });

  it.each([
    ["too short", "x".repeat(31)],
    ["embedded newline", `${"x".repeat(32)}\n${"y".repeat(32)}`],
    ["non ASCII", `${"x".repeat(32)}é`],
    ["oversized pipe", "x".repeat(259)],
  ])("rejects %s input", async (_label, value) => {
    await expect(readBearerFromStdin(pieces(value))).rejects.toThrow();
  });
});
