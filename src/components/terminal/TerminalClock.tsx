import React, { useEffect, useState } from 'react';

function clockText(): string {
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date());
}
/** The clock ticks locally, without rerendering the terminal's feeds and AI workspace. */
const TerminalClock: React.FC = () => {
  const [clock, setClock] = useState(clockText);
  useEffect(() => {
    const timer = window.setInterval(() => setClock(clockText()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <b>{clock}</b>;
};
export default React.memo(TerminalClock);
