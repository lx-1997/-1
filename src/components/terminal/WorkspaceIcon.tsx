import React from 'react';

const paths: Record<string, React.ReactNode> = {
  stocks: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M7 15v2m5-7v7m5-11v11" /></>,
  ai: <><path d="M20 11.5a8 8 0 0 1-8 8H5l-3 2v-9a8 8 0 0 1 16-1" /><path d="m19 2 1 3 3 1-3 1-1 3-1-3-3-1 3-1zM7 11h5m-5 4h8" /></>,
  market: <><path d="M3 12h4l3-7 4 14 3-7h4" /></>,
  review: <><path d="M4 5h16v16H4zM8 2v6m8-6v6M4 10h16M8 14h3m-3 3h7" /></>,
  articles: <><path d="M4 3h12l4 4v14H4zM15 3v5h5M8 12h8m-8 4h8" /></>,
  notes: <><path d="M5 3h14v18H5zM9 7h6m-6 4h6m-6 4h3M3 7h4m-4 4h4m-4 4h4" /></>,
  reports: <><path d="M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3zM12 6v16M6 8h3m-3 4h3m6-4h3m-3 4h3" /></>,
};
export default function WorkspaceIcon({ name }: { name: string }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.market}</svg>;
}
