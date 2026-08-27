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

export class BoundedInputFifo {
  private tail: Promise<void> = Promise.resolve();
  private activeGeneration = 0;
  private pendingCount = 0;

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
}
