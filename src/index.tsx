import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import FinancialTerminal from './components/FinancialTerminal';
import ErrorBoundary from './components/ErrorBoundary';
import { AuthProvider } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import './index.css';
import './components/PublicTerminalSimplify.css';

// 终端独占模式：构建时 REACT_APP_TERMINAL_ONLY=true，公开免登录，整页仅渲染金融终端。
const TERMINAL_ONLY = process.env.REACT_APP_TERMINAL_ONLY === 'true';
// 完整版外壳（antd + i18n + 整站 App）代码分割：终端版永不加载它 → antd/i18n 不进终端主包，首屏更快。
const AppShell = React.lazy(() => import('./AppShell'));
const OntologyStandalone = React.lazy(() => import('./components/OntologyStandalone'));
const ONTOLOGY_PATH = window.location.pathname.replace(/\/+$/, '') === '/ontology';
// Local component workbench: available in development without loading the application shell.
const MarketUIShowcase = process.env.NODE_ENV === 'development'
  ? React.lazy(() => import('./components/market-ui/examples/MarketUIShowcase')) : null;
const MARKET_UI_PATH = window.location.pathname.replace(/\/+$/, '') === '/dev/ui';

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <React.StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <AuthProvider>
          <ErrorBoundary>
            {MarketUIShowcase && MARKET_UI_PATH ? (
              <React.Suspense fallback={<p role="status">组件示例加载中</p>}>
                <MarketUIShowcase />
              </React.Suspense>
            ) : TERMINAL_ONLY && ONTOLOGY_PATH ? (
              <React.Suspense fallback={null}>
                <OntologyStandalone />
              </React.Suspense>
            ) : TERMINAL_ONLY ? (
              <div className="terminal-only-root">
                <FinancialTerminal />
              </div>
            ) : (
              <React.Suspense fallback={null}>
                <AppShell />
              </React.Suspense>
            )}
          </ErrorBoundary>
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  </React.StrictMode>
);
