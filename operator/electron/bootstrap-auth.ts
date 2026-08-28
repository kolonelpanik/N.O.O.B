import { constants } from "node:fs";
import { open } from "node:fs/promises";

const TOKEN_PATTERN = /^[\x21-\x7e]{32,256}$/;
const MAX_STDIN_BYTES = 258;

/**
 * Read one bearer value from an inherited pipe without placing it in argv,
 * the environment, renderer state, or a persistent store.
 */
export async function readBearerFromStdin(
  source: AsyncIterable<Uint8Array | string>,
): Promise<string> {
  const chunks: Buffer[] = [];
  let total = 0;

  try {
    for await (const chunk of source) {
      const bytes = typeof chunk === "string" ? Buffer.from(chunk, "utf8") : Buffer.from(chunk);
      total += bytes.length;
      if (total > MAX_STDIN_BYTES) {
        bytes.fill(0);
        throw new Error("auth_stdin_too_large");
      }
      chunks.push(bytes);
    }

    const raw = Buffer.concat(chunks, total);
    try {
      if (raw.some((byte) => byte > 0x7f)) throw new Error("auth_stdin_non_ascii");
      let end = raw.length;
      if (end > 0 && raw[end - 1] === 0x0a) end -= 1;
      if (end > 0 && raw[end - 1] === 0x0d) end -= 1;
      const value = raw.subarray(0, end).toString("ascii");
      if (!TOKEN_PATTERN.test(value)) throw new Error("auth_stdin_invalid");
      return value;
    } finally {
      raw.fill(0);
    }
  } finally {
    for (const chunk of chunks) chunk.fill(0);
  }
}

/**
 * Read an explicitly provisioned owner-only token file into main-process
 * memory. The operator never creates or rewrites this file, and the renderer
 * never receives its contents.
 */
export async function readBearerFromOwnerFile(file: string): Promise<string> {
  let handle;
  try {
    try {
      handle = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW);
    } catch {
      throw new Error("auth_file_not_regular");
    }
    const info = await handle.stat();
    if (!info.isFile()) throw new Error("auth_file_not_regular");
    const currentUid = process.getuid?.();
    if (currentUid !== undefined && info.uid !== currentUid) {
      throw new Error("auth_file_owner_mismatch");
    }
    if ((info.mode & 0o077) !== 0) throw new Error("auth_file_permissions_too_open");
    if (info.size > MAX_STDIN_BYTES) throw new Error("auth_file_invalid");

    // Read at most one byte beyond the accepted ceiling from the already
    // verified descriptor. A concurrent rename cannot redirect this handle.
    const raw = Buffer.alloc(MAX_STDIN_BYTES + 1);
    const { bytesRead } = await handle.read(raw, 0, raw.length, 0);
    try {
      if (bytesRead > MAX_STDIN_BYTES || raw.subarray(0, bytesRead).some((byte) => byte > 0x7f)) {
        throw new Error("auth_file_invalid");
      }
      let end = bytesRead;
      if (end > 0 && raw[end - 1] === 0x0a) end -= 1;
      if (end > 0 && raw[end - 1] === 0x0d) end -= 1;
      const value = raw.subarray(0, end).toString("ascii");
      if (!TOKEN_PATTERN.test(value)) throw new Error("auth_file_invalid");
      return value;
    } finally {
      raw.fill(0);
    }
  } finally {
    if (handle !== undefined) await handle.close();
  }
}
