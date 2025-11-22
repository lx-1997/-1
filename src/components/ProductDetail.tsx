import React, { useState } from 'react';
import { 
  Card, 
  Row, 
  Col, 
  Image, 
  Typography, 
  Button, 
  Tag, 
  Rate, 
  Space, 
  Divider,
  InputNumber,
  message,
  Descriptions,
  Tabs
} from 'antd';
import { 
  ShoppingCartOutlined, 
  ArrowLeftOutlined,
  CheckOutlined,
  FireOutlined
} from '@ant-design/icons';
import { Product, ProductVariant } from '../types';

const { Title, Text, Paragraph } = Typography;

interface ProductDetailProps {
  product: Product;
  onBack: () => void;
  onAddToCart: (product: Product, variantId: string, quantity: number) => void;
  onBuyNow: (product: Product, variantId: string, quantity: number) => void;
}

const ProductDetail: React.FC<ProductDetailProps> = ({
  product,
  onBack,
  onAddToCart,
  onBuyNow
}) => {
  const [selectedVariant, setSelectedVariant] = useState<ProductVariant | null>(
    product.variants.length > 0 ? product.variants[0] : null
  );
  const [quantity, setQuantity] = useState(1);
  const [currentImageIndex, setCurrentImageIndex] = useState(0);

  const currentPrice = selectedVariant?.price || product.price;
  const currentStock = selectedVariant?.stock || product.stock;
  const hasDiscount = product.originalPrice && product.originalPrice > currentPrice;
  const discountPercent = hasDiscount 
    ? Math.round((1 - currentPrice / product.originalPrice!) * 100)
    : 0;

  // 按属性分组款式
  const variantGroups: Record<string, string[]> = {};
  product.variants.forEach(variant => {
    Object.entries(variant.attributes).forEach(([key, value]) => {
      if (!variantGroups[key]) {
        variantGroups[key] = [];
      }
      if (!variantGroups[key].includes(value)) {
        variantGroups[key].push(value);
      }
    });
  });

  const handleVariantSelect = (attributeKey: string, attributeValue: string) => {
    // 找到匹配的款式
    const matchingVariant = product.variants.find(variant => {
      // 检查当前选中的属性
      const currentAttributes = selectedVariant?.attributes || {};
      const newAttributes = { ...currentAttributes, [attributeKey]: attributeValue };
      
      // 检查是否有完全匹配的款式
      return Object.entries(newAttributes).every(([key, val]) => 
        variant.attributes[key] === val
      );
    });

    if (matchingVariant) {
      setSelectedVariant(matchingVariant);
      if (matchingVariant.stock < quantity) {
        setQuantity(Math.max(1, matchingVariant.stock));
      }
    }
  };

  const handleAddToCart = () => {
    if (!selectedVariant && product.variants.length > 0) {
      message.warning('请选择商品款式');
      return;
    }
    if (currentStock < quantity) {
      message.warning('库存不足');
      return;
    }
    const variantId = selectedVariant?.id || 'default';
    onAddToCart(product, variantId, quantity);
    message.success('已添加到购物车');
  };

  const handleBuyNow = () => {
    if (!selectedVariant && product.variants.length > 0) {
      message.warning('请选择商品款式');
      return;
    }
    if (currentStock < quantity) {
      message.warning('库存不足');
      return;
    }
    const variantId = selectedVariant?.id || 'default';
    onBuyNow(product, variantId, quantity);
  };

  return (
    <div style={{ padding: '24px', background: '#f5f5f5', minHeight: '100vh' }}>
      <Button 
        icon={<ArrowLeftOutlined />} 
        onClick={onBack}
        style={{ marginBottom: '16px' }}
      >
        返回商城
      </Button>

      <Card>
        <Row gutter={24}>
          {/* 左侧图片区域 */}
          <Col xs={24} md={10}>
            <div style={{ position: 'sticky', top: '24px' }}>
              {/* 主图 */}
              <div style={{ 
                width: '100%', 
                aspectRatio: '1',
                background: '#fafafa',
                borderRadius: '8px',
                overflow: 'hidden',
                marginBottom: '16px',
                position: 'relative'
              }}>
                <Image
                  src={product.images[currentImageIndex] || 'https://via.placeholder.com/500'}
                  alt={product.name}
                  preview={{
                    mask: '预览',
                    src: product.images[currentImageIndex] || 'https://via.placeholder.com/500'
                  }}
                  style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                />
                {hasDiscount && (
                  <Tag
                    color="red"
                    style={{
                      position: 'absolute',
                      top: '16px',
                      right: '16px',
                      fontSize: '16px',
                      padding: '4px 12px'
                    }}
                  >
                    {discountPercent}% OFF
                  </Tag>
                )}
              </div>

              {/* 缩略图 */}
              {product.images.length > 1 && (
                <Row gutter={8}>
                  {product.images.map((img, index) => (
                    <Col span={6} key={index}>
                      <div
                        style={{
                          width: '100%',
                          aspectRatio: '1',
                          borderRadius: '4px',
                          overflow: 'hidden',
                          border: currentImageIndex === index ? '2px solid #1890ff' : '1px solid #d9d9d9',
                          cursor: 'pointer'
                        }}
                        onClick={() => setCurrentImageIndex(index)}
                      >
                        <Image
                          src={img}
                          alt={`${product.name} ${index + 1}`}
                          preview={false}
                          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                        />
                      </div>
                    </Col>
                  ))}
                </Row>
              )}
            </div>
          </Col>

          {/* 右侧信息区域 */}
          <Col xs={24} md={14}>
            <div>
              <Title level={2} style={{ marginBottom: '8px' }}>
                {product.name}
              </Title>

              <Space style={{ marginBottom: '16px' }}>
                <Rate disabled defaultValue={product.rating} />
                <Text type="secondary">
                  {product.rating.toFixed(1)} ({product.ratingCount}条评价)
                </Text>
                <Text type="secondary">|</Text>
                <Text type="secondary">已售 {product.sales}</Text>
              </Space>

              <Divider />

              {/* 价格 */}
              <div style={{ marginBottom: '24px', padding: '16px', background: '#fff7e6', borderRadius: '8px' }}>
                <Space align="baseline">
                  <Text style={{ fontSize: '32px', color: '#ff4d4f', fontWeight: 'bold' }}>
                    ¥{currentPrice.toFixed(2)}
                  </Text>
                  {hasDiscount && (
                    <>
                      <Text delete type="secondary" style={{ fontSize: '18px' }}>
                        ¥{product.originalPrice!.toFixed(2)}
                      </Text>
                      <Tag color="red" style={{ fontSize: '14px' }}>
                        省¥{(product.originalPrice! - currentPrice).toFixed(2)}
                      </Tag>
                    </>
                  )}
                </Space>
              </div>

              {/* 款式选择 */}
              {Object.keys(variantGroups).length > 0 && (
                <div style={{ marginBottom: '24px' }}>
                  {Object.entries(variantGroups).map(([attributeKey, values]) => (
                    <div key={attributeKey} style={{ marginBottom: '16px' }}>
                      <Text strong style={{ display: 'block', marginBottom: '8px' }}>
                        {attributeKey}：
                      </Text>
                      <Space wrap>
                        {values.map(value => {
                          const isSelected = selectedVariant?.attributes[attributeKey] === value;
                          return (
                            <Button
                              key={value}
                              type={isSelected ? 'primary' : 'default'}
                              shape="round"
                              icon={isSelected ? <CheckOutlined /> : null}
                              onClick={() => handleVariantSelect(attributeKey, value)}
                              style={{ marginBottom: '8px' }}
                            >
                              {value}
                            </Button>
                          );
                        })}
                      </Space>
                    </div>
                  ))}
                  
                  {selectedVariant && (
                    <div style={{ marginTop: '16px', padding: '12px', background: '#f5f5f5', borderRadius: '4px' }}>
                      <Text type="secondary">已选择：</Text>
                      <Text strong>
                        {Object.entries(selectedVariant.attributes)
                          .map(([key, val]) => `${key}: ${val}`)
                          .join(', ')}
                      </Text>
                      <br />
                      <Text type="secondary">库存：{selectedVariant.stock}件</Text>
                    </div>
                  )}
                </div>
              )}

              {/* 数量选择 */}
              <div style={{ marginBottom: '24px' }}>
                <Text strong style={{ display: 'block', marginBottom: '8px' }}>
                  数量：
                </Text>
                <InputNumber
                  min={1}
                  max={currentStock}
                  value={quantity}
                  onChange={(val) => setQuantity(val || 1)}
                  style={{ width: '120px' }}
                />
                <Text type="secondary" style={{ marginLeft: '16px' }}>
                  库存 {currentStock} 件
                </Text>
              </div>

              {/* 操作按钮 */}
              <Space size="large" style={{ marginBottom: '24px' }}>
                <Button
                  type="primary"
                  size="large"
                  icon={<ShoppingCartOutlined />}
                  onClick={handleAddToCart}
                  disabled={currentStock === 0}
                >
                  加入购物车
                </Button>
                <Button
                  type="primary"
                  size="large"
                  danger
                  onClick={handleBuyNow}
                  disabled={currentStock === 0}
                >
                  立即购买
                </Button>
              </Space>

              {/* 商品标签 */}
              {product.tags.length > 0 && (
                <div style={{ marginBottom: '24px' }}>
                  <Space wrap>
                    {product.tags.map(tag => (
                      <Tag key={tag} color="blue">{tag}</Tag>
                    ))}
                  </Space>
                </div>
              )}
            </div>
          </Col>
        </Row>

        {/* 商品详情标签页 */}
        <Divider />
        <Tabs 
          defaultActiveKey="detail"
          items={[
            {
              key: 'detail',
              label: '商品详情',
              children: (
                <div style={{ padding: '24px', background: '#fff' }}>
                  <Paragraph style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>
                    {product.description}
                  </Paragraph>
                  {product.images.map((img, index) => (
                    <Image
                      key={index}
                      src={img}
                      alt={`${product.name} 详情图 ${index + 1}`}
                      style={{ width: '100%', marginBottom: '16px' }}
                    />
                  ))}
                </div>
              )
            },
            {
              key: 'specs',
              label: '规格参数',
              children: (
                <div style={{ padding: '24px', background: '#fff' }}>
                  <Descriptions bordered column={2}>
                    <Descriptions.Item label="商品名称">{product.name}</Descriptions.Item>
                    <Descriptions.Item label="商品分类">{product.category}</Descriptions.Item>
                    <Descriptions.Item label="商品编号">{product.id}</Descriptions.Item>
                    <Descriptions.Item label="库存数量">{currentStock}</Descriptions.Item>
                    <Descriptions.Item label="销量">{product.sales}</Descriptions.Item>
                    <Descriptions.Item label="评分">{product.rating.toFixed(1)}</Descriptions.Item>
                  </Descriptions>
                </div>
              )
            }
          ]}
        />
      </Card>
    </div>
  );
};

export default ProductDetail;

