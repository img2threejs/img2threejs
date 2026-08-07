export function resolveAnimationTime(
  elapsedSeconds: number,
  query: URLSearchParams,
): number {
  const requestedTime = query.get('captureTime');
  if (requestedTime === null || requestedTime.trim() === '') {
    return elapsedSeconds;
  }

  const captureTime = Number(requestedTime);
  return Number.isFinite(captureTime) && captureTime >= 0
    ? captureTime
    : elapsedSeconds;
}
