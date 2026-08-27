export function developmentRendererUrl(raw: string | undefined, isPackaged: boolean): string | null {
  if (isPackaged || raw === undefined || raw.trim().length === 0) return null;

  const candidate = raw.trim();
  const authority = candidate.match(
    /^http:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):([0-9]{1,5})(?:\/|$)/i,
  );
  if (authority === null) return null;

  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    return null;
  }

  const loopback = url.hostname === "127.0.0.1" ||
    url.hostname === "localhost" ||
    url.hostname === "[::1]";
  const port = Number.parseInt(authority[1], 10);
  if (
    url.protocol !== "http:" ||
    !loopback ||
    !Number.isSafeInteger(port) ||
    port < 1 ||
    port > 65_535 ||
    url.username.length > 0 ||
    url.password.length > 0 ||
    url.search.length > 0 ||
    url.hash.length > 0
  ) return null;

  return url.toString();
}

export function isTrustedIpcSource<TSender, TFrame>(
  trustedSender: TSender | null,
  sender: TSender,
  senderFrame: TFrame | null,
  senderMainFrame: TFrame,
): boolean {
  return trustedSender !== null && sender === trustedSender && senderFrame === senderMainFrame;
}
