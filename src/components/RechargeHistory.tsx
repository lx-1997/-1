import React from 'react';
import { Card, Table, Tag, Typography, Space, Statistic, Row, Col } from 'antd';
import { DollarOutlined, HistoryOutlined, CheckCircleOutlined, ClockCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import { RechargeRecord } from '../types';

const { Title, Text } = Typography;

interface RechargeHistoryProps {
  rechargeHistory: RechargeRecord[];
  platformBalance: number;
}

const RechargeHistory: React.FC<RechargeHistoryProps> = ({
  rechargeHistory,
  platformBalance
}) => {
  const columns = [
    {
      title: '交易时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      render: (date: string) => new Date(date).toLocaleString('zh-CN'),
      sorter: (a: RechargeRecord, b: RechargeRecord) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime(),
    },
    {
      title: '充值金额',
      dataIndex: 'amount',
      key: 'amount',
      render: (amount: number) => (
        <Text strong style={{ color: '#52c41a' }}>
          ${amount.toFixed(2)}
        </Text>
      ),
      sorter: (a: RechargeRecord, b: RechargeRecord) => a.amount - b.amount,
    },
    {
      title: '支付方式',
      dataIndex: 'method',
      key: 'method',
      render: (method: string) => {
        const methodMap: { [key: string]: { text: string; color: string } } = {
          alipay: { text: '支付宝', color: '#1677ff' },
          wechatpay: { text: '微信支付', color: '#27d100' },
          bankcard: { text: '银行卡', color: '#faad14' }
        };
        const methodInfo = methodMap[method] || { text: method, color: '#666' };
        return <Tag color={methodInfo.color}>{methodInfo.text}</Tag>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => {
        const statusMap: { [key: string]: { text: string; color: string; icon: React.ReactNode } } = {
          success: { text: '成功', color: 'success', icon: <CheckCircleOutlined /> },
          pending: { text: '处理中', color: 'processing', icon: <ClockCircleOutlined /> },
          failed: { text: '失败', color: 'error', icon: <CloseCircleOutlined /> }
        };
        const statusInfo = statusMap[status] || { text: status, color: 'default', icon: null };
        return (
          <Tag color={statusInfo.color} icon={statusInfo.icon}>
            {statusInfo.text}
          </Tag>
        );
      },
    },
    {
      title: '交易ID',
      dataIndex: 'transactionId',
      key: 'transactionId',
      render: (id: string) => (
        <Text code style={{ fontSize: '12px' }}>
          {id}
        </Text>
      ),
    },
  ];

  const totalRecharged = rechargeHistory
    .filter(record => record.status === 'success')
    .reduce((sum, record) => sum + record.amount, 0);

  const successCount = rechargeHistory.filter(record => record.status === 'success').length;
  const pendingCount = rechargeHistory.filter(record => record.status === 'pending').length;

  return (
    <div>
      {/* 统计信息 */}
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="平台总余额"
              value={platformBalance}
              prefix={<DollarOutlined />}
              valueStyle={{ color: '#52c41a' }}
              suffix="USD"
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="累计充值"
              value={totalRecharged}
              prefix={<DollarOutlined />}
              valueStyle={{ color: '#1890ff' }}
              suffix="USD"
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="成功充值"
              value={successCount}
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#52c41a' }}
              suffix="笔"
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="处理中"
              value={pendingCount}
              prefix={<ClockCircleOutlined />}
              valueStyle={{ color: '#faad14' }}
              suffix="笔"
            />
          </Card>
        </Col>
      </Row>

      {/* 充值记录表格 */}
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <HistoryOutlined />
          <Title level={4} style={{ margin: 0 }}>充值记录</Title>
        </Space>
        
        <Table
          columns={columns}
          dataSource={rechargeHistory}
          rowKey="id"
          pagination={{
            pageSize: 10,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条记录`,
          }}
          scroll={{ x: 800 }}
        />
      </Card>
    </div>
  );
};

export default RechargeHistory;




