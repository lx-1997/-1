import React from 'react';
import { App as AntdApp, ConfigProvider, theme as antTheme } from 'antd';
import InvestmentOntologyCenter from './InvestmentOntologyCenter';

const OntologyStandalone: React.FC = () => (
  <ConfigProvider
    theme={{
      algorithm: antTheme.darkAlgorithm,
      token: {
        colorPrimary: '#2997ff',
        colorSuccess: '#30d158',
        colorWarning: '#ffd60a',
        colorError: '#ff453a',
        colorInfo: '#2997ff',
        colorText: '#f5f5f7',
        colorTextSecondary: '#a1a1a6',
        colorBorder: '#2c2d32',
        colorBorderSecondary: '#222329',
        colorBgLayout: '#0b0c0f',
        colorBgContainer: '#17181c',
        colorBgElevated: '#202126',
        borderRadius: 10,
        fontSize: 13,
      },
      components: {
        Button: { primaryShadow: 'none' },
        Select: {
          selectorBg: '#17181c',
          colorBorder: '#2c2d32',
          optionSelectedBg: 'rgba(41,151,255,0.14)',
          optionActiveBg: 'rgba(255,255,255,0.045)',
        },
      },
    }}
  >
    <AntdApp>
      <div className="ontology-standalone">
        <nav className="ontology-standalone-nav" aria-label="投资本体导航">
          <a href="/" className="ontology-standalone-brand">
            <span>◆</span>
            <strong>稻草财经</strong>
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
