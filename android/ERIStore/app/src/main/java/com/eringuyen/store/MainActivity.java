package com.eringuyen.store;

import android.app.*;
import android.os.*;
import android.graphics.Typeface;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {
    private static final String API = "https://eri-api-dag8.onrender.com";
    private LinearLayout list;
    private String category = "products";
    private final String[] labels = {"AI Plus", "Chứng chỉ", "Mod Game"};
    private final String[] endpoints = {"products", "certificates", "mods"};

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        showCatalog(0);
    }
    private TextView text(String value, int size, boolean bold) {
        TextView v = new TextView(this);
        v.setText(value);
        v.setTextSize(size);
        v.setPadding(12, 12, 12, 12);
        if (bold) v.setTypeface(null, Typeface.BOLD);
        return v;
    }
    private void showCatalog(int tab) {
        category = endpoints[tab];
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(12, 18, 12, 12);
        root.addView(text("ERI STORE", 26, true));
        LinearLayout tabs = new LinearLayout(this);
        tabs.setOrientation(LinearLayout.HORIZONTAL);
        for (int i = 0; i < labels.length; i++) {
            final int selected = i;
            Button b = new Button(this);
            b.setText(labels[i]);
            tabs.addView(b, new LinearLayout.LayoutParams(0, -2, 1));
            b.setOnClickListener(v -> showCatalog(selected));
        }
        root.addView(tabs);
        Button refresh = new Button(this);
        refresh.setText("Làm mới");
        root.addView(refresh);
        ScrollView scroll = new ScrollView(this);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(list);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        final String selectedCategory = category;
        refresh.setOnClickListener(v -> load(selectedCategory));
        load(selectedCategory);
    }
    private void load(String selected) {
        list.removeAllViews();
        list.addView(text("Đang tải sản phẩm...", 16, false));
        new Thread(() -> {
            try {
                JSONObject result = request("/api/v1/" + selected, null);
                JSONArray items = result.getJSONArray("items");
                runOnUiThread(() -> {
                    if (!selected.equals(category)) return;
                    list.removeAllViews();
                    if (items.length() == 0) list.addView(text("Chưa có sản phẩm được đăng bán.", 16, false));
                    for (int i = 0; i < items.length(); i++) {
                        JSONObject item = items.optJSONObject(i);
                        if (item == null) continue;
                        addItem(item, selected);
                    }
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    if (!selected.equals(category)) return;
                    list.removeAllViews();
                    list.addView(text("Không tải được sản phẩm: " + e.getMessage(), 15, false));
                });
            }
        }).start();
    }
    private void addItem(JSONObject item, String selected) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(12, 16, 12, 16);
        card.addView(text(item.optString("name"), 20, true));
        String detail = selected.equals("mods")
                ? "Phiên bản " + item.optString("version") : item.optString("description");
        card.addView(text(detail, 14, false));
        card.addView(text(String.format(java.util.Locale.forLanguageTag("vi-VN"), "%,d ₫", item.optLong("price_vnd")), 18, true));
        Button buy = new Button(this);
        buy.setText("Mua ngay");
        card.addView(buy);
        buy.setOnClickListener(v -> buy(item, selected, buy));
        list.addView(card);
        View line = new View(this);
        line.setBackgroundColor(0xffdddddd);
        list.addView(line, new LinearLayout.LayoutParams(-1, 1));
    }
    private void buy(JSONObject item, String selected, Button button) {
        button.setEnabled(false);
        new Thread(() -> {
            try {
                JSONObject payload = new JSONObject();
                payload.put("kind", selected.equals("products") ? "ai" : selected.equals("certificates") ? "certificate" : "mod");
                payload.put("product_id", item.getInt("id"));
                JSONObject order = request("/api/v1/orders", payload);
                runOnUiThread(() -> showOrder(order));
            } catch (Exception e) {
                runOnUiThread(() -> new AlertDialog.Builder(this).setTitle("Không tạo được đơn")
                        .setMessage(e.getMessage()).setPositiveButton("Đóng", null).show());
            } finally {
                runOnUiThread(() -> button.setEnabled(true));
            }
        }).start();
    }
    private void showOrder(JSONObject order) {
        String message = "Sản phẩm: " + order.optString("product_name")
                + "\nMã đơn: " + order.optString("id")
                + "\nSố tiền: " + String.format(java.util.Locale.forLanguageTag("vi-VN"), "%,d ₫", order.optLong("amount_vnd"))
                + "\nTrạng thái: " + order.optString("status")
                + "\n\n" + order.optString("payment_instructions", "Vui lòng liên hệ người bán để được hướng dẫn thanh toán.")
                + "\nNội dung chuyển: " + order.optString("transfer_note", order.optString("id"));
        AlertDialog.Builder dialog = new AlertDialog.Builder(this).setTitle("Đơn hàng").setMessage(message)
                .setPositiveButton("Đóng", null);
        String checkout = order.optString("checkout_url");
        if (checkout.startsWith("https://")) {
            dialog.setNeutralButton("Thanh toán", (d, w) -> {
                try { startActivity(new android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(checkout))); }
                catch (Exception ignored) { }
            });
        }
        dialog.show();
    }
    private JSONObject request(String path, JSONObject payload) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(API + path).openConnection();
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(20000);
        try {
            if (payload != null) {
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                try (OutputStream output = conn.getOutputStream()) {
                    output.write(payload.toString().getBytes(StandardCharsets.UTF_8));
                }
            }
            int code = conn.getResponseCode();
            InputStream stream = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
            if (stream == null) throw new IOException("HTTP " + code);
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int count;
            while ((count = stream.read(chunk)) != -1) buffer.write(chunk, 0, count);
            String body = buffer.toString("UTF-8");
            if (code < 200 || code >= 300) throw new IOException("HTTP " + code + ": " + body);
            return new JSONObject(body);
        } finally { conn.disconnect(); }
    }
}
