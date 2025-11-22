/**
 * 购物车工具函数
 */

import { CartItem, Product, ProductVariant } from '../types';

/**
 * 检查商品是否已在购物车中
 */
export function isProductInCart(
  cartItems: CartItem[],
  productId: string,
  variantId: string
): boolean {
  return cartItems.some(
    item => item.productId === productId && item.variantId === variantId
  );
}

/**
 * 获取购物车中某个商品的数量
 */
export function getCartItemQuantity(
  cartItems: CartItem[],
  productId: string,
  variantId: string
): number {
  const item = cartItems.find(
    item => item.productId === productId && item.variantId === variantId
  );
  return item ? item.quantity : 0;
}

/**
 * 计算购物车总价
 */
export function calculateCartTotal(cartItems: CartItem[]): number {
  return cartItems.reduce(
    (total, item) => total + item.variant.price * item.quantity,
    0
  );
}

/**
 * 计算购物车商品总数
 */
export function calculateCartItemCount(cartItems: CartItem[]): number {
  return cartItems.reduce((total, item) => total + item.quantity, 0);
}

/**
 * 验证商品库存
 */
export function validateStock(
  variant: ProductVariant,
  quantity: number
): { valid: boolean; message?: string } {
  if (variant.stock < quantity) {
    return {
      valid: false,
      message: `库存不足，当前库存：${variant.stock}件`
    };
  }
  if (quantity <= 0) {
    return {
      valid: false,
      message: '购买数量必须大于0'
    };
  }
  return { valid: true };
}

/**
 * 合并购物车中的相同商品
 */
export function mergeCartItems(items: CartItem[]): CartItem[] {
  const itemMap = new Map<string, CartItem>();
  
  items.forEach(item => {
    const key = `${item.productId}_${item.variantId}`;
    const existing = itemMap.get(key);
    
    if (existing) {
      existing.quantity += item.quantity;
    } else {
      itemMap.set(key, { ...item });
    }
  });
  
  return Array.from(itemMap.values());
}

/**
 * 格式化价格
 */
export function formatPrice(price: number): string {
  return `¥${price.toFixed(2)}`;
}

/**
 * 格式化总价（带千分位）
 */
export function formatTotalPrice(total: number): string {
  return `¥${total.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}



