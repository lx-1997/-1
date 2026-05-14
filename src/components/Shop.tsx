import React, { useState } from 'react';
import { Image, Typography, Tag, Rate, Button, Input, Space, Select, Empty } from 'antd';
import { FileTextOutlined, SearchOutlined, FireOutlined } from '@ant-design/icons';
import { Product } from '../types';

const { Text } = Typography;
const { Search } = Input;
const { Option } = Select;

interface ShopProps {
  products: Product[];
  onProductClick: (product: Product) => void;
  onAddToCart: (product: Product, variantId: string, quantity: number) => void;
  showHeader?: boolean;
}

const Shop: React.FC<ShopProps> = ({ products, onProductClick, onAddToCart, showHeader = true }) => {
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
    const variant = product.variants.find(item => item.stock > 0) || product.variants[0];
    onAddToCart(product, variant?.id || 'default', 1);
  };

  return (
    <div className="shop-shell">
      {/* 搜索和筛选栏 */}
      {showHeader && (
        <div className="section-heading">
          <div>
            <h2>研究订阅与模板库</h2>
            <div className="section-description">把研报包、策略模板、数据包和专家服务沉淀为可复用研究资产。</div>
          </div>
        </div>
      )}

      <div className="filter-bar">
        <Space wrap size={10} style={{ width: '100%', alignItems: 'center' }}>
          <Search
            placeholder="搜索研报、模板、数据包..."
            allowClear
            enterButton={<SearchOutlined />}
            value={searchKeyword}
            onChange={(e) => setSearchKeyword(e.target.value)}
            style={{ maxWidth: 420, minWidth: 240 }}
          />
          <Select
            value={selectedCategory}
            onChange={setSelectedCategory}
            style={{ width: 150 }}
          >
            {categories.map(cat => (
              <Option key={cat} value={cat}>
                {cat === 'all' ? '全部分类' : cat}
              </Option>
            ))}
          </Select>
          <Select
            value={sortBy}
            onChange={setSortBy}
            style={{ width: 150 }}
          >
            <Option value="default">默认排序</Option>
            <Option value="sales">订阅最多</Option>
            <Option value="rating">评分最高</Option>
            <Option value="price-low">价格从低到高</Option>
            <Option value="price-high">价格从高到低</Option>
          </Select>
        </Space>
      </div>

      {/* 商品列表 */}
      {filteredProducts.length === 0 ? (
        <Empty description="暂无研究资产" />
      ) : (
        <div className="product-grid">
          {filteredProducts.map(product => {
            const mainImage = product.images[0] || 'https://via.placeholder.com/300';
            const hasDiscount = product.originalPrice && product.originalPrice > product.price;
            const discountPercent = hasDiscount 
              ? Math.round((1 - product.price / product.originalPrice!) * 100)
              : 0;

            return (
              <article
                  className="product-card-pro"
                  key={product.id}
                  onClick={() => onProductClick(product)}
                >
                  <div className="product-image-wrap">
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
                        objectFit: 'cover',
                        opacity: 0.18,
                        zIndex: 0
                      }}
                    />
                    <div className="product-image-art">
                      <span>{product.category}</span>
                      <strong>{product.name}</strong>
                    </div>
                    {hasDiscount && (
                      <Tag
                        color="red"
                        style={{
                          position: 'absolute',
                          top: 8,
                          right: 8,
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
                          top: 8,
                          left: 8,
                          zIndex: 1
                        }}
                      >
                        热销
                      </Tag>
                    )}
                  </div>

                  <div className="product-card-body">
                    <h3 className="product-title">{product.name}</h3>
                    <div className="product-desc">{product.description}</div>
                    
                    <div style={{ marginBottom: '8px' }}>
                      <Space>
                        <Rate disabled defaultValue={product.rating} style={{ fontSize: '12px' }} />
                        <Text type="secondary" style={{ fontSize: '12px' }}>
                          ({product.ratingCount})
                        </Text>
                      </Space>
                    </div>

                    <div>
                      <Text type="secondary" style={{ fontSize: '12px' }}>
                        已订阅 {product.sales}
                      </Text>
                    </div>

                    <div className="product-price-row">
                      <div>
                        <Text className="product-price">
                          ¥{product.price.toFixed(2)}
                        </Text>
                        {hasDiscount && (
                          <Text delete type="secondary" style={{ marginLeft: '8px', fontSize: '14px' }}>
                            ¥{product.originalPrice!.toFixed(2)}
                          </Text>
                        )}
                      </div>
                      {product.variants.length > 0 && (
                        <Tag color="blue">{product.variants.length}个版本</Tag>
                      )}
                    </div>

                    <Button
                      type="primary"
                      icon={<FileTextOutlined />}
                      onClick={(e) => handleQuickAdd(e, product)}
                      block
                      style={{ marginTop: 12 }}
                    >
                      加入订阅单
                    </Button>
                  </div>
                </article>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default Shop;
