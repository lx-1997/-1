import { apiGet, apiPost } from './apiClient';
import { User } from '../types';

/**
 * 真实认证服务（替换演示态登录）。
 *
 * 令牌存于 localStorage 的 `auth_token`，与 apiClient 的请求拦截器约定一致——
 * 拦截器会在每次请求自动注入 `Authorization: Bearer <token>`，并在 401 时清理 + 跳登录页。
 */

const TOKEN_KEY = 'auth_token';

export type AuthRole = 'admin' | 'analyst' | 'viewer';

export interface Membership {
  tier: 'trial' | 'premium' | 'lifetime';  // 体验期 / 尊享会员 / 永久会员
  expires_at: string | null;      // 到期 ISO 时间（体验期为 null；永久为哨兵远期）
  days_left: number | null;       // 剩余天数（体验期/永久为 null）
}

export interface AuthUser {
  id: string;
  email: string | null;
  phone?: string | null;
  username: string;
  role: AuthRole;
  is_active: boolean;
  created_at: string;
  membership?: Membership | null;
  trial_claimable?: boolean;  // 是否可领「登录送 3 天体验会员」
}

interface TokenResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export interface AuthSession {
  token: string;
  user: User;
  authUser: AuthUser;
}

const ROLE_TO_LEVEL: Record<AuthRole, User['memberLevel']> = {
  admin: 'vip',
  analyst: 'premium',
  viewer: 'free'
};

export function getStoredToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

function storeToken(token: string): void {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(TOKEN_KEY, token);
  }
}

export function clearToken(): void {
  if (typeof window !== 'undefined') {
    window.localStorage.removeItem(TOKEN_KEY);
  }
}

/** 把后端认证账号映射到前端 User 视图模型（社区字段给安全默认值）。 */
export function mapAuthUser(authUser: AuthUser): User {
  return {
    id: authUser.id,
    username: authUser.username,
    email: authUser.email || '',
    avatar: `https://api.dicebear.com/7.x/avataaars/svg?seed=${encodeURIComponent(authUser.username)}`,
    reputation: 0,
    memberLevel: ROLE_TO_LEVEL[authUser.role] ?? 'free',
    joinDate: (authUser.created_at || '').slice(0, 10),
    totalPosts: 0,
    totalEarnings: 0,
    followers: 0,
    following: 0,
    balance: 0
  };
}

function toSession(resp: TokenResponse): AuthSession {
  storeToken(resp.access_token);
  return { token: resp.access_token, user: mapAuthUser(resp.user), authUser: resp.user };
}

export async function login(username: string, password: string): Promise<AuthSession> {
  const resp = await apiPost<TokenResponse>('/api/auth/login', { username, password });
  return toSession(resp);
}

export async function register(
  email: string,
  username: string,
  password: string,
  phone?: string,
  inviteCode?: string,
  website?: string,        // 蜜罐：真人留空
  turnstileToken?: string  // 人机校验票据
): Promise<AuthSession> {
  const resp = await apiPost<TokenResponse>('/api/auth/register', {
    email: email || undefined,
    phone: phone || undefined,
    username,
    password,
    invite_code: inviteCode || undefined,
    website: website || undefined,
    turnstile_token: turnstileToken || undefined
  });
  return toSession(resp);
}

export interface CaptchaConfig { enabled: boolean; provider: string; sitekey: string; }
export async function fetchCaptchaConfig(): Promise<CaptchaConfig> {
  try { return await apiGet<CaptchaConfig>('/api/auth/captcha'); } catch { return { enabled: false, provider: '', sitekey: '' }; }
}

export interface InviteOverview { code: string; invited_count: number; invited: { username: string; created_at: string }[]; }
export async function fetchInvite(): Promise<InviteOverview> {
  return apiGet<InviteOverview>('/api/auth/invite');
}

