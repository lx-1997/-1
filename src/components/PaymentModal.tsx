import React, { useState, useEffect, useRef } from 'react';
import {
  Modal,
  Radio,
  Typography,
  Space,
  Button,
  Descriptions,
  message,
  QRCode,
  Spin
} from 'antd';
import {
  WechatOutlined,
  AlipayOutlined,
  DollarOutlined,
  CheckCircleOutlined
} from '@ant-design/icons';
import { Order } from '../types';
import { createPayment, pollPaymentStatus, PaymentStatus } from '../services/paymentService';

const { Title, Text } = Typography;

interface PaymentModalProps {
  visible: boolean;
  order: Order;
  onPay: (method: 'wechat' | 'alipay') => void;
  onCancel: () => void;
}

const PaymentModal: React.FC<PaymentModalProps> = ({
  visible,
  order,
  onPay,
  onCancel
}) => {
  const [selectedMethod, setSelectedMethod] = useState<'wechat' | 'alipay'>('wechat');
  const [isPaying, setIsPaying] = useState(false);
  const [qrCode, setQrCode] = useState<string>('');
  const [paymentStatus, setPaymentStatus] = useState<PaymentStatus | null>(null);
  const cancelPollRef = useRef<(() => void) | null>(null);

  // 当模态框关闭时，取消轮询
  useEffect(() => {
    if (!visible) {
      if (cancelPollRef.current) {
        cancelPollRef.current();
        cancelPollRef.current = null;
      }
      // 重置状态
      setIsPaying(false);
      setQrCode('');
      setPaymentStatus(null);
    }
  }, [visible]);

  const handlePay = async () => {
    setIsPaying(true);
    setPaymentStatus(null);
    
    try {
      // 创建支付订单
      const response = await createPayment({
        orderId: order.id,
        paymentMethod: selectedMethod,
        amount: order.totalAmount,
        description: `订单${order.id}`
      });

      if (!response.success) {
        message.error(response.message || '支付订单创建失败');
        setIsPaying(false);
        return;
      }

      // 显示二维码
      if (response.qrCode) {
        setQrCode(response.qrCode);
        message.success('请使用手机扫描二维码完成支付');

        // 开始轮询支付状态
        cancelPollRef.current = pollPaymentStatus(
          order.id,
          (status) => {
            setPaymentStatus(status);
            if (status.status === 'paid') {
              message.success('支付成功！');
              setIsPaying(false);
              if (cancelPollRef.current) {
                cancelPollRef.current();
                cancelPollRef.current = null;
              }
              // 延迟关闭，让用户看到成功提示
              setTimeout(() => {
                onPay(selectedMethod);
              }, 1500);
            } else if (status.status === 'failed') {
              message.error('支付失败，请重试');
              setIsPaying(false);
              if (cancelPollRef.current) {
                cancelPollRef.current();
                cancelPollRef.current = null;
              }
            }
          },
          60, // 最多轮询60次
          2000 // 每2秒轮询一次
        );
      } else {
        // 如果没有二维码，可能是H5支付，直接跳转
        if (response.paymentUrl) {
          window.location.href = response.paymentUrl;
        } else {
          message.warning('支付方式暂不支持，请联系客服');
          setIsPaying(false);
        }
      }
    } catch (error) {
      console.error('支付失败:', error);
      message.error('支付失败，请重试');
      setIsPaying(false);
    }
  };

  return (
    <Modal
      title="选择支付方式"
      open={visible}
      onCancel={onCancel}
      footer={null}
      width={600}
    >
      <div style={{ padding: '24px 0' }}>
        {/* 订单信息 */}
        <Descriptions bordered column={1} style={{ marginBottom: '24px' }}>
          <Descriptions.Item label="订单号">{order.id}</Descriptions.Item>
          <Descriptions.Item label="订单金额">
            <Text strong style={{ color: '#ff4d4f', fontSize: '20px' }}>
              ¥{order.totalAmount.toFixed(2)}
            </Text>
          </Descriptions.Item>
        </Descriptions>

        {/* 支付方式选择 */}
        <div style={{ marginBottom: '24px' }}>
          <Title level={5} style={{ marginBottom: '16px' }}>选择支付方式</Title>
          <Radio.Group
            value={selectedMethod}
            onChange={(e) => setSelectedMethod(e.target.value)}
            style={{ width: '100%' }}
          >
            <Space direction="vertical" style={{ width: '100%' }}>
              <Radio.Button
                value="wechat"
                style={{
                  width: '100%',
                  height: '60px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center'
                }}
              >
                <Space size="large">
                  <WechatOutlined style={{ fontSize: '24px', color: '#07c160' }} />
                  <Text strong style={{ fontSize: '16px' }}>微信支付</Text>
                </Space>
              </Radio.Button>
              <Radio.Button
                value="alipay"
                style={{
                  width: '100%',
                  height: '60px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center'
                }}
              >
                <Space size="large">
                  <AlipayOutlined style={{ fontSize: '24px', color: '#1677ff' }} />
                  <Text strong style={{ fontSize: '16px' }}>支付宝</Text>
                </Space>
              </Radio.Button>
            </Space>
          </Radio.Group>
        </div>

        {/* 支付二维码预览 */}
        {isPaying && qrCode && (
          <div style={{ textAlign: 'center', marginBottom: '24px', padding: '24px', background: '#f5f5f5', borderRadius: '8px' }}>
            {paymentStatus?.status === 'paid' ? (
              <div>
                <CheckCircleOutlined style={{ fontSize: '64px', color: '#52c41a', marginBottom: '16px' }} />
                <Text strong style={{ display: 'block', fontSize: '18px', color: '#52c41a' }}>
                  支付成功！
                </Text>
              </div>
            ) : (
              <>
                <Text type="secondary" style={{ display: 'block', marginBottom: '16px' }}>
                  请使用{selectedMethod === 'wechat' ? '微信' : '支付宝'}扫描二维码完成支付
                </Text>
                <div style={{ display: 'inline-block', padding: '16px', background: '#fff', borderRadius: '8px' }}>
                  <QRCode
                    value={qrCode}
                    size={200}
                    errorLevel="M"
                  />
                </div>
                <Text type="secondary" style={{ display: 'block', marginTop: '16px', fontSize: '12px' }}>
                  二维码有效期15分钟
                </Text>
                {paymentStatus?.status === 'pending' && (
                  <div style={{ marginTop: '16px' }}>
                    <Spin size="small" />
                    <Text type="secondary" style={{ marginLeft: '8px' }}>
                      等待支付中...
                    </Text>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* 操作按钮 */}
        <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
          <Button 
            onClick={() => {
              if (cancelPollRef.current) {
                cancelPollRef.current();
                cancelPollRef.current = null;
              }
              onCancel();
            }}
            disabled={paymentStatus?.status === 'paid'}
          >
            {paymentStatus?.status === 'paid' ? '已完成' : '取消'}
          </Button>
          {!paymentStatus || paymentStatus.status !== 'paid' ? (
            <Button
              type="primary"
              icon={selectedMethod === 'wechat' ? <WechatOutlined /> : <AlipayOutlined />}
              onClick={handlePay}
              loading={isPaying && !qrCode}
              disabled={isPaying && !!qrCode}
              size="large"
            >
              {isPaying && qrCode 
                ? '等待支付...' 
                : `使用${selectedMethod === 'wechat' ? '微信' : '支付宝'}支付`}
            </Button>
          ) : null}
        </Space>

        {/* 支付说明 */}
        <div style={{ marginTop: '24px', padding: '16px', background: '#fff7e6', borderRadius: '4px' }}>
          <Text type="secondary" style={{ fontSize: '12px' }}>
            <DollarOutlined /> 支付说明：<br />
            • 支付成功后，订单将自动更新为已支付状态<br />
            • 如遇支付问题，请联系客服<br />
            • 支持退款，退款将在1-3个工作日内到账
          </Text>
        </div>
      </div>
    </Modal>
  );
};

export default PaymentModal;


