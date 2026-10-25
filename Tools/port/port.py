#!/usr/bin/env python3
"""Extract an AppImage or Flatpak bundle into a payload folder for the iOS wrapper.

Usage: port.py --src FILE --kind auto|appimage|flatpak --name NAME --out DIR

Writes DIR/rootfs/ (the extracted application), DIR/manifest.json and,
when one is found, DIR/icon.png. Runs on a Linux CI runner and needs
squashfs-tools (AppImage) or flatpak + ostree (Flatpak).
"""
import argparse
import configparser
import json
import os
import shutil
import struct
import subprocess
import sys
from collections import Counter

ELF_MACHINES = {0x03: "x86", 0x28: "arm", 0x3E: "x86_64", 0xB7: "aarch64", 0xF3: "riscv"}


def elf_arch(path):
    try:
        with open(path, "rb") as f:
            head = f.read(20)
    except OSError:
        return None
    if len(head) < 20 or head[:4] != b"\x7fELF":
        return None
    return ELF_MACHINES.get(struct.unpack_from("<H", head, 18)[0], "unknown")


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, **kw)


def squashfs_ok(src, offset):
    r = subprocess.run(["unsquashfs", "-s", "-o", str(offset), src],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def appimage_offset(src):
    with open(src, "rb") as f:
        head = f.read(64)
        if head[:4] != b"\x7fELF":
            sys.exit("Not an ELF file")
        if head[4] == 2:
            shoff = struct.unpack_from("<Q", head, 0x28)[0]
            shentsize, shnum = struct.unpack_from("<HH", head, 0x3A)
        else:
            shoff = struct.unpack_from("<I", head, 0x20)[0]
            shentsize, shnum = struct.unpack_from("<HH", head, 0x2E)
        guess = shoff + shentsize * shnum
        f.seek(guess)
        if f.read(4) == b"hsqs" and squashfs_ok(src, guess):
            return guess
    # Fallback: scan for the squashfs magic.
    chunk = 1 << 20
    with open(src, "rb") as f:
        pos = 0
        carry = b""
        while True:
            data = f.read(chunk)
            if not data:
                break
            buf = carry + data
            start = 0
            while True:
                i = buf.find(b"hsqs", start)
                if i < 0:
                    break
                cand = pos - len(carry) + i
                if cand > 0 and squashfs_ok(src, cand):
                    return cand
                start = i + 1
            carry = buf[-3:]
            pos += len(data)
    sys.exit("Could not find the squashfs image inside the AppImage (type 1 AppImages are not supported)")


def extract_appimage(src, rootfs):
    off = appimage_offset(src)
    print("squashfs offset:", off)
    r = run(["unsquashfs", "-f", "-n", "-d", rootfs, "-o", str(off), src])
    if r.returncode != 0:
        sys.exit("unsquashfs failed")


def extract_flatpak(src, rootfs, work):
    repo = os.path.join(work, "flatpak-repo")
    r = run(["flatpak", "build-import-bundle", repo, src])
    if r.returncode != 0:
        sys.exit("flatpak build-import-bundle failed (is this a single-file .flatpak bundle?)")
    refs = subprocess.run(["ostree", "refs", "--repo=" + repo], capture_output=True, text=True).stdout.split()
    if not refs:
        sys.exit("No refs found in the bundle")
    ref = next((x for x in refs if x.startswith("app/")), refs[0])
    print("ref:", ref)
    r = run(["ostree", "--repo=" + repo, "checkout", "-U", ref, rootfs])
    if r.returncode != 0:
        sys.exit("ostree checkout failed")


def prune_symlinks(root):
    """Drop symlinks that are absolute or point outside the payload."""
    real_root = os.path.realpath(root)
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            if not os.path.islink(p):
                continue
            target = os.readlink(p)
            resolved = os.path.realpath(os.path.join(dirpath, target))
            if os.path.isabs(target) or not resolved.startswith(real_root + os.sep):
                os.unlink(p)
                removed += 1
    return removed


def find_desktop(root):
    candidates = []
    top = [os.path.join(root, n) for n in os.listdir(root) if n.endswith(".desktop")]
    candidates.extend(top)
    for sub in ("export/share/applications", "usr/share/applications", "files/share/applications"):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            candidates.extend(os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".desktop"))
    for path in candidates:
        if os.path.isfile(path) and not os.path.islink(path):
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            cp.optionxform = str
            try:
                cp.read(path, encoding="utf-8")
                if cp.has_section("Desktop Entry"):
                    e = cp["Desktop Entry"]
                    return {"name": e.get("Name"), "exec": e.get("Exec"), "icon": e.get("Icon")}
            except Exception:
                continue
    return {}


