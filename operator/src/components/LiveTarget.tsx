import { forwardRef, useCallback, useEffect, useRef } from "react";
import type { GatewayInputCommand } from "../../shared/gateway-contract";
import type { OperatorMode } from "../hooks/useOperatorController";
import { mouseButtonFromDom, splitRelativeMovement } from "../input/input-mapping";

interface LiveTargetProps {
  imageSource: string | null;
  live: boolean;
  claimed: boolean;
  mode: OperatorMode;
  pointerCapture: boolean;
  pointerLocked: boolean;
  usingFrameFallback: boolean;
  onStreamError(): void;
  onStreamRecovered(): void;
  sendInput(command: GatewayInputCommand, recordAction?: boolean): Promise<boolean>;
}

export const LiveTarget = forwardRef<HTMLDivElement, LiveTargetProps>(function LiveTarget(
  {
    imageSource,
    live,
    claimed,
    mode,
    pointerCapture,
    pointerLocked,
    usingFrameFallback,
    onStreamError,
    onStreamRecovered,
    sendInput,
  },
  ref,
) {
  const pendingRef = useRef({ dx: 0, dy: 0, wheel: 0 });
  const frameRequestRef = useRef<number | null>(null);
  const active = claimed && mode === "human" && pointerCapture;

  const flushPointer = useCallback(() => {
    frameRequestRef.current = null;
    const pending = pendingRef.current;
    pendingRef.current = { dx: 0, dy: 0, wheel: 0 };
    const commands = splitRelativeMovement(pending.dx, pending.dy, pending.wheel);
    for (const command of commands) {
      void sendInput(command, false);
    }
  }, [sendInput]);

  const scheduleFlush = useCallback(() => {
    if (frameRequestRef.current === null) {
      frameRequestRef.current = window.requestAnimationFrame(flushPointer);
    }
  }, [flushPointer]);

  useEffect(
    () => () => {
      if (frameRequestRef.current !== null) window.cancelAnimationFrame(frameRequestRef.current);
    },
    [],
  );

  const onMouseMove = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!active || !pointerLocked) return;
    event.preventDefault();
    pendingRef.current.dx += event.movementX;
    pendingRef.current.dy += event.movementY;
    scheduleFlush();
  };

  const onWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (!active) return;
    event.preventDefault();
    const scaled = Math.trunc(-event.deltaY / 24) || -Math.sign(event.deltaY);
    pendingRef.current.wheel += scaled;
    scheduleFlush();
  };

  const onMouseButton = (event: React.MouseEvent<HTMLDivElement>, kind: "down" | "up") => {
    if (!active) return;
    const button = mouseButtonFromDom(event.button);
    if (button === null) return;
    event.preventDefault();
    if (frameRequestRef.current !== null) {
      window.cancelAnimationFrame(frameRequestRef.current);
      flushPointer();
    }
    void sendInput({ op: "mouse_button", button, event: kind }, false);
  };

  return (
    <section className="live-target" aria-labelledby="live-target-title">
      <div className="region-heading-row">
        <h1 id="live-target-title">Live target</h1>
        <span className={`frame-transport ${live ? "frame-transport--live" : ""}`}>
          {live ? (usingFrameFallback ? "Live" : "Live") : "—"}
        </span>
      </div>
      <div className="target-stage">
        <div
          ref={ref}
          className={`target-frame ${active ? "target-frame--capture" : ""} ${pointerLocked ? "target-frame--locked" : ""}`}
          tabIndex={active ? 0 : -1}
          onMouseMove={onMouseMove}
          onMouseDown={(event) => onMouseButton(event, "down")}
          onMouseUp={(event) => onMouseButton(event, "up")}
          onWheel={onWheel}
          onContextMenu={(event) => {
            if (active) event.preventDefault();
          }}
          aria-label="Target display"
        >
          {imageSource === null ? (
            <div className="target-empty" aria-label="Target video unavailable">—</div>
          ) : (
            <img
              src={imageSource}
              alt="Live target video"
              draggable={false}
              onLoad={() => {
                if (!usingFrameFallback) onStreamRecovered();
              }}
              onError={onStreamError}
            />
          )}
        </div>
      </div>
    </section>
  );
});
