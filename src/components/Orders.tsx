import React, { useState } from 'react';
import {
  Card,
  Table,
  Tag,
  Button,
  Typography,
  Space,
  Image,
  Modal,
  Descriptions,
  Empty,
  Tabs
} from 'antd';
import {
  EyeOutlined,
  ReloadOutlined
} from '@ant-design/icons';
import { Order } from '../types';
import PaymentModal from './PaymentModal';

const { Title, Text } = Typography;
const { TabPane } = Tabs;

interface OrdersProps {
  orders: Order[];
  onPay: (orderId: string, paymentMethod: 'wechat' | 'alipay') => void;
  onCancel: (orderId: string) => void;
  onRefund: (orderId: string) => void;
}

const Orders: React.FC<OrdersProps> = ({
  orders,
  onPay,
  onCancel,
  onRefund
}) => {
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [paymentModalVisible, setPaymentModalVisible] = useState(false);
  const [activeTab, setActiveTab] = useState<string>('all');

  const getPaymentStatusTag = (status: Order['paymentStatus']) => {
    const statusMap = {
      pending: { color: 'orange', text: '待支付' },
      paid: { color: 'green', text: '已支付' },
      failed: { color: 'red', text: '支付失败' },
      refunded: { color: 'default', text: '已退款' }
    };
    const config = statusMap[status];
    return <Tag color={config.color}>{config.text}</Tag>;
  };

  const getOrderStatusTag = (status: Order['orderStatus']) => {
    const statusMap = {
      pending: { color: 'orange', text: '待处理' },
      processing: { color: 'blue', text: '处理中' },
      shipped: { color: 'cyan', text: '已发货' },
      delivered: { color: 'green', text: '已送达' },
      cancelled: { color: 'default', text: '已取消' }
    };
    const config = statusMap[status];
    return <Tag color={config.color}>{config.text}</Tag>;
  };

  const getPaymentMethodText = (method: Order['paymentMethod']) => {
    const methodMap = {
      wechat: '微信支付',
      alipay: '支付宝',
      balance: '余额支付'
    };
    return methodMap[method];
  };

  const filteredOrders = orders.filter(order => {
    if (activeTab === 'all') return true;
    if (activeTab === 'pending') return order.paymentStatus === 'pending';
    if (activeTab === 'paid') return order.paymentStatus === 'paid';
    if (activeTab === 'delivered') return order.orderStatus === 'delivered';
    return true;
  });

  const columns = [
    {
      title: '订单号',
      dataIndex: 'id',
      key: 'id',
      width: '15%',
      render: (id: string) => <Text copyable={{ text: id }}>{id.slice(0, 8)}...</Text>
    },
    {
      title: '商品',
      key: 'items',
      width: '30%',
      render: (_: any, record: Order) => (
        <Space>
          {record.items[0]?.image && (
            <Image
              src={record.items[0].image}
              alt={record.items[0].productName}
              width={60}
              height={60}
              style={{ objectFit: 'cover', borderRadius: '4px' }}
            />
          )}
          <div>
            <Text strong>{record.items[0]?.productName}</Text>
            {record.items.length > 1 && (
              <Text type="secondary" style={{ display: 'block', fontSize: '12px' }}>
                等{record.items.length}件商品
              </Text>
            )}
          </div>
        </Space>
      )
    },
    {
      title: '金额',
      key: 'amount',
      width: '10%',
      render: (_: any, record: Order) => (
        <Text strong style={{ color: '#ff4d4f' }}>
          ¥{record.totalAmount.toFixed(2)}
        </Text>
      )
    },
    {
      title: '支付方式',
      key: 'paymentMethod',
      width: '10%',
      render: (_: any, record: Order) => (
        <Text>{getPaymentMethodText(record.paymentMethod)}</Text>
      )
    },
    {
      title: '支付状态',
      key: 'paymentStatus',
      width: '10%',
      render: (_: any, record: Order) => getPaymentStatusTag(record.paymentStatus)
    },
    {
      title: '订单状态',
      key: 'orderStatus',
      width: '10%',
      render: (_: any, record: Order) => getOrderStatusTag(record.orderStatus)
    },
    {
      title: '下单时间',
      key: 'createdAt',
      width: '15%',
      render: (_: any, record: Order) => (
        <Text type="secondary">
          {new Date(record.createdAt).toLocaleString()}
        </Text>
      )
    },
    {
      title: '操作',
      key: 'action',
      width: '15%',
      render: (_: any, record: Order) => (
        <Space>
          <Button
            type="link"
            icon={<EyeOutlined />}
            onClick={() => {
              setSelectedOrder(record);
              setDetailModalVisible(true);
            }}
          >
            详情
          </Button>
          {record.paymentStatus === 'pending' && (
            <>
              <Button
                type="link"
                onClick={() => {
                  setSelectedOrder(record);
                  setPaymentModalVisible(true);
                }}
              >
                支付
              </Button>
              <Button
                type="link"
                danger
                onClick={() => onCancel(record.id)}
              >
                取消
              </Button>
            </>
          )}
          {record.paymentStatus === 'paid' && record.orderStatus !== 'delivered' && (
            <Button
              type="link"
              onClick={() => onRefund(record.id)}
            >
              申请退款
            </Button>
          )}
        </Space>
      )
    }
  ];

  return (
    <div style={{ padding: '24px', background: '#f5f5f5', minHeight: '100vh' }}>
      <Title level={3} style={{ marginBottom: '24px' }}>我的订单</Title>

      <Card>
        <Tabs activeKey={activeTab} onChange={setActiveTab}>
          <TabPane tab={`全部订单 (${orders.length})`} key="all" />
          <TabPane tab={`待支付 (${orders.filter(o => o.paymentStatus === 'pending').length})`} key="pending" />
          <TabPane tab={`已支付 (${orders.filter(o => o.paymentStatus === 'paid').length})`} key="paid" />
          <TabPane tab={`已完成 (${orders.filter(o => o.orderStatus === 'delivered').length})`} key="delivered" />
        </Tabs>

        {filteredOrders.length === 0 ? (
          <Empty description="暂无订单" />
        ) : (
          <Table
            columns={columns}
            dataSource={filteredOrders}
            rowKey="id"
            pagination={{
              pageSize: 10,
              showTotal: (total) => `共 ${total} 条订单`
            }}
          />
        )}
      </Card>

      {/* 订单详情模态框 */}
      <Modal
        title="订单详情"
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        footer={null}
        width={800}
      >
        {selectedOrder && (
          <div>
            <Descriptions bordered column={2}>
              <Descriptions.Item label="订单号">{selectedOrder.id}</Descriptions.Item>
              <Descriptions.Item label="下单时间">
                {new Date(selectedOrder.createdAt).toLocaleString()}
              </Descriptions.Item>
              <Descriptions.Item label="支付方式">
                {getPaymentMethodText(selectedOrder.paymentMethod)}
              </Descriptions.Item>
              <Descriptions.Item label="支付状态">
                {getPaymentStatusTag(selectedOrder.paymentStatus)}
              </Descriptions.Item>
              <Descriptions.Item label="订单状态">
                {getOrderStatusTag(selectedOrder.orderStatus)}
              </Descriptions.Item>
              <Descriptions.Item label="订单金额">
                <Text strong style={{ color: '#ff4d4f', fontSize: '16px' }}>
                  ¥{selectedOrder.totalAmount.toFixed(2)}
                </Text>
              </Descriptions.Item>
              {selectedOrder.transactionId && (
                <Descriptions.Item label="交易号" span={2}>
                  {selectedOrder.transactionId}
                </Descriptions.Item>
              )}
            </Descriptions>

            <Title level={5} style={{ marginTop: '24px', marginBottom: '16px' }}>
              商品列表
            </Title>
            <Table
              columns={[
                {
                  title: '商品',
                  key: 'product',
                  render: (_: any, item: any) => (
                    <Space>
                      {item.image && (
                        <Image src={item.image} width={50} height={50} />
                      )}
                      <div>
                        <Text strong>{item.productName}</Text>
                        <br />
                        <Text type="secondary" style={{ fontSize: '12px' }}>
                          {item.variantName}
                        </Text>
                      </div>
                    </Space>
                  )
                },
                {
                  title: '单价',
                  dataIndex: 'price',
                  render: (price: number) => `¥${price.toFixed(2)}`
                },
                {
                  title: '数量',
                  dataIndex: 'quantity'
                },
                {
                  title: '小计',
                  render: (_: any, item: any) => (
                    <Text strong>¥{(item.price * item.quantity).toFixed(2)}</Text>
                  )
                }
              ]}
              dataSource={selectedOrder.items}
              rowKey="id"
              pagination={false}
            />

            {selectedOrder.shippingAddress && (
              <>
                <Title level={5} style={{ marginTop: '24px', marginBottom: '16px' }}>
                  收货地址
                </Title>
                <Descriptions bordered>
                  <Descriptions.Item label="收货人">{selectedOrder.shippingAddress.name}</Descriptions.Item>
                  <Descriptions.Item label="联系电话">{selectedOrder.shippingAddress.phone}</Descriptions.Item>
                  <Descriptions.Item label="收货地址" span={2}>
                    {selectedOrder.shippingAddress.province} {selectedOrder.shippingAddress.city}{' '}
                    {selectedOrder.shippingAddress.district} {selectedOrder.shippingAddress.address}
                  </Descriptions.Item>
                </Descriptions>
              </>
            )}
          </div>
        )}
      </Modal>

      {/* 支付模态框 */}
      {selectedOrder && (
        <PaymentModal
          visible={paymentModalVisible}
          order={selectedOrder}
          onPay={(method) => {
            onPay(selectedOrder.id, method);
            setPaymentModalVisible(false);
          }}
          onCancel={() => setPaymentModalVisible(false)}
        />
      )}
    </div>
  );
};

export default Orders;




