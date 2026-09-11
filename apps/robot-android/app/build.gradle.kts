import java.util.Properties
import org.gradle.kotlin.dsl.implementation

val localProperties = Properties().apply {
    val propertiesFile = rootProject.file("local.properties")
    if (propertiesFile.exists()) {
        propertiesFile.inputStream().use(::load)
    }
}

fun localSecret(name: String): String =
    localProperties.getProperty(name, "").replace("\\", "\\\\").replace("\"", "\\\"")

plugins {
    alias(libs.plugins.android.application)
}

android {
    namespace = "com.zongheng.exhibitionhallrobot"
    buildToolsVersion = "36.0.0"
    compileSdk = 36
    compileSdkMinor = 1

    defaultConfig {
        applicationId = "com.zongheng.exhibitionhallrobot"
        minSdk = 22
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        multiDexEnabled = true

        buildConfigField("String", "IFLYTEK_APP_ID", "\"\"")
        buildConfigField("String", "IFLYTEK_API_KEY", "\"\"")
        buildConfigField("String", "IFLYTEK_API_SECRET", "\"\"")
        buildConfigField("String", "ASR_MODE", "\"${localProperties.getProperty("asr.mode", "iflytek")}\"")
        buildConfigField("String", "TTS_MODE", "\"${localProperties.getProperty("tts.mode", "local")}\"")
    }

    buildTypes {
        create("speechTest") {
            initWith(getByName("debug"))
            applicationIdSuffix = ".speechtest"
            versionNameSuffix = "-speechtest"
            isDebuggable = true
            matchingFallbacks += listOf("debug")
        }
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
    buildFeatures {
        buildConfig = true
    }
}

dependencies {
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.jar", "*.aar"))))
    //implementation("com.google.code.gson:gson:2.13.2")
    implementation(libs.appcompat)
    implementation(libs.material)
    implementation(libs.activity)
    implementation(libs.constraintlayout)
    implementation("com.google.zxing:core:3.5.3")
    testImplementation(libs.junit)
    androidTestImplementation(libs.ext.junit)
    androidTestImplementation(libs.espresso.core)
    api("com.google.code.gson:gson:2.13.2")
}
