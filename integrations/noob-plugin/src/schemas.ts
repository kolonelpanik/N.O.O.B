import { z } from "zod";

export const DeviceId = z.string().regex(/^noob_[a-z0-9]{16,64}$/);
export const CandidateId = z.string().regex(/^candidate_[A-Za-z0-9_-]{16,128}$/);
export const ControlSessionId = z.string().regex(/^ctl_[A-Za-z0-9_-]{24,128}$/);
export const RequestId = z.string().uuid();
export const FrameToken = z.string().regex(/^ft1\.[A-Za-z0-9_-]{16,2048}\.[A-Za-z0-9_-]{32,128}$/);
export const MediaId = z.string().regex(/^m_[0-9a-f]{32}$/);
export const CameraJobId = z.string().regex(/^j_[0-9a-f]{32}$/);
export const ClipFrameIndex = z.number().int().min(0).max(149);
export const SourceId = z.enum(["target", "environment"]);
export const MouseButton = z.enum(["left", "right", "middle"]);

export const KeyName = z.enum([
  "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
  "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
  "ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
  "ENTER", "ESCAPE", "BACKSPACE", "TAB", "SPACE", "MINUS", "EQUALS",
  "LEFT_BRACKET", "RIGHT_BRACKET", "BACKSLASH", "SEMICOLON", "QUOTE", "GRAVE_ACCENT",
  "COMMA", "PERIOD", "FORWARD_SLASH", "CAPS_LOCK",
  "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
  "PRINT_SCREEN", "SCROLL_LOCK", "PAUSE", "INSERT", "HOME", "PAGE_UP", "DELETE", "END",
  "PAGE_DOWN", "RIGHT_ARROW", "LEFT_ARROW", "DOWN_ARROW", "UP_ARROW", "APPLICATION",
  "LEFT_CONTROL", "LEFT_SHIFT", "LEFT_ALT", "LEFT_GUI", "RIGHT_CONTROL", "RIGHT_SHIFT", "RIGHT_ALT", "RIGHT_GUI",
  "GUI", "CMD", "COMMAND", "CTRL", "CONTROL", "SHIFT", "ALT", "OPTION",
]);

export const ReadAnnotations = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: true,
} as const;

export const LocalReadAnnotations = { ...ReadAnnotations, openWorldHint: false } as const;

export const WriteAnnotations = {
  readOnlyHint: false,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: true,
} as const;

export const InputAnnotations = {
  readOnlyHint: false,
  destructiveHint: true,
  idempotentHint: false,
  openWorldHint: true,
} as const;
