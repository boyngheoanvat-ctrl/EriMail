package vn.eri.login;

import android.app.Activity;
import android.os.Bundle;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.text.InputType;
import android.view.inputmethod.InputMethodManager;
import android.content.Context;

import org.json.JSONObject;
import java.net.HttpURLConnection;
import java.net.URL;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import java.util.UUID;

public final class ERILoginActivity extends Activity {
    private static final String API = "https://eri-login-api.onrender.com";
    private static final String STORE = "eri_license_v1";
    private EditText input;
    private TextView status;
    private Button action;
    private boolean busy = false;
    private SharedPreferences prefs;

    @Override public void onCreate(Bundle savedState) {
        super.onCreate(savedState);
        prefs = getSharedPreferences(STORE, MODE_PRIVATE);
        LinearLayout layout = new LinearLayout(this);
        layout.setPadding(dp(24), dp(30), dp(24), dp(24));
        layout.setBackgroundColor(Color.rgb(10, 17, 35));
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER);
        TextView title = new TextView(this);
        title.setText("ERI LOGIN");
        title.setTextSize(31);
        title.setTextColor(Color.WHITE);
        title.setTypeface(null, Typeface.BOLD);
        title.setGravity(Gravity.CENTER);
        layout.addView(title, params(12));
        TextView subtitle = new TextView(this);
        subtitle.setText("1 KEY / 1 THIẾT BỊ");
        subtitle.setTextSize(16);
        subtitle.setGravity(Gravity.CENTER);
        subtitle.setTextColor(Color.LTGRAY);
        layout.addView(subtitle, params(28));
        input = new EditText(this);
        input.setSingleLine(true);
        input.setHint("ERI-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX");
        input.setTextColor(Color.BLACK);
        input.setHintTextColor(Color.GRAY);
        input.setBackgroundColor(Color.WHITE);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS);
        input.setPadding(dp(10), dp(8), dp(10), dp(8));
        input.setText(prefs.getString("key", ""));
        layout.addView(input, params(16));
        action = new Button(this);
        action.setText("KÍCH HOẠT KEY");
        action.setOnClickListener(v -> check(true));
        layout.addView(action, params(16));
        status = new TextView(this);
        status.setText("Nhập key do ERI cấp.");
        status.setTextColor(Color.WHITE);
        status.setTextSize(14);
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(6), dp(4), dp(6), dp(4));
        layout.addView(status, params(0));
        setContentView(layout);
        if (!prefs.getString("key", "").isEmpty()) check(false);
    }

    private int dp(int x) { return (int) (x * getResources().getDisplayMetrics().density); }
    private LinearLayout.LayoutParams params(int margin) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
        p.bottomMargin = dp(margin);
        return p;
    }

    private String getDevice() {
        String v = prefs.getString("device", "");
        try { UUID.fromString(v); return v; } catch(Exception ignored) {}
        v = UUID.randomUUID().toString();
        prefs.edit().putString("device", v).apply();
        return v;
    }

    private String read(InputStream in) throws Exception {
        if (in == null) return "";
        try(InputStream stream=in; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer=new byte[4096];
            int n;
            while ((n=stream.read(buffer))>=0) {
                if (out.size()>65536) throw new IllegalStateException("API trả về quá nhiều dữ liệu");
                out.write(buffer,0,n);
            }
            return out.toString("UTF-8");
        }
    }

    private void check(boolean activate) {
        if (busy) return;
        final String key = input.getText().toString().trim().toUpperCase(java.util.Locale.US);
        if (key.isEmpty()) { status.setText("Bạn chưa nhập key."); return; }
        busy = true;
        action.setEnabled(false);
        status.setText("Đang kết nối API ERI...");
        final String device=getDevice();
        new Thread(() -> {
            boolean allowed = false;
            String error = "Không xác thực được key.";
            HttpURLConnection con = null;
            try {
                URL url = new URL(API + (activate ? "/api/key/activate" : "/api/key/verify"));
                con = (HttpURLConnection) url.openConnection();
                con.setRequestMethod("POST");
                con.setConnectTimeout(20000);
                con.setReadTimeout(45000);
                con.setDoOutput(true);
                con.setRequestProperty("Content-Type", "application/json");
                JSONObject body = new JSONObject();
                body.put("app_id", getPackageName());
                body.put("device_id", device);
                body.put("key", key);
                byte[] payload = body.toString().getBytes("UTF-8");
                try(OutputStream os=con.getOutputStream()) { os.write(payload); }
                int code=con.getResponseCode();
                JSONObject json = new JSONObject(read(code>=200&&code<300 ? con.getInputStream() : con.getErrorStream()));
                allowed=(code>=200&&code<300)&&json.optBoolean("valid", false);
                error=json.optString("error","HTTP "+code);
                if (!activate && code==409) error="Key chưa kích hoạt. Nhấn KÍCH HOẠT KEY.";
            } catch(Exception e) { error="Lỗi kết nối. Kiểm tra Internet và thử lại."; }
            finally { if(con!=null) con.disconnect(); }
            final boolean ok=allowed;
            final String msg=error;
            runOnUiThread(() -> {
                busy=false;
                action.setEnabled(true);
                if (ok) {
                    prefs.edit().putString("key",key).apply();
                    launchOriginal();
                } else status.setText(msg);
            });
        }).start();
    }

    private void launchOriginal() {
        try {
            ActivityInfo info = getPackageManager().getActivityInfo(getComponentName(), PackageManager.GET_META_DATA);
            if (info.metaData == null) throw new IllegalStateException("Missing original activity");
            String name = info.metaData.getString("eri.target");
            if (name==null || name.trim().isEmpty()) throw new IllegalStateException("Missing launch target");
            Intent intent = new Intent();
            intent.setClassName(getPackageName(), name);
            intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP);
            startActivity(intent);
            finish();
        } catch(Exception ex) {
            status.setText("Không mở được ứng dụng đích: " + ex.getMessage());
        }
    }

    @Override public void onBackPressed() {
        moveTaskToBack(true);
    }
}
