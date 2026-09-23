package com.daocaijing.app;

import android.Manifest;
import android.content.Intent;
import android.net.Uri;
import android.os.PowerManager;
import android.provider.Settings;
import android.os.Build;

import androidx.core.content.ContextCompat;

import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.JSObject;
import com.google.firebase.messaging.FirebaseMessaging;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import com.getcapacitor.PluginMethod;

import org.json.JSONArray;
import org.json.JSONObject;

@CapacitorPlugin(
        name = "NativeBackground",
        permissions = {
                @Permission(alias = "notifications", strings = { Manifest.permission.POST_NOTIFICATIONS })
        }
)
public class NativeBackgroundPlugin extends Plugin {
    @PluginMethod
    public void setFilters(PluginCall call) {
        BackgroundNotificationService.saveFilters(
                getContext(),
                call.getString("mode", "all"),
                jsonArray(call.getData().optJSONArray("topics")),
                jsonArray(call.getData().optJSONArray("keywords")),
                jsonArray(call.getData().optJSONArray("watchlist"))
        );
        call.resolve();
    }

    @PluginMethod
    public void start(PluginCall call) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && getPermissionState("notifications") != PermissionState.GRANTED) {
            requestPermissionForAlias("notifications", call, "notificationPermissionCallback");
            return;
        }
        startService(call);
    }

    @PermissionCallback
    private void notificationPermissionCallback(PluginCall call) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && getPermissionState("notifications") != PermissionState.GRANTED) {
            call.reject("需要允许通知权限，才能开启后台提醒");
            return;
        }
        startService(call);
    }

    private void startService(PluginCall call) {
        BackgroundNotificationService.setEnabled(getContext(), true);
        Intent intent = new Intent(getContext(), BackgroundNotificationService.class);
        ContextCompat.startForegroundService(getContext(), intent);
        JSObject result = new JSObject();
        result.put("enabled", true);
        result.put("permission", "granted");
        call.resolve(result);
    }

    @PluginMethod
    public void stop(PluginCall call) {
        BackgroundNotificationService.setEnabled(getContext(), false);
        getContext().stopService(new Intent(getContext(), BackgroundNotificationService.class));
        JSObject result = new JSObject();
        result.put("enabled", false);
        call.resolve(result);
    }

    @PluginMethod
    public void status(PluginCall call) {
        JSObject result = new JSObject();
        result.put("enabled", BackgroundNotificationService.isEnabled(getContext()));
        result.put("permission", Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
                ? "granted"
                : getPermissionState("notifications").toString());
        android.content.SharedPreferences prefs = getContext().getSharedPreferences(BackgroundNotificationService.PREFS, android.content.Context.MODE_PRIVATE);
        result.put("lastSyncAt", prefs.getLong(BackgroundNotificationService.KEY_LAST_SYNC, 0L));
        result.put("lastError", prefs.getString(BackgroundNotificationService.KEY_LAST_ERROR, ""));
        result.put("fcmToken", prefs.getString(DaocaijingFirebaseMessagingService.KEY_FCM_TOKEN, ""));
        result.put("batteryOptimizationIgnored", isBatteryOptimizationIgnored());
        call.resolve(result);
    }

    /** 获取 FCM registration token；没有 google-services.json 时优雅返回 unavailable。 */
    @PluginMethod
    public void getPushToken(PluginCall call) {
        try {
            FirebaseMessaging.getInstance().getToken().addOnCompleteListener(task -> {
                if (!task.isSuccessful() || task.getResult() == null || task.getResult().trim().isEmpty()) {
                    call.resolve(new JSObject().put("available", false).put("token", ""));
                    return;
                }
                String token = task.getResult().trim();
                getContext().getSharedPreferences(BackgroundNotificationService.PREFS, android.content.Context.MODE_PRIVATE)
                        .edit().putString(DaocaijingFirebaseMessagingService.KEY_FCM_TOKEN, token).apply();
                call.resolve(new JSObject().put("available", true).put("token", token));
            });
        } catch (Exception ignored) {
            call.resolve(new JSObject().put("available", false).put("token", ""));
        }
    }

    /**
     * 打开系统电池优化列表，由用户自行选择策略。应用不声明
     * REQUEST_IGNORE_BATTERY_OPTIMIZATIONS，避免触发 Google Play 的受限权限审核。
     */
    @PluginMethod
    public void requestBatteryOptimization(PluginCall call) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M || isBatteryOptimizationIgnored()) {
            call.resolve(new JSObject().put("ignored", true).put("opened", false));
            return;
        }
        try {
            Intent intent = new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
            call.resolve(new JSObject().put("ignored", false).put("opened", true).put("manual", true));
        } catch (Exception ignored) {
            call.reject("系统未开放电池策略设置入口");
        }
    }

    @PluginMethod
    public void openBatterySettings(PluginCall call) {
        try {
            Intent intent = new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
            call.resolve();
        } catch (Exception failure) {
            call.reject("无法打开系统电池设置");
        }
    }

    private boolean isBatteryOptimizationIgnored() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return true;
        PowerManager manager = (PowerManager) getContext().getSystemService(android.content.Context.POWER_SERVICE);
        return manager == null || manager.isIgnoringBatteryOptimizations(getContext().getPackageName());
    }

    /**
     * 当用户之前拒绝过通知权限时，系统可能不再弹出授权框；此时仍给用户一个
     * 软件内的自助入口，直接跳到本 App 的通知设置页。
     */
    @PluginMethod
    public void openNotificationSettings(PluginCall call) {
        Intent intent;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            intent = new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                    .putExtra(Settings.EXTRA_APP_PACKAGE, getContext().getPackageName());
        } else {
            intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                    .setData(android.net.Uri.parse("package:" + getContext().getPackageName()));
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        getContext().startActivity(intent);
        call.resolve();
    }

    private static String jsonArray(JSONArray value) {
        return value == null ? "[]" : value.toString();
    }
}
