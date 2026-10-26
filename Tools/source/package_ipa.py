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
    data.setdefault("MinimumOSVersion", "17.0")
    data.setdefault("UILaunchScreen", {})
    data.setdefault("CFBundleSupportedPlatforms", ["iPhoneOS"])
    data.setdefault("DTPlatformName", "iphoneos")
    data.setdefault("UIRequiredDeviceCapabilities", ["arm64"])
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


# Installed data that has no use inside an iOS app bundle.
SHARE_EXCLUDE = {
    "locale", "doc", "man", "info", "help", "qlogging-categories6", "ECM", "cmake",
    "pkgconfig", "aclocal", "gettext", "applications", "metainfo", "dbus-1",
    "kconf_update", "bash-completion", "zsh", "fish", "licenses", "kdevappwizard",
}


def copy_share(app, share_dir, app_names=()):
    """Copies an install prefix's share/ into <App>.app/share, where the
    iNux bootstrap links it into Application Support at launch. The app's
    own data folder (share/<app>) goes to the bundle root instead: on iOS,
    Qt's AppDataLocation always includes the bundle root, whatever the
    organization name, so lookups like "qml/main.qml" find it there."""
    if not share_dir or not os.path.isdir(share_dir):
        return
    dest = os.path.join(app, "share")
    os.makedirs(dest, exist_ok=True)
    total = 0
    for entry in sorted(os.listdir(share_dir)):
        if entry in SHARE_EXCLUDE or entry.startswith("."):
            continue
        src = os.path.join(share_dir, entry)
        target = os.path.join(app if entry in app_names else dest, entry)
        if entry in app_names and os.path.isdir(src):
            shutil.copytree(src, app, dirs_exist_ok=True)
            total += bundle_size(src)
            print("  app data share/%s -> bundle root" % entry)
            continue
        if os.path.isdir(src):
            shutil.copytree(src, target, symlinks=False, dirs_exist_ok=True)
        else:
            shutil.copyfile(src, target)
        total += bundle_size(target) if os.path.isdir(target) else os.path.getsize(target)
        print("  bundled share/%s" % entry)
    print("bundled %.1f MB of app data" % (total / 1e6))


def qml_classnames(search):
    """uri -> plugin class actually linked (written by InuxIOSApp.cmake)."""
    names = {}
    for root, _dirs, files in os.walk(search):
        if "inux-qml-classnames.txt" in files:
            for line in open(os.path.join(root, "inux-qml-classnames.txt")):
                parts = line.split()
                if len(parts) == 2:
                    names[parts[0]] = parts[1]
            break
    return names


def copy_qml(app, qml_dir, classnames=None):
    """Bundles QML modules installed in the prefix (lib/qml). Static builds
    of Qt 6 modules write 'prefer :/qt/qml/...' into qmldir, pointing at
    resources that only exist when the module is linked through its CMake
    target; dropping it makes the engine use the bundled files."""
    if not qml_dir or not os.path.isdir(qml_dir):
        return
    dest = os.path.join(app, "qml-modules")
    for root, _dirs, files in os.walk(qml_dir):
        rel = os.path.relpath(root, qml_dir)
        if "objects-Release" in root.split(os.sep) or "objects-Debug" in root.split(os.sep):
            continue
        for f in files:
            if f.endswith((".a", ".o", ".cmake", ".prl")):
                continue
            src = os.path.join(root, f)
            out = os.path.join(dest, rel, f)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if f == "qmldir":
                lines = [l for l in open(src).read().splitlines() if not l.startswith("prefer ")]
                uri = rel.replace(os.sep, ".")
                if classnames and uri in classnames:
                    lines = [("classname " + classnames[uri]) if l.startswith("classname ") else l for l in lines]
                open(out, "w").write("\n".join(lines) + "\n")
            else:
                shutil.copyfile(src, out)
    print("bundled QML modules from", qml_dir)


def find_app_icon(share_dir, names):
    """The app's own icon from the installed hicolor theme, largest PNG."""
    best, best_size = None, 0
    base = os.path.join(share_dir or "", "icons", "hicolor")
    if not os.path.isdir(base):
        return None
    for root, _dirs, files in os.walk(base):
        for f in files:
            stem = f.rsplit(".", 1)[0]
            if f.endswith(".png") and any(stem == n or stem.endswith("-" + n) or stem.endswith("." + n) for n in names):
                size = os.path.getsize(os.path.join(root, f))
                if size > best_size:
                    best, best_size = os.path.join(root, f), size
    return best


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
    ap.add_argument("--share-dir", default="", help="install prefix share/ to bundle")
    ap.add_argument("--qml-dir", default="", help="install prefix lib/qml to bundle")
    a = ap.parse_args()

    app = find_app(a.search)
    name = a.name or os.path.splitext(os.path.basename(app))[0]
    print("packaging", app, flush=True)
    ensure_plist(app, name, a.bundle_id)
    icon = a.icon
    if not icon:
        exe = os.path.splitext(os.path.basename(app))[0]
        icon = find_app_icon(a.share_dir, [exe, name, a.bundle_id.rsplit(".", 1)[-1]])
        if icon:
            print("using icon", icon)
    set_icon(app, icon)
    exe = os.path.splitext(os.path.basename(app))[0]
    copy_share(app, a.share_dir, app_names={exe, name})
    copy_qml(app, a.qml_dir, qml_classnames(a.search))
    ipa = make_ipa(app, a.out)
    size = os.path.getsize(ipa)
    print("wrote %s (%.1f MB)" % (ipa, size / 1e6))


if __name__ == "__main__":
    main()
