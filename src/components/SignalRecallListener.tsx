import { useEffect, useRef, useState } from 'react';
import type { Stock } from '../types';
import { createRealtimeMessageStream } from '../services/eventService';
import { evaluateAndNotify, loadRecallPrefs, RECALL_PREFS_EVENT } from '../utils/signalRecall';

interface SignalRecallListenerProps {
  stocks: Stock[];
}

/**
 * 应用级静默监听：只要开启了信号召回，就在后台订阅信号流；
 * 盯的标的出信号时调用 evaluateAndNotify 把用户叫回来——
 * 不依赖当前停留在哪个页面。未开启时不连接，避免空跑 SSE。
 */
const SignalRecallListener: React.FC<SignalRecallListenerProps> = ({ stocks }) => {
  const watchlistRef = useRef<string[]>([]);
  const [enabled, setEnabled] = useState<boolean>(() => loadRecallPrefs().browserEnabled);

  useEffect(() => {
    watchlistRef.current = stocks.map(stock => stock.symbol).filter(Boolean);
  }, [stocks]);

  // 偏好变化（本页切换开关或其它标签页）时，重新决定是否需要监听。
  useEffect(() => {
    const sync = () => setEnabled(loadRecallPrefs().browserEnabled);
    window.addEventListener(RECALL_PREFS_EVENT, sync);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(RECALL_PREFS_EVENT, sync);
      window.removeEventListener('storage', sync);
    };
  }, []);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    const stream = createRealtimeMessageStream({
      onMessage: message => {
        evaluateAndNotify(message, watchlistRef.current);
      },
      onStatus: () => {
        /* 静默监听，连接状态不打扰用户 */
      },
      onError: () => {
        /* 重连交给 stream 内部 */
      },
    });
    return () => stream.close();
  }, [enabled]);

  return null;
};

export default SignalRecallListener;
