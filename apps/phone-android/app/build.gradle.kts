import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Never commit real credentials. CI environment overrides the ignored local file.
val sparkLocalSecrets = Properties().apply {
    val secretFile = rootProject.file("local.secrets.properties")
    if (secretFile.isFile) secretFile.inputStream().use { load(it) }
}
fun sparkCredential(name: String): String =
    providers.environmentVariable(name).orNull?.trim()?.takeIf { it.isNotEmpty() }
        ?: sparkLocalSecrets.getProperty(name, "").trim()

val sparkAppId = sparkCredential("SPARK_APP_ID")
val sparkApiKey = sparkCredential("SPARK_API_KEY")
val sparkApiSecret = sparkCredential("SPARK_API_SECRET")
// Fail closed for builds, including local/CI packaging; never ship a silently unusable ASR APK.
val verifySparkCredentials = tasks.register("verifySparkCredentials") {
    doLast {
        val missing = listOf(
            "SPARK_APP_ID" to sparkAppId,
            "SPARK_API_KEY" to sparkApiKey,
            "SPARK_API_SECRET" to sparkApiSecret
        ).filter { it.second.isBlank() }.map { it.first }
        check(missing.isEmpty()) {
            "Missing ASR build credentials: ${missing.joinToString()}. " +
                "Set CI environment variables or ignored local.secrets.properties; see docs/GITLAB_INTEGRATION_HANDOFF.md."
        }
    }
}
tasks.matching { it.name == "preBuild" }.configureEach { dependsOn(verifySparkCredentials) }

android {
    namespace = "com.codexpro.asrapk"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.codexpro.digitalhumanasr"
        minSdk = 24
        targetSdk = 34
        versionCode = 9
        versionName = "1.8"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        resValue("string", "spark_builtin_app_id", sparkAppId)
        resValue("string", "spark_builtin_api_key", sparkApiKey)
        resValue("string", "spark_builtin_api_secret", sparkApiSecret)
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.5")
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.jar", "*.aar"))))
    implementation("androidx.activity:activity-ktx:1.9.0")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.fragment:fragment-ktx:1.8.2")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.4")
    implementation("androidx.lifecycle:lifecycle-livedata-ktx:2.8.4")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.google.code.gson:gson:2.8.8")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}
