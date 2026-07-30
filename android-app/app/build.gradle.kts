// app/build.gradle.kts — single-module native Android app
//
// Build-time requirements (no internet at build):
//   - JDK 17+ installed (Termux: pkg install openjdk-17)
//   - Android SDK installed (cmdline-tools + platform-tools + build-tools 34)
//   - Gradle 8.7+ installed (or Android Studio, which bundles it)
// All dependencies are resolved from Google's Maven repo at *first* build
// and then cached locally. After the first build, no internet is required.
//
// DO NOT add distributionUrl to a gradle-wrapper.properties — the build
// must work offline after initial dependency resolution.

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.rolloutshield.dashboard"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.rolloutshield.dashboard"
        minSdk = 26              // Android 8.0; covers >99% of devices
        targetSdk = 34
        versionCode = 1
        versionName = "0.2.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Debug-signed by default so the user can sideload without
            // generating a release keystore. Owner replaces with their
            // own keystore for production use.
            signingConfig = signingConfigs.getByName("debug")
        }
        debug {
            // default
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // Standard AndroidX — resolved once, then cached locally
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.webkit:webkit:1.11.0")
    implementation("androidx.activity:activity-ktx:1.9.0")
}