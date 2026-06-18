import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import FinancialTerminal from './components/FinancialTerminal';
import ErrorBoundary from './components/ErrorBoundary';
import { ThemeProvider } from './context/ThemeContext';
import './index.css';

// 终端独占模式：构建时 REACT_APP_TERMINAL_ONLY=true，公开免登录，整页仅渲染金融终端。
const TERMINAL_ONLY = process.env.REACT_APP_TERMINAL_ONLY === 'true';
// 完整版外壳（antd + i18n + 整站 App）代码分割：终端版永不加载它 → antd/i18n 不进终端主包，首屏更快。
const AppShell = React.lazy(() => import('./AppShell'));

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <React.StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <ErrorBoundary>
          {TERMINAL_ONLY ? (
            <div className="terminal-only-root">
              <FinancialTerminal />
            </div>
          ) : (
            <React.Suspense fallback={null}>
              <AppShell />
            </React.Suspense>
          )}
        </ErrorBoundary>
      </ThemeProvider>
    </BrowserRouter>
  </React.StrictMode>
);
