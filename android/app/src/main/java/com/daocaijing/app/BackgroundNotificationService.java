package com.daocaijing.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Android 后台提醒兜底：FCM 负责实时到达，前台服务保留轮询作为网络/推送异常时的兜底。
 */
public class BackgroundNotificationService extends Service {
    public static final String ACTION_STOP = "com.daocaijing.app.action.STOP_BACKGROUND";
    public static final String PREFS = "daocaijing_background_notifications";
    public static final String KEY_ENABLED = "enabled";
    public static final String KEY_MODE = "mode";
    public static final String KEY_TOPICS = "topics";
    public static final String KEY_KEYWORDS = "keywords";
    public static final String KEY_WATCHLIST = "watchlist";
    public static final String KEY_INITIALIZED = "initialized";
    public static final String KEY_SEEN = "seen_ids";
    public static final String KEY_LAST_SYNC = "last_sync_at";
    public static final String KEY_LAST_ERROR = "last_error";

    private static final String API_BASE = "https://daocaijing.com";
    private static final String WEB_TOKEN = "dfw_2vQ9_k7Rm";
    private static final String SERVICE_CHANNEL = "daocaijing_background_service";
    public static final String NEWS_CHANNEL = "daocaijing_background_news";
    private static final int SERVICE_NOTIFICATION_ID = 7001;
    private static final long POLL_INTERVAL_SECONDS = 90L;

    private ScheduledExecutorService executor;
    private volatile boolean polling;
    private volatile boolean scheduled;
    private volatile boolean pollHadError;
    private volatile boolean pollSucceeded;

    static void saveFilters(Context context, String mode, String topics, String keywords, String watchlist) {
        SharedPreferences.Editor editor = context.getSharedPreferences(PREFS, MODE_PRIVATE).edit();
        if (mode != null) editor.putString(KEY_MODE, mode);
        if (topics != null) editor.putString(KEY_TOPICS, topics);
        if (keywords != null) editor.putString(KEY_KEYWORDS, keywords);
        if (watchlist != null) editor.putString(KEY_WATCHLIST, watchlist);
        editor.apply();
    }

    static boolean isEnabled(Context context) {
        return context.getSharedPreferences(PREFS, MODE_PRIVATE).getBoolean(KEY_ENABLED, false);
    }

