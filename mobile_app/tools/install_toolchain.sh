#!/usr/bin/env bash
# Install the Android build toolchain into the user profile (no admin, nothing system-wide):
#   %USERPROFILE%/.sar-toolchain/jdk-17         Temurin JDK 17 (React Native's Gradle needs 17)
#   %USERPROFILE%/.sar-toolchain/android-sdk    command-line tools + platform-tools
# Gradle downloads the exact platform / build-tools / NDK versions it needs on first build.
# Resumable: re-run after a dropped connection.
set -euo pipefail

ROOT="$(cygpath -u "${USERPROFILE:-$HOME}")/.sar-toolchain"
mkdir -p "$ROOT/downloads"
JDK_URL="https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse"
SDK_URL="https://dl.google.com/android/repository/commandlinetools-win-16111833_latest.zip"

get() {  # url dest
    for attempt in $(seq 1 30); do
        curl -L --fail --silent --show-error --connect-timeout 30 --speed-limit 1024 --speed-time 60 \
             -C - -o "$2" "$1" && return 0
        echo "  retry $attempt for $(basename "$2")"
        sleep 2
    done
    return 1
}

if [[ ! -x "$ROOT/jdk-17/bin/java.exe" ]]; then
    echo "[jdk] downloading Temurin 17"
    get "$JDK_URL" "$ROOT/downloads/jdk17.zip"
    rm -rf "$ROOT/jdk-tmp" && mkdir -p "$ROOT/jdk-tmp"
    unzip -q "$ROOT/downloads/jdk17.zip" -d "$ROOT/jdk-tmp"
    rm -rf "$ROOT/jdk-17" && mv "$ROOT/jdk-tmp"/jdk-17* "$ROOT/jdk-17" && rm -rf "$ROOT/jdk-tmp"
fi
"$ROOT/jdk-17/bin/java.exe" -version 2>&1 | head -1

SDK="$ROOT/android-sdk"
if [[ ! -f "$SDK/cmdline-tools/latest/bin/sdkmanager.bat" ]]; then
    echo "[sdk] downloading command-line tools"
    get "$SDK_URL" "$ROOT/downloads/cmdline-tools.zip"
    rm -rf "$SDK/cmdline-tools" && mkdir -p "$SDK/cmdline-tools"
    unzip -q "$ROOT/downloads/cmdline-tools.zip" -d "$SDK/cmdline-tools"
    mv "$SDK/cmdline-tools/cmdline-tools" "$SDK/cmdline-tools/latest"
fi

export JAVA_HOME="$(cygpath -w "$ROOT/jdk-17")"
SDKM="$SDK/cmdline-tools/latest/bin/sdkmanager.bat"
echo "[sdk] accepting licences"
yes | "$SDKM" --sdk_root="$(cygpath -w "$SDK")" --licenses > /dev/null 2>&1 || true
echo "[sdk] installing platform-tools"
for attempt in 1 2 3 4 5; do
    "$SDKM" --sdk_root="$(cygpath -w "$SDK")" "platform-tools" && break
done
echo "[sdk] installing CMake 3.22.1 (the version React Native's Gradle builds ask for)"
for attempt in 1 2 3 4 5; do
    "$SDKM" --sdk_root="$(cygpath -w "$SDK")" "cmake;3.22.1" && break
done

# React Native's New Architecture build produces object paths over 260 characters. Windows
# long paths must be enabled (HKLM\...\FileSystem\LongPathsEnabled = 1) AND ninja must be
# long-path aware, which needs ninja >= 1.12; CMake 3.22.1 ships 1.10.2. Swap in 1.12.1.
NINJA_DIR="$SDK/cmake/3.22.1/bin"
if [[ -x "$NINJA_DIR/ninja.exe" ]] && "$NINJA_DIR/ninja.exe" --version | grep -q '^1\.10'; then
    echo "[sdk] upgrading ninja in CMake 3.22.1 to 1.12.1 (long-path aware)"
    get "https://github.com/ninja-build/ninja/releases/download/v1.12.1/ninja-win.zip" "$ROOT/downloads/ninja-win.zip"
    mv "$NINJA_DIR/ninja.exe" "$NINJA_DIR/ninja-1.10.2.exe"
    unzip -q -o "$ROOT/downloads/ninja-win.zip" -d "$NINJA_DIR"
fi
"$NINJA_DIR/ninja.exe" --version
reg_long=$(reg query 'HKLM\SYSTEM\CurrentControlSet\Control\FileSystem' //v LongPathsEnabled 2>/dev/null | grep -o '0x1' || true)
[[ -z "$reg_long" ]] && echo "[warn] Windows long paths are OFF: run as admin: reg add HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem /v LongPathsEnabled /t REG_DWORD /d 1 /f"

echo "[done] JAVA_HOME=$(cygpath -w "$ROOT/jdk-17")  ANDROID_HOME=$(cygpath -w "$SDK")"
