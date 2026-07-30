# rollout-shield — Native Android App

A real native Android app (NOT a PWA) that hosts the rollout-shield
dashboard on a loopback HTTP server inside the APK. The app appears on
your homescreen as a real launcher icon and runs entirely offline.

## What this is

* **Real APK** — installed via `adb install` or sideloaded through the
  file manager. Shows up in the launcher with the shield+R icon.
* **Native Kotlin** — `MainActivity.kt`, `LocalServer.kt`, `LocalCrypto.kt`.
  No React Native, no Flutter, no Cordova. No third-party UI framework
  beyond AndroidX + Material Components.
* **Loopback-only** — the in-process HTTP server binds to `127.0.0.1`.
  Nothing else.
* **No INTERNET permission** — declared absent from
  `AndroidManifest.xml`. The OS physically refuses to route any packet
  off-device from this app.
* **Owner-unlock gated** — refuses to start the dashboard until
  `filesDir/owner_unlock` is present. If absent, shows an "Unlock
  required" screen with a button to generate the unlock locally.
* **Cloud-backup disabled** — `data_extraction_rules.xml` blocks Google
  Auto Backup and device transfer. State never leaves the device.

## Security model

```
  ┌──────────────────────────────────────┐
  │           Your Phone                 │
  │  ┌────────────────────────────────┐  │
  │  │  rollout-shield app            │  │
  │  │  (no INTERNET permission)      │  │
  │  │  ┌──────────────────────────┐  │  │
  │  │  │ 127.0.0.1:8765 loopback  │  │  │
  │  │  │ HTTP server (Kotlin)     │  │  │
  │  │  └──────────────────────────┘  │  │
  │  │           ▲                     │  │
  │  │           │ loadUrl             │  │
  │  │  ┌────────┴─────────┐           │  │
  │  │  │   WebView       │           │  │
  │  │  │   (loopback)    │           │  │
  │  │  └──────────────────┘           │  │
  │  └────────────────────────────────┘  │
  │  ✗ Cannot reach network               │
  │  ✗ Other apps can't reach 127.0.0.1  │
  │  ✗ Auto-backup disabled              │
  └──────────────────────────────────────┘
```

## Build — desktop (Android Studio)

The simplest path. Requires:

* Android Studio Hedgehog (2023.1) or newer
* JDK 17 (bundled with Android Studio)
* Android SDK Platform 34 + Build-Tools 34.0.0

Steps:

1. Open Android Studio → `File` → `Open` → select the `android-app/`
   directory. Gradle sync will run.
2. `Build` → `Build Bundle(s) / APK(s)` → `Build APK(s)`.
3. Output: `app/build/outputs/apk/debug/app-debug.apk` (or `release/`).
4. Install: `adb install -r app/build/outputs/apk/debug/app-debug.apk`
5. The icon appears in your launcher as **rollout-shield**.

## Build — on-device (Termux)

If you only have your phone (no desktop):

```sh
# 1. Install Termux from F-Droid (NOT Google Play — Play version is
#    outdated and breaks proot-distro).
# 2. Inside Termux:
pkg update -y
pkg install -y openjdk-17 wget unzip

# 3. Install Android SDK command-line tools (one-time):
mkdir -p ~/android-sdk/cmdline-tools
cd ~/android-sdk/cmdline-tools
wget https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip commandlinetools-linux-*.zip
mv cmdline-tools latest
rm commandlinetools-linux-*.zip

# 4. Accept licenses + install platforms:
export ANDROID_HOME=~/android-sdk
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin
yes | sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0"

# 5. Copy this android-app/ directory onto the phone (scp, USB, etc).
# 6. Inside android-app/:
export ANDROID_HOME=~/android-sdk
./build-on-termux.sh
# → produces app/build/outputs/apk/debug/app-debug.apk
```

## Install on the phone

Once you have the APK:

```sh
adb install -r app-debug.apk
```

Or, sideload manually:

1. Copy `app-debug.apk` to your phone (USB, Bluetooth, etc).
2. Open the file in your file manager.
3. Android will ask "Allow installs from this source" — grant it once.
4. Tap the APK → Install → Open.

The icon appears in your launcher.

## First-run

When you open the app:

* If `filesDir/owner_unlock` is missing, you see an "Unlock required"
  screen with a "Generate unlock on this device" button. Tap it.
* The unlock file is created at `/data/data/com.rolloutshield.dashboard/
  files/owner_unlock`, owned by the app's UID, mode 0600.
* BACKUP THIS KEY OFFLINE. Recovery: same 32-word phrase process as
  the desktop tool (`secure_state.py --backup` ↔ `LocalCrypto.generateUnlock`
  both produce identical 32-byte / urlsafe-b64 forms).

## Verification

* `aapt dump badging app-debug.apk` shows:
  ```
  package: name='com.rolloutshield.dashboard' versionCode='1' versionName='0.2.0'
  uses-permission: (none)
  ```
* `adb shell dumpsys package com.rolloutshield.dashboard | grep -i permission`
  shows no `android.permission.INTERNET`.
* The app launches, the WebView loads `http://127.0.0.1:8765/`, and
  all API endpoints respond.

## What this is NOT

* NOT a PWA — no service worker, no manifest.webmanifest, no browser tab.
* NOT a WebView wrapper around a remote URL — it's a real native app
  with its own in-process HTTP server.
* NOT dependent on Google Play Services, Firebase, or any third-party
  service. Pure AndroidX + Material.
* NOT connecting to anything off-device. There is literally no
  `android.permission.INTERNET` declaration in the manifest.

## File map

```
android-app/
├── build.gradle.kts                # root build
├── settings.gradle.kts             # project layout
├── gradle.properties               # build flags
├── README.md                       # this file
├── build-on-termux.sh              # on-device build script
└── app/
    ├── build.gradle.kts            # module build (compileSdk, deps)
    └── src/main/
        ├── AndroidManifest.xml     # NO INTERNET permission
        ├── kotlin/com/rolloutshield/dashboard/
        │   ├── MainActivity.kt     # WebView host + unlock gate
        │   ├── LocalServer.kt      # 127.0.0.1-only HTTP server
        │   └── LocalCrypto.kt      # Fernet key generation
        └── res/
            ├── drawable/ic_launcher_foreground.xml   # shield+R vector
            ├── mipmap-anydpi-v26/ic_launcher.xml      # adaptive icon
            ├── mipmap-anydpi-v26/ic_launcher_round.xml
            ├── values/colors.xml                     # brand palette
            ├── values/strings.xml
            ├── values/themes.xml                     # Material theme
            ├── xml/network_security_config.xml       # deny all non-loopback
            └── xml/data_extraction_rules.xml         # disable cloud backup
```