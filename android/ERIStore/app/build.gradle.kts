plugins { id("com.android.application") }
android {
    namespace = "com.eringuyen.store"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.eringuyen.store"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }
    buildTypes { getByName("release") { isMinifyEnabled = false } }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
