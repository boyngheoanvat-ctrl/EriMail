package com.eringuyen.store

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.os.Build
import android.graphics.Color
import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec
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
    @Volatile private var customerToken: String = ""

    private fun eriMoney(value: Long): String =
        java.text.NumberFormat.getIntegerInstance(java.util.Locale("vi", "VN")).format(value) + "đ"

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
        for ((label, endpoint) in listOf("Mod Game" to "mods", "AI Plus" to "products", "Chứng chỉ" to "certificates")) {
            tabs.addView(Button(this).apply {
                text = label
                textSize = 12f
                setOnClickListener { category = endpoint; load() }
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        }
        root.addView(tabs)
        root.addView(Button(this).apply {
            text = "Tài khoản / Lịch sử mua hàng"
            setOnClickListener { showCustomerMenu() }
        })
        root.addView(Button(this).apply {
            text = "Làm mới"
            setOnClickListener { load() }
        })
        val scroll = ScrollView(this)
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        scroll.addView(list)
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        customerToken = try { readCustomerToken() } catch (_: Exception) { "" }
        root.requestApplyInsets()
        load()
    }

    private fun message(value: String) {
        list.removeAllViews()
        list.addView(TextView(this).apply { text = value; textSize = 16f })
    }

    private fun request(path: String, body: String? = null,
                        authenticated: Boolean = false, method: String = "GET"): Pair<Int, String> {
        val conn = URL(api + path).openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = 15000
            conn.readTimeout = 15000
            if (authenticated) {
                if (customerToken.isEmpty()) throw Exception("Vui lòng đăng nhập")
                conn.setRequestProperty("Authorization", "Bearer " + customerToken)
            }
            if (body != null) {
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            if (body == null && method != "GET") conn.requestMethod = method
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            return code to (stream?.bufferedReader()?.use { it.readText() } ?: "")
        } finally { conn.disconnect() }
    }


    // Android KeyStore key cannot be extracted; SharedPreferences stores ciphertext only.
    private fun customerKey(): javax.crypto.SecretKey {
        val alias = "eri-store-buyer-token-v1"
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = store.getKey(alias, null) as? javax.crypto.SecretKey
        if (existing != null) return existing
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build()
        )
        return generator.generateKey()
    }

    private fun readCustomerToken(): String {
        val saved = getSharedPreferences("eri_customer", MODE_PRIVATE)
            .getString("token_cipher", "") ?: ""
        if (saved.isBlank()) return ""
        val raw = Base64.decode(saved, Base64.NO_WRAP)
        if (raw.size < 13) return ""
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, customerKey(),
                    GCMParameterSpec(128, raw.copyOfRange(0, 12)))
        return String(cipher.doFinal(raw.copyOfRange(12, raw.size)), Charsets.UTF_8)
    }

    private fun saveCustomerToken(value: String) {
        val pref = getSharedPreferences("eri_customer", MODE_PRIVATE).edit()
        if (value.isEmpty()) {
            pref.remove("token_cipher").apply()
            customerToken = ""
            return
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, customerKey())
        val combined = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        pref.putString("token_cipher", Base64.encodeToString(combined, Base64.NO_WRAP)).apply()
        customerToken = value
    }

    private fun inputRow(hint: String, password: Boolean = false): EditText =
        EditText(this).apply {
            this.hint = hint
            this.setSingleLine(true)
            if (password) {
                this.inputType = android.text.InputType.TYPE_CLASS_TEXT or
                    android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
            } else {
                this.inputType = android.text.InputType.TYPE_CLASS_TEXT
            }
        }

    private fun showAuthDialog(register: Boolean = false) {
        val form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(15), dp(8), dp(15), dp(4))
        }
        val username = inputRow("Tên đăng nhập (3–32 ký tự)")
        val password = inputRow("Mật khẩu (ít nhất 10 ký tự)", true)
        val name = inputRow("Tên người nhận")
        val contact = inputRow("Liên hệ giao hàng (Zalo / SĐT)")
        form.addView(username)
        form.addView(password)
        if (register) {
            form.addView(name)
            form.addView(contact)
        }
        AlertDialog.Builder(this)
            .setTitle(if (register) "Đăng ký ERI STORE" else "Đăng nhập ERI STORE")
            .setView(ScrollView(this).apply { addView(form) })
            .setPositiveButton(if (register) "Đăng ký" else "Đăng nhập") { _, _ ->
                val user = username.text.toString().trim()
                val pass = password.text.toString()
                val displayName = name.text.toString().trim()
                val delivery = contact.text.toString().trim()
                if (user.isBlank() || pass.isBlank() ||
                    (register && (displayName.isBlank() || delivery.isBlank()))) {
                    AlertDialog.Builder(this).setMessage("Vui lòng điền đầy đủ thông tin.")
                        .setPositiveButton("Đóng", null).show()
                } else {
                    thread {
                        try {
                            val body = JSONObject().put("username", user).put("password", pass)
                            if (register) {
                                body.put("display_name", displayName).put("contact", delivery)
                            }
                            val (code, response) = request(
                                if (register) "/api/v1/auth/register" else "/api/v1/auth/login",
                                body.toString()
                            )
                            if (code !in 200..299) throw Exception(
                                "HTTP $code: " + JSONObject(response).optString("detail", "Đăng nhập thất bại")
                            )
                            val auth = JSONObject(response)
                            saveCustomerToken(auth.getString("token"))
                            runOnUiThread {
                                AlertDialog.Builder(this).setTitle("Thành công")
                                    .setMessage("Đã đăng nhập. Bạn có thể đặt hàng và xem lịch sử.")
                                    .setPositiveButton("OK", null).show()
                            }
                        } catch (e: Exception) {
                            runOnUiThread {
                                AlertDialog.Builder(this).setTitle("Không đăng nhập được")
                                    .setMessage(e.message ?: "Lỗi kết nối").setPositiveButton("OK", null).show()
                            }
                        }
                    }
                }
            }
            .setNeutralButton(if (register) "Đăng nhập" else "Đăng ký") { _, _ ->
                showAuthDialog(!register)
            }
            .setNegativeButton("Hủy", null)
            .show()
    }

    private fun showCustomerMenu() {
        if (customerToken.isEmpty()) {
            showAuthDialog()
            return
        }
        thread {
            try {
                val (code, body) = request("/api/v1/auth/me", authenticated = true)
                if (code == 401) {
                    saveCustomerToken("")
                    runOnUiThread { showAuthDialog() }
                    return@thread
                }
                if (code != 200) throw Exception("HTTP $code")
                val user = JSONObject(body)
                runOnUiThread {
                    AlertDialog.Builder(this).setTitle("Tài khoản của tôi")
                        .setMessage(
                            "Tên: " + user.optString("display_name") +
                            "\nTài khoản: " + user.optString("username") +
                            "\nLiên hệ: " + user.optString("contact")
                        )
                        .setPositiveButton("Lịch sử mua hàng") { _, _ -> showHistory() }
                        .setNeutralButton("Đăng xuất") { _, _ ->
                            thread {
                                try { request("/api/v1/auth/logout", authenticated = true, method = "POST") }
                                catch (_: Exception) {}
                                saveCustomerToken("")
                                runOnUiThread { showAuthDialog() }
                            }
                        }
                        .setNegativeButton("Đóng", null).show()
                }
            } catch (e: Exception) {
                runOnUiThread {
                    AlertDialog.Builder(this).setMessage("Không tải được tài khoản: " + e.message)
                        .setPositiveButton("Đóng", null).show()
                }
            }
        }
    }

    private fun showHistory() {
        if (customerToken.isEmpty()) { showAuthDialog(); return }
        thread {
            try {
                val (code, body) = request("/api/v1/me/orders", authenticated = true)
                if (code != 200) throw Exception("HTTP $code: " + body.take(140))
                val array = JSONObject(body).getJSONArray("items")
                runOnUiThread {
                    val holder = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
                    if (array.length() == 0) {
                        holder.addView(TextView(this).apply { text = "Bạn chưa có đơn hàng." })
                    }
                    for (i in 0 until array.length()) {
                        val order = array.getJSONObject(i)
                        holder.addView(Button(this).apply {
                            isAllCaps = false
                            text = order.optString("product_name") +
                                "\n" + order.optString("id") + " • " +
                                eriMoney(order.optLong("amount_vnd")) + " • " +
                                order.optString("status")
                            setOnClickListener {
                                thread {
                                    try {
                                        val (c, b) = request("/api/v1/orders/" + order.optString("id"),
                                                             authenticated = true)
                                        if (c != 200) throw Exception("HTTP $c")
                                        showOrder(JSONObject(b))
                                    } catch (e: Exception) {
                                        runOnUiThread {
                                            AlertDialog.Builder(this@MainActivity).setMessage(e.message)
                                                .setPositiveButton("Đóng", null).show()
                                        }
                                    }
                                }
                            }
                        })
                    }
                    AlertDialog.Builder(this).setTitle("Đơn hàng của tôi")
                        .setView(ScrollView(this).apply {
                            setPadding(dp(10), dp(10), dp(10), dp(10))
                            addView(holder)
                        })
                        .setPositiveButton("Đóng", null).show()
                }
            } catch (e: Exception) {
                runOnUiThread {
                    AlertDialog.Builder(this).setMessage("Không tải được lịch sử: " + e.message)
                        .setPositiveButton("Đóng", null).show()
                }
            }
        }
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
                            text = eriMoney(price)
                            textSize = 17f
                        })
                        if (selected == "products" || selected == "certificates") {
                            card.addView(TextView(this).apply {
                                text = "Giao thủ công sau khi xác nhận thanh toán"
                                textSize = 13f
                            })
                        }
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

    private fun showOrder(order: JSONObject) {
                val instructions = order.optString("payment_instructions", "Người bán xác nhận thanh toán thủ công.")
                val note = order.optString("transfer_note")
                val manualNote = if (order.optString("kind") == "ai" || order.optString("kind") == "certificate")
                    "\nGiao thủ công sau khi Admin kiểm tra thanh toán."
                else ""
                val details = "Mã đơn: " + order.optString("id") +
                    "\nSản phẩm: " + order.optString("product_name") +
                    "\nSố tiền: " + eriMoney(order.optLong("amount_vnd")) +
                    "\nNội dung chuyển: " + note + "\n\n" + instructions + manualNote
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
    }

    private fun buy(endpoint: String, id: Int) {
        if (customerToken.isEmpty()) {
            showAuthDialog()
            return
        }
        val kind = when (endpoint) {
            "products" -> "ai"
            "certificates" -> "certificate"
            else -> "mod"
        }
        thread {
            try {
                val payload = JSONObject().put("kind", kind).put("product_id", id)
                val (code, raw) = request("/api/v1/orders", payload.toString(), authenticated = true)
                if (code != 201) throw Exception("Máy chủ từ chối tạo đơn: " + raw.take(180))
                showOrder(JSONObject(raw))
            } catch (e: Exception) {
                runOnUiThread {
                    AlertDialog.Builder(this).setTitle("Không tạo được đơn")
                        .setMessage(e.message).setPositiveButton("Đóng", null).show()
                }
            }
        }
    }
}