// 邀请奖励活动：进度 + 卡包(可兑换) + 被邀请人状态
export type CardKey = 'day3' | 'week' | 'month' | 'quarter' | 'year' | 'lifetime';
export type ReferralCards = Record<string, number>;
export interface ReferralInvitee { username: string; created_at: string; status: 'registered' | 'activated' | 'paid'; qualified: boolean; dup_ip: boolean; }
export interface ReferralLevel { name: string; score: number; next_name: string | null; to_next: number; next_at: number | null; }
export interface ReferralMilestone { at: number; card: CardKey; card_label: string; card_value?: number; reached: boolean; }
export interface ReferralHero { card: CardKey; card_label: string; card_value: number; at: number; reached: boolean; remaining: number; percent: number; }
export interface ReferralOverview {
  code: string;
  invited_total: number;
  qualified_reg: number;
  qualified_paid: number;
  earned: ReferralCards;
  redeemed: ReferralCards;
  available: ReferralCards;
  has_redeemable: boolean;
  level: ReferralLevel;
  next_milestone: { at: number; remaining: number; card: CardKey; card_label: string; card_value?: number } | null;
  hero?: ReferralHero;
  ladder: ReferralMilestone[];
  weekly: { count: number; target: number; remaining: number; days_left: number };
  invited: ReferralInvitee[];
  suspicious: boolean;
  card_days: ReferralCards;
  card_label: Record<string, string>;
}
export interface ReferralLeaderRow { rank: number; name: string; count: number; }
export interface CampaignPrize { tier: string; label: string; emoji: string; }
export interface CampaignBoardRow { rank: number; name: string; count: number; prize: CampaignPrize | null; }
export interface CampaignMe { count: number; rank: number | null; prize: CampaignPrize | null; to_next: number; to_top10: number; }
export interface ReferralCampaign {
  active: boolean; upcoming: boolean; ended: boolean;
  title: string; start: string; end: string;
  ends_in: number; starts_in: number;
  prizes: { ranks: string; label: string; emoji: string }[];
  leaderboard: CampaignBoardRow[];
  me: CampaignMe | null;
}
export async function fetchCampaign(): Promise<ReferralCampaign | null> {
  try { return await apiGet<ReferralCampaign>('/api/referral/campaign'); } catch { return null; }
}
export interface RedeemResult { ok: boolean; status?: 'granted' | 'pending'; message?: string; days?: number; card_label?: string; membership?: Membership; }
export async function fetchReferral(): Promise<ReferralOverview> {
  return apiGet<ReferralOverview>('/api/me/referral');
}

// ===== 轻互动：资讯表态（看多/看空）+ 收藏 =====
export interface NewsReaction { bull: number; bear: number; mine: 'bull' | 'bear' | null; }
export interface BookmarkItem { message_id: string; title: string; topic: string; url: string; symbol: string; created_at: string; }
export async function reactNews(messageId: string, stance: 'bull' | 'bear'): Promise<NewsReaction> {
  return apiPost<NewsReaction>('/api/news/react', { message_id: messageId, stance });
}
export async function fetchReactions(ids: string[]): Promise<Record<string, NewsReaction>> {
  try { const r = await apiPost<{ reactions: Record<string, NewsReaction> }>('/api/news/reactions', { ids }); return r.reactions || {}; } catch { return {}; }
}
export async function toggleBookmark(m: { message_id: string; title?: string; topic?: string; url?: string; symbol?: string }): Promise<{ bookmarked: boolean; count: number }> {
  return apiPost<{ bookmarked: boolean; count: number }>('/api/me/bookmark', m);
}
export async function fetchBookmarks(): Promise<BookmarkItem[]> {
  try { const r = await apiGet<{ items: BookmarkItem[] }>('/api/me/bookmarks'); return r.items || []; } catch { return []; }
}

// ===== 「我们提前发现的」量化战绩 =====
export interface TrackHit { date: string; name: string; kind: 'sector' | 'stock'; pct?: number; lead_hours?: number; evidence?: number; reason?: string; signals?: Array<{ id?: string; url?: string; title?: string; topic?: string; lead?: string }>; }
export interface TrackRecord { hit_count: number; avg_lead_hours: number; max_lead_hours: number; sector_hits: number; stock_hits: number; days_covered: number; days: number; recent: TrackHit[]; personal?: { hit_count: number; avg_lead_hours?: number; matched: TrackHit[] }; }
export async function fetchTrackRecord(): Promise<TrackRecord | null> {
  try { return await apiGet<TrackRecord>('/api/track-record'); } catch { return null; }
}
export async function fetchReferralLeaderboard(): Promise<ReferralLeaderRow[]> {
  try { const r = await apiGet<{ items: ReferralLeaderRow[] }>('/api/referral/leaderboard'); return r.items || []; } catch { return []; }
}
export async function redeemReferralCard(cardType: CardKey): Promise<RedeemResult> {
  return apiPost<RedeemResult>('/api/me/referral/redeem', { card_type: cardType });
}

export async function fetchMe(): Promise<User> {
  const resp = await apiGet<AuthUser>('/api/auth/me');
  return mapAuthUser(resp);
}

