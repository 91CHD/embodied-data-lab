import { describe, it, expect } from 'vitest';
import { mediaTime, frameAtTime, mergeEvents } from './timeline';
describe('episode cursor', () => {
  it('maps local time into the shared camera shard and clamps to episode end', () => {
    expect(mediaTime(0, 7.3, 8.1, 10)).toBe(7.3);
    expect(mediaTime(0.4, 7.3, 8.1, 10)).toBeCloseTo(7.7);
    expect(mediaTime(5, 7.3, 8.1, 10)).toBe(8.0);
  });
  it('never seeks to the next episode start in a shared video, including a single-frame episode', () => {
    expect(mediaTime(10, 2, 3, 30)).toBeCloseTo(3 - 1 / 30);
    expect(mediaTime(1, 2, 2.1, 10)).toBe(2);
  });
  it('uses real episode-local timestamps rather than assuming integer fps', () => {
    expect(frameAtTime([0, 0.04, 0.083, 0.125], 0.09)).toBe(2);
    expect(frameAtTime([0, 0.04, 0.083], 0)).toBe(0);
    expect(frameAtTime([0, 0.04, 0.083], 2)).toBe(2);
  });
});
describe('durable event replay', () => {
  it('deduplicates overlapping history and stream and keeps database id order', () => {
    const e = (id: number, message = 'log') => ({
      id,
      message,
      job_id: 'x',
      level: 'info',
      created_at: 'x',
    });
    expect(mergeEvents([e(2), e(4)], [e(1), e(2), e(3)]).map((e) => e.id)).toEqual([1, 2, 3, 4]);
  });
});
