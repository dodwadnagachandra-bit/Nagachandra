import { describe, expect, it } from "vitest";
import { RollingBuffer } from "../lib/rolling-buffer";

describe("RollingBuffer", () => {
  it("starts empty with length 0", () => {
    const buf = new RollingBuffer<number>(5);
    expect(buf.length).toBe(0);
    expect(buf.getAll()).toEqual([]);
  });

  it("pushes items and tracks length", () => {
    const buf = new RollingBuffer<number>(5);
    buf.push(1);
    buf.push(2);
    buf.push(3);
    expect(buf.length).toBe(3);
    expect(buf.getAll()).toEqual([1, 2, 3]);
  });

  it("evicts oldest items when over maxSize", () => {
    const buf = new RollingBuffer<number>(3);
    buf.push(1);
    buf.push(2);
    buf.push(3);
    buf.push(4);
    buf.push(5);
    expect(buf.length).toBe(3);
    expect(buf.getAll()).toEqual([3, 4, 5]);
  });

  it("clears all items", () => {
    const buf = new RollingBuffer<number>(5);
    buf.push(1);
    buf.push(2);
    buf.clear();
    expect(buf.length).toBe(0);
    expect(buf.getAll()).toEqual([]);
  });

  it("getAll returns a copy (not the internal array)", () => {
    const buf = new RollingBuffer<number>(5);
    buf.push(1);
    buf.push(2);
    const all = buf.getAll();
    all.push(99);
    expect(buf.length).toBe(2);
    expect(buf.getAll()).toEqual([1, 2]);
  });

  it("works with object types", () => {
    const buf = new RollingBuffer<{ ts: number; value: number }>(2);
    buf.push({ ts: 1, value: 10 });
    buf.push({ ts: 2, value: 20 });
    buf.push({ ts: 3, value: 30 });
    expect(buf.getAll()).toEqual([
      { ts: 2, value: 20 },
      { ts: 3, value: 30 },
    ]);
  });
});
