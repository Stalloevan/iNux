#!/usr/bin/env python3
"""Build recipe for GTK/GNOME apps on iOS.

  gnome.py --plan plan.json [--src-plan DIR] --recipe-out recipe.json
           --plan-out plan-ios.json

Stands in for the org.gnome.Platform runtime: everything an app built
against GTK 4 and libadwaita expects, cross-compiled statically for iOS.

  1. a libintl without catalogs (English UI), as for KDE apps;
  2. GLib (libffi and PCRE2 come in as Meson subprojects), image codecs,
     FreeType, Fontconfig, FriBidi, HarfBuzz, Pixman, Cairo, Pango,
     gdk-pixbuf, Graphene and libepoxy;
  3. GTK 4 with the iNux UIKit backend (Tools/source/gtk-uikit): a GDK
     backend that draws into UIKit views and takes touch, trackpad and
     keyboard input, and an input method that drives the on-screen
     keyboard;
  4. libadwaita (with an iOS settings backend), the Adwaita icon theme and
     the Adwaita fonts.

Build-time programs (glib-compile-resources, gdbus-codegen, msgfmt, ...)
are Homebrew's macOS builds; see the workflow. The plan's own modules are
marked so the driver applies the same options, and the last one becomes
the app bundle.
"""
import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
GNOME = "https://download.gnome.org/sources/%s/%s/%s-%s.tar.xz"


def meson(name, url, opts, **extra):
    m = {"name": name, "buildsystem": "meson", "config-opts": list(opts),
         "sources": [{"type": "archive", "url": url}]}
    m.update(extra)
    return m


def meson_git(name, url, tag, opts, **extra):
    m = {"name": name, "buildsystem": "meson", "config-opts": list(opts),
         "sources": [{"type": "git", "url": url, "tag": tag}]}
    m.update(extra)
    return m


def cmake(name, url, opts, **extra):
    m = {"name": name, "buildsystem": "cmake-ninja", "config-opts": ["-DBUILD_TESTING=OFF"] + list(opts),
         "sources": [{"type": "archive", "url": url}]}
    m.update(extra)
    return m


GLIB = "2.90.1"
GTK = "4.24.1"
LIBADWAITA = "1.10.0"


