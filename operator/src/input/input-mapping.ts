import type { GatewayInputCommand, MouseButton } from "../../shared/gateway-contract";

const DIGIT_KEYS = ["ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"];

const STATIC_CODE_MAP: Readonly<Record<string, string>> = {
  Enter: "ENTER",
  Escape: "ESCAPE",
  Backspace: "BACKSPACE",
  Tab: "TAB",
  Space: "SPACE",
  Minus: "MINUS",
  Equal: "EQUALS",
  BracketLeft: "LEFT_BRACKET",
  BracketRight: "RIGHT_BRACKET",
  Backslash: "BACKSLASH",
  Semicolon: "SEMICOLON",
  Quote: "QUOTE",
  Backquote: "GRAVE_ACCENT",
  Comma: "COMMA",
  Period: "PERIOD",
  Slash: "FORWARD_SLASH",
  CapsLock: "CAPS_LOCK",
  PrintScreen: "PRINT_SCREEN",
  ScrollLock: "SCROLL_LOCK",
  Pause: "PAUSE",
  Insert: "INSERT",
  Home: "HOME",
  PageUp: "PAGE_UP",
  Delete: "DELETE",
  End: "END",
  PageDown: "PAGE_DOWN",
  ArrowRight: "RIGHT_ARROW",
  ArrowLeft: "LEFT_ARROW",
  ArrowDown: "DOWN_ARROW",
  ArrowUp: "UP_ARROW",
  ContextMenu: "APPLICATION",
  ControlLeft: "LEFT_CONTROL",
  ShiftLeft: "LEFT_SHIFT",
  AltLeft: "LEFT_ALT",
  MetaLeft: "LEFT_GUI",
  ControlRight: "RIGHT_CONTROL",
  ShiftRight: "RIGHT_SHIFT",
  AltRight: "RIGHT_ALT",
  MetaRight: "RIGHT_GUI",
};

const ALLOWED_KEYS = new Set<string>([
  ...Array.from({ length: 26 }, (_, index) => String.fromCharCode(65 + index)),
  ...DIGIT_KEYS,
  ...Array.from({ length: 12 }, (_, index) => `F${index + 1}`),
  ...Object.values(STATIC_CODE_MAP),
]);

const MOUSE_BUTTONS = new Set<MouseButton>(["left", "middle", "right"]);

const ACTION_KEY_ALIASES: Readonly<Record<string, string>> = {
  GUI: "LEFT_GUI",
  CMD: "LEFT_GUI",
  COMMAND: "LEFT_GUI",
  CTRL: "LEFT_CONTROL",
  CONTROL: "LEFT_CONTROL",
  SHIFT: "LEFT_SHIFT",
  ALT: "LEFT_ALT",
  OPTION: "LEFT_ALT",
};

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
}

function boundedInteger(value: unknown, minimum: number, maximum: number): value is number {
  return Number.isInteger(value) && typeof value === "number" && value >= minimum && value <= maximum;
}

export function domCodeToGatewayKey(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) {
    return code.slice(3);
  }
  if (/^Digit[0-9]$/.test(code)) {
    return DIGIT_KEYS[Number.parseInt(code.slice(5), 10)] ?? null;
  }
  if (/^F(?:[1-9]|1[0-2])$/.test(code)) {
    return code;
  }
  return STATIC_CODE_MAP[code] ?? null;
}

export function mapKeyboardEvent(
  event: Pick<KeyboardEvent, "code" | "repeat">,
  kind: "down" | "up",
): GatewayInputCommand | null {
  if (kind === "down" && event.repeat) {
    return null;
  }
  const key = domCodeToGatewayKey(event.code);
  return key === null ? null : { op: "key", event: kind, key };
}

export function mouseButtonFromDom(button: number): MouseButton | null {
  if (button === 0) return "left";
  if (button === 1) return "middle";
  if (button === 2) return "right";
  return null;
}

function isAsciiText(value: unknown): value is string {
  if (typeof value !== "string" || value.length < 1 || value.length > 512) {
    return false;
  }
  return [...value].every((character) => {
    const code = character.charCodeAt(0);
    return code === 9 || code === 10 || code === 13 || (code >= 32 && code <= 126);
  });
}

