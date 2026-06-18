export function debounce<T extends (...args: any[]) => any>(
  fn: T,
  delay: number
): (...args: Parameters<T>) => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return (...args: Parameters<T>) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      fn(...args);
      timer = null;
    }, delay);
  };
}

export function throttle<T extends (...args: any[]) => any>(
  fn: T,
  interval: number
): { (...args: Parameters<T>): void; cancel: () => void } {
  let lastTime = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const throttled = (...args: Parameters<T>) => {
    const now = Date.now();
    if (now - lastTime >= interval) {
      lastTime = now;
      fn(...args);
    } else if (!timer) {
      timer = setTimeout(() => {
        lastTime = Date.now();
        fn(...args);
        timer = null;
      }, interval - (now - lastTime));
    }
  };
  throttled.cancel = () => {
    if (timer) { clearTimeout(timer); timer = null; }
  };
  return throttled;
}
