# 商城模块使用说明

## 功能概述

商城模块已完整集成到应用中，提供了类似淘宝的购物体验，包括商品浏览、购物车管理、订单处理和支付功能。

## 已实现的功能

### 1. 商品列表 (`Shop.tsx`)
- ✅ 商品网格展示（类似淘宝的商品卡片）
- ✅ 商品搜索功能
- ✅ 分类筛选
- ✅ 多种排序方式（价格、销量、评分）
- ✅ 商品图片、价格、销量、评分展示
- ✅ 快速加入购物车
- ✅ 折扣标签显示

### 2. 商品详情 (`ProductDetail.tsx`)
- ✅ 多图展示（主图 + 缩略图切换）
- ✅ 商品详细介绍
- ✅ 款式选择（颜色、尺寸等属性）
- ✅ 数量选择器
- ✅ 库存显示
- ✅ 加入购物车和立即购买
- ✅ 商品详情和规格参数标签页

### 3. 购物车 (`Cart.tsx`)
- ✅ 购物车商品列表
- ✅ 商品数量修改
- ✅ 商品删除
- ✅ 全选/单选功能
- ✅ 价格计算（单价、小计、总计）
- ✅ 结算功能
- ✅ 购物车商品数量统计

### 4. 订单管理 (`Orders.tsx`)
- ✅ 订单列表（全部/待支付/已支付/已完成）
- ✅ 订单详情查看
- ✅ 订单支付
- ✅ 订单取消
- ✅ 退款申请
- ✅ 订单状态标签

### 5. 支付功能 (`PaymentModal.tsx`)
- ✅ 微信支付支持
- ✅ 支付宝支付支持
- ✅ 支付二维码生成和展示
- ✅ 支付状态轮询（自动检测支付结果）
- ✅ 支付成功/失败提示
- ✅ 支付状态实时更新

### 6. 支付服务 (`paymentService.ts`)
- ✅ 支付订单创建
- ✅ 支付状态查询
- ✅ 支付状态轮询
- ✅ 支付取消
- ✅ 退款申请

### 7. 工具函数 (`cartUtils.ts`)
- ✅ 购物车商品检查
- ✅ 价格格式化
- ✅ 库存验证
- ✅ 购物车合并
- ✅ 购物车统计

## 技术架构

### 文件结构
```
src/
├── components/
│   ├── Shop.tsx              # 商品列表
│   ├── ProductDetail.tsx     # 商品详情
│   ├── Cart.tsx              # 购物车
│   ├── Orders.tsx            # 订单管理
│   └── PaymentModal.tsx      # 支付模态框
├── services/
│   └── paymentService.ts     # 支付服务
├── utils/
│   └── cartUtils.ts          # 购物车工具函数
├── types/
│   └── index.ts              # 类型定义（已更新）
└── data/
    └── mockData.ts           # 模拟数据（已更新）
```

### 类型定义

主要类型包括：
- `Product`: 商品
- `ProductVariant`: 商品款式
- `CartItem`: 购物车项
- `Order`: 订单
- `OrderItem`: 订单项
- `ShippingAddress`: 收货地址

## 使用流程

### 1. 浏览商品
1. 登录后，点击侧边栏"商城"菜单
2. 浏览商品列表，可以使用搜索、分类筛选、排序
3. 点击商品卡片查看详情

### 2. 购买商品
1. 在商品详情页选择款式和数量
2. 点击"加入购物车"或"立即购买"
3. 如果加入购物车，商品会保存到购物车
4. 如果立即购买，会直接创建订单

### 3. 购物车管理
1. 点击顶部购物车图标或侧边栏"购物车"
2. 查看购物车中的商品
3. 修改商品数量或删除商品
4. 选择要结算的商品
5. 点击"结算"按钮创建订单

### 4. 订单支付
1. 在"我的订单"页面查看订单
2. 点击"支付"按钮
3. 选择支付方式（微信或支付宝）
4. 扫描二维码完成支付
5. 系统会自动检测支付状态

## 支付集成说明