export function validateGatewayInput(value: unknown): GatewayInputCommand | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.op !== "string") {
    return null;
  }
  switch (candidate.op) {
    case "key":
      return exactKeys(candidate, ["op", "event", "key"]) &&
        (candidate.event === "down" || candidate.event === "up") &&
        typeof candidate.key === "string" &&
        ALLOWED_KEYS.has(candidate.key)
        ? (candidate as GatewayInputCommand)
        : null;
    case "type":
      return exactKeys(candidate, ["op", "text", "interval_ms"]) &&
        isAsciiText(candidate.text) &&
        boundedInteger(candidate.interval_ms, 0, 25)
        ? (candidate as GatewayInputCommand)
        : null;
    case "combo": {
      const keys = candidate.keys;
      return exactKeys(candidate, ["op", "keys", "hold_ms"]) &&
        Array.isArray(keys) &&
        keys.length >= 1 &&
        keys.length <= 6 &&
        keys.every((key) => typeof key === "string" && ALLOWED_KEYS.has(key)) &&
        new Set(keys).size === keys.length &&
        boundedInteger(candidate.hold_ms, 20, 500)
        ? (candidate as GatewayInputCommand)
        : null;
    }
    case "mouse_move":
      return exactKeys(candidate, ["op", "dx", "dy", "wheel"]) &&
        boundedInteger(candidate.dx, -127, 127) &&
        boundedInteger(candidate.dy, -127, 127) &&
        boundedInteger(candidate.wheel, -127, 127)
        ? (candidate as GatewayInputCommand)
        : null;
    case "mouse_button":
      return exactKeys(candidate, ["op", "button", "event"]) &&
        typeof candidate.button === "string" &&
        MOUSE_BUTTONS.has(candidate.button as MouseButton) &&
        (candidate.event === "down" || candidate.event === "up" || candidate.event === "click")
        ? (candidate as GatewayInputCommand)
        : null;
    case "release_all":
    case "ping":
      return exactKeys(candidate, ["op"]) ? (candidate as GatewayInputCommand) : null;
    default:
      return null;
  }
}

export function parseAgentPayload(source: string): GatewayInputCommand | null {
  try {
    const parsed: unknown = JSON.parse(source);
    const canonical = validateGatewayInput(parsed);
    if (canonical !== null) return canonical;
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const action = parsed as Record<string, unknown>;
    if (action.action === "type") {
      return exactKeys(action, ["action", "text"]) && isAsciiText(action.text)
        ? { op: "type", text: action.text, interval_ms: 0 }
        : null;
    }
    if (action.action === "combo" && exactKeys(action, ["action", "keys"]) && Array.isArray(action.keys)) {
      const normalizedKeys = action.keys.map((key) => {
        if (typeof key !== "string") return null;
        const upper = key.toUpperCase();
        return ACTION_KEY_ALIASES[upper] ?? (ALLOWED_KEYS.has(upper) ? upper : null);
      });
      if (
        normalizedKeys.length < 1 ||
        normalizedKeys.length > 6 ||
        normalizedKeys.some((key) => key === null)
      ) return null;
      const keys = normalizedKeys as string[];
      return new Set(keys).size === keys.length
        ? { op: "combo", keys, hold_ms: 50 }
        : null;
    }
    return null;
  } catch {
    return null;
  }
}

export function describeInput(command: GatewayInputCommand): string {
  switch (command.op) {
    case "type":
      return `Type text · ${command.text.length} characters`;
    case "key":
      return `Key ${command.event} · ${command.key}`;
    case "combo":
      return `Key combination · ${command.keys.length} keys`;
    case "mouse_move":
      return "Relative pointer movement";
    case "mouse_button":
      return `Pointer ${command.event} · ${command.button}`;
    case "release_all":
      return "Input state released";
    case "ping":
      return "Transport ping";
  }
}

function clampAxis(value: number): number {
  return Math.max(-127, Math.min(127, Math.trunc(value)));
}

export function splitRelativeMovement(dx: number, dy: number, wheel: number): GatewayInputCommand[] {
  const commands: GatewayInputCommand[] = [];
  let remainingX = Math.trunc(dx);
  let remainingY = Math.trunc(dy);
  let remainingWheel = Math.trunc(wheel);
  while (remainingX !== 0 || remainingY !== 0 || remainingWheel !== 0) {
    const partX = clampAxis(remainingX);
    const partY = clampAxis(remainingY);
    const partWheel = clampAxis(remainingWheel);
    commands.push({ op: "mouse_move", dx: partX, dy: partY, wheel: partWheel });
    remainingX -= partX;
    remainingY -= partY;
    remainingWheel -= partWheel;
  }
  return commands;
}

export function validTypeText(text: string): boolean {
  return isAsciiText(text);
}
