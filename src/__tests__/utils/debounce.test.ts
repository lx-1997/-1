import { debounce, throttle } from '../../utils/debounce';

describe('debounce', () => {
  jest.useFakeTimers();

  it('should delay function execution by the specified delay', () => {
    const fn = jest.fn();
    const debounced = debounce(fn, 300);

    debounced('a', 'b');
    expect(fn).not.toHaveBeenCalled();

    jest.advanceTimersByTime(300);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith('a', 'b');
  });

  it('should cancel previous calls when invoked again within delay', () => {
    const fn = jest.fn();
    const debounced = debounce(fn, 300);

    debounced('first');
    jest.advanceTimersByTime(100);
    debounced('second');
    jest.advanceTimersByTime(100);
    debounced('third');

    jest.advanceTimersByTime(300);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith('third');
  });

  it('should pass all arguments to the original function', () => {
    const fn = jest.fn();
    const debounced = debounce(fn, 200);

    debounced(1, 'two', { key: 'val' });
    jest.advanceTimersByTime(200);

    expect(fn).toHaveBeenCalledWith(1, 'two', { key: 'val' });
  });

  it('should clear the timer after execution', () => {
    const fn = jest.fn();
    const debounced = debounce(fn, 200);

    debounced();
    jest.advanceTimersByTime(200);
    expect(fn).toHaveBeenCalledTimes(1);

    debounced();
    jest.advanceTimersByTime(200);
    expect(fn).toHaveBeenCalledTimes(2);
  });
});

describe('throttle', () => {
  jest.useFakeTimers();

  it('should execute immediately on the first call', () => {
    const fn = jest.fn();
    const throttled = throttle(fn, 500);

    throttled('x');
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith('x');
  });

  it('should not execute more than once within the interval', () => {
    const fn = jest.fn();
    const throttled = throttle(fn, 500);

    throttled();
    throttled();
    throttled();
    expect(fn).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(500);
    throttled();
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('should schedule a trailing call when invoked mid-interval', () => {
    const fn = jest.fn();
    const throttled = throttle(fn, 500);

    throttled();
    expect(fn).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(300);
    throttled();

    jest.advanceTimersByTime(200);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('should cancel pending execution when cancel is called', () => {
    const fn = jest.fn();
    const throttled = throttle(fn, 500);

    throttled();
    expect(fn).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(300);
    throttled();

    throttled.cancel();

    jest.advanceTimersByTime(200);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('should pass arguments correctly on trailing call', () => {
    const fn = jest.fn();
    const throttled = throttle(fn, 500);

    throttled();
    expect(fn).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(300);
    throttled('trailing', 'args');

    jest.advanceTimersByTime(200);
    expect(fn).toHaveBeenCalledTimes(2);
    expect(fn).toHaveBeenLastCalledWith('trailing', 'args');
  });
});