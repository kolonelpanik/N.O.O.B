import { describe, expect, it } from "vitest";
import {
  describeInput,
  domCodeToGatewayKey,
  mapKeyboardEvent,
  mergeGatewayMouseMoves,
  parseAgentPayload,
  splitGatewayRelativeMovement,
  splitRelativeMovement,
  validateGatewayInput,
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

  it("coalesces browser motion into tightly bounded gateway batches", () => {
    expect(splitGatewayRelativeMovement(300, -200, 0)).toEqual([
      { op: "mouse_move", dx: 300, dy: -200, wheel: 0 },
    ]);
    expect(splitGatewayRelativeMovement(1100, 0, 140)).toEqual([
      { op: "mouse_move", dx: 1016, dy: 0, wheel: 127 },
      { op: "mouse_move", dx: 84, dy: 0, wheel: 13 },
    ]);
    expect(splitGatewayRelativeMovement(Number.POSITIVE_INFINITY, 0, 0)).toEqual([]);
  });

  it("merges only adjacent movement that stays inside one gateway burst", () => {
    expect(
      mergeGatewayMouseMoves(
        { op: "mouse_move", dx: 400, dy: -200, wheel: 0 },
        { op: "mouse_move", dx: 500, dy: 100, wheel: 1 },
      ),
    ).toEqual({ op: "mouse_move", dx: 900, dy: -100, wheel: 1 });
    expect(
      mergeGatewayMouseMoves(
        { op: "mouse_move", dx: 1016, dy: 0, wheel: 0 },
        { op: "mouse_move", dx: 1, dy: 0, wheel: 0 },
      ),
    ).toBeNull();
  });

  it("validates the aggregate HTTP bound without widening wheel reports", () => {
    expect(
      validateGatewayInput({ op: "mouse_move", dx: 1016, dy: -1016, wheel: 127 }),
    ).toEqual({ op: "mouse_move", dx: 1016, dy: -1016, wheel: 127 });
    expect(
      validateGatewayInput({ op: "mouse_move", dx: 1017, dy: 0, wheel: 0 }),
    ).toBeNull();
    expect(
      validateGatewayInput({ op: "mouse_move", dx: 0, dy: 0, wheel: 128 }),
    ).toBeNull();
  });

  it("keeps typed content out of action metadata", () => {
    const command = { op: "type", text: "secret-like typed content", interval_ms: 0 } as const;
    expect(describeInput(command)).toBe("Type text · 25 characters");
    expect(describeInput(command)).not.toContain(command.text);
    expect(validTypeText("ASCII only\n")).toBe(true);
    expect(validTypeText("not ASCII: π")).toBe(false);
  });
});
