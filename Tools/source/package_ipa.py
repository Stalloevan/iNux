#!/usr/bin/env python3
"""Find the built .app and wrap it as an unsigned IPA.

  package_ipa.py --search DIR --out NAME.ipa [--name "Display Name"]
                 [--bundle-id com.inux.ported.x] [--icon PNG]

Picks the largest .app under DIR (the real app, not a helper), ensures its
Info.plist has the keys the installer needs, optionally sets an icon, and
zips it as Payload/<App>.app. The IPA is unsigned; the user signs it with
their own tool before installing.
"""
import argparse
import glob
import os
import plistlib
import shutil
import subprocess
import sys
import zipfile


def find_app(search):
    apps = []
    for root, dirs, _files in os.walk(search):
        for d in list(dirs):
            if d.endswith(".app"):
                p = os.path.join(root, d)
                apps.append((bundle_size(p), p))
                dirs.remove(d)  # don't descend into the bundle
    if not apps:
        sys.exit("no .app bundle found under " + search)
    apps.sort()
    return apps[-1][1]


def bundle_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def ensure_plist(app, name, bundle_id):
    p = os.path.join(app, "Info.plist")
    data = {}
    if os.path.exists(p):
        with open(p, "rb") as f:
            data = plistlib.load(f)
    exe = data.get("CFBundleExecutable") or os.path.splitext(os.path.basename(app))[0]
    data.setdefault("CFBundleExecutable", exe)
    data.setdefault("CFBundleIdentifier", bundle_id)
    data.setdefault("CFBundleName", name)
    data.setdefault("CFBundleDisplayName", name)
    data.setdefault("CFBundleShortVersionString", "1.0")
    data.setdefault("CFBundleVersion", "1")
    data.setdefault("CFBundlePackageType", "APPL")
    data.setdefault("LSRequiresIPhoneOS", True)
    data.setdefault("UIDeviceFamily", [1, 2])
    data.setdefault("MinimumOSVersion", "16.0")
    data.setdefault("UILaunchScreen", {})
    data.setdefault("UISupportedInterfaceOrientations",
                    ["UIInterfaceOrientationPortrait",
                     "UIInterfaceOrientationLandscapeLeft",
                     "UIInterfaceOrientationLandscapeRight"])
    with open(p, "wb") as f:
        plistlib.dump(data, f)
    return exe


def set_icon(app, icon):
    """Installs a single 1024px icon; iOS accepts one CFBundleIcons entry."""
    if not icon or not os.path.exists(icon):
        return
    dest = os.path.join(app, "AppIcon60x60@2x.png")
    try:
        subprocess.run(["sips", "-s", "format", "png", "-z", "120", "120", icon, "--out", dest],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        shutil.copyfile(icon, dest)
    p = os.path.join(app, "Info.plist")
    with open(p, "rb") as f:
        data = plistlib.load(f)
    data["CFBundleIcons"] = {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon60x60"]}}
    with open(p, "wb") as f:
        plistlib.dump(data, f)


def make_ipa(app, out):
    out = os.path.abspath(out)
    if os.path.exists(out):
        os.remove(out)
    base = os.path.dirname(app)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(app):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, base)
                z.write(full, os.path.join("Payload", rel))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--bundle-id", default="com.inux.ported.app")
    ap.add_argument("--icon", default="")
    a = ap.parse_args()

    app = find_app(a.search)
    name = a.name or os.path.splitext(os.path.basename(app))[0]
    print("packaging", app, flush=True)
    ensure_plist(app, name, a.bundle_id)
    set_icon(app, a.icon)
    ipa = make_ipa(app, a.out)
    size = os.path.getsize(ipa)
    print("wrote %s (%.1f MB)" % (ipa, size / 1e6))


if __name__ == "__main__":
    main()
