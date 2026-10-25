#!/bin/sh
# Builds a small type-2 AppImage around Engine/test/hello.c, for testing the
# porter and the engine end to end:  make-test-appimage.sh OUT.AppImage
set -eu
OUT=$1
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

APPDIR="$WORK/Hello.AppDir"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications"
cc -O2 -o "$APPDIR/usr/bin/hello" "$HERE/hello.c" -lz
ln -s usr/bin/hello "$APPDIR/AppRun"
cat > "$APPDIR/hello.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Hello iNux
Exec=hello --interactive
Icon=hello
Terminal=true
Categories=Utility;
EOF
cp "$APPDIR/hello.desktop" "$APPDIR/usr/share/applications/"
# An absolute symlink, like many real AppImages contain.
ln -s /usr/bin/hello "$APPDIR/usr/bin/hello-abs"
# 1x1 PNG icon.
printf '\211PNG\r\n\032\n\000\000\000\rIHDR\000\000\000\001\000\000\000\001\010\006\000\000\000\037\025\304\211\000\000\000\rIDATx\234c\370\017\004\000\011\373\003\375\343U\362\234\000\000\000\000IEND\256B`\202' > "$APPDIR/hello.png"
ln -s hello.png "$APPDIR/.DirIcon"

# Stand-in for the AppImage runtime: any ELF, tagged with the type-2 magic.
printf 'int main(void){return 0;}\n' > "$WORK/runtime.c"
cc -O2 -o "$WORK/runtime" "$WORK/runtime.c"
printf 'AI\002' | dd of="$WORK/runtime" bs=1 seek=8 conv=notrunc 2>/dev/null

mksquashfs "$APPDIR" "$WORK/fs.squashfs" -root-owned -noappend -quiet >/dev/null
cat "$WORK/runtime" "$WORK/fs.squashfs" > "$OUT"
chmod +x "$OUT"
echo "built $OUT"