// 拉取当前用户的会员状态（体验期 / 尊享会员 + 剩余天数）。失败返回 null（按体验期处理）。
export async function fetchMembership(): Promise<Membership | null> {
  try {
    const resp = await apiGet<AuthUser>('/api/auth/me');
    return resp.membership ?? null;
  } catch { return null; }
}

// 拉取当前账号原始信息（含 role / membership），失败返回 null。
export async function fetchAccount(): Promise<AuthUser | null> {
  try { return await apiGet<AuthUser>('/api/auth/me'); } catch { return null; }
}

// 兑换会员码：成功返回 {tier, days, membership}；失败由 apiClient 抛出（含后端 detail 文案），调用方 catch 显示。
export async function redeemCode(code: string): Promise<{ ok: boolean; tier: string; days: number; trial?: boolean; membership: Membership; already?: boolean; message?: string }> {
  return apiPost<{ ok: boolean; tier: string; days: number; trial?: boolean; membership: Membership; already?: boolean; message?: string }>('/api/membership/redeem', { code });
}

// 领取「登录送 3 天体验会员」：成功返回 {days, membership}；失败由 apiClient 抛出（含后端 detail 文案）。
export async function claimTrial(): Promise<{ ok: boolean; days: number; membership: Membership }> {
  return apiPost<{ ok: boolean; days: number; membership: Membership }>('/api/membership/claim-trial', {});
}

// iFinD 专业数据（同花顺，仅白名单账号）：A 股实时行情 + 基本面。
export interface IfindRow { code: string; time?: string; latest?: number; changeRatio?: number; open?: number; high?: number; low?: number; amount?: number; volume?: number; pe_ttm?: number; pb?: number; totalCapital?: number; turnoverRatio?: number; [k: string]: any; }
export interface IfindQuoteResult { ok: boolean; rows: IfindRow[]; skipped?: string[]; error?: string; }
export async function fetchIfindQuote(codes: string): Promise<IfindQuoteResult> {
  try { return await apiGet<IfindQuoteResult>('/api/ifind/quote', { params: { codes } }); }
  catch (e: any) { return { ok: false, rows: [], error: e?.response?.data?.detail || e?.message || '请求失败' }; }
}

// 连续看复盘签到：打开当日复盘即记一次（幂等）。返回连续/累计 + 本次新达成的里程碑奖励。
export interface CheckinMilestone { days: number; reward_days: number; membership?: Membership | null; }
export interface CheckinResult { ok: boolean; date?: string; first_today?: boolean; streak: number; total: number; longest: number; milestone?: CheckinMilestone | null; }
export async function checkin(): Promise<CheckinResult | null> {
  try { return await apiPost<CheckinResult>('/api/checkin', {}); } catch { return null; }
}
export interface CheckinStatus { checked_today: boolean; streak: number; total: number; longest: number; next_milestone: number | null; days_to_next: number | null; next_reward_days?: number | null; milestones: { days: number; reward_days: number; reached: boolean }[]; }
export async function fetchCheckinStatus(): Promise<CheckinStatus | null> {
  try { return await apiGet<CheckinStatus>('/api/checkin/me'); } catch { return null; }
}

// 购买/收款配置（套餐价格 + 收款码是否就绪）
export interface PayPackage { key: string; label: string; days: number; price: number; orig?: number; }
export interface PaymentConfig { enabled: boolean; note: string; packages: PayPackage[]; wechat: boolean; alipay: boolean; pkg_qr?: Record<string, { wechat: boolean; alipay: boolean }>; }
export async function fetchPaymentConfig(): Promise<PaymentConfig | null> {
  try { return await apiGet<PaymentConfig>('/api/payment-config'); } catch { return null; }
}

// ===== 后台私信（用户 ↔ 管理员） =====
export interface SupportMessage { id: number; sender: 'user' | 'admin'; content: string; created_at: string; }
export async function fetchSupportThread(): Promise<SupportMessage[]> {
  try { const r = await apiGet<{ messages: SupportMessage[] }>('/api/support/thread'); return r.messages || []; } catch { return []; }
}
export async function sendSupport(content: string): Promise<SupportMessage | null> {
  try { const r = await apiPost<{ message: SupportMessage }>('/api/support/send', { content }); return r.message || null; } catch { return null; }
}
export async function fetchSupportUnread(): Promise<number> {
  try { const r = await apiGet<{ unread: number }>('/api/support/unread'); return r.unread || 0; } catch { return 0; }
}

export function logout(): void {
  clearToken();
}
