import type { JobEvent } from './api';
export function mergeEvents(old: JobEvent[], incoming: JobEvent[]): JobEvent[] {
  return [...new Map([...old, ...incoming].map((e) => [e.id, e])).values()].sort(
    (a, b) => a.id - b.id,
  );
}
export function mediaTime(local: number, start: number, end: number, fps: number) {
  // v3 video end offsets are exclusive: seeking to end would reveal the next episode.
  const lastFrame = Math.max(start, end - 1 / Math.max(1, fps));
  return Math.max(start, Math.min(lastFrame, start + Math.max(0, local)));
}
export function frameAtTime(times: number[], time: number) {
  let lo = 0,
    hi = times.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (times[mid] <= time) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}
