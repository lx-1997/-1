import React from 'react';

export type ResearchIconName =
  | 'overview' | 'research' | 'sources' | 'chart' | 'valuation'
  | 'bullish' | 'neutral' | 'bearish' | 'check' | 'warning' | 'refresh'
  | 'arrow-up-right' | 'arrow-left' | 'close' | 'search' | 'document'
  | 'quote' | 'flow' | 'ratings' | 'business' | 'fundamentals' | 'earnings'
  | 'technical' | 'capital' | 'news' | 'link' | 'calendar' | 'shield'
  | 'chevron-down' | 'target';

const PATHS: Record<ResearchIconName, string> = {
  overview: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  research: 'M14 3H5a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h7M14 3v5h5M14 3l5 5v3M8 8h2M8 12h4M8 16h2M19 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM18.2 18.2 21 21',
  sources: 'm12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5',
  chart: 'M3 3v18h18M6 16l4-7 4 4 6-8',
  valuation: 'M6 3h12a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1ZM8 6h8v4H8zM8 14h1M12 14h1M16 14h1M8 18h1M12 18h1M16 18h1',
  bullish: 'M3 17l6-6 4 3 8-10M15 4h6v6',
  neutral: 'M3 12h18M17 8l4 4-4 4',
  bearish: 'M3 7l6 6 4-3 8 10M15 20h6v-6',
  check: 'M20 11.1V12a8 8 0 1 1-4.7-7.3M8 11l4 4L21 6',
  warning: 'm10.3 4-8 14a2 2 0 0 0 1.7 3h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0ZM12 9v4M12 17h.01',
  refresh: 'M20 7v5h-5M4 17v-5h5M5.5 7a7 7 0 0 1 11.6-2L20 8M4 16l2.9 3A7 7 0 0 0 18.5 17',
  'arrow-up-right': 'M6 18 18 6M6 6h12v12',
  'arrow-left': 'M20 12H4M10 6l-6 6 6 6',
  close: 'm6 6 12 12M6 18 18 6',
  search: 'M18 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0ZM16 15l5 6',
  document: 'M14 3H5a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9l-6-6ZM14 3v6h6M8 13h8M8 17h6',
  quote: 'M10 5H4v7h5c0 3-1 5-4 7M20 5h-6v7h5c0 3-1 5-4 7',
  flow: 'M3 7h17M16 3l4 4-4 4M21 17H4M8 13l-4 4 4 4',
  ratings: 'M12 3v9h9M21 12a9 9 0 1 1-9-9M16 3.9a9 9 0 0 1 4.1 4.1',
  business: 'M4 21V8l8-5 8 5v13M8 21v-5h8v5M8 9h1M15 9h1M8 12h1M15 12h1M2 21h20',
  fundamentals: 'M5 20V10h3v10M11 20V4h3v16M17 20v-7h3v7M3 20h19',
  earnings: 'M3 18l6-7 4 3 8-10M15 4h6v6M3 21h18',
  technical: 'M5 3v18M3 7h4v8H3zM12 3v18M10 5h4v7h-4zM19 3v18M17 10h4v8h-4z',
  capital: 'M3 7h17M16 3l4 4-4 4M21 17H4M8 13l-4 4 4 4',
  news: 'M5 3h15v18H5a2 2 0 0 1-2-2V8h2M5 17v2M8 6h9v4H8zM8 13h9M8 17h6',
  link: 'M10 13a5 5 0 0 0 7.1 0l3-3a5 5 0 0 0-7.1-7.1l-1.7 1.7M14 11a5 5 0 0 0-7.1 0l-3 3a5 5 0 0 0 7.1 7.1l1.7-1.7',
  calendar: 'M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2ZM7 3v4M17 3v4M3 10h18M7 14h2M13 14h2M7 18h2',
  shield: 'm12 3 8 3v6c0 4-3 7-8 9-5-2-8-5-8-9V6l8-3ZM8 12l3 3 5-6',
  'chevron-down': 'm6 9 6 6 6-6',
  target: 'M21 12a9 9 0 1 1-9-9M17 12a5 5 0 1 1-5-5M12 12l9-9M17 3h4v4',
};

interface ResearchIconProps {
  name: ResearchIconName;
  className?: string;
  size?: number;
}

/** Decorative icons keep the accompanying text as the accessible label. */
export default function ResearchIcon({ name, className, size = 20 }: ResearchIconProps) {
  return <svg className={`si-icon${className ? ` ${className}` : ''}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={PATHS[name]} /></svg>;
}
