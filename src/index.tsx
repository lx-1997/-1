import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App as AntdApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import './index.css';

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <React.StrictMode>
    <BrowserRouter>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: '#167c80',
            colorSuccess: '#12805c',
            colorWarning: '#b7791f',
            colorError: '#c43e3e',
            colorInfo: '#2f6f9f',
            colorText: '#172026',
            colorTextSecondary: '#64727d',
            colorBorder: '#d9e1e7',
            colorBgLayout: '#eef2f5',
            colorBgContainer: '#ffffff',
            borderRadius: 6,
            fontSize: 13,
            controlHeight: 34
          },
          components: {
            Button: {
              borderRadius: 5,
              controlHeight: 32
            },
            Card: {
              borderRadiusLG: 6,
              paddingLG: 16
            },
            Menu: {
              itemBorderRadius: 4,
              itemHeight: 38
            },
            Tabs: {
              horizontalMargin: '0 18px 0 0',
              titleFontSize: 14
            }
          }
        }}
      >
        <AntdApp>
          <App />
        </AntdApp>
      </ConfigProvider>
    </BrowserRouter>
  </React.StrictMode>
);
