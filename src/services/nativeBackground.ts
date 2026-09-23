import { Capacitor, registerPlugin } from '@capacitor/core';
import type { ForegroundPopupMode, ForegroundPopupTopic } from '../utils/foregroundNewsPopup';

interface NativeBackgroundPlugin {
  setFilters(options: {
    mode: ForegroundPopupMode;
    topics: ForegroundPopupTopic[];
    keywords: string[];
    watchlist: string[];
  }): Promise<void>;
  start(): Promise<{ enabled: boolean; permission?: string }>;
  stop(): Promise<{ enabled: boolean }>;
  status(): Promise<{ enabled: boolean; permission?: string; lastSyncAt?: number; lastError?: string; fcmToken?: string; batteryOptimizationIgnored?: boolean }>;
  getPushToken(): Promise<{ available: boolean; token?: string }>;
  requestBatteryOptimization(): Promise<{ ignored: boolean; opened?: boolean; fallback?: boolean }>;
  openBatterySettings(): Promise<void>;
  openNotificationSettings(): Promise<void>;
}

const NativeBackground = registerPlugin<NativeBackgroundPlugin>('NativeBackground');

export function nativeBackgroundSupported(): boolean {
  return Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android';
}

export async function setNativeBackgroundFilters(options: Parameters<NativeBackgroundPlugin['setFilters']>[0]): Promise<void> {
  if (!nativeBackgroundSupported()) return;
  await NativeBackground.setFilters(options);
}

export async function startNativeBackground(): Promise<{ enabled: boolean; permission?: string }> {
  if (!nativeBackgroundSupported()) return { enabled: false, permission: 'unsupported' };
  return NativeBackground.start();
}

export async function stopNativeBackground(): Promise<{ enabled: boolean }> {
  if (!nativeBackgroundSupported()) return { enabled: false };
  return NativeBackground.stop();
}

export async function getNativeBackgroundStatus(): Promise<{ enabled: boolean; permission?: string; lastSyncAt?: number; lastError?: string; fcmToken?: string; batteryOptimizationIgnored?: boolean }> {
  if (!nativeBackgroundSupported()) return { enabled: false, permission: 'unsupported', batteryOptimizationIgnored: false };
  return NativeBackground.status();
}

export async function getNativePushToken(): Promise<{ available: boolean; token?: string }> {
  if (!nativeBackgroundSupported()) return { available: false };
  return NativeBackground.getPushToken();
}

export async function requestNativeBatteryOptimization(): Promise<{ ignored: boolean; opened?: boolean; fallback?: boolean }> {
  if (!nativeBackgroundSupported()) return { ignored: false, opened: false };
  return NativeBackground.requestBatteryOptimization();
}

export async function openNativeBatterySettings(): Promise<void> {
  if (!nativeBackgroundSupported()) return;
  await NativeBackground.openBatterySettings();
}

export async function openNativeNotificationSettings(): Promise<void> {
  if (!nativeBackgroundSupported()) return;
  await NativeBackground.openNotificationSettings();
}
