import { readAccountStorage, writeAccountStorage } from './accountStorage';
import type { AppState, Post, Comment, Rating, Payment, CartItem, Order } from '../types';

/** Local community/cart state is partitioned by the authenticated account.
 * Unowned legacy state cannot safely be attributed to the next person signing in.
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

export function loadSavedCommunity(accountId: string | null = null): Partial<PersistedCommunity> | null {
  if (typeof window === 'undefined') return null;
  try {
    const parsed = readAccountStorage<unknown>(COMMUNITY_STORAGE_KEY, accountId, null);
    if (!parsed || typeof parsed !== 'object') return null;
    const saved = parsed as Record<string, unknown>;
    const arr = <T,>(v: unknown): T[] | undefined => (Array.isArray(v) ? (v as T[]) : undefined);
    return {
      posts: arr<Post>(saved.posts),
      comments: arr<Comment>(saved.comments),
      ratings: arr<Rating>(saved.ratings),
      payments: arr<Payment>(saved.payments),
      purchasedPosts: arr<string>(saved.purchasedPosts),
      likedPosts: arr<string>(saved.likedPosts),
      cart: arr<CartItem>(saved.cart),
      orders: arr<Order>(saved.orders),
      userBalance: typeof saved.userBalance === 'number' ? saved.userBalance : null,
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
    writeAccountStorage(COMMUNITY_STORAGE_KEY, state.user?.id ?? null, payload);
  } catch {
    /* 配额超限或隐私模式下静默失败，不阻断交互 */
  }
}
