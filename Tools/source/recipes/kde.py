#!/usr/bin/env python3
"""Build recipe for KDE/Qt apps on iOS.

  kde.py --graph graph.json --plan plan.json --kf-version 6.31.0
         --recipe-out recipe.json --plan-out plan-ios.json

graph.json comes from kf_graph.py (the KDE Frameworks the app needs, in
dependency order). The recipe stands in for the org.kde.Platform runtime:

  1. host tools, built natively for macOS: Extra CMake Modules, and the
     frameworks that provide build-time programs (kconfig's
     kconfig_compiler, kpackage's kpackagetool) with their dependencies;
     iOS builds find them through KF6_HOST_TOOLING, as KDE's own
     cross-builds do;
  2. the iNux compat libraries (QProcess and libintl for iOS);
  3. stand-ins for frameworks that cannot exist on iOS (KDocTools:
     no help viewer; kdbusaddons and kcrash are patched to work
     without D-Bus or a crash-reporter process);
  4. third-party libraries the app needs (sound: OpenAL, libsndfile);
  5. the frameworks themselves, with iOS options and patches.

The plan's own modules (libraries such as libkdegames, then the app) are
marked so the build driver applies the same iOS options, and the final
module is turned into an app bundle.
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)

# Frameworks never built for iOS.
#   kglobalaccel: global shortcuts need D-Bus; kxmlgui skips it on Apple.
#   kdoctools: replaced by a CMake-only stand-in (no help viewer on iOS).
#   kbreezeicons/breeze-icons: icons are optional (USE_BreezeIcons=OFF).
IOS_SKIP = {"kglobalaccel", "kdoctools", "kbreezeicons", "breeze-icons"}

# Host-side frameworks that provide build-time tools, with what they need.
HOST_TOOL_DEPS = {
    "kconfig": [],
    "kpackage": ["karchive", "kcoreaddons", "ki18n"],
}

COMMON_KDE = [
    "-DBUILD_TESTING=OFF",
    "-DBUILD_QCH=OFF",
    "-DBUILD_PYTHON_BINDINGS=OFF",
    "-DBUILD_DESIGNERPLUGIN=OFF",
    "-DUSE_DBUS=OFF",
    "-DWITH_X11=OFF",
    "-DWITH_WAYLAND=OFF",
    "-DKDE_INSTALL_USE_QT_SYS_PATHS=OFF",
]

# Per-framework iOS options.
FRAMEWORK_OPTS = {
    "karchive": ["-DWITH_BZIP2=OFF", "-DWITH_LIBLZMA=OFF", "-DWITH_LIBZSTD=OFF", "-DWITH_OPENSSL=OFF"],
    "kconfig": ["-DKCONFIG_USE_QML=OFF"],
    "kcoreaddons": ["-DKCOREADDONS_USE_QML=OFF", "-DENABLE_INOTIFY=OFF", "-DENABLE_PROCSTAT=OFF"],
    "kiconthemes": ["-DUSE_BreezeIcons=OFF", "-DKICONTHEMES_USE_QTQUICK=OFF"],
    "kdnssd": ["-DCMAKE_DISABLE_FIND_PACKAGE_Avahi=ON", "-DCMAKE_DISABLE_FIND_PACKAGE_DNSSD=ON"],
    "kguiaddons": ["-DBUILD_GEO_SCHEME_HANDLER=OFF"],
    "attica": ["-DATTICA_STATIC_BUILD=ON"],
    "kxmlgui": ["-DFORCE_DISABLE_KGLOBALACCEL=ON"],
}

# Per-framework iOS source patches (recipes/patches/README.md).
FRAMEWORK_PATCHES = {
    "kconfig": ["patches/kconfig-ios-qprocess.patch"],
    "kcrash": ["patches/kcrash-ios-noop.patch"],
    "kdbusaddons": ["patches/kdbusaddons-ios-nodbus.patch"],
    "kpackage": ["patches/kpackage-static-build.patch"],
}

# Fixes for static builds, needed on the host side too.
HOST_PATCHES = {
    "kpackage": ["patches/kpackage-static-build.patch"],
}

HOST_OPTS = {
    "karchive": FRAMEWORK_OPTS["karchive"],
    "kcoreaddons": ["-DKCOREADDONS_USE_QML=OFF"],
    "ki18n": ["-DBUILD_WITH_QML=OFF"],
    "kconfig": ["-DKCONFIG_USE_QML=OFF", "-DKCONFIG_USE_GUI=OFF"],
}

# Third-party libraries, keyed by the CMake package name a project requires.
# "app-link": libraries/frameworks the final app must link, because
# projects often find these with plain find_library() and miss their deps.
XIPH = "https://github.com/xiph/%s"
THIRD_PARTY = {
    "OpenAL": [
        {"name": "openal-soft", "url": "https://github.com/kcat/openal-soft", "tag": "1.24.3",
         "opts": ["-DLIBTYPE=STATIC", "-DALSOFT_UTILS=OFF", "-DALSOFT_EXAMPLES=OFF",
                  "-DALSOFT_TESTS=OFF", "-DALSOFT_INSTALL_EXAMPLES=OFF", "-DALSOFT_INSTALL_UTILS=OFF",
                  "-DALSOFT_BACKEND_SDL2=OFF", "-DALSOFT_BACKEND_SDL3=OFF"],
         "app-link": ["lib/libopenal.a", "-framework AudioToolbox", "-framework CoreAudio",
                      "-framework AVFoundation", "-framework Foundation"]},
    ],
    "SndFile": [
        {"name": "ogg", "url": XIPH % "ogg", "tag": "v1.3.6",
         "opts": ["-DINSTALL_DOCS=OFF"], "app-link": ["lib/libogg.a"]},
        {"name": "vorbis", "url": XIPH % "vorbis", "tag": "v1.3.7",
         "opts": [], "app-link": ["lib/libvorbisenc.a", "lib/libvorbisfile.a", "lib/libvorbis.a"]},
        {"name": "flac", "url": XIPH % "flac", "tag": "1.5.0",
         "opts": ["-DBUILD_PROGRAMS=OFF", "-DBUILD_EXAMPLES=OFF", "-DBUILD_DOCS=OFF",
                  "-DINSTALL_MANPAGES=OFF", "-DBUILD_CXXLIBS=OFF", "-DWITH_OGG=ON"],
         "app-link": ["lib/libFLAC.a"]},
        {"name": "opus", "url": XIPH % "opus", "tag": "v1.5.2",
         "opts": ["-DOPUS_BUILD_PROGRAMS=OFF", "-DOPUS_BUILD_TESTING=OFF", "-DOPUS_INSTALL_CMAKE_CONFIG_MODULE=ON"],
         "app-link": ["lib/libopus.a"]},
        {"name": "libsndfile", "url": "https://github.com/libsndfile/libsndfile", "tag": "1.2.2",
         "opts": ["-DBUILD_PROGRAMS=OFF", "-DBUILD_EXAMPLES=OFF", "-DENABLE_EXTERNAL_LIBS=ON",
                  "-DENABLE_MPEG=OFF", "-DINSTALL_MANPAGES=OFF", "-DENABLE_CPACK=OFF", "-DBUILD_REGTEST=OFF",
                  "-DENABLE_PACKAGE_CONFIG=ON"],
         "app-link": ["lib/libsndfile.a"]},
    ],
}


def git_module(name, url, tag, opts, **extra):
    m = {
        "name": name,
        "buildsystem": "cmake-ninja",
        "config-opts": ["-DBUILD_TESTING=OFF"] + list(opts),
        "sources": [{"type": "git", "url": url, "tag": tag}],
    }
    m.update(extra)
    return m


def kde_repo(name):
    return "https://github.com/KDE/%s" % name


def host_modules(order, kf_tag):
    mods = [git_module("host-extra-cmake-modules", kde_repo("extra-cmake-modules"), "v" + kf_tag,
                       ["-DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON", "-DCMAKE_DISABLE_FIND_PACKAGE_Sphinx=ON",
                        "-DBUILD_HTML_DOCS=OFF", "-DBUILD_MAN_DOCS=OFF", "-DBUILD_QTHELP_DOCS=OFF"],
                       host=True, ecm=True)]
    wanted = []
    for tool, deps in HOST_TOOL_DEPS.items():
        if tool in order:
            for d in deps + [tool]:
                if d not in wanted:
                    wanted.append(d)
    # Keep dependency order.
    known = ["karchive", "kcoreaddons", "ki18n", "kconfig", "kpackage"]
    for name in sorted(wanted, key=lambda n: known.index(n) if n in known else 99):
        mods.append(git_module("host-" + name, kde_repo(name), "v" + kf_tag,
                               ["-DBUILD_QCH=OFF", "-DBUILD_PYTHON_BINDINGS=OFF", "-DUSE_DBUS=OFF",
                                "-DBUILD_DESIGNERPLUGIN=OFF"] + HOST_OPTS.get(name, []),
                               host=True))
        if name in HOST_PATCHES:
            mods[-1]["inux-patches"] = HOST_PATCHES[name]
    return mods


def compat_modules(kf_tag):
    return [
        # ECM again, for iOS builds: it is only CMake code, but the iOS
        # prefix must not search the host one (host libraries would leak
        # into the app).
        git_module("extra-cmake-modules", kde_repo("extra-cmake-modules"), "v" + kf_tag,
                   ["-DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON", "-DCMAKE_DISABLE_FIND_PACKAGE_Sphinx=ON",
                    "-DBUILD_HTML_DOCS=OFF", "-DBUILD_MAN_DOCS=OFF", "-DBUILD_QTHELP_DOCS=OFF"],
                   ecm=True),
        {"name": "inux-compat", "buildsystem": "cmake-ninja", "config-opts": [],
         "sources": [{"type": "dir", "path": os.path.join(SOURCE, "compat")}]},
        {"name": "inux-kdoctools-stub", "buildsystem": "cmake", "config-opts": ["-DINUX_KF_VERSION=" + kf_tag],
         "sources": [{"type": "dir", "path": os.path.join(SOURCE, "stubs", "kdoctools")}]},
    ]


def third_party_modules(packages):
    mods = []
    for pkg in packages:
        for spec in THIRD_PARTY.get(pkg, []):
            if any(m["name"] == spec["name"] for m in mods):
                continue
            mods.append(git_module(spec["name"], spec["url"], spec["tag"], spec["opts"],
                                   **{"inux-app-link": spec.get("app-link", [])}))
    return mods


def framework_modules(order, kf_tag):
    mods = []
    for name in order:
        if name in IOS_SKIP:
            continue
        m = git_module(name, kde_repo(name), "v" + kf_tag, COMMON_KDE + FRAMEWORK_OPTS.get(name, []),
                       kde=True)
        if name in FRAMEWORK_PATCHES:
            m["inux-patches"] = FRAMEWORK_PATCHES[name]
        mods.append(m)
    return mods


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--kf-version", required=True)
    ap.add_argument("--recipe-out", required=True)
    ap.add_argument("--plan-out", required=True)
    a = ap.parse_args()

    graph = json.load(open(a.graph))
    plan = json.load(open(a.plan))
    order = graph["order"]

    packages = set()
    for root in graph.get("roots", {}).values():
        packages.update(root.get("other", []))
    for fw in graph.get("frameworks", {}).values():
        packages.update(fw.get("other", []))

    modules = host_modules(order, a.kf_version)
    modules += compat_modules(a.kf_version)
    modules += third_party_modules(sorted(packages))
    modules += framework_modules(order, a.kf_version)
    json.dump({"modules": modules}, open(a.recipe_out, "w"), indent=2)

    plan_modules = [m for m in plan["modules"] if not m.get("missing")]
    for i, m in enumerate(plan_modules):
        m["kde"] = True
        m["config-opts"] = COMMON_KDE + [o for o in m.get("config-opts", [])
                                         if not o.startswith("-DCMAKE_INSTALL_PREFIX")]
        if i == len(plan_modules) - 1:
            m["app"] = True
            m["app-target"] = plan.get("command") or ""
    plan["modules"] = plan_modules
    json.dump(plan, open(a.plan_out, "w"), indent=2)

    print("recipe: %d modules" % len(modules))
    for m in modules:
        tags = [k for k in ("host", "kde", "inux-patches", "inux-app-link") if m.get(k)]
        print("  %-26s %s" % (m["name"], " ".join(tags)))
    print("plan:")
    for m in plan_modules:
        print("  %-26s %s" % (m["name"], "app -> " + m["app-target"] if m.get("app") else "library"))


if __name__ == "__main__":
    main()
