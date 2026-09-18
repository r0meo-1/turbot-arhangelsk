plugins {
    id("com.android.application")
}

val startIoEnabled = providers.gradleProperty("STARTIO_ENABLED")
    .map(String::toBoolean)
    .orElse(false)
    .get()

val startIoAppId = providers.gradleProperty("STARTIO_APP_ID")
    .orElse("")
    .get()
    .replace("\\", "\\\\")
    .replace("\"", "\\\"")

android {
    namespace = "ru.r0meo1.turbot"
    compileSdk = 36

    defaultConfig {
        applicationId = "ru.r0meo1.turbot"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        buildConfigField("boolean", "STARTIO_ENABLED", startIoEnabled.toString())
        buildConfigField("String", "STARTIO_APP_ID", "\"$startIoAppId\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("com.startapp:inapp-sdk:5.3.1")
}
