/**
 * Fixed-size circular buffer for chart time-series data.
 * Maintains at most `maxSize` items, evicting the oldest on overflow.
 */
export class RollingBuffer<T> {
  private buffer: T[];
  private readonly maxSize: number;

  constructor(maxSize: number) {
    this.buffer = [];
    this.maxSize = maxSize;
  }

  /** Append a value, evicting the oldest if at capacity. */
  push(value: T): void {
    this.buffer.push(value);
    if (this.buffer.length > this.maxSize) {
      this.buffer.shift();
    }
  }

  /** Return a shallow copy of all items in insertion order. */
  getAll(): T[] {
    return [...this.buffer];
  }

  /** Remove all items. */
  clear(): void {
    this.buffer = [];
  }

  /** Current number of items in the buffer. */
  get length(): number {
    return this.buffer.length;
  }
}
