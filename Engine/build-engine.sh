#!/bin/sh
# Builds the iNux execution engine: Blink (x86-64 Linux emulator) plus the
# glue in Engine/shim, as a static library.
#
#   Engine/build-engine.sh ios  OUTDIR   # arm64 iPhoneOS library (run on macOS)
#   Engine/build-engine.sh host OUTDIR   # native library + inux-run test tool
#
# OUTDIR receives lib/libinuxengine.a, include/inux_engine.h and, for host,
# bin/inux-run.
set -eu

TARGET=${1:?usage: build-engine.sh ios|host OUTDIR}
mkdir -p "${2:?usage: build-engine.sh ios|host OUTDIR}"
OUT=$(cd "$2" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
SHA=$(tr -d '[:space:]' < "$HERE/BLINK_COMMIT")
SRC="$OUT/blink-src"
OBJ="$OUT/obj"

if [ "$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)" != "$SHA" ]; then
  rm -rf "$SRC"
  git init -q "$SRC"
  git -C "$SRC" remote add origin https://github.com/jart/blink
  git -C "$SRC" fetch -q --depth 1 origin "$SHA"
  git -C "$SRC" checkout -q FETCH_HEAD
fi

cd "$SRC"

# Feature set:
#  - VFS gives the guest a private root with /dev and /proc.
#  - no JIT: iOS does not allow generating executable code at runtime.
#  - no fork: iOS apps cannot create processes.
if ! ./configure --enable-vfs --disable-overlays --disable-jit --disable-fork \
    --disable-backtrace > "$OUT/configure.log" 2>&1; then
  cat "$OUT/configure.log"
  exit 1
fi

# Settings probed on the build host that must not leak into the iOS build.
if [ "$TARGET" = ios ]; then
  sed -e 's@^#define HAVE_FORK@// #define HAVE_FORK@' \
      -e 's@^#define HAVE_LIBUNWIND@// #define HAVE_LIBUNWIND@' \
      config.h > config.h.tmp && mv config.h.tmp config.h
fi

CFG_CFLAGS=$(sed -n 's/^CFLAGS = //p' config.mk)
CFG_CPPFLAGS=$(sed -n 's/^CPPFLAGS = //p' config.mk)

case "$TARGET" in
  ios)
    SDK=$(xcrun --sdk iphoneos --show-sdk-path)
    CC="xcrun --sdk iphoneos clang"
    ARCHFLAGS="-arch arm64 -isysroot $SDK -miphoneos-version-min=16.0"
    ;;
  host)
    CC=${CC:-cc}
    ARCHFLAGS=""
    ;;
  *)
    echo "unknown target $TARGET" >&2
    exit 2
    ;;
esac

HOST_SYSTEM=$(uname -s)
COMMON="$ARCHFLAGS -O2 -g0 -U_FORTIFY_SOURCE -fno-common -Wno-strict-aliasing \
  -fno-strict-aliasing -Wno-unused-command-line-argument -w \
  $CFG_CFLAGS $CFG_CPPFLAGS -iquote. -DNOLINEAR"
SHIM="-include $HERE/shim/inux_shim.h"
# Single quotes survive the eval below, so the macros become C strings.
INFO="'-DBUILD_MODE=\"inux\"' '-DBUILD_TOOLCHAIN=\"clang\"' \
  '-DBUILD_TIMESTAMP=\"iNux\"' '-DBLINK_COMMITS=\"0\"' '-DBLINK_GITSHA=\"${SHA}\"' \
  '-DBLINK_UNAME_V=\"iNux\"' '-DCONFIG_ARGUMENTS=\"iNux\"'"

rm -rf "$OBJ"
mkdir -p "$OBJ"

# Write one compile command per file, then run them in parallel.
JOBS="$OUT/compile.jobs"
: > "$JOBS"
for f in blink/*.c; do
  base=$(basename "$f" .c)
  extra=""
  case "$base" in
    blinkenlights) continue ;;
    blink) extra="$INFO -Dmain=inux_blink_main -Dkill=inux_kill -Denviron=inux_guest_environ" ;;
    syscall) extra="$INFO" ;;
  esac
  if [ "$HOST_SYSTEM" = Darwin ]; then
    case "$base" in
      jit|syscall|xlat) extra="$extra -D_DARWIN_C_SOURCE" ;;
    esac
  fi
  echo "$CC $COMMON $SHIM $extra -c $f -o $OBJ/$base.o" >> "$JOBS"
done
echo "$CC $ARCHFLAGS -O2 -w -c $HERE/shim/inux_engine.c -o $OBJ/inux_engine.o" >> "$JOBS"

NPROC=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
if ! tr '\n' '\0' < "$JOBS" | xargs -0 -n 1 -P "$NPROC" sh -c 'eval "$0" || { echo "FAILED: $0" >&2; exit 255; }'; then
  echo "engine compile failed" >&2
  exit 1
fi

mkdir -p "$OUT/lib" "$OUT/include"
rm -f "$OUT/lib/libinuxengine.a"
if [ "$TARGET" = ios ]; then
  xcrun --sdk iphoneos libtool -static -o "$OUT/lib/libinuxengine.a" "$OBJ"/*.o
else
  ar rcs "$OUT/lib/libinuxengine.a" "$OBJ"/*.o
fi
cp "$HERE/shim/inux_engine.h" "$OUT/include/"

if [ "$TARGET" = host ]; then
  mkdir -p "$OUT/bin"
  $CC -O2 -I"$HERE/shim" "$HERE/test/inux-run.c" "$OUT/lib/libinuxengine.a" \
    -o "$OUT/bin/inux-run" -lpthread -lm -lz
fi

echo "engine built: $OUT/lib/libinuxengine.a"
