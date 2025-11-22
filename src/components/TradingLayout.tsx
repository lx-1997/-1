import React, { useState, useEffect } from 'react';
import { Layout, Menu, Avatar, Dropdown, Button, Space, Typography, Badge, Drawer } from 'antd';
import {
  DashboardOutlined,
  FireOutlined,
  UserOutlined,
  LogoutOutlined,
  SettingOutlined,
  BellOutlined,
  MenuOutlined
} from '@ant-design/icons';
import { AppState, ViewType } from '../types';
import Sidebar from './Sidebar';
import Header from './Header';
import MainContent from './MainContent';

const { Sider, Content } = Layout;
const { Text } = Typography;

interface TradingLayoutProps {
  appState: AppState;
  onLogout: () => void;
  onStockSelect: (stock: any) => void;
  onBackToStocks: () => void;
  onPostClick: (post: any) => void;
  onCreatePost: () => void;
  onPurchase: (postId: string, amount: number) => void;
  onRate: (postId: string, rating: number, feedback: string) => void;
  onLike: (postId: string) => void;
  onShare: (postId: string) => void;
  onRecharge: (amount: number, method: string) => void;
  onViewChange: (view: ViewType) => void;
  // 商城相关
  onProductClick: (product: any) => void;
  onAddToCart: (product: any, variantId: string, quantity: number) => void;
  onUpdateCartQuantity: (itemId: string, quantity: number) => void;
  onRemoveFromCart: (itemId: string) => void;
  onCheckout: (items: any[]) => void;
  onOrderPay: (orderId: string, paymentMethod: 'wechat' | 'alipay') => void;
  onOrderCancel: (orderId: string) => void;
  onOrderRefund: (orderId: string) => void;
  onBuyNow: (product: any, variantId: string, quantity: number) => void;
}

const TradingLayout: React.FC<TradingLayoutProps> = ({
  appState,
  onLogout,
  onStockSelect,
  onBackToStocks,
  onPostClick,
  onCreatePost,
  onPurchase,
  onRate,
  onLike,
  onShare,
  onRecharge,
  onViewChange,
  onProductClick,
  onAddToCart,
  onUpdateCartQuantity,
  onRemoveFromCart,
  onCheckout,
  onOrderPay,
  onOrderCancel,
  onOrderRefund,
  onBuyNow
}) => {
  const [collapsed, setCollapsed] = useState(false);
  const [selectedMenu, setSelectedMenu] = useState('home');
  const [isMobile, setIsMobile] = useState(false);
  const [mobileMenuVisible, setMobileMenuVisible] = useState(false);

  // 检测屏幕尺寸
  useEffect(() => {
    const checkIsMobile = () => {
      setIsMobile(window.innerWidth < 768);
      if (window.innerWidth >= 768) {
        setMobileMenuVisible(false);
      }
    };

    checkIsMobile();
    window.addEventListener('resize', checkIsMobile);
    return () => window.removeEventListener('resize', checkIsMobile);
  }, []);

  const menuItems = [
    {
      key: 'home',
      icon: <DashboardOutlined />,
      label: '首页'
    }
  ];

  const userMenuItems = [
    {
      key: 'profile',
      icon: <UserOutlined />,
      label: '个人资料'
    },
    {
      key: 'settings',
      icon: <SettingOutlined />,
      label: '设置'
    },
    {
      type: 'divider' as const
    },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      onClick: onLogout
    }
  ];

  const handleMenuSelect = (key: string) => {
    setSelectedMenu(key);
    if (isMobile) {
      setMobileMenuVisible(false);
    }
    
    // 如果切换到非股票相关菜单，清除选中的股票
    if (key !== 'home' && appState.selectedStock) {
      onBackToStocks();
    }
    
    // 根据菜单项切换视图，确保所有菜单项都能正确跳转
    switch (key) {
      case 'home':
        // 首页不需要设置 currentView，MainContent 会根据 selectedMenu 显示
        break;
      case 'profile':
        onViewChange('profile');
        break;
      case 'cart':
        onViewChange('cart');
        break;
      case 'orders':
        onViewChange('orders');
        break;
      case 'recharge-history':
      case 'platform-balance':
        // 这些页面不在 ViewType 中，不需要设置 currentView
        // MainContent 会根据 selectedMenu 显示
        break;
      default:
        // 默认显示首页
        break;
    }
  };

  return (
    <Layout style={{ height: '100vh' }}>
      {/* 顶部导航栏 */}
      <Header
        appState={appState}
        onLogout={onLogout}
        onStockSelect={onStockSelect}
        onRecharge={onRecharge}
        isMobile={isMobile}
        onMobileMenuToggle={() => setMobileMenuVisible(!mobileMenuVisible)}
        onViewChange={onViewChange}
      />

      <Layout>
        {/* 桌面端侧边栏 */}
        {!isMobile && (
          <Sider
            collapsible
            collapsed={collapsed}
            onCollapse={setCollapsed}
            width={240}
            collapsedWidth={80}
            style={{
              background: '#fff',
              borderRight: '1px solid #f0f0f0'
            }}
          >
            <Sidebar
              selectedMenu={selectedMenu}
              onMenuSelect={handleMenuSelect}
              appState={appState}
            />
          </Sider>
        )}

        {/* 移动端抽屉菜单 */}
        {isMobile && (
          <Drawer
            title="菜单"
            placement="left"
            onClose={() => setMobileMenuVisible(false)}
            open={mobileMenuVisible}
            width={280}
            styles={{ body: { padding: 0 } }}
          >
            <Sidebar
              selectedMenu={selectedMenu}
              onMenuSelect={handleMenuSelect}
              appState={appState}
            />
          </Drawer>
        )}

        {/* 主内容区域 */}
        <Content style={{ 
          background: '#f5f5f5',
          padding: isMobile ? '16px' : '0',
          minHeight: 'calc(100vh - 64px)'
        }}>
          <MainContent
            selectedMenu={selectedMenu}
            appState={appState}
            onStockSelect={onStockSelect}
            onBackToStocks={onBackToStocks}
            onPostClick={onPostClick}
            onCreatePost={onCreatePost}
            onPurchase={(post: any) => onPurchase(post.id, post.price || 0)}
            onRate={(post: any, rating: number) => onRate(post.id, rating, '')}
            onLike={(post: any) => onLike(post.id)}
            onShare={(post: any) => onShare(post.id)}
            onViewChange={onViewChange}
            isMobile={isMobile}
            onProductClick={onProductClick}
            onAddToCart={onAddToCart}
            onUpdateCartQuantity={onUpdateCartQuantity}
            onRemoveFromCart={onRemoveFromCart}
            onCheckout={onCheckout}
            onOrderPay={onOrderPay}
            onOrderCancel={onOrderCancel}
            onOrderRefund={onOrderRefund}
            onBuyNow={onBuyNow}
          />
        </Content>
      </Layout>
    </Layout>
  );
};

export default TradingLayout;
