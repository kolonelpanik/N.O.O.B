import { describe, expect, it, vi } from "vitest";
import { BoundedInputFifo, InputQueueOverflowError } from "./input-fifo";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("bounded input FIFO", () => {
  it("never lets key-up overtake a failed key-down and suppresses the queued command", async () => {
    const fifo = new BoundedInputFifo();
    const generation = fifo.invalidate();
    const down = deferred<void>();
    const events: string[] = [];
    const failClosed = vi.fn(() => {
      events.push("release-all");
      fifo.invalidate();
    });

    const downResult = fifo.enqueue(generation, async () => {
      events.push("down");
      await down.promise;
    }, failClosed);
    const upResult = fifo.enqueue(generation, async () => {
      events.push("up");
    }, failClosed);

    await Promise.resolve();
    expect(events).toEqual(["down"]);
    down.reject(new Error("transport failed"));
    await expect(downResult).rejects.toThrow("transport failed");
    await expect(upResult).resolves.toEqual({ executed: false });
    expect(events).toEqual(["down", "release-all"]);
    expect(failClosed).toHaveBeenCalledTimes(1);
  });

  it("preserves pointer movement before a later button transition", async () => {
    const fifo = new BoundedInputFifo();
    const generation = fifo.invalidate();
    const movement = deferred<void>();
    const events: string[] = [];

    const moveResult = fifo.enqueue(generation, async () => {
      events.push("move-start");
      await movement.promise;
      events.push("move-end");
    }, () => fifo.invalidate());
    const buttonResult = fifo.enqueue(generation, async () => {
      events.push("button-down");
    }, () => fifo.invalidate());

    await Promise.resolve();
    expect(events).toEqual(["move-start"]);
    movement.resolve();
    await Promise.all([moveResult, buttonResult]);
    expect(events).toEqual(["move-start", "move-end", "button-down"]);
  });

  it("fails closed when the bounded queue is full", async () => {
    const fifo = new BoundedInputFifo(1);
    const generation = fifo.invalidate();
    const first = deferred<void>();
    const failClosed = vi.fn(() => fifo.invalidate());
    const firstResult = fifo.enqueue(generation, () => first.promise, failClosed);

    await expect(fifo.enqueue(generation, async () => undefined, failClosed))
      .rejects.toBeInstanceOf(InputQueueOverflowError);
    expect(failClosed).toHaveBeenCalledTimes(1);
    first.resolve();
    await firstResult;
  });

  it("coalesces adjacent movement without crossing key and button barriers", async () => {
    const fifo = new BoundedInputFifo();
    const generation = fifo.invalidate();
    const blocker = deferred<void>();
    const delivered: Array<string | { dx: number; dy: number }> = [];
    const merge = (
      current: { dx: number; dy: number },
      next: { dx: number; dy: number },
    ) => ({ dx: current.dx + next.dx, dy: current.dy + next.dy });

    const active = fifo.enqueue(generation, () => blocker.promise, () => fifo.invalidate());
    const firstMove = fifo.enqueueCoalesced(
      generation,
      "mouse",
      { dx: 5, dy: -2 },
      merge,
      async (movement) => { delivered.push(movement); },
      () => fifo.invalidate(),
    );
    const mergedMove = fifo.enqueueCoalesced(
      generation,
      "mouse",
      { dx: 7, dy: 2 },
      merge,
      async (movement) => { delivered.push(movement); },
      () => fifo.invalidate(),
    );
    const key = fifo.enqueue(
      generation,
      async () => { delivered.push("key-down"); },
      () => fifo.invalidate(),
    );
    const laterMove = fifo.enqueueCoalesced(
      generation,
      "mouse",
      { dx: 3, dy: 1 },
      merge,
      async (movement) => { delivered.push(movement); },
      () => fifo.invalidate(),
    );
    const button = fifo.enqueue(
      generation,
      async () => { delivered.push("button-down"); },
      () => fifo.invalidate(),
    );

    expect(firstMove).toBe(mergedMove);
    expect(fifo.pending).toBe(5);
    blocker.resolve();
    await Promise.all([active, firstMove, mergedMove, key, laterMove, button]);
    expect(delivered).toEqual([
      { dx: 12, dy: 0 },
      "key-down",
      { dx: 3, dy: 1 },
      "button-down",
    ]);
    expect(fifo.pending).toBe(0);
  });
});
