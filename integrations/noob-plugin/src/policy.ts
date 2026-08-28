import net from "node:net";
import path from "node:path";

const HOSTNAME = /^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/;
const DEVICE_ID = /^noob_[a-z0-9]{16,64}$/;
const CANDIDATE_ID = /^candidate_[A-Za-z0-9_-]{16,128}$/;
const CONTROL_ID = /^ctl_[A-Za-z0-9_-]{24,128}$/;
const REQUEST_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IPV6_SCOPE = /^[A-Za-z0-9_.-]{1,32}$/;

export function validAddress(value: string): boolean {
  return value.length <= 253 && (net.isIP(value) !== 0 || HOSTNAME.test(value));
}

export function isPrivateOrLocalAddress(value: string): boolean {
  const ipVersion = net.isIP(value);
  if (ipVersion === 0) {
    return value.toLowerCase().endsWith(".local");
  }
  if (ipVersion === 4) {
    const octets = value.split(".").map(Number);
    const first = octets[0] ?? -1;
    const second = octets[1] ?? -1;
    return first === 10 ||
      first === 127 ||
      (first === 169 && second === 254) ||
      (first === 172 && second >= 16 && second <= 31) ||
      (first === 192 && second === 168);
  }
  const normalized = value.toLowerCase();
  const scopeIndex = normalized.indexOf("%");
  const address = scopeIndex === -1 ? normalized : normalized.slice(0, scopeIndex);
  const scope = scopeIndex === -1 ? null : normalized.slice(scopeIndex + 1);
  const linkLocal = address.startsWith("fe8") || address.startsWith("fe9") ||
    address.startsWith("fea") || address.startsWith("feb");
  if (linkLocal) return scope !== null && IPV6_SCOPE.test(scope);
  if (scope !== null) return false;
  return address === "::1" || address.startsWith("fc") || address.startsWith("fd");
}

export function assertAddress(value: string): void {
  if (!validAddress(value)) throw new Error("invalid_device_address");
  if (process.env.NOOB_ALLOW_PUBLIC_DEVICE !== "1" && !isPrivateOrLocalAddress(value)) {
    throw new Error("public_device_address_blocked");
  }
}

export function assertAbsolutePath(value: string, label: string): void {
  if (!path.isAbsolute(value) || value.includes("\0")) throw new Error(`invalid_${label}_path`);
}

export function assertDeviceId(value: string): void {
  if (!DEVICE_ID.test(value)) throw new Error("invalid_device_id");
}

export function assertCandidateId(value: string): void {
  if (!CANDIDATE_ID.test(value)) throw new Error("invalid_candidate_id");
}

export function assertControlId(value: string): void {
  if (!CONTROL_ID.test(value)) throw new Error("invalid_control_session_id");
}

export function assertRequestId(value: string): void {
  if (!REQUEST_ID.test(value)) throw new Error("invalid_request_id");
}

export function publicError(error: unknown): string {
  const text = error instanceof Error ? error.message : "operation_failed";
  return /^[a-z0-9_]{3,80}$/.test(text) ? text : "operation_failed";
}
