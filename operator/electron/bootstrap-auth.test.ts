import { chmod, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { readBearerFromOwnerFile, readBearerFromStdin } from "./bootstrap-auth.js";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

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

describe("owner-file authentication bootstrap", () => {
  it("reads a provisioned owner-only token without exposing it outside main-process memory", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "noob-token-test-"));
    temporaryDirectories.push(directory);
    const file = path.join(directory, "gateway.token");
    const value = "T".repeat(64);
    await writeFile(file, `${value}\n`, { mode: 0o600 });
    await expect(readBearerFromOwnerFile(file)).resolves.toBe(value);
  });

  it("rejects permissive files and symbolic links", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "noob-token-test-"));
    temporaryDirectories.push(directory);
    const file = path.join(directory, "gateway.token");
    const link = path.join(directory, "gateway-link.token");
    await writeFile(file, `${"T".repeat(64)}\n`, { mode: 0o600 });
    await chmod(file, 0o644);
    await expect(readBearerFromOwnerFile(file)).rejects.toThrow("auth_file_permissions_too_open");
    await chmod(file, 0o600);
    await symlink(file, link);
    await expect(readBearerFromOwnerFile(link)).rejects.toThrow("auth_file_not_regular");
  });
});
