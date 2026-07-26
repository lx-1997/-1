import React from 'react';
import { App as AntdApp, ConfigProvider, theme as antTheme } from 'antd';
import InvestmentOntologyCenter from './InvestmentOntologyCenter';

const OntologyStandalone: React.FC = () => (
  <ConfigProvider
    theme={{
      algorithm: antTheme.darkAlgorithm,
      token: {
        colorPrimary: '#10a37f',
        colorSuccess: '#10b981',
        colorWarning: '#f59e0b',
        colorError: '#ef4444',
        colorInfo: '#3b82f6',
        colorText: '#ececec',
        colorTextSecondary: '#9b9b9b',
        colorBorder: '#2d2d2d',
        colorBorderSecondary: '#252525',
        colorBgLayout: '#0f0f0f',
        colorBgContainer: '#1a1a1a',
        colorBgElevated: '#242424',
        borderRadius: 8,
        fontSize: 13,
      },
      components: {
        Button: { primaryShadow: 'none' },
        Select: {
          selectorBg: '#1a1a1a',
          colorBorder: '#2d2d2d',
          optionSelectedBg: 'rgba(16,163,127,0.12)',
          optionActiveBg: 'rgba(255,255,255,0.04)',
        },
      },
    }}
  >
    <AntdApp>
      <div className="ontology-standalone">
        <nav className="ontology-standalone-nav" aria-label="投资本体导航">
          <a href="/" className="ontology-standalone-brand">
            <span>◆</span>
            <strong>DEEPFOCUS</strong>
            <small>持仓决策助手</small>
          </a>
          <div>
            <a href="/">返回金融终端</a>
            <a href="/ai-fund">AI 模拟盘</a>
          </div>
        </nav>
        <InvestmentOntologyCenter />
      </div>
    </AntdApp>
  </ConfigProvider>
);

export default OntologyStandalone;
