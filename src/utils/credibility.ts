/**
 * 来源可信度分级 —— 全站单一事实来源（消除分析师快答 / 深研 / 圆桌里重复的阈值与文案）。
 * 华尔街看重源质量：研报级 vs 论坛帖。≥0.75 高可信、≥0.5 中、<0.5 存疑。
 */

export type CredibilityTier = 'high' | 'mid' | 'low';

export interface CredibilityMeta {
  label: string;
  tier: CredibilityTier;
}

/** 把 0–1 可信度映射成 {label, tier}；非数字（无分值）返回 null（不显示徽标）。 */
export function credibilityMeta(c?: number | null): CredibilityMeta | null {
  if (typeof c !== 'number') {
    return null;
  }
  if (c >= 0.75) {
    return { label: '高可信', tier: 'high' };
  }
  if (c >= 0.5) {
    return { label: '中', tier: 'mid' };
  }
  return { label: '存疑', tier: 'low' };
}
