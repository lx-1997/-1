import type { AppState, Post, Comment, Rating, Payment, CartItem, Order } from '../types';

/**
 * 社区与商城的用户生成内容（UGC）本地持久化。
 *
 * 本应用为演示态、无真实账户体系（mockUser），社区/商城的发帖、评论、点赞、评分、
 * 购买、下单等写操作此前仅改内存 reducer，刷新即全部丢失（体验报告确认为最高优先缺陷）。
 * 这里把这些状态镜像到 localStorage：刷新后保留、与已标注的「本地演示数据」说明一致。
 * 仅单机、不跨设备——与平台数据可信度标注口径相符。
 */
export const COMMUNITY_STORAGE_KEY = 'deepfocus.community.v1';

export interface PersistedCommunity {
  posts: Post[];
  comments: Comment[];
  ratings: Rating[];
  payments: Payment[];
  purchasedPosts: string[];
  likedPosts: string[];
  cart: CartItem[];
  orders: Order[];
  userBalance: number | null;
}

export function loadSavedCommunity(): Partial<PersistedCommunity> | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(COMMUNITY_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    const arr = <T,>(v: unknown): T[] | undefined => (Array.isArray(v) ? (v as T[]) : undefined);
    return {
      posts: arr<Post>(parsed.posts),
      comments: arr<Comment>(parsed.comments),
      ratings: arr<Rating>(parsed.ratings),
      payments: arr<Payment>(parsed.payments),
      purchasedPosts: arr<string>(parsed.purchasedPosts),
      likedPosts: arr<string>(parsed.likedPosts),
      cart: arr<CartItem>(parsed.cart),
      orders: arr<Order>(parsed.orders),
      userBalance: typeof parsed.userBalance === 'number' ? parsed.userBalance : null,
    };
  } catch {
    return null;
  }
}

export function saveCommunity(state: AppState): void {
  if (typeof window === 'undefined') return;
  try {
    const payload: PersistedCommunity = {
      posts: state.posts,
      comments: state.comments,
      ratings: state.ratings,
      payments: state.payments,
      purchasedPosts: state.purchasedPosts,
      likedPosts: state.likedPosts,
      cart: state.cart,
      orders: state.orders,
      userBalance: state.user ? state.user.balance : null,
    };
    window.localStorage.setItem(COMMUNITY_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    /* 配额超限或隐私模式下静默失败，不阻断交互 */
  }
}
