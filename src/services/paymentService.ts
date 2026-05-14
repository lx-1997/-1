/**
 * 支付服务
 * 提供微信支付和支付宝支付的API接口
 */

export interface PaymentRequest {
  orderId: string;
  paymentMethod: 'wechat' | 'alipay';
  amount: number;
  description?: string;
}

export interface PaymentResponse {
  success: boolean;
  qrCode?: string; // 支付二维码URL
  paymentUrl?: string; // 支付跳转URL（H5支付）
  transactionId?: string;
  message?: string;
}

export interface PaymentStatus {
  status: 'pending' | 'paid' | 'failed' | 'cancelled';
  transactionId?: string;
  paidAt?: string;
}

/**
 * 创建支付订单
 * @param request 支付请求
 * @returns 支付响应
 */
export async function createPayment(request: PaymentRequest): Promise<PaymentResponse> {
  // 实际项目中，这里应该调用后端API
  // const response = await fetch('/api/payment/create', {
  //   method: 'POST',
  //   headers: { 'Content-Type': 'application/json' },
  //   body: JSON.stringify(request)
  // });
  // return await response.json();

  // 模拟API调用
  return new Promise((resolve) => {
    setTimeout(() => {
      const qrCode = request.paymentMethod === 'wechat'
        ? `weixin://wxpay/bizpayurl?pr=${request.orderId}`
        : `https://qr.alipay.com/${request.orderId}`;
      
      resolve({
        success: true,
        qrCode,
        transactionId: `${request.paymentMethod.toUpperCase()}${Date.now()}`,
        message: '支付订单创建成功'
      });
    }, 1000);
  });
}

/**
 * 查询支付状态
 * @param orderId 订单ID
 * @returns 支付状态
 */
export async function checkPaymentStatus(orderId: string): Promise<PaymentStatus> {
  // 实际项目中，这里应该调用后端API
  // const response = await fetch(`/api/payment/status/${orderId}`);
  // return await response.json();

  // 模拟API调用
  return new Promise((resolve) => {
    setTimeout(() => {
      // 模拟支付成功（实际应该查询真实状态）
      resolve({
        status: 'paid',
        transactionId: `TXN${Date.now()}`,
        paidAt: new Date().toISOString()
      });
    }, 500);
  });
}

/**
 * 取消支付
 * @param orderId 订单ID
 * @returns 是否成功
 */
export async function cancelPayment(orderId: string): Promise<boolean> {
  // 实际项目中，这里应该调用后端API
  // const response = await fetch(`/api/payment/cancel/${orderId}`, {
  //   method: 'POST'
  // });
  // return (await response.json()).success;

  // 模拟API调用
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(true);
    }, 500);
  });
}

/**
 * 申请退款
 * @param orderId 订单ID
 * @param reason 退款原因
 * @returns 是否成功
 */
export async function requestRefund(orderId: string, reason?: string): Promise<boolean> {
  // 实际项目中，这里应该调用后端API
  // const response = await fetch(`/api/payment/refund`, {
  //   method: 'POST',
  //   headers: { 'Content-Type': 'application/json' },
  //   body: JSON.stringify({ orderId, reason })
  // });
  // return (await response.json()).success;

  // 模拟API调用
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(true);
    }, 1000);
  });
}

/**
 * 轮询支付状态
 * @param orderId 订单ID
 * @param onStatusChange 状态变化回调
 * @param maxAttempts 最大尝试次数
 * @param interval 轮询间隔（毫秒）
 */
export function pollPaymentStatus(
  orderId: string,
  onStatusChange: (status: PaymentStatus) => void,
  maxAttempts: number = 60,
  interval: number = 2000
): () => void {
  let attempts = 0;
  let cancelled = false;

  const poll = async () => {
    if (cancelled || attempts >= maxAttempts) {
      return;
    }

    attempts++;
    try {
      const status = await checkPaymentStatus(orderId);
      onStatusChange(status);

      if (status.status === 'paid' || status.status === 'failed' || status.status === 'cancelled') {
        return; // 支付完成或失败，停止轮询
      }
    } catch (error) {
      console.error('查询支付状态失败:', error);
    }

    if (!cancelled && attempts < maxAttempts) {
      setTimeout(poll, interval);
    }
  };

  poll();

  // 返回取消函数
  return () => {
    cancelled = true;
  };
}



