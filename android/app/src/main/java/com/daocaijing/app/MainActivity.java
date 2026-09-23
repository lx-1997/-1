package com.daocaijing.app;

import android.content.Intent;
import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

import org.json.JSONObject;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(NativeBackgroundPlugin.class);
        super.onCreate(savedInstanceState);
        dispatchNotificationIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        dispatchNotificationIntent(intent);
    }

    private void dispatchNotificationIntent(Intent intent) {
        if (intent == null || !"com.daocaijing.app.action.OPEN_NOTIFICATION".equals(intent.getAction()) || getBridge() == null) return;
        try {
            JSONObject detail = new JSONObject();
            detail.put("id", intent.getStringExtra("df_message_id"));
            detail.put("title", intent.getStringExtra("df_message_title"));
            detail.put("content", intent.getStringExtra("df_message_content"));
            detail.put("topic", intent.getStringExtra("df_message_topic"));
            detail.put("url", intent.getStringExtra("df_message_url"));
            String script = "window.dispatchEvent(new CustomEvent('df:native-notification',{detail:" + detail + "}));";
            getBridge().getWebView().postDelayed(() -> getBridge().getWebView().evaluateJavascript(script, null), 650);
        } catch (Exception ignored) { }
    }
}
