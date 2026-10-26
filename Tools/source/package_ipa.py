#!/usr/bin/env python3
"""Find the built .app and wrap it as an unsigned IPA.

  package_ipa.py --search DIR --out NAME.ipa [--name "Display Name"]
                 [--bundle-id com.inux.ported.x] [--icon PNG]
  package_ipa.py --exe PATH --layout gtk --out NAME.ipa ...

Qt/KDE apps: picks the largest .app under DIR (the real app, not a
helper). GTK apps (built with Meson, which makes no bundle): builds the
.app around the installed executable (--exe), with the install prefix's
data under <App>.app/share, where the UIKit backend points GLib and GTK.

Then ensures the Info.plist has the keys the installer needs, sets an icon,
and zips it as Payload/<App>.app. The IPA is unsigned; the user signs it
with their own tool before installing.
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


def make_bundle(exe, work):
    """A minimal .app around a plain executable."""
    exe_name = os.path.basename(exe)
    app = os.path.join(work, exe_name + ".app")
    if os.path.isdir(app):
        shutil.rmtree(app)
    os.makedirs(app)
    shutil.copy2(exe, os.path.join(app, exe_name))
    os.chmod(os.path.join(app, exe_name), 0o755)
    with open(os.path.join(app, "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleExecutable": exe_name}, f)
    return app


def ensure_plist(app, name, bundle_id):
    p = os.path.join(app, "Info.plist")
    data = {}
    if os.path.exists(p):
        with open(p, "rb") as f:
            data = plistlib.load(f)
    exe = data.get("CFBundleExecutable") or os.path.splitext(os.path.basename(app))[0]
    data.setdefault("CFBundleExecutable", exe)
    data.setdefault("CFBundleIdentifier", bundle_id)
    # Qt's generated Info.plist can carry unexpanded placeholders such as
    # "{PRODUCT_NAME}"; always write the real display name.
    for key in ("CFBundleName", "CFBundleDisplayName"):
        data[key] = name
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


ICON_SIZES = {  # file name -> pixels
    "AppIcon60x60@2x.png": 120,
    "AppIcon60x60@3x.png": 180,
    "AppIcon76x76@2x~ipad.png": 152,
    "AppIcon83.5x83.5@2x~ipad.png": 167,
}


def square_opaque_icon(icon):
    """The icon as an opaque square (PIL image) or None.

    iOS draws transparent pixels black and applies its own rounded mask, so
    a transparent margin shows up as a black frame around a shrunken icon.
    The artwork is cropped to its bounds. A (nearly) square tile, like most
    KDE icons, then fills the whole square, with what little transparency
    is left (rounded corners) flattened onto its edge colour. Other shapes
    (GNOME's round or irregular icons) are centred on a white tile with a
    margin, the way iOS shows such icons."""
    try:
        from PIL import Image
    except ImportError:
        return None
    im = Image.open(icon).convert("RGBA")
    alpha = im.getchannel("A")
    box = alpha.point(lambda v: 255 if v > 24 else 0).getbbox()
    if box:
        im = im.crop(box)
    w, h = im.size
    side = max(w, h)

    # How much of the cropped square the artwork covers.
    hist = im.getchannel("A").point(lambda v: 255 if v > 200 else 0).histogram()
    coverage = hist[255] / float(side * side)
    if coverage < 0.88:
        side = int(side / 0.8)
        canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
        canvas.alpha_composite(im, ((side - w) // 2, (side - h) // 2))
        return canvas.convert("RGB")

    # Edge colour: average of the opaque pixels along the border.
    px = im.load()
    samples = []
    for x in range(w):
        samples += [px[x, 0], px[x, h - 1]]
    for y in range(h):
        samples += [px[0, y], px[w - 1, y]]
    samples = [c for c in samples if c[3] > 200] or [(128, 128, 128, 255)]
    bg = tuple(sum(c[i] for c in samples) // len(samples) for i in range(3)) + (255,)
    canvas = Image.new("RGBA", (side, side), bg)
    canvas.alpha_composite(im, ((side - w) // 2, (side - h) // 2))
    return canvas.convert("RGB")


def svg_to_png(svg, work):
    """Renders a scalable app icon with the host's rsvg-convert."""
    tool = shutil.which("rsvg-convert")
    if not tool:
        return None
    out = os.path.join(work, "icon-1024.png")
    try:
        subprocess.run([tool, "-w", "1024", "-h", "1024", "-o", out, svg], check=True)
        return out
    except Exception:
        return None


def set_icon(app, icon):
    """Installs the app icon in the sizes iPhone and iPad ask for."""
    if not icon or not os.path.exists(icon):
        return
    art = square_opaque_icon(icon)
    files = []
    for fname, px in ICON_SIZES.items():
        dest = os.path.join(app, fname)
        if art is not None:
            from PIL import Image
            art.resize((px, px), Image.LANCZOS).save(dest, "PNG")
        elif fname.startswith("AppIcon60x60@2x"):
            try:
                subprocess.run(["sips", "-s", "format", "png", "-z", str(px), str(px), icon, "--out", dest],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                shutil.copyfile(icon, dest)
        else:
            continue
        files.append(fname)
    p = os.path.join(app, "Info.plist")
    with open(p, "rb") as f:
        data = plistlib.load(f)
    data["CFBundleIcons"] = {"CFBundlePrimaryIcon": {
        "CFBundleIconFiles": ["AppIcon60x60", "AppIcon76x76", "AppIcon83.5x83.5"]}}
    data["CFBundleIcons~ipad"] = data["CFBundleIcons"]
    with open(p, "wb") as f:
        plistlib.dump(data, f)


def metainfo_name(share_dir, app_id):
    """The app's display name from its AppStream metainfo (untranslated)."""
    import xml.etree.ElementTree as ET
    base = os.path.join(share_dir or "", "metainfo")
    if not os.path.isdir(base):
        return None
    for f in sorted(os.listdir(base)):
        if not f.endswith(".xml") or app_id.lower() not in f.lower():
            continue
        try:
            root = ET.parse(os.path.join(base, f)).getroot()
        except Exception:
            continue
        for n in root.findall("name"):
            if not n.attrib and n.text and n.text.strip():
                return n.text.strip()
    return None


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


# Installed data a GTK app does not use at run time on iOS.
GTK_SHARE_EXCLUDE = SHARE_EXCLUDE | {
    "gtk-doc", "vala", "gir-1.0", "installed-tests", "thumbnailers", "gdb", "fontconfig",
    "xml", "emacs", "vim", "polkit-1", "systemd", "GConf", "mime", "gnome-shell",
    "search-providers", "help", "appdata",
}
GTK_SHARE_EXCLUDE_PATHS = {
    os.path.join("gtk-4.0", "valgrind"), os.path.join("gtk-4.0", "gtk4builder.rng"),
    os.path.join("glib-2.0", "codegen"), os.path.join("glib-2.0", "gdb"),
    os.path.join("glib-2.0", "valgrind"), os.path.join("icons", "Adwaita", "cursors"),
    os.path.join("gettext"),
}


def copy_share_gtk(app, share_dir):
    """Copies the prefix's share/ into <App>.app/share (XDG_DATA_DIRS on the
    device) and compiles the GSettings schemas there."""
    if not share_dir or not os.path.isdir(share_dir):
        return
    dest = os.path.join(app, "share")

    def ignore(directory, names):
        rel = os.path.relpath(directory, share_dir)
        skipped = []
        for n in names:
            path = os.path.normpath(os.path.join(rel, n))
            if (rel == "." and n in GTK_SHARE_EXCLUDE) or path in GTK_SHARE_EXCLUDE_PATHS or n.startswith("."):
                skipped.append(n)
            # Emoji names for the emoji chooser: English only (the UI is).
            elif rel == os.path.join("gtk-4.0", "emoji") and not n.startswith("en"):
                skipped.append(n)
        return skipped

    shutil.copytree(share_dir, dest, ignore=ignore, dirs_exist_ok=True)
    print("bundled share/: " + ", ".join(sorted(os.listdir(dest))))
    schemas = os.path.join(dest, "glib-2.0", "schemas")
    if os.path.isdir(schemas):
        tool = shutil.which("glib-compile-schemas")
        if tool:
            subprocess.run([tool, schemas], check=True)
            print("compiled GSettings schemas")
        else:
            print("warning: glib-compile-schemas not found; settings will not work")
    print("bundled %.1f MB of app data" % (bundle_size(dest) / 1e6))


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


def find_app_icon(share_dir, names, work=None):
    """The app's own icon from the installed hicolor theme: the largest PNG,
    or a rendering of the scalable SVG when there is no PNG."""
    best, best_size = None, 0
    svg = None
    base = os.path.join(share_dir or "", "icons", "hicolor")
    if not os.path.isdir(base):
        return None
    names = [n for n in names if n]
    for root, _dirs, files in os.walk(base):
        for f in files:
            stem = f.rsplit(".", 1)[0]
            if not any(stem == n or stem.endswith("-" + n) or stem.endswith("." + n) for n in names):
                continue
            if stem.endswith("-symbolic"):
                continue
            if f.endswith(".png"):
                size = os.path.getsize(os.path.join(root, f))
                if size > best_size:
                    best, best_size = os.path.join(root, f), size
            elif f.endswith(".svg") and svg is None:
                svg = os.path.join(root, f)
    if best is None and svg and work:
        best = svg_to_png(svg, work)
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
    ap.add_argument("--search", default="")
    ap.add_argument("--exe", default="", help="app executable to bundle (GTK apps)")
    ap.add_argument("--layout", default="qt", choices=["qt", "gtk"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--app-id", default="")
    ap.add_argument("--bundle-id", default="com.inux.ported.app")
    ap.add_argument("--icon", default="")
    ap.add_argument("--share-dir", default="", help="install prefix share/ to bundle")
    ap.add_argument("--qml-dir", default="", help="install prefix lib/qml to bundle")
    ap.add_argument("--simulator", action="store_true", help="the app was built for the iOS Simulator")
    a = ap.parse_args()

    work = os.path.abspath("ipa-work")
    os.makedirs(work, exist_ok=True)
    if a.exe:
        app = make_bundle(a.exe, work)
    else:
        if not a.search:
            sys.exit("give --search or --exe")
        app = find_app(a.search)
    exe = os.path.splitext(os.path.basename(app))[0]
    name = a.name or exe
    nice = None
    for key in (a.app_id, name, exe):
        if key:
            nice = metainfo_name(a.share_dir, key)
            if nice:
                break
    if nice:
        name = nice
    print("packaging", app, "as", repr(name), flush=True)
    ensure_plist(app, name, a.bundle_id)
    if a.layout == "gtk" or a.simulator:
        p = os.path.join(app, "Info.plist")
        with open(p, "rb") as f:
            data = plistlib.load(f)
        if a.layout == "gtk":
            # iPad trackpads and mice reach the app as pointer events.
            data["UIApplicationSupportsIndirectInputEvents"] = True
        if a.simulator:
            data["CFBundleSupportedPlatforms"] = ["iPhoneSimulator"]
            data["DTPlatformName"] = "iphonesimulator"
        with open(p, "wb") as f:
            plistlib.dump(data, f)
    icon = a.icon
    if not icon:
        icon = find_app_icon(a.share_dir, [a.app_id, exe, name, a.bundle_id.rsplit(".", 1)[-1]], work)
        if icon:
            print("using icon", icon)
    set_icon(app, icon)
    if a.layout == "gtk":
        copy_share_gtk(app, a.share_dir)
    else:
        copy_share(app, a.share_dir, app_names={exe, name})
        copy_qml(app, a.qml_dir, qml_classnames(a.search))
    ipa = make_ipa(app, a.out)
    size = os.path.getsize(ipa)
    print("wrote %s (%.1f MB)" % (ipa, size / 1e6))


if __name__ == "__main__":
    main()
