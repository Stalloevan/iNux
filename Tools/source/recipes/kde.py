#!/usr/bin/env python3
"""Emit the build modules that stand in for the org.kde.Platform runtime on
iOS: extra-cmake-modules plus KDE Frameworks 6, in dependency order.

  kde.py --runtime-version 6.11 [--frameworks a,b,c] > modules.json

The result uses the same module schema as Tools/source/resolve.py, so
Tools/source/build.py can build these before the app's own modules. Qt6
itself is installed separately (aqtinstall), not built here.

Not every framework is iOS-clean; the list is the portable, Qt-only core
that GUI apps usually need. Frameworks that pull in X11, Wayland, D-Bus
system services or Linux-only code are left out, and an app that truly
needs one will fail at its own build step with a clear missing-package
error. Extend --frameworks to try more.
"""
import argparse
import json

# KF release tags track the runtime version's series. Flathub's org.kde
# runtime 6.x maps to the newest KF 6.y; CI passes the exact tag it wants.
DEFAULT_KF = "6.11.0"

# Dependency-ordered. Each builds on only the ones above it (+ Qt + ECM).
FRAMEWORK_ORDER = [
    "kcoreaddons", "kconfig", "ki18n", "kcodecs", "kguiaddons",
    "kwidgetsaddons", "kcolorscheme", "kconfigwidgets", "kitemviews",
    "kitemmodels", "kcompletion", "kiconthemes", "kwindowsystem",
    "kcrash", "karchive", "kdbusaddons", "kglobalaccel", "kxmlgui",
    "kbookmarks", "kjobwidgets", "ksvg", "knotifications",
]

# A few frameworks need a non-Qt third-party library first.
EXTRA_DEPS = {
    "karchive": [
        {
            "name": "zlib",
            "buildsystem": "cmake",
            "config-opts": [],
            "sources": [{"type": "archive",
                         "url": "https://zlib.net/zlib-1.3.1.tar.gz",
                         "sha256": "9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23"}],
        },
    ],
    "ki18n": [
        {
            "name": "gettext-libintl",
            "buildsystem": "autotools",
            "subdir": "gettext-runtime",
            "config-opts": ["--disable-java", "--disable-csharp", "--without-emacs"],
            "sources": [{"type": "archive",
                         "url": "https://ftp.gnu.org/gnu/gettext/gettext-0.22.5.tar.gz",
                         "sha256": "ec1705b1e969b83a9f073144ec806151db88127f5e40fe5a94cb6c8fa48996a0"}],
        },
    ],
}


def framework_module(name, tag):
    return {
        "name": name,
        "buildsystem": "cmake-ninja",
        # iOS build hygiene: no host tools, examples or tests inside the app.
        "config-opts": [
            "-DBUILD_TESTING=OFF",
            "-DBUILD_EXAMPLES=OFF",
            "-DKDE_INSTALL_USE_QT_SYS_PATHS=OFF",
        ],
        "sources": [{
            "type": "git",
            "url": "https://invent.kde.org/frameworks/%s.git" % name,
            "tag": "v" + tag,
        }],
    }


def ecm_module(tag):
    return {
        "name": "extra-cmake-modules",
        "buildsystem": "cmake",
        # ECM only ships CMake modules; it needs neither Qt nor Sphinx here.
        "config-opts": ["-DBUILD_TESTING=OFF",
                        "-DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON",
                        "-DCMAKE_DISABLE_FIND_PACKAGE_Sphinx=ON"],
        # ECM is a host build tool (CMake modules only); build for the host.
        "build-options": {"host": True},
        "host": True,
        "sources": [{
            "type": "git",
            "url": "https://invent.kde.org/frameworks/extra-cmake-modules.git",
            "tag": "v" + tag,
        }],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-version", default="")
    ap.add_argument("--kf-version", default=DEFAULT_KF)
    ap.add_argument("--frameworks", default="")
    args = ap.parse_args()

    tag = args.kf_version
    names = [f.strip() for f in args.frameworks.split(",") if f.strip()] or FRAMEWORK_ORDER

    modules = [ecm_module(tag)]
    for name in names:
        for dep in EXTRA_DEPS.get(name, []):
            if dep not in modules:
                modules.append(dep)
        modules.append(framework_module(name, tag))
    print(json.dumps({"modules": modules}, indent=2))


if __name__ == "__main__":
    main()
