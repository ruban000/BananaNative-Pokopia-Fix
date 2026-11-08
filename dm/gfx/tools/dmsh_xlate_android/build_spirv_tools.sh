#!/usr/bin/env bash
# Builds SPIRV-Tools' validator and optimizer for the phone (android/ONDEVICE.md), for arm64-v8a with the Android NDK. The shader builder calls their C APIs through ctypes
# (SPIRV_VAL=libSPIRV-Tools-shared.so, SPIRV_OPT=libSPIRV-Tools-opt.so) where the Mac runs spirv-val and spirv-opt. The version is the Mac's: Homebrew's spirv-tools 1.4.363.0 (v2026.4),
# the Vulkan SDK tag vulkan-sdk-1.4.363.0 of SPIRV-Tools and SPIRV-Headers, so the phone and the Mac accept the same modules. The NDK build makes no executables (SPIRV-Tools
# skips them for Android); spv_val_run.c (build.sh run) is the adb shell test runner.
# Runs on the Mac and on Linux x86_64 (one bash script for both).
# Usage: build_spirv_tools.sh <work dir> [tag] → validator, optimizer, libSPIRV-Tools.so and libc++_shared.so (stripped, 16 KB pages). Keep the work dir off the home folder.
set -e
W=${1:?usage: build_spirv_tools.sh <work dir> [tag]}; TAG=${2:-vulkan-sdk-1.4.363.0}
NDK=${ANDROID_NDK_HOME:-$(ls -d ~/Library/Android/sdk/ndk/* ~/Android/Sdk/ndk/* 2>/dev/null | sort -V | tail -1)}
[ -d "$NDK/toolchains/llvm/prebuilt" ] || { echo "[RED] no Android NDK (set ANDROID_NDK_HOME)"; exit 1; }
mkdir -p "$W"; W=$(cd "$W" && pwd -P)
[ -d $W/src ] || git clone -q --depth 1 -b $TAG https://github.com/KhronosGroup/SPIRV-Tools.git $W/src
[ -d $W/src/external/spirv-headers ] || git clone -q --depth 1 -b $TAG https://github.com/KhronosGroup/SPIRV-Headers.git $W/src/external/spirv-headers
for source in "$W/src" "$W/src/external/spirv-headers"; do
  [ "$(git -C "$source" rev-parse HEAD)" = "$(git -C "$source" rev-parse "$TAG^{commit}")" ] || { echo "[RED] $source is not pinned to $TAG"; exit 1; }
done
# The optimizer uses the core's C++ API, so both libraries share one C++ runtime.
cmake -S $W/src -B $W/build-android -G Ninja -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-33 \
  -DANDROID_STL=c++_shared -DCMAKE_BUILD_TYPE=Release -DSPIRV_SKIP_TESTS=ON -DSPIRV_WERROR=OFF -DSPIRV_TOOLS_BUILD_STATIC=OFF -DBUILD_SHARED_LIBS=ON \
  -DCMAKE_SHARED_LINKER_FLAGS=-Wl,-z,max-page-size=16384 > $W/cmake.log
cmake --build "$W/build-android" --target SPIRV-Tools-shared SPIRV-Tools-opt > "$W/ninja.log"
BIN=$(ls -d $NDK/toolchains/llvm/prebuilt/*/bin | head -1)
for lib in libSPIRV-Tools-shared.so libSPIRV-Tools.so; do
  "$BIN/llvm-strip" -o "$W/$lib" "$W/build-android/source/$lib"
done
"$BIN/llvm-strip" -o "$W/libSPIRV-Tools-opt.so" "$W/build-android/source/opt/libSPIRV-Tools-opt.so"
"$BIN/llvm-strip" -o "$W/libc++_shared.so" "$BIN/../sysroot/usr/lib/aarch64-linux-android/libc++_shared.so"
ls -la "$W"/libSPIRV-Tools*.so "$W/libc++_shared.so"
