import {describe, expect, it} from 'vitest';
import {resolveAnimationTime} from '../src/animationTime';

describe('resolveAnimationTime', () => {
  it('pins every capture frame to the requested time', () => {
    const query = new URLSearchParams('captureTime=1.25');
    expect(resolveAnimationTime(0.1, query)).toBe(1.25);
    expect(resolveAnimationTime(8.4, query)).toBe(1.25);
  });

  it('preserves live elapsed time without a valid capture override', () => {
    expect(resolveAnimationTime(2.75, new URLSearchParams())).toBe(2.75);
    expect(resolveAnimationTime(2.75, new URLSearchParams('captureTime=not-a-number'))).toBe(2.75);
    expect(resolveAnimationTime(2.75, new URLSearchParams('captureTime=-1'))).toBe(2.75);
  });
});
