import { describe, expect, it } from "vitest";
import {
  describeInput,
  domCodeToGatewayKey,
  mapKeyboardEvent,
  parseAgentPayload,
  splitRelativeMovement,
  validTypeText,
} from "./input-mapping";

describe("input mapping", () => {
  it("maps DOM physical codes to the strict gateway vocabulary", () => {
    expect(domCodeToGatewayKey("KeyA")).toBe("A");
    expect(domCodeToGatewayKey("Digit0")).toBe("ZERO");
    expect(domCodeToGatewayKey("MetaLeft")).toBe("LEFT_GUI");
    expect(domCodeToGatewayKey("ArrowDown")).toBe("DOWN_ARROW");
    expect(domCodeToGatewayKey("AudioVolumeUp")).toBeNull();
  });

  it("drops keyboard repeats and never relies on typed key content", () => {
    expect(mapKeyboardEvent({ code: "KeyQ", repeat: true }, "down")).toBeNull();
    expect(mapKeyboardEvent({ code: "KeyQ", repeat: false }, "down")).toEqual({
      op: "key",
      event: "down",
      key: "Q",
    });
  });

  it("validates exact agent payload shapes", () => {
    expect(parseAgentPayload('{"op":"ping"}')).toEqual({ op: "ping" });
    expect(parseAgentPayload('{"op":"key","event":"down","key":"LEFT_GUI"}')).toEqual({
      op: "key",
      event: "down",
      key: "LEFT_GUI",
    });
    expect(parseAgentPayload('{"op":"ping","extra":true}')).toBeNull();
    expect(parseAgentPayload('{"op":"key","event":"down","key":"NotAKey"}')).toBeNull();
  });

  it("normalizes documented Phase-4 action envelopes into canonical gateway input", () => {
    expect(parseAgentPayload('{"action":"type","text":"ls -la\\n"}')).toEqual({
      op: "type",
      text: "ls -la\n",
      interval_ms: 0,
    });
    expect(parseAgentPayload('{"action":"combo","keys":["GUI","SPACE"]}')).toEqual({
      op: "combo",
      keys: ["LEFT_GUI", "SPACE"],
      hold_ms: 50,
    });
    expect(parseAgentPayload('{"action":"combo","keys":["GUI","LEFT_GUI"]}')).toBeNull();
    expect(parseAgentPayload('{"action":"type","text":"ok","extra":true}')).toBeNull();
  });

  it("chunks relative movement into gateway-safe signed bytes", () => {
    expect(splitRelativeMovement(300, -200, 0)).toEqual([
      { op: "mouse_move", dx: 127, dy: -127, wheel: 0 },
      { op: "mouse_move", dx: 127, dy: -73, wheel: 0 },
      { op: "mouse_move", dx: 46, dy: 0, wheel: 0 },
    ]);
  });

  it("keeps typed content out of action metadata", () => {
    const command = { op: "type", text: "secret-like typed content", interval_ms: 0 } as const;
    expect(describeInput(command)).toBe("Type text · 25 characters");
    expect(describeInput(command)).not.toContain(command.text);
    expect(validTypeText("ASCII only\n")).toBe(true);
    expect(validTypeText("not ASCII: π")).toBe(false);
  });
});