def stack_modules():
    mods = []

    # libintl stand-in (shared with the KDE recipe's compat library).
    mods.append({
        "name": "inux-intl",
        "buildsystem": "simple",
        "sources": [{"type": "file", "path": os.path.join(SOURCE, "compat", "libintl.c")},
                    {"type": "file", "path": os.path.join(SOURCE, "compat", "libintl.h")}],
        "build-commands": [
            "$CC $CFLAGS -c libintl.c -o libintl.o",
            "ar rcs \"$FLATPAK_DEST/lib/libintl.a\" libintl.o",
            "cp libintl.h \"$FLATPAK_DEST/include/libintl.h\"",
        ],
    })

    mods.append(meson("glib", GNOME % ("glib", "2.90", "glib", GLIB), [
        "-Dtests=false", "-Dinstalled_tests=false", "-Dintrospection=disabled",
        "-Dman-pages=disabled", "-Ddocumentation=false", "-Dlibmount=disabled",
        "-Dselinux=disabled", "-Dxattr=false", "-Dsysprof=disabled", "-Dlibelf=disabled",
        "-Ddtrace=disabled", "-Dsystemtap=disabled", "-Dbsymbolic_functions=false",
        "-Dglib_debug=disabled", "-Dnls=enabled",
        # Bundled as Meson subprojects, linked statically.
        "--force-fallback-for=libffi,libpcre2-8",
    ]))

    mods.append(cmake("libpng", "https://github.com/pnggroup/libpng/archive/refs/tags/v1.6.50.tar.gz", [
        "-DPNG_SHARED=OFF", "-DPNG_STATIC=ON", "-DPNG_FRAMEWORK=OFF", "-DPNG_TESTS=OFF",
        "-DPNG_TOOLS=OFF", "-DPNG_HARDWARE_OPTIMIZATIONS=OFF",
    ]))
    mods.append(cmake("libjpeg-turbo",
                      "https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.1.2/libjpeg-turbo-3.1.2.tar.gz", [
        "-DENABLE_SHARED=OFF", "-DENABLE_STATIC=ON", "-DWITH_TURBOJPEG=OFF", "-DWITH_TOOLS=OFF",
        "-DWITH_TESTS=OFF", "-DWITH_SIMD=OFF",
    ]))
    mods.append(cmake("libtiff", "https://download.osgeo.org/libtiff/tiff-4.7.1.tar.gz", [
        "-Dtiff-tools=OFF", "-Dtiff-tests=OFF", "-Dtiff-contrib=OFF", "-Dtiff-docs=OFF",
        "-Djbig=OFF", "-Dlzma=OFF", "-Dzstd=OFF", "-Dwebp=OFF", "-Dlerc=OFF", "-Dlibdeflate=OFF",
        "-Dcxx=OFF", "-Djpeg=ON", "-Dzlib=ON",
    ]))
    mods.append(meson("freetype", "https://download.savannah.gnu.org/releases/freetype/freetype-2.14.1.tar.xz", [
        "-Dbrotli=disabled", "-Dbzip2=disabled", "-Dharfbuzz=disabled", "-Dpng=enabled",
        "-Dzlib=system", "-Dtests=disabled",
    ]))
    mods.append(cmake("expat", "https://github.com/libexpat/libexpat/releases/download/R_2_7_3/expat-2.7.3.tar.xz", [
        "-DEXPAT_BUILD_TOOLS=OFF", "-DEXPAT_BUILD_EXAMPLES=OFF", "-DEXPAT_BUILD_TESTS=OFF",
        "-DEXPAT_SHARED_LIBS=OFF", "-DEXPAT_BUILD_DOCS=OFF", "-DEXPAT_BUILD_PKGCONFIG=ON",
    ]))
    mods.append(meson_git("fontconfig", "https://gitlab.freedesktop.org/fontconfig/fontconfig.git", "2.17.1", [
        "-Ddoc=disabled", "-Dnls=disabled", "-Dtests=disabled", "-Dtools=disabled",
        "-Dcache-build=disabled",
    ]))
    mods.append(meson("fribidi", "https://github.com/fribidi/fribidi/releases/download/v1.0.16/fribidi-1.0.16.tar.xz", [
        "-Ddocs=false", "-Dbin=false", "-Dtests=false",
    ]))
    mods.append(meson("harfbuzz", "https://github.com/harfbuzz/harfbuzz/releases/download/12.3.0/harfbuzz-12.3.0.tar.xz", [
        "-Dglib=enabled", "-Dgobject=disabled", "-Dcairo=disabled", "-Dicu=disabled",
        "-Dgraphite2=disabled", "-Dfreetype=enabled", "-Dcoretext=disabled", "-Dtests=disabled",
        "-Dintrospection=disabled", "-Ddocs=disabled", "-Dutilities=disabled", "-Dbenchmark=disabled",
    ]))
    mods.append(meson_git("pixman", "https://gitlab.freedesktop.org/pixman/pixman.git", "pixman-0.46.0", [
        "-Da64-neon=disabled", "-Darm-simd=disabled", "-Dneon=disabled", "-Dgtk=disabled",
        "-Dlibpng=disabled", "-Dopenmp=disabled", "-Dtests=disabled", "-Ddemos=disabled",
    ]))
    mods.append(meson_git("cairo", "https://gitlab.freedesktop.org/cairo/cairo.git", "1.18.4", [
        "-Dquartz=disabled", "-Dxlib=disabled", "-Dxcb=disabled", "-Dpng=enabled",
        "-Dfreetype=enabled", "-Dfontconfig=enabled", "-Dglib=enabled", "-Dzlib=enabled",
        "-Dtests=disabled", "-Dspectre=disabled", "-Dsymbol-lookup=disabled", "-Dlzo=disabled",
        "-Ddwrite=disabled",
    ]))
    mods.append(meson("pango", GNOME % ("pango", "1.58", "pango", "1.58.2"), [
        "-Dfontconfig=enabled", "-Dfreetype=enabled", "-Dcairo=enabled", "-Dlibthai=disabled",
        "-Dxft=disabled", "-Dintrospection=disabled", "-Dbuild-testsuite=false",
        "-Dbuild-examples=false", "-Ddocumentation=false", "-Dman-pages=false", "-Dsysprof=disabled",
    ]))
    mods.append(meson("gdk-pixbuf", GNOME % ("gdk-pixbuf", "2.44", "gdk-pixbuf", "2.44.8"), [
        "-Dpng=enabled", "-Djpeg=enabled", "-Dtiff=disabled", "-Dgif=enabled", "-Dglycin=disabled",
        "-Dothers=disabled", "-Dbuiltin_loaders=png,jpeg,gif", "-Dgtk_doc=false",
        "-Ddocumentation=false", "-Dintrospection=disabled", "-Dman=false", "-Dtests=false",
        "-Dinstalled_tests=false", "-Dgio_sniffing=false", "-Dthumbnailer=disabled",
    ]))
    mods.append(meson("graphene", "https://github.com/ebassi/graphene/releases/download/1.10.8/graphene-1.10.8.tar.xz", [
        "-Dtests=false", "-Dinstalled_tests=false", "-Dintrospection=disabled", "-Dgobject_types=true",
        "-Dgtk_doc=false",
    ]))
    mods.append(meson("libepoxy", "https://github.com/anholt/libepoxy/releases/download/1.5.10/libepoxy-1.5.10.tar.xz", [
        "-Ddocs=false", "-Dglx=no", "-Degl=no", "-Dx11=false", "-Dtests=false",
    ]))

    gtk = meson("gtk", GNOME % ("gtk", "4.24", "gtk", GTK), [
        "-Dx11-backend=false", "-Dwayland-backend=false", "-Dbroadway-backend=false",
        "-Dmacos-backend=false", "-Dwin32-backend=false", "-Dandroid-backend=false",
        "-Duikit-backend=true", "-Dmedia-gstreamer=disabled", "-Dprint-cups=disabled",
        "-Dprint-cpdb=disabled", "-Dvulkan=disabled", "-Dcloudproviders=disabled",
        "-Dsysprof=disabled", "-Dtracker=disabled", "-Dcolord=disabled", "-Daccesskit=disabled",
        "-Dintrospection=disabled", "-Ddocumentation=false", "-Dscreenshots=false",
        "-Dman-pages=false", "-Dbuild-demos=false", "-Dbuild-testsuite=false",
        "-Dbuild-examples=false", "-Dbuild-tests=false", "-Df16c=disabled",
    ])
    # The UIKit backend's sources, then the patch that wires it into GTK's
    # build and backend list.
    gtk["sources"].append({"type": "dir", "path": os.path.join(SOURCE, "gtk-uikit", "tree")})
    gtk["inux-patches"] = ["../gtk-uikit/gtk-uikit-backend.patch"]
    mods.append(gtk)

    adw = meson("libadwaita", GNOME % ("libadwaita", "1.10", "libadwaita", LIBADWAITA), [
        "-Dintrospection=disabled", "-Dvapi=false", "-Ddocumentation=false", "-Dtests=false",
        "-Dexamples=false",
    ])
    adw["inux-patches"] = ["patches/libadwaita-ios.patch"]
    mods.append(adw)

    # Data only: symbolic icons GTK and libadwaita apps use, and the
    # default GNOME fonts (Adwaita Sans / Mono).
    mods.append(meson("adwaita-icon-theme", GNOME % ("adwaita-icon-theme", "49", "adwaita-icon-theme", "49.0"), []))
    mods.append(meson("adwaita-fonts", GNOME % ("adwaita-fonts", "49", "adwaita-fonts", "49.0"), []))
    return mods


