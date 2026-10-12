export interface ServerSentFrame { event: string; data: string }

/** Parse complete SSE frames across byte/chunk boundaries and release cancelled reads. */
export async function* readServerSentEvents(
  body: ReadableStream<Uint8Array>,
  signal: AbortSignal,
  onActivity?: () => void,
): AsyncGenerator<ServerSentFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let ended = false;
  const cancel = () => {
    // Some test/native readers have only read(); browser readers implement cancel().
    try { void reader.cancel?.().catch(() => {}); } catch { /* Already released. */ }
  };
  signal.addEventListener('abort', cancel, { once: true });
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (signal.aborted) return;
      if (done) { ended = true; return; }
      if (!value?.byteLength) continue;
      onActivity?.();
      // Normalize after combining chunks: a CRLF can straddle two TCP reads.
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n');
      let boundary: number;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        if (signal.aborted) return;
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = '';
        const data: string[] = [];
        for (const line of raw.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).replace(/^ /, '');
          else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
        }
        if (data.length) yield { event, data: data.join('\n') };
      }
      if (buffer.length > 1024 * 1024) throw new Error('实时响应格式异常，请重试');
    }
  } finally {
    signal.removeEventListener('abort', cancel);
    if (!ended) cancel();
    reader.releaseLock?.();
  }
}