### 当前实现
- 支付功能为模拟实现，用于演示
- 支付二维码为模拟生成
- 支付状态轮询为模拟实现

### 实际集成步骤

#### 1. 微信支付集成
```typescript
// 在 paymentService.ts 中替换 createPayment 函数
export async function createPayment(request: PaymentRequest): Promise<PaymentResponse> {
  const response = await fetch('/api/payment/wechat/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      orderId: request.orderId,
      amount: request.amount,
      description: request.description
    })
  });
  const data = await response.json();
  return {
    success: data.success,
    qrCode: data.code_url, // 微信支付二维码
    transactionId: data.transaction_id
  };
}
```

#### 2. 支付宝集成
```typescript
// 支付宝支付
export async function createPayment(request: PaymentRequest): Promise<PaymentResponse> {
  const response = await fetch('/api/payment/alipay/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      orderId: request.orderId,
      amount: request.amount,
      subject: request.description
    })
  });
  const data = await response.json();
  return {
    success: data.success,
    qrCode: data.qr_code, // 支付宝二维码
    transactionId: data.trade_no
  };
}
```

#### 3. 支付状态查询
```typescript
// 查询真实支付状态
export async function checkPaymentStatus(orderId: string): Promise<PaymentStatus> {
  const response = await fetch(`/api/payment/status/${orderId}`);
  const data = await response.json();
  return {
    status: data.status, // 'pending' | 'paid' | 'failed' | 'cancelled'
    transactionId: data.transaction_id,
    paidAt: data.paid_at
  };
}
```

### 后端API要求

需要实现以下API端点：

1. **创建支付订单**
   - `POST /api/payment/create`
   - 请求体：`{ orderId, paymentMethod, amount, description }`
   - 返回：`{ success, qrCode, transactionId }`

2. **查询支付状态**
   - `GET /api/payment/status/:orderId`
   - 返回：`{ status, transactionId, paidAt }`

3. **取消支付**
   - `POST /api/payment/cancel/:orderId`
   - 返回：`{ success }`

4. **申请退款**
   - `POST /api/payment/refund`
   - 请求体：`{ orderId, reason }`
   - 返回：`{ success }`

## 导航集成

### 侧边栏菜单
- 商城：进入商品列表
- 购物车：查看购物车
- 我的订单：查看订单列表

### Header顶部
- 购物车图标：显示购物车商品数量，点击跳转到购物车

## 数据管理

### 模拟数据
- `mockProducts`: 5个示例商品
- `mockOrders`: 2个示例订单

### 状态管理
商城相关状态已集成到 `AppState`：
- `products`: 商品列表
- `cart`: 购物车
- `orders`: 订单列表
- `selectedProduct`: 当前选中的商品

## 注意事项

1. **支付功能**：当前为模拟实现，实际使用时需要集成真实的支付SDK
2. **库存管理**：当前为前端验证，实际应该在后端验证
3. **订单状态**：订单状态更新需要后端支持
4. **收货地址**：当前订单中收货地址为可选，实际使用时需要添加地址管理功能

## 后续优化建议

1. 添加收货地址管理功能
2. 添加商品评价功能
3. 添加优惠券/折扣功能
4. 添加订单物流跟踪
5. 添加商品收藏功能
6. 优化移动端体验
7. 添加商品推荐功能
8. 添加搜索历史记录

## 测试建议

1. 测试商品浏览和搜索
2. 测试商品详情页的款式选择
3. 测试购物车的增删改查
4. 测试订单创建和支付流程
5. 测试订单状态更新
6. 测试支付状态轮询
7. 测试移动端响应式布局

## 总结

商城模块已完整实现，提供了完整的购物流程：
- ✅ 商品浏览和搜索
- ✅ 商品详情和款式选择
- ✅ 购物车管理
- ✅ 订单创建和管理
- ✅ 支付功能（微信/支付宝）
- ✅ 支付状态实时更新

所有功能都已集成到现有应用中，可以直接使用。支付功能当前为模拟实现，实际使用时需要替换为真实的支付API调用。



