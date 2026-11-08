#!/bin/sh
# Puts the dav1d sources this tree builds (8-bit decoder, C and aarch64 assembly) next to CMakeLists.txt: src/, include/ and LICENSE, from upstream's release tarball
# (version and SHA-256 below, which VideoLAN publishes beside it). Offline: DAV1D_TARBALL=<path to dav1d-1.5.4.tar.xz>. The tarball is checked either way.
set -eu
VER=1.5.4
SHA256=686616b7c69eb88d44459391ab25cac13b6647a3b288835c5784e71c1514a5c5
URL=https://downloads.videolan.org/pub/videolan/dav1d/$VER/dav1d-$VER.tar.xz
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
if [ -n "${DAV1D_TARBALL:-}" ]; then cp "$DAV1D_TARBALL" "$tmp/d.tar.xz"; else curl -fsSL -o "$tmp/d.tar.xz" "$URL"; fi
echo "$SHA256  $tmp/d.tar.xz" | sha256sum -c - >/dev/null || { echo "vendor.sh: dav1d-$VER.tar.xz does not match the pinned SHA-256" >&2; exit 1; }
tar -xJf "$tmp/d.tar.xz" -C "$tmp"
up=$tmp/dav1d-$VER
rm -rf "$here/src" "$here/include"
mkdir -p "$here/src/arm/64" "$here/include"
cp "$up"/src/*.c "$up"/src/*.h "$here/src/"
cp "$up"/src/arm/*.c "$up"/src/arm/*.h "$up"/src/arm/asm.S "$here/src/arm/"
# 8-bit only: the 16-bit assembly (cdef16.S, itx16.S, mc16*.S …) is left out
for f in "$up"/src/arm/64/*.S; do case $(basename "$f") in *16*.S) ;; *) cp "$f" "$here/src/arm/64/" ;; esac; done
cp -r "$up"/include/common "$up"/include/compat "$up"/include/dav1d "$here/include/"
cp "$up/COPYING" "$here/LICENSE"
echo "dav1d $VER vendored into $here"
