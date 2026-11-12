package com.eringuyen.store

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.os.Build
import android.graphics.Color
import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.widget.*
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

class MainActivity : Activity() {
    private val api = "https://eri-api-dag8.onrender.com"
    private lateinit var list: LinearLayout
    private lateinit var title: TextView
    private var category = "mods"

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density + 0.5f).toInt()

    @Suppress("DEPRECATION")
    private fun enableEdgeToEdge() {
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(false)
            val appearance = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or
                WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS
            window.insetsController?.setSystemBarsAppearance(appearance, appearance)
        } else {
            var flags = View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
                View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                flags = flags or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
            }
            window.decorView.systemUiVisibility = flags
        }
    }

    @Suppress("DEPRECATION")
    private fun applySafeInsets(root: View) {
        root.setOnApplyWindowInsetsListener { view, insets ->
            val left: Int
            val top: Int
            val right: Int
            val bottom: Int
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val safe = insets.getInsets(
                    WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout()
                )
                left = safe.left
                top = safe.top
                right = safe.right
                bottom = safe.bottom
            } else {
                left = insets.systemWindowInsetLeft
                top = insets.systemWindowInsetTop
                right = insets.systemWindowInsetRight
                bottom = insets.systemWindowInsetBottom
            }
            view.setPadding(dp(16) + left, dp(12) + top, dp(16) + right, dp(12) + bottom)
            insets
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.WHITE)
            setPadding(dp(16), dp(12), dp(16), dp(12))
        }
        applySafeInsets(root)
        title = TextView(this).apply {
            text = "ERI STORE"
            textSize = 24f
            setTypeface(null, Typeface.BOLD)
        }
        root.addView(title)
        val tabs = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        for ((label, endpoint) in listOf("Mod Game" to "mods", "AI Plus" to "products")) {
            tabs.addView(Button(this).apply {
                text = label
                textSize = 12f
                setOnClickListener { category = endpoint; load() }
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        }
        root.addView(tabs)
        root.addView(Button(this).apply {
            text = "Làm mới"
            setOnClickListener { load() }
        })
        val scroll = ScrollView(this)
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        scroll.addView(list)
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        root.requestApplyInsets()
        load()
    }

    private fun message(value: String) {
        list.removeAllViews()
        list.addView(TextView(this).apply { text = value; textSize = 16f })
    }

    private fun request(path: String, body: String? = null): Pair<Int, String> {
        val conn = URL(api + path).openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = 15000
            conn.readTimeout = 15000
            if (body != null) {
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            return code to (stream?.bufferedReader()?.use { it.readText() } ?: "")
        } finally { conn.disconnect() }
    }

    private fun load() {
        val selected = category
        title.text = "ERI STORE — " + when (selected) {
            "products" -> "AI Plus"
            "certificates" -> "Chứng chỉ"
            else -> "Mod Game"
        }
        message("Đang tải sản phẩm...")
        thread {
            try {
                val (code, raw) = request("/api/v1/" + selected)
                if (code != 200) throw Exception("HTTP $code")
                val data = JSONObject(raw).getJSONArray("items")
                runOnUiThread {
                    if (selected != category) return@runOnUiThread
                    list.removeAllViews()
                    if (data.length() == 0) { message("Chưa có sản phẩm."); return@runOnUiThread }
                    for (i in 0 until data.length()) {
                        val item = data.getJSONObject(i)
                        val id = item.getInt("id")
                        val name = item.optString("name")
                        val price = item.optLong("price_vnd")
                        val card = LinearLayout(this).apply {
                            orientation = LinearLayout.VERTICAL
                            setPadding(12, 18, 12, 18)
                        }
                        card.addView(TextView(this).apply {
                            text = name
                            textSize = 19f
                            setTypeface(null, Typeface.BOLD)
                        })
                        val detail = if (selected == "mods") {
                            item.optString("description").takeIf { it.isNotBlank() }
                                ?: ("Phiên bản: " + item.optString("version"))
                        } else item.optString("description")
                        card.addView(TextView(this).apply { text = detail })
                        card.addView(TextView(this).apply {
                            text = "%,d đ".format(java.util.Locale.US, price)
                            textSize = 17f
                        })
                        card.addView(Button(this).apply {
                            text = "Mua ngay"
                            setOnClickListener { buy(selected, id) }
                        })
                        list.addView(card)
                        list.addView(View(this).apply { setBackgroundColor(0xFFDDDDDD.toInt()) },
                            LinearLayout.LayoutParams(-1, 1))
                    }
                }
            } catch (e: Exception) {
                runOnUiThread { if (selected == category) message("Không tải được: " + (e.message ?: "Lỗi mạng")) }
            }
        }
    }

    private fun buy(endpoint: String, id: Int) {
        val kind = when (endpoint) {
            "products" -> "ai"
            "certificates" -> "certificate"
            else -> "mod"
        }
        thread {
            try {
                val payload = JSONObject().put("kind", kind).put("product_id", id)
                val (code, raw) = request("/api/v1/orders", payload.toString())
                if (code != 201) throw Exception("Máy chủ từ chối tạo đơn: " + raw.take(180))
                val order = JSONObject(raw)
                val instructions = order.optString("payment_instructions", "Người bán xác nhận thanh toán thủ công.")
                val note = order.optString("transfer_note")
                val details = "Mã đơn: " + order.optString("id") +
                    "\nSản phẩm: " + order.optString("product_name") +
                    "\nSố tiền: " + "%,d đ".format(java.util.Locale.US, order.optLong("amount_vnd")) +
                    "\nNội dung chuyển: " + note + "\n\n" + instructions
                val qrPath = order.optString("qr_url")
                val qrBitmap = if (qrPath.isNotBlank()) {
                    try {
                        val imageUrl = if (qrPath.startsWith("https://")) qrPath else api + qrPath
                        val conn = URL(imageUrl).openConnection() as HttpURLConnection
                        try {
                            conn.connectTimeout = 15000
                            conn.readTimeout = 15000
                            if (conn.responseCode != 200) null else {
                                val bytes = conn.inputStream.use { it.readBytes() }
                                if (bytes.size > 2 * 1024 * 1024) null else
                                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                            }
                        } finally { conn.disconnect() }
                    } catch (_: Exception) { null }
                } else null
                runOnUiThread {
                    val content = LinearLayout(this).apply {
                        orientation = LinearLayout.VERTICAL
                        setPadding(dp(16), dp(8), dp(16), dp(8))
                    }
                    if (qrBitmap != null) {
                        content.addView(TextView(this).apply {
                            text = "Quét mã QR MoMo"
                            textSize = 18f
                        })
                        content.addView(ImageView(this).apply {
                            setImageBitmap(qrBitmap)
                            adjustViewBounds = true
                            scaleType = ImageView.ScaleType.FIT_CENTER
                        }, LinearLayout.LayoutParams(-1, dp(260)))
                    } else if (order.optBoolean("payment_setup_required", false)) {
                        content.addView(TextView(this).apply {
                            text = "Chủ cửa hàng chưa liên kết QR MoMo."
                        })
                    } else if (qrPath.isNotBlank()) {
                        content.addView(TextView(this).apply { text = "Không tải được ảnh QR." })
                    }
                    content.addView(TextView(this).apply { text = details })
                    AlertDialog.Builder(this).setTitle("Đơn hàng đã tạo")
                        .setView(ScrollView(this).apply { addView(content) })
                        .setPositiveButton("Đóng", null).show()
                }
            } catch (e: Exception) {
                runOnUiThread {
                    AlertDialog.Builder(this).setTitle("Không tạo được đơn")
                        .setMessage(e.message).setPositiveButton("Đóng", null).show()
                }
            }
        }
    }
}
