import React, { useState } from 'react';
import { Card, Row, Col, Image, Typography, Tag, Rate, Button, Input, Space, Select, Empty } from 'antd';
import { ShoppingCartOutlined, SearchOutlined, FireOutlined } from '@ant-design/icons';
import { Product } from '../types';

const { Title, Text, Paragraph } = Typography;
const { Search } = Input;
const { Option } = Select;

interface ShopProps {
  products: Product[];
  onProductClick: (product: Product) => void;
  onAddToCart: (product: Product, variantId: string, quantity: number) => void;
}

const Shop: React.FC<ShopProps> = ({ products, onProductClick, onAddToCart }) => {
  const [searchKeyword, setSearchKeyword] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [sortBy, setSortBy] = useState<string>('default');

  // 获取所有分类
  const categories = ['all', ...Array.from(new Set(products.map(p => p.category)))];

  // 过滤和排序商品
  const filteredProducts = products
    .filter(product => {
      const matchKeyword = !searchKeyword || 
        product.name.toLowerCase().includes(searchKeyword.toLowerCase()) ||
        product.description.toLowerCase().includes(searchKeyword.toLowerCase());
      const matchCategory = selectedCategory === 'all' || product.category === selectedCategory;
      return matchKeyword && matchCategory && product.status === 'on_sale';
    })
    .sort((a, b) => {
      switch (sortBy) {
        case 'price-low':
          return a.price - b.price;
        case 'price-high':
          return b.price - a.price;
        case 'sales':
          return b.sales - a.sales;
        case 'rating':
          return b.rating - a.rating;
        default:
          return 0;
      }
    });

  const handleQuickAdd = (e: React.MouseEvent, product: Product) => {
    e.stopPropagation();
    if (product.variants.length > 0) {
      // 如果有多个款式，跳转到详情页选择
      onProductClick(product);
    } else {
      // 如果只有一个款式或没有款式，直接添加到购物车
      const variantId = product.variants[0]?.id || 'default';
      onAddToCart(product, variantId, 1);
    }
  };

  return (
    <div style={{ padding: '16px' }}>
      {/* 搜索和筛选栏 */}
      <Card style={{ marginBottom: '24px' }}>
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <Search
              placeholder="搜索商品..."
              allowClear
              enterButton={<SearchOutlined />}
              size="large"
              value={searchKeyword}
              onChange={(e) => setSearchKeyword(e.target.value)}
              style={{ maxWidth: '500px' }}
            />
          </Col>
          <Col>
            <Select
              value={selectedCategory}
              onChange={setSelectedCategory}
              style={{ width: 150 }}
              size="large"
            >
              {categories.map(cat => (
                <Option key={cat} value={cat}>
                  {cat === 'all' ? '全部分类' : cat}
                </Option>
              ))}
            </Select>
          </Col>
          <Col>
            <Select
              value={sortBy}
              onChange={setSortBy}
              style={{ width: 150 }}
              size="large"
            >
              <Option value="default">默认排序</Option>
              <Option value="sales">销量最高</Option>
              <Option value="rating">评分最高</Option>
              <Option value="price-low">价格从低到高</Option>
              <Option value="price-high">价格从高到低</Option>
            </Select>
          </Col>
        </Row>
      </Card>

      {/* 商品列表 */}
      {filteredProducts.length === 0 ? (
        <Empty description="暂无商品" />
      ) : (
        <Row gutter={[16, 16]}>
          {filteredProducts.map(product => {
            const mainImage = product.images[0] || 'https://via.placeholder.com/300';
            const hasDiscount = product.originalPrice && product.originalPrice > product.price;
            const discountPercent = hasDiscount 
              ? Math.round((1 - product.price / product.originalPrice!) * 100)
              : 0;

            return (
              <Col xs={24} sm={12} md={8} lg={6} key={product.id}>
                <Card
                  hoverable
                  style={{ 
                    height: '100%',
                    borderRadius: '8px',
                    overflow: 'hidden',
                    cursor: 'pointer'
                  }}
                  cover={
                    <div style={{ position: 'relative', paddingTop: '100%', background: '#fafafa' }}>
                      <Image
                        src={mainImage}
                        alt={product.name}
                        preview={false}
                        style={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          width: '100%',
                          height: '100%',
                          objectFit: 'cover'
                        }}
                      />
                      {hasDiscount && (
                        <Tag
                          color="red"
                          style={{
                            position: 'absolute',
                            top: '8px',
                            right: '8px',
                            zIndex: 1
                          }}
                        >
                          {discountPercent}% OFF
                        </Tag>
                      )}
                      {product.sales > 100 && (
                        <Tag
                          color="orange"
                          icon={<FireOutlined />}
                          style={{
                            position: 'absolute',
                            top: '8px',
                            left: '8px',
                            zIndex: 1
                          }}
                        >
                          热销
                        </Tag>
                      )}
                    </div>
                  }
                  onClick={() => onProductClick(product)}
                  actions={[
                    <Button
                      type="primary"
                      icon={<ShoppingCartOutlined />}
                      onClick={(e) => handleQuickAdd(e, product)}
                      block
                    >
                      加入购物车
                    </Button>
                  ]}
                >
                  <div style={{ minHeight: '120px' }}>
                    <Title level={5} ellipsis={{ rows: 2 }} style={{ marginBottom: '8px' }}>
                      {product.name}
                    </Title>
                    <Paragraph
                      ellipsis={{ rows: 2 }}
                      style={{ color: '#666', fontSize: '12px', marginBottom: '12px', minHeight: '40px' }}
                    >
                      {product.description}
                    </Paragraph>
                    
                    <div style={{ marginBottom: '8px' }}>
                      <Space>
                        <Rate disabled defaultValue={product.rating} style={{ fontSize: '12px' }} />
                        <Text type="secondary" style={{ fontSize: '12px' }}>
                          ({product.ratingCount})
                        </Text>
                      </Space>
                    </div>

                    <div style={{ marginBottom: '8px' }}>
                      <Text type="secondary" style={{ fontSize: '12px' }}>
                        已售 {product.sales}
                      </Text>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div>
                        <Text strong style={{ fontSize: '18px', color: '#ff4d4f' }}>
                          ¥{product.price.toFixed(2)}
                        </Text>
                        {hasDiscount && (
                          <Text delete type="secondary" style={{ marginLeft: '8px', fontSize: '14px' }}>
                            ¥{product.originalPrice!.toFixed(2)}
                          </Text>
                        )}
                      </div>
                      {product.variants.length > 0 && (
                        <Tag color="blue">{product.variants.length}种款式</Tag>
                      )}
                    </div>
                  </div>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}
    </div>
  );
};

export default Shop;