    static void setEnabled(Context context, boolean enabled) {
        context.getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean(KEY_ENABLED, enabled).apply();
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannels();
        executor = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread thread = new Thread(r, "daocaijing-background-poll");
            thread.setDaemon(true);
            return thread;
        });
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            setEnabled(this, false);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!isEnabled(this)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        // startForeground 必须在服务启动后的短时间内调用，否则 Android 会回收服务。
        startForeground(SERVICE_NOTIFICATION_ID, buildServiceNotification());
        if (executor != null && executor.isShutdown()) {
            executor = Executors.newSingleThreadScheduledExecutor();
        }
        if (executor != null && !scheduled) {
            executor.scheduleAtFixedRate(this::pollOnce, 0, POLL_INTERVAL_SECONDS, TimeUnit.SECONDS);
            scheduled = true;
        }
        // dataSync 前台服务受 Android 15/16 的时长上限约束；系统终止后不应
        // 通过 START_STICKY 反复拉起，避免形成耗电和后台启动违规循环。
        return START_NOT_STICKY;
    }

    /**
     * Android 15+ 在 dataSync 前台服务达到系统时长上限时回调。必须尽快
     * 停止服务，否则系统会将其视为未处理超时并触发异常/ANR。
     */
    @Override
    public void onTimeout(int startId, int fgsType) {
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                .putBoolean(KEY_ENABLED, false)
                .putString(KEY_LAST_ERROR, "系统已达到后台运行时限，请重新开启提醒")
                .apply();
        if (executor != null) executor.shutdownNow();
        stopSelf(startId);
    }

    private void pollOnce() {
        if (polling || !isEnabled(this)) return;
        polling = true;
        pollHadError = false;
        pollSucceeded = false;
        try {
            if (!hasNetwork()) {
                recordPoll("当前没有可用网络，已自动重试");
                return;
            }
            List<MessageItem> incoming = new ArrayList<>();
            addRealtimeMessages(incoming);
            addResearchReports(incoming);
            addInstitutionNotes(incoming);
            processIncoming(incoming);
            recordPoll(pollHadError ? "部分资讯源暂不可用，已自动重试" : "");
        } catch (Exception ignored) {
            recordPoll("后台检查失败，已自动重试");
        } finally {
            polling = false;
        }
    }

    private void addRealtimeMessages(List<MessageItem> out) {
        try {
            JSONObject response = fetchJson("/api/realtime/messages?limit=200&w=" + WEB_TOKEN);
            JSONArray messages = response.optJSONArray("messages");
            if (messages == null) return;
            for (int i = 0; i < messages.length(); i++) {
                JSONObject item = messages.optJSONObject(i);
                if (item != null) out.add(MessageItem.fromRealtime(item));
            }
        } catch (Exception ignored) { pollHadError = true; }
    }

    private void addResearchReports(List<MessageItem> out) {
        try {
            JSONObject response = fetchJson("/api/research/wire?limit=80&w=" + WEB_TOKEN);
            JSONArray items = response.optJSONArray("items");
            if (items == null) return;
            for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.optJSONObject(i);
                if (item == null) continue;
                String id = item.optString("id", item.optString("file_id", ""));
                String title = item.optString("title", "研报更新");
                String content = joinValues(item.optString("org", ""), item.optString("hashtag", ""));
                out.add(new MessageItem("report:" + (id.isEmpty() ? title : id), title, content, "研报", "info",
                        firstArrayValue(item.optJSONArray("instruments")), arrayStrings(item.optJSONArray("instruments")),
                        item.optString("preview_url", ""), item.optString("created_at", item.optString("date", ""))));
            }
        } catch (Exception ignored) { pollHadError = true; }
    }

    private void addInstitutionNotes(List<MessageItem> out) {
        try {
            JSONObject response = fetchJson("/api/zsxq/stream?limit=20&w=" + WEB_TOKEN);
            JSONArray items = response.optJSONArray("items");
            if (items == null) return;
            for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.optJSONObject(i);
                if (item == null) continue;
                String id = item.optString("id", "");
                String title = item.optString("title", "机构纪要更新");
                String body = item.optString("text", "").replaceAll("\\s+", " ");
                String severity = item.optBoolean("digested", false) ? "warning" : "info";
                out.add(new MessageItem("zsxq:" + (id.isEmpty() ? title : id), title, body, "机构纪要", severity,
                        "", arrayStrings(item.optJSONArray("tags")), "", item.optString("create_time", item.optString("date", ""))));
            }
        } catch (Exception ignored) { pollHadError = true; }
    }

    private JSONObject fetchJson(String path) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(API_BASE + path).openConnection();
        connection.setRequestMethod("GET");
        connection.setConnectTimeout(12000);
        connection.setReadTimeout(18000);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("X-DF-Web", WEB_TOKEN);
        try {
            int responseCode = connection.getResponseCode();
            if (responseCode < 200 || responseCode >= 300) {
                pollHadError = true;
                return new JSONObject();
            }
            pollSucceeded = true;
            InputStream stream = connection.getInputStream();
            BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8));
            StringBuilder body = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) body.append(line);
            reader.close();
            return new JSONObject(body.toString());
        } finally {
            connection.disconnect();
        }
    }

    private boolean hasNetwork() {
        ConnectivityManager manager = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (manager == null) return true;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            Network network = manager.getActiveNetwork();
            NetworkCapabilities capabilities = network == null ? null : manager.getNetworkCapabilities(network);
            return capabilities != null && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
        }
        android.net.NetworkInfo info = manager.getActiveNetworkInfo();
        return info != null && info.isConnected();
    }

    private void recordPoll(String error) {
        SharedPreferences.Editor editor = getSharedPreferences(PREFS, MODE_PRIVATE).edit();
        if (pollSucceeded) editor.putLong(KEY_LAST_SYNC, System.currentTimeMillis());
        editor.putString(KEY_LAST_ERROR, error == null ? "" : error).apply();
        NotificationManagerCompat.from(this).notify(SERVICE_NOTIFICATION_ID, buildServiceNotification());
    }

    private void processIncoming(List<MessageItem> incoming) {
        if (incoming.isEmpty()) return;
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        Set<String> seen = loadSeen(prefs);
        boolean initialized = prefs.getBoolean(KEY_INITIALIZED, false);
        if (!initialized) {
            for (MessageItem item : incoming) seen.add(item.id);
            saveSeen(prefs, seen, true);
            return;
        }

        List<MessageItem> fresh = new ArrayList<>();
        for (MessageItem item : incoming) {
            if (!seen.contains(item.id)) {
                seen.add(item.id);
                if (matchesFilters(item, prefs)) fresh.add(item);
            }
        }
        // 新消息按旧到新通知，且一次最多 6 条，防止服务恢复时刷屏。
        Collections.reverse(fresh);
        int count = 0;
        for (MessageItem item : fresh) {
            if (count++ >= 6) break;
            notifyNews(item);
        }
        saveSeen(prefs, seen, false);
    }

    private boolean matchesFilters(MessageItem item, SharedPreferences prefs) {
        String mode = prefs.getString(KEY_MODE, "all");
        if ("off".equals(mode)) return false;
        Set<String> topics = new HashSet<>(readArray(prefs.getString(KEY_TOPICS, "[\"快讯\",\"文章\",\"研报\",\"机构纪要\"]")));
        if (!topics.contains(normalizeTopic(item.topic))) return false;
        List<String> keywords = readArray(prefs.getString(KEY_KEYWORDS, "[]"));
        if (!keywords.isEmpty()) {
            String haystack = (item.title + " " + item.content + " " + item.symbol + " " + String.join(" ", item.tags)).toLowerCase(Locale.ROOT);
            boolean hit = false;
            for (String keyword : keywords) {
                if (haystack.contains(keyword.toLowerCase(Locale.ROOT))) { hit = true; break; }
            }
            if (!hit) return false;
        }
        if ("all".equals(mode)) return true;
        if ("critical".equals(item.severity) || "warning".equals(item.severity)) return true;
        if ("success".equals(item.severity)) {
            List<String> watchlist = readArray(prefs.getString(KEY_WATCHLIST, "[]"));
            return !item.symbol.isEmpty() && watchlist.contains(item.symbol);
        }
        return false;
    }

    private static String normalizeTopic(String topic) {
        if ("深度文章".equals(topic)) return "文章";
        if ("投行研报".equals(topic)) return "研报";
        if ("纪要".equals(topic) || "机构调研纪要".equals(topic)) return "机构纪要";
        return topic;
    }

    private void notifyNews(MessageItem item) {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        Intent intent = new Intent(this, MainActivity.class)
                .setAction("com.daocaijing.app.action.OPEN_NOTIFICATION")
                .putExtra("df_message_id", item.id)
                .putExtra("df_message_title", item.title)
                .putExtra("df_message_content", item.content)
                .putExtra("df_message_topic", item.topic)
                .putExtra("df_message_url", item.url);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pendingIntent = PendingIntent.getActivity(this, item.id.hashCode(), intent, flags);
        Notification notification = new NotificationCompat.Builder(this, NEWS_CHANNEL)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle("稻财经 · " + item.topic)
                .setContentText(item.title)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(item.content.isEmpty() ? item.title : item.content))
                .setContentIntent(pendingIntent)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setDefaults(NotificationCompat.DEFAULT_ALL)
                .build();
        NotificationManagerCompat.from(this).notify(Math.abs(item.id.hashCode()), notification);
    }

    /** FCM 数据消息入口：复用同一通知频道和点击协议，避免 App 内外两套通知体验分叉。 */
    public static void showRemoteNotification(Context context, String id, String title, String content,
                                              String topic, String severity, String symbol, String url) {
        if (!isEnabled(context)) return;
        if (Build.VERSION.SDK_INT >= 33
                && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        ensureNotificationChannels(context);
        String safeId = (id == null || id.isEmpty()) ? "fcm-" + System.currentTimeMillis() : id;
        Intent intent = new Intent(context, MainActivity.class)
                .setAction("com.daocaijing.app.action.OPEN_NOTIFICATION")
                .putExtra("df_message_id", safeId)
                .putExtra("df_message_title", title)
                .putExtra("df_message_content", content)
                .putExtra("df_message_topic", topic)
                .putExtra("df_message_url", url);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pendingIntent = PendingIntent.getActivity(context, safeId.hashCode(), intent, flags);
        Notification notification = new NotificationCompat.Builder(context, NEWS_CHANNEL)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle("稻财经 · " + (topic == null || topic.isEmpty() ? "快讯" : topic))
                .setContentText(title == null || title.isEmpty() ? "资讯更新" : title)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(content == null || content.isEmpty() ? title : content))
                .setContentIntent(pendingIntent)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setDefaults(NotificationCompat.DEFAULT_ALL)
                .build();
        NotificationManagerCompat.from(context).notify(Math.abs(safeId.hashCode()), notification);
        markSeen(context, safeId);
    }

    private Notification buildServiceNotification() {
        Intent intent = new Intent(this, MainActivity.class);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pendingIntent = PendingIntent.getActivity(this, 7001, intent, flags);
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        long lastSync = prefs.getLong(KEY_LAST_SYNC, 0L);
        String lastError = prefs.getString(KEY_LAST_ERROR, "");
        String content;
        if (!lastError.isEmpty()) {
            content = lastError + " · 下次自动检查";
        } else if (lastSync > 0L) {
            content = "最近检查 " + formatTime(lastSync) + " · FCM 实时 · 轮询兜底";
        } else {
            content = "FCM 实时推送 · 正在准备轮询兜底";
        }
        return new NotificationCompat.Builder(this, SERVICE_CHANNEL)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle("稻财经后台提醒已开启")
                .setContentText(content)
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .build();
    }

    private static String formatTime(long timestamp) {
        return new SimpleDateFormat("HH:mm", Locale.CHINA).format(new Date(timestamp));
    }

    private void createNotificationChannels() {
        ensureNotificationChannels(this);
    }

    private static void ensureNotificationChannels(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        manager.createNotificationChannel(new NotificationChannel(SERVICE_CHANNEL, "后台提醒服务", NotificationManager.IMPORTANCE_LOW));
        manager.createNotificationChannel(new NotificationChannel(NEWS_CHANNEL, "资讯提醒", NotificationManager.IMPORTANCE_HIGH));
    }

    private static void markSeen(Context context, String id) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, MODE_PRIVATE);
        Set<String> seen = loadSeen(prefs);
        seen.add(id);
        saveSeen(prefs, seen, prefs.getBoolean(KEY_INITIALIZED, false));
    }

    private static Set<String> loadSeen(SharedPreferences prefs) {
        return new LinkedHashSet<>(readArray(prefs.getString(KEY_SEEN, "[]")));
    }

    private static void saveSeen(SharedPreferences prefs, Set<String> seen, boolean initialized) {
        List<String> values = new ArrayList<>(seen);
        if (values.size() > 500) values = values.subList(values.size() - 500, values.size());
        JSONArray array = new JSONArray();
        for (String value : values) array.put(value);
        prefs.edit().putString(KEY_SEEN, array.toString()).putBoolean(KEY_INITIALIZED, initialized || prefs.getBoolean(KEY_INITIALIZED, false)).apply();
    }

    private static List<String> readArray(String raw) {
        List<String> values = new ArrayList<>();
        try {
            JSONArray array = new JSONArray(raw == null ? "[]" : raw);
            for (int i = 0; i < array.length(); i++) {
                String value = array.optString(i, "").trim();
                if (!value.isEmpty()) values.add(value);
            }
        } catch (Exception ignored) { }
        return values;
    }

    private static String arrayToString(JSONArray array) { return array == null ? "" : array.toString(); }
    private static List<String> arrayStrings(JSONArray array) { return readArray(arrayToString(array)); }
    private static String firstArrayValue(JSONArray array) { return array != null && array.length() > 0 ? array.optString(0, "") : ""; }
    private static String joinValues(String a, String b) { return (a + (a.isEmpty() || b.isEmpty() ? "" : " · ") + b).trim(); }

    @Override public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onDestroy() {
        scheduled = false;
        if (executor != null) executor.shutdownNow();
        super.onDestroy();
    }

    private static final class MessageItem {
        final String id, title, content, topic, severity, symbol, url, createdAt;
        final List<String> tags;
        MessageItem(String id, String title, String content, String topic, String severity, String symbol, List<String> tags, String url, String createdAt) {
            this.id = id; this.title = title; this.content = content; this.topic = topic; this.severity = severity; this.symbol = symbol; this.tags = tags; this.url = url; this.createdAt = createdAt;
        }
        static MessageItem fromRealtime(JSONObject item) {
            return new MessageItem(item.optString("id", item.optString("title", "message")), item.optString("title", "新消息"), item.optString("content", ""), item.optString("topic", "快讯"), item.optString("severity", "info"), item.optString("symbol", ""), arrayStrings(item.optJSONArray("tags")), item.optString("url", ""), item.optString("created_at", ""));
        }
    }
}
