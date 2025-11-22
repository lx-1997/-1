import React, { useState } from 'react';
import {
  Card,
  Table,
  Button,
  InputNumber,
  Typography,
  Space,
  Checkbox,
  Empty,
  message,
  Row,
  Col,
  Divider
} from 'antd';
import {
  DeleteOutlined,
  ShoppingCartOutlined,
  ArrowLeftOutlined
} from '@ant-design/icons';
import { CartItem } from '../types';
import { formatPrice, formatTotalPrice, calculateCartItemCount } from '../utils/cartUtils';

const { Title, Text } = Typography;

interface CartProps {
  cartItems: CartItem[];
  onUpdateQuantity: (itemId: string, quantity: number) => void;
  onRemoveItem: (itemId: string) => void;
  onCheckout: (selectedItems: CartItem[]) => void;
  onBack: () => void;
}

const Cart: React.FC<CartProps> = ({
  cartItems,
  onUpdateQuantity,
  onRemoveItem,
  onCheckout
}) => {
  const [selectedItems, setSelectedItems] = useState<string[]>(
    cartItems.map(item => item.id)
  );

  // 计算总价
  const totalAmount = cartItems
    .filter(item => selectedItems.includes(item.id))
    .reduce((sum, item) => sum + item.variant.price * item.quantity, 0);

  const handleSelectAll = (checked: boolean) => {
    setSelectedItems(checked ? cartItems.map(item => item.id) : []);
  };

  const handleSelectItem = (itemId: string, checked: boolean) => {
    if (checked) {
      setSelectedItems([...selectedItems, itemId]);
    } else {
      setSelectedItems(selectedItems.filter(id => id !== itemId));
    }
  };

  const handleCheckout = () => {
    if (selectedItems.length === 0) {
      message.warning('请选择要结算的商品');
      return;
    }
    const itemsToCheckout = cartItems.filter(item => selectedItems.includes(item.id));
    onCheckout(itemsToCheckout);
  };

  const columns = [
    {
      title: '商品信息',
      key: 'product',
      width: '40%',
      render: (_: any, record: CartItem) => (
        <Space>
          <img
            src={record.product.images[0] || 'https://via.placeholder.com/80'}
            alt={record.product.name}
            style={{
              width: '80px',
              height: '80px',
              objectFit: 'cover',
              borderRadius: '4px'
            }}
          />
          <div>
            <Text strong style={{ display: 'block', marginBottom: '4px' }}>
              {record.product.name}
            </Text>
            <Text type="secondary" style={{ fontSize: '12px' }}>
              {record.variant.name}
            </Text>
          </div>
        </Space>
      )
    },
    {
      title: '单价',
      key: 'price',
      width: '15%',
      render: (_: any, record: CartItem) => (
        <Text>{formatPrice(record.variant.price)}</Text>
      )
    },
    {
      title: '数量',
      key: 'quantity',
      width: '20%',
      render: (_: any, record: CartItem) => (
        <InputNumber
          min={1}
          max={record.variant.stock}
          value={record.quantity}
          onChange={(val) => {
            if (val && val > 0) {
              onUpdateQuantity(record.id, val);
            }
          }}
        />
      )
    },
    {
      title: '小计',
      key: 'subtotal',
      width: '15%',
      render: (_: any, record: CartItem) => (
        <Text strong style={{ color: '#ff4d4f' }}>
          {formatPrice(record.variant.price * record.quantity)}
        </Text>
      )
    },
    {
      title: '操作',
      key: 'action',
      width: '10%',
      render: (_: any, record: CartItem) => (
        <Button
          type="link"
          danger
          icon={<DeleteOutlined />}
          onClick={() => {
            onRemoveItem(record.id);
            setSelectedItems(selectedItems.filter(id => id !== record.id));
          }}
        >
          删除
        </Button>
      )
    }
  ];

  if (cartItems.length === 0) {
    return (
      <div style={{ padding: '24px', background: '#f5f5f5', minHeight: '100vh' }}>
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => window.history.back()}
          style={{ marginBottom: '16px' }}
        >
          返回
        </Button>
        <Card>
          <Empty
            description="购物车是空的"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          >
            <Button type="primary" href="#/shop">
              去购物
            </Button>
          </Empty>
        </Card>
      </div>
    );
  }

  return (
    <div style={{ padding: '24px', background: '#f5f5f5', minHeight: '100vh' }}>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() => window.history.back()}
        style={{ marginBottom: '16px' }}
      >
        返回
      </Button>

      <Card>
        <Title level={3} style={{ marginBottom: '24px' }}>
          <ShoppingCartOutlined /> 购物车 ({calculateCartItemCount(cartItems)}件商品)
        </Title>

        <div style={{ marginBottom: '16px' }}>
          <Checkbox
            checked={selectedItems.length === cartItems.length && cartItems.length > 0}
            indeterminate={selectedItems.length > 0 && selectedItems.length < cartItems.length}
            onChange={(e) => handleSelectAll(e.target.checked)}
          >
            全选
          </Checkbox>
        </div>

        <Table
          columns={columns}
          dataSource={cartItems}
          rowKey="id"
          pagination={false}
          rowSelection={{
            selectedRowKeys: selectedItems,
            onSelect: (record, selected) => {
              handleSelectItem(record.id, selected || false);
            },
            onSelectAll: (selected) => {
              handleSelectAll(selected || false);
            }
          }}
          summary={() => (
            <Table.Summary fixed>
              <Table.Summary.Row>
                <Table.Summary.Cell index={0} colSpan={4}>
                  <div style={{ textAlign: 'right', paddingRight: '16px' }}>
                    <Text>已选择 {selectedItems.length} 件商品，合计：</Text>
                    <Text strong style={{ fontSize: '20px', color: '#ff4d4f', marginLeft: '8px' }}>
                      {formatTotalPrice(totalAmount)}
                    </Text>
                  </div>
                </Table.Summary.Cell>
                <Table.Summary.Cell index={1}>
                  <Button
                    type="primary"
                    size="large"
                    danger
                    onClick={handleCheckout}
                    disabled={selectedItems.length === 0}
                    block
                  >
                    结算 ({selectedItems.length})
                  </Button>
                </Table.Summary.Cell>
              </Table.Summary.Row>
            </Table.Summary>
          )}
        />
      </Card>

      {/* 底部结算栏（移动端） */}
      <div
        style={{
          position: 'sticky',
          bottom: 0,
          background: '#fff',
          padding: '16px',
          borderTop: '1px solid #f0f0f0',
          boxShadow: '0 -2px 8px rgba(0,0,0,0.1)',
          display: 'none' // 可以通过媒体查询在移动端显示
        }}
      >
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <div>
              <Text type="secondary">合计：</Text>
              <Text strong style={{ fontSize: '18px', color: '#ff4d4f', marginLeft: '8px' }}>
                {formatTotalPrice(totalAmount)}
              </Text>
            </div>
          </Col>
          <Col>
            <Button
              type="primary"
              size="large"
              danger
              onClick={handleCheckout}
              disabled={selectedItems.length === 0}
            >
              结算 ({selectedItems.length})
            </Button>
          </Col>
        </Row>
      </div>
    </div>
  );
};

export default Cart;


