import React, { useState } from 'react';
import { Tabs, Card } from 'antd';
import { DashboardOutlined, FireOutlined, ShoppingOutlined } from '@ant-design/icons';
import { AppState } from '../types';
import Dashboard from './Dashboard';
import StockList from './StockList';
import Shop from './Shop';

interface HomePageProps {
  appState: AppState;
  onStockSelect: (stock: any) => void;
  onProductClick: (product: any) => void;
  onAddToCart: (product: any, variantId: string, quantity: number) => void;
}

const HomePage: React.FC<HomePageProps> = ({
  appState,
  onStockSelect,
  onProductClick,
  onAddToCart
}) => {
  const [activeTab, setActiveTab] = useState('dashboard');

  const tabItems = [
    {
      key: 'dashboard',
      label: (
        <span>
          <DashboardOutlined />
          仪表盘
        </span>
      ),
      children: (
        <Dashboard
          appState={appState}
          onStockSelect={onStockSelect}
        />
      )
    },
    {
      key: 'stocks',
      label: (
        <span>
          <FireOutlined />
          个股专区
        </span>
      ),
      children: (
        <StockList
          stocks={appState.stocks}
          onStockSelect={onStockSelect}
        />
      )
    },
    {
      key: 'shop',
      label: (
        <span>
          <ShoppingOutlined />
          商城
        </span>
      ),
      children: (
        <Shop
          products={appState.products}
          onProductClick={onProductClick}
          onAddToCart={onAddToCart}
        />
      )
    }
  ];

  return (
    <div style={{ minHeight: '100vh' }}>
      <Card style={{ margin: '16px' }}>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
          size="large"
        />
      </Card>
    </div>
  );
};

export default HomePage;