GTK3_RE = re.compile(r"gtk\+-3\.0|gtk3|libhandy")


def uses_gtk3(src_plan):
    """GTK 3 apps need a GTK 3 backend, which does not exist yet."""
    if not src_plan or not os.path.isdir(src_plan):
        return False
    for root, _dirs, files in os.walk(src_plan):
        for f in files:
            if f in ("meson.build", "configure.ac", "CMakeLists.txt"):
                try:
                    if GTK3_RE.search(open(os.path.join(root, f), errors="replace").read()):
                        return True
                except OSError:
                    pass
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--src-plan", default="")
    ap.add_argument("--recipe-out", required=True)
    ap.add_argument("--plan-out", required=True)
    a = ap.parse_args()

    plan = json.load(open(a.plan))
    app_dir = ""
    plan_modules = [m for m in plan["modules"] if not m.get("missing")]
    if plan_modules and a.src_plan:
        app_dir = os.path.join(a.src_plan, plan_modules[-1]["name"])
    if uses_gtk3(app_dir):
        raise SystemExit("this app uses GTK 3; only GTK 4 apps are supported so far")

    modules = stack_modules()
    json.dump({"modules": modules}, open(a.recipe_out, "w"), indent=2)

    for i, m in enumerate(plan_modules):
        m["gnome"] = True
        if i == len(plan_modules) - 1:
            m["app"] = True
            m["app-target"] = plan.get("command") or ""
    plan["modules"] = plan_modules
    json.dump(plan, open(a.plan_out, "w"), indent=2)

    print("recipe: %d modules" % len(modules))
    for m in modules:
        print("  %-22s %s" % (m["name"], m["buildsystem"]))
    print("plan:")
    for m in plan_modules:
        print("  %-22s %s" % (m["name"], "app -> " + m["app-target"] if m.get("app") else "library"))


if __name__ == "__main__":
    main()
