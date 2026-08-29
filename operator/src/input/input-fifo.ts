export interface FifoResult<T> {
  executed: boolean;
  value?: T;
}

export class InputQueueOverflowError extends Error {
  constructor() {
    super("input_queue_overflow");
    this.name = "InputQueueOverflowError";
  }
}

interface CoalescedTail {
  generation: number;
  key: string;
  started: boolean;
  value: unknown;
  promise: Promise<FifoResult<unknown>>;
}

export class BoundedInputFifo {
  private tail: Promise<void> = Promise.resolve();
  private activeGeneration = 0;
  private pendingCount = 0;
  private coalescedTail: CoalescedTail | null = null;

  constructor(private readonly maximumPending = 128) {
    if (!Number.isInteger(maximumPending) || maximumPending < 1) {
      throw new TypeError("maximumPending must be a positive integer");
    }
  }

  get generation(): number {
    return this.activeGeneration;
  }

  get pending(): number {
    return this.pendingCount;
  }

  invalidate(): number {
    this.activeGeneration += 1;
    this.coalescedTail = null;
    return this.activeGeneration;
  }

  whenIdle(): Promise<void> {
    return this.tail;
  }

  enqueue<T>(
    generation: number,
    operation: () => Promise<T>,
    onFailure: (error: unknown) => void,
  ): Promise<FifoResult<T>> {
    if (generation !== this.activeGeneration) {
      return Promise.resolve({ executed: false });
    }
    if (this.pendingCount >= this.maximumPending) {
      const error = new InputQueueOverflowError();
      onFailure(error);
      return Promise.reject(error);
    }

    // A non-coalesced command is an ordering barrier. Later movement must not
    // merge backward across a key or button transition that entered first.
    this.coalescedTail = null;
    this.pendingCount += 1;
    const run = this.tail.then(async (): Promise<FifoResult<T>> => {
      if (generation !== this.activeGeneration) return { executed: false };
      try {
        return { executed: true, value: await operation() };
      } catch (error) {
        onFailure(error);
        throw error;
      }
    });
    this.tail = run.then(
      () => undefined,
      () => undefined,
    );
    return run.finally(() => {
      this.pendingCount -= 1;
    });
  }

  enqueueCoalesced<TValue, TResult>(
    generation: number,
    key: string,
    value: TValue,
    merge: (current: TValue, next: TValue) => TValue | null,
    operation: (merged: TValue) => Promise<TResult>,
    onFailure: (error: unknown) => void,
  ): Promise<FifoResult<TResult>> {
    if (generation !== this.activeGeneration) {
      return Promise.resolve({ executed: false });
    }

    const existing = this.coalescedTail;
    if (
      existing !== null &&
      !existing.started &&
      existing.generation === generation &&
      existing.key === key
    ) {
      const merged = merge(existing.value as TValue, value);
      if (merged !== null) {
        existing.value = merged;
        return existing.promise as Promise<FifoResult<TResult>>;
      }
    }

    if (this.pendingCount >= this.maximumPending) {
      const error = new InputQueueOverflowError();
      onFailure(error);
      return Promise.reject(error);
    }

    const entry: CoalescedTail = {
      generation,
      key,
      started: false,
      value,
      promise: Promise.resolve({ executed: false }),
    };
    this.pendingCount += 1;
    const run = this.tail.then(async (): Promise<FifoResult<TResult>> => {
      entry.started = true;
      if (this.coalescedTail === entry) this.coalescedTail = null;
      if (generation !== this.activeGeneration) return { executed: false };
      try {
        return { executed: true, value: await operation(entry.value as TValue) };
      } catch (error) {
        onFailure(error);
        throw error;
      }
    });
    const result = run.finally(() => {
      this.pendingCount -= 1;
    });
    entry.promise = result as Promise<FifoResult<unknown>>;
    this.coalescedTail = entry;
    this.tail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }
}
