import React, { useState } from 'react';
import { Modal, Button, InputNumber, Space, Typography, message, Divider } from 'antd';
import { DollarOutlined, CreditCardOutlined, AlipayOutlined, WechatOutlined } from '@ant-design/icons';

const { Title, Text, Paragraph } = Typography;

interface RechargeModalProps {
  visible: boolean;
  onCancel: () => void;
  onRecharge: (amount: number, method: string) => void;
  currentBalance: number;
}

const RechargeModal: React.FC<RechargeModalProps> = ({
  visible,
  onCancel,
  onRecharge,
  currentBalance
}) => {
  const [amount, setAmount] = useState<number>(10);
  const [selectedMethod, setSelectedMethod] = useState<string>('alipay');

  const presetAmounts = [10, 20, 50, 100, 200, 500];

  const paymentMethods = [
    {
      key: 'alipay',
      name: '支付宝',
      icon: <AlipayOutlined style={{ color: '#1677ff' }} />,
      color: '#1677ff'
    },
    {
      key: 'wechat',
      name: '微信支付',
      icon: <WechatOutlined style={{ color: '#07c160' }} />,
      color: '#07c160'
    },
    {
      key: 'card',
      name: '银行卡',
      icon: <CreditCardOutlined style={{ color: '#722ed1' }} />,
      color: '#722ed1'
    }
  ];

  const handleRecharge = () => {
    if (amount <= 0) {
      message.error('充值金额必须大于0');
      return;
    }
    onRecharge(amount, selectedMethod);
  };

  return (
    <Modal
      title={
        <Space>
          <DollarOutlined style={{ color: '#52c41a' }} />
          <span>账户充值</span>
        </Space>
      }
      open={visible}
      onCancel={onCancel}
      width={500}
      footer={[
        <Button key="cancel" onClick={onCancel}>
          取消
        </Button>,
        <Button key="recharge" type="primary" onClick={handleRecharge}>
          确认充值 ${amount}
        </Button>
      ]}
    >
      <div style={{ padding: '16px 0' }}>
        {/* 当前余额 */}
        <div style={{ textAlign: 'center', marginBottom: '24px' }}>
          <Text type="secondary">当前余额</Text>
          <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#52c41a', marginTop: '8px' }}>
            ${currentBalance.toFixed(2)}
          </div>
        </div>

        <Divider />

        {/* 充值金额 */}
        <div style={{ marginBottom: '24px' }}>
          <Title level={5}>选择充值金额</Title>
          <Space wrap style={{ marginBottom: '16px' }}>
            {presetAmounts.map(preset => (
              <Button
                key={preset}
                type={amount === preset ? 'primary' : 'default'}
                onClick={() => setAmount(preset)}
                style={{ minWidth: '60px' }}
              >
                ${preset}
              </Button>
            ))}
          </Space>
          <div>
            <Text>自定义金额：</Text>
            <InputNumber
              value={amount}
              onChange={(value) => setAmount(value || 0)}
              min={1}
              max={10000}
              precision={2}
              prefix="$"
              style={{ width: '120px', marginLeft: '8px' }}
            />
          </div>
        </div>

        <Divider />

        {/* 支付方式 */}
        <div>
          <Title level={5}>选择支付方式</Title>
          <Space direction="vertical" style={{ width: '100%' }}>
            {paymentMethods.map(method => (
              <Button
                key={method.key}
                type={selectedMethod === method.key ? 'primary' : 'default'}
                onClick={() => setSelectedMethod(method.key)}
                style={{
                  width: '100%',
                  height: '48px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'flex-start',
                  borderColor: selectedMethod === method.key ? method.color : undefined
                }}
              >
                <Space>
                  {method.icon}
                  <Text strong>{method.name}</Text>
                </Space>
              </Button>
            ))}
          </Space>
        </div>

        <Divider />

        {/* 充值说明 */}
        <div style={{ background: '#f6ffed', padding: '12px', borderRadius: '6px' }}>
          <Text type="secondary" style={{ fontSize: '12px' }}>
            • 充值金额将立即到账<br/>
            • 余额可用于购买付费内容<br/>
            • 充值记录可在个人中心查看<br/>
            • 如有问题请联系客服
          </Text>
        </div>
      </div>
    </Modal>
  );
};

export default RechargeModal;




