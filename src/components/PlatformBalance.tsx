import React from 'react';
import { Card, Row, Col, Statistic, Typography, Progress, Divider } from 'antd';
import { 
  DollarOutlined, 
  UserOutlined, 
  ShoppingCartOutlined, 
  BankOutlined,
  RiseOutlined,
  WalletOutlined
} from '@ant-design/icons';

const { Title, Text } = Typography;

interface PlatformBalanceProps {
  platformBalance: number;
  totalRecharged: number;
  totalSpent: number;
  activeUsers: number;
  totalPosts: number;
  paidPosts: number;
}

const PlatformBalance: React.FC<PlatformBalanceProps> = ({
  platformBalance,
  totalRecharged,
  totalSpent,
  activeUsers,
  totalPosts,
  paidPosts
}) => {
  const profit = totalRecharged - totalSpent;
  const profitMargin = totalRecharged > 0 ? (profit / totalRecharged) * 100 : 0;
  const paidPostRatio = totalPosts > 0 ? (paidPosts / totalPosts) * 100 : 0;

  return (
    <div>
      <Title level={3}>
        <BankOutlined style={{ marginRight: 8 }} />
        平台余额管理
      </Title>
      
      {/* 主要财务指标 */}
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="平台总余额"
              value={platformBalance}
              prefix={<DollarOutlined />}
              valueStyle={{ color: '#52c41a', fontSize: '24px' }}
              suffix="USD"
            />
            <Text type="secondary">当前可用资金</Text>
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="累计充值"
              value={totalRecharged}
              prefix={<RiseOutlined />}
              valueStyle={{ color: '#1890ff', fontSize: '24px' }}
              suffix="USD"
            />
            <Text type="secondary">用户充值总额</Text>
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="累计支出"
              value={totalSpent}
              prefix={<ShoppingCartOutlined />}
              valueStyle={{ color: '#faad14', fontSize: '24px' }}
              suffix="USD"
            />
            <Text type="secondary">用户消费总额</Text>
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="平台利润"
              value={profit}
              prefix={<WalletOutlined />}
              valueStyle={{ 
                color: profit >= 0 ? '#52c41a' : '#ff4d4f', 
                fontSize: '24px' 
              }}
              suffix="USD"
            />
            <Text type="secondary">净利润</Text>
          </Card>
        </Col>
      </Row>

      {/* 业务指标 */}
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}>
          <Card>
            <Statistic
              title="活跃用户"
              value={activeUsers}
              prefix={<UserOutlined />}
              valueStyle={{ color: '#722ed1', fontSize: '20px' }}
              suffix="人"
            />
            <Progress 
              percent={Math.min((activeUsers / 1000) * 100, 100)} 
              size="small" 
              status="active"
              style={{ marginTop: 8 }}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="付费内容"
              value={paidPosts}
              prefix={<DollarOutlined />}
              valueStyle={{ color: '#fa8c16', fontSize: '20px' }}
              suffix={`/ ${totalPosts}`}
            />
            <Progress 
              percent={paidPostRatio} 
              size="small" 
              status="active"
              style={{ marginTop: 8 }}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="利润率"
              value={profitMargin}
              prefix={<RiseOutlined />}
              valueStyle={{ 
                color: profitMargin >= 0 ? '#52c41a' : '#ff4d4f', 
                fontSize: '20px' 
              }}
              suffix="%"
            />
            <Progress 
              percent={Math.abs(profitMargin)} 
              size="small" 
              status={profitMargin >= 0 ? 'success' : 'exception'}
              style={{ marginTop: 8 }}
            />
          </Card>
        </Col>
      </Row>

      {/* 资金流向分析 */}
      <Card title="资金流向分析">
        <Row gutter={16}>
          <Col span={12}>
            <div style={{ textAlign: 'center' }}>
              <Title level={4}>收入来源</Title>
              <div style={{ margin: '16px 0' }}>
                <Text strong>用户充值: </Text>
                <Text style={{ color: '#52c41a' }}>${totalRecharged.toFixed(2)}</Text>
              </div>
              <Progress 
                percent={100} 
                strokeColor="#52c41a"
                showInfo={false}
              />
            </div>
          </Col>
          <Col span={12}>
            <div style={{ textAlign: 'center' }}>
              <Title level={4}>支出用途</Title>
              <div style={{ margin: '16px 0' }}>
                <Text strong>用户消费: </Text>
                <Text style={{ color: '#faad14' }}>${totalSpent.toFixed(2)}</Text>
              </div>
              <Progress 
                percent={totalRecharged > 0 ? (totalSpent / totalRecharged) * 100 : 0} 
                strokeColor="#faad14"
                showInfo={false}
              />
            </div>
          </Col>
        </Row>
        
        <Divider />
        
        <div style={{ textAlign: 'center' }}>
          <Title level={4}>净资金流</Title>
          <div style={{ margin: '16px 0' }}>
            <Text strong>平台留存: </Text>
            <Text style={{ 
              color: profit >= 0 ? '#52c41a' : '#ff4d4f',
              fontSize: '18px',
              fontWeight: 'bold'
            }}>
              ${profit.toFixed(2)}
            </Text>
          </div>
          <Progress 
            percent={Math.abs(profitMargin)} 
            strokeColor={profit >= 0 ? '#52c41a' : '#ff4d4f'}
            status={profit >= 0 ? 'success' : 'exception'}
          />
        </div>
      </Card>
    </div>
  );
};

export default PlatformBalance;