def find_icon(root, icon_name, out_dir):
    best, best_size = None, -1
    diricon = os.path.join(root, ".DirIcon")
    if os.path.isfile(diricon):
        with open(diricon, "rb") as f:
            if f.read(8) == b"\x89PNG\r\n\x1a\n":
                best, best_size = diricon, os.path.getsize(diricon)
    if icon_name:
        for dirpath, _dirs, files in os.walk(root):
            for n in files:
                if n == icon_name + ".png":
                    p = os.path.join(dirpath, n)
                    size = os.path.getsize(p)
                    if size > best_size:
                        best, best_size = p, size
    if best:
        shutil.copyfile(best, os.path.join(out_dir, "icon.png"))
        return True
    return False


def scan(root):
    archs = Counter()
    binaries = []
    count = 0
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for n in files:
            p = os.path.join(dirpath, n)
            if os.path.islink(p):
                continue
            count += 1
            try:
                total += os.path.getsize(p)
            except OSError:
                continue
            a = elf_arch(p)
            if a:
                archs[a] += 1
                if ".so" not in n and len(binaries) < 25:
                    binaries.append({"path": os.path.relpath(p, root), "arch": a})
    return count, total, archs, binaries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--kind", default="auto")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    kind = args.kind
    if kind == "auto":
        kind = "flatpak" if args.src.lower().endswith(".flatpak") else "appimage"

    out = os.path.abspath(args.out)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    rootfs = os.path.join(out, "rootfs")
    work = os.path.join(os.path.dirname(out), "port-work")
    os.makedirs(work, exist_ok=True)

    if kind == "appimage":
        extract_appimage(args.src, rootfs)
    elif kind == "flatpak":
        extract_flatpak(args.src, rootfs, work)
    else:
        sys.exit("Unknown kind: " + kind)

    notes = []
    removed = prune_symlinks(rootfs)
    if removed:
        notes.append("Removed %d absolute or out-of-tree symlinks (iOS app bundles cannot contain them)." % removed)

    desktop = find_desktop(rootfs)
    has_icon = find_icon(rootfs, desktop.get("icon"), out)
    count, total, archs, binaries = scan(rootfs)
    arch = archs.most_common(1)[0][0] if archs else "none"

    entry = None
    if kind == "appimage" and os.path.exists(os.path.join(rootfs, "AppRun")):
        entry = "AppRun"
    elif desktop.get("exec"):
        entry = desktop["exec"]
    elif binaries:
        entry = binaries[0]["path"]

    if not archs:
        notes.append("No ELF binaries were found; this may be a script-only or data-only payload.")
    elif len(archs) > 1:
        notes.append("Mixed architectures in payload: " + ", ".join("%s x%d" % kv for kv in archs.items()))
    if arch in ("x86_64", "x86"):
        notes.append("x86 payload: needs an emulation backend to run on iOS (none is bundled yet).")
    elif arch == "aarch64":
        notes.append("ARM64 Linux payload: needs a Linux userland layer; iOS cannot exec it directly.")
    if kind == "flatpak":
        notes.append("Flatpak runtime (libraries from org.freedesktop.Platform and friends) is NOT included.")
    if total > 2 * 1024 ** 3:
        notes.append("Payload is over 2 GB; sideloading tools may reject it.")

    manifest = {
        "name": args.name,
        "kind": kind,
        "arch": arch,
        "entry": entry,
        "desktopName": desktop.get("name"),
        "desktopExec": desktop.get("exec"),
        "fileCount": count,
        "totalBytes": total,
        "binaries": binaries,
        "notes": notes,
        "hasIcon": has_icon,
    }
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
