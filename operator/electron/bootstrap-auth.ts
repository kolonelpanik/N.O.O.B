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
