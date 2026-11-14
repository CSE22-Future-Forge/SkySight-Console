#!/usr/bin/env bash
# Build the Android APK with the user-profile toolchain (tools/install_toolchain.sh).
#   bash tools/build_android.sh            # debug APK  (dev client, needs Metro: npm start)
#   bash tools/build_android.sh release    # release APK (JS bundled, runs standalone)
# Env: ABIS=arm64-v8a,x86_64 (default)   PREBUILD=1 to regenerate android/ (after app.json/plugin/module changes)
set -euo pipefail
cd "$(dirname "$0")/.."

# Windows: the New Architecture C++ build exceeds 260-character paths. Needs Windows long paths
# enabled + the long-path-aware ninja that tools/install_toolchain.sh installs.

TC="$(cygpath -u "${USERPROFILE:-$HOME}")/.sar-toolchain"
export JAVA_HOME="$(cygpath -w "$TC/jdk-17")"
export ANDROID_HOME="$(cygpath -w "$TC/android-sdk")"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$TC/jdk-17/bin:$TC/android-sdk/platform-tools:$PATH"
export NODE_ENV=development
[[ "${1:-debug}" == release ]] && export NODE_ENV=production

# Real phones are arm64; x86_64 is for the emulator. Override with ABIS=... (comma separated).
ABIS="${ABIS:-arm64-v8a,x86_64}"

# Regenerate android/ only when it is missing or asked for (PREBUILD=1, e.g. after app.json /
# plugin / native module changes). Re-running it needlessly wipes Gradle's build cache.
if [[ ! -f android/gradlew || "${PREBUILD:-0}" == 1 ]]; then
    rm -rf android
    npx expo prebuild --platform android --no-install
fi
cd android
if [[ "${1:-debug}" == release ]]; then
    ./gradlew assembleRelease --no-daemon --console=plain -PreactNativeArchitectures="$ABIS"
    ls -la app/build/outputs/apk/release/
else
    ./gradlew assembleDebug --no-daemon --console=plain -PreactNativeArchitectures="$ABIS"
    ls -la app/build/outputs/apk/debug/
fi
