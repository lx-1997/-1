package com.daocaijing.app;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import java.util.Map;

/**
 * FCM data-message receiver. The server performs the user's topic/keyword
 * filtering; this service only renders the already-authorized message and
 * stores token rotations for the WebView bridge to register on next launch.
 */
public class DaocaijingFirebaseMessagingService extends FirebaseMessagingService {
    public static final String KEY_FCM_TOKEN = "fcm_token";

    @Override
    public void onNewToken(String token) {
        if (token == null || token.trim().isEmpty()) return;
        getSharedPreferences(BackgroundNotificationService.PREFS, MODE_PRIVATE)
                .edit().putString(KEY_FCM_TOKEN, token.trim()).apply();
    }

    @Override
    public void onMessageReceived(RemoteMessage message) {
        Map<String, String> data = message.getData();
        String id = value(data, "message_id", message.getMessageId());
        // 研报/机构纪要同时由本地轮询兜底；服务端下发稳定 dedupe_id 后，
        // 两条路径共用同一个 seen key，避免 FCM + 轮询重复弹窗。
        String dedupeId = value(data, "dedupe_id", id);
        String title = value(data, "title", "资讯更新");
        String body = value(data, "body", "点击查看资讯详情");
        String topic = value(data, "topic", "快讯");
        String severity = value(data, "severity", "info");
        String symbol = value(data, "symbol", "");
        String url = value(data, "url", "");
        BackgroundNotificationService.showRemoteNotification(this, dedupeId, title, body, topic, severity, symbol, url);
    }

    private static String value(Map<String, String> data, String key, String fallback) {
        String value = data == null ? null : data.get(key);
        return value == null || value.trim().isEmpty() ? (fallback == null ? "" : fallback) : value;
    }
}
