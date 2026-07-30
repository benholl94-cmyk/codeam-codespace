#!/data/data/com.termux/files/usr/bin/bash
# build-on-termux.sh — build the rollout-shield APK on-device (Termux).
#
# Prerequisites (run once):
#   pkg install -y openjdk-17 wget unzip
#   export ANDROID_HOME=~/android-sdk
#   # install cmdline-tools, then:
#   yes | sdkmanager --licenses
#   sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0"
#
# Run from the android-app/ directory:
#   ./build-on-termux.sh
#
# Output: app/build/outputs/apk/debug/app-debug.apk

set -euo pipefail

# 1. Locate / install Gradle
if ! command -v gradle >/dev/null 2>&1; then
    echo "[build] gradle not found; trying pkg..."
    if command -v pkg >/dev/null 2>&1; then
        pkg install -y gradle || true
    fi
fi
if ! command -v gradle >/dev/null 2>&1; then
    echo "[build] ERROR: gradle not installed."
    echo "  pkg install gradle   (Termux)"
    echo "  OR install Android Studio + run from there"
    exit 1
fi

# 2. Sanity-check ANDROID_HOME / SDK
: "${ANDROID_HOME:?ANDROID_HOME must be set (e.g. ~/android-sdk)}"
if [ ! -d "$ANDROID_HOME/platforms/android-34" ]; then
    echo "[build] WARNING: $ANDROID_HOME/platforms/android-34 not found."
    echo "  Run: sdkmanager \"platforms;android-34\" \"build-tools;34.0.0\""
fi

# 3. Set local.properties so gradle finds the SDK
cat > local.properties <<EOF
sdk.dir=$ANDROID_HOME
EOF

# 4. Build
echo "[build] gradle assembleDebug …"
gradle assembleDebug --no-daemon --console=plain 2>&1 | tee build.log

# 5. Locate APK
APK="app/build/outputs/apk/debug/app-debug.apk"
if [ -f "$APK" ]; then
    SIZE=$(stat -c %s "$APK")
    echo "[build] OK: $APK ($SIZE bytes)"
    echo "[build] next: adb install -r $APK"
else
    echo "[build] FAILED: $APK not found"
    exit 2
fi